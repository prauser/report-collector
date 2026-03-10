import { api } from "@/lib/api";

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="text-sm text-gray-500 mb-1">{label}</div>
      <div className="text-2xl font-bold text-gray-900">{value.toLocaleString()}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

export default async function StatsPage() {
  const [overview, llm] = await Promise.all([
    api.stats.overview(),
    api.stats.llm(30),
  ]);

  const brokerReport = llm.by_message_type.find((m) => m.message_type === "broker_report");
  const news = llm.by_message_type.find((m) => m.message_type === "news");
  const general = llm.by_message_type.find((m) => m.message_type === "general");
  const totalParseCalls = llm.by_message_type.reduce((s, m) => s + m.count, 0);

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold text-gray-900">통계 대시보드</h1>

      {/* 전체 현황 */}
      <section>
        <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">전체 현황</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="총 리포트" value={overview.total_reports} />
          <StatCard label="오늘 수집" value={overview.reports_today} />
          <StatCard label="PDF 보유" value={overview.reports_with_pdf} sub={`${Math.round(overview.reports_with_pdf / Math.max(overview.total_reports, 1) * 100)}%`} />
          <StatCard label="AI 분석 완료" value={overview.reports_with_ai} sub={`${Math.round(overview.reports_with_ai / Math.max(overview.total_reports, 1) * 100)}%`} />
        </div>
      </section>

      {/* 증권사 / 종목 Top 10 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-4">증권사 Top 10</h2>
          <div className="space-y-2">
            {overview.top_brokers.map((b, i) => (
              <div key={b.broker} className="flex items-center gap-3">
                <span className="text-xs text-gray-400 w-4">{i + 1}</span>
                <div className="flex-1">
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-sm text-gray-700">{b.broker}</span>
                    <span className="text-sm font-medium text-gray-900">{b.count.toLocaleString()}</span>
                  </div>
                  <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 rounded-full"
                      style={{ width: `${(b.count / overview.top_brokers[0].count) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-4">종목 Top 10 (최근 30일)</h2>
          <div className="space-y-2">
            {overview.top_stocks.map((s, i) => (
              <div key={s.stock} className="flex items-center gap-3">
                <span className="text-xs text-gray-400 w-4">{i + 1}</span>
                <div className="flex-1">
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-sm text-gray-700">{s.stock}</span>
                    <span className="text-sm font-medium text-gray-900">{s.count.toLocaleString()}</span>
                  </div>
                  <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-green-500 rounded-full"
                      style={{ width: `${(s.count / (overview.top_stocks[0]?.count || 1)) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* LLM 비용 */}
      <section>
        <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">
          LLM 비용 (최근 30일)
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <StatCard
            label="총 비용"
            value={`$${parseFloat(llm.total_cost_usd).toFixed(4)}`}
          />
          <StatCard
            label="총 호출 수"
            value={llm.by_purpose.reduce((s, r) => s + r.call_count, 0)}
          />
          <StatCard
            label="리포트 파싱 비율"
            value={`${totalParseCalls ? Math.round(((brokerReport?.count ?? 0) / totalParseCalls) * 100) : 0}%`}
            sub="broker_report / 전체 파싱"
          />
        </div>

        {/* 메시지 타입별 필터링 비율 */}
        <div className="bg-white rounded-xl border border-gray-200 p-5 mb-4">
          <h3 className="text-sm font-medium text-gray-700 mb-4">파싱 필터링 비율 (메시지 타입별)</h3>
          {totalParseCalls === 0 ? (
            <p className="text-sm text-gray-400">데이터 없음</p>
          ) : (
            <div className="space-y-3">
              {[
                { key: "broker_report", label: "증권 리포트", color: "bg-blue-500" },
                { key: "news", label: "뉴스", color: "bg-yellow-400" },
                { key: "general", label: "일반 메시지", color: "bg-gray-300" },
              ].map(({ key, label, color }) => {
                const row = llm.by_message_type.find((m) => m.message_type === key);
                const cnt = row?.count ?? 0;
                const pct = totalParseCalls ? Math.round((cnt / totalParseCalls) * 100) : 0;
                const cost = row?.cost_usd ?? 0;
                return (
                  <div key={key}>
                    <div className="flex items-center justify-between text-sm mb-1">
                      <span className="text-gray-700">{label}</span>
                      <span className="text-gray-500">
                        {cnt.toLocaleString()}건 ({pct}%) · ${cost.toFixed(4)}
                      </span>
                    </div>
                    <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                      <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* purpose별 상세 */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-gray-600">모델</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">목적</th>
                <th className="text-right px-4 py-3 font-medium text-gray-600">호출</th>
                <th className="text-right px-4 py-3 font-medium text-gray-600">입력 토큰</th>
                <th className="text-right px-4 py-3 font-medium text-gray-600">출력 토큰</th>
                <th className="text-right px-4 py-3 font-medium text-gray-600">비용 (USD)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {llm.by_purpose.map((r, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-gray-700 font-mono text-xs">{r.model}</td>
                  <td className="px-4 py-3 text-gray-700">{r.purpose}</td>
                  <td className="px-4 py-3 text-right text-gray-600">{r.call_count.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right text-gray-600">{r.total_input_tokens.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right text-gray-600">{r.total_output_tokens.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right font-medium text-gray-900">
                    ${parseFloat(r.total_cost_usd).toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
