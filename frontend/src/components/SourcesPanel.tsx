import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { CITATION_EVENT, type CitationDetail } from "./citations";
import type { Source } from "../types";

function locator(s: Source): string {
  if (s.doc_type === "transcript" && s.start_time) return `🎬 @${s.start_time}`;
  const leaf = (s.section_path || "").split(" > ").pop() || "";
  return `§${leaf || "(无)"}`;
}

function breadcrumbParts(section_path: string): string[] {
  return section_path.split(" > ").filter(Boolean);
}

function SourceCard({
  s,
  i,
  id,
  highlight,
  cardRef,
  conversationId,
  messageId,
}: {
  s: Source;
  i: number;
  id: string;
  highlight: boolean;
  cardRef: (el: HTMLLIElement | null) => void;
  conversationId: string | null;
  messageId: string;
}) {
  const [reportOpen, setReportOpen] = useState(false);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const PREVIEW_CHARS = 400;
  const truncated = s.text.length > PREVIEW_CHARS;
  const crumbs = s.doc_type === "transcript" ? [] : breadcrumbParts(s.section_path || "");
  const hasBreadcrumb = crumbs.length > 1;
  const canExpand = truncated;

  async function submit() {
    setSubmitting(true);
    setErr(null);
    try {
      await api.sendFeedback({
        kind: "citation",
        note: note.trim() || undefined,
        conversation_id: conversationId,
        message_id: messageId,
        parent_id: s.parent_id,
        doc_title: s.doc_title,
        section_path: s.section_path,
        start_time: s.start_time,
        category: s.category,
      });
      setSent(true);
      setReportOpen(false);
      setNote("");
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <li
      id={id}
      ref={cardRef}
      className={
        "border-l-2 pl-3 transition-colors duration-500 " +
        (highlight
          ? "border-accent bg-blue-50 dark:bg-blue-900/20"
          : "border-gray-200 dark:border-gray-700")
      }
    >
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium text-ink">
          {i + 1}. [{s.doc_title}] <span className="text-muted">{locator(s)}</span>
        </div>
        <button
          type="button"
          title="报告引用有误"
          onClick={() => setReportOpen((v) => !v)}
          className="text-xs text-muted hover:text-red-600 shrink-0"
        >
          {sent ? "已报告" : "⚠ 报错"}
        </button>
      </div>
      <div className="text-xs text-muted mt-0.5">
        分类: <code className="bg-gray-100 px-1 rounded">{s.category || "—"}</code>
      </div>
      {hasBreadcrumb && (
        <div className="text-xs text-muted mt-1 leading-relaxed break-words">
          § {crumbs.join(" › ")}
        </div>
      )}
      <div
        className={
          "text-xs text-gray-600 mt-1 whitespace-pre-wrap " +
          (expanded ? "max-h-96 overflow-y-auto pr-1" : "line-clamp-6")
        }
      >
        {expanded || !truncated ? s.text : s.text.slice(0, PREVIEW_CHARS) + "…"}
      </div>
      {canExpand && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-xs text-accent hover:underline"
        >
          {expanded ? "收起" : "展开"}
        </button>
      )}
      {reportOpen && (
        <div className="mt-2 flex flex-col gap-1.5">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="可选：为什么这条引用有误？"
            rows={2}
            className="border border-gray-300 rounded p-2 text-xs bg-white dark:bg-gray-800"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="px-2 py-1 rounded bg-red-600 text-white text-xs disabled:opacity-50"
            >
              {submitting ? "提交中…" : "提交报告"}
            </button>
            {err && <span className="text-xs text-red-600">{err}</span>}
          </div>
        </div>
      )}
    </li>
  );
}

export function SourcesPanel({
  sources,
  messageId,
  conversationId,
}: {
  sources: Source[];
  messageId: string;
  conversationId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState<number | null>(null);
  const refs = useRef<Record<number, HTMLLIElement | null>>({});

  useEffect(() => {
    function onCitation(e: Event) {
      const detail = (e as CustomEvent<CitationDetail>).detail;
      if (!detail || detail.messageId !== messageId) return;
      setOpen(true);
      setHighlightIdx(detail.sourceIndex);
      // Wait for the panel to expand, then scroll the card into view.
      requestAnimationFrame(() => {
        const el = refs.current[detail.sourceIndex];
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      window.setTimeout(() => setHighlightIdx(null), 1800);
    }
    window.addEventListener(CITATION_EVENT, onCitation);
    return () => window.removeEventListener(CITATION_EVENT, onCitation);
  }, [messageId]);

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
            <SourceCard
              key={s.parent_id + i}
              s={s}
              i={i}
              id={`src-${messageId}-${i}`}
              highlight={highlightIdx === i}
              cardRef={(el) => {
                refs.current[i] = el;
              }}
              conversationId={conversationId}
              messageId={messageId}
            />
          ))}
        </ol>
      )}
    </div>
  );
}
