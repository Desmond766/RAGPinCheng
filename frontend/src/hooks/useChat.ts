import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { streamChat } from "../api/chatStream";
import type { ChatMessage } from "../types";

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

/** Manages the active chat thread.
 *
 * The owning component drives `conversationId`. When it changes, we replay
 * the conversation's messages from the backend. A null id means "fresh
 * chat, not yet persisted" — the first `send()` call will create one on
 * the fly and notify the parent.
 */
export function useChat({
  conversationId,
  onConversationCreated,
  onConversationUpdated,
}: {
  conversationId: string | null;
  onConversationCreated?: (id: string) => void;
  onConversationUpdated?: (id: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // When send() lazy-creates a conversation, it stashes the new id here so
  // the next [conversationId] effect run treats it as a no-op — otherwise
  // the effect would abort the streaming controller we just set and replace
  // the optimistic user/assistant messages with the (still-empty) DB read.
  const skipNextLoadRef = useRef<string | null>(null);

  // Reload messages whenever the active conversation changes.
  useEffect(() => {
    if (conversationId && skipNextLoadRef.current === conversationId) {
      skipNextLoadRef.current = null;
      return;
    }
    abortRef.current?.abort();
    setError(null);
    if (!conversationId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const state = await api.getConversation(conversationId);
        if (cancelled) return;
        // Pair each assistant message with the immediately-preceding user
        // turn so the FeedbackBar has `query` to ship — otherwise resumed
        // conversations would send feedback with only `answer_text`.
        let lastUserContent: string | undefined;
        const replayed: ChatMessage[] = state.messages.map((m) => {
          if (m.role === "user") lastUserContent = m.content;
          return {
            id: m.id != null ? String(m.id) : newId(),
            role: m.role,
            content: m.content,
            sources: m.sources_for_ui || undefined,
            query: m.role === "assistant" ? lastUserContent : undefined,
            stage: "done",
          };
        });
        setMessages(replayed);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const send = useCallback(
    async (query: string, categories?: string[] | null) => {
      const trimmed = query.trim();
      if (!trimmed || sending) return;

      // Lazy-create a conversation on the first message if there isn't one.
      let cid = conversationId;
      if (!cid) {
        try {
          const conv = await api.createConversation();
          cid = conv.id;
          // Mark the just-created id so the [conversationId] effect doesn't
          // run its abort+reload path when the parent calls setCurrentId.
          skipNextLoadRef.current = cid;
          onConversationCreated?.(cid);
        } catch (e: any) {
          setError(e?.message || String(e));
          return;
        }
      }

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
          cid,
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
        // Persisted; let the sidebar refresh title + updated_at.
        if (cid) onConversationUpdated?.(cid);
      }
    },
    [sending, conversationId, onConversationCreated, onConversationUpdated],
  );

  return { messages, send, sending, loading, error };
}
