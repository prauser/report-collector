"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, useCallback } from "react";
import { api, PendingMessage } from "@/lib/api";

const CHANNEL_COLORS: Record<string, string> = {
  "@repostory123": "bg-blue-100 text-blue-800",
  "@companyreport": "bg-green-100 text-green-800",
  "@searfin": "bg-purple-100 text-purple-800",
};

function channelBadge(ch: string) {
  const cls = CHANNEL_COLORS[ch] ?? "bg-gray-100 text-gray-700";
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-mono ${cls}`}>
      {ch}
    </span>
  );
}

export default function PendingPage() {
  const [items, setItems] = useState<PendingMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [resolving, setResolving] = useState<number | null>(null);
  const limit = 20;

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [listRes, statsRes] = await Promise.all([
        api.pending.list({ status: "pending", limit, offset }),
        api.pending.stats(),
      ]);
      setItems(listRes.items);
      setTotal(listRes.total);
      setStats(statsRes);
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => { fetchData(); }, [fetchData]);

  async function handleResolve(id: number, decision: "broker_report" | "discarded") {
    setResolving(id);
    try {
      await api.pending.resolve(id, decision);
      setItems((prev) => prev.filter((m) => m.id !== id));
      setTotal((t) => t - 1);
    } finally {
      setResolving(null);
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">검토 대기 메시지</h1>
        <a href="/" className="text-sm text-blue-600 hover:underline">← 리포트 목록</a>
      </div>

      {/* 상태 요약 */}
      <div className="flex gap-4 mb-6">
        {Object.entries(stats).map(([status, count]) => (
          <div key={status} className="bg-white border rounded-lg px-4 py-3 text-center min-w-[100px]">
            <div className="text-2xl font-bold">{count}</div>
            <div className="text-xs text-gray-500 mt-1">{status}</div>
          </div>
        ))}
      </div>

      {loading ? (
        <div className="text-center py-16 text-gray-400">불러오는 중...</div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-gray-400">검토 대기 메시지가 없습니다</div>
      ) : (
        <>
          <div className="text-sm text-gray-500 mb-3">총 {total}건</div>
          <div className="space-y-4">
            {items.map((msg) => (
              <div key={msg.id} className="bg-white border rounded-lg p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    {/* 헤더 */}
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      {channelBadge(msg.source_channel)}
                      <span className="text-xs text-gray-400">
                        msg #{msg.source_message_id}
                      </span>
                      <span className="text-xs text-gray-400">
                        {new Date(msg.created_at).toLocaleString("ko-KR")}
                      </span>
                      {msg.s2a_label && (
                        <span className="bg-yellow-100 text-yellow-800 text-xs px-2 py-0.5 rounded">
                          {msg.s2a_label}
                        </span>
                      )}
                    </div>

                    {/* LLM 이유 */}
                    {msg.s2a_reason && (
                      <div className="text-xs text-amber-700 bg-amber-50 px-3 py-1.5 rounded mb-2">
                        LLM: {msg.s2a_reason}
                      </div>
                    )}

                    {/* 원문 */}
                    <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans leading-relaxed bg-gray-50 p-3 rounded max-h-48 overflow-y-auto">
                      {msg.raw_text ?? "(텍스트 없음)"}
                    </pre>

                    {/* PDF URL */}
                    {msg.pdf_url && (
                      <a
                        href={msg.pdf_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-block mt-2 text-xs text-blue-600 hover:underline truncate max-w-full"
                      >
                        📄 PDF 링크
                      </a>
                    )}
                  </div>

                  {/* 액션 버튼 */}
                  <div className="flex flex-col gap-2 shrink-0">
                    <button
                      onClick={() => handleResolve(msg.id, "broker_report")}
                      disabled={resolving === msg.id}
                      className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
                    >
                      리포트
                    </button>
                    <button
                      onClick={() => handleResolve(msg.id, "discarded")}
                      disabled={resolving === msg.id}
                      className="px-3 py-1.5 bg-gray-200 text-gray-700 text-sm rounded hover:bg-gray-300 disabled:opacity-50"
                    >
                      버리기
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* 페이지네이션 */}
          {total > limit && (
            <div className="flex justify-center gap-2 mt-6">
              <button
                onClick={() => setOffset(Math.max(0, offset - limit))}
                disabled={offset === 0}
                className="px-3 py-1 border rounded text-sm disabled:opacity-40"
              >
                이전
              </button>
              <span className="px-3 py-1 text-sm text-gray-600">
                {Math.floor(offset / limit) + 1} / {Math.ceil(total / limit)}
              </span>
              <button
                onClick={() => setOffset(offset + limit)}
                disabled={offset + limit >= total}
                className="px-3 py-1 border rounded text-sm disabled:opacity-40"
              >
                다음
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
