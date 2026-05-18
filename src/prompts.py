"""Prompt loader. All prompt text lives in `prompts/*.md`, not in source code."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .config import ROOT

PROMPTS_DIR = ROOT / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a prompt template by name (without extension) from `prompts/`."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, **kwargs: object) -> str:
    """Load a prompt and fill in `{placeholder}` fields via str.format."""
    return load_prompt(name).format(**kwargs)
