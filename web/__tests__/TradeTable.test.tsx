import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import TradeTable from "@/components/trades/TradeTable";
import { TradeResponse } from "@/lib/api";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

// Mock api module
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      trades: {
        updateReason: vi.fn(),
        updateReview: vi.fn(),
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

describe("TradeTable", () => {
  describe("empty state", () => {
    it("shows empty message when no trades", () => {
      render(<TradeTable trades={[]} />);
      expect(screen.getByText(/매매 내역이 없습니다/)).toBeInTheDocument();
      expect(screen.getByText(/CSV를 업로드하세요/)).toBeInTheDocument();
    });

    it("shows upload link in empty state", () => {
      render(<TradeTable trades={[]} />);
      const link = screen.getByRole("link", { name: /업로드 페이지/ });
      expect(link).toHaveAttribute("href", "/trades/upload");
    });
  });

  describe("trade rendering", () => {
    it("renders trade row with stock name and symbol", () => {
      render(<TradeTable trades={[makeTrade()]} />);
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
      expect(screen.getByText("005930")).toBeInTheDocument();
    });

    it("renders buy side with green badge", () => {
      render(<TradeTable trades={[makeTrade({ side: "buy" })]} />);
      const badge = screen.getByText("매수");
      expect(badge).toHaveClass("bg-green-100");
      expect(badge).toHaveClass("text-green-700");
    });

    it("renders sell side with red badge", () => {
      render(<TradeTable trades={[makeTrade({ side: "sell" })]} />);
      const badge = screen.getByText("매도");
      expect(badge).toHaveClass("bg-red-100");
      expect(badge).toHaveClass("text-red-700");
    });

    it("formats amount in Korean locale", () => {
      render(<TradeTable trades={[makeTrade({ amount: 710000 })]} />);
      expect(screen.getByText("710,000원")).toBeInTheDocument();
    });

    it("formats price in Korean locale", () => {
      render(<TradeTable trades={[makeTrade({ price: 71000 })]} />);
      expect(screen.getByText("71,000원")).toBeInTheDocument();
    });

    it("formats fees in Korean locale", () => {
      render(<TradeTable trades={[makeTrade({ fees: 710 })]} />);
      expect(screen.getByText("710원")).toBeInTheDocument();
    });

    it("shows dash for null fees", () => {
      render(<TradeTable trades={[makeTrade({ fees: null })]} />);
      expect(screen.getAllByText("-").length).toBeGreaterThan(0);
    });

    it("shows date in YYYY-MM-DD format", () => {
      render(<TradeTable trades={[makeTrade({ traded_at: "2024-03-15T09:30:00" })]} />);
      expect(screen.getByText("2024-03-15")).toBeInTheDocument();
    });

    it("shows broker name", () => {
      render(<TradeTable trades={[makeTrade({ broker: "키움증권" })]} />);
      expect(screen.getByText("키움증권")).toBeInTheDocument();
    });

    it("shows dash for null broker", () => {
      render(<TradeTable trades={[makeTrade({ broker: null })]} />);
      expect(screen.getAllByText("-").length).toBeGreaterThan(0);
    });

    it("renders multiple trades", () => {
      const trades = [
        makeTrade({ id: 1, symbol: "005930", name: "삼성전자" }),
        makeTrade({ id: 2, symbol: "000660", name: "SK하이닉스", side: "sell" }),
      ];
      render(<TradeTable trades={trades} />);
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
      expect(screen.getByText("SK하이닉스")).toBeInTheDocument();
    });

    it("uses symbol as display when name is null", () => {
      render(<TradeTable trades={[makeTrade({ name: null, symbol: "005930" })]} />);
      expect(screen.getByText("005930")).toBeInTheDocument();
    });
  });

  describe("table headers", () => {
    it("renders all column headers", () => {
      render(<TradeTable trades={[makeTrade()]} />);
      expect(screen.getByText("날짜")).toBeInTheDocument();
      expect(screen.getByText("종목")).toBeInTheDocument();
      expect(screen.getByText("구분")).toBeInTheDocument();
      expect(screen.getByText("수량")).toBeInTheDocument();
      expect(screen.getByText("단가")).toBeInTheDocument();
      expect(screen.getByText("금액")).toBeInTheDocument();
      expect(screen.getByText("브로커")).toBeInTheDocument();
      expect(screen.getByText("수수료")).toBeInTheDocument();
      expect(screen.getByText("매매이유")).toBeInTheDocument();
      expect(screen.getByText("복기")).toBeInTheDocument();
    });
  });

  describe("inline editing - reason", () => {
    let updateReasonMock: ReturnType<typeof vi.fn>;

    beforeEach(async () => {
      const { api } = await import("@/lib/api");
      updateReasonMock = api.trades.updateReason as ReturnType<typeof vi.fn>;
      updateReasonMock.mockClear();
      updateReasonMock.mockResolvedValue(makeTrade({ reason: "가격 급락 반등 기대" }));
    });

    it("shows placeholder text when reason is null", () => {
      render(<TradeTable trades={[makeTrade({ reason: null })]} />);
      expect(screen.getByText("이유 입력...")).toBeInTheDocument();
    });

    it("shows existing reason text", () => {
      render(<TradeTable trades={[makeTrade({ reason: "기존 이유" })]} />);
      expect(screen.getByText("기존 이유")).toBeInTheDocument();
    });

    it("switches to input on click", async () => {
      render(<TradeTable trades={[makeTrade({ reason: null })]} />);
      const placeholder = screen.getByText("이유 입력...");
      fireEvent.click(placeholder);
      const input = screen.getByDisplayValue("");
      expect(input).toBeInTheDocument();
    });

    it("calls updateReason on blur", async () => {
      render(<TradeTable trades={[makeTrade({ id: 42, reason: null })]} />);
      fireEvent.click(screen.getByText("이유 입력..."));
      const input = screen.getByDisplayValue("");
      fireEvent.change(input, { target: { value: "새 이유" } });
      fireEvent.blur(input);
      await waitFor(() => {
        expect(updateReasonMock).toHaveBeenCalledWith(42, "새 이유");
      });
    });

    it("calls updateReason on Enter key", async () => {
      render(<TradeTable trades={[makeTrade({ id: 42, reason: null })]} />);
      fireEvent.click(screen.getByText("이유 입력..."));
      const input = screen.getByDisplayValue("");
      fireEvent.change(input, { target: { value: "Enter 이유" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() => {
        expect(updateReasonMock).toHaveBeenCalledWith(42, "Enter 이유");
      });
    });

    it("closes input on Escape without saving", async () => {
      render(<TradeTable trades={[makeTrade({ id: 42, reason: null })]} />);
      fireEvent.click(screen.getByText("이유 입력..."));
      const input = screen.getByDisplayValue("");
      fireEvent.change(input, { target: { value: "입력 중" } });
      fireEvent.keyDown(input, { key: "Escape" });
      // Input should be gone; placeholder restored
      expect(screen.queryByDisplayValue("입력 중")).not.toBeInTheDocument();
      expect(updateReasonMock).not.toHaveBeenCalled();
    });

    it("Escape resets draft so clicking again shows original value", async () => {
      render(<TradeTable trades={[makeTrade({ id: 42, reason: "원래 이유" })]} />);
      // Click to open editor
      fireEvent.click(screen.getByText("원래 이유"));
      const input = screen.getByDisplayValue("원래 이유");
      // Type something new and press Escape
      fireEvent.change(input, { target: { value: "버릴 내용" } });
      fireEvent.keyDown(input, { key: "Escape" });
      // Original span should show the original value
      expect(screen.getByText("원래 이유")).toBeInTheDocument();
      // Click again — input should open with the original value, not the abandoned draft
      fireEvent.click(screen.getByText("원래 이유"));
      expect(screen.getByDisplayValue("원래 이유")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("버릴 내용")).not.toBeInTheDocument();
      expect(updateReasonMock).not.toHaveBeenCalled();
    });

    it("does not call updateReason when text is unchanged", async () => {
      render(<TradeTable trades={[makeTrade({ id: 42, reason: "기존 이유" })]} />);
      fireEvent.click(screen.getByText("기존 이유"));
      const input = screen.getByDisplayValue("기존 이유");
      fireEvent.blur(input);
      await waitFor(() => {
        expect(updateReasonMock).not.toHaveBeenCalled();
      });
    });
  });

  describe("inline editing - review", () => {
    let updateReviewMock: ReturnType<typeof vi.fn>;

    beforeEach(async () => {
      const { api } = await import("@/lib/api");
      updateReviewMock = api.trades.updateReview as ReturnType<typeof vi.fn>;
      updateReviewMock.mockClear();
      updateReviewMock.mockResolvedValue(makeTrade({ review: "복기 내용" }));
    });

    it("shows placeholder text when review is null", () => {
      render(<TradeTable trades={[makeTrade({ review: null })]} />);
      expect(screen.getByText("복기 입력...")).toBeInTheDocument();
    });

    it("calls updateReview on blur", async () => {
      render(<TradeTable trades={[makeTrade({ id: 7, review: null })]} />);
      const reviewPlaceholder = screen.getByText("복기 입력...");
      fireEvent.click(reviewPlaceholder);
      const input = screen.getByDisplayValue("");
      fireEvent.change(input, { target: { value: "복기 작성" } });
      fireEvent.blur(input);
      await waitFor(() => {
        expect(updateReviewMock).toHaveBeenCalledWith(7, "복기 작성");
      });
    });
  });
});
