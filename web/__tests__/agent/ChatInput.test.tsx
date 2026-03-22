import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ChatInput from "@/components/agent/ChatInput";

function renderInput(
  overrides: Partial<{
    value: string;
    onChange: (v: string) => void;
    onSend: () => void;
    disabled: boolean;
    placeholder: string;
  }> = {}
) {
  const onChange = overrides.onChange ?? vi.fn();
  const onSend = overrides.onSend ?? vi.fn();
  const props = {
    value: overrides.value ?? "",
    onChange,
    onSend,
    disabled: overrides.disabled ?? false,
    placeholder: overrides.placeholder,
  };
  return { onChange, onSend, ...render(<ChatInput {...props} />) };
}

describe("ChatInput", () => {
  describe("rendering", () => {
    it("renders the textarea", () => {
      renderInput();
      expect(screen.getByRole("textbox", { name: /메시지 입력/ })).toBeInTheDocument();
    });

    it("renders send button", () => {
      renderInput();
      expect(screen.getByRole("button", { name: /전송/ })).toBeInTheDocument();
    });

    it("shows default placeholder text", () => {
      renderInput({ value: "" });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ }) as HTMLTextAreaElement;
      expect(textarea.placeholder).toBe("메시지를 입력하세요...");
    });

    it("shows custom placeholder", () => {
      renderInput({ placeholder: "여기에 입력...", value: "" });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ }) as HTMLTextAreaElement;
      expect(textarea.placeholder).toBe("여기에 입력...");
    });

    it("shows keyboard hint text", () => {
      renderInput();
      expect(screen.getByText(/Enter로 전송/)).toBeInTheDocument();
    });

    it("shows current value", () => {
      renderInput({ value: "테스트 메시지" });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ }) as HTMLTextAreaElement;
      expect(textarea.value).toBe("테스트 메시지");
    });
  });

  describe("disabled state", () => {
    it("disables textarea when disabled=true", () => {
      renderInput({ disabled: true });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ });
      expect(textarea).toBeDisabled();
    });

    it("disables send button when disabled=true", () => {
      renderInput({ disabled: true, value: "some text" });
      const btn = screen.getByRole("button", { name: /전송/ });
      expect(btn).toBeDisabled();
    });

    it("disables send button when value is empty", () => {
      renderInput({ value: "" });
      const btn = screen.getByRole("button", { name: /전송/ });
      expect(btn).toBeDisabled();
    });

    it("disables send button when value is only whitespace", () => {
      renderInput({ value: "   " });
      const btn = screen.getByRole("button", { name: /전송/ });
      expect(btn).toBeDisabled();
    });

    it("enables send button when value is non-empty and not disabled", () => {
      renderInput({ value: "텍스트", disabled: false });
      const btn = screen.getByRole("button", { name: /전송/ });
      expect(btn).not.toBeDisabled();
    });
  });

  describe("onChange", () => {
    it("calls onChange when user types", () => {
      const onChange = vi.fn();
      renderInput({ onChange });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ });
      fireEvent.change(textarea, { target: { value: "새 텍스트" } });
      expect(onChange).toHaveBeenCalledWith("새 텍스트");
    });
  });

  describe("onSend via button click", () => {
    it("calls onSend when send button is clicked", () => {
      const onSend = vi.fn();
      renderInput({ value: "메시지", onSend });
      fireEvent.click(screen.getByRole("button", { name: /전송/ }));
      expect(onSend).toHaveBeenCalledTimes(1);
    });

    it("does not call onSend when value is empty and button clicked", () => {
      const onSend = vi.fn();
      renderInput({ value: "", onSend });
      fireEvent.click(screen.getByRole("button", { name: /전송/ }));
      expect(onSend).not.toHaveBeenCalled();
    });

    it("does not call onSend when disabled and button clicked", () => {
      const onSend = vi.fn();
      renderInput({ value: "텍스트", onSend, disabled: true });
      fireEvent.click(screen.getByRole("button", { name: /전송/ }));
      expect(onSend).not.toHaveBeenCalled();
    });
  });

  describe("onSend via keyboard", () => {
    it("calls onSend on Enter key press", () => {
      const onSend = vi.fn();
      renderInput({ value: "메시지", onSend });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ });
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
      expect(onSend).toHaveBeenCalledTimes(1);
    });

    it("does not call onSend on Shift+Enter", () => {
      const onSend = vi.fn();
      renderInput({ value: "메시지", onSend });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ });
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
      expect(onSend).not.toHaveBeenCalled();
    });

    it("does not call onSend on Enter when value is empty", () => {
      const onSend = vi.fn();
      renderInput({ value: "", onSend });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ });
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
      expect(onSend).not.toHaveBeenCalled();
    });

    it("does not call onSend on Enter when disabled", () => {
      const onSend = vi.fn();
      renderInput({ value: "텍스트", onSend, disabled: true });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ });
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
      expect(onSend).not.toHaveBeenCalled();
    });

    it("does not call onSend on other keys", () => {
      const onSend = vi.fn();
      renderInput({ value: "텍스트", onSend });
      const textarea = screen.getByRole("textbox", { name: /메시지 입력/ });
      fireEvent.keyDown(textarea, { key: "Tab" });
      fireEvent.keyDown(textarea, { key: "a" });
      expect(onSend).not.toHaveBeenCalled();
    });
  });
});
