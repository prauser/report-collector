"use client";

import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { SectorListItem } from "@/lib/api";

interface Props {
  sectors: SectorListItem[];
}

const PIE_COLORS = [
  "#2563eb",
  "#16a34a",
  "#dc2626",
  "#d97706",
  "#7c3aed",
  "#0891b2",
  "#be185d",
  "#65a30d",
  "#f59e0b",
  "#0f172a",
];

interface TooltipPayloadEntry {
  name: string;
  value: number;
  percent?: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const entry = payload[0];
  const pct = entry.percent != null ? (entry.percent * 100).toFixed(1) : null;
  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-sm">
      <p className="font-medium text-gray-800 mb-1">{entry.name}</p>
      <p className="text-gray-600">
        리포트 수: <span className="font-medium text-gray-900">{entry.value.toLocaleString()}</span>
      </p>
      {pct != null && (
        <p className="text-gray-600">
          비율: <span className="font-medium text-gray-900">{pct}%</span>
        </p>
      )}
    </div>
  );
}

export default function SectorPieChart({ sectors }: Props) {
  if (sectors.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
        섹터 데이터가 없습니다
      </div>
    );
  }

  const data = sectors.map((s) => ({
    name: s.sector_name,
    value: s.report_count,
  }));

  return (
    <ResponsiveContainer width="100%" height={360}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="45%"
          innerRadius={60}
          outerRadius={110}
          paddingAngle={2}
          dataKey="value"
        >
          {data.map((entry, index) => (
            <Cell
              key={entry.name}
              fill={PIE_COLORS[index % PIE_COLORS.length]}
            />
          ))}
        </Pie>
        <Tooltip content={<CustomTooltip />} />
        <Legend
          verticalAlign="bottom"
          wrapperStyle={{ fontSize: "12px", paddingTop: "8px" }}
          iconType="circle"
          iconSize={8}
          formatter={(value: string) =>
            value.length > 12 ? `${value.slice(0, 12)}…` : value
          }
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
