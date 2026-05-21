import { useState } from "react";
import type { ChatMessage } from "../types";

export function DebugPanel({ msg }: { msg: ChatMessage }) {
  const [open, setOpen] = useState(false);
  const prep = msg.prep;
  const done = msg.done;
  if (!prep && !done) return null;

  const timings = done?.timings || {};

  return (
    <div className="mt-2 text-xs text-muted">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="underline decoration-dotted hover:text-ink"
      >
        🔍 调试信息
      </button>
      {open && (
        <div className="mt-1 p-2 bg-gray-50 border border-gray-200 rounded">
          {prep?.rewrite_applied && (
            <div>
              🔄 检索改写：<code>{prep.search_query}</code>
            </div>
          )}
          <div>
            timings:{" "}
            {Object.entries(timings).map(([k, v]) => (
              <code key={k} className="mr-2">
                {k}={v.toFixed(2)}s
              </code>
            ))}
          </div>
          <div>
            history_chars: <code>{done?.history_chars ?? prep?.history_chars ?? 0}</code>{" "}
            · budget: <code>{done?.budget ?? prep?.budget ?? 0}</code>
            {prep && (
              <>
                {" "}· fresh: <code>{prep.fresh_count}</code> · final:{" "}
                <code>{prep.final_count}</code>
              </>
            )}
          </div>
          {(done?.sources?.length ?? prep?.used_sources?.length ?? 0) > 0 && (
            <div className="mt-1">
              <div className="font-medium">来源得分:</div>
              <ol className="ml-4 list-decimal">
                {(done?.sources ?? prep?.used_sources ?? []).map((s, i) => (
                  <li key={s.parent_id + i}>
                    <span className="text-ink">{s.doc_title}</span>{" "}
                    · 重排 <code>{s.score.toFixed(4)}</code>{" "}
                    · RRF <code>{s.rrf_score.toFixed(4)}</code>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
