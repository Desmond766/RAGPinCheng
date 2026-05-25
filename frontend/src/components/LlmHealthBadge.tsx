import type { LlmHealth } from "../types";

type State = "ok" | "degraded" | "down" | "unknown";

function classify(h: LlmHealth | null): State {
  if (!h) return "unknown";
  if (h.ok) return "ok";
  const anyUp = h.models.some((m) => m.ok);
  return anyUp ? "degraded" : "down";
}

const DOT_CLASS: Record<State, string> = {
  ok: "bg-green-500",
  degraded: "bg-amber-500",
  down: "bg-red-500",
  unknown: "bg-gray-400",
};

const LABEL: Record<State, string> = {
  ok: "LLM 正常",
  degraded: "LLM 部分异常",
  down: "LLM 不可用",
  unknown: "LLM 状态未知",
};

export function LlmHealthBadge({
  health,
  loading,
  onRefresh,
}: {
  health: LlmHealth | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const state = classify(health);
  const title = health
    ? health.models
        .map(
          (m) =>
            `${m.role === "rewrite" ? "改写" : "生成"} · ${m.model}: ` +
            (m.ok ? `OK (${m.latency_ms ?? "—"} ms)` : `FAIL — ${m.error ?? "未知错误"}`),
        )
        .join("\n")
    : "尚未检测";

  return (
    <button
      type="button"
      onClick={onRefresh}
      disabled={loading}
      title={`${LABEL[state]}\n\n${title}\n\n点击重新检测`}
      className={
        "ml-2 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs " +
        "border border-gray-200 hover:bg-gray-50 disabled:opacity-60"
      }
    >
      <span
        className={
          "inline-block w-2 h-2 rounded-full " +
          DOT_CLASS[state] +
          (state === "down" || state === "degraded" ? " animate-pulse" : "")
        }
      />
      <span className="text-muted">{loading ? "检测中…" : LABEL[state]}</span>
    </button>
  );
}
