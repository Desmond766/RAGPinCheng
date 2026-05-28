"""GLM-4 generation with cited sources, via Zhipu's OpenAI-compatible API."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from openai import OpenAI

from .config import (
    LLM_MODEL,
    LLM_REWRITE_MODEL,
    LLM_TEMPERATURE,
    MAX_CONTEXT_CHARS,
    ZHIPU_API_KEY,
    ZHIPU_BASE_URL,
)
from .prompts import load_prompt, render_prompt
from .retrieve import RetrievedParent


@dataclass
class Answer:
    text: str
    sources: list[RetrievedParent]
    messages: list[dict] | None = None  # exact messages sent to the LLM (for debugging)
    model: str | None = None
    context_chars: int = 0
    budget_used: int = 0   # chars actually packed into <sources> after budget trim
    budget: int = 0        # the budget passed in (for telemetry / regression detection)
    # Token usage from the provider (populated when the API returns it).
    # Keys: prompt_tokens, completion_tokens, total_tokens.
    usage: dict = field(default_factory=dict)


@dataclass
class GenerationPrep:
    """Everything determined synchronously before the LLM call.

    Shared by the sync and streaming code paths so message construction and
    source-packing aren't duplicated.
    """
    used_sources: list[RetrievedParent]
    messages: list[dict]
    model: str
    context_chars: int
    budget: int
    # Populated by the streaming iterator as it consumes the final
    # usage-bearing chunk. Empty until the stream is exhausted.
    usage: dict = field(default_factory=dict)


def _build_context(
    parents: list[RetrievedParent],
    budget: int,
) -> tuple[str, list[RetrievedParent]]:
    """Pack as many parents as fit under `budget` chars. Always keep at least one."""
    blocks: list[str] = []
    used: list[RetrievedParent] = []
    total = 0
    for p in parents:
        company_attr = f' company="{p.company}"' if p.company else ""
        if p.doc_type == "transcript" and p.start_time:
            block = (
                f'<source id="{p.parent_id[:8]}" doc="{p.doc_title}" '
                f'category="{p.category}"{company_attr} '
                f'time="{p.start_time}" type="transcript">\n'
                f"{p.text}\n"
                f"</source>"
            )
        else:
            # Show the LLM only the leaf of the breadcrumb (e.g. `(5) 钢材耐腐蚀性差`)
            # instead of the full path `第1章 概述 > 1.1 ... > 1.1.1 ... > (5) ...`.
            # Inline citations stay short and readable; the full breadcrumb is
            # exposed in the SourcesPanel expand view.
            section_leaf = (
                p.section_path.split(" > ")[-1] if p.section_path else ""
            )
            block = (
                f'<source id="{p.parent_id[:8]}" doc="{p.doc_title}" '
                f'category="{p.category}"{company_attr} '
                f'section="{section_leaf}" type="pdf">\n'
                f"{p.text}\n"
                f"</source>"
            )
        if total + len(block) > budget and used:
            break
        blocks.append(block)
        used.append(p)
        total += len(block)
    return "\n\n".join(blocks), used


def _client() -> OpenAI:
    if not ZHIPU_API_KEY:
        raise RuntimeError("ZHIPU_API_KEY is not set. Add it to .env.")
    return OpenAI(api_key=ZHIPU_API_KEY, base_url=ZHIPU_BASE_URL)


def _prepare_generation(
    query: str,
    parents: list[RetrievedParent],
    history: list[dict] | None,
    budget: int | None,
) -> GenerationPrep:
    """Build the messages list and decide which parents fit under `budget`.

    Pure / synchronous: makes no API call. Both `generate()` and
    `stream_generate()` build on this so they agree on what gets sent.
    """
    effective_budget = MAX_CONTEXT_CHARS if budget is None else max(budget, 0)
    context, used = _build_context(parents, effective_budget)
    user_msg = render_prompt("answer_user", context=context, query=query)

    messages: list[dict] = [
        {"role": "system", "content": load_prompt("answer_system")}
    ]
    if history:
        for m in history:
            role = m.get("role")
            content = m.get("content") or ""
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})

    return GenerationPrep(
        used_sources=used,
        messages=messages,
        model=LLM_MODEL,
        context_chars=len(context),
        budget=effective_budget,
    )


def _extract_usage(resp) -> dict:
    """Best-effort pull of token usage off an OpenAI-compatible response/chunk."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    out: dict = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = getattr(u, k, None)
        if v is None and isinstance(u, dict):
            v = u.get(k)
        if v is not None:
            out[k] = int(v)
    return out


def rewrite_query(
    history: list[dict],
    question: str,
    max_turns: int = 6,
    usage_out: dict | None = None,
) -> str:
    """Rewrite a follow-up question into a standalone one using recent chat history.

    `history` is a list of {"role": "user"|"assistant", "content": str} dicts
    NOT including the current `question`. Returns `question` unchanged when
    history is empty or the rewrite call fails.
    """
    if not history:
        return question

    recent = history[-max_turns:]
    convo_lines = []
    for m in recent:
        speaker = "用户" if m.get("role") == "user" else "助手"
        content = (m.get("content") or "").strip()
        if content:
            convo_lines.append(f"{speaker}：{content}")
    if not convo_lines:
        return question

    user_msg = render_prompt(
        "rewrite_user",
        history="\n".join(convo_lines),
        question=question,
    )
    try:
        client = _client()
        resp = client.chat.completions.create(
            # Rewrite uses LLM_REWRITE_MODEL, not LLM_MODEL — the rewrite is
            # latency-critical (one cheap call before retrieval) while answer
            # generation cares more about quality. Defaults to LLM_MODEL when
            # LLM_REWRITE_MODEL is unset, so single-model setups still work.
            model=LLM_REWRITE_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": load_prompt("rewrite_system")},
                {"role": "user", "content": user_msg},
            ],
            extra_body={"thinking": {"type": "disabled"}},
        )
        rewritten = (resp.choices[0].message.content or "").strip()
        if usage_out is not None:
            usage_out.update(_extract_usage(resp))
            usage_out["model"] = LLM_REWRITE_MODEL
    except Exception:
        return question

    rewritten = rewritten.strip().strip('"').strip("'").strip("“”‘’").strip()
    return rewritten or question


def generate(
    query: str,
    parents: list[RetrievedParent],
    history: list[dict] | None = None,
    budget: int | None = None,
) -> Answer:
    """Run the answering LLM call (non-streaming).

    Channel separation:
      - `history` (conversation channel) is interleaved as native chat turns.
        Callers must strip <sources> from prior assistant messages before
        passing them here.
      - `parents` (knowledge channel) are packed into the *current* user
        message only, never into history.
      - `query` is the user's original question, not the retrieval rewrite.
    """
    prep = _prepare_generation(query, parents, history, budget)
    client = _client()
    resp = client.chat.completions.create(
        model=prep.model,
        temperature=LLM_TEMPERATURE,
        messages=prep.messages,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return Answer(
        text=resp.choices[0].message.content or "",
        sources=prep.used_sources,
        messages=prep.messages,
        model=prep.model,
        context_chars=prep.context_chars,
        budget_used=prep.context_chars,
        budget=prep.budget,
        usage=_extract_usage(resp),
    )


def stream_generate(
    query: str,
    parents: list[RetrievedParent],
    history: list[dict] | None = None,
    budget: int | None = None,
) -> tuple[GenerationPrep, Iterator[str]]:
    """Streaming variant of `generate()`.

    Returns `(prep, generator)`:
      - `prep` is resolved synchronously: which parents got packed, what
        messages will be sent, model/context telemetry. Render this up-front
        in the UI.
      - `generator` yields text deltas as they arrive from the LLM. The full
        answer text is the concatenation of all yielded chunks.

    The same channel-separation rules as `generate()` apply.
    """
    prep = _prepare_generation(query, parents, history, budget)
    client = _client()
    resp = client.chat.completions.create(
        model=prep.model,
        temperature=LLM_TEMPERATURE,
        messages=prep.messages,
        stream=True,
        # Ask the provider to send a final chunk carrying token usage.
        stream_options={"include_usage": True},
        extra_body={"thinking": {"type": "disabled"}},
    )

    def _iter() -> Iterator[str]:
        for chunk in resp:
            # Usage-only chunks have no `choices`; capture and continue.
            usage = _extract_usage(chunk)
            if usage:
                prep.usage.update(usage)
                prep.usage["model"] = prep.model
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    return prep, _iter()
