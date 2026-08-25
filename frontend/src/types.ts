// API types — mirrors backend serialization exactly.

export type View = "chat" | "models" | "agent" | "team" | "research" | "projects" | "git";

export interface RuntimeSettings {
  free_only: boolean;
  max_spend: number;
  default_model: string;
  num_ctx: number;
  keep_alive: string;
  custom_instructions: string;
  currency: string;
}

export interface OllamaStatus {
  status: "running" | "unavailable" | "error" | string;
  version: string | null;
  latency_ms: number | null;
  models_count: number | null;
  detail: string | null;
  host?: string | null;
}

export interface SystemStatus {
  ollama: OllamaStatus;
  models_in_catalog: number;
  runtime: Omit<RuntimeSettings, "currency">;
  currency: string;
  metrics: {
    uptime_s: number;
    http_requests: number;
    chat_requests: number;
    chat_errors: number;
    blocked_paid_requests: number;
  };
  server_time: string;
}

export interface Costs {
  currency: string;
  current: number;
  session: number;
  total: number;
  free_only: boolean;
  max_spend: number;
}

export interface TokenUsage {
  session: { input_tokens: number; output_tokens: number; total_tokens: number };
  total: { input_tokens: number; output_tokens: number; total_tokens: number };
  per_model: Array<{
    provider: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  }>;
}

export interface ModelCardData {
  id: number;
  provider: string;
  name: string;
  display_name: string;
  is_local: boolean;
  is_free: boolean;
  location: "local" | "cloud" | string;
  cost_eur: number;
  context_length: number | null; // null → "Unknown"
  size_bytes: number | null;
  parameter_size: string | null; // null → "Unknown"
  quantization: string | null;
  family: string | null;
  capabilities: string[];
  categories: string[];
  available: boolean;
  status: string;
  favorite: boolean;
  measured_tps: number | null; // null → "Unknown"
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  usage_count: number;
  last_used_at: string | null;
  last_seen_at: string | null;
}

export interface ModelsResponse {
  models: ModelCardData[];
  recent: ModelCardData[];
  categories: string[];
  count: number;
}

export interface ChatMessageData {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  model: string | null;
  provider: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  token_method: "exact" | "estimated";
  status: "complete" | "stopped" | "error" | "streaming" | "pending" | string;
  error: string | null;
  created_at: string;
}

export interface ConversationData {
  id: string;
  title: string;
  model: string | null;
  provider: string | null;
  system_prompt: string | null;
  pinned: boolean;
  archived: boolean;
  favorite: boolean;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  message_count: number | null;
  created_at: string;
  updated_at: string;
  messages?: ChatMessageData[];
}

// SSE stream events
export type SSEvent =
  | { type: "meta"; request_id: string; conversation_id: string; user_message_id: string | null; assistant_message_id: string; model: string; provider: string }
  | { type: "delta"; content: string }
  | { type: "usage"; input_tokens: number; output_tokens: number; total_tokens: number; method: "exact" | "estimated"; tokens_per_second: number | null; cost_eur: number }
  | { type: "done"; assistant_message_id: string; status: "complete" | "stopped" | string }
  | { type: "error"; code: string; message: string; status_code?: number; details?: Record<string, unknown> };

export interface ApiErrorShape {
  code: string;
  message: string;
  status?: number;
}

export interface ModelTestResult {
  model: string;
  provider: string;
  tokens_per_second: number | null;
  latency_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  token_method: "exact" | "estimated";
  cost_eur: number;
}
