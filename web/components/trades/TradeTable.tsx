"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { api, TradeResponse } from "@/lib/api";

interface Props {
  trades: TradeResponse[];
  onUpdate?: (updated: TradeResponse) => void;
}

function SideBadge({ side }: { side: "buy" | "sell" }) {
  if (side === "buy") {
    return (
      <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
        매수
      </span>
    );
  }
  return (
    <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
      매도
    </span>
  );
}

function formatAmount(val: number | null): string {
  if (val === null || val === undefined) return "-";
  return val.toLocaleString("ko-KR") + "원";
}

function formatDate(dateStr: string): string {
  return dateStr.slice(0, 10);
}

function InlineEdit({
  value,
  placeholder,
  onSave,
}: {
  value: string | null;
  placeholder: string;
  onSave: (v: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value ?? "");
  const savingRef = useRef(false);

  async function save() {
    if (savingRef.current) return;
    if (text === (value ?? "")) { setEditing(false); return; }
    savingRef.current = true;
    try {
      await onSave(text);
    } finally {
      savingRef.current = false;
    }
    setEditing(false);
  }

  if (editing) {
    return (
      <input
        autoFocus
        className="w-full px-1.5 py-0.5 border border-blue-400 rounded text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
        value={text}
        disabled={savingRef.current}
        onChange={(e) => setText(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
          if (e.key === "Escape") {
            setText(value ?? "");
            setEditing(false);
          }
        }}
      />
    );
  }

  return (
    <span
      role="button"
      tabIndex={0}
      className="block cursor-pointer text-xs text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded px-1.5 py-0.5 min-w-[60px] min-h-[20px]"
      title="클릭하여 편집"
      aria-label={value ? `편집: ${value}` : placeholder}
      onClick={() => { setText(value ?? ""); setEditing(true); }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { setText(value ?? ""); setEditing(true); } }}
    >
      {value || <span className="text-gray-300">{placeholder}</span>}
    </span>
  );
}

export default function TradeTable({ trades, onUpdate }: Props) {
  const [localTrades, setLocalTrades] = useState<TradeResponse[]>(trades);

  // Sync when parent passes new trades
  useEffect(() => {
    setLocalTrades(trades);
  }, [trades]);

  async function handleReasonSave(id: number, reason: string) {
    const updated = await api.trades.updateReason(id, reason);
    setLocalTrades((prev) => prev.map((t) => (t.id === id ? updated : t)));
    onUpdate?.(updated);
  }

  async function handleReviewSave(id: number, review: string) {
    const updated = await api.trades.updateReview(id, review);
    setLocalTrades((prev) => prev.map((t) => (t.id === id ? updated : t)));
    onUpdate?.(updated);
  }

  if (localTrades.length === 0) {
    return (
      <div className="text-center py-16 text-gray-400">
        <p>매매 내역이 없습니다. CSV를 업로드하세요.</p>
        <Link
          href="/trades/upload"
          className="mt-2 inline-block text-blue-600 hover:text-blue-800 text-sm hover:underline"
        >
          업로드 페이지로 이동
        </Link>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-28">날짜</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-32">종목</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-20">구분</th>
            <th className="text-right px-4 py-3 font-medium text-gray-600 w-20">수량</th>
            <th className="text-right px-4 py-3 font-medium text-gray-600 w-28">단가</th>
            <th className="text-right px-4 py-3 font-medium text-gray-600 w-32">금액</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-24">브로커</th>
            <th className="text-right px-4 py-3 font-medium text-gray-600 w-24">수수료</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600">매매이유</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600">복기</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {localTrades.map((t) => (
            <tr key={t.id} className="hover:bg-gray-50 transition-colors">
              <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                {formatDate(t.traded_at)}
              </td>
              <td className="px-4 py-3">
                <div className="font-medium text-gray-900">{t.name ?? t.symbol}</div>
                {t.name && (
                  <div className="text-xs text-gray-400">{t.symbol}</div>
                )}
              </td>
              <td className="px-4 py-3">
                <SideBadge side={t.side} />
              </td>
              <td className="px-4 py-3 text-right text-gray-700">
                {t.quantity.toLocaleString("ko-KR")}
              </td>
              <td className="px-4 py-3 text-right text-gray-700">
                {formatAmount(t.price)}
              </td>
              <td className="px-4 py-3 text-right font-medium text-gray-900">
                {formatAmount(t.amount)}
              </td>
              <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                {t.broker ?? "-"}
              </td>
              <td className="px-4 py-3 text-right text-gray-500">
                {t.fees !== null && t.fees !== undefined ? formatAmount(t.fees) : "-"}
              </td>
              <td className="px-4 py-3 min-w-[120px]">
                <InlineEdit
                  value={t.reason}
                  placeholder="이유 입력..."
                  onSave={(v) => handleReasonSave(t.id, v)}
                />
              </td>
              <td className="px-4 py-3 min-w-[120px]">
                <InlineEdit
                  value={t.review}
                  placeholder="복기 입력..."
                  onSave={(v) => handleReviewSave(t.id, v)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
