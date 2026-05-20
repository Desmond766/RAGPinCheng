"""SSE event formatting helpers.

We send typed events via ``sse_starlette.ServerSentEvent`` which the
sse-starlette EventSourceResponse turns into the wire format
``event: <name>\\ndata: <json>\\n\\n``.
"""
from __future__ import annotations

import json
from typing import Any

from sse_starlette.sse import ServerSentEvent


def event(name: str, payload: Any) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps(payload, ensure_ascii=False), event=name)
