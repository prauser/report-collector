"use client";

import { opinionColor } from "@/lib/utils";
import type { StockHistoryItem } from "@/lib/api";

interface Props {
  items: StockHistoryItem[];
}

export default function OpinionTimeline({ items }: Props) {
  const withOpinion = items.filter((it) => it.opinion != null);

  if (withOpinion.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400 text-sm">
        투자의견 데이터가 없습니다
      </div>
    );
  }

  // Sort oldest first for timeline display
  const sorted = [...withOpinion].sort((a, b) =>
    a.report_date.localeCompare(b.report_date)
  );

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {sorted.map((item) => (
          <div
            key={item.report_id}
            className="flex flex-col items-center gap-1 text-center"
            title={`${item.broker} - ${item.title}`}
          >
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${opinionColor(item.opinion)}`}
            >
              {item.opinion}
            </span>
            <span className="text-xs text-gray-400">{item.report_date}</span>
            <span className="text-xs text-gray-500 max-w-[80px] truncate">{item.broker}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
