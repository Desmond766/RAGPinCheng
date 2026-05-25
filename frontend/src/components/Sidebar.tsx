import { useTheme } from "../hooks/useTheme";
import type { ApiConfig, Health, LlmHealth, LlmModelHealth } from "../types";

function modelDot(m: LlmModelHealth | undefined): string {
  if (!m) return "bg-gray-400";
  return m.ok ? "bg-green-500" : "bg-red-500";
}

function modelLine(m: LlmModelHealth | undefined): string {
  if (!m) return "—";
  if (m.ok) return `OK · ${m.latency_ms ?? "—"} ms`;
  return `失败 · ${m.error ?? "未知错误"}`;
}

export function Sidebar({
  categories,
  selected,
  onToggle,
  onClearCategories,
  onNewChat,
  config,
  health,
  llmHealth,
  llmHealthLoading,
  onRefreshLlmHealth,
  turnIndex,
}: {
  categories: string[];
  selected: string[];
  onToggle: (c: string) => void;
  onClearCategories: () => void;
  onNewChat: () => void;
  config: ApiConfig | null;
  health: Health | null;
  llmHealth: LlmHealth | null;
  llmHealthLoading: boolean;
  onRefreshLlmHealth: () => void;
  turnIndex: number;
}) {
  const gen = llmHealth?.models.find((m) => m.role === "generation");
  const rew = llmHealth?.models.find((m) => m.role === "rewrite");
  const [theme, toggleTheme] = useTheme();
  return (
    <aside className="w-72 shrink-0 border-r border-gray-200 bg-panel/70 backdrop-blur-sm flex flex-col">
      <div className="px-4 py-4 border-b border-gray-200 space-y-2">
        <button
          type="button"
          onClick={onNewChat}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50"
        >
          ＋ 新建对话
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 flex items-center justify-center gap-2"
          aria-label="切换主题"
        >
          <span>{theme === "dark" ? "☀️" : "🌙"}</span>
          <span>{theme === "dark" ? "浅色模式" : "深色模式"}</span>
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
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold text-muted uppercase tracking-wider">
              模型
            </h2>
            <button
              type="button"
              onClick={onRefreshLlmHealth}
              disabled={llmHealthLoading}
              className="text-xs text-accent hover:underline disabled:opacity-50"
              title="重新检测 LLM 接口"
            >
              {llmHealthLoading ? "检测中…" : "刷新"}
            </button>
          </div>
          <div className="mt-2 text-xs space-y-1 text-muted">
            <div>嵌入: <code>{config?.embed_model || "—"}</code></div>
            <div>
              重排:{" "}
              <code>
                {config?.rerank_enabled ? config.reranker_model : "— 已禁用"}
              </code>
            </div>
            <div className="flex items-center gap-1.5">
              <span
                className={"inline-block w-2 h-2 rounded-full " + modelDot(gen)}
                title={modelLine(gen)}
              />
              <span>生成:</span>
              <code>{config?.llm_model || "—"}</code>
            </div>
            <div className="ml-3.5 text-[11px] text-muted/80">{modelLine(gen)}</div>
            <div className="flex items-center gap-1.5">
              <span
                className={"inline-block w-2 h-2 rounded-full " + modelDot(rew)}
                title={modelLine(rew)}
              />
              <span>改写:</span>
              <code>{config?.llm_rewrite_model || "—"}</code>
            </div>
            <div className="ml-3.5 text-[11px] text-muted/80">{modelLine(rew)}</div>
            <div>集合: <code>{config?.collection || "—"}</code></div>
            {llmHealth && (
              <div className="pt-1 text-[11px] text-muted/70">
                密钥: <code>{llmHealth.key_masked}</code>
                {llmHealth.cached && " · 来自缓存"}
              </div>
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}
