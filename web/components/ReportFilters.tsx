"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { Search, X } from "lucide-react";
import type { FilterOptions } from "@/lib/api";

interface Props {
  filters: FilterOptions;
}

export default function ReportFilters({ filters }: Props) {
  const router = useRouter();
  const params = useSearchParams();
  const [, startTransition] = useTransition();

  const [q, setQ] = useState(params.get("q") ?? "");
  const [stock, setStock] = useState(params.get("stock") ?? "");
  const [broker, setBroker] = useState(params.get("broker") ?? "");
  const [opinion, setOpinion] = useState(params.get("opinion") ?? "");
  const [fromDate, setFromDate] = useState(params.get("from_date") ?? "");
  const [toDate, setToDate] = useState(params.get("to_date") ?? "");

  function submit() {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (stock) p.set("stock", stock);
    if (broker) p.set("broker", broker);
    if (opinion) p.set("opinion", opinion);
    if (fromDate) p.set("from_date", fromDate);
    if (toDate) p.set("to_date", toDate);
    p.set("page", "1");
    startTransition(() => router.push(`/?${p.toString()}`));
  }

  function reset() {
    setQ(""); setStock(""); setBroker(""); setOpinion("");
    setFromDate(""); setToDate("");
    startTransition(() => router.push("/"));
  }

  const hasFilter = q || stock || broker || opinion || fromDate || toDate;

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
      {/* 검색어 */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="제목, 종목명 검색..."
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>
        <input
          type="text"
          placeholder="종목명/코드"
          className="w-36 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={stock}
          onChange={(e) => setStock(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
      </div>

      {/* 필터 드롭다운 */}
      <div className="flex flex-wrap gap-2 items-center">
        <select
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={broker}
          onChange={(e) => setBroker(e.target.value)}
        >
          <option value="">전체 증권사</option>
          {filters.brokers.map((b) => (
            <option key={b} value={b}>{b}</option>
          ))}
        </select>

        <select
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={opinion}
          onChange={(e) => setOpinion(e.target.value)}
        >
          <option value="">전체 의견</option>
          {filters.opinions.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>

        <input
          type="date"
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
        />
        <span className="text-gray-400 text-sm">~</span>
        <input
          type="date"
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={toDate}
          onChange={(e) => setToDate(e.target.value)}
        />

        <button
          onClick={submit}
          className="px-4 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
        >
          검색
        </button>

        {hasFilter && (
          <button
            onClick={reset}
            className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700"
          >
            <X className="w-3.5 h-3.5" /> 초기화
          </button>
        )}
      </div>
    </div>
  );
}
