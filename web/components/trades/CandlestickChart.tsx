"use client";

import { useEffect, useRef, useCallback } from "react";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  HistogramSeries,
  type IChartApi,
  type CandlestickData,
  type HistogramData,
  type Time,
  ColorType,
  CrosshairMode,
} from "lightweight-charts";
import type { OhlcvResponse, TradeResponse } from "@/lib/api";

interface Props {
  ohlcv: OhlcvResponse[];
  trades: TradeResponse[];
}

function toTime(dateStr: string): Time {
  return dateStr as Time;
}

export default function CandlestickChart({ ohlcv, trades }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const initChart = useCallback(() => {
    const container = containerRef.current;
    if (!container || ohlcv.length === 0) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#333",
        fontFamily: "system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: "#f0f0f0" },
        horzLines: { color: "#f0f0f0" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: "#e0e0e0",
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      timeScale: {
        borderColor: "#e0e0e0",
        timeVisible: false,
      },
      width: container.clientWidth,
      height: container.clientHeight || 500,
    });

    chartRef.current = chart;

    // Candlestick series (v5 API)
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#ef4444",
      downColor: "#3b82f6",
      borderUpColor: "#ef4444",
      borderDownColor: "#3b82f6",
      wickUpColor: "#ef4444",
      wickDownColor: "#3b82f6",
    });

    const candleData: CandlestickData[] = ohlcv.map((d) => ({
      time: toTime(d.date),
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));
    candleSeries.setData(candleData);

    // Volume series (bottom overlay)
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const volumeData: HistogramData[] = ohlcv.map((d) => ({
      time: toTime(d.date),
      value: d.volume,
      color: d.close >= d.open ? "rgba(239,68,68,0.3)" : "rgba(59,130,246,0.3)",
    }));
    volumeSeries.setData(volumeData);

    // Trade markers (v5: createSeriesMarkers)
    if (trades.length > 0) {
      const markers = trades
        .map((t) => {
          const dateStr = t.traded_at.slice(0, 10);
          const isBuy = t.side === "buy";
          return {
            time: toTime(dateStr),
            position: isBuy ? ("belowBar" as const) : ("aboveBar" as const),
            color: isBuy ? "#16a34a" : "#dc2626",
            shape: isBuy ? ("arrowUp" as const) : ("arrowDown" as const),
            text: `${isBuy ? "매수" : "매도"} ${t.quantity.toLocaleString()}주 @${t.price.toLocaleString()}`,
          };
        })
        .sort((a, b) => (a.time as string).localeCompare(b.time as string));
      createSeriesMarkers(candleSeries, markers);
    }

    chart.timeScale().fitContent();

    // Resize handler
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height });
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [ohlcv, trades]);

  useEffect(() => {
    const cleanup = initChart();
    return () => cleanup?.();
  }, [initChart]);

  return (
    <div
      ref={containerRef}
      className="w-full"
      style={{ height: "500px" }}
    />
  );
}
