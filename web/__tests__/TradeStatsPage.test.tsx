import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TradeStatsResponse } from "@/lib/api";

// Mock api module
const mockStats = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      trades: {
        stats: mockStats,
      },
    },
  };
});

const makeStats = (overrides: Partial<TradeStatsResponse> = {}): TradeStatsResponse => ({
  total_count: 10,
  buy_count: 6,
  sell_count: 4,
  total_amount: "5000000",
  symbol_frequency: [
    { symbol: "005930", count: 5 },
    { symbol: "035420", count: 3 },
    { symbol: "000660", count: 2 },
  ],
  ...overrides,
});

let TradeStatsPage: React.ComponentType;

beforeEach(async () => {
  vi.resetModules();
  mockStats.mockReset();
  const mod = await import("@/app/trades/stats/page");
  TradeStatsPage = mod.default;
});

describe("TradeStatsPage", () => {
  describe("loading state", () => {
    it("shows loading text initially", async () => {
      let resolve!: (v: unknown) => void;
      mockStats.mockReturnValue(new Promise((r) => { resolve = r; }));

      render(<TradeStatsPage />);
      expect(screen.getByText("불러오는 중...")).toBeInTheDocument();

      resolve(makeStats());
    });
  });

  describe("page title", () => {
    it("renders 매매 통계 heading", async () => {
      mockStats.mockResolvedValue(makeStats());

      render(<TradeStatsPage />);
      expect(screen.getByText("매매 통계")).toBeInTheDocument();
    });
  });

  describe("stat cards", () => {
    it("shows all four stat card labels after loading", async () => {
      mockStats.mockResolvedValue(makeStats());

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("총 거래수")).toBeInTheDocument());

      expect(screen.getByText("매수 건수")).toBeInTheDocument();
      expect(screen.getByText("매도 건수")).toBeInTheDocument();
      expect(screen.getByText("총 거래금액")).toBeInTheDocument();
    });

    it("displays correct total_count value", async () => {
      mockStats.mockResolvedValue(makeStats({ total_count: 42 }));

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("총 거래수")).toBeInTheDocument());

      expect(screen.getByText("42")).toBeInTheDocument();
    });

    it("displays correct buy_count value", async () => {
      mockStats.mockResolvedValue(makeStats({ buy_count: 25 }));

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("매수 건수")).toBeInTheDocument());

      expect(screen.getByText("25")).toBeInTheDocument();
    });

    it("displays correct sell_count value", async () => {
      mockStats.mockResolvedValue(makeStats({ sell_count: 17 }));

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("매도 건수")).toBeInTheDocument());

      expect(screen.getByText("17")).toBeInTheDocument();
    });

    it("displays total_amount formatted with Korean Won sign", async () => {
      mockStats.mockResolvedValue(makeStats({ total_amount: "5000000" }));

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("총 거래금액")).toBeInTheDocument());

      // Should contain ₩ and the formatted number
      const amountEl = screen.getByText(/₩/);
      expect(amountEl).toBeInTheDocument();
      expect(amountEl.textContent).toContain("5,000,000");
    });
  });

  describe("symbol frequency table", () => {
    it("shows 종목별 거래 빈도 section heading", async () => {
      mockStats.mockResolvedValue(makeStats());

      render(<TradeStatsPage />);
      await waitFor(() =>
        expect(screen.getByText("종목별 거래 빈도")).toBeInTheDocument()
      );
    });

    it("renders all symbols in the table", async () => {
      mockStats.mockResolvedValue(
        makeStats({
          symbol_frequency: [
            { symbol: "005930", count: 5 },
            { symbol: "035420", count: 3 },
          ],
        })
      );

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("005930")).toBeInTheDocument());

      expect(screen.getByText("035420")).toBeInTheDocument();
    });

    it("renders trade counts for each symbol", async () => {
      mockStats.mockResolvedValue(
        makeStats({
          symbol_frequency: [{ symbol: "005930", count: 7 }],
        })
      );

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("005930")).toBeInTheDocument());

      expect(screen.getByText("7")).toBeInTheDocument();
    });

    it("shows rank numbers starting from 1", async () => {
      mockStats.mockResolvedValue(
        makeStats({
          symbol_frequency: [
            { symbol: "A001", count: 3 },
            { symbol: "B002", count: 2 },
          ],
        })
      );

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("A001")).toBeInTheDocument());

      // Rank "1" appears once; "2" appears as both rank and count for B002
      expect(screen.getByText("1")).toBeInTheDocument();
      expect(screen.getAllByText("2").length).toBeGreaterThanOrEqual(1);
    });

    it("shows empty state message when symbol_frequency is empty", async () => {
      mockStats.mockResolvedValue(makeStats({ symbol_frequency: [] }));

      render(<TradeStatsPage />);
      await waitFor(() =>
        expect(screen.getByText("거래 내역이 없습니다.")).toBeInTheDocument()
      );
    });
  });

  describe("phase 2 placeholder", () => {
    it("shows the performance analysis placeholder note", async () => {
      mockStats.mockResolvedValue(makeStats());

      render(<TradeStatsPage />);
      await waitFor(() =>
        expect(
          screen.getByText(/승률, 평균수익률 등 성과 분석은 매수-매도 매칭 후 추가 예정/)
        ).toBeInTheDocument()
      );
    });
  });

  describe("error state", () => {
    it("shows error message when API call fails", async () => {
      mockStats.mockRejectedValue(new Error("Network error"));

      render(<TradeStatsPage />);
      await waitFor(() =>
        expect(
          screen.getByText("데이터를 불러오는데 실패했습니다. 다시 시도해주세요.")
        ).toBeInTheDocument()
      );
    });

    it("shows retry button when API call fails", async () => {
      mockStats.mockRejectedValue(new Error("Network error"));

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("다시 시도")).toBeInTheDocument());

      expect(screen.getByText("다시 시도").tagName).toBe("BUTTON");
    });

    it("does not show stat cards in error state", async () => {
      mockStats.mockRejectedValue(new Error("Network error"));

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("다시 시도")).toBeInTheDocument());

      expect(screen.queryByText("총 거래수")).not.toBeInTheDocument();
    });

    it("does not show loading indicator after error", async () => {
      mockStats.mockRejectedValue(new Error("Network error"));

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("다시 시도")).toBeInTheDocument());

      expect(screen.queryByText("불러오는 중...")).not.toBeInTheDocument();
    });
  });

  describe("retry after error", () => {
    it("refetches data when retry button is clicked", async () => {
      mockStats
        .mockRejectedValueOnce(new Error("Network error"))
        .mockResolvedValueOnce(makeStats());

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("다시 시도")).toBeInTheDocument());

      fireEvent.click(screen.getByText("다시 시도"));

      await waitFor(() => expect(screen.getByText("총 거래수")).toBeInTheDocument());
      expect(mockStats).toHaveBeenCalledTimes(2);
    });

    it("clears error message after successful retry", async () => {
      mockStats
        .mockRejectedValueOnce(new Error("Network error"))
        .mockResolvedValueOnce(makeStats());

      render(<TradeStatsPage />);
      await waitFor(() => expect(screen.getByText("다시 시도")).toBeInTheDocument());

      fireEvent.click(screen.getByText("다시 시도"));

      await waitFor(() =>
        expect(
          screen.queryByText("데이터를 불러오는데 실패했습니다. 다시 시도해주세요.")
        ).not.toBeInTheDocument()
      );
    });
  });

  describe("API call", () => {
    it("calls api.trades.stats on mount", async () => {
      mockStats.mockResolvedValue(makeStats());

      render(<TradeStatsPage />);
      await waitFor(() => expect(mockStats).toHaveBeenCalledOnce());
    });
  });
});

describe("api.trades.stats type", () => {
  it("api object exposes trades.stats as a function", async () => {
    // Verify the api shape: trades.stats must be a callable function
    const { api } = await import("@/lib/api");
    expect(typeof api.trades.stats).toBe("function");
  });
});
