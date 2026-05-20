"""Streamlit chat UI for the PinCheng RAG system."""
from __future__ import annotations

import streamlit as st

from src.config import COLLECTION, EMBED_MODEL, LLM_MODEL, RERANK_ENABLED, RERANKER_MODEL
from src.index import collection_stats, list_categories, parents_count
from src.session import ChatSession

st.set_page_config(page_title="品诚钢构知识助手", page_icon="🔩", layout="wide")


@st.cache_resource(show_spinner="正在加载嵌入模型 (BGE-M3)...")
def _warm_model():
    from src.embed import get_model
    get_model()
    return True


@st.cache_resource(show_spinner="正在加载重排序模型 (BGE-reranker-v2-m3)...")
def _warm_reranker():
    from src.rerank import get_reranker
    get_reranker()
    return True


@st.cache_data(ttl=60)
def _categories() -> list[str]:
    return list_categories()


def _get_session() -> ChatSession:
    if "chat" not in st.session_state:
        st.session_state.chat = ChatSession()
    return st.session_state.chat


def _render_sources(sources: list[dict]) -> None:
    with st.expander(f"📎 参考来源 ({len(sources)})"):
        for i, s in enumerate(sources, 1):
            if s.get("doc_type") == "transcript" and s.get("start_time"):
                locator = f"🎬 @{s['start_time']}"
            else:
                locator = f"§{s['section_path']}"
            st.markdown(
                f"**{i}. [{s['doc_title']}] {locator}**  "
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
        st.markdown(
            f"**重排模型:** `{RERANKER_MODEL if RERANK_ENABLED else '— 已禁用'}`"
        )
        st.markdown(f"**生成模型:** `{LLM_MODEL}`")
        st.markdown(f"**Qdrant 集合:** `{COLLECTION}`")
        st.markdown(f"**当前轮次:** {chat.state.turn_index}")
        st.markdown("---")
        all_categories = _categories()
        selected_categories = st.multiselect(
            "🗂 限定分类（留空 = 全部）",
            options=all_categories,
            default=[],
            help="只在选中的分类中检索；不选则覆盖全部语料。",
        )
        if st.button("清空对话"):
            chat.reset()
            st.rerun()

    _warm_model()
    if RERANK_ENABLED:
        _warm_reranker()

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
        with st.spinner("检索中..."):
            prep, stream = chat.ask_stream(
                query, categories=selected_categories or None
            )
        if prep.rewrite_applied:
            st.caption(f"🔄 检索改写：{prep.search_query}")
        # Stream tokens as they arrive; st.write_stream returns the full text.
        # When this returns, the stream wrapper has already finalized state,
        # so chat.state.messages[-1] and chat.last_turn_result are populated.
        st.write_stream(stream)
        last_msg = chat.state.messages[-1]
        if last_msg.sources_for_ui:
            _render_sources(last_msg.sources_for_ui)
        result = chat.last_turn_result
        with st.expander("🔍 调试信息"):
            timing_md = (
                "  ".join(f"`{k}={v:.2f}s`" for k, v in result.timings.items())
                if result and result.timings
                else "(no timing data)"
            )
            st.markdown(
                f"- timings: {timing_md}\n"
                f"- history_chars: `{result.history_chars if result else 0}`\n"
                f"- budget: `{result.budget if result else 0}`\n"
                f"- fresh_sources: `{len(result.fresh_sources) if result else 0}`\n"
                f"- final_sources (after merge): `{len(result.final_sources) if result else 0}`"
            )


if __name__ == "__main__":
    main()
