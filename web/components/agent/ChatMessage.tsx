"use client";

import { UiMessage } from "@/lib/agent-types";
import MarkdownRenderer from "@/components/shared/MarkdownRenderer";
import ThinkingProcess from "@/components/agent/ThinkingProcess";

interface Props {
  message: UiMessage;
}

export default function ChatMessage({ message }: Props) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end mb-3">
        <div
          className="max-w-[80%] bg-blue-500 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap"
          data-testid="chat-message-user"
        >
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start mb-3">
      <div className="flex items-start gap-2 max-w-[85%]">
        <div
          className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center text-xs flex-shrink-0 mt-0.5"
          aria-hidden="true"
        >
          AI
        </div>
        <div
          className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm"
          data-testid="chat-message-assistant"
        >
          {(message.thinking?.length || message.toolSteps?.length) ? (
            <ThinkingProcess
              thinking={message.thinking}
              toolSteps={message.toolSteps}
              isStreaming={message.streaming}
            />
          ) : null}
          {message.streaming && message.content === "" ? (
            <span className="flex gap-1 items-center py-1">
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:0ms]" />
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:300ms]" />
            </span>
          ) : (
            <MarkdownRenderer content={message.content} />
          )}
          {message.streaming && message.content !== "" && (
            <span className="inline-block w-1 h-3.5 bg-gray-400 animate-pulse ml-0.5 align-middle" />
          )}
        </div>
      </div>
    </div>
  );
}
