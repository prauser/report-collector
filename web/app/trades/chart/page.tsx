"use client";
export const dynamic = "force-dynamic";

import { useState, useEffect, useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api, OhlcvResponse, TradeResponse, SymbolOption } from "@/lib/api";
import CandlestickChart from "@/components/trades/CandlestickChart";

function ChartContent() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = params.get("symbol") ?? "";

  const [symbols, setSymbols] = useState<SymbolOption[]>([]);
  const [ohlcv, setOhlcv] = useState<OhlcvResponse[]>([]);
  const [trades, setTrades] = useState<TradeResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load symbol list
  useEffect(() => {
    api.trades.symbols().then(setSymbols).catch(() => {});
  }, []);

  // Load chart data when symbol changes
  const fetchData = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    try {
      const [ohlcvData, tradeData] = await Promise.all([
        api.ohlcv.get(symbol),
        api.trades.chartData(symbol),
      ]);
      setOhlcv(ohlcvData);
      setTrades(tradeData);
      if (ohlcvData.length === 0) {
        setError("OHLCV 데이터가 없습니다. CSV 업로드 후 수집이 완료되면 표시됩니다.");
      }
    } catch {
      setError("데이터를 불러오지 못했습니다.");
      setOhlcv([]);
      setTrades([]);
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSymbolChange = (newSymbol: string) => {
    router.push(`/trades/chart?symbol=${newSymbol}`);
  };

  // Summary stats for selected symbol
  const buyTrades = trades.filter((t) => t.side === "buy");
  const sellTrades = trades.filter((t) => t.side === "sell");
  const totalBuyAmt = buyTrades.reduce((s, t) => s + t.amount, 0);
  const totalSellAmt = sellTrades.reduce((s, t) => s + t.amount, 0);

  const selectedName =
    symbols.find((s) => s.symbol === symbol)?.name ?? trades[0]?.name ?? "";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">종목 차트</h1>
      </div>

      {/* Symbol selector */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex items-center gap-4 flex-wrap">
          <label className="text-sm font-medium text-gray-700">종목 선택</label>
          <select
            value={symbol}
            onChange={(e) => handleSymbolChange(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm min-w-[200px]"
          >
            <option value="">-- 종목을 선택하세요 --</option>
            {symbols.map((s) => (
              <option key={s.symbol} value={s.symbol}>
                {s.name ?? s.symbol} ({s.symbol}) — {s.count}건
              </option>
            ))}
          </select>

          {symbol && selectedName && (
            <span className="text-lg font-semibold text-gray-800">
              {selectedName} ({symbol})
            </span>
          )}
        </div>
      </div>

      {/* Chart */}
      {loading && (
        <div className="text-center py-24 text-gray-400">차트 데이터 불러오는 중...</div>
      )}

      {error && !loading && (
        <div className="text-center py-24 text-gray-400">{error}</div>
      )}

      {!loading && !error && ohlcv.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <CandlestickChart ohlcv={ohlcv} trades={trades} />
        </div>
      )}

      {!loading && !symbol && (
        <div className="text-center py-24 text-gray-400">
          종목을 선택하면 캔들차트와 매매 마커가 표시됩니다.
        </div>
      )}

      {/* Trade summary for selected symbol */}
      {symbol && trades.length > 0 && !loading && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">매매 요약</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-xs text-gray-500">총 거래</div>
              <div className="text-lg font-semibold">{trades.length}건</div>
            </div>
            <div>
              <div className="text-xs text-gray-500">매수</div>
              <div className="text-lg font-semibold text-green-600">
                {buyTrades.length}건 / {totalBuyAmt.toLocaleString("ko-KR")}원
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">매도</div>
              <div className="text-lg font-semibold text-red-600">
                {sellTrades.length}건 / {totalSellAmt.toLocaleString("ko-KR")}원
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500">기간</div>
              <div className="text-sm font-medium">
                {trades[0]?.traded_at.slice(0, 10)} ~ {trades[trades.length - 1]?.traded_at.slice(0, 10)}
              </div>
            </div>
          </div>

          {/* Trade list */}
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b">
                  <th className="pb-2 pr-4">일시</th>
                  <th className="pb-2 pr-4">구분</th>
                  <th className="pb-2 pr-4 text-right">가격</th>
                  <th className="pb-2 pr-4 text-right">수량</th>
                  <th className="pb-2 pr-4 text-right">금액</th>
                  <th className="pb-2">메모</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-gray-50">
                    <td className="py-2 pr-4 text-gray-600">{t.traded_at.slice(0, 10)}</td>
                    <td className="py-2 pr-4">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                          t.side === "buy"
                            ? "bg-green-100 text-green-700"
                            : "bg-red-100 text-red-700"
                        }`}
                      >
                        {t.side === "buy" ? "매수" : "매도"}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-right">{t.price.toLocaleString("ko-KR")}</td>
                    <td className="py-2 pr-4 text-right">{t.quantity.toLocaleString("ko-KR")}</td>
                    <td className="py-2 pr-4 text-right">{t.amount.toLocaleString("ko-KR")}</td>
                    <td className="py-2 text-gray-500 truncate max-w-[200px]">{t.reason ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ChartPage() {
  return (
    <Suspense fallback={<div className="text-center py-16 text-gray-400">불러오는 중...</div>}>
      <ChartContent />
    </Suspense>
  );
}
