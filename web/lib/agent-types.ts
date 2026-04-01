export interface ChatSession {
  id: number;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

// SSE event types emitted by the backend
export type SseTextEvent = {
  type: "text";
  text: string;
};

export type SseDoneEvent = {
  type: "done";
};

export type SseErrorEvent = {
  type: "error";
  message: string;
};

export type SseToolCallEvent = {
  type: "tool_call";
  id: string;
  name: string;
  input: Record<string, unknown>;
};

export type SseToolResultEvent = {
  type: "tool_result";
  id: string;
  name: string;
  summary: string;
};

export type SseThinkingEvent = {
  type: "thinking";
  text: string;
};

export type SseEvent =
  | SseTextEvent
  | SseDoneEvent
  | SseErrorEvent
  | SseToolCallEvent
  | SseToolResultEvent
  | SseThinkingEvent;

// A single tool invocation tracked during a streaming response
export interface ToolStep {
  id: string;
  name: string;
  input: Record<string, unknown>;
  summary?: string;
  status: "calling" | "done";
}

// Local UI message (may be partially streamed)
export interface UiMessage {
  id: string; // client-side temporary id
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  thinking?: string[];
  toolSteps?: ToolStep[];
}
