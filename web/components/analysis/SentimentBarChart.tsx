"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import type { SectorStockItem } from "@/lib/api";

interface Props {
  stocks: SectorStockItem[];
}

interface CustomTooltipProps {
  active?: boolean;
  label?: string;
  payload?: { value: number | null }[];
}

function CustomTooltip({ active, label, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const val = payload[0].value;
  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-sm">
      <p className="font-medium text-gray-800 mb-1">{label}</p>
      <p className="text-gray-600">
        평균 감성:{" "}
        <span
          className={`font-medium ${
            val != null && val >= 0 ? "text-green-600" : "text-red-500"
          }`}
        >
          {val != null ? val.toFixed(3) : "-"}
        </span>
      </p>
    </div>
  );
}

export default function SentimentBarChart({ stocks }: Props) {
  const filtered = stocks.filter((s) => s.avg_sentiment != null);

  if (filtered.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
        감성 데이터가 없습니다
      </div>
    );
  }

  const data = filtered.map((s) => ({
    name: s.stock_name ?? s.stock_code,
    sentiment: s.avg_sentiment as number,
  }));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
        <XAxis
          dataKey="name"
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={{ stroke: "#e5e7eb" }}
          angle={-30}
          textAnchor="end"
          interval={0}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          domain={[-1, 1]}
          width={40}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={0} stroke="#d1d5db" />
        <Bar dataKey="sentiment" radius={0}>
          {data.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={entry.sentiment >= 0 ? "#22c55e" : "#ef4444"}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
