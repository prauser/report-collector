"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, TradeResponse } from "@/lib/api";
import TradeTable from "@/components/trades/TradeTable";
import TradeFilters from "@/components/trades/TradeFilters";
import Pagination from "@/components/Pagination";

const LIMIT = 30;

function TradesContent() {
  const params = useSearchParams();
  const page = parseInt(params.get("page") ?? "1");
  const symbol = params.get("symbol") ?? undefined;
  const broker = params.get("broker") ?? undefined;
  const side = params.get("side") ?? undefined;
  const dateFrom = params.get("date_from") ?? undefined;
  const dateTo = params.get("date_to") ?? undefined;

  const [trades, setTrades] = useState<TradeResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const fetchTrades = useCallback(async () => {
    setLoading(true);
    try {
      const offset = (page - 1) * LIMIT;
      const res = await api.trades.list({
        symbol,
        broker,
        side,
        date_from: dateFrom,
        date_to: dateTo,
        offset,
        limit: LIMIT,
      });
      setTrades(res.items);
      setTotal(res.total);
    } catch {
      setTrades([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, symbol, broker, side, dateFrom, dateTo]);

  useEffect(() => {
    fetchTrades();
  }, [fetchTrades]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">체결 내역</h1>
        <div className="flex items-center gap-3">
          <Link
            href="/trades/review"
            className="text-sm text-blue-600 hover:underline"
          >
            복기 현황 →
          </Link>
          <span className="text-sm text-gray-500">
            총 {total.toLocaleString()}건
          </span>
        </div>
      </div>

      <TradeFilters />

      {loading ? (
        <div className="text-center py-16 text-gray-400">불러오는 중...</div>
      ) : (
        <TradeTable trades={trades} />
      )}

      {!loading && total > LIMIT && (
        <Pagination total={total} page={page} limit={LIMIT} basePath="/trades" />
      )}
    </div>
  );
}

export default function TradesPage() {
  return (
    <Suspense fallback={<div className="text-center py-16 text-gray-400">불러오는 중...</div>}>
      <TradesContent />
    </Suspense>
  );
}
