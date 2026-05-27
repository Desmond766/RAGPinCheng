import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { ChatMessage } from "../types";
import { SourcesPanel } from "./SourcesPanel";
import { DebugPanel } from "./DebugPanel";
import { FeedbackBar } from "./FeedbackBar";
import {
  dispatchCitation,
  linkifyCitations,
  resolveCitation,
} from "./citations";

// Demote inline `$$...$$` to `$...$` so KaTeX renders it inline instead of as
// a block break. Standalone display blocks on their own line are kept.
function normalizeMath(src: string): string {
  return src.replace(/\$\$([^\n$]+?)\$\$/g, (match, body, offset, full) => {
    const before = full[offset - 1];
    const after = full[offset + match.length];
    const inline =
      (before !== undefined && before !== "\n") ||
      (after !== undefined && after !== "\n");
    return inline ? `$${body}$` : match;
  });
}

function StageIndicator({ msg }: { msg: ChatMessage }) {
  const stage = msg.stage;
  if (!stage || stage === "done") return null;
  let label = "";
  if (stage === "retrieving") label = "🔎 改写问题并检索资料中…";
  else if (stage === "generating") {
    const n = msg.prep?.final_count ?? msg.sources?.length ?? 0;
    label = `📝 已检索到 ${n} 条来源，正在生成回答…`;
  } else if (stage === "streaming" && !msg.content) label = "📝 正在生成回答…";
  if (!label) return null;
  return <div className="text-muted text-sm">{label}</div>;
}

export function Message({
  msg,
  conversationId,
  turnIndex,
}: {
  msg: ChatMessage;
  conversationId: string | null;
  turnIndex: number;
}) {
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
            <div className={"prose-tight " + (msg.streaming && msg.content ? "caret" : "")}>
              {msg.content ? (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                  components={{
                    a: ({ href, children, ...props }) => {
                      if (href && href.startsWith("#cite-")) {
                        return (
                          <a
                            href={href}
                            onClick={(e) => {
                              e.preventDefault();
                              const idx = resolveCitation(href, msg.sources || []);
                              if (idx >= 0) {
                                dispatchCitation({ messageId: msg.id, sourceIndex: idx });
                              }
                            }}
                            className="text-accent underline decoration-dotted underline-offset-2 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded px-0.5 cursor-pointer"
                          >
                            {children}
                          </a>
                        );
                      }
                      return (
                        <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                          {children}
                        </a>
                      );
                    },
                  }}
                >
                  {linkifyCitations(normalizeMath(msg.content))}
                </ReactMarkdown>
              ) : null}
              <StageIndicator msg={msg} />
            </div>
            {msg.error && (
              <div className="mt-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
                ⚠️ {msg.error}
              </div>
            )}
            {msg.sources && msg.sources.length > 0 && (
              <SourcesPanel
                sources={msg.sources}
                messageId={msg.id}
                conversationId={conversationId}
              />
            )}
            {!msg.streaming && !msg.error && msg.content && (
              <FeedbackBar msg={msg} conversationId={conversationId} turnIndex={turnIndex} />
            )}
            <DebugPanel msg={msg} />
          </>
        )}
      </div>
    </div>
  );
}
