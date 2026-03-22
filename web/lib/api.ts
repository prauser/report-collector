import type { ChatSession, ChatMessage, SseEvent } from "./agent-types";

export type { ChatSession, ChatMessage, SseEvent };

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Layer2 types
// ---------------------------------------------------------------------------

export interface Layer2StockMention {
  stock_code: string | null;
  company_name: string | null;
  mention_type: string;
  impact: string | null;
  relevance_score: number | null;
}

export interface Layer2SectorMention {
  sector: string;
  mention_type: string;
  impact: string | null;
}

export interface Layer2Keyword {
  keyword: string;
  keyword_type: string | null;
}

export interface Layer2ChainStep {
  step: string;
  text: string;
  direction?: "positive" | "negative" | "neutral" | "mixed";
  confidence?: "high" | "medium" | "low";
}

export interface Layer2Thesis {
  summary?: string;
  sentiment?: number | null;
}

export interface Layer2Opinion {
  rating?: string;
  target_price?: number;
  prev_rating?: string;
  prev_target_price?: number;
  change_reason?: string;
}

export interface Layer2Financials {
  earnings_quarter?: string;
  revenue?: string | number;
  operating_profit?: string | number;
  eps?: string | number;
  key_metrics?: Record<string, string | number>;
}

export interface Layer2Meta {
  broker?: string;
  analyst?: string;
  title?: string;
  report_type?: string;
  stock_name?: string;
  stock_code?: string;
  sector?: string;
  opinion?: string;
  target_price?: number;
  prev_opinion?: string;
  prev_target_price?: number;
}

export interface Layer2AnalysisData {
  meta?: Layer2Meta;
  target?: Record<string, string>;
  thesis?: Layer2Thesis;
  chain?: Layer2ChainStep[];
  opinion?: Layer2Opinion;
  financials?: Layer2Financials;
}

export interface Layer2Data {
  report_category: string;
  analysis_data: Layer2AnalysisData;
  extraction_quality: string | null;
  stock_mentions: Layer2StockMention[];
  sector_mentions: Layer2SectorMention[];
  keywords: Layer2Keyword[];
}

// ---------------------------------------------------------------------------
// Report types
// ---------------------------------------------------------------------------

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
  // Layer2 summary fields
  display_title: string;
  layer2_summary: string | null;
  layer2_sentiment: number | null;
  layer2_category: string | null;
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
  source_message_id: number | null;
  // Layer2 full data
  layer2: Layer2Data | null;
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
  analysis_done: number;
  analysis_pending: number;
  analysis_failed: number;
  analysis_truncated: number;
  analysis_by_category: { category: string; count: number }[];
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
  error_msg: string | null;
}

export interface ParseQuality {
  good: number;
  partial: number;
  poor: number;
  unknown: number;
}

export interface PdfCoverage {
  channel: string;
  total_reports: number;
  has_pdf_url: number;
  pdf_downloaded: number;
  ai_analyzed: number;
  pdf_failed: number;
  parse_quality: ParseQuality;
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

/** Client-side fetch without Next.js cache — use for client components that need fresh data. */
async function clientFetch<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const url = new URL(`${BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    });
  }
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export interface TradeResponse {
  id: number;
  symbol: string;
  name: string | null;
  side: "buy" | "sell";
  traded_at: string;
  price: number;
  quantity: number;
  amount: number;
  broker: string | null;
  account_type: string | null;
  market: string | null;
  fees: number | null;
  reason: string | null;
  review: string | null;
  created_at: string;
}

export interface TradeListParams {
  symbol?: string;
  date_from?: string;
  date_to?: string;
  broker?: string;
  side?: string;
  account_type?: string;
  offset?: number;
  limit?: number;
}

export interface TradeListResponse {
  items: TradeResponse[];
  total: number;
  limit: number;
  offset: number;
}

export interface TradeBase {
  symbol: string;
  name: string | null;
  side: string;
  traded_at: string;
  price: number;
  quantity: number;
  amount: number;
  broker: string | null;
  account_type: string | null;
  market: string | null;
  fees: number | null;
}

export interface TradeUploadResponse {
  inserted: number;
  skipped: number;
  preview: TradeBase[] | null;
}

export interface TradeStatsResponse {
  total_count: number;
  buy_count: number;
  sell_count: number;
  total_amount: string;
  symbol_frequency: { symbol: string; count: number }[];
}

async function patch<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export const api = {
  agent: {
    getSessions: () =>
      clientFetch<{ sessions: ChatSession[] }>("/api/agent/sessions").then(
        (r) => r.sessions
      ),
    getMessages: (sessionId: number) =>
      clientFetch<{ messages: ChatMessage[] }>(
        `/api/agent/sessions/${sessionId}/messages`
      ).then((r) => r.messages),
    deleteSession: async (sessionId: number): Promise<void> => {
      const res = await fetch(`${BASE}/api/agent/sessions/${sessionId}`, {
        method: "DELETE",
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`API error ${res.status}: DELETE session`);
    },
    /**
     * Send a chat message and return an async generator of SSE events.
     * Uses fetch + ReadableStream (not EventSource) so we can POST a body.
     * Pass an AbortSignal to cancel the stream (e.g. on component unmount).
     */
    chat: async function* (
      message: string,
      sessionId?: number,
      signal?: AbortSignal
    ): AsyncGenerator<SseEvent> {
      const res = await fetch(`${BASE}/api/agent/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
        cache: "no-store",
        signal,
      });
      if (!res.ok) {
        throw new Error(`API error ${res.status}: POST /api/agent/chat`);
      }
      if (!res.body) throw new Error("No response body for SSE stream");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE lines are separated by \n\n
          const parts = buffer.split("\n\n");
          buffer = parts.pop() ?? "";

          for (const part of parts) {
            for (const line of part.split("\n")) {
              if (line.startsWith("data: ")) {
                const jsonStr = line.slice(6).trim();
                if (!jsonStr) continue;
                try {
                  const event = JSON.parse(jsonStr) as SseEvent;
                  yield event;
                } catch {
                  // ignore malformed JSON
                }
              }
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    },
  },
  reports: {
    list: (params: ReportListParams) =>
      get<PaginatedReports>("/api/reports", params as Record<string, unknown>),
    get: (id: number) => get<ReportDetail>(`/api/reports/${id}`),
    filters: () => get<FilterOptions>("/api/reports/filters"),
  },
  trades: {
    list: (params?: TradeListParams) =>
      clientFetch<TradeListResponse>("/api/trades", params as Record<string, unknown>),
    stats: () => clientFetch<TradeStatsResponse>("/api/trades/stats"),
    updateReason: (id: number, reason: string) =>
      patch<TradeResponse>(`/api/trades/${id}/reason`, { reason }),
    updateReview: (id: number, review: string) =>
      patch<TradeResponse>(`/api/trades/${id}/review`, { review }),
    upload: async (file: File, broker?: string, dryRun?: boolean): Promise<TradeUploadResponse> => {
      const url = new URL(`${BASE}/api/trades/upload`);
      if (broker) url.searchParams.set("broker", broker);
      if (dryRun !== undefined) url.searchParams.set("dry_run", String(dryRun));
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(url.toString(), { method: "POST", body: form });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        const msg = detail?.detail ?? `API error ${res.status}`;
        throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      }
      return res.json();
    },
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
