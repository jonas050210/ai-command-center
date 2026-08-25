// API types — mirrors backend serialization exactly.

export type View = "chat" | "models" | "agent" | "team" | "compare" | "research" | "projects" | "git";

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

// ── Agent Mode ─────────────────────────────────────────────────────
export interface AgentRun {
  id: number;
  project_id: number | null;
  task: string;
  workspace: string;
  plan: string;
  status: string;
  stage: string;
  summary: string;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_eur: number;
  created_at: string;
  updated_at: string;
  steps?: AgentStep[];
}

export interface AgentStep {
  id: number;
  run_id: number;
  seq: number;
  stage: string;
  tool: string | null;
  target: string | null;
  summary: string;
  status: string;
  detail: string;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface AgentEvent {
  type: "run" | "stage" | "activity" | "tool_result" | "tokens" | "done" | "error";
  run_id: number;
  stage?: string;
  status?: string;
  kind?: string;
  tool?: string;
  content?: string;
  ok?: boolean;
  round?: number;
  input?: number;
  output?: number;
  cost?: number;
  summary?: string;
  code?: string;
  message?: string;
  workspace?: string;
}

// ── Team Mode ──────────────────────────────────────────────────────
export interface TeamMember {
  id: number;
  team_id: number;
  provider: string | null;
  model: string;
  role: string;
  responsibility: string;
  input_tokens: number;
  output_tokens: number;
  status: string;
}

export interface TeamTask {
  id: number;
  team_id: number;
  title: string;
  description: string;
  assignee: string | null;
  status: "todo" | "in_progress" | "review" | "done" | string;
  progress: number;
  dependencies: string;
  error: string | null;
}

export interface TeamEvent {
  id: number;
  team_id: number;
  phase: string;
  actor: string | null;
  kind: string;
  content: string;
  created_at: string;
}

export interface TeamRun {
  id: number;
  name: string;
  task: string;
  master_plan: string;
  deliverable: string;
  status: string;
  project_id: number | null;
  created_at: string;
  updated_at: string;
  member_count?: number;
  members?: TeamMember[];
  events?: TeamEvent[];
  tasks?: TeamTask[];
  tokens?: { input_tokens: number; output_tokens: number; total_tokens: number; cost_eur: number };
}

export interface TeamEventStream {
  type: "team" | "phase" | "activity" | "tokens" | "done" | "error";
  team_id: number;
  phase?: string;
  status?: string;
  actor?: string;
  kind?: string;
  content?: string;
  round?: number;
  members?: Array<{ model: string; role: string; input_tokens: number; output_tokens: number; total_tokens: number }>;
  total?: { input_tokens: number; output_tokens: number; total_tokens: number; cost_eur: number };
  deliverable?: string;
  code?: string;
  message?: string;
}

// ── Compare Mode ───────────────────────────────────────────────────
export interface CompareAnswer {
  id: number;
  run_id: number;
  model: string;
  provider: string;
  answer: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens?: number;
  token_method: string;
  cost_eur: number;
  status: string;
  error: string | null;
  selected: number;
}

export interface CompareRun {
  id: number;
  prompt: string;
  status: string;
  selected_model: string | null;
  combined: string;
  created_at: string;
  answers?: CompareAnswer[];
}

export interface CompareEvent {
  type: "run" | "delta" | "answer_done" | "done" | "error";
  run_id: number;
  model?: string;
  content?: string;
  status?: string;
  answer_id?: number;
  input_tokens?: number;
  output_tokens?: number;
  token_method?: string;
  tokens_per_second?: number | null;
  cost_eur?: number;
  error?: string;
  code?: string;
  message?: string;
}

// ── Research Mode ──────────────────────────────────────────────────
export interface ResearchSource {
  title: string;
  url: string;
  snippet: string;
  excerpt?: string;
}

export interface ResearchRun {
  id: number;
  query: string;
  status: string;
  result: string;
  notes: string;
  summary: string;
  comparison: string;
  project_id: number | null;
  created_at: string;
  sources: ResearchSource[];
}

export interface ResearchEvent {
  type: "run" | "status" | "source" | "summary" | "done" | "error";
  research_id: number;
  status?: string;
  message?: string;
  index?: number;
  title?: string;
  url?: string;
  snippet?: string;
  summary?: string;
  sources?: number;
  code?: string;
}

// ── Projects ───────────────────────────────────────────────────────
export interface Project {
  id: number;
  name: string;
  description: string;
  root_path: string | null;
  status: string;
  settings: Record<string, unknown>;
  task_count: number;
  chat_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectFile {
  path: string;
  name: string;
  size_bytes: number | null;
  mime: string | null;
}

export interface ProjectTask {
  id: number;
  project_id: number | null;
  title: string;
  description: string;
  status: string;
  created_at: string;
}

export interface ProjectDetail extends Project {
  files: ProjectFile[];
  tasks: ProjectTask[];
  conversations: Array<{ id: string; title: string; updated_at: string }>;
}

// ── Git / GitHub ───────────────────────────────────────────────────
export interface GitStatus {
  ok: boolean;
  is_repo?: boolean;
  path?: string;
  branch?: string;
  clean?: boolean;
  changes?: number;
  porcelain?: string[];
  detail?: string;
  error?: string | null;
}

export interface GithubState {
  authenticated: boolean;
  message?: string;
  login?: string;
  name?: string;
  repositories?: Array<{ full_name: string; html_url: string; private: boolean; default_branch: string; description: string | null }>;
  issues?: Array<{ number: number; title: string; state: string; html_url: string; user: string | null }>;
  pulls?: Array<{ number: number; title: string; html_url: string; head: string | null; base: string | null; user: string | null }>;
  error?: string;
}
