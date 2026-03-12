"use client";
export const dynamic = "force-dynamic";

import { useEffect, useState, useCallback } from "react";
import { api, BackfillStats } from "@/lib/api";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function pct(n: number, total: number) {
  if (!total) return "0%";
  return `${Math.round((n / total) * 100)}%`;
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "done"
      ? "bg-green-100 text-green-800"
      : status === "running"
      ? "bg-blue-100 text-blue-800"
      : "bg-red-100 text-red-800";
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-medium ${cls}`}>
      {status}
    </span>
  );
}

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const w = max ? Math.round((value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-gray-100 rounded-full h-2">
        <div className={`h-2 rounded-full ${color}`} style={{ width: `${w}%` }} />
      </div>
      <span className="text-xs text-gray-500 w-8 text-right">{w}%</span>
    </div>
  );
}

async function triggerBackfill(channel: string): Promise<{ started: string[]; already_running: string[] }> {
  const res = await fetch(`${BASE}/api/backfill/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

async function fetchRunning(): Promise<string[]> {
  const res = await fetch(`${BASE}/api/backfill/running`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.running ?? [];
}

export default function BackfillPage() {
  const [data, setData] = useState<BackfillStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<string[]>([]);
  const [triggering, setTriggering] = useState<string | null>(null);

  const refresh = useCallback(() => {
    Promise.all([
      api.stats.backfill(),
      fetchRunning(),
    ]).then(([stats, r]) => {
      setData(stats);
      setRunning(r);
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // 실행 중인 채널이 있으면 5초마다 폴링
  useEffect(() => {
    if (running.length === 0) return;
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [running, refresh]);

  const handleRun = async (channel: string) => {
    setTriggering(channel);
    try {
      await triggerBackfill(channel);
      await new Promise((r) => setTimeout(r, 500));
      refresh();
    } catch (e) {
      alert(`실행 실패: ${e}`);
    } finally {
      setTriggering(null);
    }
  };

  const handleRunAll = async () => {
    setTriggering("all");
    try {
      await triggerBackfill("all");
      await new Promise((r) => setTimeout(r, 500));
      refresh();
    } catch (e) {
      alert(`실행 실패: ${e}`);
    } finally {
      setTriggering(null);
    }
  };

  if (loading) return <div className="p-8 text-gray-400">불러오는 중...</div>;
  if (!data) return <div className="p-8 text-red-500">데이터 로드 실패</div>;

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">백필 현황</h1>
        <div className="flex items-center gap-3">
          {running.length > 0 && (
            <span className="text-xs text-blue-600 animate-pulse">
              ● {running.length}개 채널 실행 중
            </span>
          )}
          <button
            onClick={handleRunAll}
            disabled={triggering !== null}
            className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {triggering === "all" ? "시작 중..." : "전체 실행"}
          </button>
          <a href="/" className="text-sm text-blue-600 hover:underline">← 리포트 목록</a>
        </div>
      </div>

      {/* 채널별 누적 현황 */}
      <section>
        <h2 className="text-lg font-semibold mb-3">채널별 누적 현황</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs uppercase">
                <th className="text-left p-3">채널</th>
                <th className="text-right p-3">마지막 실행</th>
                <th className="text-right p-3">최신 msg ID</th>
                <th className="text-right p-3">총 순회</th>
                <th className="text-right p-3">저장</th>
                <th className="text-right p-3">검토 대기</th>
                <th className="text-right p-3">스킵</th>
                <th className="text-right p-3">실행 횟수</th>
                <th className="text-center p-3">실행</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.by_channel.length === 0 ? (
                <tr>
                  <td colSpan={9} className="p-4 text-center text-gray-400">
                    백필 실행 기록 없음
                  </td>
                </tr>
              ) : (
                data.by_channel.map((ch) => {
                  const isRunning = running.includes(ch.channel);
                  return (
                    <tr key={ch.channel} className="hover:bg-gray-50">
                      <td className="p-3 font-mono text-xs">{ch.channel}</td>
                      <td className="p-3 text-right text-gray-600">{ch.last_run_date ?? "-"}</td>
                      <td className="p-3 text-right text-gray-600">{ch.latest_message_id?.toLocaleString() ?? "-"}</td>
                      <td className="p-3 text-right">{ch.total_scanned.toLocaleString()}</td>
                      <td className="p-3 text-right text-green-700 font-medium">{ch.total_saved.toLocaleString()}</td>
                      <td className="p-3 text-right text-amber-600">{ch.total_pending.toLocaleString()}</td>
                      <td className="p-3 text-right text-gray-400">{ch.total_skipped.toLocaleString()}</td>
                      <td className="p-3 text-right">{ch.total_runs}</td>
                      <td className="p-3 text-center">
                        {isRunning ? (
                          <span className="text-xs text-blue-500 animate-pulse">실행 중</span>
                        ) : (
                          <button
                            onClick={() => handleRun(ch.channel)}
                            disabled={triggering !== null}
                            className="text-xs px-2 py-1 bg-gray-100 hover:bg-blue-50 hover:text-blue-600 rounded disabled:opacity-50"
                          >
                            ▶ 실행
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* PDF / AI 분석 커버리지 */}
      <section>
        <h2 className="text-lg font-semibold mb-3">PDF / AI 분석 커버리지</h2>
        <div className="space-y-4">
          {data.pdf_coverage.length === 0 ? (
            <p className="text-gray-400 text-sm">리포트 없음</p>
          ) : (
            data.pdf_coverage.map((c) => (
              <div key={c.channel} className="bg-white border rounded-lg p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="font-mono text-sm">{c.channel}</span>
                  <span className="text-xs text-gray-500">총 {c.total_reports.toLocaleString()}건</span>
                </div>
                <div className="space-y-2">
                  <div>
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span>PDF URL 보유</span>
                      <span>{c.has_pdf_url.toLocaleString()} / {c.total_reports.toLocaleString()} ({pct(c.has_pdf_url, c.total_reports)})</span>
                    </div>
                    <Bar value={c.has_pdf_url} max={c.total_reports} color="bg-blue-400" />
                  </div>
                  <div>
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span>PDF 다운로드 완료</span>
                      <span>{c.pdf_downloaded.toLocaleString()} / {c.has_pdf_url.toLocaleString()} ({pct(c.pdf_downloaded, c.has_pdf_url)})</span>
                    </div>
                    <Bar value={c.pdf_downloaded} max={c.has_pdf_url} color="bg-indigo-400" />
                  </div>
                  <div>
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span>AI 본문 분석 완료</span>
                      <span>{c.ai_analyzed.toLocaleString()} / {c.pdf_downloaded.toLocaleString()} ({pct(c.ai_analyzed, c.pdf_downloaded)})</span>
                    </div>
                    <Bar value={c.ai_analyzed} max={c.pdf_downloaded} color="bg-purple-400" />
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      {/* 최근 실행 히스토리 */}
      <section>
        <h2 className="text-lg font-semibold mb-3">최근 실행 히스토리</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs uppercase">
                <th className="text-left p-3">채널</th>
                <th className="text-left p-3">실행일</th>
                <th className="text-right p-3">msg 범위</th>
                <th className="text-right p-3">순회</th>
                <th className="text-right p-3">저장</th>
                <th className="text-right p-3">대기</th>
                <th className="text-right p-3">스킵</th>
                <th className="text-center p-3">상태</th>
                <th className="text-right p-3">소요</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.recent_runs.map((r, i) => {
                const secs = r.finished_at
                  ? Math.round((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000)
                  : null;
                const range =
                  r.from_message_id && r.to_message_id
                    ? `${r.from_message_id.toLocaleString()} → ${r.to_message_id.toLocaleString()}`
                    : r.to_message_id
                    ? `~ ${r.to_message_id.toLocaleString()}`
                    : "-";
                return (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="p-3 font-mono text-xs">{r.channel}</td>
                    <td className="p-3 text-gray-600">{r.run_date}</td>
                    <td className="p-3 text-right text-gray-500 text-xs">{range}</td>
                    <td className="p-3 text-right">{r.n_scanned.toLocaleString()}</td>
                    <td className="p-3 text-right text-green-700 font-medium">{r.n_saved.toLocaleString()}</td>
                    <td className="p-3 text-right text-amber-600">{r.n_pending.toLocaleString()}</td>
                    <td className="p-3 text-right text-gray-400">{r.n_skipped.toLocaleString()}</td>
                    <td className="p-3 text-center"><StatusBadge status={r.status} /></td>
                    <td className="p-3 text-right text-gray-500 text-xs">
                      {secs !== null ? `${secs}s` : "-"}
                    </td>
                  </tr>
                );
              })}
              {data.recent_runs.length === 0 && (
                <tr>
                  <td colSpan={9} className="p-4 text-center text-gray-400">실행 기록 없음</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
