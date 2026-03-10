import { api } from "@/lib/api";
import { formatPrice, opinionColor, sentimentLabel } from "@/lib/utils";
import Link from "next/link";
import { ArrowLeft, FileText, Brain, TrendingUp, Calendar, Building2 } from "lucide-react";
import { notFound } from "next/navigation";

export default async function ReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let report;
  try {
    report = await api.reports.get(parseInt(id));
  } catch {
    notFound();
  }

  const sentiment = sentimentLabel(report.ai_sentiment);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* 뒤로가기 */}
      <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
        <ArrowLeft className="w-4 h-4" /> 목록으로
      </Link>

      {/* 헤더 */}
      <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <h1 className="text-xl font-semibold text-gray-900 leading-snug">{report.title}</h1>
          {report.opinion && (
            <span className={`shrink-0 px-3 py-1 rounded-full text-sm font-medium ${opinionColor(report.opinion)}`}>
              {report.opinion}
            </span>
          )}
        </div>

        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-gray-600">
          <span className="flex items-center gap-1.5">
            <Building2 className="w-4 h-4 text-gray-400" />
            {report.broker}
            {report.analyst && <span className="text-gray-400">· {report.analyst}</span>}
          </span>
          <span className="flex items-center gap-1.5">
            <Calendar className="w-4 h-4 text-gray-400" />
            {report.report_date}
          </span>
          {report.stock_name && (
            <span className="flex items-center gap-1.5">
              <TrendingUp className="w-4 h-4 text-gray-400" />
              {report.stock_name}
              {report.stock_code && <span className="text-gray-400">({report.stock_code})</span>}
              {report.sector && <span className="text-gray-400">· {report.sector}</span>}
            </span>
          )}
        </div>

        {/* 투자의견 / 목표가 */}
        {(report.target_price || report.earnings_quarter) && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-2 border-t border-gray-100">
            {report.target_price && (
              <div>
                <div className="text-xs text-gray-400 mb-0.5">목표가</div>
                <div className="font-semibold text-gray-900">{formatPrice(report.target_price)}</div>
                {report.prev_target_price && report.prev_target_price !== report.target_price && (
                  <div className="text-xs text-gray-400">이전 {formatPrice(report.prev_target_price)}</div>
                )}
              </div>
            )}
            {report.earnings_quarter && (
              <div>
                <div className="text-xs text-gray-400 mb-0.5">실적 분기</div>
                <div className="font-semibold text-gray-900">{report.earnings_quarter}</div>
              </div>
            )}
            {report.est_revenue && (
              <div>
                <div className="text-xs text-gray-400 mb-0.5">예상 매출</div>
                <div className="font-semibold text-gray-900">
                  {(report.est_revenue / 1e8).toFixed(0)}억
                </div>
              </div>
            )}
            {report.est_op_profit && (
              <div>
                <div className="text-xs text-gray-400 mb-0.5">예상 영업이익</div>
                <div className="font-semibold text-gray-900">
                  {(report.est_op_profit / 1e8).toFixed(0)}억
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* AI 분석 */}
      {report.has_ai && (
        <div className="bg-white rounded-xl border border-purple-100 p-6 space-y-4">
          <div className="flex items-center gap-2 text-purple-700 font-medium">
            <Brain className="w-5 h-5" /> AI 분석
          </div>

          {report.ai_summary && (
            <p className="text-gray-700 leading-relaxed">{report.ai_summary}</p>
          )}

          <div className="flex flex-wrap gap-4">
            {report.ai_sentiment && (
              <div>
                <div className="text-xs text-gray-400 mb-1">감성 점수</div>
                <span className={`text-sm font-medium ${sentiment.color}`}>
                  {sentiment.label} ({parseFloat(report.ai_sentiment).toFixed(2)})
                </span>
              </div>
            )}
            {report.ai_keywords && report.ai_keywords.length > 0 && (
              <div>
                <div className="text-xs text-gray-400 mb-1">키워드</div>
                <div className="flex flex-wrap gap-1.5">
                  {report.ai_keywords.map((kw) => (
                    <span key={kw} className="px-2 py-0.5 bg-purple-50 text-purple-700 text-xs rounded-full">
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* PDF / 원문 */}
      <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-3">
        <div className="flex items-center gap-2 text-gray-700 font-medium">
          <FileText className="w-5 h-5" /> 원본 자료
        </div>
        <div className="flex flex-wrap gap-3 text-sm">
          {report.pdf_url && (
            <a
              href={report.pdf_url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              PDF 다운로드
            </a>
          )}
          {report.pdf_size_kb && (
            <span className="text-gray-500 self-center">{report.pdf_size_kb} KB · {report.page_count}페이지</span>
          )}
        </div>
        {report.raw_text && (
          <details className="mt-2">
            <summary className="text-sm text-gray-500 cursor-pointer hover:text-gray-700">
              원문 텍스트 보기
            </summary>
            <pre className="mt-2 text-xs text-gray-600 whitespace-pre-wrap bg-gray-50 p-4 rounded-lg max-h-60 overflow-y-auto">
              {report.raw_text}
            </pre>
          </details>
        )}
      </div>

      {/* 메타 */}
      <div className="text-xs text-gray-400 text-right">
        수집: {new Date(report.collected_at).toLocaleString("ko-KR")} · 채널: {report.source_channel}
      </div>
    </div>
  );
}
