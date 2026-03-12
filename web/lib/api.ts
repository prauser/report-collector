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

export interface BackfillChannelStat {
  channel: string;
  last_run_date: string | null;
  last_finished_at: string | null;
  latest_message_id: number | null;
  earliest_from_id: number | null;
  total_runs: number;
  total_scanned: number;
  total_saved: number;
  total_pending: number;
  total_skipped: number;
}

export interface BackfillRunItem {
  channel: string;
  run_date: string;
  started_at: string;
  finished_at: string | null;
  from_message_id: number | null;
  to_message_id: number | null;
  n_scanned: number;
  n_saved: number;
  n_pending: number;
  n_skipped: number;
  status: string;
}

export interface PdfCoverage {
  channel: string;
  total_reports: number;
  has_pdf_url: number;
  pdf_downloaded: number;
  ai_analyzed: number;
}

export interface BackfillStats {
  by_channel: BackfillChannelStat[];
  recent_runs: BackfillRunItem[];
  pdf_coverage: PdfCoverage[];
}

export interface PendingMessage {
  id: number;
  source_channel: string;
  source_message_id: number | null;
  raw_text: string | null;
  pdf_url: string | null;
  s2a_label: string | null;
  s2a_reason: string | null;
  review_status: string;
  created_at: string;
}

export interface PendingListResponse {
  items: PendingMessage[];
  total: number;
  limit: number;
  offset: number;
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
    backfill: () => get<BackfillStats>("/api/stats/backfill"),
  },
  backfill: {
    channels: () => get<{ channels: string[] }>("/api/backfill/channels"),
    running: () => get<{ running: string[] }>("/api/backfill/running"),
    run: (channel: string) =>
      fetch(`${BASE}/api/backfill/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel }),
      }).then((r) => r.json()),
  },
  pending: {
    list: (params?: { status?: string; channel?: string; limit?: number; offset?: number }) =>
      get<PendingListResponse>("/api/pending", params as Record<string, unknown>),
    stats: () => get<Record<string, number>>("/api/pending/stats"),
    resolve: (id: number, decision: "broker_report" | "discarded") =>
      fetch(`${BASE}/api/pending/${id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      }).then((r) => r.json()),
  },
};
