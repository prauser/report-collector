import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import MarkdownRenderer from "@/components/shared/MarkdownRenderer";

describe("MarkdownRenderer", () => {
  describe("basic text", () => {
    it("renders plain text", () => {
      render(<MarkdownRenderer content="Hello world" />);
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });

    it("renders empty content without crashing", () => {
      const { container } = render(<MarkdownRenderer content="" />);
      expect(container).toBeInTheDocument();
    });
  });

  describe("headings", () => {
    it("renders h1", () => {
      render(<MarkdownRenderer content="# 제목" />);
      expect(screen.getByRole("heading", { level: 1, name: "제목" })).toBeInTheDocument();
    });

    it("renders h2", () => {
      render(<MarkdownRenderer content="## 소제목" />);
      expect(screen.getByRole("heading", { level: 2, name: "소제목" })).toBeInTheDocument();
    });

    it("renders h3", () => {
      render(<MarkdownRenderer content="### 소소제목" />);
      expect(screen.getByRole("heading", { level: 3, name: "소소제목" })).toBeInTheDocument();
    });
  });

  describe("lists", () => {
    it("renders unordered list items", () => {
      render(<MarkdownRenderer content={"- 항목1\n- 항목2\n- 항목3"} />);
      expect(screen.getByText("항목1")).toBeInTheDocument();
      expect(screen.getByText("항목2")).toBeInTheDocument();
      expect(screen.getByText("항목3")).toBeInTheDocument();
    });

    it("renders ordered list items", () => {
      render(<MarkdownRenderer content={"1. 첫째\n2. 둘째\n3. 셋째"} />);
      expect(screen.getByText("첫째")).toBeInTheDocument();
      expect(screen.getByText("둘째")).toBeInTheDocument();
      expect(screen.getByText("셋째")).toBeInTheDocument();
    });

    it("renders ul element for unordered list", () => {
      const { container } = render(<MarkdownRenderer content={"- 항목"} />);
      expect(container.querySelector("ul")).toBeInTheDocument();
    });

    it("renders ol element for ordered list", () => {
      const { container } = render(<MarkdownRenderer content={"1. 항목"} />);
      expect(container.querySelector("ol")).toBeInTheDocument();
    });
  });

  describe("code", () => {
    it("renders inline code", () => {
      render(<MarkdownRenderer content="이것은 `code` 입니다" />);
      const code = screen.getByText("code");
      expect(code.tagName).toBe("CODE");
    });

    it("renders code block", () => {
      const md = "```python\nprint('hello')\n```";
      const { container } = render(<MarkdownRenderer content={md} />);
      expect(container.querySelector("pre")).toBeInTheDocument();
    });
  });

  describe("table (GFM)", () => {
    it("renders table with thead and tbody", () => {
      const md = "| 종목 | 가격 |\n| --- | --- |\n| 삼성전자 | 71,000 |";
      const { container } = render(<MarkdownRenderer content={md} />);
      expect(container.querySelector("table")).toBeInTheDocument();
      expect(container.querySelector("thead")).toBeInTheDocument();
    });

    it("renders table headers", () => {
      const md = "| 종목 | 가격 |\n| --- | --- |\n| 삼성전자 | 71,000 |";
      render(<MarkdownRenderer content={md} />);
      expect(screen.getByText("종목")).toBeInTheDocument();
      expect(screen.getByText("가격")).toBeInTheDocument();
    });

    it("renders table data cells", () => {
      const md = "| 종목 | 가격 |\n| --- | --- |\n| 삼성전자 | 71,000 |";
      render(<MarkdownRenderer content={md} />);
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
      expect(screen.getByText("71,000")).toBeInTheDocument();
    });
  });

  describe("emphasis", () => {
    it("renders bold text", () => {
      render(<MarkdownRenderer content="**굵게**" />);
      const strong = screen.getByText("굵게");
      expect(strong.tagName).toBe("STRONG");
    });

    it("renders italic text", () => {
      render(<MarkdownRenderer content="*기울임*" />);
      const em = screen.getByText("기울임");
      expect(em.tagName).toBe("EM");
    });
  });

  describe("blockquote", () => {
    it("renders blockquote", () => {
      const { container } = render(<MarkdownRenderer content="> 인용문" />);
      expect(container.querySelector("blockquote")).toBeInTheDocument();
    });
  });

  describe("className prop", () => {
    it("applies custom className to wrapper", () => {
      const { container } = render(
        <MarkdownRenderer content="test" className="custom-class" />
      );
      const wrapper = container.firstChild as HTMLElement;
      expect(wrapper.className).toContain("custom-class");
    });
  });
});
