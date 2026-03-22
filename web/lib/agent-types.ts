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

export type SseEvent = SseTextEvent | SseDoneEvent | SseErrorEvent;

// Local UI message (may be partially streamed)
export interface UiMessage {
  id: string; // client-side temporary id
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}
