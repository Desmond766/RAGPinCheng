import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";
import { Message } from "./Message";

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  // Auto-scroll on every render so streaming tokens stay in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  });

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-center px-6">
        <div className="max-w-lg">
          <div className="text-4xl mb-3">📚</div>
          <h1 className="text-xl font-semibold text-ink">品诚 BIM 知识库</h1>
          <p className="text-muted mt-2">
            统一管理公司多年积累的<strong>行业规范与标准</strong>、<strong>客户要求</strong>、
            <strong>内部标准</strong>、<strong>项目资料</strong>与<strong>培训视频</strong>，
            帮助员工快速查标准、查经验，并辅助新人培训。
          </p>
          <p className="text-muted mt-3 text-sm">
            试着问：
            <br />
            <code className="text-xs">Revit 建模交付时的命名规则是什么？</code>
            <br />
            <code className="text-xs">XX 客户对图层有哪些特殊要求？</code>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto py-6 space-y-6">
      {messages.map((m) => (
        <Message key={m.id} msg={m} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
