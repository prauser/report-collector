"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, useRef, useCallback, useId } from "react";
import { api } from "@/lib/api";
import type { ChatSession, UiMessage } from "@/lib/agent-types";
import ChatMessage from "@/components/agent/ChatMessage";
import ChatInput from "@/components/agent/ChatInput";
import SessionList from "@/components/agent/SessionList";

export default function AgentPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const idPrefix = useId();
  const msgCounter = useRef(0);

  // Stable makeId: idPrefix from useId is stable, msgCounter is a ref
  const makeId = useCallback(() => {
    msgCounter.current += 1;
    return `${idPrefix}-${msgCounter.current}`;
  }, [idPrefix]);

  // Abort any in-progress stream on unmount
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  // Auto-scroll to bottom when messages update
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Load sessions on mount
  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const data = await api.agent.getSessions();
      setSessions(data);
    } catch {
      // Non-fatal; show empty state
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Load messages when session changes
  const loadMessages = useCallback(
    async (sessionId: number) => {
      setMessagesLoading(true);
      setMessages([]);
      setError(null);
      try {
        const data = await api.agent.getMessages(sessionId);
        const uiMessages: UiMessage[] = data.map((m) => ({
          id: makeId(),
          role: m.role,
          content: m.content,
          streaming: false,
        }));
        setMessages(uiMessages);
      } catch {
        setError("메시지를 불러오지 못했습니다.");
      } finally {
        setMessagesLoading(false);
      }
    },
    [makeId]
  );

  const handleSelectSession = useCallback(
    (sessionId: number) => {
      if (isStreaming) return;
      setCurrentSessionId(sessionId);
      loadMessages(sessionId);
      setSidebarOpen(false);
    },
    [isStreaming, loadMessages]
  );

  const handleNewChat = useCallback(() => {
    if (isStreaming) return;
    setCurrentSessionId(null);
    setMessages([]);
    setError(null);
    setSidebarOpen(false);
  }, [isStreaming]);

  const handleDeleteSession = useCallback(
    async (sessionId: number) => {
      if (isStreaming) return;
      try {
        await api.agent.deleteSession(sessionId);
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
        if (currentSessionId === sessionId) {
          setCurrentSessionId(null);
          setMessages([]);
        }
      } catch {
        setError("세션 삭제에 실패했습니다.");
      }
    },
    [isStreaming, currentSessionId]
  );

  const handleSend = useCallback(async () => {
    const text = inputValue.trim();
    if (!text || isStreaming) return;

    // Abort any in-progress stream before starting a new one
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setInputValue("");
    setError(null);

    const userMsgId = makeId();
    const assistantMsgId = makeId();

    const userMsg: UiMessage = { id: userMsgId, role: "user", content: text };
    const assistantMsg: UiMessage = {
      id: assistantMsgId,
      role: "assistant",
      content: "",
      streaming: true,
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);

    // Track whether this was a new chat (no session yet)
    const wasNewChat = currentSessionId === null;

    try {
      const stream = api.agent.chat(text, currentSessionId ?? undefined, controller.signal);
      for await (const event of stream) {
        if (event.type === "text") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId
                ? { ...m, content: m.content + event.text }
                : m
            )
          );
        } else if (event.type === "done") {
          // Mark streaming as complete
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId ? { ...m, streaming: false } : m
            )
          );
        } else if (event.type === "error") {
          const errMsg = event.message || "응답 중 오류가 발생했습니다.";
          setError(errMsg);
          // Remove the empty assistant bubble or set error content on it
          setMessages((prev) =>
            prev.filter((m) => m.id !== assistantMsgId || m.content !== "")
          );
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId ? { ...m, streaming: false } : m
            )
          );
        }
      }

      // After stream completes, refresh sessions
      // If this was a new chat, auto-select the newest session
      if (wasNewChat) {
        const updatedSessions = await api.agent.getSessions();
        setSessions(updatedSessions);
        if (updatedSessions.length > 0) {
          setCurrentSessionId(updatedSessions[0].id);
        }
      } else {
        void loadSessions();
      }
    } catch (err) {
      // Ignore AbortError (user navigated away or started new send)
      if (err instanceof Error && err.name === "AbortError") return;
      const msg = err instanceof Error ? err.message : "알 수 없는 오류";
      setError(msg);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId ? { ...m, streaming: false } : m
        )
      );
    } finally {
      setIsStreaming(false);
    }
  }, [inputValue, isStreaming, currentSessionId, loadSessions, makeId]);

  const welcomeScreen = messages.length === 0 && !messagesLoading;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] -mx-4 -my-6 overflow-hidden">
      {/* Sidebar overlay for mobile */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/30 z-20 md:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar */}
      <aside
        className={[
          "fixed md:relative inset-y-0 left-0 z-30 md:z-auto",
          "w-64 bg-white border-r border-gray-200",
          "flex flex-col flex-shrink-0",
          "transition-transform duration-200 ease-in-out",
          sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        ].join(" ")}
        aria-label="대화 세션 목록"
      >
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-gray-200">
          <span className="text-sm font-semibold text-gray-700">이전 대화</span>
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            className="md:hidden p-1 rounded text-gray-400 hover:text-gray-600"
            aria-label="사이드바 닫기"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
              <path fillRule="evenodd" d="M5.47 5.47a.75.75 0 011.06 0L12 10.94l5.47-5.47a.75.75 0 111.06 1.06L13.06 12l5.47 5.47a.75.75 0 11-1.06 1.06L12 13.06l-5.47 5.47a.75.75 0 01-1.06-1.06L10.94 12 5.47 6.53a.75.75 0 010-1.06z" clipRule="evenodd" />
            </svg>
          </button>
        </div>
        <SessionList
          sessions={sessions}
          currentSessionId={currentSessionId}
          onSelect={handleSelectSession}
          onNewChat={handleNewChat}
          onDelete={handleDeleteSession}
          loading={sessionsLoading}
        />
      </aside>

      {/* Main chat area */}
      <div className="flex flex-col flex-1 min-w-0 bg-gray-50">
        {/* Top bar */}
        <div className="flex items-center gap-3 px-4 py-2.5 bg-white border-b border-gray-200 flex-shrink-0">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="md:hidden p-1.5 rounded-lg text-gray-500 hover:bg-gray-100"
            aria-label="대화 목록 열기"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
              <path fillRule="evenodd" d="M3 6.75A.75.75 0 013.75 6h16.5a.75.75 0 010 1.5H3.75A.75.75 0 013 6.75zM3 12a.75.75 0 01.75-.75h16.5a.75.75 0 010 1.5H3.75A.75.75 0 013 12zm0 5.25a.75.75 0 01.75-.75h16.5a.75.75 0 010 1.5H3.75a.75.75 0 01-.75-.75z" clipRule="evenodd" />
            </svg>
          </button>
          <div className="flex-1">
            <h1 className="text-sm font-semibold text-gray-800">
              {currentSessionId
                ? sessions.find((s) => s.id === currentSessionId)?.title ?? "AI Agent"
                : "AI Agent"}
            </h1>
            <p className="text-xs text-gray-400">리포트 분석 AI 어시스턴트</p>
          </div>
          {/* New chat button (desktop) */}
          <button
            type="button"
            onClick={handleNewChat}
            disabled={isStreaming}
            className="hidden md:flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50 transition-colors"
            aria-label="새 대화 시작"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
              <path fillRule="evenodd" d="M12 3.75a.75.75 0 01.75.75v6.75h6.75a.75.75 0 010 1.5h-6.75v6.75a.75.75 0 01-1.5 0v-6.75H4.5a.75.75 0 010-1.5h6.75V4.5a.75.75 0 01.75-.75z" clipRule="evenodd" />
            </svg>
            새 대화
          </button>
        </div>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-4 py-4" aria-live="polite" aria-label="대화 메시지">
          {/* Welcome screen */}
          {welcomeScreen && (
            <div className="flex flex-col items-center justify-center h-full text-center px-6 pb-10">
              <div className="w-14 h-14 rounded-full bg-blue-100 flex items-center justify-center mb-4 text-2xl" aria-hidden="true">
                🤖
              </div>
              <h2 className="text-lg font-semibold text-gray-800 mb-2">
                AI 리포트 어시스턴트
              </h2>
              <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                수집된 리포트 데이터를 기반으로 종목, 섹터, 투자 트렌드에 대해 질문해 보세요.
              </p>
              <div className="mt-6 flex flex-col gap-2 w-full max-w-xs">
                {[
                  "삼성전자 최근 리포트 요약해줘",
                  "반도체 섹터 전망 어때?",
                  "이번 달 가장 많이 언급된 종목은?",
                ].map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => setInputValue(prompt)}
                    className="text-left text-sm px-3 py-2.5 rounded-xl border border-gray-200 bg-white hover:bg-gray-50 text-gray-700 transition-colors"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Messages loading */}
          {messagesLoading && (
            <div className="text-center py-10 text-gray-400 text-sm">
              불러오는 중...
            </div>
          )}

          {/* Message list */}
          {!messagesLoading && (
            <div className="max-w-3xl mx-auto">
              {messages.map((msg) => (
                <ChatMessage key={msg.id} message={msg} />
              ))}
            </div>
          )}

          {/* Error banner */}
          {error && (
            <div
              role="alert"
              className="max-w-3xl mx-auto mt-2 px-4 py-2.5 bg-red-50 border border-red-200 rounded-xl text-sm text-red-600"
            >
              {error}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area — fixed to bottom of main content */}
        <div className="flex-shrink-0">
          <ChatInput
            value={inputValue}
            onChange={setInputValue}
            onSend={handleSend}
            disabled={isStreaming}
          />
        </div>
      </div>
    </div>
  );
}
