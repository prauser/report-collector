import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TradeResponse } from "@/lib/api";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

// Mock api module
const mockList = vi.fn();
const mockUpdateReason = vi.fn();
const mockUpdateReview = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      trades: {
        list: mockList,
        updateReason: mockUpdateReason,
        updateReview: mockUpdateReview,
      },
    },
  };
});

const makeTrade = (overrides: Partial<TradeResponse> = {}): TradeResponse => ({
  id: 1,
  symbol: "005930",
  name: "삼성전자",
  side: "buy",
  traded_at: "2024-03-15T09:30:00",
  price: 71000,
  quantity: 10,
  amount: 710000,
  broker: "키움증권",
  account_type: "general",
  market: "KOSPI",
  fees: 710,
  reason: null,
  review: null,
  created_at: "2024-03-15T09:30:05",
  ...overrides,
});

function makeListResponse(items: TradeResponse[]) {
  return { items, total: items.length, limit: 500, offset: 0 };
}

// Import component after mocks
let ReviewPage: React.ComponentType;

beforeEach(async () => {
  vi.resetModules();
  mockList.mockReset();
  mockUpdateReason.mockReset();
  mockUpdateReview.mockReset();
  // Re-import to pick up fresh mocks
  const mod = await import("@/app/trades/review/page");
  ReviewPage = mod.default;
});

describe("ReviewPage", () => {
  describe("loading state", () => {
    it("shows loading text initially", async () => {
      // Keep the promise pending so we see loading state
      let resolve!: (v: unknown) => void;
      mockList.mockReturnValue(new Promise((r) => { resolve = r; }));

      render(<ReviewPage />);
      expect(screen.getByText("불러오는 중...")).toBeInTheDocument();

      // Cleanup: resolve so no memory leaks
      resolve(makeListResponse([]));
    });
  });

  describe("stats cards", () => {
    it("shows stat cards after loading", async () => {
      const trades = [
        makeTrade({ id: 1, reason: "이유 있음", review: null }),
        makeTrade({ id: 2, reason: null, review: "복기 있음" }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("총 거래")).toBeInTheDocument());

      expect(screen.getByText("매매이유 작성률")).toBeInTheDocument();
      expect(screen.getByText("복기 작성률")).toBeInTheDocument();
      // The header shows total count
      expect(screen.getByText("총 2건")).toBeInTheDocument();
    });

    it("shows 50% reason completion when half written", async () => {
      const trades = [
        makeTrade({ id: 1, reason: "작성됨", review: null }),
        makeTrade({ id: 2, reason: null, review: null }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("매매이유 작성률")).toBeInTheDocument());

      // The value "50%" should be displayed
      expect(screen.getByText("50%")).toBeInTheDocument();
    });

    it("shows 100% when all reasons written", async () => {
      const trades = [
        makeTrade({ id: 1, reason: "이유1", review: "복기1" }),
        makeTrade({ id: 2, reason: "이유2", review: "복기2" }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("복기 작성률")).toBeInTheDocument());

      const pcts = screen.getAllByText("100%");
      expect(pcts.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe("all-done state", () => {
    it("shows celebratory message when all trades complete", async () => {
      const trades = [
        makeTrade({ id: 1, reason: "이유", review: "복기" }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(
          screen.getByText(/모든 거래에 매매이유와 복기가 작성되었습니다/)
        ).toBeInTheDocument()
      );
    });

    it("does not show filter tabs when all done", async () => {
      const trades = [makeTrade({ id: 1, reason: "이유", review: "복기" })];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText(/모든 거래에/)).toBeInTheDocument()
      );

      expect(screen.queryByText("매매이유 미작성")).not.toBeInTheDocument();
      expect(screen.queryByText("복기 미작성")).not.toBeInTheDocument();
      expect(screen.queryByText("전체 미작성")).not.toBeInTheDocument();
    });
  });

  describe("filter tabs", () => {
    it("shows all three filter tabs when trades are incomplete", async () => {
      const trades = [makeTrade({ id: 1, reason: null, review: null })];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("매매이유 미작성")).toBeInTheDocument());

      expect(screen.getByText("복기 미작성")).toBeInTheDocument();
      expect(screen.getByText("전체 미작성")).toBeInTheDocument();
    });

    it("defaults to 전체 미작성 tab", async () => {
      const trades = [makeTrade({ id: 1, reason: null, review: null })];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("전체 미작성")).toBeInTheDocument());

      const allTab = screen.getByText("전체 미작성").closest("button");
      expect(allTab).toHaveClass("border-blue-500");
    });

    it("switches to 매매이유 미작성 tab on click", async () => {
      const trades = [
        makeTrade({ id: 1, reason: null, review: "복기있음" }),
        makeTrade({ id: 2, reason: "이유있음", review: null }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("매매이유 미작성")).toBeInTheDocument());

      fireEvent.click(screen.getByText("매매이유 미작성"));

      const tab = screen.getByText("매매이유 미작성").closest("button");
      expect(tab).toHaveClass("border-blue-500");
    });

    it("shows count badge on tabs", async () => {
      const trades = [
        makeTrade({ id: 1, reason: null, review: null }),
        makeTrade({ id: 2, reason: "있음", review: null }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("매매이유 미작성")).toBeInTheDocument());

      // "전체 미작성" should show count 2 (both have at least one missing)
      // "복기 미작성" should show count 2 (both missing review)
      // "매매이유 미작성" should show count 1 (only id=1 missing reason)
      const badges = screen.getAllByText("2");
      expect(badges.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe("filter logic", () => {
    it("매매이유 미작성 tab shows only trades with missing reason", async () => {
      const trades = [
        makeTrade({ id: 1, symbol: "A001", name: "알파", reason: null, review: "있음" }),
        makeTrade({ id: 2, symbol: "B002", name: "베타", reason: "있음", review: null }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("매매이유 미작성")).toBeInTheDocument());

      fireEvent.click(screen.getByText("매매이유 미작성"));

      await waitFor(() => expect(screen.getByText("알파")).toBeInTheDocument());
      expect(screen.queryByText("베타")).not.toBeInTheDocument();
    });

    it("복기 미작성 tab shows only trades with missing review", async () => {
      const trades = [
        makeTrade({ id: 1, symbol: "A001", name: "알파", reason: null, review: "있음" }),
        makeTrade({ id: 2, symbol: "B002", name: "베타", reason: "있음", review: null }),
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("복기 미작성")).toBeInTheDocument());

      fireEvent.click(screen.getByText("복기 미작성"));

      await waitFor(() => expect(screen.getByText("베타")).toBeInTheDocument());
      expect(screen.queryByText("알파")).not.toBeInTheDocument();
    });

    it("전체 미작성 tab shows trades with any missing field", async () => {
      const trades = [
        makeTrade({ id: 1, name: "알파", reason: null, review: "있음" }),
        makeTrade({ id: 2, name: "베타", reason: "있음", review: null }),
        makeTrade({ id: 3, name: "감마", reason: "있음", review: "있음" }), // complete
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("전체 미작성")).toBeInTheDocument());

      // The default tab is "all" so we should see 알파 and 베타 but not 감마
      await waitFor(() => expect(screen.getByText("알파")).toBeInTheDocument());
      expect(screen.getByText("베타")).toBeInTheDocument();
      expect(screen.queryByText("감마")).not.toBeInTheDocument();
    });

    it("shows tab-specific empty state message when filter returns no results", async () => {
      const trades = [
        makeTrade({ id: 1, reason: "있음", review: null }), // reason complete, review missing
      ];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("매매이유 미작성")).toBeInTheDocument());

      fireEvent.click(screen.getByText("매매이유 미작성"));

      await waitFor(() =>
        expect(screen.getByText("이 항목은 모두 작성 완료입니다.")).toBeInTheDocument()
      );
    });
  });

  describe("header", () => {
    it("renders page title", async () => {
      mockList.mockResolvedValue(makeListResponse([]));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("복기 현황")).toBeInTheDocument());
    });

    it("shows total trade count in header", async () => {
      const trades = [makeTrade({ id: 1 }), makeTrade({ id: 2 })];
      mockList.mockResolvedValue(makeListResponse(trades));

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("총 2건")).toBeInTheDocument());
    });
  });

  describe("inline editing via TradeTable", () => {
    it("updates trade in state after reason save", async () => {
      const trade = makeTrade({ id: 10, reason: null, review: null });
      mockList.mockResolvedValue(makeListResponse([trade]));
      mockUpdateReason.mockResolvedValue({ ...trade, reason: "새 이유" });

      render(<ReviewPage />);
      await waitFor(() => expect(screen.getByText("이유 입력...")).toBeInTheDocument());

      fireEvent.click(screen.getByText("이유 입력..."));
      const input = screen.getByDisplayValue("");
      fireEvent.change(input, { target: { value: "새 이유" } });
      fireEvent.blur(input);

      await waitFor(() => expect(mockUpdateReason).toHaveBeenCalledWith(10, "새 이유"));
    });
  });

  describe("API call", () => {
    it("fetches trades with limit 500", async () => {
      mockList.mockResolvedValue(makeListResponse([]));

      render(<ReviewPage />);
      await waitFor(() => expect(mockList).toHaveBeenCalled());

      expect(mockList).toHaveBeenCalledWith(expect.objectContaining({ limit: 500 }));
    });
  });

  describe("zero-trades empty state", () => {
    it("shows 매매 내역이 없습니다 when no trades exist", async () => {
      mockList.mockResolvedValue(makeListResponse([]));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("매매 내역이 없습니다.")).toBeInTheDocument()
      );
    });

    it("shows upload page link when no trades exist", async () => {
      mockList.mockResolvedValue(makeListResponse([]));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("업로드 페이지로 이동")).toBeInTheDocument()
      );

      const link = screen.getByText("업로드 페이지로 이동").closest("a");
      expect(link).toHaveAttribute("href", "/trades/upload");
    });

    it("does not show green all-done message when no trades exist", async () => {
      mockList.mockResolvedValue(makeListResponse([]));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("매매 내역이 없습니다.")).toBeInTheDocument()
      );

      expect(
        screen.queryByText(/모든 거래에 매매이유와 복기가 작성되었습니다/)
      ).not.toBeInTheDocument();
    });

    it("does not show filter tabs when no trades exist", async () => {
      mockList.mockResolvedValue(makeListResponse([]));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("매매 내역이 없습니다.")).toBeInTheDocument()
      );

      expect(screen.queryByText("매매이유 미작성")).not.toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("shows error message when API call fails", async () => {
      mockList.mockRejectedValue(new Error("Network error"));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(
          screen.getByText("데이터를 불러오는데 실패했습니다. 다시 시도해주세요.")
        ).toBeInTheDocument()
      );
    });

    it("shows retry button when API call fails", async () => {
      mockList.mockRejectedValue(new Error("Network error"));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("다시 시도")).toBeInTheDocument()
      );

      const btn = screen.getByText("다시 시도");
      expect(btn.tagName).toBe("BUTTON");
    });

    it("does not show filter tabs when in error state", async () => {
      mockList.mockRejectedValue(new Error("Network error"));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("다시 시도")).toBeInTheDocument()
      );

      expect(screen.queryByText("매매이유 미작성")).not.toBeInTheDocument();
    });
  });

  describe("retry after error", () => {
    it("refetches data when retry button is clicked", async () => {
      const trade = makeTrade({ id: 1, reason: null, review: null });
      // First call fails, second succeeds
      mockList
        .mockRejectedValueOnce(new Error("Network error"))
        .mockResolvedValueOnce(makeListResponse([trade]));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("다시 시도")).toBeInTheDocument()
      );

      fireEvent.click(screen.getByText("다시 시도"));

      await waitFor(() =>
        expect(screen.getByText("매매이유 미작성")).toBeInTheDocument()
      );

      expect(mockList).toHaveBeenCalledTimes(2);
    });

    it("clears error message after successful retry", async () => {
      const trade = makeTrade({ id: 1, reason: null, review: null });
      mockList
        .mockRejectedValueOnce(new Error("Network error"))
        .mockResolvedValueOnce(makeListResponse([trade]));

      render(<ReviewPage />);
      await waitFor(() =>
        expect(screen.getByText("다시 시도")).toBeInTheDocument()
      );

      fireEvent.click(screen.getByText("다시 시도"));

      await waitFor(() =>
        expect(
          screen.queryByText("데이터를 불러오는데 실패했습니다. 다시 시도해주세요.")
        ).not.toBeInTheDocument()
      );
    });
  });
});

describe("StatCard", () => {
  it("renders label and value", async () => {
    const { default: StatCard } = await import("@/components/shared/StatCard");
    render(<StatCard label="테스트 라벨" value={42} />);
    expect(screen.getByText("테스트 라벨")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders string value", async () => {
    const { default: StatCard } = await import("@/components/shared/StatCard");
    render(<StatCard label="비율" value="75%" />);
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("renders sub text when provided", async () => {
    const { default: StatCard } = await import("@/components/shared/StatCard");
    render(<StatCard label="라벨" value={10} sub="추가 설명" />);
    expect(screen.getByText("추가 설명")).toBeInTheDocument();
  });

  it("does not render sub element when sub is omitted", async () => {
    const { default: StatCard } = await import("@/components/shared/StatCard");
    render(<StatCard label="라벨" value={10} />);
    expect(screen.queryByText("추가 설명")).not.toBeInTheDocument();
  });

  it("formats number value with toLocaleString", async () => {
    const { default: StatCard } = await import("@/components/shared/StatCard");
    render(<StatCard label="큰 숫자" value={1000000} />);
    // In jsdom locale, large numbers may or may not use comma separators
    // The important thing is the element exists and contains the number
    const el = screen.getByText("1,000,000");
    expect(el).toBeInTheDocument();
  });
});
