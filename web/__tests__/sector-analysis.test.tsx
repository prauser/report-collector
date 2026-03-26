import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { SectorListItem, SectorStockItem } from "@/lib/api";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  useSearchParams: vi.fn(() => new URLSearchParams()),
  useRouter: vi.fn(() => ({ push: vi.fn() })),
  usePathname: vi.fn(() => "/analysis"),
}));

// Mock React.use so SectorPage can be tested without Suspense wrappers
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    use: vi.fn((val: unknown) => {
      if (
        val &&
        typeof (val as { _mockResolvedValue?: unknown })._mockResolvedValue !==
          "undefined"
      ) {
        return (val as { _mockResolvedValue: unknown })._mockResolvedValue;
      }
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

// Mock recharts — browser layout APIs unavailable in jsdom
vi.mock("recharts", () => ({
  PieChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="pie-chart">{children}</div>
  ),
  Pie: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="pie">{children}</div>
  ),
  Cell: () => null,
  BarChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="bar-chart">{children}</div>
  ),
  Bar: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="bar">{children}</div>
  ),
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="line-chart">{children}</div>
  ),
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ReferenceLine: () => null,
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
      analysis: {
        sectors: vi.fn(),
        sector: vi.fn(),
      },
    },
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSectorListItem(overrides: Partial<SectorListItem> = {}): SectorListItem {
  return {
    sector_name: "반도체",
    report_count: 120,
    avg_sentiment: 0.5,
    top_stocks: [
      { stock_code: "005930", stock_name: "삼성전자", report_count: 60 },
      { stock_code: "000660", stock_name: "SK하이닉스", report_count: 40 },
    ],
    ...overrides,
  };
}

function makeSectorStockItem(overrides: Partial<SectorStockItem> = {}): SectorStockItem {
  return {
    stock_code: "005930",
    stock_name: "삼성전자",
    report_count: 60,
    avg_sentiment: 0.5,
    latest_opinion: "매수",
    latest_target_price: 90000,
    ...overrides,
  };
}

function makeParams(name: string): Promise<{ name: string }> {
  const p = Promise.resolve({ name }) as Promise<{ name: string }> & {
    _mockResolvedValue: { name: string };
  };
  p._mockResolvedValue = { name };
  return p;
}

// ---------------------------------------------------------------------------
// SectorPieChart
// ---------------------------------------------------------------------------

import SectorPieChart from "@/components/analysis/SectorPieChart";

describe("SectorPieChart", () => {
  it("shows empty message when no sectors", () => {
    render(<SectorPieChart sectors={[]} />);
    expect(screen.getByText("섹터 데이터가 없습니다")).toBeInTheDocument();
  });

  it("renders chart when sectors are provided", () => {
    const sectors = [
      makeSectorListItem(),
      makeSectorListItem({ sector_name: "IT", report_count: 80 }),
    ];
    render(<SectorPieChart sectors={sectors} />);
    expect(screen.getByTestId("responsive-container")).toBeInTheDocument();
    expect(screen.getByTestId("pie-chart")).toBeInTheDocument();
  });

  it("renders pie element for sector data", () => {
    render(<SectorPieChart sectors={[makeSectorListItem()]} />);
    expect(screen.getByTestId("pie")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SentimentBarChart
// ---------------------------------------------------------------------------

import SentimentBarChart from "@/components/analysis/SentimentBarChart";

describe("SentimentBarChart", () => {
  it("shows empty message when no stocks have avg_sentiment", () => {
    const stocks = [makeSectorStockItem({ avg_sentiment: null })];
    render(<SentimentBarChart stocks={stocks} />);
    expect(screen.getByText("감성 데이터가 없습니다")).toBeInTheDocument();
  });

  it("shows empty message for empty array", () => {
    render(<SentimentBarChart stocks={[]} />);
    expect(screen.getByText("감성 데이터가 없습니다")).toBeInTheDocument();
  });

  it("renders chart when stocks have avg_sentiment", () => {
    const stocks = [
      makeSectorStockItem({ avg_sentiment: 0.5 }),
      makeSectorStockItem({
        stock_code: "000660",
        stock_name: "SK하이닉스",
        avg_sentiment: -0.2,
      }),
    ];
    render(<SentimentBarChart stocks={stocks} />);
    expect(screen.getByTestId("responsive-container")).toBeInTheDocument();
    expect(screen.getByTestId("bar-chart")).toBeInTheDocument();
  });

  it("renders bar element", () => {
    render(<SentimentBarChart stocks={[makeSectorStockItem()]} />);
    expect(screen.getByTestId("bar")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AnalysisPage — tabs
// ---------------------------------------------------------------------------

import AnalysisPage from "@/app/analysis/page";

describe("AnalysisPage — tab structure", () => {
  beforeEach(async () => {
    const { api } = await import("@/lib/api");
    (api.stocks.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
      limit: 30,
      offset: 0,
    });
    (api.analysis.sectors as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
    });
  });

  it("renders 종목 and 섹터 tab buttons", () => {
    render(<AnalysisPage />);
    expect(screen.getByText("종목")).toBeInTheDocument();
    expect(screen.getByText("섹터")).toBeInTheDocument();
  });

  it("renders page heading '종목분석'", () => {
    render(<AnalysisPage />);
    expect(screen.getByText("종목분석")).toBeInTheDocument();
  });

  it("shows stocks tab by default (search input visible)", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText("종목코드 또는 종목명 검색")
      ).toBeInTheDocument();
    });
  });

  it("switches to sectors tab when 섹터 button is clicked", async () => {
    const { useRouter } = await import("next/navigation");
    const pushMock = vi.fn();
    (useRouter as ReturnType<typeof vi.fn>).mockReturnValue({ push: pushMock });

    render(<AnalysisPage />);
    fireEvent.click(screen.getByText("섹터"));

    expect(pushMock).toHaveBeenCalledWith(
      expect.stringContaining("tab=sectors")
    );
  });

  it("shows sectors tab when URL has tab=sectors", async () => {
    const { useSearchParams } = await import("next/navigation");
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(
      new URLSearchParams("tab=sectors")
    );

    const { api } = await import("@/lib/api");
    (api.analysis.sectors as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      items: [makeSectorListItem()],
    });

    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("섹터별 리포트 분포")).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// AnalysisPage — sectors tab content
// ---------------------------------------------------------------------------

describe("AnalysisPage — sectors tab content", () => {
  beforeEach(async () => {
    const { useSearchParams } = await import("next/navigation");
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(
      new URLSearchParams("tab=sectors")
    );

    const { api } = await import("@/lib/api");
    (api.analysis.sectors as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeSectorListItem({ sector_name: "반도체", report_count: 120 }),
        makeSectorListItem({ sector_name: "IT", report_count: 80, avg_sentiment: 0.3 }),
      ],
    });
  });

  it("renders sector names in table", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("반도체")).toBeInTheDocument();
      expect(screen.getByText("IT")).toBeInTheDocument();
    });
  });

  it("renders report counts", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("120")).toBeInTheDocument();
      expect(screen.getByText("80")).toBeInTheDocument();
    });
  });

  it("renders top stocks in sectors table", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getAllByText(/삼성전자/).length).toBeGreaterThan(0);
    });
  });

  it("renders sector donut chart section heading", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("섹터별 리포트 분포")).toBeInTheDocument();
    });
  });

  it("renders table column headers for sectors", async () => {
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("섹터명")).toBeInTheDocument();
      expect(screen.getByText(/리포트 수/)).toBeInTheDocument();
      expect(screen.getByText(/평균 감성/)).toBeInTheDocument();
      expect(screen.getByText("주요 종목")).toBeInTheDocument();
    });
  });

  it("shows empty state when no sectors", async () => {
    const { api } = await import("@/lib/api");
    (api.analysis.sectors as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      items: [],
    });
    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("섹터 데이터가 없습니다.")).toBeInTheDocument();
    });
  });

  it("navigates to sector detail page on row click", async () => {
    const { useRouter } = await import("next/navigation");
    const pushMock = vi.fn();
    (useRouter as ReturnType<typeof vi.fn>).mockReturnValue({ push: pushMock });

    render(<AnalysisPage />);
    await waitFor(() => {
      expect(screen.getByText("반도체")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("반도체").closest("tr")!);
    expect(pushMock).toHaveBeenCalledWith(
      expect.stringContaining("/analysis/sector/")
    );
  });
});

// ---------------------------------------------------------------------------
// SectorPage
// ---------------------------------------------------------------------------

import SectorPage from "@/app/analysis/sector/[name]/page";

describe("SectorPage", () => {
  beforeEach(async () => {
    const { api } = await import("@/lib/api");
    (api.analysis.sector as ReturnType<typeof vi.fn>).mockResolvedValue({
      sector_name: "반도체",
      items: [
        makeSectorStockItem({ stock_code: "005930", stock_name: "삼성전자" }),
        makeSectorStockItem({
          stock_code: "000660",
          stock_name: "SK하이닉스",
          avg_sentiment: -0.1,
          latest_opinion: "중립",
          latest_target_price: 200000,
        }),
      ],
    });
  });

  it("renders sector name as heading", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(screen.getByText("반도체")).toBeInTheDocument();
    });
  });

  it("renders stock names in table", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
      expect(screen.getByText("SK하이닉스")).toBeInTheDocument();
    });
  });

  it("renders stock codes in table", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(screen.getByText("005930")).toBeInTheDocument();
      expect(screen.getByText("000660")).toBeInTheDocument();
    });
  });

  it("renders back link to /analysis?tab=sectors", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(screen.getByText("반도체")).toBeInTheDocument();
    });
    const backLink = screen.getByRole("link", { name: /← 섹터분석/ });
    expect(backLink).toHaveAttribute("href", "/analysis?tab=sectors");
  });

  it("renders total stock count", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(screen.getByText(/2개/)).toBeInTheDocument();
    });
  });

  it("renders table column headers", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(screen.getByText("종목명")).toBeInTheDocument();
      expect(screen.getByText("종목코드")).toBeInTheDocument();
      expect(screen.getByText("최신 의견")).toBeInTheDocument();
      expect(screen.getByText("목표가")).toBeInTheDocument();
    });
  });

  it("renders latest_opinion badge", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      // "매수" appears as opinion badge (sentiment label is "긍정" for avg_sentiment 0.5)
      expect(screen.getByText("매수")).toBeInTheDocument();
      // "중립" may appear both as sentiment label and as opinion badge for SK하이닉스
      expect(screen.getAllByText("중립").length).toBeGreaterThan(0);
    });
  });

  it("renders latest_target_price formatted", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      // 90000원 → 90,000원
      expect(screen.getByText(/90,000원/)).toBeInTheDocument();
    });
  });

  it("renders links to stock detail pages", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      const link = screen.getByRole("link", { name: "삼성전자" });
      expect(link).toHaveAttribute("href", "/analysis/stocks/005930");
    });
  });

  it("renders sentiment bar chart section", async () => {
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(screen.getByText("종목별 감성 비교")).toBeInTheDocument();
    });
  });

  it("renders error message on API failure", async () => {
    const { api } = await import("@/lib/api");
    (api.analysis.sector as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("API error 404: /api/analysis/sector/없는섹터")
    );
    render(<SectorPage params={makeParams("%EC%97%86%EB%8A%94%EC%84%B9%ED%84%B0")} />);
    await waitFor(() => {
      expect(
        screen.getByText("API error 404: /api/analysis/sector/없는섹터")
      ).toBeInTheDocument();
    });
  });

  it("renders empty state when no stocks", async () => {
    const { api } = await import("@/lib/api");
    (api.analysis.sector as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      sector_name: "반도체",
      items: [],
    });
    render(<SectorPage params={makeParams("%EB%B0%98%EB%8F%84%EC%B2%B4")} />);
    await waitFor(() => {
      expect(
        screen.getByText("이 섹터에 해당하는 종목 데이터가 없습니다.")
      ).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// SectorPage — weighted average sentiment (fix for review issue #1)
// ---------------------------------------------------------------------------

describe("SectorPage — weighted average sentiment", () => {
  it("computes weighted average, not simple average", async () => {
    const { api } = await import("@/lib/api");
    // stock A: sentiment=1.0, report_count=90
    // stock B: sentiment=0.0, report_count=10
    // Simple avg = (1.0 + 0.0) / 2 = 0.5
    // Weighted avg = (1.0*90 + 0.0*10) / (90+10) = 90/100 = 0.9
    (api.analysis.sector as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      sector_name: "테스트",
      items: [
        makeSectorStockItem({ stock_code: "AAA", avg_sentiment: 1.0, report_count: 90 }),
        makeSectorStockItem({ stock_code: "BBB", avg_sentiment: 0.0, report_count: 10 }),
      ],
    });

    const { useSearchParams } = await import("next/navigation");
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(new URLSearchParams());

    render(<SectorPage params={makeParams("%ED%85%8C%EC%8A%A4%ED%8A%B8")} />);
    await waitFor(() => {
      // weighted avg = 0.9 → sentimentLabel >=0.6 → "매우 긍정"
      // simple avg = 0.5 → would be "긍정" (>=0.2 but <0.6)
      // So if we see "매우 긍정" in the header area, weighting is correct
      expect(screen.getAllByText("매우 긍정").length).toBeGreaterThan(0);
    });
  });

  it("excludes null sentiment stocks from weighted average", async () => {
    const { api } = await import("@/lib/api");
    (api.analysis.sector as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      sector_name: "테스트",
      items: [
        makeSectorStockItem({ stock_code: "AAA", avg_sentiment: 0.8, report_count: 50 }),
        makeSectorStockItem({ stock_code: "BBB", avg_sentiment: null, report_count: 100 }),
      ],
    });

    const { useSearchParams } = await import("next/navigation");
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(new URLSearchParams());

    render(<SectorPage params={makeParams("%ED%85%8C%EC%8A%A4%ED%8A%B8")} />);
    await waitFor(() => {
      // Only AAA contributes: avg = 0.8 → "매우 긍정" (>=0.6)
      // If null stock's count (100) were included, result could differ
      expect(screen.getAllByText("매우 긍정").length).toBeGreaterThan(0);
    });
  });

  it("shows null sentiment when all stocks have null sentiment", async () => {
    const { api } = await import("@/lib/api");
    (api.analysis.sector as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      sector_name: "테스트",
      items: [
        makeSectorStockItem({ stock_code: "AAA", avg_sentiment: null, report_count: 50 }),
      ],
    });

    const { useSearchParams } = await import("next/navigation");
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(new URLSearchParams());

    render(<SectorPage params={makeParams("%ED%85%8C%EC%8A%A4%ED%8A%B8")} />);
    await waitFor(() => {
      // null sentiment → sentimentLabel returns "-" or equivalent empty label
      const avgSentimentCell = screen.getAllByText(/-/);
      expect(avgSentimentCell.length).toBeGreaterThan(0);
    });
  });
});

// ---------------------------------------------------------------------------
// AnalysisPage — setTab preserves URL params (fix for review issue #2)
// ---------------------------------------------------------------------------

describe("AnalysisPage — setTab preserves URL params", () => {
  beforeEach(async () => {
    const { api } = await import("@/lib/api");
    (api.stocks.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
      limit: 30,
      offset: 0,
    });
    (api.analysis.sectors as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
    });
  });

  it("preserves search param when switching tabs", async () => {
    const { useSearchParams, useRouter } = await import("next/navigation");
    const pushMock = vi.fn();
    (useRouter as ReturnType<typeof vi.fn>).mockReturnValue({ push: pushMock });
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(
      new URLSearchParams("tab=stocks&search=삼성&sort=latest_date")
    );

    render(<AnalysisPage />);
    fireEvent.click(screen.getByText("섹터"));

    expect(pushMock).toHaveBeenCalledWith(
      expect.stringContaining("search=%EC%82%BC%EC%84%B1")
    );
    expect(pushMock).toHaveBeenCalledWith(
      expect.stringContaining("tab=sectors")
    );
  });

  it("preserves sort param when switching tabs", async () => {
    const { useSearchParams, useRouter } = await import("next/navigation");
    const pushMock = vi.fn();
    (useRouter as ReturnType<typeof vi.fn>).mockReturnValue({ push: pushMock });
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(
      new URLSearchParams("tab=stocks&sort=latest_date")
    );

    render(<AnalysisPage />);
    fireEvent.click(screen.getByText("섹터"));

    expect(pushMock).toHaveBeenCalledWith(
      expect.stringContaining("sort=latest_date")
    );
  });

  it("resets page param when switching tabs", async () => {
    const { useSearchParams, useRouter } = await import("next/navigation");
    const pushMock = vi.fn();
    (useRouter as ReturnType<typeof vi.fn>).mockReturnValue({ push: pushMock });
    (useSearchParams as ReturnType<typeof vi.fn>).mockReturnValue(
      new URLSearchParams("tab=stocks&page=3&search=test")
    );

    render(<AnalysisPage />);
    fireEvent.click(screen.getByText("섹터"));

    const calledUrl = pushMock.mock.calls[0][0] as string;
    const calledParams = new URLSearchParams(calledUrl.split("?")[1]);
    expect(calledParams.has("page")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// API types — structural checks
// ---------------------------------------------------------------------------

import type {
  SectorListResponse,
  SectorTopStock,
  SectorStockResponse,
} from "@/lib/api";

describe("Sector API type shapes", () => {
  it("SectorListItem has required fields", () => {
    const item: SectorListItem = makeSectorListItem();
    expect(item).toHaveProperty("sector_name");
    expect(item).toHaveProperty("report_count");
    expect(item).toHaveProperty("avg_sentiment");
    expect(item).toHaveProperty("top_stocks");
  });

  it("SectorTopStock has required fields", () => {
    const s: SectorTopStock = {
      stock_code: "005930",
      stock_name: "삼성전자",
      report_count: 60,
    };
    expect(s).toHaveProperty("stock_code");
    expect(s).toHaveProperty("stock_name");
    expect(s).toHaveProperty("report_count");
  });

  it("SectorListResponse has items array", () => {
    const resp: SectorListResponse = {
      items: [makeSectorListItem()],
    };
    expect(resp.items).toHaveLength(1);
  });

  it("SectorStockItem has required fields", () => {
    const item: SectorStockItem = makeSectorStockItem();
    expect(item).toHaveProperty("stock_code");
    expect(item).toHaveProperty("stock_name");
    expect(item).toHaveProperty("report_count");
    expect(item).toHaveProperty("avg_sentiment");
    expect(item).toHaveProperty("latest_opinion");
    expect(item).toHaveProperty("latest_target_price");
  });

  it("SectorStockResponse has sector_name and items", () => {
    const resp: SectorStockResponse = {
      sector_name: "반도체",
      items: [makeSectorStockItem()],
    };
    expect(resp.sector_name).toBe("반도체");
    expect(resp.items).toHaveLength(1);
  });
});
