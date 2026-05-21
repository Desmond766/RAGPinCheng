import { useState } from "react";
import type { Source } from "../types";

function locator(s: Source): string {
  if (s.doc_type === "transcript" && s.start_time) return `🎬 @${s.start_time}`;
  return `§${s.section_path || "(无)"}`;
}

export function SourcesPanel({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (!sources?.length) return null;
  return (
    <div className="mt-3 border border-gray-200 rounded-lg bg-white/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-3 py-2 text-sm text-muted hover:bg-gray-50 rounded-lg flex items-center justify-between"
      >
        <span>📎 参考来源 ({sources.length})</span>
        <span className="text-xs">{open ? "收起" : "展开"}</span>
      </button>
      {open && (
        <ol className="px-4 py-2 space-y-3 text-sm">
          {sources.map((s, i) => (
            <li key={s.parent_id + i} className="border-l-2 border-gray-200 pl-3">
              <div className="font-medium text-ink">
                {i + 1}. [{s.doc_title}] <span className="text-muted">{locator(s)}</span>
              </div>
              <div className="text-xs text-muted mt-0.5">
                分类: <code className="bg-gray-100 px-1 rounded">{s.category || "—"}</code>
              </div>
              <div className="text-xs text-gray-600 mt-1 whitespace-pre-wrap line-clamp-6">
                {s.text.length > 400 ? s.text.slice(0, 400) + "…" : s.text}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
