import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Test the trades namespace types and structure of api.ts
describe("api.trades namespace", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("api.trades.list calls GET /api/trades", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 30, offset: 0 }),
    } as Response);

    const { api } = await import("@/lib/api");
    await api.trades.list({ limit: 30, offset: 0 });

    expect(mockFetch).toHaveBeenCalledOnce();
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/api/trades");
    expect(calledUrl).toContain("limit=30");
    expect(calledUrl).toContain("offset=0");
  });

  it("api.trades.list passes symbol filter", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 30, offset: 0 }),
    } as Response);

    const { api } = await import("@/lib/api");
    await api.trades.list({ symbol: "005930" });

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("symbol=005930");
  });

  it("api.trades.list passes side filter", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 30, offset: 0 }),
    } as Response);

    const { api } = await import("@/lib/api");
    await api.trades.list({ side: "buy" });

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("side=buy");
  });

  it("api.trades.updateReason calls PATCH /api/trades/{id}/reason", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 1, reason: "test reason" }),
    } as Response);

    const { api } = await import("@/lib/api");
    await api.trades.updateReason(1, "test reason");

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, options] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/trades/1/reason");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body as string)).toEqual({ reason: "test reason" });
  });

  it("api.trades.updateReview calls PATCH /api/trades/{id}/review", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 1, review: "test review" }),
    } as Response);

    const { api } = await import("@/lib/api");
    await api.trades.updateReview(1, "test review");

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, options] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/trades/1/review");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body as string)).toEqual({ review: "test review" });
  });

  it("api.trades.list omits undefined params", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 30, offset: 0 }),
    } as Response);

    const { api } = await import("@/lib/api");
    await api.trades.list({ symbol: undefined, broker: undefined });

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("symbol=");
    expect(calledUrl).not.toContain("broker=");
  });

  it("api.trades.list passes date_from and date_to", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [], total: 0, limit: 30, offset: 0 }),
    } as Response);

    const { api } = await import("@/lib/api");
    await api.trades.list({ date_from: "2024-01-01", date_to: "2024-12-31" });

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("date_from=2024-01-01");
    expect(calledUrl).toContain("date_to=2024-12-31");
  });

  it("throws on non-ok response", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    } as Response);

    const { api } = await import("@/lib/api");
    await expect(api.trades.list()).rejects.toThrow("API error 500");
  });
});

describe("api.trades.upload", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls POST /api/trades/upload with FormData", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ inserted: 3, skipped: 0, preview: null }),
    } as Response);

    const { api } = await import("@/lib/api");
    const file = new File(["col"], "trades.csv", { type: "text/csv" });
    await api.trades.upload(file);

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, options] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/trades/upload");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
  });

  it("appends dry_run=true to URL when specified", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ inserted: 0, skipped: 0, preview: [] }),
    } as Response);

    const { api } = await import("@/lib/api");
    const file = new File(["col"], "trades.csv", { type: "text/csv" });
    await api.trades.upload(file, undefined, true);

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("dry_run=true");
  });

  it("appends broker param when provided", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ inserted: 0, skipped: 0, preview: [] }),
    } as Response);

    const { api } = await import("@/lib/api");
    const file = new File(["col"], "trades.csv", { type: "text/csv" });
    await api.trades.upload(file, "kiwoom", false);

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("broker=kiwoom");
    expect(url).toContain("dry_run=false");
  });

  it("throws error with API detail message on non-ok response", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({ detail: "브로커를 자동으로 감지하지 못했습니다." }),
    } as Response);

    const { api } = await import("@/lib/api");
    const file = new File(["col"], "trades.csv", { type: "text/csv" });
    await expect(api.trades.upload(file)).rejects.toThrow(
      "브로커를 자동으로 감지하지 못했습니다."
    );
  });

  it("throws generic error message when response body is not parseable", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => { throw new Error("not json"); },
    } as unknown as Response);

    const { api } = await import("@/lib/api");
    const file = new File(["col"], "trades.csv", { type: "text/csv" });
    await expect(api.trades.upload(file)).rejects.toThrow("API error 500");
  });

  it("does not append broker when not provided", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ inserted: 0, skipped: 0, preview: null }),
    } as Response);

    const { api } = await import("@/lib/api");
    const file = new File(["col"], "trades.csv", { type: "text/csv" });
    await api.trades.upload(file);

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).not.toContain("broker=");
  });
});
