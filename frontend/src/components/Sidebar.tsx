import type { Conversation } from "../types";
import { ConversationList } from "./ConversationList";
import { UserMenu } from "./UserMenu";

export function Sidebar({
  conversations,
  conversationsLoading,
  currentConversationId,
  onSelectConversation,
  onDeleteConversation,
  categories,
  selected,
  onToggle,
  onClearCategories,
  onNewChat,
}: {
  conversations: Conversation[];
  conversationsLoading: boolean;
  currentConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  categories: string[];
  selected: string[];
  onToggle: (c: string) => void;
  onClearCategories: () => void;
  onNewChat: () => void;
}) {
  return (
    <aside className="w-72 shrink-0 border-r border-gray-200 bg-panel/70 backdrop-blur-sm flex flex-col">
      <div className="px-3 py-3 border-b border-gray-200">
        <button
          type="button"
          onClick={onNewChat}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
        >
          ＋ 新建对话
        </button>
      </div>

      <div className="px-2 py-3 overflow-y-auto flex-1">
        <ConversationList
          conversations={conversations}
          currentId={currentConversationId}
          onSelect={onSelectConversation}
          onDelete={onDeleteConversation}
          loading={conversationsLoading}
        />

        {categories.length > 0 && (
          <section className="mt-5 px-2">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-semibold text-muted uppercase tracking-wider">
                限定分类
              </h2>
              {selected.length > 0 && (
                <button
                  type="button"
                  onClick={onClearCategories}
                  className="text-xs text-accent hover:underline"
                >
                  清空
                </button>
              )}
            </div>
            <p className="text-xs text-muted mt-1">留空 = 全部</p>
            <div className="mt-2 space-y-1">
              {categories.map((c) => {
                const on = selected.includes(c);
                return (
                  <label
                    key={c}
                    className="flex items-center gap-2 text-sm cursor-pointer select-none"
                  >
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => onToggle(c)}
                      className="accent-blue-600"
                    />
                    <span>{c}</span>
                  </label>
                );
              })}
            </div>
          </section>
        )}
      </div>

      <div className="px-2 py-2 border-t border-gray-200">
        <UserMenu />
      </div>
    </aside>
  );
}
