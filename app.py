"""Streamlit chat UI for the PinCheng RAG system."""
from __future__ import annotations

import streamlit as st

from src.config import COLLECTION, EMBED_MODEL, LLM_MODEL
from src.index import collection_stats, parents_count
from src.session import ChatSession

st.set_page_config(page_title="品诚钢构知识助手", page_icon="🔩", layout="wide")


@st.cache_resource(show_spinner="正在加载嵌入模型 (BGE-M3)...")
def _warm_model():
    from src.embed import get_model
    get_model()
    return True


def _get_session() -> ChatSession:
    if "chat" not in st.session_state:
        st.session_state.chat = ChatSession()
    return st.session_state.chat


def _render_sources(sources: list[dict]) -> None:
    with st.expander(f"📎 参考来源 ({len(sources)})"):
        for i, s in enumerate(sources, 1):
            st.markdown(
                f"**{i}. [{s['doc_title']}] §{s['section_path']}**  "
                f"·  分类: `{s['category']}`  ·  得分: {s['score']:.4f}"
            )
            st.caption(s["text"][:400] + ("..." if len(s["text"]) > 400 else ""))


def main() -> None:
    st.title("🔩 品诚钢构知识助手")
    st.caption("基于公司内部钢结构标准与设计手册的检索增强问答")

    chat = _get_session()

    with st.sidebar:
        st.header("语料库")
        stats = collection_stats()
        st.metric("子块 (Qdrant)", stats.get("children", 0))
        st.metric("父段落 (SQLite)", parents_count())
        st.markdown("---")
        st.markdown(f"**嵌入模型:** `{EMBED_MODEL}`")
        st.markdown(f"**生成模型:** `{LLM_MODEL}`")
        st.markdown(f"**Qdrant 集合:** `{COLLECTION}`")
        st.markdown(f"**当前轮次:** {chat.state.turn_index}")
        if st.button("清空对话"):
            chat.reset()
            st.rerun()

    _warm_model()

    # Replay history from session state.
    for msg in chat.state.messages:
        with st.chat_message(msg.role):
            st.markdown(msg.content)
            if msg.role == "assistant" and msg.sources_for_ui:
                _render_sources(msg.sources_for_ui)

    query = st.chat_input("请输入问题，例如：Q235钢的抗拉强度设计值是多少？")
    if not query:
        return

    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("检索并生成中..."):
            result = chat.ask(query)
        if result.rewrite_applied:
            st.caption(f"🔄 检索改写：{result.search_query}")
        st.markdown(result.answer_text)
        # The turn was just appended to state — re-read its sources for display.
        last_msg = chat.state.messages[-1]
        if last_msg.sources_for_ui:
            _render_sources(last_msg.sources_for_ui)
        with st.expander("🔍 调试信息"):
            st.markdown(
                f"- history_chars: `{result.history_chars}`\n"
                f"- budget: `{result.budget}`\n"
                f"- fresh_sources: `{len(result.fresh_sources)}`\n"
                f"- final_sources (after merge): `{len(result.final_sources)}`"
            )


if __name__ == "__main__":
    main()
