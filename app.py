"""Streamlit chat UI for the PinCheng RAG system."""
from __future__ import annotations

import streamlit as st

from src.config import COLLECTION, EMBED_MODEL, LLM_MODEL
from src.generate import generate, rewrite_query
from src.index import collection_stats, parents_count
from src.retrieve import retrieve

st.set_page_config(page_title="品诚钢构知识助手", page_icon="🔩", layout="wide")


@st.cache_resource(show_spinner="正在加载嵌入模型 (BGE-M3)...")
def _warm_model():
    from src.embed import get_model
    get_model()
    return True


def main() -> None:
    st.title("🔩 品诚钢构知识助手")
    st.caption("基于公司内部钢结构标准与设计手册的检索增强问答")

    with st.sidebar:
        st.header("语料库")
        stats = collection_stats()
        st.metric("子块 (Qdrant)", stats.get("children", 0))
        st.metric("父段落 (SQLite)", parents_count())
        st.markdown("---")
        st.markdown(f"**嵌入模型:** `{EMBED_MODEL}`")
        st.markdown(f"**生成模型:** `{LLM_MODEL}`")
        st.markdown(f"**Qdrant 集合:** `{COLLECTION}`")
        if st.button("清空对话"):
            st.session_state.messages = []
            st.rerun()

    _warm_model()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("sources"):
                with st.expander(f"📎 参考来源 ({len(msg['sources'])})"):
                    for i, s in enumerate(msg["sources"], 1):
                        st.markdown(
                            f"**{i}. [{s['doc_title']}] §{s['section_path']}**  "
                            f"·  分类: `{s['category']}`  ·  得分: {s['score']:.4f}"
                        )
                        st.caption(s["text"][:400] + ("..." if len(s["text"]) > 400 else ""))

    query = st.chat_input("请输入问题，例如：Q235钢的抗拉强度设计值是多少？")
    if not query:
        return

    # History BEFORE appending the new user turn — used for query rewriting.
    prior_history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]

    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        if prior_history:
            with st.spinner("理解上下文中..."):
                search_query = rewrite_query(prior_history, query)
        else:
            search_query = query
        if search_query != query:
            st.caption(f"🔄 检索改写：{search_query}")
        with st.spinner("检索资料中..."):
            parents = retrieve(search_query)
        if not parents:
            answer_text = "资料中未找到相关内容。"
            sources_payload = []
        else:
            with st.spinner("生成答案中..."):
                answer = generate(search_query, parents)
            answer_text = answer.text
            sources_payload = [
                {
                    "doc_title": p.doc_title,
                    "section_path": p.section_path,
                    "category": p.category,
                    "score": p.score,
                    "text": p.text,
                }
                for p in answer.sources
            ]

        st.markdown(answer_text)
        if sources_payload:
            with st.expander(f"📎 参考来源 ({len(sources_payload)})"):
                for i, s in enumerate(sources_payload, 1):
                    st.markdown(
                        f"**{i}. [{s['doc_title']}] §{s['section_path']}**  "
                        f"·  分类: `{s['category']}`  ·  得分: {s['score']:.4f}"
                    )
                    st.caption(s["text"][:400] + ("..." if len(s["text"]) > 400 else ""))

    st.session_state.messages.append(
        {"role": "assistant", "content": answer_text, "sources": sources_payload}
    )


if __name__ == "__main__":
    main()
