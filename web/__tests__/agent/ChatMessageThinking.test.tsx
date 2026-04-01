import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ChatMessage from "@/components/agent/ChatMessage";
import type { UiMessage, ToolStep } from "@/lib/agent-types";

function makeAssistantMsg(overrides: Partial<UiMessage> = {}): UiMessage {
  return {
    id: "test-2",
    role: "assistant",
    content: "분석 완료",
    ...overrides,
  };
}

const toolStep: ToolStep = {
  id: "toolu_001",
  name: "search_reports",
  input: { stock_name: "삼성전자" },
  status: "done",
  summary: "12건 검색됨",
};

describe("ChatMessage with ThinkingProcess", () => {
  it("does not render ThinkingProcess when no thinking or toolSteps", () => {
    render(<ChatMessage message={makeAssistantMsg()} />);
    expect(screen.queryByText(/분석 과정/)).not.toBeInTheDocument();
  });

  it("renders ThinkingProcess header when thinking is provided", () => {
    render(
      <ChatMessage
        message={makeAssistantMsg({
          thinking: ["삼성전자를 검색합니다"],
          streaming: false,
        })}
      />
    );
    expect(screen.getByText("분석 과정")).toBeInTheDocument();
  });

  it("renders ThinkingProcess header when toolSteps is provided", () => {
    render(
      <ChatMessage
        message={makeAssistantMsg({
          toolSteps: [toolStep],
          streaming: false,
        })}
      />
    );
    expect(screen.getByText("도구 1회 사용")).toBeInTheDocument();
  });

  it("ThinkingProcess is expanded during streaming", () => {
    render(
      <ChatMessage
        message={makeAssistantMsg({
          content: "",
          thinking: ["생각 중"],
          streaming: true,
        })}
      />
    );
    // Content should be visible when streaming (expanded)
    expect(screen.getByText("생각 중")).toBeInTheDocument();
  });

  it("ThinkingProcess is collapsed when streaming is done", () => {
    render(
      <ChatMessage
        message={makeAssistantMsg({
          thinking: ["생각 중"],
          streaming: false,
        })}
      />
    );
    // Content is hidden when collapsed; header is still present
    expect(screen.getByText("분석 과정")).toBeInTheDocument();
    expect(screen.queryByText("생각 중")).not.toBeInTheDocument();
  });

  it("renders ThinkingProcess above content (before markdown)", () => {
    const { container } = render(
      <ChatMessage
        message={makeAssistantMsg({
          content: "분석 결과입니다",
          toolSteps: [toolStep],
          streaming: false,
        })}
      />
    );
    const bubble = container.querySelector("[data-testid='chat-message-assistant']");
    expect(bubble).not.toBeNull();
    // ThinkingProcess renders a div with border-l-2, markdown renders a div with prose
    const children = bubble!.children;
    // First child should be ThinkingProcess (border-l-2), second should be markdown
    expect(children[0].className).toContain("border-l-2");
    expect(children[1].className).toContain("prose");
  });
});
