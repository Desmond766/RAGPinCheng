import { useState } from "react";
import { api } from "../api/client";
import type { ChatMessage } from "../types";

export function FeedbackBar({
  msg,
  sessionId,
  turnIndex,
}: {
  msg: ChatMessage;
  sessionId: string | null;
  turnIndex: number;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    const trimmed = note.trim();
    if (!trimmed) {
      setErr("请填写具体原因");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await api.sendFeedback({
        kind: "answer",
        rating: "down",
        note: trimmed,
        session_id: sessionId,
        turn_index: turnIndex,
        message_id: msg.id,
        query: msg.query,
        answer_text: msg.content,
      });
      setSent(true);
      setOpen(false);
      setNote("");
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  if (sent) {
    return (
      <div className="mt-3 text-xs text-green-600 dark:text-green-400">
        已收到反馈，谢谢。
      </div>
    );
  }

  return (
    <div className="mt-3 flex flex-col gap-2 text-xs">
      {!open ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="self-start px-2 py-1 rounded border border-gray-300 text-muted hover:bg-gray-50 hover:text-red-600"
        >
          👎 这个回答不好
        </button>
      ) : (
        <div className="flex flex-col gap-1.5">
          <label className="text-muted">请描述哪里不好（必填）：</label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="例如：缺少 XX 来源 / 引用错误 / 答非所问 / 数值有误…"
            rows={3}
            autoFocus
            className="border border-gray-300 rounded p-2 text-xs bg-white dark:bg-gray-800"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={submitting || !note.trim()}
              className="px-2 py-1 rounded bg-red-600 text-white text-xs disabled:opacity-50"
            >
              {submitting ? "提交中…" : "提交反馈"}
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setNote("");
                setErr(null);
              }}
              disabled={submitting}
              className="px-2 py-1 rounded border border-gray-300 text-xs"
            >
              取消
            </button>
            {err && <span className="text-red-600">{err}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
