"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { StockHistoryItem } from "@/lib/api";

interface Props {
  items: StockHistoryItem[];
}

// Stable set of colors for different brokers
const BROKER_COLORS = [
  "#2563eb", // blue
  "#16a34a", // green
  "#dc2626", // red
  "#d97706", // amber
  "#7c3aed", // violet
  "#0891b2", // cyan
  "#be185d", // pink
  "#65a30d", // lime
];

interface ChartDataPoint {
  date: string;
  [broker: string]: string | number | null;
}

interface TooltipPayloadEntry {
  name: string;
  value: number | null;
  color: string;
  payload: ChartDataPoint & { opinions?: Record<string, string | null> };
}

interface CustomTooltipProps {
  active?: boolean;
  label?: string;
  payload?: TooltipPayloadEntry[];
}

function CustomTooltip({ active, label, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-sm max-w-xs">
      <p className="font-medium text-gray-700 mb-2">{label}</p>
      {payload.map((entry) => {
        const opinion = entry.payload.opinions?.[entry.name];
        return (
          <div key={entry.name} className="flex items-center gap-2 py-0.5">
            <span
              className="inline-block w-2 h-2 rounded-full shrink-0"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-gray-600 truncate">{entry.name}</span>
            <span className="font-medium text-gray-900 ml-auto">
              {entry.value != null ? entry.value.toLocaleString("ko-KR") + "원" : "-"}
            </span>
            {opinion && (
              <span className="text-xs text-gray-500">({opinion})</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function TargetPriceChart({ items }: Props) {
  // Filter items with target_price
  const filtered = items.filter((it) => it.target_price != null);

  if (filtered.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
        목표가 데이터가 없습니다
      </div>
    );
  }

  // Get unique brokers
  const brokers = Array.from(new Set(filtered.map((it) => it.broker)));

  // Build chart data: one point per date, value per broker
  // Multiple entries on the same date: use the latest one per broker
  const dateMap = new Map<string, ChartDataPoint>();
  for (const item of filtered) {
    const date = item.report_date;
    if (!dateMap.has(date)) {
      dateMap.set(date, { date, opinions: {} } as ChartDataPoint & { opinions: Record<string, string | null> });
    }
    const point = dateMap.get(date)!;
    // Keep the first (latest) entry for a broker on the same date — do not overwrite
    if (!(item.broker in point)) {
      point[item.broker] = item.target_price;
      (point as ChartDataPoint & { opinions: Record<string, string | null> }).opinions![item.broker] = item.opinion;
    }
  }

  const chartData = Array.from(dateMap.values()).sort((a, b) =>
    a.date.localeCompare(b.date)
  );

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={{ stroke: "#e5e7eb" }}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => (v / 10000).toFixed(0) + "만"}
          width={45}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          wrapperStyle={{ fontSize: "12px", paddingTop: "8px" }}
          iconType="circle"
          iconSize={8}
        />
        {brokers.map((broker, i) => (
          <Line
            key={broker}
            type="monotone"
            dataKey={broker}
            stroke={BROKER_COLORS[i % BROKER_COLORS.length]}
            strokeWidth={2}
            dot={{ r: 3, strokeWidth: 0 }}
            activeDot={{ r: 5 }}
            connectNulls={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
