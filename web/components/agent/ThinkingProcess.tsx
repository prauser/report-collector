"use client";

import { useState, useEffect } from "react";
import type { ToolStep } from "@/lib/agent-types";

interface Props {
  thinking?: string[];
  toolSteps?: ToolStep[];
  isStreaming?: boolean;
}

const TOOL_ICONS: Record<string, string> = {
  search_reports: "🔍",
  get_report_detail: "📄",
  list_stocks: "📊",
  get_report_stats: "📈",
};

function getToolIcon(name: string): string {
  return TOOL_ICONS[name] ?? "🔧";
}

function summarizeInput(input: Record<string, unknown>): string {
  const entries = Object.entries(input);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) =>
      typeof v === "object" && v !== null
        ? `${k}: ${JSON.stringify(v)}`
        : `${k}: ${String(v)}`
    )
    .join(", ");
}

export default function ThinkingProcess({ thinking, toolSteps, isStreaming }: Props) {
  const hasContent = !!(toolSteps?.length || thinking?.length);

  // Auto-expand while streaming, collapse when done
  const [open, setOpen] = useState(!!isStreaming);

  useEffect(() => {
    if (isStreaming) {
      setOpen(true);
    } else if (hasContent) {
      setOpen(false);
    }
  }, [isStreaming, hasContent]);

  if (!hasContent) return null;

  const toolCount = toolSteps?.length ?? 0;
  const label = toolCount > 0 ? `도구 ${toolCount}회 사용` : "분석 과정";

  return (
    <div className="mb-2 border-l-2 border-blue-200 pl-3">
      {/* Header / toggle */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs text-blue-500 font-medium hover:text-blue-700 transition-colors select-none"
        aria-expanded={open}
        aria-controls="thinking-content"
      >
        <span>{label}</span>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="currentColor"
          className={`w-3.5 h-3.5 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        >
          <path
            fillRule="evenodd"
            d="M12.53 16.28a.75.75 0 01-1.06 0l-7.5-7.5a.75.75 0 011.06-1.06L12 14.69l6.97-6.97a.75.75 0 111.06 1.06l-7.5 7.5z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {/* Collapsible body */}
      {open && (
        <div id="thinking-content" role="region" className="mt-1.5 flex flex-col gap-1.5">
          {/* Thinking entries */}
          {thinking?.map((text, i) => (
            <p
              key={`thinking-${i}`}
              className="text-xs text-gray-400 italic leading-relaxed"
            >
              {text}
            </p>
          ))}

          {/* Tool step cards */}
          {toolSteps?.map((step) => (
            <div
              key={step.id}
              className="flex items-start gap-2 bg-gray-50 border border-gray-100 rounded-lg px-2.5 py-1.5"
            >
              {/* Icon */}
              <span className="text-sm flex-shrink-0 mt-0.5" aria-hidden="true">
                {getToolIcon(step.name)}
              </span>

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs font-medium text-gray-700">
                    {step.name}
                  </span>
                  {/* Status badge */}
                  {step.status === "calling" ? (
                    <span className="flex items-center gap-1 text-xs text-blue-500">
                      <svg
                        className="w-3 h-3 animate-spin"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                        aria-hidden="true"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                        />
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z"
                        />
                      </svg>
                      검색중...
                    </span>
                  ) : (
                    <span className="flex items-center gap-0.5 text-xs text-green-600">
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 24 24"
                        fill="currentColor"
                        className="w-3 h-3"
                        aria-hidden="true"
                      >
                        <path
                          fillRule="evenodd"
                          d="M19.916 4.626a.75.75 0 01.208 1.04l-9 13.5a.75.75 0 01-1.154.114l-6-6a.75.75 0 011.06-1.06l5.353 5.353 8.493-12.739a.75.75 0 011.04-.208z"
                          clipRule="evenodd"
                        />
                      </svg>
                      완료
                    </span>
                  )}
                </div>

                {/* Params summary */}
                {Object.keys(step.input).length > 0 && (
                  <p className="text-xs text-gray-400 mt-0.5 truncate">
                    {summarizeInput(step.input)}
                  </p>
                )}

                {/* Result summary */}
                {step.summary && (
                  <p className="text-xs text-gray-500 mt-0.5">{step.summary}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
