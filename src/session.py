"""Multi-turn RAG session orchestration.

Owns the per-turn pipeline:
    ① rewrite  ② retrieve  ③ merge  ④ generate  ⑤ update state

Channel separation is enforced here:
  - Conversation channel: `SessionState.messages` (text only; <sources> stripped)
  - Knowledge channel: `SessionState.last_sources` (typed RetrievedParent objects)
The two never mix inside the message list sent to the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import MAX_CONTEXT_CHARS
from .generate import Answer, generate, rewrite_query
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
    merge stats, budget) used by the eval CLI and Streamlit debug panels.
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


def retrieve_for_turn(
    fresh: list[RetrievedParent],
    last_sources: list[RetrievedParent] | None,
    carry: int = CARRY_SOURCES,
) -> list[RetrievedParent]:
    """Merge fresh retrieval with the top-`carry` of last turn's sources.

    Dedup by parent_id; fresh ranking wins ties. The carry-forward is a
    safety net for thin follow-up rewrites that fail to re-find a chunk
    the user is clearly still discussing.
    """
    if not last_sources or carry <= 0:
        return list(fresh)
    seen = {p.parent_id for p in fresh}
    merged = list(fresh)
    for p in last_sources[:carry]:
        if p.parent_id not in seen:
            merged.append(p)
            seen.add(p.parent_id)
    return merged


class ChatSession:
    """Drives one user-facing conversation through the 5-stage pipeline."""

    def __init__(self) -> None:
        self.state = SessionState()

    def reset(self) -> None:
        self.state.reset()

    def ask(self, query: str) -> TurnResult:
        # ① REWRITE — only when there's prior history.
        prior_for_rewrite = self.state.history_for_rewrite()
        if prior_for_rewrite:
            search_query = rewrite_query(prior_for_rewrite, query)
        else:
            search_query = query
        rewrite_applied = search_query != query

        # ② RETRIEVE fresh.
        fresh_sources = retrieve(search_query)

        # ③ MERGE with prior turn's carry-forward.
        final_sources = retrieve_for_turn(fresh_sources, self.state.last_sources)

        # No-source escape hatch.
        if not final_sources:
            fallback = "资料中未找到相关内容。"
            self.state.append_turn(query, fallback, sources_for_ui=[])
            self.state.last_sources = []
            self.state.last_search_query = search_query
            return TurnResult(
                answer_text=fallback,
                sources=[],
                search_query=search_query,
                fresh_sources=[],
                final_sources=[],
                answer=None,
                history_chars=0,
                budget=0,
                rewrite_applied=rewrite_applied,
            )

        # ④ GENERATE with history + dynamic budget.
        history_msgs = self.state.history_for_llm(k=HISTORY_TURNS)
        history_chars = sum(len(m["content"]) for m in history_msgs)
        budget = max(MAX_CONTEXT_CHARS - history_chars - RESERVE_CHARS, 0)
        answer = generate(
            query=query,
            parents=final_sources,
            history=history_msgs,
            budget=budget,
        )

        # ⑤ UPDATE STATE — assistant text only; sources stripped from history.
        sources_for_ui = [
            {
                "doc_title": p.doc_title,
                "section_path": p.section_path,
                "category": p.category,
                "score": p.score,
                "text": p.text,
                "parent_id": p.parent_id,
            }
            for p in answer.sources
        ]
        self.state.append_turn(query, answer.text, sources_for_ui=sources_for_ui)
        self.state.last_sources = final_sources
        self.state.last_search_query = search_query

        return TurnResult(
            answer_text=answer.text,
            sources=answer.sources,
            search_query=search_query,
            fresh_sources=fresh_sources,
            final_sources=final_sources,
            answer=answer,
            history_chars=history_chars,
            budget=budget,
            rewrite_applied=rewrite_applied,
        )
