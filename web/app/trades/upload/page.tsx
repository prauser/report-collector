"use client";
export const dynamic = "force-dynamic";

import Link from "next/link";
import CsvUploader from "@/components/trades/CsvUploader";

export default function TradeUploadPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">CSV 업로드</h1>
          <p className="text-sm text-gray-500 mt-1">
            증권사에서 내보낸 CSV 파일로 매매 내역을 가져옵니다.
          </p>
        </div>
        <Link href="/trades" className="text-sm text-blue-600 hover:underline">
          ← 체결 목록
        </Link>
      </div>

      <CsvUploader />
    </div>
  );
}
