"use client";

import Link from "next/link";
import type { ReportSummary } from "@/lib/api";
import { opinionColor, formatPrice, sentimentLabel } from "@/lib/utils";
import { FileText, Brain, ChevronUp, ChevronDown } from "lucide-react";

interface Props {
  reports: ReportSummary[];
}

export default function ReportTable({ reports }: Props) {
  if (reports.length === 0) {
    return (
      <div className="text-center py-16 text-gray-400">
        검색 결과가 없습니다.
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-24">날짜</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-28">증권사</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-28">종목</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600">제목</th>
            <th className="text-left px-4 py-3 font-medium text-gray-600 w-20">의견</th>
            <th className="text-right px-4 py-3 font-medium text-gray-600 w-24">목표가</th>
            <th className="text-center px-4 py-3 font-medium text-gray-600 w-20">감성</th>
            <th className="text-center px-4 py-3 font-medium text-gray-600 w-16">자료</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {reports.map((r) => {
            const sentiment = sentimentLabel(r.ai_sentiment);
            return (
              <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                  {r.report_date}
                </td>
                <td className="px-4 py-3 text-gray-700 whitespace-nowrap">
                  {r.broker}
                </td>
                <td className="px-4 py-3">
                  {r.stock_name && (
                    <div>
                      <span className="font-medium text-gray-900">{r.stock_name}</span>
                      {r.stock_code && (
                        <span className="text-xs text-gray-400 ml-1">{r.stock_code}</span>
                      )}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Link
                    href={`/reports/${r.id}`}
                    className="text-blue-600 hover:text-blue-800 hover:underline line-clamp-2"
                  >
                    {r.title}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  {r.opinion && (
                    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${opinionColor(r.opinion)}`}>
                      {r.opinion}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="text-gray-900">{formatPrice(r.target_price)}</div>
                  {r.prev_target_price && r.target_price && r.prev_target_price !== r.target_price && (
                    <div className="flex items-center justify-end gap-0.5 text-xs">
                      {r.target_price > r.prev_target_price ? (
                        <ChevronUp className="w-3 h-3 text-green-500" />
                      ) : (
                        <ChevronDown className="w-3 h-3 text-red-500" />
                      )}
                      <span className="text-gray-400">{formatPrice(r.prev_target_price)}</span>
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-center">
                  {r.has_ai && r.ai_sentiment && (
                    <span className={`text-xs font-medium ${sentiment.color}`}>
                      {sentiment.label}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-center">
                  <div className="flex items-center justify-center gap-1.5">
                    {r.has_pdf && (
                      <FileText className="w-4 h-4 text-blue-400" />
                    )}
                    {r.has_ai && (
                      <Brain className="w-4 h-4 text-purple-400" />
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
