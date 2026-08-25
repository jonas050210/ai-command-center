// API types — mirrors backend serialization exactly.

export type View = "chat" | "models" | "agent" | "compare" | "team" | "research" | "projects" | "git";

export interface RuntimeSettings {
  free_only: boolean;
  max_spend: number;
  default_model: string;
  default_provider: string;
  num_ctx: number;
  keep_alive: string;
  custom_instructions: string;
  eur_per_usd: number;
  currency: string;
  // Agent Mode capabilities (human approval still required for mutations)
  cap_filesystem_read: boolean;
  cap_filesystem_write: boolean;
  cap_command_execute: boolean;
  cap_network_fetch: boolean;
  cap_git_operate: boolean;
  cap_memory: boolean;
}

// ── Memory & skills ──────────────────────────────────────────────────
export interface MemoryRow {
  id: number;
  key: string;
  content: string;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface OllamaStatus {
  status: "running" | "unavailable" | "error" | string;
  version: string | null;
  latency_ms: number | null;
  models_count: number | null;
  detail: string | null;
  host?: string | null;
}

export interface ProviderInfo {
  name: string;
  display_name: string;
  is_local: boolean;
  status: string;
  version: string | null;
  latency_ms: number | null;
  models_count: number | null;
  detail: string | null;
  base_url: string | null;
  cost_input_per_mtok: number;
  cost_output_per_mtok: number;
  is_free: boolean;
  supports_pull: boolean;
  supports_delete: boolean;
  requires_api_key: boolean;
  key_configured: boolean;
  key_masked: string | null;
}

export interface ProviderSummary {
  name: string;
  display_name: string;
  is_local: boolean;
  configured: boolean;
  last_status: string | null;
}

export interface SystemStatus {
  ollama: OllamaStatus;
  models_in_catalog: number;
  runtime: Omit<RuntimeSettings, "currency">;
  currency: string;
  providers: ProviderSummary[];
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
  provider_caps: Record<string, {
    pull: boolean; delete: boolean; requires_api_key: boolean; is_local: boolean;
  }>;
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
  | { type: "meta"; request_id: string; conversation_id: string; user_message_id: string | null; assistant_message_id: string; model: string; provider: string; compacted?: boolean }
  | { type: "delta"; content: string }
  | { type: "usage"; input_tokens: number; output_tokens: number; total_tokens: number; method: "exact" | "estimated"; tokens_per_second: number | null; cost_eur: number }
  | { type: "done"; assistant_message_id: string; status: "complete" | "stopped" | string }
  | { type: "error"; code: string; message: string; status_code?: number; details?: Record<string, unknown> };

export interface ApiErrorShape {
  code: string;
  message: string;
  status?: number;
}

// ── Agent Mode ───────────────────────────────────────────────────────
export type AgentRunStatus = "running" | "complete" | "stopped" | "denied" | "error";

export interface AgentRunRow {
  id: string;
  task: string;
  provider: string | null;
  model: string | null;
  status: AgentRunStatus;
  result: string | null;
  error: string | null;
  steps: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
  finished_at: string | null;
}

export interface AgentStepRow {
  id: number;
  run_id: string;
  step: number;
  kind: "model" | "tool_call" | "tool_result" | "approval" | "note" | string;
  content: string;
  data_json: string | null;
  created_at: string;
}

export interface AgentApprovalRow {
  id: string;
  run_id: string;
  tool: string;
  args_json: string;
  preview: string | null;
  danger: string;
  status: "pending" | "approved" | "denied" | "expired" | string;
  created_at: string;
  decided_at: string | null;
}

export interface AgentToolInfo {
  name: string;
  description: string;
  danger: "read" | "write" | "exec" | string;
  capability: string;
  requires_approval: boolean;
  parameters: Record<string, unknown>;
}

export interface ExecutionRow {
  id: number;
  kind: string;
  status: string;
  command: string | null;
  actor: string;
  exit_code: number | null;
  log: string | null;
  started_at: string;
  finished_at: string | null;
}

// Agent run SSE events — exact mirror of backend/app/agent/engine.py emissions
export type AgentEvent =
  | { type: "meta"; run_id: string; model: string; provider: string;
      capabilities: Record<string, boolean>; max_steps: number }
  | { type: "note"; level: "info" | "warn" | string; message: string }
  | { type: "step"; step: number }
  | { type: "delta"; step: number; content: string }
  | { type: "tool_call"; step: number; call_id: string; tool: string;
      args: Record<string, unknown> }
  | { type: "approval_required"; approval_id: string; tool: string;
      args: Record<string, unknown>; preview: string | null; danger: string;
      timeout_s: number }
  | { type: "approval_decided"; approval_id: string | null; status: string;
      tool?: string }
  | { type: "tool_result"; step: number; call_id: string; tool: string; ok: boolean;
      danger: string; exit_code?: number | null; ms?: number;
      output: string; diff?: string | null; error?: string }
  | { type: "usage"; input_tokens: number; output_tokens: number;
      total_tokens: number; steps: number; elapsed_s: number }
  | { type: "done"; run_id: string; status: AgentRunStatus; result: string;
      error: string | null; steps: number; elapsed_s: number }
  | { type: "error"; code: string; message: string; status_code?: number };

// ── Projects ─────────────────────────────────────────────────────────
export interface ProjectRow {
  id: number;
  name: string;
  description: string;
  root_path: string;
  status: "active" | "archived" | string;
  file_count: number;
  missing: boolean;
  display_path: string;
  created_at: string;
  updated_at: string;
}

// ── Compare Mode ─────────────────────────────────────────────────────
export type CompareEvent =
  | { type: "meta"; comparisons: Array<{ index: number; provider: string; model: string; is_local: boolean }> }
  | { type: "slot_status"; index: number; status: "queued" | "running" | "cancelled" | string }
  | { type: "delta"; index: number; content: string }
  | { type: "model_done"; index: number; status: "complete" | "stopped" | "error" | string;
      input_tokens?: number | null; output_tokens?: number | null;
      token_method?: "exact" | "estimated"; tokens_per_second?: number | null;
      elapsed_s?: number; code?: string; message?: string }
  | { type: "done" }
  | { type: "error"; code: string; message: string; status_code?: number };

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

// ── Research Mode ────────────────────────────────────────────────────
export interface ResearchSource {
  index: number;
  title: string;
  url: string;
  snippet?: string;
}

export type ResearchEvent =
  | { type: "meta"; research_id: number; question: string }
  | { type: "status"; stage: "searching" | "fetching" | "answering"; message: string }
  | { type: "sources"; sources: ResearchSource[] }
  | { type: "note"; level: string; message: string }
  | { type: "delta"; content: string }
  | { type: "citations"; citations: ResearchSource[] }
  | { type: "usage"; input_tokens: number; output_tokens: number; method: string;
      model: string; provider: string; elapsed_s: number }
  | { type: "done"; research_id: number; status: string; answer: string;
      citations: ResearchSource[] }
  | { type: "error"; code: string; message: string; status_code?: number };

export interface ResearchRunRow {
  id: number;
  query: string;
  status: "complete" | "stopped" | "error" | "running" | string;
  result: string;
  sources: ResearchSource[];
  created_at: string;
  updated_at: string;
}

// ── Git / GitHub ─────────────────────────────────────────────────────
export interface GitFileStatus {
  path: string;
  x: string;
  y: string;
  staged: boolean;
  untracked: boolean;
}

export interface GitStatus {
  path: string;
  branch: string;
  ahead: number;
  behind: number;
  remote: string | null;
  files: GitFileStatus[];
  clean: boolean;
}

export interface GitCommitRow {
  sha: string;
  date: string;
  author: string;
  decorations: string;
  message: string;
}

export interface GitBranchRow {
  name: string;
  current: boolean;
}

export interface GithubRepoRow {
  name: string;
  full_name: string;
  private: boolean;
  html_url: string;
  default_branch: string;
  clone_url: string;
  updated_at: string;
}

export interface GithubUser {
  login: string;
  name: string | null;
  avatar_url: string | null;
  html_url: string | null;
}
