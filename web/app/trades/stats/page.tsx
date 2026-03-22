"use client";

import { useState, useEffect, useCallback } from "react";
import { api, TradeStatsResponse } from "@/lib/api";
import StatCard from "@/components/shared/StatCard";

export default function TradeStatsPage() {
  const [stats, setStats] = useState<TradeStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.trades.stats();
      setStats(data);
    } catch {
      setError("데이터를 불러오는데 실패했습니다. 다시 시도해주세요.");
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  const totalAmount = stats
    ? parseFloat(stats.total_amount).toLocaleString("ko-KR", {
        maximumFractionDigits: 0,
      })
    : "-";

  return (
    <div className="space-y-8">
      {/* Header */}
      <h1 className="text-xl font-semibold text-gray-900">매매 통계</h1>

      {/* Error state */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-8 text-center space-y-4">
          <p className="text-sm font-medium text-red-700">{error}</p>
          <button
            onClick={fetchStats}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors"
          >
            다시 시도
          </button>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="text-center py-16 text-gray-400">불러오는 중...</div>
      )}

      {/* Stats content */}
      {!loading && !error && stats && (
        <>
          {/* Summary stat cards */}
          <section>
            <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
              거래 현황
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatCard label="총 거래수" value={stats.total_count} />
              <StatCard label="매수 건수" value={stats.buy_count} />
              <StatCard label="매도 건수" value={stats.sell_count} />
              <StatCard label="총 거래금액" value={`₩${totalAmount}`} />
            </div>
          </section>

          {/* Symbol frequency table */}
          <section>
            <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
              종목별 거래 빈도
            </h2>
            {stats.symbol_frequency.length === 0 ? (
              <div className="bg-gray-50 border border-gray-200 rounded-xl p-8 text-center">
                <p className="text-sm text-gray-500">거래 내역이 없습니다.</p>
              </div>
            ) : (
              <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 w-8">#</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600">종목</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600">거래 횟수</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">
                        비율
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {stats.symbol_frequency.map((row, i) => {
                      const pct =
                        stats.total_count > 0
                          ? Math.round((row.count / stats.total_count) * 100)
                          : 0;
                      return (
                        <tr key={row.symbol} className="hover:bg-gray-50">
                          <td className="px-4 py-3 text-xs text-gray-400">{i + 1}</td>
                          <td className="px-4 py-3 font-medium text-gray-800">{row.symbol}</td>
                          <td className="px-4 py-3 text-right text-gray-700">
                            {row.count.toLocaleString()}
                          </td>
                          <td className="px-4 py-3 text-right text-gray-500 hidden sm:table-cell">
                            {pct}%
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Phase 2 placeholder */}
          <section>
            <div className="bg-blue-50 border border-blue-200 rounded-xl p-5">
              <p className="text-sm text-blue-700">
                승률, 평균수익률 등 성과 분석은 매수-매도 매칭 후 추가 예정입니다.
              </p>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
