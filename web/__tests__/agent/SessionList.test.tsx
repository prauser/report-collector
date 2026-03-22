import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import SessionList from "@/components/agent/SessionList";
import type { ChatSession } from "@/lib/agent-types";

function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 1,
    title: "테스트 대화",
    message_count: 3,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

function renderList(
  overrides: Partial<{
    sessions: ChatSession[];
    currentSessionId: number | null;
    onSelect: (id: number) => void;
    onNewChat: () => void;
    onDelete: (id: number) => void;
    loading: boolean;
  }> = {}
) {
  const onSelect = overrides.onSelect ?? vi.fn();
  const onNewChat = overrides.onNewChat ?? vi.fn();
  const onDelete = overrides.onDelete ?? vi.fn();
  const props = {
    sessions: overrides.sessions ?? [],
    currentSessionId: overrides.currentSessionId ?? null,
    onSelect,
    onNewChat,
    onDelete,
    loading: overrides.loading ?? false,
  };
  return { onSelect, onNewChat, onDelete, ...render(<SessionList {...props} />) };
}

describe("SessionList", () => {
  describe("new chat button", () => {
    it("renders new chat button", () => {
      renderList();
      expect(screen.getByRole("button", { name: /새 대화 시작/ })).toBeInTheDocument();
    });

    it("calls onNewChat when new chat button is clicked", () => {
      const onNewChat = vi.fn();
      renderList({ onNewChat });
      fireEvent.click(screen.getByRole("button", { name: /새 대화 시작/ }));
      expect(onNewChat).toHaveBeenCalledTimes(1);
    });
  });

  describe("empty state", () => {
    it("shows empty message when no sessions and not loading", () => {
      renderList({ sessions: [] });
      expect(screen.getByText(/대화 내역이 없습니다/)).toBeInTheDocument();
    });

    it("shows loading message when loading=true", () => {
      renderList({ loading: true });
      expect(screen.getByText(/불러오는 중/)).toBeInTheDocument();
    });

    it("does not show empty message when loading", () => {
      renderList({ sessions: [], loading: true });
      expect(screen.queryByText(/대화 내역이 없습니다/)).not.toBeInTheDocument();
    });
  });

  describe("session rendering", () => {
    it("renders session title", () => {
      renderList({ sessions: [makeSession({ title: "삼성전자 분석" })] });
      expect(screen.getByText("삼성전자 분석")).toBeInTheDocument();
    });

    it("renders message count", () => {
      renderList({ sessions: [makeSession({ message_count: 5 })] });
      expect(screen.getByText(/5개 메시지/)).toBeInTheDocument();
    });

    it("renders fallback title when title is empty", () => {
      renderList({ sessions: [makeSession({ title: "" })] });
      // "새 대화" appears in both the button and the session item; use getAllByText
      const elements = screen.getAllByText("새 대화");
      expect(elements.length).toBeGreaterThanOrEqual(1);
    });

    it("renders multiple sessions", () => {
      renderList({
        sessions: [
          makeSession({ id: 1, title: "대화 1" }),
          makeSession({ id: 2, title: "대화 2" }),
          makeSession({ id: 3, title: "대화 3" }),
        ],
      });
      expect(screen.getByText("대화 1")).toBeInTheDocument();
      expect(screen.getByText("대화 2")).toBeInTheDocument();
      expect(screen.getByText("대화 3")).toBeInTheDocument();
    });

    it("highlights active session", () => {
      renderList({
        sessions: [makeSession({ id: 42, title: "활성 대화" })],
        currentSessionId: 42,
      });
      const item = screen.getByRole("button", { name: /대화 선택: 활성 대화/ });
      expect(item.className).toContain("bg-blue-50");
    });

    it("does not highlight inactive session", () => {
      renderList({
        sessions: [makeSession({ id: 1, title: "비활성 대화" })],
        currentSessionId: 99,
      });
      const item = screen.getByRole("button", { name: /대화 선택: 비활성 대화/ });
      expect(item.className).not.toContain("bg-blue-50");
    });
  });

  describe("session selection", () => {
    it("calls onSelect with session id when clicked", () => {
      const onSelect = vi.fn();
      renderList({
        sessions: [makeSession({ id: 7, title: "클릭 테스트" })],
        onSelect,
      });
      fireEvent.click(screen.getByRole("button", { name: /대화 선택: 클릭 테스트/ }));
      expect(onSelect).toHaveBeenCalledWith(7);
    });

    it("calls onSelect when Enter key pressed on session item", () => {
      const onSelect = vi.fn();
      renderList({
        sessions: [makeSession({ id: 3, title: "키보드 테스트" })],
        onSelect,
      });
      fireEvent.keyDown(
        screen.getByRole("button", { name: /대화 선택: 키보드 테스트/ }),
        { key: "Enter" }
      );
      expect(onSelect).toHaveBeenCalledWith(3);
    });
  });

  describe("session deletion", () => {
    it("renders delete button for each session", () => {
      renderList({
        sessions: [makeSession({ id: 1, title: "삭제 테스트" })],
      });
      expect(
        screen.getByRole("button", { name: /대화 삭제: 삭제 테스트/ })
      ).toBeInTheDocument();
    });

    it("calls onDelete with session id when delete button clicked", () => {
      const onDelete = vi.fn();
      renderList({
        sessions: [makeSession({ id: 5, title: "삭제할 대화" })],
        onDelete,
      });
      fireEvent.click(screen.getByRole("button", { name: /대화 삭제: 삭제할 대화/ }));
      expect(onDelete).toHaveBeenCalledWith(5);
    });

    it("delete button click does not trigger onSelect", () => {
      const onSelect = vi.fn();
      const onDelete = vi.fn();
      renderList({
        sessions: [makeSession({ id: 5, title: "테스트" })],
        onSelect,
        onDelete,
      });
      fireEvent.click(screen.getByRole("button", { name: /대화 삭제: 테스트/ }));
      expect(onSelect).not.toHaveBeenCalled();
      expect(onDelete).toHaveBeenCalledWith(5);
    });
  });

  describe("relative date display", () => {
    it("shows 오늘 for today's session", () => {
      renderList({
        sessions: [makeSession({ updated_at: new Date().toISOString() })],
      });
      expect(screen.getByText(/오늘/)).toBeInTheDocument();
    });

    it("shows 어제 for yesterday's session", () => {
      const yesterday = new Date();
      yesterday.setDate(yesterday.getDate() - 1);
      renderList({
        sessions: [makeSession({ updated_at: yesterday.toISOString() })],
      });
      expect(screen.getByText(/어제/)).toBeInTheDocument();
    });
  });
});
