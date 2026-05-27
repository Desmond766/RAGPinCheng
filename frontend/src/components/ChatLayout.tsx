import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { useChat } from "../hooks/useChat";
import type { Conversation } from "../types";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { Sidebar } from "./Sidebar";

export function ChatLayout() {
  const [categories, setCategories] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [currentId, setCurrentId] = useState<string | null>(null);

  const refreshConversations = useCallback(async () => {
    try {
      const { conversations: list } = await api.listConversations();
      setConversations(list);
      return list;
    } catch {
      return [] as Conversation[];
    } finally {
      setConversationsLoading(false);
    }
  }, []);

  useEffect(() => {
    api.categories().then((r) => setCategories(r.categories)).catch(() => {});
    refreshConversations();
  }, [refreshConversations]);

  const { messages, send, sending, loading } = useChat({
    conversationId: currentId,
    onConversationCreated: (id) => {
      setCurrentId(id);
      refreshConversations();
    },
    onConversationUpdated: () => {
      refreshConversations();
    },
  });

  const onSelectConversation = useCallback((id: string) => {
    setCurrentId(id);
  }, []);

  const onNewChat = useCallback(() => {
    setCurrentId(null);
  }, []);

  const onDeleteConversation = useCallback(
    async (id: string) => {
      try {
        await api.deleteConversation(id);
      } catch (e) {
        console.error(e);
      }
      if (id === currentId) setCurrentId(null);
      refreshConversations();
    },
    [currentId, refreshConversations],
  );

  function toggleCategory(c: string) {
    setSelected((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );
  }

  return (
    <div className="h-full flex">
      <Sidebar
        conversations={conversations}
        conversationsLoading={conversationsLoading}
        currentConversationId={currentId}
        onSelectConversation={onSelectConversation}
        onDeleteConversation={onDeleteConversation}
        categories={categories}
        selected={selected}
        onToggle={toggleCategory}
        onClearCategories={() => setSelected([])}
        onNewChat={onNewChat}
      />
      <main className="flex-1 flex flex-col min-w-0">
        <header className="px-6 py-3 border-b border-gray-200 bg-bg/80 backdrop-blur-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-lg">📚</span>
            <span className="font-semibold">品成 BIM 知识库</span>
          </div>
          {loading && <div className="text-xs text-muted">加载历史…</div>}
        </header>
        <MessageList messages={messages} conversationId={currentId} />
        <Composer
          onSend={(t) => send(t, selected)}
          disabled={sending || loading}
        />
      </main>
    </div>
  );
}
