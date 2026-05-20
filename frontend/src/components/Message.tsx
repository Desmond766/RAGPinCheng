import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../types";
import { SourcesPanel } from "./SourcesPanel";
import { DebugPanel } from "./DebugPanel";

export function Message({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`w-full flex ${isUser ? "justify-end" : "justify-start"} px-4`}>
      <div
        className={
          "max-w-3xl w-full rounded-2xl px-4 py-3 " +
          (isUser
            ? "bg-accent text-white ml-12"
            : "bg-panel border border-gray-200 mr-12")
        }
      >
        {isUser ? (
          <div className="whitespace-pre-wrap break-words">{msg.content}</div>
        ) : (
          <>
            <div className={"prose-tight " + (msg.streaming ? "caret" : "")}>
              {msg.content ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
              ) : msg.streaming ? (
                <span className="text-muted">检索中…</span>
              ) : null}
            </div>
            {msg.error && (
              <div className="mt-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
                ⚠️ {msg.error}
              </div>
            )}
            {msg.sources && msg.sources.length > 0 && (
              <SourcesPanel sources={msg.sources} />
            )}
            <DebugPanel msg={msg} />
          </>
        )}
      </div>
    </div>
  );
}
