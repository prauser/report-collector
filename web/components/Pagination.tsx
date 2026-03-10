"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface Props {
  total: number;
  page: number;
  limit: number;
}

export default function Pagination({ total, page, limit }: Props) {
  const router = useRouter();
  const params = useSearchParams();
  const totalPages = Math.ceil(total / limit);

  if (totalPages <= 1) return null;

  function go(p: number) {
    const next = new URLSearchParams(params.toString());
    next.set("page", String(p));
    router.push(`/?${next.toString()}`);
  }

  const pages: number[] = [];
  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, page + 2);
  for (let i = start; i <= end; i++) pages.push(i);

  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-gray-500">
        총 {total.toLocaleString()}건 중 {(page - 1) * limit + 1}–
        {Math.min(page * limit, total)}건
      </span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => go(page - 1)}
          disabled={page <= 1}
          className="p-1.5 rounded-lg border border-gray-200 disabled:opacity-40 hover:bg-gray-100"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        {start > 1 && (
          <>
            <button onClick={() => go(1)} className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-100">1</button>
            {start > 2 && <span className="text-gray-400">…</span>}
          </>
        )}
        {pages.map((p) => (
          <button
            key={p}
            onClick={() => go(p)}
            className={`px-3 py-1.5 text-sm rounded-lg border ${
              p === page
                ? "bg-blue-600 text-white border-blue-600"
                : "border-gray-200 hover:bg-gray-100"
            }`}
          >
            {p}
          </button>
        ))}
        {end < totalPages && (
          <>
            {end < totalPages - 1 && <span className="text-gray-400">…</span>}
            <button onClick={() => go(totalPages)} className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 hover:bg-gray-100">{totalPages}</button>
          </>
        )}
        <button
          onClick={() => go(page + 1)}
          disabled={page >= totalPages}
          className="p-1.5 rounded-lg border border-gray-200 disabled:opacity-40 hover:bg-gray-100"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
