import type { ChatEvent } from "../types";
import { getCsrfToken } from "./client";

/** SSE-over-POST consumer.
 *
 * EventSource only does GET, so we read the response body manually and
 * parse the wire format: blocks separated by a blank line, each with
 * `event: <name>` and `data: <json>` lines.
 */
export async function* streamChat(
  conversationId: string,
  body: { query: string; categories?: string[] | null },
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent, void, void> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "text/event-stream",
  };
  const csrf = getCsrfToken();
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const res = await fetch(`/api/conversations/${conversationId}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    credentials: "include",
    signal,
  });
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => "");
    throw new Error(`chat failed: ${res.status} ${res.statusText} ${t}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      // sse-starlette emits CRLF line terminators; normalize so the split
      // logic works regardless of which terminator the server uses.
      buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      // Split on blank line — SSE event boundary.
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const ev = parseBlock(raw);
        if (ev) yield ev;
      }
    }
    // Flush any trailing complete block (shouldn't normally happen).
    if (buf.trim().length > 0) {
      const ev = parseBlock(buf);
      if (ev) yield ev;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* noop */
    }
  }
}

function parseBlock(raw: string): ChatEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (let line of raw.split("\n")) {
    // Defensive: drop a trailing CR if a CRLF survived the buffer normalize
    // (e.g. when a CR landed on a chunk boundary).
    if (line.endsWith("\r")) line = line.slice(0, -1);
    if (!line || line.startsWith(":")) continue; // comment / heartbeat
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const field = line.slice(0, colon);
    // Per SSE spec, ignore a single space after the colon.
    let value = line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  const dataStr = dataLines.join("\n");
  let parsed: unknown;
  try {
    parsed = JSON.parse(dataStr);
  } catch {
    return null;
  }
  switch (eventName) {
    case "prep":
    case "token":
    case "done":
    case "error":
      return { type: eventName, data: parsed as any };
    default:
      return null;
  }
}
