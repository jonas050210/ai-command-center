// Agent Mode — real model→tool loop with human approval gates.
// Timeline: streamed SSE events rendered as model text, tool calls,
// approval cards (with the exact diff being approved) and tool results.
// History + audit log come from the backend; nothing is simulated.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON, streamSSE, ApiError } from "../api";
import { useStore } from "../store";
import type {
  AgentApprovalRow, AgentEvent, AgentRunRow, AgentStepRow, AgentToolInfo,
  ExecutionRow, ProjectRow,
} from "../types";
import { cx, formatNumber, timeAgo } from "../utils";
import {
  AlertIcon, BotIcon, ClockIcon, ShieldIcon, StopIcon, TerminalIcon,
} from "../icons";
import {
  ApprovalCard, ToolCallCard, dangerChip, statusChip,
} from "./agentUi";

// ── timeline entries (UI-side folding of the event stream) ──────────
type Entry =
  | { key: string; kind: "text"; step: number; text: string }
  | { key: string; kind: "note"; level: string; text: string }
  | { key: string; kind: "tool"; callId: string; tool: string; args: Record<string, unknown>;
      status: "running" | "ok" | "error"; output?: string; diff?: string | null;
      danger: string; ms?: number }
  | { key: string; kind: "approval"; id: string; tool: string;
      args: Record<string, unknown>; preview: string | null; danger: string;
      status: "pending" | "approved" | "denied" | "expired" };

let entrySeq = 1;

export function AgentView() {
  const { models, settings, currentModel, refreshCosts, refreshTokens, notify } = useStore();
  const [task, setTask] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const [finalResult, setFinalResult] = useState<string>("");
  const [usage, setUsage] = useState<{ input: number; output: number; steps: number; elapsed: number } | null>(null);
  const [streamError, setStreamError] = useState<{ code: string; message: string } | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [caps, setCaps] = useState<Record<string, boolean> | null>(null);
  const [memCtx, setMemCtx] = useState<{ memory_count: number; agent_md: boolean } | null>(null);
  const [toolInfos, setToolInfos] = useState<AgentToolInfo[]>([]);
  const [runs, setRuns] = useState<AgentRunRow[]>([]);
  const [detail, setDetail] = useState<{ run: AgentRunRow; steps: AgentStepRow[]; approvals: AgentApprovalRow[] } | null>(null);
  const [executions, setExecutions] = useState<ExecutionRow[] | null>(null);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const effectiveModel = model ?? currentModel ?? settings?.default_model ?? "";
  const modelCaps = models.find((m) => m.name === effectiveModel)?.capabilities ?? [];
  const modelHasTools = modelCaps.includes("tools");

  const refreshRuns = useCallback(async () => {
    try {
      const data = await getJSON<{ runs: AgentRunRow[] }>("/api/agent/runs");
      setRuns(data.runs);
    } catch { /* keep old */ }
  }, []);

  // boot data
  useEffect(() => {
    getJSON<{ capabilities: Record<string, boolean> }>("/api/agent/capabilities")
      .then((d) => setCaps(d.capabilities)).catch(() => undefined);
    getJSON<{ memory_count: number; agent_md: boolean }>("/api/memory/context")
      .then(setMemCtx).catch(() => undefined);
    getJSON<{ tools: AgentToolInfo[] }>("/api/agent/tools")
      .then((d) => setToolInfos(d.tools)).catch(() => undefined);
    getJSON<{ projects: ProjectRow[] }>("/api/projects")
      .then((d) => {
        setProjects(d.projects);
        // "Agent here" jump from ProjectsView
        const wanted = sessionStorage.getItem("aicc.agentProject");
        if (wanted) {
          sessionStorage.removeItem("aicc.agentProject");
          const found = d.projects.find((p) => String(p.id) === wanted);
          if (found) setProjectId(found.id);
        }
      }).catch(() => undefined);
    void refreshRuns();
  }, [refreshRuns]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries, finalStatus]);

  // ── stream consumer ────────────────────────────────────────────────
  const run = useCallback(async () => {
    const text = task.trim();
    if (!text || running) return;
    setRunning(true);
    setFinalStatus(null);
    setFinalResult("");
    setUsage(null);
    setStreamError(null);
    setDetail(null);
    const controller = new AbortController();
    abortRef.current = controller;

    // fold the stream into timeline entries
    const upsert = (fn: (list: Entry[]) => Entry[]) => setEntries((list) => fn(list));
    let textKey: string | null = null;   // current per-step model text entry

    const onEvent = (ev: AgentEvent) => {
      if (ev.type === "meta") {
        setRunId(ev.run_id);
      } else if (ev.type === "note") {
        upsert((l) => [...l, { key: `n${entrySeq++}`, kind: "note", level: ev.level, text: ev.message }]);
      } else if (ev.type === "step") {
        textKey = null;
      } else if (ev.type === "delta") {
        if (textKey === null) {
          textKey = `t${entrySeq++}`;
          const k = textKey;
          upsert((l) => [...l, { key: k, kind: "text", step: ev.step, text: ev.content }]);
        } else {
          const k = textKey;
          upsert((l) => l.map((e) => (e.key === k && e.kind === "text")
            ? { ...e, text: e.text + ev.content } : e));
        }
      } else if (ev.type === "tool_call") {
        upsert((l) => [...l, { key: `tc-${ev.call_id}`, kind: "tool", callId: ev.call_id,
          tool: ev.tool, args: ev.args, status: "running", danger: "" }]);
      } else if (ev.type === "approval_required") {
        upsert((l) => [...l, { key: `ap-${ev.approval_id}`, kind: "approval",
          id: ev.approval_id, tool: ev.tool, args: ev.args, preview: ev.preview,
          danger: ev.danger, status: "pending" }]);
      } else if (ev.type === "approval_decided") {
        upsert((l) => l.map((e) => (e.kind === "approval" && e.status === "pending"
          && (ev.approval_id === null ? e.tool === ev.tool : e.id === ev.approval_id))
          ? { ...e, status: ev.status as Extract<Entry, { kind: "approval" }>["status"] } : e));
      } else if (ev.type === "tool_result") {
        upsert((l) => l.map((e) => (e.kind === "tool" && e.callId === ev.call_id)
          ? { ...e, status: ev.ok ? "ok" : "error", output: ev.error ?? ev.output,
            diff: ev.diff ?? null, danger: ev.danger, ms: ev.ms } : e));
      } else if (ev.type === "usage") {
        setUsage({ input: ev.input_tokens, output: ev.output_tokens,
          steps: ev.steps, elapsed: ev.elapsed_s });
      } else if (ev.type === "done") {
        setFinalStatus(ev.status);
        setFinalResult(ev.result ?? "");
        if (ev.error) setStreamError({ code: ev.status.toUpperCase(), message: ev.error });
      } else if (ev.type === "error") {
        setStreamError({ code: ev.code, message: ev.message });
      }
    };

    try {
      await streamSSE("/api/agent/runs", {
        task: text, model: effectiveModel || undefined,
        project_id: projectId ?? undefined,
      }, onEvent, controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) {
        setStreamError((prev) => prev ?? (e instanceof ApiError
          ? { code: e.code, message: e.message }
          : { code: "NETWORK", message: "Lost connection to the backend." }));
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
      void refreshRuns();
      void refreshCosts();
      void refreshTokens();
    }
  }, [task, running, effectiveModel, projectId, refreshRuns, refreshCosts, refreshTokens]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    if (runId) {
      sendJSON("POST", `/api/agent/runs/${runId}/stop`).catch(() => undefined);
    }
  }, [runId]);

  const decide = useCallback(async (approvalId: string, approve: boolean) => {
    setDeciding(true);
    try {
      const r = await sendJSON<{ status: string }>(
        "POST", `/api/agent/approvals/${approvalId}`, { approve });
      setEntries((l) => l.map((e) => (e.kind === "approval" && e.id === approvalId)
        ? { ...e, status: r.status as Extract<Entry, { kind: "approval" }>["status"] } : e));
      notify(approve ? "Action approved" : "Action denied — run stopping",
        approve ? "good" : "info");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "bad");
    } finally {
      setDeciding(false);
    }
  }, [notify]);

  const openDetail = useCallback(async (id: string) => {
    try {
      const d = await getJSON<{ run: AgentRunRow; steps: AgentStepRow[]; approvals: AgentApprovalRow[] }>(
        `/api/agent/runs/${id}`);
      setDetail(d);
      setEntries([]);
      setFinalStatus(null);
      setStreamError(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to load run", "bad");
    }
  }, [notify]);

  const toggleAudit = useCallback(async () => {
    if (executions !== null) { setExecutions(null); return; }
    try {
      const d = await getJSON<{ executions: ExecutionRow[] }>("/api/agent/executions");
      setExecutions(d.executions);
    } catch { notify("Failed to load audit log", "bad"); }
  }, [executions, notify]);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* header strip */}
      <div className="px-6 pt-4 pb-3 border-b border-line space-y-2.5">
        <div className="flex items-center gap-2.5 flex-wrap">
          <BotIcon className="w-[18px] h-[18px] text-accent" />
          <span className="text-[15px] font-bold">Agent Mode</span>
          <span className="chip chip-good !text-[9px]"><ShieldIcon className="w-3 h-3" /> sandboxed · human-gated</span>
          <div className="ml-auto flex items-center gap-2">
            <select className="input !w-auto !py-1 !px-2 !text-[11px] font-mono"
              value={effectiveModel} disabled={running || models.length === 0}
              onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={`${m.provider}/${m.name}`} value={m.name}>
                  {m.name}{m.capabilities.includes("tools") ? " · tools" : ""}
                </option>
              ))}
              {models.length === 0 && <option value="">no models synced</option>}
            </select>
            <select className="input !w-auto !py-1 !px-2 !text-[11px]"
              value={projectId ?? ""} disabled={running}
              title="Scope the run's file tools to a project directory"
              onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">workspace (global)</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>project: {p.name}</option>
              ))}
            </select>
            <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2.5"
              onClick={() => void refreshRuns()}>
              History ({runs.length})
            </button>
            <button className={cx("btn btn-ghost !text-[10.5px] !py-1 !px-2.5",
              executions !== null && "!text-accent")}
              onClick={() => void toggleAudit()}>
              Audit log
            </button>
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap text-[9.5px]">
          {caps && Object.entries({
            "read files": caps["filesystem:read"], "write files": caps["filesystem:write"],
            "run commands": caps["command:execute"], "network": caps["network:fetch"],
            "git": caps["git:operate"], "memory": caps["memory"],
          }).map(([label, on]) => (
            <span key={label} className={cx("chip !text-[9px] !py-[1px]",
              on ? "chip-good" : "opacity-50")}>
              {on ? "✓" : "✕"} {label}
            </span>
          ))}
          {memCtx && (memCtx.memory_count > 0 || memCtx.agent_md) && (
            <span className="chip chip-accent !text-[9px] !py-[1px]"
              title="Injected into the next run's prompt (Settings → Memory & skills)">
              context: {memCtx.memory_count} memories{memCtx.agent_md ? " + AGENT.md" : ""}
            </span>
          )}
          <span className="text-faint ml-1">— toggled in Settings → Agent permissions</span>
          {!modelHasTools && effectiveModel && (
            <span className="chip chip-warn !text-[9px] !py-[1px] ml-auto">
              {effectiveModel} doesn't advertise tool support — runs may fail
            </span>
          )}
        </div>
      </div>

      {streamError && (
        <div className="mx-6 mt-3 flex items-start gap-2.5 rounded-xl border px-4 py-3
          border-[rgba(248,113,113,0.4)] bg-[rgba(248,113,113,0.07)]">
          <AlertIcon className="w-4 h-4 text-bad mt-[1px] shrink-0" />
          <div className="text-[12.5px]">
            <div className="font-semibold text-bad">{streamError.code}</div>
            <div className="text-bad/80 whitespace-pre-wrap">{streamError.message}</div>
          </div>
        </div>
      )}

      {/* timeline */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0 py-3">
        {entries.length === 0 && !detail && runs.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center px-6 text-center anim-fade-up">
            <BotIcon className="w-10 h-10 text-accent opacity-80 mb-4" />
            <h1 className="text-[17px] font-bold">Give the agent a task</h1>
            <p className="text-[12px] text-dim mt-1.5 max-w-[480px]">
              The model plans, calls sandboxed tools, and asks for your approval with an
              exact diff before every write or command. Every execution is audit-logged.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-1.5 mt-4 max-w-[560px]">
              {toolInfos.map((t) => (
                <span key={t.name} className={dangerChip(t.danger)} title={t.description}>
                  {t.name}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* live timeline */}
        {entries.map((e) => {
          if (e.kind === "note") {
            return (
              <div key={e.key} className={cx("mx-6 my-1.5 text-[10.5px] flex items-start gap-1.5",
                e.level === "warn" ? "text-warn" : "text-faint")}>
                <AlertIcon className="w-3 h-3 mt-[1px] shrink-0" /> {e.text}
              </div>
            );
          }
          if (e.kind === "text") {
            return (
              <div key={e.key} className="mx-6 my-2 flex justify-start">
                <div className="max-w-[86%] rounded-2xl rounded-tl-md glass-soft px-4 py-2.5 text-[13px] whitespace-pre-wrap leading-relaxed">
                  {e.text}
                </div>
              </div>
            );
          }
          if (e.kind === "tool") {
            return (
              <div key={e.key} className="mx-6">
                <ToolCallCard tool={e.tool} args={e.args} status={e.status}
                  output={e.output} diff={e.diff} danger={e.danger} ms={e.ms}
                  callId={e.callId} />
              </div>
            );
          }
          return (
            <div key={e.key} className="mx-6">
              <ApprovalCard entry={e} onDecide={decide} deciding={deciding} />
            </div>
          );
        })}

        {/* final status */}
        {finalStatus && !detail && (
          <div className="mx-6 my-3 glass-soft rounded-xl border border-line p-3.5 anim-fade-up">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[12px] font-bold">Run {finalStatus}</span>
              <span className={statusChip(finalStatus)}>{finalStatus.toUpperCase()}</span>
              {usage && (
                <span className="text-[10px] text-faint ml-auto flex items-center gap-1.5">
                  <ClockIcon className="w-3 h-3" /> {usage.elapsed}s · {usage.steps} steps ·
                  {" "}{formatNumber(usage.input)} in / {formatNumber(usage.output)} out tokens
                </span>
              )}
            </div>
            {finalResult && finalStatus !== "complete" && (
              <div className="text-[11.5px] text-dim mt-2 whitespace-pre-wrap">{finalResult}</div>
            )}
          </div>
        )}

        {/* run detail (read-only history) */}
        {detail && (
          <div className="mx-6 my-2 space-y-2 anim-fade-up">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-bold truncate">{detail.run.task}</span>
              <span className={statusChip(detail.run.status)}>{detail.run.status}</span>
              <span className="text-[9.5px] text-faint ml-auto">{timeAgo(detail.run.created_at)}</span>
              <button className="btn btn-ghost !text-[10px] !py-0.5 !px-2" onClick={() => setDetail(null)}>close</button>
            </div>
            {detail.steps.map((s) => (
              <div key={s.id} className="glass-soft rounded-lg border border-line px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="chip !text-[8.5px]">{s.kind}</span>
                  <span className="text-[9.5px] text-faint ml-auto">step {s.step}</span>
                </div>
                {s.content && (
                  <pre className="text-[10.5px] text-dim whitespace-pre-wrap mt-1.5 max-h-[160px] overflow-auto">{s.content}</pre>
                )}
              </div>
            ))}
            {detail.approvals.length > 0 && (
              <div className="text-[10.5px] text-faint px-1">
                Approvals: {detail.approvals.map((a) => `${a.tool} → ${a.status}`).join(" · ")}
              </div>
            )}
          </div>
        )}

        {/* run history (when idle) */}
        {!detail && entries.length === 0 && runs.length > 0 && (
          <div className="mx-6 my-2 space-y-1.5">
            <div className="micro-label px-1 pb-1">Recent runs</div>
            {runs.map((r) => (
              <button key={r.id} onClick={() => void openDetail(r.id)}
                className="w-full text-left glass-soft rounded-lg border border-line px-3 py-2.5 hover:border-[rgba(69,227,255,0.3)] transition-colors">
                <div className="flex items-center gap-2">
                  <span className={statusChip(r.status)}>{r.status}</span>
                  <span className="text-[12px] text-ink font-medium truncate flex-1">{r.task}</span>
                  <span className="text-[9.5px] text-faint shrink-0">{timeAgo(r.created_at)}</span>
                </div>
                <div className="text-[9.5px] text-faint mt-1">
                  {r.model ?? "?"} · {r.steps} steps · {formatNumber(r.input_tokens + r.output_tokens)} tokens
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* audit log overlay */}
      {executions !== null && (
        <div className="border-t border-line max-h-[220px] overflow-y-auto px-6 py-2.5">
          <div className="micro-label pb-1.5">Security audit log — every tool execution, denial and failure</div>
          {executions.length === 0 && <div className="text-[10.5px] text-faint py-2">No executions yet.</div>}
          {executions.map((x) => (
            <div key={x.id} className="flex items-start gap-2 py-1 border-b border-line/40 last:border-0">
              <span className={statusChip(x.status)}>{x.status}</span>
              <span className="font-mono text-[10.5px] text-accent shrink-0">{x.kind}</span>
              <span className="font-mono text-[10px] text-faint truncate flex-1">{x.command ?? ""}</span>
              <span className="text-[9px] text-faint shrink-0">{timeAgo(x.started_at)}</span>
            </div>
          ))}
        </div>
      )}

      {/* composer */}
      <div className="border-t border-line p-4">
        <div className="glass-soft rounded-2xl border border-line2 focus-within:border-[rgba(69,227,255,0.45)] transition-colors">
          <textarea
            className="w-full bg-transparent resize-none outline-none px-4 pt-3 text-[13.5px] min-h-[64px] max-h-[180px]"
            placeholder="Describe the task — e.g. ‘Create a Python script in the workspace that prints prime numbers, then run it.’"
            value={task}
            disabled={running}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void run(); }
            }}
          />
          <div className="flex items-center gap-2 px-3 pb-2.5">
            <span className="text-[9.5px] text-faint flex items-center gap-1.5">
              <TerminalIcon className="w-3 h-3" />
              Enter to run · files stay inside the sandboxed workspace
            </span>
            {running ? (
              <button className="btn btn-danger !text-[11.5px] !py-1.5 !px-3.5 ml-auto" onClick={stop}>
                <StopIcon className="w-3.5 h-3.5" /> Stop run
              </button>
            ) : (
              <button className="btn btn-accent !text-[11.5px] !py-1.5 !px-3.5 ml-auto"
                disabled={!task.trim() || models.length === 0} onClick={() => void run()}>
                <BotIcon className="w-3.5 h-3.5" /> Run agent
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
