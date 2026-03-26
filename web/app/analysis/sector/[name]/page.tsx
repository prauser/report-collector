"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, use } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, SectorStockItem } from "@/lib/api";
import { sentimentLabel, formatPrice, opinionColor } from "@/lib/utils";
import SentimentBarChart from "@/components/analysis/SentimentBarChart";

interface Props {
  params: Promise<{ name: string }>;
}

export default function SectorPage({ params }: Props) {
  const { name } = use(params);
  const sectorName = decodeURIComponent(name);
  const router = useRouter();

  const [stocks, setStocks] = useState<SectorStockItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api.analysis
      .sector(sectorName)
      .then((res) => setStocks(res.items))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [sectorName]);

  const totalReports = stocks.reduce((acc, s) => acc + s.report_count, 0);
  const stocksWithSentiment = stocks.filter(
    (s): s is typeof s & { avg_sentiment: number } => s.avg_sentiment != null
  );
  const weightedSentimentSum = stocksWithSentiment.reduce(
    (acc, s) => acc + s.avg_sentiment * s.report_count,
    0
  );
  const weightedReportCount = stocksWithSentiment.reduce(
    (acc, s) => acc + s.report_count,
    0
  );
  const avgSentiment =
    weightedReportCount > 0 ? weightedSentimentSum / weightedReportCount : null;

  const { label: sentLabel, color: sentColor } = sentimentLabel(
    avgSentiment != null ? String(avgSentiment) : null
  );

  if (loading) {
    return (
      <div className="text-center py-16 text-gray-400">불러오는 중...</div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Link
          href="/analysis?tab=sectors"
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          ← 섹터분석
        </Link>
        <div className="text-center py-16 text-red-500">{error}</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        href="/analysis?tab=sectors"
        className="inline-block text-sm text-gray-500 hover:text-gray-700"
      >
        ← 섹터분석
      </Link>

      {/* Sector info header */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h1 className="text-xl font-semibold text-gray-900 mb-3">{sectorName}</h1>
        <div className="flex flex-wrap gap-6 text-sm">
          <div>
            <span className="text-gray-500">총 종목수</span>
            <span className="ml-2 font-medium text-gray-900">
              {stocks.length.toLocaleString()}개
            </span>
          </div>
          <div>
            <span className="text-gray-500">총 리포트</span>
            <span className="ml-2 font-medium text-gray-900">
              {totalReports.toLocaleString()}건
            </span>
          </div>
          <div>
            <span className="text-gray-500">평균 감성</span>
            <span className={`ml-2 font-medium ${sentColor}`}>{sentLabel}</span>
          </div>
        </div>
      </div>

      {/* Sentiment bar chart */}
      {stocks.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h2 className="text-sm font-medium text-gray-700 mb-3">종목별 감성 비교</h2>
          <SentimentBarChart stocks={stocks} />
        </div>
      )}

      {/* Stock comparison table */}
      {stocks.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          이 섹터에 해당하는 종목 데이터가 없습니다.
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                    종목명
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                    종목코드
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">
                    리포트
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                    평균 감성
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                    최신 의견
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">
                    목표가
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {stocks.map((stock) => {
                  const { label, color } = sentimentLabel(
                    stock.avg_sentiment != null
                      ? String(stock.avg_sentiment)
                      : null
                  );
                  return (
                    <tr
                      key={stock.stock_code}
                      className="hover:bg-gray-50 cursor-pointer transition-colors"
                      onClick={() =>
                        router.push(`/analysis/stocks/${stock.stock_code}`)
                      }
                    >
                      <td className="px-4 py-3">
                        <Link
                          href={`/analysis/stocks/${stock.stock_code}`}
                          className="text-blue-600 hover:underline font-medium"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {stock.stock_name ?? stock.stock_code}
                        </Link>
                      </td>
                      <td className="px-4 py-3 font-mono text-gray-700">
                        {stock.stock_code}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-900 font-medium">
                        {stock.report_count.toLocaleString()}
                      </td>
                      <td className={`px-4 py-3 font-medium ${color}`}>{label}</td>
                      <td className="px-4 py-3">
                        {stock.latest_opinion ? (
                          <span
                            className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${opinionColor(
                              stock.latest_opinion
                            )}`}
                          >
                            {stock.latest_opinion}
                          </span>
                        ) : (
                          <span className="text-gray-400">-</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-900">
                        {formatPrice(stock.latest_target_price)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
