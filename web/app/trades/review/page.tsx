"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { api, TradeResponse } from "@/lib/api";
import TradeTable from "@/components/trades/TradeTable";
import StatCard from "@/components/shared/StatCard";

type FilterTab = "reason" | "review" | "all";

const TAB_LABELS: { key: FilterTab; label: string }[] = [
  { key: "reason", label: "매매이유 미작성" },
  { key: "review", label: "복기 미작성" },
  { key: "all", label: "전체 미작성" },
];

function isMissing(v: string | null | undefined): boolean {
  return v === null || v === undefined || v.trim() === "";
}

function filterTrades(trades: TradeResponse[], tab: FilterTab): TradeResponse[] {
  if (tab === "reason") return trades.filter((t) => isMissing(t.reason));
  if (tab === "review") return trades.filter((t) => isMissing(t.review));
  // "all" = either reason or review is missing
  return trades.filter((t) => isMissing(t.reason) || isMissing(t.review));
}

function completionPct(trades: TradeResponse[], field: "reason" | "review"): number {
  if (trades.length === 0) return 100;
  const done = trades.filter((t) => !isMissing(t[field])).length;
  return Math.round((done / trades.length) * 100);
}

export default function ReviewPage() {
  const [allTrades, setAllTrades] = useState<TradeResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<FilterTab>("all");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.trades.list({ limit: 500, offset: 0 });
      setAllTrades(res.items);
    } catch {
      setError("데이터를 불러오는데 실패했습니다. 다시 시도해주세요.");
      setAllTrades([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  function handleUpdate(updated: TradeResponse) {
    setAllTrades((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  }

  const filtered = filterTrades(allTrades, activeTab);
  const totalMissing = filterTrades(allTrades, "all").length;
  const reasonPct = completionPct(allTrades, "reason");
  const reviewPct = completionPct(allTrades, "review");
  const allDone = !loading && !error && totalMissing === 0 && allTrades.length > 0;
  const isEmpty = !loading && !error && allTrades.length === 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">복기 현황</h1>
        <span className="text-sm text-gray-500">
          총 {allTrades.length.toLocaleString()}건
        </span>
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-8 text-center space-y-4">
          <p className="text-sm font-medium text-red-700">{error}</p>
          <button
            onClick={fetchAll}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors"
          >
            다시 시도
          </button>
        </div>
      )}

      {/* Zero-trades empty state */}
      {isEmpty && (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-8 text-center space-y-4">
          <p className="text-sm font-medium text-gray-600">매매 내역이 없습니다.</p>
          <Link
            href="/trades/upload"
            className="inline-block px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
          >
            업로드 페이지로 이동
          </Link>
        </div>
      )}

      {/* Stats */}
      {!loading && !error && !isEmpty && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <StatCard
            label="총 거래"
            value={allTrades.length}
          />
          <StatCard
            label="매매이유 작성률"
            value={`${reasonPct}%`}
            sub={`${allTrades.filter((t) => !isMissing(t.reason)).length}/${allTrades.length}건`}
          />
          <StatCard
            label="복기 작성률"
            value={`${reviewPct}%`}
            sub={`${allTrades.filter((t) => !isMissing(t.review)).length}/${allTrades.length}건`}
          />
        </div>
      )}

      {/* All-done empty state */}
      {allDone && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-8 text-center">
          <p className="text-lg font-medium text-green-800">
            모든 거래에 매매이유와 복기가 작성되었습니다! 🎉
          </p>
        </div>
      )}

      {/* Filter tabs */}
      {!loading && !error && !isEmpty && !allDone && (
        <>
          <div className="flex gap-2 border-b border-gray-200">
            {TAB_LABELS.map(({ key, label }) => {
              const count = filterTrades(allTrades, key).length;
              const isActive = activeTab === key;
              return (
                <button
                  key={key}
                  onClick={() => setActiveTab(key)}
                  className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                    isActive
                      ? "border-blue-500 text-blue-600"
                      : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                  }`}
                >
                  {label}
                  {count > 0 && (
                    <span
                      className={`ml-1.5 inline-flex items-center justify-center px-1.5 py-0.5 rounded-full text-xs font-medium ${
                        isActive
                          ? "bg-blue-100 text-blue-700"
                          : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Content */}
          {filtered.length === 0 ? (
            <div className="bg-green-50 border border-green-200 rounded-xl p-8 text-center">
              <p className="text-sm font-medium text-green-700">
                이 항목은 모두 작성 완료입니다.
              </p>
            </div>
          ) : (
            <TradeTable trades={filtered} onUpdate={handleUpdate} />
          )}
        </>
      )}

      {/* Loading state inside tabs area */}
      {loading && (
        <div className="text-center py-16 text-gray-400">불러오는 중...</div>
      )}
    </div>
  );
}
