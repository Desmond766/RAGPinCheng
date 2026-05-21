import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { streamChat } from "../api/chatStream";
import type { ChatMessage } from "../types";

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

export function useChat() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Bootstrap a session on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { session_id } = await api.createSession();
        if (!cancelled) setSessionId(session_id);
      } catch (e: any) {
        if (!cancelled) setBootstrapError(e?.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const send = useCallback(
    async (query: string, categories?: string[] | null) => {
      const trimmed = query.trim();
      if (!trimmed || sending || !sessionId) return;

      setSending(true);
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const userMsg: ChatMessage = { id: newId(), role: "user", content: trimmed };
      const assistantId = newId();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        query: trimmed,
        streaming: true,
        stage: "retrieving",
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);

      try {
        for await (const ev of streamChat(
          sessionId,
          { query: trimmed, categories: categories && categories.length ? categories : null },
          ctrl.signal,
        )) {
          if (ev.type === "prep") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, prep: ev.data, sources: ev.data.used_sources, stage: "generating" }
                  : m,
              ),
            );
          } else if (ev.type === "token") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + ev.data.text, stage: "streaming" }
                  : m,
              ),
            );
          } else if (ev.type === "done") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content: ev.data.answer_text || m.content,
                      sources: ev.data.sources,
                      done: ev.data,
                      streaming: false,
                      stage: "done",
                    }
                  : m,
              ),
            );
          } else if (ev.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, error: ev.data.message, streaming: false, stage: "done" }
                  : m,
              ),
            );
          }
        }
      } catch (e: any) {
        const msg = e?.name === "AbortError" ? "（已中止）" : e?.message || String(e);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, error: msg, streaming: false, stage: "done" }
              : m,
          ),
        );
      } finally {
        setSending(false);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId && m.streaming
              ? { ...m, streaming: false, stage: "done" }
              : m,
          ),
        );
      }
    },
    [sending, sessionId],
  );

  const reset = useCallback(async () => {
    abortRef.current?.abort();
    if (sessionId) {
      try {
        await api.deleteSession(sessionId);
      } catch {
        /* noop */
      }
    }
    setMessages([]);
    try {
      const { session_id } = await api.createSession();
      setSessionId(session_id);
    } catch (e: any) {
      setBootstrapError(e?.message || String(e));
    }
  }, [sessionId]);

  return { sessionId, messages, send, sending, reset, bootstrapError };
}
