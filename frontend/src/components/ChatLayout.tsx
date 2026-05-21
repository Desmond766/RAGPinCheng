import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useChat } from "../hooks/useChat";
import type { ApiConfig, Health } from "../types";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { Sidebar } from "./Sidebar";

export function ChatLayout() {
  const { sessionId, messages, send, sending, reset, bootstrapError } = useChat();
  const [categories, setCategories] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [config, setConfig] = useState<ApiConfig | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.categories().then((r) => setCategories(r.categories)).catch(() => {});
    api.config().then(setConfig).catch(() => {});
    api.health().then(setHealth).catch(() => {});
  }, []);

  const turnIndex = useMemo(
    () => messages.filter((m) => m.role === "user").length,
    [messages],
  );

  function toggle(c: string) {
    setSelected((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );
  }

  return (
    <div className="h-full flex">
      <Sidebar
        categories={categories}
        selected={selected}
        onToggle={toggle}
        onClearCategories={() => setSelected([])}
        onNewChat={reset}
        config={config}
        health={health}
        turnIndex={turnIndex}
      />
      <main className="flex-1 flex flex-col min-w-0">
        <header className="px-6 py-3 border-b border-gray-200 bg-bg/80 backdrop-blur-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-lg">📚</span>
            <span className="font-semibold">品诚 BIM 知识库</span>
          </div>
          <div className="text-xs text-muted">
            {sessionId ? (
              <>
                session: <code>{sessionId.slice(0, 8)}…</code>
              </>
            ) : bootstrapError ? (
              <span className="text-red-600">
                后端连接失败：{bootstrapError}
              </span>
            ) : (
              "正在连接后端…"
            )}
          </div>
        </header>
        <MessageList messages={messages} sessionId={sessionId} />
        <Composer
          onSend={(t) => send(t, selected)}
          disabled={sending || !sessionId}
        />
      </main>
    </div>
  );
}
