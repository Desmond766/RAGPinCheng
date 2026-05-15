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
from .retrieve import RetrievedParent

SYSTEM_PROMPT = """你是一名钢结构工程知识助手，专门基于提供的国家标准、设计手册和规范资料回答工程问题。

**严格规则：**
1. 只根据下方 <sources> 中提供的资料作答。如果资料中没有答案，必须明确回答："资料中未找到相关内容。"，禁止编造或使用外部知识。
2. 每个事实性陈述必须在句末标注引用，格式为：[文档标题 §章节路径]。例如：[GB50017-2017《钢结构设计标准》 §4.4.1]。
3. 涉及数值、公式或表格时，原样引用，不要近似或省略单位。
4. 涉及 LaTeX 公式（$$ ... $$）时保持原格式输出。
5. 用中文回答，语言简洁专业。
"""


@dataclass
class Answer:
    text: str
    sources: list[RetrievedParent]


def _build_context(parents: list[RetrievedParent]) -> tuple[str, list[RetrievedParent]]:
    blocks = []
    used: list[RetrievedParent] = []
    total = 0
    for p in parents:
        block = (
            f'<source id="{p.parent_id[:8]}" doc="{p.doc_title}" section="{p.section_path}">\n'
            f"{p.text}\n"
            f"</source>"
        )
        if total + len(block) > MAX_CONTEXT_CHARS and used:
            break
        blocks.append(block)
        used.append(p)
        total += len(block)
    return "\n\n".join(blocks), used


def _client() -> OpenAI:
    if not ZHIPU_API_KEY:
        raise RuntimeError("ZHIPU_API_KEY is not set. Add it to .env.")
    return OpenAI(api_key=ZHIPU_API_KEY, base_url=ZHIPU_BASE_URL)


def generate(query: str, parents: list[RetrievedParent]) -> Answer:
    context, used = _build_context(parents)
    user_msg = f"<sources>\n{context}\n</sources>\n\n问题：{query}"
    client = _client()
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return Answer(text=resp.choices[0].message.content or "", sources=used)
