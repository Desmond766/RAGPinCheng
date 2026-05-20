import { useEffect, useRef, useState } from "react";

export function Composer({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 240) + "px";
  }, [text]);

  function submit() {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
  }

  return (
    <div className="border-t border-gray-200 bg-bg px-4 py-3">
      <div className="max-w-3xl mx-auto flex items-end gap-2">
        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="请输入问题…  （Enter 发送，Shift+Enter 换行）"
          className="flex-1 resize-none rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-accent/60"
        />
        <button
          type="button"
          onClick={submit}
          disabled={disabled || !text.trim()}
          className="rounded-xl bg-accent text-white px-4 py-3 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-blue-700"
        >
          {disabled ? "回答中…" : "发送"}
        </button>
      </div>
      <div className="text-center text-xs text-muted mt-2">
        资料来源仅供参考。生成内容可能存在差错，请以正式规范文本为准。
      </div>
    </div>
  );
}
