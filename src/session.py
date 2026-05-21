"""Multi-turn RAG session orchestration.

Owns the per-turn pipeline:
    ① rewrite  ② retrieve  ③ merge  ④ generate  ⑤ update state

Channel separation is enforced here:
  - Conversation channel: `SessionState.messages` (text only; <sources> stripped)
  - Knowledge channel: `SessionState.last_sources` (typed RetrievedParent objects)
The two never mix inside the message list sent to the LLM.

Two entry points share the pipeline:
  - `ask()` — synchronous, returns a fully resolved TurnResult. Used by the
    eval CLI and any programmatic caller that wants the complete answer.
  - `ask_stream()` — returns (prep, generator). Consume the generator to drive
    token-by-token UI rendering; state is finalized when the generator
    exhausts, and `self.last_turn_result` then holds the full TurnResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import Iterator

from .config import MAX_CONTEXT_CHARS
from .generate import Answer, GenerationPrep, generate, rewrite_query, stream_generate
from .rerank import rerank_scores
from .retrieve import RetrievedParent, retrieve

# Fixed reserve (chars) inside MAX_CONTEXT_CHARS for system prompt + question
# scaffolding + answer_user template overhead. Leaves the remainder for history
# + sources, with sources being the elastic component.
RESERVE_CHARS = 700

# How many prior turns (user+assistant pairs) to feed back as native chat history.
HISTORY_TURNS = 4

# How many of the previous turn's top sources to carry forward into the next
# retrieval as a safety net for thin follow-ups.
CARRY_SOURCES = 2


@dataclass
class Message:
    """A single chat turn from the conversation channel.

    `content` is what gets sent to the LLM. `sources_for_ui` is a UI-only
    snapshot of the citations that produced this turn; it never re-enters
    the LLM context.
    """
    role: str  # "user" | "assistant"
    content: str
    sources_for_ui: list[dict] | None = None


@dataclass
class SessionState:
    messages: list[Message] = field(default_factory=list)
    last_sources: list[RetrievedParent] = field(default_factory=list)
    last_search_query: str = ""
    turn_index: int = 0

    def history_for_llm(self, k: int = HISTORY_TURNS) -> list[dict]:
        """Return the last k turn pairs as {role, content} dicts for the LLM."""
        window = self.messages[-(k * 2):] if k > 0 else []
        return [{"role": m.role, "content": m.content} for m in window]

    def history_for_rewrite(self) -> list[dict]:
        """All prior turns, formatted for rewrite_query()."""
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def append_turn(
        self,
        user_text: str,
        assistant_text: str,
        sources_for_ui: list[dict] | None = None,
    ) -> None:
        self.messages.append(Message(role="user", content=user_text))
        self.messages.append(
            Message(
                role="assistant",
                content=assistant_text,
                sources_for_ui=sources_for_ui,
            )
        )
        self.turn_index += 1

    def reset(self) -> None:
        self.messages.clear()
        self.last_sources = []
        self.last_search_query = ""
        self.turn_index = 0


@dataclass
class TurnResult:
    """Per-turn output — bundles the answer with debug/telemetry fields.

    Kept separate from `Answer` so that `Answer` stays a pure LLM-call result
    and `TurnResult` carries the orchestration-level metrics (rewrite,
    merge stats, budget, timings) used by the eval CLI and Streamlit debug
    panels.
    """
    answer_text: str
    sources: list[RetrievedParent]
    search_query: str
    fresh_sources: list[RetrievedParent]
    final_sources: list[RetrievedParent]
    answer: Answer | None              # None on the no-source fallback path
    history_chars: int
    budget: int
    rewrite_applied: bool
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class StreamingTurnPrep:
    """Pre-stream snapshot returned by ChatSession.ask_stream().

    Everything determined before the LLM stream begins, so the UI can render
    headers/captions/source previews up-front and then drive `text_stream` for
    the answer body. After the stream is consumed, the full TurnResult is
    available on the owning session as `last_turn_result`.
    """
    search_query: str
    rewrite_applied: bool
    fresh_sources: list[RetrievedParent]
    final_sources: list[RetrievedParent]
    used_sources: list[RetrievedParent]
    history_chars: int
    budget: int
    timings: dict[str, float]
    no_source_fallback: bool = False


def retrieve_for_turn(
    fresh: list[RetrievedParent],
    last_sources: list[RetrievedParent] | None,
    query: str,
    carry: int = CARRY_SOURCES,
) -> list[RetrievedParent]:
    """Merge fresh retrieval with the top-`carry` of last turn's sources.

    Carry-forward parents are RE-SCORED against the current `query` via the
    same cross-encoder used in fresh retrieval, so their scores live on the
    same scale and can be sorted together with fresh results. Without this,
    a highly relevant carry-forward could be silently squeezed out of the
    budget by less relevant fresh hits ranked above it.
    """
    if not last_sources or carry <= 0:
        return list(fresh)

    seen = {p.parent_id for p in fresh}
    carry_candidates = [p for p in last_sources[:carry] if p.parent_id not in seen]
    if not carry_candidates:
        return list(fresh)

    # Rescore against the current query so scores are comparable to fresh.
    # Reranking on parent text (vs child) is acceptable here because
    # carry-forward is capped at CARRY_SOURCES (=2) — the extra cost is small.
    new_scores = rerank_scores(query, [p.text for p in carry_candidates])
    rescored = [
        replace(p, score=float(s)) for p, s in zip(carry_candidates, new_scores)
    ]

    merged = list(fresh) + rescored
    merged.sort(key=lambda p: p.score, reverse=True)
    return merged


class ChatSession:
    """Drives one user-facing conversation through the 5-stage pipeline."""

    def __init__(self) -> None:
        self.state = SessionState()
        # Populated at the end of every turn (sync or streaming).
        # Streaming callers read this AFTER consuming the text generator to
        # get sources / timings / debug info.
        self.last_turn_result: TurnResult | None = None

    def reset(self) -> None:
        self.state.reset()
        self.last_turn_result = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_search_query(self, query: str) -> tuple[str, float]:
        """Step ①: pick the query to send to retrieval, with timing.

        Skips the rewrite LLM call when:
          - There is no prior history (first turn).
          - The new question is literally identical to the most recent user
            message (whitespace-stripped). In that case we reuse the cached
            `last_search_query` so a repeat question doesn't pay the rewrite
            cost twice.
        """
        t0 = perf_counter()
        prior = self.state.history_for_rewrite()
        if not prior:
            return query, perf_counter() - t0

        last_user = next(
            (m["content"] for m in reversed(prior) if m["role"] == "user"),
            None,
        )
        if (
            last_user is not None
            and self.state.last_search_query
            and query.strip() == last_user.strip()
        ):
            return self.state.last_search_query, perf_counter() - t0

        rewritten = rewrite_query(prior, query)
        return rewritten, perf_counter() - t0

    def _sources_for_ui(
        self, parents: list[RetrievedParent]
    ) -> list[dict]:
        return [
            {
                "doc_title": p.doc_title,
                "section_path": p.section_path,
                "category": p.category,
                "score": p.score,
                "rrf_score": p.rrf_score,
                "text": p.text,
                "parent_id": p.parent_id,
                "doc_type": p.doc_type,
                "start_time": p.start_time,
            }
            for p in parents
        ]

    # ── Sync entry point ──────────────────────────────────────────────────────

    def ask(
        self, query: str, categories: list[str] | None = None
    ) -> TurnResult:
        timings: dict[str, float] = {}

        # ① REWRITE
        search_query, rewrite_t = self._resolve_search_query(query)
        timings["rewrite"] = rewrite_t
        rewrite_applied = search_query != query

        # ② RETRIEVE + ③ MERGE
        t = perf_counter()
        fresh_sources = retrieve(search_query, categories=categories)
        final_sources = retrieve_for_turn(
            fresh_sources, self.state.last_sources, search_query
        )
        timings["retrieve"] = perf_counter() - t

        # No-source escape hatch.
        if not final_sources:
            fallback = "资料中未找到相关内容。"
            self.state.append_turn(query, fallback, sources_for_ui=[])
            self.state.last_sources = []
            self.state.last_search_query = search_query
            timings["generate"] = 0.0
            timings["total"] = sum(timings.values())
            result = TurnResult(
                answer_text=fallback,
                sources=[],
                search_query=search_query,
                fresh_sources=[],
                final_sources=[],
                answer=None,
                history_chars=0,
                budget=0,
                rewrite_applied=rewrite_applied,
                timings=timings,
            )
            self.last_turn_result = result
            return result

        # ④ GENERATE with history + dynamic budget.
        history_msgs = self.state.history_for_llm(k=HISTORY_TURNS)
        history_chars = sum(len(m["content"]) for m in history_msgs)
        budget = max(MAX_CONTEXT_CHARS - history_chars - RESERVE_CHARS, 0)
        t = perf_counter()
        answer = generate(
            query=query,
            parents=final_sources,
            history=history_msgs,
            budget=budget,
        )
        timings["generate"] = perf_counter() - t
        timings["total"] = sum(timings.values())

        # ⑤ UPDATE STATE — assistant text only; sources stripped from history.
        sources_for_ui = self._sources_for_ui(answer.sources)
        self.state.append_turn(query, answer.text, sources_for_ui=sources_for_ui)
        self.state.last_sources = final_sources
        self.state.last_search_query = search_query

        result = TurnResult(
            answer_text=answer.text,
            sources=answer.sources,
            search_query=search_query,
            fresh_sources=fresh_sources,
            final_sources=final_sources,
            answer=answer,
            history_chars=history_chars,
            budget=budget,
            rewrite_applied=rewrite_applied,
            timings=timings,
        )
        self.last_turn_result = result
        return result

    # ── Streaming entry point ─────────────────────────────────────────────────

    def ask_stream(
        self, query: str, categories: list[str] | None = None
    ) -> tuple[StreamingTurnPrep, Iterator[str]]:
        """Streaming variant of `ask()`.

        Returns `(prep, generator)`. The generator yields text deltas; when
        it exhausts (or the consumer closes it), state is finalized and
        `self.last_turn_result` is set with the full TurnResult.
        """
        timings: dict[str, float] = {}

        # ① REWRITE
        search_query, rewrite_t = self._resolve_search_query(query)
        timings["rewrite"] = rewrite_t
        rewrite_applied = search_query != query

        # ② RETRIEVE + ③ MERGE
        t = perf_counter()
        fresh_sources = retrieve(search_query, categories=categories)
        final_sources = retrieve_for_turn(
            fresh_sources, self.state.last_sources, search_query
        )
        timings["retrieve"] = perf_counter() - t

        # No-source path: stream the fallback message and finalize.
        if not final_sources:
            fallback = "资料中未找到相关内容。"
            prep = StreamingTurnPrep(
                search_query=search_query,
                rewrite_applied=rewrite_applied,
                fresh_sources=[],
                final_sources=[],
                used_sources=[],
                history_chars=0,
                budget=0,
                timings=dict(timings),
                no_source_fallback=True,
            )

            def _fallback_iter() -> Iterator[str]:
                yield fallback

            stream = self._wrap_stream(
                _fallback_iter(),
                query=query,
                search_query=search_query,
                rewrite_applied=rewrite_applied,
                fresh_sources=[],
                final_sources=[],
                gen_prep=None,
                history_chars=0,
                budget=0,
                timings_so_far=dict(timings),
            )
            return prep, stream

        # ④ STREAM GENERATE with history + dynamic budget.
        history_msgs = self.state.history_for_llm(k=HISTORY_TURNS)
        history_chars = sum(len(m["content"]) for m in history_msgs)
        budget = max(MAX_CONTEXT_CHARS - history_chars - RESERVE_CHARS, 0)
        gen_prep, raw_stream = stream_generate(
            query=query,
            parents=final_sources,
            history=history_msgs,
            budget=budget,
        )

        prep = StreamingTurnPrep(
            search_query=search_query,
            rewrite_applied=rewrite_applied,
            fresh_sources=fresh_sources,
            final_sources=final_sources,
            used_sources=gen_prep.used_sources,
            history_chars=history_chars,
            budget=budget,
            timings=dict(timings),
        )

        stream = self._wrap_stream(
            raw_stream,
            query=query,
            search_query=search_query,
            rewrite_applied=rewrite_applied,
            fresh_sources=fresh_sources,
            final_sources=final_sources,
            gen_prep=gen_prep,
            history_chars=history_chars,
            budget=budget,
            timings_so_far=dict(timings),
        )
        return prep, stream

    def _wrap_stream(
        self,
        raw_stream: Iterator[str],
        *,
        query: str,
        search_query: str,
        rewrite_applied: bool,
        fresh_sources: list[RetrievedParent],
        final_sources: list[RetrievedParent],
        gen_prep: GenerationPrep | None,
        history_chars: int,
        budget: int,
        timings_so_far: dict[str, float],
    ) -> Iterator[str]:
        """Accumulate streamed text, time the generate stage, then finalize.

        `try/finally` covers the cases where the consumer abandons the
        generator (Streamlit rerun, exception) — partial text still flushes
        to state and `last_turn_result` so the UI stays coherent.
        """
        accumulated: list[str] = []
        t0 = perf_counter()
        try:
            for chunk in raw_stream:
                accumulated.append(chunk)
                yield chunk
        finally:
            gen_time = perf_counter() - t0
            full_text = "".join(accumulated)
            timings = dict(timings_so_far)
            timings["generate"] = gen_time
            timings["total"] = sum(timings.values())
            self._finalize_streaming_turn(
                query=query,
                search_query=search_query,
                rewrite_applied=rewrite_applied,
                fresh_sources=fresh_sources,
                final_sources=final_sources,
                gen_prep=gen_prep,
                full_text=full_text,
                history_chars=history_chars,
                budget=budget,
                timings=timings,
            )

    def _finalize_streaming_turn(
        self,
        *,
        query: str,
        search_query: str,
        rewrite_applied: bool,
        fresh_sources: list[RetrievedParent],
        final_sources: list[RetrievedParent],
        gen_prep: GenerationPrep | None,
        full_text: str,
        history_chars: int,
        budget: int,
        timings: dict[str, float],
    ) -> None:
        """Mirror of the ⑤ UPDATE STATE block in `ask()`, for the streaming path."""
        if gen_prep is None:
            # no-source fallback
            self.state.append_turn(query, full_text, sources_for_ui=[])
            self.state.last_sources = []
            self.state.last_search_query = search_query
            self.last_turn_result = TurnResult(
                answer_text=full_text,
                sources=[],
                search_query=search_query,
                fresh_sources=[],
                final_sources=[],
                answer=None,
                history_chars=0,
                budget=0,
                rewrite_applied=rewrite_applied,
                timings=timings,
            )
            return

        sources_for_ui = self._sources_for_ui(gen_prep.used_sources)
        self.state.append_turn(query, full_text, sources_for_ui=sources_for_ui)
        self.state.last_sources = final_sources
        self.state.last_search_query = search_query

        answer = Answer(
            text=full_text,
            sources=gen_prep.used_sources,
            messages=gen_prep.messages,
            model=gen_prep.model,
            context_chars=gen_prep.context_chars,
            budget_used=gen_prep.context_chars,
            budget=gen_prep.budget,
        )
        self.last_turn_result = TurnResult(
            answer_text=full_text,
            sources=gen_prep.used_sources,
            search_query=search_query,
            fresh_sources=fresh_sources,
            final_sources=final_sources,
            answer=answer,
            history_chars=history_chars,
            budget=budget,
            rewrite_applied=rewrite_applied,
            timings=timings,
        )
