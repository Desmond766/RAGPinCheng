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
        </div>
      )}
    </div>
  );
}
