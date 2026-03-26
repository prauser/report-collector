"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, StockListItem, SectorListItem } from "@/lib/api";
import { sentimentLabel } from "@/lib/utils";
import SectorPieChart from "@/components/analysis/SectorPieChart";

const LIMIT = 30;

type Tab = "stocks" | "sectors";

// ---------------------------------------------------------------------------
// Stocks tab
// ---------------------------------------------------------------------------

function StocksTab() {
  const params = useSearchParams();
  const router = useRouter();

  const search = params.get("search") ?? "";
  const sort = (params.get("sort") ?? "report_count") as "report_count" | "latest_date";
  const page = parseInt(params.get("page") ?? "1") || 1;

  const [stocks, setStocks] = useState<StockListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [searchInput, setSearchInput] = useState(search);

  const fetchStocks = useCallback(async () => {
    setLoading(true);
    try {
      const offset = (page - 1) * LIMIT;
      const res = await api.stocks.list({
        search: search || undefined,
        sort,
        limit: LIMIT,
        offset,
      });
      setStocks(res.items);
      setTotal(res.total);
    } catch {
      setStocks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [search, sort, page]);

  useEffect(() => {
    fetchStocks();
  }, [fetchStocks]);

  useEffect(() => {
    setSearchInput(search);
  }, [search]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const sp = new URLSearchParams(params.toString());
    if (searchInput) {
      sp.set("search", searchInput);
    } else {
      sp.delete("search");
    }
    sp.delete("page");
    router.push(`/analysis?${sp.toString()}`);
  }

  function handleSort(newSort: "report_count" | "latest_date") {
    const sp = new URLSearchParams(params.toString());
    sp.set("sort", newSort);
    sp.delete("page");
    router.push(`/analysis?${sp.toString()}`);
  }

  function handlePage(newPage: number) {
    const sp = new URLSearchParams(params.toString());
    sp.set("page", String(newPage));
    router.push(`/analysis?${sp.toString()}`);
  }

  const totalPages = Math.ceil(total / LIMIT);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-500">총 {total.toLocaleString()}개 종목</span>
      </div>

      {/* Search + Sort */}
      <div className="flex flex-col sm:flex-row gap-3">
        <form onSubmit={handleSearch} className="flex gap-2 flex-1">
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="종목코드 또는 종목명 검색"
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors"
          >
            검색
          </button>
        </form>
        <div className="flex gap-2 items-center text-sm">
          <span className="text-gray-500">정렬:</span>
          <button
            onClick={() => handleSort("report_count")}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              sort === "report_count"
                ? "bg-gray-900 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            리포트 수
          </button>
          <button
            onClick={() => handleSort("latest_date")}
            className={`px-3 py-1.5 rounded-md transition-colors ${
              sort === "latest_date"
                ? "bg-gray-900 text-white"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            최신순
          </button>
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="text-center py-16 text-gray-400">불러오는 중...</div>
      ) : stocks.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          {search ? `"${search}" 검색 결과가 없습니다.` : "종목 데이터가 없습니다."}
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">종목코드</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">종목명</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wide">리포트</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">최신 리포트</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">평균 감성</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {stocks.map((stock) => {
                  const { label, color } = sentimentLabel(
                    stock.avg_sentiment != null ? String(stock.avg_sentiment) : null
                  );
                  return (
                    <tr
                      key={stock.stock_code}
                      className="hover:bg-gray-50 cursor-pointer transition-colors"
                      onClick={() => router.push(`/analysis/stocks/${stock.stock_code}`)}
                    >
                      <td className="px-4 py-3 font-mono text-gray-900">{stock.stock_code}</td>
                      <td className="px-4 py-3">
                        <Link
                          href={`/analysis/stocks/${stock.stock_code}`}
                          className="text-blue-600 hover:underline font-medium"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {stock.stock_name ?? stock.stock_code}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-right text-gray-900 font-medium">
                        {stock.report_count.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-gray-500">
                        {stock.latest_report_date ?? "-"}
                      </td>
                      <td className={`px-4 py-3 font-medium ${color}`}>{label}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pagination */}
      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => handlePage(page - 1)}
            disabled={page <= 1}
            className="px-3 py-1.5 text-sm rounded-md border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
          >
            이전
          </button>
          <span className="text-sm text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => handlePage(page + 1)}
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

// ---------------------------------------------------------------------------
// Sectors tab
// ---------------------------------------------------------------------------

type SectorSortKey = "report_count" | "avg_sentiment";

function SectorsTab() {
  const router = useRouter();
  const [sectors, setSectors] = useState<SectorListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SectorSortKey>("report_count");
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => {
    setLoading(true);
    api.analysis
      .sectors()
      .then((res) => setSectors(res.items))
      .catch(() => setSectors([]))
      .finally(() => setLoading(false));
  }, []);

  function toggleSort(key: SectorSortKey) {
    if (sortKey === key) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  }

  const sorted = [...sectors].sort((a, b) => {
    let diff = 0;
    if (sortKey === "report_count") {
      diff = a.report_count - b.report_count;
    } else {
      const av = a.avg_sentiment ?? -Infinity;
      const bv = b.avg_sentiment ?? -Infinity;
      diff = av - bv;
    }
    return sortAsc ? diff : -diff;
  });

  if (loading) {
    return <div className="text-center py-16 text-gray-400">불러오는 중...</div>;
  }

  if (sectors.length === 0) {
    return <div className="text-center py-16 text-gray-400">섹터 데이터가 없습니다.</div>;
  }

  return (
    <div className="space-y-6">
      {/* Donut chart */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <h2 className="text-sm font-medium text-gray-700 mb-3">섹터별 리포트 분포</h2>
        <SectorPieChart sectors={sorted} />
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  섹터명
                </th>
                <th
                  className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wide cursor-pointer hover:text-gray-700 select-none"
                  onClick={() => toggleSort("report_count")}
                >
                  리포트 수 {sortKey === "report_count" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th
                  className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide cursor-pointer hover:text-gray-700 select-none"
                  onClick={() => toggleSort("avg_sentiment")}
                >
                  평균 감성 {sortKey === "avg_sentiment" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  주요 종목
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sorted.map((sector) => {
                const { label, color } = sentimentLabel(
                  sector.avg_sentiment != null ? String(sector.avg_sentiment) : null
                );
                return (
                  <tr
                    key={sector.sector_name}
                    className="hover:bg-gray-50 cursor-pointer transition-colors"
                    onClick={() =>
                      router.push(
                        `/analysis/sector/${encodeURIComponent(sector.sector_name)}`
                      )
                    }
                  >
                    <td className="px-4 py-3 font-medium text-blue-600 hover:underline">
                      {sector.sector_name}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-900 font-medium">
                      {sector.report_count.toLocaleString()}
                    </td>
                    <td className={`px-4 py-3 font-medium ${color}`}>{label}</td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {sector.top_stocks
                        .map((s) => s.stock_name ?? s.stock_code)
                        .join(", ")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page content (tab switcher)
// ---------------------------------------------------------------------------

function AnalysisContent() {
  const params = useSearchParams();
  const router = useRouter();

  const tab = (params.get("tab") ?? "stocks") as Tab;

  function setTab(t: Tab) {
    const sp = new URLSearchParams(params.toString());
    sp.set("tab", t);
    sp.delete("page");
    router.push(`/analysis?${sp.toString()}`);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">종목분석</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        <button
          onClick={() => setTab("stocks")}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
            tab === "stocks"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          종목
        </button>
        <button
          onClick={() => setTab("sectors")}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
            tab === "sectors"
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          섹터
        </button>
      </div>

      {tab === "stocks" ? <StocksTab /> : <SectorsTab />}
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense fallback={<div className="text-center py-16 text-gray-400">불러오는 중...</div>}>
      <AnalysisContent />
    </Suspense>
  );
}
