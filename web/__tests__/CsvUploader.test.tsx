import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock next/link
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

// Use vi.hoisted so mockUpload can be referenced inside vi.mock factory
const { mockUpload } = vi.hoisted(() => ({ mockUpload: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      trades: {
        list: vi.fn(),
        updateReason: vi.fn(),
        updateReview: vi.fn(),
        upload: mockUpload,
      },
    },
  };
});

import CsvUploader from "@/components/trades/CsvUploader";
import { TradeBase } from "@/lib/api";

function makeTradeBase(overrides: Partial<TradeBase> = {}): TradeBase {
  return {
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
    ...overrides,
  };
}

function makeCsvFile(name = "trades.csv"): File {
  const blob = new Blob(["col1,col2\nval1,val2\n"], { type: "text/csv" });
  return new File([blob], name, { type: "text/csv" });
}

describe("CsvUploader", () => {
  beforeEach(() => {
    mockUpload.mockClear();
  });

  describe("initial state", () => {
    it("renders the drop zone", () => {
      render(<CsvUploader />);
      expect(
        screen.getByRole("button", { name: /CSV 파일 업로드 영역/ })
      ).toBeInTheDocument();
    });

    it("renders upload icon area with instruction text", () => {
      render(<CsvUploader />);
      expect(
        screen.getByText(/CSV 파일을 드래그하거나 클릭하세요/)
      ).toBeInTheDocument();
    });

    it("renders broker selector", () => {
      render(<CsvUploader />);
      expect(screen.getByRole("combobox", { name: /브로커 선택/ })).toBeInTheDocument();
    });

    it("broker selector has auto-detect option", () => {
      render(<CsvUploader />);
      expect(screen.getByText("자동 감지")).toBeInTheDocument();
    });

    it("broker selector has broker options", () => {
      render(<CsvUploader />);
      expect(screen.getByText("미래에셋")).toBeInTheDocument();
      expect(screen.getByText("삼성증권")).toBeInTheDocument();
      expect(screen.getByText("키움증권")).toBeInTheDocument();
    });

    it("hidden file input exists with csv accept", () => {
      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      expect(input).toBeInTheDocument();
      expect(input.accept).toBe(".csv");
    });
  });

  describe("file selection via input", () => {
    it("calls upload with dry_run=true when file is selected", async () => {
      mockUpload.mockResolvedValueOnce({ inserted: 0, skipped: 0, preview: [] });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const file = makeCsvFile();

      fireEvent.change(input, { target: { files: [file] } });

      await waitFor(() => {
        expect(mockUpload).toHaveBeenCalledWith(file, undefined, true);
      });
    });

    it("shows file name after selection", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase()],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const file = makeCsvFile("my-trades.csv");

      fireEvent.change(input, { target: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText("my-trades.csv")).toBeInTheDocument();
      });
    });

    it("rejects non-csv files with error message", async () => {
      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const file = new File(["data"], "trades.xlsx", {
        type: "application/vnd.ms-excel",
      });

      fireEvent.change(input, { target: { files: [file] } });

      expect(
        screen.getByText(/CSV 파일만 업로드할 수 있습니다/)
      ).toBeInTheDocument();
      expect(mockUpload).not.toHaveBeenCalled();
    });

    it("passes selected broker to upload", async () => {
      mockUpload.mockResolvedValueOnce({ inserted: 0, skipped: 0, preview: [] });

      render(<CsvUploader />);
      const select = screen.getByRole("combobox", { name: /브로커 선택/ });
      fireEvent.change(select, { target: { value: "kiwoom" } });

      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const file = makeCsvFile();
      fireEvent.change(input, { target: { files: [file] } });

      await waitFor(() => {
        expect(mockUpload).toHaveBeenCalledWith(file, "kiwoom", true);
      });
    });

    it("passes undefined for broker when auto-detect is selected", async () => {
      mockUpload.mockResolvedValueOnce({ inserted: 0, skipped: 0, preview: [] });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const file = makeCsvFile();
      fireEvent.change(input, { target: { files: [file] } });

      await waitFor(() => {
        expect(mockUpload).toHaveBeenCalledWith(file, undefined, true);
      });
    });
  });

  describe("drag and drop", () => {
    it("changes border color on drag over", () => {
      render(<CsvUploader />);
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });

      fireEvent.dragOver(dropZone);

      // After dragOver, the drop zone should indicate active drag
      expect(dropZone.className).toContain("border-blue-400");
    });

    it("reverts border on drag leave", () => {
      render(<CsvUploader />);
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });

      fireEvent.dragOver(dropZone);
      fireEvent.dragLeave(dropZone);

      expect(dropZone.className).not.toContain("border-blue-400");
    });

    it("handles file drop and triggers preview", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase()],
      });

      render(<CsvUploader />);
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });
      const file = makeCsvFile("drop-test.csv");

      fireEvent.drop(dropZone, {
        dataTransfer: { files: [file] },
      });

      await waitFor(() => {
        expect(mockUpload).toHaveBeenCalledWith(file, undefined, true);
      });
    });
  });

  describe("preview table", () => {
    it("shows preview table after successful dry run", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase()],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByText(/파싱 결과 미리보기/)).toBeInTheDocument();
      });
    });

    it("shows parsed row count in preview header", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase(), makeTradeBase({ symbol: "000660", name: "SK하이닉스" })],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByText(/파싱 결과 미리보기/)).toBeInTheDocument();
      });
      // preview header contains the row count
      const header = screen.getByText(/파싱 결과 미리보기/);
      expect(header.textContent).toContain("2건");
    });

    it("shows stock name in preview row", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase({ name: "삼성전자" })],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByText("삼성전자")).toBeInTheDocument();
      });
    });

    it("shows buy badge in preview", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase({ side: "buy" })],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        const badge = screen.getByText("매수");
        expect(badge).toHaveClass("bg-green-100");
      });
    });

    it("shows sell badge in preview", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase({ side: "sell" })],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        const badge = screen.getByText("매도");
        expect(badge).toHaveClass("bg-red-100");
      });
    });

    it("shows amount formatted in Korean locale", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase({ amount: 710000 })],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByText("710,000원")).toBeInTheDocument();
      });
    });

    it("shows date in YYYY-MM-DD format", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase({ traded_at: "2024-03-15T09:30:00" })],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByText("2024-03-15")).toBeInTheDocument();
      });
    });

    it("shows save button after preview", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase()],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument();
      });
    });
  });

  describe("save (actual upload)", () => {
    it("calls upload with dry_run=false on save", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockResolvedValueOnce({ inserted: 3, skipped: 1, preview: null });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const file = makeCsvFile();
      fireEvent.change(input, { target: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        expect(mockUpload).toHaveBeenCalledWith(file, undefined, false);
      });
    });

    it("shows success result message after upload", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockResolvedValueOnce({ inserted: 5, skipped: 2, preview: null });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        expect(screen.getByText(/5건 저장/)).toBeInTheDocument();
        expect(screen.getByText(/2건 중복 스킵/)).toBeInTheDocument();
      });
    });

    it("shows link to /trades after successful upload", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockResolvedValueOnce({ inserted: 1, skipped: 0, preview: null });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        const link = screen.getByRole("link", { name: /체결 목록 보기/ });
        expect(link).toHaveAttribute("href", "/trades");
      });
    });

    it("shows error message when upload fails", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockRejectedValueOnce(new Error("서버 오류 발생"));

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        expect(screen.getByText("서버 오류 발생")).toBeInTheDocument();
      });
    });

    it("does not show 체결 목록 보기 link when upload fails", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockRejectedValueOnce(new Error("서버 에러"));

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        expect(screen.getByText("서버 에러")).toBeInTheDocument();
      });
      expect(screen.queryByRole("link", { name: /체결 목록 보기/ })).not.toBeInTheDocument();
    });

    it("shows retry button when upload fails", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockRejectedValueOnce(new Error("네트워크 오류"));

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        expect(screen.getByText("다시 시도")).toBeInTheDocument();
      });
    });
  });

  describe("error handling", () => {
    it("shows error message when preview fails", async () => {
      mockUpload.mockRejectedValueOnce(new Error("브로커를 자동으로 감지하지 못했습니다"));

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(
          screen.getByText(/브로커를 자동으로 감지하지 못했습니다/)
        ).toBeInTheDocument();
      });
    });

    it("shows error alert role on preview failure", async () => {
      mockUpload.mockRejectedValueOnce(new Error("파싱 오류"));

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByRole("alert")).toBeInTheDocument();
      });
    });
  });

  describe("cancel / reset", () => {
    it("shows cancel button in preview_ready state", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase()],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
      });
    });

    it("resets to idle after clicking 다시 시도 in error phase", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockRejectedValueOnce(new Error("오류"));

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        expect(screen.getByText("다시 시도")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText("다시 시도"));
      expect(
        screen.getByText(/CSV 파일을 드래그하거나 클릭하세요/)
      ).toBeInTheDocument();
    });

    it("resets to idle on cancel", async () => {
      mockUpload.mockResolvedValueOnce({
        inserted: 0,
        skipped: 0,
        preview: [makeTradeBase()],
      });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument()
      );

      fireEvent.click(screen.getByRole("button", { name: "취소" }));

      expect(
        screen.getByText(/CSV 파일을 드래그하거나 클릭하세요/)
      ).toBeInTheDocument();
    });

    it("shows re-upload button after done", async () => {
      mockUpload
        .mockResolvedValueOnce({
          inserted: 0,
          skipped: 0,
          preview: [makeTradeBase()],
        })
        .mockResolvedValueOnce({ inserted: 1, skipped: 0, preview: null });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /저장/ })).toBeInTheDocument()
      );
      fireEvent.click(screen.getByRole("button", { name: /저장/ }));

      await waitFor(() => {
        expect(screen.getByText("다시 업로드")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText("다시 업로드"));
      expect(
        screen.getByText(/CSV 파일을 드래그하거나 클릭하세요/)
      ).toBeInTheDocument();
    });
  });

  describe("empty preview", () => {
    it("shows 파싱된 거래 내역이 없습니다 when preview is empty array", async () => {
      mockUpload.mockResolvedValueOnce({ inserted: 0, skipped: 0, preview: [] });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(
          screen.getByText(/파싱된 거래 내역이 없습니다/)
        ).toBeInTheDocument();
      });
    });

    it("does not show preview table when preview is empty array", async () => {
      mockUpload.mockResolvedValueOnce({ inserted: 0, skipped: 0, preview: [] });

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      await waitFor(() => {
        expect(screen.queryByText(/파싱 결과 미리보기/)).not.toBeInTheDocument();
      });
    });
  });

  describe("drop zone disabled when busy", () => {
    it("drop zone has pointer-events-none when previewing", async () => {
      // Make upload hang so we stay in previewing phase
      let resolveUpload!: (val: unknown) => void;
      mockUpload.mockReturnValueOnce(
        new Promise((resolve) => {
          resolveUpload = resolve;
        })
      );

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      // Should now be in previewing phase
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });
      expect(dropZone.className).toContain("pointer-events-none");
      expect(dropZone.className).toContain("opacity-50");

      // Clean up
      resolveUpload({ inserted: 0, skipped: 0, preview: [] });
    });

    it("file input is disabled when previewing", async () => {
      let resolveUpload!: (val: unknown) => void;
      mockUpload.mockReturnValueOnce(
        new Promise((resolve) => {
          resolveUpload = resolve;
        })
      );

      render(<CsvUploader />);
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeCsvFile()] } });

      expect(input).toBeDisabled();

      resolveUpload({ inserted: 0, skipped: 0, preview: [] });
    });

    it("drop zone is not disabled in idle phase", () => {
      render(<CsvUploader />);
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });
      expect(dropZone.className).not.toContain("pointer-events-none");
    });
  });

  describe("keyboard activation of drop zone", () => {
    it("pressing Enter on drop zone triggers file input click", () => {
      render(<CsvUploader />);
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const clickSpy = vi.spyOn(input, "click");

      fireEvent.keyDown(dropZone, { key: "Enter" });

      expect(clickSpy).toHaveBeenCalled();
    });

    it("pressing Space on drop zone triggers file input click", () => {
      render(<CsvUploader />);
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const clickSpy = vi.spyOn(input, "click");

      fireEvent.keyDown(dropZone, { key: " " });

      expect(clickSpy).toHaveBeenCalled();
    });

    it("pressing other keys does not trigger file input click", () => {
      render(<CsvUploader />);
      const dropZone = screen.getByRole("button", { name: /CSV 파일 업로드 영역/ });
      const input = screen.getByLabelText("CSV 파일 선택") as HTMLInputElement;
      const clickSpy = vi.spyOn(input, "click");

      fireEvent.keyDown(dropZone, { key: "Tab" });

      expect(clickSpy).not.toHaveBeenCalled();
    });
  });
});

