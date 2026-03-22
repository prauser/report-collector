import type {
  Layer2Data,
  Layer2ChainStep,
} from "@/lib/api";

// ── Chain step 한글화 맵 ────────────────────────────────────────────────────
const STEP_LABELS: Record<string, string> = {
  trigger: "촉매",
  mechanism: "메커니즘",
  demand_transmission: "수요전달",
  supply_dynamics: "공급동향",
  pricing_impact: "가격영향",
  financial_impact: "실적영향",
  valuation_impact: "밸류에이션",
  structural_risk: "구조적리스크",
  uncertainty: "불확실성",
  data_signal: "데이터시그널",
  policy_logic: "정책논리",
  market_transmission: "시장전달",
  local_impact: "국내영향",
};

// ── Direction 색상 ──────────────────────────────────────────────────────────
function directionColor(direction?: string): string {
  switch (direction) {
    case "positive":
      return "text-green-700 bg-green-50 border-green-200";
    case "negative":
      return "text-red-700 bg-red-50 border-red-200";
    case "mixed":
      return "text-amber-700 bg-amber-50 border-amber-200";
    default:
      return "text-gray-700 bg-gray-50 border-gray-200";
  }
}

function directionDot(direction?: string): string {
  switch (direction) {
    case "positive":
      return "bg-green-500";
    case "negative":
      return "bg-red-500";
    case "mixed":
      return "bg-amber-500";
    default:
      return "bg-gray-400";
  }
}

// ── Sentiment 색상 & 레이블 ─────────────────────────────────────────────────
function sentimentStyle(sentiment: number | null | undefined): { color: string; label: string } {
  if (sentiment == null) return { color: "bg-gray-100 text-gray-600", label: "중립" };
  if (sentiment >= 0.3) return { color: "bg-green-100 text-green-700", label: "긍정" };
  if (sentiment <= -0.3) return { color: "bg-red-100 text-red-700", label: "부정" };
  return { color: "bg-gray-100 text-gray-600", label: "중립" };
}

// ── Confidence 뱃지 ─────────────────────────────────────────────────────────
function ConfidenceBadge({ confidence }: { confidence?: string }) {
  if (!confidence) return null;
  const map: Record<string, string> = {
    high: "bg-blue-100 text-blue-700",
    medium: "bg-yellow-100 text-yellow-700",
    low: "bg-gray-100 text-gray-500",
  };
  const labels: Record<string, string> = { high: "높음", medium: "중간", low: "낮음" };
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded ${map[confidence] ?? "bg-gray-100 text-gray-500"}`}>
      {labels[confidence] ?? confidence}
    </span>
  );
}

// ── Impact 색상 ─────────────────────────────────────────────────────────────
function impactColor(impact?: string | null): string {
  switch (impact) {
    case "positive":
      return "bg-green-50 text-green-700 border border-green-200";
    case "negative":
      return "bg-red-50 text-red-700 border border-red-200";
    case "mixed":
      return "bg-amber-50 text-amber-700 border border-amber-200";
    default:
      return "bg-gray-100 text-gray-600";
  }
}

// ── Category 레이블 ─────────────────────────────────────────────────────────
function categoryLabel(cat: string): string {
  switch (cat) {
    case "stock":
      return "기업";
    case "industry":
      return "산업";
    case "macro":
      return "매크로";
    default:
      return cat;
  }
}

// ── Thesis 카드 ─────────────────────────────────────────────────────────────
function ThesisCard({ thesis }: { thesis: { summary?: string; sentiment?: number | null } }) {
  if (!thesis.summary && thesis.sentiment == null) return null;
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-700">핵심 투자 논리</h3>
      {thesis.summary && (
        <p className="text-gray-800 leading-relaxed text-sm">{thesis.summary}</p>
      )}
      {thesis.sentiment != null && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">감성 점수</span>
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${sentimentStyle(thesis.sentiment).color}`}>
            {sentimentStyle(thesis.sentiment).label} ({thesis.sentiment.toFixed(2)})
          </span>
          {/* Gauge bar */}
          <div className="flex-1 max-w-[120px] h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                thesis.sentiment >= 0.3
                  ? "bg-green-500"
                  : thesis.sentiment <= -0.3
                  ? "bg-red-500"
                  : "bg-gray-400"
              }`}
              style={{ width: `${((thesis.sentiment + 1) / 2) * 100}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Chain 카드 ──────────────────────────────────────────────────────────────
function ChainSection({ chain }: { chain: Layer2ChainStep[] }) {
  if (!chain || chain.length === 0) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-700">인과 추론 체인</h3>
      <div className="space-y-2">
        {chain.map((step, i) => (
          <div key={i} className={`flex gap-3 p-3 rounded-lg border ${directionColor(step.direction)}`}>
            <div className="flex flex-col items-center gap-1 pt-0.5">
              <div className={`w-2 h-2 rounded-full shrink-0 ${directionDot(step.direction)}`} />
              {i < chain.length - 1 && (
                <div className="w-0.5 flex-1 bg-gray-200 min-h-[12px]" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-semibold uppercase tracking-wide opacity-70">
                  {STEP_LABELS[step.step] ?? step.step}
                </span>
                <ConfidenceBadge confidence={step.confidence} />
              </div>
              <p className="text-sm leading-relaxed">{step.text}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Opinion 카드 ────────────────────────────────────────────────────────────
function OpinionCard({
  opinion,
}: {
  opinion: {
    rating?: string;
    target_price?: number;
    prev_rating?: string;
    prev_target_price?: number;
    change_reason?: string;
  };
}) {
  if (!opinion.rating && !opinion.target_price) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-700">투자의견</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        {opinion.rating && (
          <div>
            <div className="text-xs text-gray-400 mb-0.5">의견</div>
            <div className="font-semibold text-gray-900">{opinion.rating}</div>
            {opinion.prev_rating && opinion.prev_rating !== opinion.rating && (
              <div className="text-xs text-gray-400">이전: {opinion.prev_rating}</div>
            )}
          </div>
        )}
        {opinion.target_price && (
          <div>
            <div className="text-xs text-gray-400 mb-0.5">목표주가</div>
            <div className="font-semibold text-gray-900">
              {opinion.target_price.toLocaleString()}원
            </div>
            {opinion.prev_target_price &&
              opinion.prev_target_price !== opinion.target_price && (
                <div className="text-xs text-gray-400">
                  이전: {opinion.prev_target_price.toLocaleString()}원
                </div>
              )}
          </div>
        )}
      </div>
      {opinion.change_reason && (
        <p className="text-sm text-gray-600 italic">{opinion.change_reason}</p>
      )}
    </div>
  );
}

// ── Financials 테이블 ───────────────────────────────────────────────────────
function FinancialsTable({
  financials,
}: {
  financials: {
    earnings_quarter?: string;
    revenue?: string | number;
    operating_profit?: string | number;
    eps?: string | number;
    key_metrics?: Record<string, string | number>;
  };
}) {
  const hasBasic =
    financials.earnings_quarter ||
    financials.revenue !== undefined ||
    financials.operating_profit !== undefined ||
    financials.eps !== undefined;
  const hasMetrics =
    financials.key_metrics && Object.keys(financials.key_metrics).length > 0;

  if (!hasBasic && !hasMetrics) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-700">재무 추정치</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border border-gray-200 rounded-lg overflow-hidden">
          <tbody className="divide-y divide-gray-100">
            {financials.earnings_quarter && (
              <tr className="bg-gray-50">
                <td className="px-3 py-2 text-gray-500 font-medium w-32">실적 분기</td>
                <td className="px-3 py-2 text-gray-900">{financials.earnings_quarter}</td>
              </tr>
            )}
            {financials.revenue !== undefined && (
              <tr>
                <td className="px-3 py-2 text-gray-500 font-medium">매출액</td>
                <td className="px-3 py-2 text-gray-900">{String(financials.revenue)}</td>
              </tr>
            )}
            {financials.operating_profit !== undefined && (
              <tr className="bg-gray-50">
                <td className="px-3 py-2 text-gray-500 font-medium">영업이익</td>
                <td className="px-3 py-2 text-gray-900">{String(financials.operating_profit)}</td>
              </tr>
            )}
            {financials.eps !== undefined && (
              <tr>
                <td className="px-3 py-2 text-gray-500 font-medium">EPS</td>
                <td className="px-3 py-2 text-gray-900">{String(financials.eps)}</td>
              </tr>
            )}
            {hasMetrics &&
              Object.entries(financials.key_metrics!).map(([k, v], idx) => (
                <tr key={k} className={idx % 2 === 0 ? "bg-gray-50" : ""}>
                  <td className="px-3 py-2 text-gray-500 font-medium">{k}</td>
                  <td className="px-3 py-2 text-gray-900">{String(v)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main Layer2Section ──────────────────────────────────────────────────────
interface Props {
  layer2: Layer2Data;
}

export default function Layer2Section({ layer2 }: Props) {
  const { analysis_data, stock_mentions, sector_mentions, keywords, report_category } = layer2;
  const thesis = analysis_data.thesis;
  const chain = analysis_data.chain ?? [];
  const opinion = analysis_data.opinion;
  const financials = analysis_data.financials;

  const hasThesis = thesis && (thesis.summary || thesis.sentiment != null);
  const hasChain = chain.length > 0;
  const hasOpinion = opinion && (opinion.rating || opinion.target_price);
  const hasFinancials =
    financials &&
    (financials.earnings_quarter ||
      financials.revenue !== undefined ||
      financials.operating_profit !== undefined ||
      financials.eps !== undefined ||
      (financials.key_metrics && Object.keys(financials.key_metrics).length > 0));
  const hasStocks = stock_mentions.length > 0;
  const hasSectors = sector_mentions.length > 0;
  const hasKeywords = keywords.length > 0;

  if (!hasThesis && !hasChain && !hasOpinion && !hasFinancials && !hasStocks && !hasSectors && !hasKeywords) {
    return null;
  }

  return (
    <div className="bg-white rounded-xl border border-blue-100 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 text-blue-700 font-medium">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          Layer2 심층 분석
        </div>
        <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">
          {categoryLabel(report_category)}
        </span>
      </div>

      {/* Thesis */}
      {hasThesis && <ThesisCard thesis={thesis!} />}

      {/* Chain */}
      {hasChain && <ChainSection chain={chain} />}

      {/* Opinion */}
      {hasOpinion && <OpinionCard opinion={opinion!} />}

      {/* Financials */}
      {hasFinancials && <FinancialsTable financials={financials!} />}

      {/* Related Stocks */}
      {hasStocks && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-gray-700">관련 종목</h3>
          <div className="flex flex-wrap gap-2">
            {stock_mentions.map((sm, i) => (
              <span
                key={i}
                className={`text-xs px-2.5 py-1 rounded-full font-medium ${impactColor(sm.impact)}`}
                title={sm.mention_type}
              >
                {sm.company_name || sm.stock_code}
                {sm.stock_code && sm.company_name && (
                  <span className="opacity-60 ml-1">({sm.stock_code})</span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Related Sectors */}
      {hasSectors && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-gray-700">관련 섹터</h3>
          <div className="flex flex-wrap gap-2">
            {sector_mentions.map((sm, i) => (
              <span
                key={i}
                className={`text-xs px-2.5 py-1 rounded-full font-medium ${impactColor(sm.impact)}`}
              >
                {sm.sector}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Keywords */}
      {hasKeywords && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-gray-700">키워드</h3>
          <div className="flex flex-wrap gap-1.5">
            {keywords.map((kw, i) => (
              <span
                key={i}
                className="text-xs px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full"
                title={kw.keyword_type ?? undefined}
              >
                {kw.keyword}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
