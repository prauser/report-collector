"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, use } from "react";
import Link from "next/link";
import { api, StockHistoryItem, StockHistoryResponse } from "@/lib/api";
import { opinionColor, formatPrice } from "@/lib/utils";
import TargetPriceChart from "@/components/analysis/TargetPriceChart";
import OpinionTimeline from "@/components/analysis/OpinionTimeline";

const LIMIT = 50;
const CHART_LIMIT = 1000;

interface Props {
  params: Promise<{ code: string }>;
}

export default function StockHistoryPage({ params }: Props) {
  const { code } = use(params);
  const [data, setData] = useState<StockHistoryResponse | null>(null);
  const [chartItems, setChartItems] = useState<StockHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.stocks
      .history(code, { limit: LIMIT, offset: (page - 1) * LIMIT })
      .then((res) => {
        setData(res);
      })
      .catch((err: Error) => {
        setError(err.message ?? "오류가 발생했습니다.");
      })
      .finally(() => setLoading(false));
  }, [code, page]);

  useEffect(() => {
    api.stocks
      .history(code, { limit: CHART_LIMIT, offset: 0 })
      .then((res) => {
        setChartItems(res.items);
      })
      .catch(() => {
        // Chart data is best-effort; do not block the page on error
      });
  }, [code]);

  if (loading) {
    return <div className="text-center py-16 text-gray-400">불러오는 중...</div>;
  }

  if (error || !data) {
    return (
      <div className="text-center py-16 text-red-500">
        {error ?? "데이터를 불러올 수 없습니다."}
      </div>
    );
  }

  const totalPages = Math.ceil(data.total / LIMIT);
  const stockName = data.stock_name ?? data.stock_code;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link
          href="/analysis"
          className="text-sm text-blue-600 hover:underline"
        >
          ← 종목분석
        </Link>
        <span className="text-gray-300">|</span>
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{stockName}</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {data.stock_code} &middot; 총 {data.total.toLocaleString()}건 리포트
          </p>
        </div>
      </div>

      {/* Target Price Chart */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="text-base font-semibold text-gray-800 mb-4">목표가 추이</h2>
        <TargetPriceChart items={chartItems} />
      </div>

      {/* Opinion Timeline */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="text-base font-semibold text-gray-800 mb-4">투자의견 변화</h2>
        <OpinionTimeline items={chartItems} />
      </div>

      {/* Report List */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800">관련 리포트</h2>
        </div>
        <div className="divide-y divide-gray-100">
          {data.items.map((item: StockHistoryItem) => (
            <Link
              key={item.report_id}
              href={`/reports/${item.report_id}`}
              className="block px-5 py-4 hover:bg-gray-50 transition-colors"
            >
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="text-xs text-gray-500">{item.report_date}</span>
                    <span className="text-xs text-gray-400">{item.broker}</span>
                    {item.opinion && (
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full font-medium ${opinionColor(item.opinion)}`}
                      >
                        {item.opinion}
                      </span>
                    )}
                    {item.target_price && (
                      <span className="text-xs text-gray-600 font-medium">
                        목표가 {formatPrice(item.target_price)}
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-gray-900 truncate">{item.title}</p>
                  {item.layer2_summary && (
                    <p className="text-xs text-gray-500 mt-1 line-clamp-2">
                      {item.layer2_summary}
                    </p>
                  )}
                </div>
                <span className="text-gray-300 text-lg shrink-0">→</span>
              </div>
            </Link>
          ))}
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="px-3 py-1.5 text-sm rounded-md border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
          >
            이전
          </button>
          <span className="text-sm text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="px-3 py-1.5 text-sm rounded-md border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
          >
            다음
          </button>
        </div>
      )}
    </div>
  );
}
