// Agent Mode — controlled PLAN → EXECUTE → VERIFY → FIX → FINALIZE.
// Live SSE feed of stages, tool calls, results; run history and log.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, streamSSE } from "../api";
import { useStore } from "../store";
import type { AgentEvent, AgentRun, Project } from "../types";
import { formatNumber } from "../utils";
import { AlertIcon, BotIcon, PlayIcon } from "../icons";
import { Field, LogRow, Panel } from "./ui";

interface FeedItem { tag: string; text: string; tone?: string }

export function AgentView() {
  const { settings, models, refreshModels } = useStore();
  const [task, setTask] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [running, setRunning] = useState(false);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AgentRun | null>(null);
  const [tokens, setTokens] = useState({ input: 0, output: 0, cost: 0 });
  const scrollRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<AbortController | null>(null);

  const available = models.filter((m) => m.available);

  const loadRuns = useCallback(async () => {
    try {
      const d = await getJSON<{ runs: AgentRun[] }>("/api/agent/runs");
      setRuns(d.runs);
    } catch { /* offline */ }
  }, []);

  const loadProjects = useCallback(async () => {
    try {
      const d = await getJSON<{ projects: Project[] }>("/api/projects");
      setProjects(d.projects);
    } catch { /* offline */ }
  }, []);

  useEffect(() => { void loadRuns(); void loadProjects(); }, [loadRuns, loadProjects]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [feed]);

  const push = (tag: string, text: string, tone?: string) =>
    setFeed((f) => [...f.slice(-400), { tag, text, tone }]);

  const start = async () => {
    const body = task.trim();
    if (!body || running) return;
    setRunning(true);
    setFeed([]);
    setTokens({ input: 0, output: 0, cost: 0 });
    const controller = new AbortController();
    cancelRef.current = controller;
    try {
      await streamSSE<AgentEvent>("/api/agent/runs", {
        task: body, project_id: projectId, model: model ?? undefined,
      }, (ev) => {
        if (ev.type === "run") {
          setRunId(ev.run_id);
          push("run", `Agent run #${ev.run_id} · workspace ${ev.workspace}`, "chip-accent");
        } else if (ev.type === "stage") {
          push("stage", `${ev.stage}${ev.round ? ` (round ${ev.round})` : ""} — ${ev.status}`, "chip-violet");
        } else if (ev.type === "activity") {
          const tone = ev.kind === "error" ? "chip-bad" : ev.kind === "check" ? "chip-good" : "";
          push(ev.kind ?? "log", String(ev.content ?? ""), tone);
        } else if (ev.type === "tool_result") {
          push("result", String(ev.content ?? "").slice(0, 400));
        } else if (ev.type === "tokens") {
          setTokens({ input: ev.input ?? 0, output: ev.output ?? 0, cost: ev.cost ?? 0 });
        } else if (ev.type === "done") {
          push("done", `status: ${ev.status}${ev.summary ? ` — ${ev.summary}` : ""}`, ev.status === "delivered" ? "chip-good" : "chip-bad");
          setRunning(false);
        } else if (ev.type === "error") {
          push("error", `${ev.code}: ${ev.message}`, "chip-bad");
          setRunning(false);
        }
      }, controller.signal);
    } catch (e) {
      push("error", e instanceof Error ? e.message : "Agent run failed", "chip-bad");
      setRunning(false);
    } finally {
      cancelRef.current = null;
      setRunning(false);
      void loadRuns();
      void refreshModels();
    }
  };

  const stop = () => {
    cancelRef.current?.abort();
    setRunning(false);
    push("stop", "Stream aborted by user.", "chip-bad");
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
      <div>
        <h1 className="text-[17px] font-bold">Agent Mode</h1>
        <p className="text-[11.5px] text-faint mt-0.5">
          A real, controlled agent inside a sandbox workspace — PLAN · EXECUTE · VERIFY · FIX · FINALIZE.
          Path traversal, absolute paths, arbitrary commands and shell injection are blocked server-side.
        </p>
      </div>

      <Panel title="New agent run" sub="Task → plan → tool loop → verification → summary">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_220px_180px] gap-3">
          <Field label="Task">
            <textarea className="input resize-none h-[84px]" placeholder="e.g. Create a Python CLI that greets a name, with a pytest test."
              value={task} onChange={(e) => setTask(e.target.value)} />
          </Field>
          <Field label="Model">
            <select className="input cursor-pointer" value={model ?? settings?.default_model ?? ""}
              onChange={(e) => setModel(e.target.value || null)}>
              {available.map((m) => (
                <option key={`${m.provider}/${m.name}`} value={m.name}>{m.display_name}</option>
              ))}
              {available.length === 0 && <option>{models[0]?.name ?? "qwen3:0.6b"}</option>}
            </select>
          </Field>
          <Field label="Project workspace">
            <select className="input cursor-pointer" value={projectId ?? ""}
              onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Default workspace</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
        </div>
        <div className="flex items-center gap-2 mt-3">
          {running ? (
            <button className="btn btn-danger" onClick={stop}><AlertIcon className="w-3.5 h-3.5" /> Stop</button>
          ) : (
            <button className="btn btn-accent" disabled={!task.trim()} onClick={() => void start()}>
              <PlayIcon className="w-3.5 h-3.5" /> Start agent
            </button>
          )}
          <span className="text-[10px] text-faint">
            {running ? "Streaming…" : "Each model action is validated, sandboxed and audited."}
          </span>
          {runId && (
            <span className="chip chip-accent ml-auto">run #{runId} · {formatNumber(tokens.input + tokens.output)} tok · €{tokens.cost.toFixed(2)}</span>
          )}
        </div>
      </Panel>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-4">
        <Panel title="Live log" sub="Decisions, tool calls, results, checks — no chain-of-thought" className="min-h-[260px]">
          {feed.length === 0 ? (
            <div className="text-[11.5px] text-faint py-8 text-center">No activity yet. Start an agent run above.</div>
          ) : (
            <div ref={scrollRef} className="max-h-[420px] overflow-y-auto font-mono">
              {feed.map((f, i) => <LogRow key={i} tag={f.tag} text={f.text} tone={f.tone} />)}
            </div>
          )}
        </Panel>

        <Panel title="Runs" sub="Persisted agent runs" className="min-h-[260px]">
          {runs.length === 0 ? (
            <div className="text-[11.5px] text-faint py-6 text-center">No runs yet.</div>
          ) : (
            <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
              {detail && (
          <div className="glass-soft rounded-lg px-3 py-2.5 mb-2 border border-line">
            <div className="flex items-center gap-2">
              <span className="text-[11.5px] font-semibold text-ink">Run #{detail.id} — {detail.stage} · {detail.status}</span>
              <button className="icon-btn !w-6 !h-6 ml-auto" title="Close"
                onClick={() => setDetail(null)}>×</button>
            </div>
            <div className="text-[10px] text-faint mt-1">workspace: {detail.workspace}</div>
            {detail.plan && (
              <div className="mt-2">
                <div className="micro-label">Plan</div>
                <div className="text-[10.5px] text-dim font-mono whitespace-pre-wrap max-h-[140px] overflow-y-auto">{detail.plan}</div>
              </div>
            )}
            {detail.summary && (
              <div className="mt-2">
                <div className="micro-label">Summary</div>
                <div className="text-[10.5px] text-dim whitespace-pre-wrap">{detail.summary}</div>
              </div>
            )}
            {detail.error && <div className="text-[10.5px] text-bad mt-1">{detail.error}</div>}
            {(detail.steps?.length ?? 0) > 0 && (
              <div className="mt-2">
                <div className="micro-label">Steps · {detail.steps!.length}</div>
                <div className="max-h-[160px] overflow-y-auto space-y-0.5 mt-1">
                  {detail.steps!.map((s) => (
                    <div key={s.id} className="text-[10px] font-mono text-dim">
                      <span className={s.status === "error" ? "text-bad" : "text-good"}>[{s.stage}]</span>{" "}
                      {s.tool} {s.target ?? ""} — {s.summary.slice(0, 120)}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {runs.map((r) => (
                <button key={r.id} className="w-full text-left glass-soft rounded-lg px-3 py-2 hover:bg-hover"
                  onClick={() => void (async () => {
                    try {
                      setDetail(await getJSON<AgentRun>(`/api/agent/runs/${r.id}`));
                    } catch (e) { /* offline */ }
                  })()}>
                  <div className="flex items-center gap-2">
                    <BotIcon className="w-3.5 h-3.5 text-accent shrink-0" />
                    <span className="text-[12px] font-semibold text-ink truncate flex-1">{r.task.slice(0, 80)}</span>
                    <span className={`chip !text-[8.5px] ${r.status === "delivered" ? "chip-good" : r.status === "cancelled" ? "chip-warn" : r.status === "error" ? "chip-bad" : ""}`}>{r.status}</span>
                  </div>
                  <div className="text-[9.5px] text-faint mt-1">
                    #{r.id} · {r.stage} · {formatNumber(r.input_tokens + r.output_tokens)} tok · €{r.cost_eur.toFixed(2)}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
