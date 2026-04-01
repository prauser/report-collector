import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ThinkingProcess from "@/components/agent/ThinkingProcess";
import type { ToolStep } from "@/lib/agent-types";

function makeToolStep(overrides: Partial<ToolStep> = {}): ToolStep {
  return {
    id: "toolu_001",
    name: "search_reports",
    input: { stock_name: "삼성전자" },
    status: "calling",
    ...overrides,
  };
}

describe("ThinkingProcess", () => {
  describe("renders nothing when no content", () => {
    it("returns null when thinking and toolSteps are both empty/undefined", () => {
      const { container } = render(<ThinkingProcess />);
      expect(container.firstChild).toBeNull();
    });

    it("returns null when thinking is empty array", () => {
      const { container } = render(<ThinkingProcess thinking={[]} />);
      expect(container.firstChild).toBeNull();
    });

    it("returns null when toolSteps is empty array", () => {
      const { container } = render(<ThinkingProcess toolSteps={[]} />);
      expect(container.firstChild).toBeNull();
    });
  });

  describe("header label", () => {
    it("shows '분석 과정' when only thinking entries, no toolSteps", () => {
      render(<ThinkingProcess thinking={["생각1", "생각2"]} isStreaming />);
      expect(screen.getByText("분석 과정")).toBeInTheDocument();
    });

    it("shows tool count when toolSteps are present", () => {
      const steps = [makeToolStep({ id: "a" }), makeToolStep({ id: "b" })];
      render(<ThinkingProcess toolSteps={steps} isStreaming />);
      expect(screen.getByText("도구 2회 사용")).toBeInTheDocument();
    });

    it("shows tool count (not thinking count) when both are present", () => {
      render(
        <ThinkingProcess
          thinking={["생각1"]}
          toolSteps={[makeToolStep()]}
          isStreaming
        />
      );
      expect(screen.getByText("도구 1회 사용")).toBeInTheDocument();
    });
  });

  describe("auto-expand/collapse behavior", () => {
    it("is open (content visible) when isStreaming=true", () => {
      render(<ThinkingProcess thinking={["생각"]} isStreaming={true} />);
      expect(screen.getByText("생각")).toBeInTheDocument();
    });

    it("is closed (content hidden) when isStreaming=false", () => {
      render(<ThinkingProcess thinking={["생각"]} isStreaming={false} />);
      expect(screen.queryByText("생각")).not.toBeInTheDocument();
    });

    it("is closed when isStreaming is not provided", () => {
      render(<ThinkingProcess thinking={["생각"]} />);
      expect(screen.queryByText("생각")).not.toBeInTheDocument();
    });
  });

  describe("toggle behavior", () => {
    it("clicking header opens closed panel", () => {
      render(<ThinkingProcess thinking={["생각"]} isStreaming={false} />);
      const header = screen.getByRole("button");
      expect(screen.queryByText("생각")).not.toBeInTheDocument();
      fireEvent.click(header);
      expect(screen.getByText("생각")).toBeInTheDocument();
    });

    it("clicking header closes open panel", () => {
      render(<ThinkingProcess thinking={["생각"]} isStreaming={true} />);
      const header = screen.getByRole("button");
      expect(screen.getByText("생각")).toBeInTheDocument();
      fireEvent.click(header);
      expect(screen.queryByText("생각")).not.toBeInTheDocument();
    });

    it("header button has aria-expanded=true when open", () => {
      render(<ThinkingProcess thinking={["생각"]} isStreaming={true} />);
      expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    });

    it("header button has aria-expanded=false when closed", () => {
      render(<ThinkingProcess thinking={["생각"]} isStreaming={false} />);
      expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
    });
  });

  describe("thinking text rendering", () => {
    it("renders thinking text as italic gray paragraph", () => {
      render(<ThinkingProcess thinking={["삼성전자를 검색합니다"]} isStreaming />);
      const el = screen.getByText("삼성전자를 검색합니다");
      expect(el.tagName).toBe("P");
      expect(el.className).toContain("italic");
      expect(el.className).toContain("text-gray-400");
    });

    it("renders multiple thinking entries", () => {
      render(
        <ThinkingProcess thinking={["생각1", "생각2", "생각3"]} isStreaming />
      );
      expect(screen.getByText("생각1")).toBeInTheDocument();
      expect(screen.getByText("생각2")).toBeInTheDocument();
      expect(screen.getByText("생각3")).toBeInTheDocument();
    });
  });

  describe("tool step card rendering", () => {
    it("renders tool name in card", () => {
      render(<ThinkingProcess toolSteps={[makeToolStep()]} isStreaming />);
      expect(screen.getByText("search_reports")).toBeInTheDocument();
    });

    it("renders input params summary", () => {
      render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ input: { stock_name: "삼성전자" } })]}
          isStreaming
        />
      );
      expect(screen.getByText("stock_name: 삼성전자")).toBeInTheDocument();
    });

    it("renders result summary when status is done", () => {
      render(
        <ThinkingProcess
          toolSteps={[
            makeToolStep({
              status: "done",
              summary: "삼성전자 관련 12건 검색됨",
            }),
          ]}
          isStreaming
        />
      );
      expect(screen.getByText("삼성전자 관련 12건 검색됨")).toBeInTheDocument();
    });

    it("shows 검색중... when status is calling", () => {
      render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ status: "calling" })]}
          isStreaming
        />
      );
      expect(screen.getByText("검색중...")).toBeInTheDocument();
    });

    it("shows 완료 when status is done", () => {
      render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ status: "done", summary: "결과" })]}
          isStreaming
        />
      );
      expect(screen.getByText("완료")).toBeInTheDocument();
    });

    it("does not render summary when not provided", () => {
      render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ status: "calling", summary: undefined })]}
          isStreaming
        />
      );
      // summary text should not appear; just checking it doesn't crash
      expect(screen.getByText("search_reports")).toBeInTheDocument();
    });
  });

  describe("tool icons", () => {
    it("shows 🔍 for search_reports", () => {
      const { container } = render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ name: "search_reports" })]}
          isStreaming
        />
      );
      expect(container.textContent).toContain("🔍");
    });

    it("shows 📄 for get_report_detail", () => {
      const { container } = render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ name: "get_report_detail" })]}
          isStreaming
        />
      );
      expect(container.textContent).toContain("📄");
    });

    it("shows 📊 for list_stocks", () => {
      const { container } = render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ name: "list_stocks" })]}
          isStreaming
        />
      );
      expect(container.textContent).toContain("📊");
    });

    it("shows 📈 for get_report_stats", () => {
      const { container } = render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ name: "get_report_stats" })]}
          isStreaming
        />
      );
      expect(container.textContent).toContain("📈");
    });

    it("shows 🔧 for unknown tool name", () => {
      const { container } = render(
        <ThinkingProcess
          toolSteps={[makeToolStep({ name: "unknown_tool" })]}
          isStreaming
        />
      );
      expect(container.textContent).toContain("🔧");
    });
  });

  describe("border accent", () => {
    it("has border-l-2 border-blue-200 on root element", () => {
      const { container } = render(
        <ThinkingProcess thinking={["생각"]} isStreaming />
      );
      const root = container.firstChild as HTMLElement;
      expect(root.className).toContain("border-l-2");
      expect(root.className).toContain("border-blue-200");
    });
  });
});
