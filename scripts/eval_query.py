"""Interactive agent CLI with full debug visibility for the RAG pipeline.

Routes every turn through `src.session.ChatSession` so the eval CLI exercises
exactly the same code path as the Streamlit app.

Usage:
    python scripts/eval_query.py              # start interactive session
    python scripts/eval_query.py "<question>" # seed the session with a first question

Each turn prints:
  - the rewritten (standalone) query if multi-turn
  - retrieval results: parent doc/section, score, matched child snippets, full parent text
  - which fresh / carry-forward / final source counts were used
  - the exact messages sent to the LLM (system + history + user with packed context)
  - the model used, budget, history size, and final answer with cited sources

Slash commands in the session:
    /reset        clear chat history
    /history      show turn count
    /verbose N    set parent-text preview length (chars, 0 = full text). Default 800.
    /full         shorthand for /verbose 0
    /short        shorthand for /verbose 400
    /exit         quit (also: /quit, Ctrl+C, Ctrl+D)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DENSE_TOP_K, FINAL_TOP_K, LLM_TEMPERATURE, MAX_CONTEXT_CHARS, SPARSE_TOP_K
from src.session import ChatSession, TurnResult


SEP = "=" * 78
SUB = "-" * 78


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n... [+{len(text) - limit} chars truncated]"


def _print_retrieval(result: TurnResult, preview_chars: int) -> None:
    parents = result.final_sources
    carried = len(result.final_sources) - len(result.fresh_sources)
    print(
        f"\n{SEP}\n[RETRIEVAL] fresh={len(result.fresh_sources)} "
        f"carried_from_last_turn={max(carried, 0)} final={len(parents)} "
        f"(dense_top_k={DENSE_TOP_K}, sparse_top_k={SPARSE_TOP_K}, "
        f"final_top_k={FINAL_TOP_K})\n{SEP}"
    )
    for i, p in enumerate(parents, 1):
        carry_tag = "  [carry]" if i > len(result.fresh_sources) else ""
        print(f"\n#{i}  score={p.score:.4f}  parent_id={p.parent_id[:8]}{carry_tag}")
        print(f"     doc      : {p.doc_title}")
        print(f"     category : {p.category}")
        print(f"     section  : {p.section_path}")
        print(f"     source   : {p.source_path}")
        print(f"     parent_chars: {len(p.text)}")
        print(f"     matched children ({len(p.matched_children)}):")
        for j, snip in enumerate(p.matched_children, 1):
            print(f"       [{j}] {snip}")
        print("     --- parent text ---")
        for line in _truncate(p.text, preview_chars).splitlines() or [""]:
            print(f"     | {line}")


def _print_llm_messages(result: TurnResult) -> None:
    answer = result.answer
    if answer is None:
        return
    print(
        f"\n{SEP}\n[LLM REQUEST] model={answer.model}  temperature={LLM_TEMPERATURE}  "
        f"context_chars={answer.context_chars}  budget={result.budget}/"
        f"{MAX_CONTEXT_CHARS}  history_chars={result.history_chars}\n{SEP}"
    )
    for m in answer.messages or []:
        print(f"\n--- role: {m['role']} ({len(m['content'])} chars) ---")
        print(m["content"])


def _print_answer(result: TurnResult) -> None:
    print(f"\n{SEP}\n[ANSWER]\n{SEP}")
    print(result.answer_text)
    print(f"\n{SUB}\n[SOURCES USED] ({len(result.sources)})")
    for i, p in enumerate(result.sources, 1):
        print(
            f"  {i}. [{p.doc_title}] §{p.section_path}  "
            f"(score={p.score:.4f}, parent_id={p.parent_id[:8]})"
        )
    print()


def ask(chat: ChatSession, query: str, preview_chars: int) -> None:
    print(f"\n{SEP}\n[USER QUERY] {query}\n{SEP}")

    result = chat.ask(query)

    if result.rewrite_applied:
        print(f"[REWRITE] standalone query for retrieval:\n  {result.search_query}")
    else:
        print("[REWRITE] (no rewrite — empty history or unchanged)")

    _print_retrieval(result, preview_chars)
    if result.answer is None:
        print(f"\n[ANSWER] {result.answer_text}\n")
        return
    _print_llm_messages(result)
    _print_answer(result)


def main() -> None:
    chat = ChatSession()
    preview_chars = 800

    if len(sys.argv) >= 2:
        ask(chat, " ".join(sys.argv[1:]), preview_chars)

    print("Interactive RAG agent (debug mode). Type your question, or /exit to quit.")
    while True:
        try:
            query = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query:
            continue
        if query in ("/exit", "/quit"):
            break
        if query == "/reset":
            chat.reset()
            print("[history cleared]\n")
            continue
        if query == "/history":
            print(f"[{chat.state.turn_index} turns recorded]\n")
            continue
        if query == "/full":
            preview_chars = 0
            print("[verbose: full parent text]\n")
            continue
        if query == "/short":
            preview_chars = 400
            print("[verbose: 400 chars]\n")
            continue
        if query.startswith("/verbose"):
            parts = query.split()
            if len(parts) == 2 and parts[1].isdigit():
                preview_chars = int(parts[1])
                print(f"[verbose: {preview_chars} chars]\n")
            else:
                print("usage: /verbose <N>\n")
            continue
        try:
            ask(chat, query, preview_chars)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[error] {e}\n")


if __name__ == "__main__":
    main()
