import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ChatMessage from "@/components/agent/ChatMessage";
import type { UiMessage } from "@/lib/agent-types";

function makeUserMsg(overrides: Partial<UiMessage> = {}): UiMessage {
  return {
    id: "test-1",
    role: "user",
    content: "안녕하세요",
    ...overrides,
  };
}

function makeAssistantMsg(overrides: Partial<UiMessage> = {}): UiMessage {
  return {
    id: "test-2",
    role: "assistant",
    content: "안녕하세요! 무엇을 도와드릴까요?",
    ...overrides,
  };
}

describe("ChatMessage", () => {
  describe("user message", () => {
    it("renders user message content", () => {
      render(<ChatMessage message={makeUserMsg({ content: "삼성전자 분석해줘" })} />);
      expect(screen.getByText("삼성전자 분석해줘")).toBeInTheDocument();
    });

    it("renders in right-aligned bubble", () => {
      render(<ChatMessage message={makeUserMsg()} />);
      const bubble = screen.getByTestId("chat-message-user");
      expect(bubble).toBeInTheDocument();
    });

    it("applies blue background to user bubble", () => {
      render(<ChatMessage message={makeUserMsg()} />);
      const bubble = screen.getByTestId("chat-message-user");
      expect(bubble.className).toContain("bg-blue-500");
    });

    it("applies white text to user bubble", () => {
      render(<ChatMessage message={makeUserMsg()} />);
      const bubble = screen.getByTestId("chat-message-user");
      expect(bubble.className).toContain("text-white");
    });

    it("user message wrapper is right-aligned (flex justify-end)", () => {
      const { container } = render(<ChatMessage message={makeUserMsg()} />);
      const wrapper = container.firstChild as HTMLElement;
      expect(wrapper.className).toContain("justify-end");
    });

    it("renders multiline user message preserving whitespace", () => {
      render(
        <ChatMessage message={makeUserMsg({ content: "첫 번째 줄\n두 번째 줄" })} />
      );
      expect(screen.getByText(/첫 번째 줄/)).toBeInTheDocument();
    });
  });

  describe("assistant message", () => {
    it("renders assistant message content", () => {
      render(
        <ChatMessage
          message={makeAssistantMsg({ content: "삼성전자 분석 결과입니다." })}
        />
      );
      expect(screen.getByText(/삼성전자 분석 결과입니다/)).toBeInTheDocument();
    });

    it("renders in left-aligned bubble", () => {
      render(<ChatMessage message={makeAssistantMsg()} />);
      const bubble = screen.getByTestId("chat-message-assistant");
      expect(bubble).toBeInTheDocument();
    });

    it("applies gray/white background to assistant bubble", () => {
      render(<ChatMessage message={makeAssistantMsg()} />);
      const bubble = screen.getByTestId("chat-message-assistant");
      expect(bubble.className).toContain("bg-white");
    });

    it("assistant wrapper is left-aligned (flex justify-start)", () => {
      const { container } = render(<ChatMessage message={makeAssistantMsg()} />);
      const wrapper = container.firstChild as HTMLElement;
      expect(wrapper.className).toContain("justify-start");
    });

    it("shows avatar label for assistant", () => {
      render(<ChatMessage message={makeAssistantMsg()} />);
      expect(screen.getByText("AI")).toBeInTheDocument();
    });

    it("renders markdown for assistant message (bold text)", () => {
      render(
        <ChatMessage
          message={makeAssistantMsg({ content: "**매수 추천**" })}
        />
      );
      const strong = screen.getByText("매수 추천");
      expect(strong.tagName).toBe("STRONG");
    });

    it("renders markdown table in assistant message", () => {
      const md = "| 종목 | 의견 |\n| --- | --- |\n| 삼성전자 | 매수 |";
      render(<ChatMessage message={makeAssistantMsg({ content: md })} />);
      expect(screen.getByText("종목")).toBeInTheDocument();
      expect(screen.getByText("삼성전자")).toBeInTheDocument();
    });
  });

  describe("streaming state", () => {
    it("shows typing indicator when streaming and content is empty", () => {
      const { container } = render(
        <ChatMessage
          message={makeAssistantMsg({ content: "", streaming: true })}
        />
      );
      // Three bouncing dots exist
      const dots = container.querySelectorAll(".animate-bounce");
      expect(dots.length).toBe(3);
    });

    it("shows cursor when streaming and content is not empty", () => {
      const { container } = render(
        <ChatMessage
          message={makeAssistantMsg({ content: "분석 중...", streaming: true })}
        />
      );
      const cursor = container.querySelector(".animate-pulse");
      expect(cursor).toBeInTheDocument();
    });

    it("does not show cursor when not streaming", () => {
      const { container } = render(
        <ChatMessage
          message={makeAssistantMsg({ content: "완료", streaming: false })}
        />
      );
      const cursor = container.querySelector(".animate-pulse");
      expect(cursor).not.toBeInTheDocument();
    });

    it("does not show typing dots when streaming but content exists", () => {
      const { container } = render(
        <ChatMessage
          message={makeAssistantMsg({ content: "일부 텍스트", streaming: true })}
        />
      );
      const dots = container.querySelectorAll(".animate-bounce");
      expect(dots.length).toBe(0);
    });
  });
});
