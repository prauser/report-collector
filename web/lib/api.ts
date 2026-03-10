const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ReportSummary {
  id: number;
  broker: string;
  report_date: string;
  analyst: string | null;
  stock_name: string | null;
  stock_code: string | null;
  title: string;
  sector: string | null;
  report_type: string | null;
  opinion: string | null;
  target_price: number | null;
  prev_opinion: string | null;
  prev_target_price: number | null;
  has_pdf: boolean;
  has_ai: boolean;
  ai_sentiment: string | null;
  collected_at: string;
  source_channel: string;
}

export interface ReportDetail extends ReportSummary {
  ai_summary: string | null;
  ai_keywords: string[] | null;
  ai_processed_at: string | null;
  pdf_url: string | null;
  pdf_size_kb: number | null;
  page_count: number | null;
  earnings_quarter: string | null;
  est_revenue: number | null;
  est_op_profit: number | null;
  est_eps: number | null;
  raw_text: string | null;
}

export interface PaginatedReports {
  total: number;
  page: number;
  limit: number;
  items: ReportSummary[];
}

export interface FilterOptions {
  brokers: string[];
  opinions: string[];
  report_types: string[];
  channels: string[];
}

export interface OverviewStats {
  total_reports: number;
  reports_today: number;
  reports_with_pdf: number;
  reports_with_ai: number;
  top_brokers: { broker: string; count: number }[];
  top_stocks: { stock: string; count: number }[];
}

export interface LlmStats {
  period_days: number;
  total_cost_usd: string;
  by_purpose: {
    model: string;
    purpose: string;
    message_type: string | null;
    call_count: number;
    total_input_tokens: number;
    total_output_tokens: number;
    total_cost_usd: string;
  }[];
  by_message_type: { message_type: string; count: number; cost_usd: number }[];
  daily_cost: { date: string; cost_usd: number }[];
}

export type ReportListParams = {
  q?: string;
  stock?: string;
  broker?: string;
  opinion?: string;
  report_type?: string;
  channel?: string;
  from_date?: string;
  to_date?: string;
  has_ai?: boolean;
  page?: number;
  limit?: number;
};

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const url = new URL(`${BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    });
  }
  const res = await fetch(url.toString(), { next: { revalidate: 30 } });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export const api = {
  reports: {
    list: (params: ReportListParams) =>
      get<PaginatedReports>("/api/reports", params as Record<string, unknown>),
    get: (id: number) => get<ReportDetail>(`/api/reports/${id}`),
    filters: () => get<FilterOptions>("/api/reports/filters"),
  },
  stats: {
    overview: () => get<OverviewStats>("/api/stats/overview"),
    llm: (days = 30) => get<LlmStats>("/api/stats/llm", { days }),
  },
};
