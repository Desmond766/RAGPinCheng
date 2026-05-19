"""GLM-4 generation with cited sources, via Zhipu's OpenAI-compatible API."""
from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI

from .config import (
    LLM_MODEL,
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


def _build_context(
    parents: list[RetrievedParent],
    budget: int,
) -> tuple[str, list[RetrievedParent]]:
    """Pack as many parents as fit under `budget` chars. Always keep at least one."""
    blocks: list[str] = []
    used: list[RetrievedParent] = []
    total = 0
    for p in parents:
        block = (
            f'<source id="{p.parent_id[:8]}" doc="{p.doc_title}" section="{p.section_path}">\n'
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


def rewrite_query(
    history: list[dict],
    question: str,
    max_turns: int = 6,
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
            model=LLM_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": load_prompt("rewrite_system")},
                {"role": "user", "content": user_msg},
            ],
        )
        rewritten = (resp.choices[0].message.content or "").strip()
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
    """Run the answering LLM call.

    Channel separation:
      - `history` (conversation channel) is interleaved as native chat turns.
        Callers must strip <sources> from prior assistant messages before
        passing them here.
      - `parents` (knowledge channel) are packed into the *current* user
        message only, never into history.
      - `query` is the user's original question, not the retrieval rewrite.
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

    client = _client()
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
        messages=messages,
    )
    return Answer(
        text=resp.choices[0].message.content or "",
        sources=used,
        messages=messages,
        model=LLM_MODEL,
        context_chars=len(context),
        budget_used=len(context),
        budget=effective_budget,
    )
