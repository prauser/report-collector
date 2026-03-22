"use client";

import { ChatSession } from "@/lib/agent-types";

interface Props {
  sessions: ChatSession[];
  currentSessionId: number | null;
  onSelect: (sessionId: number) => void;
  onNewChat: () => void;
  onDelete: (sessionId: number) => void;
  loading?: boolean;
}

function formatRelativeDate(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "오늘";
  if (diffDays === 1) return "어제";
  if (diffDays < 7) return `${diffDays}일 전`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}주 전`;
  return date.toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}

export default function SessionList({
  sessions,
  currentSessionId,
  onSelect,
  onNewChat,
  onDelete,
  loading = false,
}: Props) {
  return (
    <div className="flex flex-col h-full">
      {/* New chat button */}
      <div className="p-3 border-b border-gray-200">
        <button
          type="button"
          onClick={onNewChat}
          aria-label="새 대화 시작"
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-500 text-white text-sm font-medium hover:bg-blue-600 active:bg-blue-700 transition-colors"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="currentColor"
            className="w-4 h-4 flex-shrink-0"
            aria-hidden="true"
          >
            <path
              fillRule="evenodd"
              d="M12 3.75a.75.75 0 01.75.75v6.75h6.75a.75.75 0 010 1.5h-6.75v6.75a.75.75 0 01-1.5 0v-6.75H4.5a.75.75 0 010-1.5h6.75V4.5a.75.75 0 01.75-.75z"
              clipRule="evenodd"
            />
          </svg>
          새 대화
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="px-3 py-4 text-sm text-gray-400 text-center">
            불러오는 중...
          </div>
        )}

        {!loading && sessions.length === 0 && (
          <div className="px-3 py-4 text-sm text-gray-400 text-center">
            대화 내역이 없습니다
          </div>
        )}

        {!loading &&
          sessions.map((session) => {
            const isActive = session.id === currentSessionId;
            return (
              <div
                key={session.id}
                className={[
                  "group flex items-start gap-1 px-3 py-2.5 cursor-pointer transition-colors",
                  isActive
                    ? "bg-blue-50 border-r-2 border-blue-500"
                    : "hover:bg-gray-50",
                ].join(" ")}
                onClick={() => onSelect(session.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onSelect(session.id)}
                aria-label={`대화 선택: ${session.title}`}
                aria-current={isActive ? "true" : undefined}
              >
                <div className="flex-1 min-w-0">
                  <p
                    className={[
                      "text-sm truncate",
                      isActive ? "text-blue-700 font-medium" : "text-gray-800",
                    ].join(" ")}
                  >
                    {session.title || "새 대화"}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {formatRelativeDate(session.updated_at)} ·{" "}
                    {session.message_count}개 메시지
                  </p>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(session.id);
                  }}
                  aria-label={`대화 삭제: ${session.title}`}
                  className={[
                    "flex-shrink-0 p-1 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors",
                    "opacity-0 group-hover:opacity-100 focus:opacity-100",
                  ].join(" ")}
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    className="w-3.5 h-3.5"
                    aria-hidden="true"
                  >
                    <path
                      fillRule="evenodd"
                      d="M16.5 4.478v.227a48.816 48.816 0 013.878.512.75.75 0 11-.256 1.478l-.209-.035-1.005 13.07a3 3 0 01-2.991 2.77H8.084a3 3 0 01-2.991-2.77L4.087 6.66l-.209.035a.75.75 0 01-.256-1.478A48.567 48.567 0 017.5 4.705v-.227c0-1.564 1.213-2.9 2.816-2.951a52.662 52.662 0 013.369 0c1.603.051 2.815 1.387 2.815 2.951zm-6.136-1.452a51.196 51.196 0 013.273 0C14.39 3.05 15 3.684 15 4.478v.113a49.488 49.488 0 00-6 0v-.113c0-.794.609-1.428 1.364-1.452zm-.355 5.945a.75.75 0 10-1.5.058l.347 9a.75.75 0 101.499-.058l-.346-9zm5.48.058a.75.75 0 10-1.498-.058l-.347 9a.75.75 0 001.5.058l.345-9z"
                      clipRule="evenodd"
                    />
                  </svg>
                </button>
              </div>
            );
          })}
      </div>
    </div>
  );
}
