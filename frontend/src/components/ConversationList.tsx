import type { Conversation } from "../types";

function formatRelative(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 30 * 86400) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function ConversationList({
  conversations,
  currentId,
  onSelect,
  onDelete,
  loading,
}: {
  conversations: Conversation[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  loading: boolean;
}) {
  if (loading && conversations.length === 0) {
    return <div className="px-2 py-3 text-xs text-muted">加载对话列表…</div>;
  }
  if (conversations.length === 0) {
    return (
      <div className="px-2 py-3 text-xs text-muted">
        还没有对话。点击上方“+ 新建对话”开始。
      </div>
    );
  }
  return (
    <ul className="space-y-0.5">
      {conversations.map((c) => {
        const active = c.id === currentId;
        return (
          <li key={c.id}>
            <div
              className={
                "group flex items-center gap-1 rounded-lg px-2 py-2 text-sm cursor-pointer " +
                (active
                  ? "bg-accent/10 text-ink"
                  : "hover:bg-gray-100 dark:hover:bg-gray-800 text-ink/90")
              }
              onClick={() => onSelect(c.id)}
              title={c.title}
            >
              <div className="flex-1 min-w-0">
                <div className="truncate">{c.title}</div>
                <div className="text-[11px] text-muted">{formatRelative(c.updated_at)}</div>
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm(`删除对话 “${c.title}”？此操作不可恢复。`)) {
                    onDelete(c.id);
                  }
                }}
                className="opacity-0 group-hover:opacity-100 text-muted hover:text-red-600 text-xs px-1"
                title="删除对话"
              >
                ✕
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
