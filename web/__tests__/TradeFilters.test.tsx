import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// Mock next/navigation
const mockPush = vi.fn();
const mockParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => mockParams,
}));

import TradeFilters from "@/components/trades/TradeFilters";

describe("TradeFilters", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("renders symbol search input", () => {
    render(<TradeFilters />);
    expect(screen.getByPlaceholderText("종목명/코드 검색...")).toBeInTheDocument();
  });

  it("renders broker input", () => {
    render(<TradeFilters />);
    expect(screen.getByPlaceholderText("브로커")).toBeInTheDocument();
  });

  it("renders side select with options", () => {
    render(<TradeFilters />);
    expect(screen.getByText("매수/매도 전체")).toBeInTheDocument();
    expect(screen.getByText("매수")).toBeInTheDocument();
    expect(screen.getByText("매도")).toBeInTheDocument();
  });

  it("renders date inputs", () => {
    render(<TradeFilters />);
    const dateInputs = screen.getAllByDisplayValue("");
    // At least 2 date inputs exist
    expect(dateInputs.length).toBeGreaterThanOrEqual(2);
  });

  it("renders search button", () => {
    render(<TradeFilters />);
    expect(screen.getByRole("button", { name: "검색" })).toBeInTheDocument();
  });

  it("does not show reset button initially", () => {
    render(<TradeFilters />);
    expect(screen.queryByText("초기화")).not.toBeInTheDocument();
  });

  it("shows reset button when symbol is typed", () => {
    render(<TradeFilters />);
    const symbolInput = screen.getByPlaceholderText("종목명/코드 검색...");
    fireEvent.change(symbolInput, { target: { value: "삼성" } });
    expect(screen.getByText("초기화")).toBeInTheDocument();
  });

  it("navigates to /trades with symbol param on search", () => {
    render(<TradeFilters />);
    const symbolInput = screen.getByPlaceholderText("종목명/코드 검색...");
    fireEvent.change(symbolInput, { target: { value: "005930" } });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));
    expect(mockPush).toHaveBeenCalledOnce();
    const url = mockPush.mock.calls[0][0] as string;
    expect(url).toContain("/trades");
    expect(url).toContain("symbol=005930");
    expect(url).toContain("page=1");
  });

  it("navigates with side=buy when buy is selected", () => {
    render(<TradeFilters />);
    const sideSelect = screen.getByRole("combobox");
    fireEvent.change(sideSelect, { target: { value: "buy" } });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));
    const url = mockPush.mock.calls[0][0] as string;
    expect(url).toContain("side=buy");
  });

  it("navigates to /trades on reset", () => {
    render(<TradeFilters />);
    const symbolInput = screen.getByPlaceholderText("종목명/코드 검색...");
    fireEvent.change(symbolInput, { target: { value: "삼성" } });
    fireEvent.click(screen.getByText("초기화"));
    expect(mockPush).toHaveBeenCalledWith("/trades");
  });

  it("submits on Enter key in symbol input", () => {
    render(<TradeFilters />);
    const symbolInput = screen.getByPlaceholderText("종목명/코드 검색...");
    fireEvent.change(symbolInput, { target: { value: "삼성" } });
    fireEvent.keyDown(symbolInput, { key: "Enter" });
    expect(mockPush).toHaveBeenCalledOnce();
  });

  it("submits on Enter key in broker input", () => {
    render(<TradeFilters />);
    const brokerInput = screen.getByPlaceholderText("브로커");
    fireEvent.change(brokerInput, { target: { value: "키움" } });
    fireEvent.keyDown(brokerInput, { key: "Enter" });
    expect(mockPush).toHaveBeenCalledOnce();
    const url = mockPush.mock.calls[0][0] as string;
    expect(url).toContain("broker=%ED%82%A4%EC%9B%80");
  });

  it("omits empty params from URL", () => {
    render(<TradeFilters />);
    fireEvent.click(screen.getByRole("button", { name: "검색" }));
    const url = mockPush.mock.calls[0][0] as string;
    // Only page=1 should be in URL when no filters set
    expect(url).toBe("/trades?page=1");
  });

  it("includes date_from and date_to in URL", () => {
    render(<TradeFilters />);
    const dateInputs = document.querySelectorAll('input[type="date"]');
    fireEvent.change(dateInputs[0], { target: { value: "2024-01-01" } });
    fireEvent.change(dateInputs[1], { target: { value: "2024-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "검색" }));
    const url = mockPush.mock.calls[0][0] as string;
    expect(url).toContain("date_from=2024-01-01");
    expect(url).toContain("date_to=2024-12-31");
  });
});
