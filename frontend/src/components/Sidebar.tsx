import type { ApiConfig, Health } from "../types";

export function Sidebar({
  categories,
  selected,
  onToggle,
  onClearCategories,
  onNewChat,
  config,
  health,
  turnIndex,
}: {
  categories: string[];
  selected: string[];
  onToggle: (c: string) => void;
  onClearCategories: () => void;
  onNewChat: () => void;
  config: ApiConfig | null;
  health: Health | null;
  turnIndex: number;
}) {
  return (
    <aside className="w-72 shrink-0 border-r border-gray-200 bg-panel/70 backdrop-blur-sm flex flex-col">
      <div className="px-4 py-4 border-b border-gray-200">
        <button
          type="button"
          onClick={onNewChat}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50"
        >
          ＋ 新建对话
        </button>
      </div>

      <div className="px-4 py-4 overflow-y-auto flex-1 space-y-5">
        <section>
          <h2 className="text-xs font-semibold text-muted uppercase tracking-wider">
            语料库
          </h2>
          <div className="mt-2 text-sm space-y-1">
            <div>
              子块 (Qdrant): <code>{health?.children ?? "—"}</code>
            </div>
            <div>
              父段落 (SQLite): <code>{health?.parents ?? "—"}</code>
            </div>
            <div>
              当前轮次: <code>{turnIndex}</code>
            </div>
          </div>
        </section>

        <section>
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
            {categories.length === 0 && (
              <div className="text-xs text-muted">（无可用分类）</div>
            )}
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

        <section>
          <h2 className="text-xs font-semibold text-muted uppercase tracking-wider">
            模型
          </h2>
          <div className="mt-2 text-xs space-y-1 text-muted">
            <div>嵌入: <code>{config?.embed_model || "—"}</code></div>
            <div>
              重排:{" "}
              <code>
                {config?.rerank_enabled ? config.reranker_model : "— 已禁用"}
              </code>
            </div>
            <div>生成: <code>{config?.llm_model || "—"}</code></div>
            <div>集合: <code>{config?.collection || "—"}</code></div>
          </div>
        </section>
      </div>
    </aside>
  );
}
