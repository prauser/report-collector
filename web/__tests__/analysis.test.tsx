import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { StockListItem, StockHistoryItem } from "@/lib/api";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  useSearchParams: vi.fn(() => new URLSearchParams()),
  useRouter: vi.fn(() => ({ push: vi.fn() })),
  usePathname: vi.fn(() => "/analysis"),
}));

// Mock React.use so the StockHistoryPage can be tested without Suspense wrappers
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    use: vi.fn((val: unknown) => {
      // If it's a thenable (Promise), return its resolved value synchronously via _mockResolvedValue
      if (val && typeof (val as { _mockResolvedValue?: unknown })._mockResolvedValue !== "undefined") {
        return (val as { _mockResolvedValue: unknown })._mockResolvedValue;
      }
      // Otherwise fall through to the real use (for context etc.)
      return (actual.use as (val: unknown) => unknown)(val);
    }),
  };
});

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

// Mock recharts — the library uses browser layout APIs unavailable in jsdom
vi.mock("recharts", () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="line-chart">{children}</div>
  ),
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      stocks: {
        list: vi.fn(),
        history: vi.fn(),
      },
    },
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeStockItem(overrides: Partial<StockListItem> = {}): StockListItem {
  return {
    stock_code: "005930",
    stock_name: "삼성전자",
    report_count: 42,
    latest_report_date: "2024-03-15",
    avg_sentiment: 0.4,
    ...overrides,
  };
}

function makeHistoryItem(overrides: Partial<StockHistoryItem> = {}): StockHistoryItem {
  return {
    report_id: 1,
    broker: "미래에셋",
    report_date: "2024-03-15",
    title: "삼성전자 목표가 상향",
    opinion: "매수",
    target_price: 90000,
    layer2_summary: "반도체 업황 개선으로 목표가를 상향합니다.",
    layer2_sentiment: 0.7,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// TargetPriceChart
// ---------------------------------------------------------------------------

import TargetPriceChart from "@/components/analysis/TargetPriceChart";

describe("TargetPriceChart", () => {
  it("shows empty message when no items have target_price", () => {
    const items = [makeHistoryItem({ target_price: null })];
    render(<TargetPriceChart items={items} />);
    expect(screen.getByText("목표가 데이터가 없습니다")).toBeInTheDocument();
  });

  it("renders chart when items have target_price", () => {
    const items = [makeHistoryItem({ target_price: 90000 })];
    render(<TargetPriceChart items={items} />);
    expect(screen.getByTestId("responsive-container")).toBeInTheDocument();
    expect(screen.getByTestId("line-chart")).toBeInTheDocument();
  });

  it("renders chart with multiple brokers", () => {
    const items = [
      makeHistoryItem({ broker: "미래에셋", target_price: 90000 }),
      makeHistoryItem({ broker: "삼성증권", target_price: 95000, report_id: 2 }),
    ];
    render(<TargetPriceChart items={items} />);
    expect(screen.getByTestId("line-chart")).toBeInTheDocument();
  });

  it("renders empty message when items array is empty", () => {
    render(<TargetPriceChart items={[]} />);
    expect(screen.getByText("목표가 데이터가 없습니다")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// OpinionTimeline
// ---------------------------------------------------------------------------

import OpinionTimeline from "@/components/analysis/OpinionTimeline";

describe("OpinionTimeline", () => {
  it("shows empty message when no items have opinion", () => {
    const items = [makeHistoryItem({ opinion: null })];
    render(<OpinionTimeline items={items} />);
    expect(screen.getByText("투자의견 데이터가 없습니다")).toBeInTheDocument();
  });

  it("shows empty message for empty array", () => {
    render(<OpinionTimeline items={[]} />);
    expect(screen.getByText("투자의견 데이터가 없습니다")).toBeInTheDocument();
  });

  it("renders opinion badge for each item with opinion", () => {
    const items = [
      makeHistoryItem({ opinion: "매수", report_id: 1 }),
      makeHistoryItem({ opinion: "중립", report_id: 2, broker: "삼성증권" }),
    ];
    render(<OpinionTimeline items={items} />);
    expect(screen.getByText("매수")).toBeInTheDocument();
    expect(screen.getByText("중립")).toBeInTheDocument();
  });

  it("renders report date for each item", () => {
    const items = [makeHistoryItem({ report_date: "2024-03-15" })];
    render(<OpinionTimeline items={items} />);
    expect(screen.getByText("2024-03-15")).toBeInTheDocument();
  });

  it("renders broker name", () => {
    const items = [makeHistoryItem({ broker: "미래에셋" })];
    render(<OpinionTimeline items={items} />);
    expect(screen.getByText("미래에셋")).toBeInTheDocument();
  });

  it("applies green color class for buy opinion", () => {
    const items = [makeHistoryItem({ opinion: "매수" })];
    render(<OpinionTimeline items={items} />);
    const badge = screen.getByText("매수");
    expect(badge.className).toContain("green");
  });

  it("applies yellow color class for hold opinion", () => {
    const items = [makeHistoryItem({ opinion: "중립" })];
    render(<OpinionTimeline items={items} />);
    const badge = screen.getByText("중립");
    expect(badge.className).toContain("yellow");
  });

  it("applies red color class for sell opinion", () => {
    const items = [makeHistoryItem({ opinion: "매도" })];
    render(<OpinionTimeline items={items} />);
    const badge = screen.getByText("매도");
    expect(badge.className).toContain("red");
  });
});

// ---------------------------------------------------------------------------
// Analysis page (stock list)
// ---------------------------------------------------------------------------

import AnalysisPage from "@/app/analysis/page";

describe("AnalysisPage", () => {
  beforeEach(async () => {
    const { api } = await import("@/lib/api");
    const listMock = api.stocks.list as ReturnType<typeof vi.fn>;
    listMock.mockResolvedValue({
      items: [
        makeStockItem(),
        makeStockItem({
          stock_code: "000660",
          stock_name: "SK하이닉스",
          report_count: 30,
          avg_sentiment: 0.2,
        }),
      ],
      total: 2,
      limit: 30,
      offset: 0,
    });
  });

  it("renders page heading", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("종목분석")).toBeInTheDocument();
    });
  });

  it("renders stock table after loading", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
      expect(screen.getByText("SK하이닉스")).toBeInTheDocument();
    });
  });

  it("renders stock codes", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("005930")).toBeInTheDocument();
      expect(screen.getByText("000660")).toBeInTheDocument();
    });
  });

  it("renders report count", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("42")).toBeInTheDocument();
    });
  });

  it("renders latest_report_date", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getAllByText("2024-03-15").length).toBeGreaterThan(0);
    });
  });

  it("shows total count", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText(/2개 종목/)).toBeInTheDocument();
    });
  });

  it("renders search input", async () => {
    render(<AnalysisPage />);
    const input = screen.getByPlaceholderText("종목코드 또는 종목명 검색");
    expect(input).toBeInTheDocument();
  });

  it("renders sort buttons", async () => {
    render(<AnalysisPage />);
    expect(screen.getByText("리포트 수")).toBeInTheDocument();
    expect(screen.getByText("최신순")).toBeInTheDocument();
  });

  it("shows loading state initially", async () => {
    // Return a never-resolving promise to keep loading state
    const { api } = await import("@/lib/api");
    (api.stocks.list as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise(() => {})
    );
    render(<AnalysisPage />);
    // Both Suspense fallback and the inner loading state show this text
    expect(screen.getAllByText("불러오는 중...").length).toBeGreaterThan(0);
  });

  it("shows empty state when no stocks found", async () => {
    const { api } = await import("@/lib/api");
    (api.stocks.list as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 30,
      offset: 0,
    });
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("종목 데이터가 없습니다.")).toBeInTheDocument();
    });
  });

  it("renders links to stock detail pages", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      const link = screen.getByRole("link", { name: "삼성전자" });
      expect(link).toHaveAttribute("href", "/analysis/stocks/005930");
    });
  });

  it("renders table column headers", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("종목코드")).toBeInTheDocument();
    });
    expect(screen.getByText("종목명")).toBeInTheDocument();
    expect(screen.getByText("리포트")).toBeInTheDocument();
    expect(screen.getByText("최신 리포트")).toBeInTheDocument();
    expect(screen.getByText("평균 감성")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// API types — structural checks
// ---------------------------------------------------------------------------

import type { StockListResponse, StockHistoryResponse } from "@/lib/api";

describe("API type shapes", () => {
  it("StockListItem has required fields", () => {
    const item: StockListItem = makeStockItem();
    expect(item).toHaveProperty("stock_code");
    expect(item).toHaveProperty("stock_name");
    expect(item).toHaveProperty("report_count");
    expect(item).toHaveProperty("latest_report_date");
    expect(item).toHaveProperty("avg_sentiment");
  });

  it("StockHistoryItem has required fields", () => {
    const item: StockHistoryItem = makeHistoryItem();
    expect(item).toHaveProperty("report_id");
    expect(item).toHaveProperty("broker");
    expect(item).toHaveProperty("report_date");
    expect(item).toHaveProperty("title");
    expect(item).toHaveProperty("opinion");
    expect(item).toHaveProperty("target_price");
    expect(item).toHaveProperty("layer2_summary");
    expect(item).toHaveProperty("layer2_sentiment");
  });

  it("StockListResponse shape", () => {
    const resp: StockListResponse = {
      items: [makeStockItem()],
      total: 1,
      limit: 30,
      offset: 0,
    };
    expect(resp.items).toHaveLength(1);
    expect(resp.total).toBe(1);
  });

  it("StockHistoryResponse shape", () => {
    const resp: StockHistoryResponse = {
      stock_code: "005930",
      stock_name: "삼성전자",
      items: [makeHistoryItem()],
      total: 1,
      limit: 50,
      offset: 0,
    };
    expect(resp.stock_code).toBe("005930");
    expect(resp.items).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// StockHistoryPage
// ---------------------------------------------------------------------------

import StockHistoryPage from "@/app/analysis/stocks/[code]/page";

/** Create a fake params Promise that the mocked React.use can unwrap. */
function makeParams(code: string): Promise<{ code: string }> {
  const p = Promise.resolve({ code }) as Promise<{ code: string }> & { _mockResolvedValue: { code: string } };
  p._mockResolvedValue = { code };
  return p;
}

function makeHistoryResponse(overrides: Partial<StockHistoryResponse> = {}): StockHistoryResponse {
  return {
    stock_code: "005930",
    stock_name: "삼성전자",
    items: [
      makeHistoryItem({ report_id: 1 }),
      makeHistoryItem({ report_id: 2, broker: "삼성증권", target_price: 95000 }),
    ],
    total: 2,
    limit: 50,
    offset: 0,
    ...overrides,
  };
}

describe("StockHistoryPage", () => {
  beforeEach(async () => {
    const { api } = await import("@/lib/api");
    const historyMock = api.stocks.history as ReturnType<typeof vi.fn>;
    historyMock.mockResolvedValue(makeHistoryResponse());
  });

  it("renders stock name and code after loading", async () => {
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
    });
    expect(screen.getByText(/005930/)).toBeInTheDocument();
  });

  it("renders report list items after loading", async () => {
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(screen.getAllByText("삼성전자 목표가 상향").length).toBeGreaterThan(0);
    });
  });

  it("renders back link to /analysis", async () => {
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
    });
    const backLink = screen.getByRole("link", { name: /← 종목분석/ });
    expect(backLink).toHaveAttribute("href", "/analysis");
  });

  it("renders pagination controls when totalPages > 1", async () => {
    const { api } = await import("@/lib/api");
    const historyMock = api.stocks.history as ReturnType<typeof vi.fn>;
    historyMock.mockResolvedValue(
      makeHistoryResponse({ total: 120 })
    );
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(screen.getByText("이전")).toBeInTheDocument();
      expect(screen.getByText("다음")).toBeInTheDocument();
    });
  });

  it("does not render pagination when only one page", async () => {
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
    });
    expect(screen.queryByText("이전")).not.toBeInTheDocument();
    expect(screen.queryByText("다음")).not.toBeInTheDocument();
  });

  it("renders error state when API call fails", async () => {
    const { api } = await import("@/lib/api");
    const historyMock = api.stocks.history as ReturnType<typeof vi.fn>;
    historyMock.mockRejectedValue(new Error("API error 500: /api/stocks/005930/history"));
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(
        screen.getByText("API error 500: /api/stocks/005930/history")
      ).toBeInTheDocument();
    });
  });

  it("renders total report count", async () => {
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(screen.getByText(/총 2건 리포트/)).toBeInTheDocument();
    });
  });

  it("renders chart sections", async () => {
    render(<StockHistoryPage params={makeParams("005930")} />);
    await waitFor(() => {
      expect(screen.getByText("목표가 추이")).toBeInTheDocument();
      expect(screen.getByText("투자의견 변화")).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

import Navigation from "@/components/Navigation";

describe("Navigation — 종목분석 menu", () => {
  it("renders 종목분석 link in desktop nav", () => {
    render(<Navigation />);
    const links = screen.getAllByRole("link", { name: "종목분석" });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]).toHaveAttribute("href", "/analysis");
  });
});
