"use client";

import { useRef, useCallback, useEffect, KeyboardEvent, ChangeEvent } from "react";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled?: boolean;
  placeholder?: string;
}

export default function ChatInput({
  value,
  onChange,
  onSend,
  disabled = false,
  placeholder = "메시지를 입력하세요...",
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Reset height when value is cleared externally (e.g. after send)
  useEffect(() => {
    if (value === "" && textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value]);

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value);
      // Auto-resize textarea
      const el = e.target;
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    },
    [onChange]
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!disabled && value.trim()) {
          onSend();
          // Reset height after send
          if (textareaRef.current) {
            textareaRef.current.style.height = "auto";
          }
        }
      }
    },
    [disabled, onSend, value]
  );

  const handleSendClick = useCallback(() => {
    if (!disabled && value.trim()) {
      onSend();
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    }
  }, [disabled, onSend, value]);

  return (
    <div className="border-t border-gray-200 bg-white px-4 py-3">
      <div className="flex items-end gap-2 max-w-3xl mx-auto">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          rows={1}
          aria-label="메시지 입력"
          className={[
            "flex-1 resize-none rounded-xl border border-gray-300 px-3 py-2.5 text-sm leading-relaxed",
            "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent",
            "disabled:bg-gray-50 disabled:text-gray-400 disabled:cursor-not-allowed",
            "placeholder:text-gray-400",
            "overflow-y-auto",
          ].join(" ")}
        />
        <button
          type="button"
          onClick={handleSendClick}
          disabled={disabled || !value.trim()}
          aria-label="전송"
          className={[
            "flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center",
            "bg-blue-500 text-white transition-colors",
            "hover:bg-blue-600 active:bg-blue-700",
            "disabled:bg-gray-200 disabled:text-gray-400 disabled:cursor-not-allowed",
          ].join(" ")}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="currentColor"
            className="w-5 h-5"
            aria-hidden="true"
          >
            <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
          </svg>
        </button>
      </div>
      <p className="text-xs text-gray-400 text-center mt-1.5">
        Enter로 전송, Shift+Enter로 줄바꿈
      </p>
    </div>
  );
}
