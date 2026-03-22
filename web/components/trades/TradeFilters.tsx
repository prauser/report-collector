"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState, useTransition } from "react";
import { Search, X } from "lucide-react";

export default function TradeFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const [, startTransition] = useTransition();

  const [symbol, setSymbol] = useState(params.get("symbol") ?? "");
  const [broker, setBroker] = useState(params.get("broker") ?? "");
  const [side, setSide] = useState(params.get("side") ?? "");
  const [dateFrom, setDateFrom] = useState(params.get("date_from") ?? "");
  const [dateTo, setDateTo] = useState(params.get("date_to") ?? "");

  function submit() {
    const p = new URLSearchParams();
    if (symbol) p.set("symbol", symbol);
    if (broker) p.set("broker", broker);
    if (side) p.set("side", side);
    if (dateFrom) p.set("date_from", dateFrom);
    if (dateTo) p.set("date_to", dateTo);
    p.set("page", "1");
    startTransition(() => router.push(`/trades?${p.toString()}`));
  }

  function reset() {
    setSymbol("");
    setBroker("");
    setSide("");
    setDateFrom("");
    setDateTo("");
    startTransition(() => router.push("/trades"));
  }

  const hasFilter = symbol || broker || side || dateFrom || dateTo;

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="종목명/코드 검색..."
            aria-label="종목명 또는 코드로 검색"
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>
        <input
          type="text"
          placeholder="브로커"
          aria-label="브로커 필터"
          className="w-36 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={broker}
          onChange={(e) => setBroker(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <select
          aria-label="매수/매도 구분 필터"
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={side}
          onChange={(e) => setSide(e.target.value)}
        >
          <option value="">매수/매도 전체</option>
          <option value="buy">매수</option>
          <option value="sell">매도</option>
        </select>

        <input
          type="date"
          aria-label="시작 날짜"
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
        />
        <span className="text-gray-400 text-sm">~</span>
        <input
          type="date"
          aria-label="종료 날짜"
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
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
