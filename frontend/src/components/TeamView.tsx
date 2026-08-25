// Team Mode — a small pipeline of models: planner(s) → executor → reviewer(s).
// Strictly sequential (VRAM-safe). Executor members run the REAL agent
// loop: their tool calls/approvals stream through member_event wrappers
// and reuse the shared approval cards.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON, streamSSE, ApiError } from "../api";
import { useStore } from "../store";
import { cx, formatNumber, timeAgo } from "../utils";
import {
  AlertIcon, BotIcon, PlusIcon, SendIcon, StopIcon, TrashIcon, UsersIcon,
} from "../icons";
import { ApprovalCard, ToolCallCard, statusChip } from "./agentUi";

// ── types mirroring backend/team ──────────────────────────────────────
interface TeamMember {
  id: number;
  role: "planner" | "executor" | "reviewer";
  model: string;
  provider: string | null;
  responsibility: string;
  input_tokens: number;
  output_tokens: number;
}

interface Team {
  id: number;
  name: string;
  status: string;
  members: TeamMember[];
  created_at: string;
}

interface TeamRunRow {
  id: string;
  task: string;
  status: string;
  verdict: string | null;
  revision_used: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
  executor_run_id: string | null;
}

type TeamEvent =
  | { type: "team_meta"; run_id: string; team_id: number; team: string;
      members: Array<{ index: number; role: string; model: string; provider: string | null; responsibility: string }> }
  | { type: "member_start"; index: number; role: string; model: string }
  | { type: "member_delta"; index: number; content: string }
  | { type: "member_done"; index: number; role: string; status: string;
      input_tokens: number; output_tokens: number; result: string; error?: string | null }
  | { type: "member_event"; index: number; event: Agentish }
  | { type: "verdict"; verdict: string | null; message: string }
  | { type: "note"; level: string; message: string }
  | { type: "usage"; input_tokens: number; output_tokens: number; elapsed_s: number }
  | { type: "team_done"; run_id: string; status: string; plan: string; result: string;
      review: string; verdict: string | null; revision_used: number;
      executor_run_id: string | null; error: string | null; elapsed_s: number }
  | { type: "error"; code: string; message: string };

interface Agentish {
  type: string; run_id?: string; content?: string; call_id?: string;
  tool?: string; args?: Record<string, unknown>; preview?: string | null;
  danger?: string; approval_id?: string; status?: string; ok?: boolean;
  output?: string; diff?: string | null; error?: string; ms?: number;
  level?: string; message?: string; step?: number; model?: string;
}

// executor card: engine events folded per call/approval id
interface ExecItem {
  key: string;
  kind: "text" | "tool" | "approval";
  text?: string;
  callId?: string; tool?: string; args?: Record<string, unknown>;
  status?: string; output?: string; diff?: string | null; danger?: string;
  ms?: number; id?: string; preview?: string | null;
}

interface MemberState {
  index: number;
  role: string;
  model: string;
  status: "waiting" | "running" | "complete" | "error" | "stopped" | "denied";
  text: string;
  inTok: number; outTok: number;
  execItems: ExecItem[];
}

const roleChip = (role: string) => (
  role === "executor" ? "chip !text-[9px] chip-good"
    : role === "planner" ? "chip !text-[9px]" : "chip !text-[9px] chip-warn"
);

function TeamBuilder({ onCreated }: { onCreated: () => void }) {
  const { models, notify } = useStore();
  const [name, setName] = useState("");
  const [members, setMembers] = useState<Array<{ role: string; model: string; responsibility: string }>>([
    { role: "planner", model: "", responsibility: "" },
    { role: "executor", model: "", responsibility: "" },
    { role: "reviewer", model: "", responsibility: "" },
  ]);
  const [busy, setBusy] = useState(false);

  const setMember = (i: number, patch: Partial<{ role: string; model: string; responsibility: string }>) =>
    setMembers((m) => m.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  const create = async () => {
    setBusy(true);
    try {
      await sendJSON("POST", "/api/team", {
        name, members: members.map((m) => ({
          role: m.role, model: m.model, responsibility: m.responsibility,
        })),
      });
      notify("Team created", "good");
      onCreated();
    } catch (e) {
      notify(e instanceof ApiError ? `${e.code}: ${e.message}` : "Failed to create team", "bad");
    } finally {
      setBusy(false);
    }
  };

  const valid = name.trim().length > 0
    && members.filter((m) => m.role === "executor").length === 1
    && members.every((m) => m.model.trim().length > 0);

  return (
    <div className="glass-soft rounded-xl border border-[rgba(69,227,255,0.3)] p-4 space-y-3 anim-fade-up">
      <div className="flex items-center gap-2">
        <UsersIcon className="w-4 h-4 text-accent" />
        <span className="text-[13px] font-bold">New team</span>
        <span className="text-[9.5px] text-faint ml-auto">2–4 members · exactly one executor</span>
      </div>
      <input className="input" placeholder="Team name (e.g. Code Review Cell)" value={name}
        onChange={(e) => setName(e.target.value)} />
      {members.map((m, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <select className="input !w-[110px] !text-[11px]" value={m.role}
            onChange={(e) => setMember(i, { role: e.target.value })}>
            <option value="planner">planner</option>
            <option value="executor">executor</option>
            <option value="reviewer">reviewer</option>
          </select>
          <select className="input flex-1 !text-[11px] font-mono" value={m.model}
            onChange={(e) => setMember(i, { model: e.target.value })}>
            <option value="">model…</option>
            {models.map((mm) => (
              <option key={`${mm.provider}/${mm.name}`} value={mm.name}>
                {mm.name}
              </option>
            ))}
          </select>
          <input className="input flex-1 !text-[11px]" placeholder="responsibility (optional)"
            value={m.responsibility}
            onChange={(e) => setMember(i, { responsibility: e.target.value })} />
          <button className="icon-btn !w-6 !h-6" disabled={members.length <= 2}
            onClick={() => setMembers((cur) => cur.filter((_, j) => j !== i))}>
            <TrashIcon className="w-3 h-3" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <button className="btn btn-ghost !text-[10.5px]" disabled={members.length >= 4}
          onClick={() => setMembers((m) => [...m, { role: "planner", model: "", responsibility: "" }])}>
          <PlusIcon className="w-3 h-3" /> Add member
        </button>
        <button className="btn btn-accent !text-[11px] !py-1.5 !px-3 ml-auto"
          disabled={!valid || busy} onClick={() => void create()}>
          {busy ? "Creating…" : "Create team"}
        </button>
      </div>
    </div>
  );
}

export function TeamView() {
  const { notify, refreshCosts, refreshTokens } = useStore();
  const [teams, setTeams] = useState<Team[]>([]);
  const [activeTeam, setActiveTeam] = useState<number | null>(null);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [members, setMembers] = useState<MemberState[]>([]);
  const [verdict, setVerdict] = useState<{ verdict: string | null; message: string } | null>(null);
  const [final, setFinal] = useState<{ status: string; plan: string; result: string; review: string; revision: number; elapsed: number } | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [runs, setRuns] = useState<TeamRunRow[]>([]);
  const [deciding, setDeciding] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const refreshTeams = useCallback(async () => {
    try {
      const d = await getJSON<{ teams: Team[] }>("/api/team");
      setTeams(d.teams);
      setActiveTeam((cur) => cur ?? d.teams[0]?.id ?? null);
    } catch { /* keep old */ }
  }, []);

  const refreshRuns = useCallback(async (teamId: number | null) => {
    if (!teamId) { setRuns([]); return; }
    try {
      const d = await getJSON<{ runs: TeamRunRow[] }>(`/api/team/${teamId}`);
      setRuns(d.runs ?? []);
    } catch { /* keep old */ }
  }, []);

  useEffect(() => { void refreshTeams(); }, [refreshTeams]);
  useEffect(() => { void refreshRuns(activeTeam); }, [activeTeam, refreshRuns]);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [members, notes]);

  const patchMember = useCallback((index: number, fn: (m: MemberState) => MemberState) => {
    setMembers((cur) => cur.map((m) => (m.index === index ? fn(m) : m)));
  }, []);

  const decide = useCallback(async (approvalId: string, approve: boolean) => {
    setDeciding(true);
    try {
      const r = await sendJSON<{ status: string }>(
        "POST", `/api/agent/approvals/${approvalId}`, { approve });
      setMembers((cur) => cur.map((m) => ({ ...m, execItems: m.execItems.map((it) =>
        it.kind === "approval" && it.id === approvalId ? { ...it, status: r.status } : it) })));
      notify(approve ? "Action approved" : "Denied — run stopping", approve ? "good" : "info");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "bad");
    } finally {
      setDeciding(false);
    }
  }, [notify]);

  const run = useCallback(async () => {
    if (running || !activeTeam || !task.trim()) return;
    setRunning(true);
    setFinal(null);
    setVerdict(null);
    setNotes([]);
    setRunError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    let itemSeq = 1;
    const textKeyOf: Record<number, string> = {};

    try {
      await streamSSE(`/api/team/${activeTeam}/runs`, { task: task.trim() },
        (ev: TeamEvent) => {
          const inner = ev.type === "member_event" ? ev.event : null;
          if (ev.type === "team_meta") {
            setRunId(ev.run_id);
            setMembers(ev.members.map((m) => ({
              index: m.index, role: m.role, model: m.model,
              status: "waiting", text: "", inTok: 0, outTok: 0, execItems: [],
            })));
          } else if (ev.type === "member_start") {
            patchMember(ev.index, (m) => ({ ...m, status: "running" }));
          } else if (ev.type === "member_delta") {
            patchMember(ev.index, (m) => ({ ...m, text: m.text + ev.content }));
          } else if (ev.type === "member_done") {
            patchMember(ev.index, (m) => ({ ...m,
              status: ev.status as MemberState["status"],
              inTok: ev.input_tokens, outTok: ev.output_tokens }));
          } else if (ev.type === "verdict") {
            setVerdict({ verdict: ev.verdict, message: ev.message });
          } else if (ev.type === "note") {
            setNotes((n) => [...n.slice(-4), ev.message]);
          } else if (ev.type === "member_event" && inner) {
            // engine events of the executor run
            if (inner.type === "delta") {
              const k = textKeyOf[ev.index] ?? `x${itemSeq++}`;
              textKeyOf[ev.index] = k;
              patchMember(ev.index, (m) => {
                const items = [...m.execItems];
                const pos = items.findIndex((x) => x.key === k);
                if (pos === -1) items.push({ key: k, kind: "text", text: inner.content ?? "" });
                else items[pos] = { ...items[pos], text: (items[pos].text ?? "") + (inner.content ?? "") };
                return { ...m, execItems: items };
              });
            } else if (inner.type === "step") {
              delete textKeyOf[ev.index];
            } else if (inner.type === "tool_call") {
              const k = `c-${inner.call_id}`;
              patchMember(ev.index, (m) => ({
                ...m, execItems: [...m.execItems, { key: k, kind: "tool",
                  callId: inner.call_id, tool: inner.tool, args: inner.args,
                  status: "running", danger: "" }],
              }));
            } else if (inner.type === "approval_required") {
              patchMember(ev.index, (m) => ({
                ...m, execItems: [...m.execItems, { key: `a-${inner.approval_id}`,
                  kind: "approval", id: inner.approval_id, tool: inner.tool,
                  args: inner.args, preview: inner.preview ?? null,
                  danger: inner.danger ?? "write", status: "pending" }],
              }));
            } else if (inner.type === "approval_decided") {
              patchMember(ev.index, (m) => ({
                ...m, execItems: m.execItems.map((it) =>
                  (it.kind === "approval" && it.status === "pending"
                    && (inner.approval_id === null || inner.approval_id === undefined
                      ? it.tool === inner.tool : it.id === inner.approval_id))
                    ? { ...it, status: inner.status } : it),
              }));
            } else if (inner.type === "tool_result") {
              patchMember(ev.index, (m) => ({
                ...m, execItems: m.execItems.map((it) =>
                  (it.kind === "tool" && it.callId === inner.call_id)
                    ? { ...it, status: inner.ok ? "ok" : "error",
                        output: inner.error ?? inner.output, diff: inner.diff ?? null,
                        danger: inner.danger ?? "", ms: inner.ms } : it),
              }));
            } else if (inner.type === "note") {
              setNotes((n) => [...n.slice(-4), inner.message ?? ""]);
            }
          } else if (ev.type === "team_done") {
            setFinal({ status: ev.status, plan: ev.plan, result: ev.result,
              review: ev.review, revision: ev.revision_used, elapsed: ev.elapsed_s });
            if (ev.error) setRunError(ev.error);
          } else if (ev.type === "error") {
            setRunError(`${ev.code}: ${ev.message}`);
          }
        }, controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) {
        setRunError(e instanceof ApiError ? `${e.code}: ${e.message}`
          : "Lost connection to the backend.");
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
      void refreshRuns(activeTeam);
      void refreshCosts();
      void refreshTokens();
    }
  }, [running, activeTeam, task, patchMember, refreshRuns, refreshCosts, refreshTokens, notify]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    if (runId) {
      sendJSON("POST", `/api/team/runs/${runId}/stop`).catch(() => undefined);
    }
  }, [runId]);

  const currentTeam = teams.find((t) => t.id === activeTeam) ?? null;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-6 pt-4 pb-3 border-b border-line space-y-2.5">
        <div className="flex items-center gap-2.5 flex-wrap">
          <UsersIcon className="w-[18px] h-[18px] text-accent" />
          <span className="text-[15px] font-bold">Team Mode</span>
          <span className="text-[9.5px] text-faint">planner → executor → reviewer · strictly sequential (VRAM-safe)</span>
          <div className="ml-auto flex items-center gap-2">
            <select className="input !w-auto !py-1 !px-2 !text-[11px]"
              value={activeTeam ?? ""} disabled={running}
              onChange={(e) => setActiveTeam(e.target.value ? Number(e.target.value) : null)}>
              {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              {teams.length === 0 && <option value="">no teams yet</option>}
            </select>
            <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2.5"
              onClick={() => setBuilderOpen((v) => !v)}>
              <PlusIcon className="w-3 h-3" /> Team
            </button>
          </div>
        </div>
        {currentTeam && (
          <div className="flex items-center gap-1.5 flex-wrap text-[9.5px]">
            {currentTeam.members.map((m) => (
              <span key={m.id} className={roleChip(m.role)}>
                {m.role}: <span className="font-mono ml-1">{m.model}</span>
                <span className="opacity-60 ml-1">{formatNumber(m.input_tokens + m.output_tokens)} tok</span>
              </span>
            ))}
          </div>
        )}
        {builderOpen && <TeamBuilder onCreated={() => { setBuilderOpen(false); void refreshTeams(); }} />}
      </div>

      {runError && (
        <div className="mx-6 mt-3 flex items-start gap-2.5 rounded-xl border px-4 py-3
          border-[rgba(248,113,113,0.4)] bg-[rgba(248,113,113,0.07)]">
          <AlertIcon className="w-4 h-4 text-bad mt-[1px] shrink-0" />
          <div className="text-[12.5px] text-bad/90 whitespace-pre-wrap">{runError}</div>
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0 py-3 space-y-3 px-6">
        {members.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center anim-fade-up">
            <UsersIcon className="w-10 h-10 text-accent opacity-80 mb-4" />
            <h1 className="text-[17px] font-bold">Assemble a team</h1>
            <p className="text-[12px] text-dim mt-1.5 max-w-[460px]">
              A planner breaks the task down, the executor executes it with the full
              tool gateway (your approvals included), and a reviewer accepts it or
              requests exactly one revision. Members run one at a time.
            </p>
          </div>
        )}

        {members.map((m) => (
          <div key={m.index} className="glass-soft rounded-xl border border-line">
            <div className="px-3.5 py-2 border-b border-line flex items-center gap-2 flex-wrap">
              <span className={roleChip(m.role)}>{m.role}</span>
              <span className="font-mono text-[11.5px] font-semibold">{m.model}</span>
              <span className={statusChip(m.status)}>{m.status}</span>
              {(m.inTok > 0 || m.outTok > 0) && (
                <span className="text-[9px] text-faint ml-auto">
                  {formatNumber(m.inTok)} in / {formatNumber(m.outTok)} out
                </span>
              )}
            </div>
            <div className="px-3.5 py-2.5">
              {m.role === "executor"
                ? (m.execItems.length === 0
                    ? (m.status === "running"
                        ? <div className="text-[10.5px] text-faint">Agent starting…</div>
                        : <div className="text-[10.5px] text-faint">Waiting…</div>)
                    : m.execItems.map((it) => {
                        if (it.kind === "text") {
                          return (
                            <div key={it.key} className="my-1.5 text-[12.5px] whitespace-pre-wrap">{it.text}</div>
                          );
                        }
                        if (it.kind === "tool") {
                          return (
                            <ToolCallCard key={it.key} tool={it.tool ?? "?"}
                              args={it.args ?? {}} status={it.status ?? "running"}
                              output={it.output} diff={it.diff} danger={it.danger ?? ""}
                              ms={it.ms} callId={it.callId} />
                          );
                        }
                        return (
                          <ApprovalCard key={it.key}
                            entry={{ id: it.id ?? "", tool: it.tool ?? "?",
                              args: it.args ?? {}, preview: it.preview ?? null,
                              danger: it.danger ?? "write",
                              status: it.status ?? "pending" }}
                            onDecide={decide} deciding={deciding} />
                        );
                      }))
                : (m.text
                    ? <div className="text-[12.5px] whitespace-pre-wrap leading-relaxed">{m.text}</div>
                    : <div className="text-[10.5px] text-faint">
                        {m.status === "running" ? "Thinking…" : "Waiting…"}
                      </div>)}
            </div>
          </div>
        ))}

        {notes.map((n, i) => (
          <div key={i} className="text-[10.5px] text-faint flex items-start gap-1.5">
            <AlertIcon className="w-3 h-3 mt-[1px] shrink-0" /> {n}
          </div>
        ))}

        {verdict && (
          <div className={cx("rounded-xl border p-3 text-[12px] anim-fade-up",
            verdict.verdict === "accepted"
              ? "border-[rgba(52,211,153,0.4)] bg-[rgba(52,211,153,0.06)] text-good"
              : "border-[rgba(251,191,36,0.4)] bg-[rgba(251,191,36,0.06)] text-warn")}>
            {verdict.message}
          </div>
        )}

        {final && (
          <div className="glass-soft rounded-xl border border-line p-3.5 anim-fade-up space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[12px] font-bold">Team run {final.status}</span>
              <span className={statusChip(final.status)}>{final.status.toUpperCase()}</span>
              {final.revision > 0 && <span className="chip !text-[9px]">{final.revision} revision</span>}
              <span className="text-[9.5px] text-faint ml-auto">{final.elapsed}s</span>
            </div>
          </div>
        )}

        {members.length === 0 && runs.length > 0 && (
          <div className="space-y-1.5">
            <div className="micro-label px-1 pb-1">Recent team runs</div>
            {runs.map((r) => (
              <div key={r.id} className="glass-soft rounded-lg border border-line px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <span className={statusChip(r.status)}>{r.status}</span>
                  {r.verdict && <span className={statusChip(r.verdict)}>{r.verdict}</span>}
                  <span className="text-[12px] text-ink font-medium truncate flex-1">{r.task}</span>
                  <span className="text-[9.5px] text-faint shrink-0">{timeAgo(r.created_at)}</span>
                </div>
                <div className="text-[9.5px] text-faint mt-1">
                  {formatNumber(r.input_tokens + r.output_tokens)} tokens
                  {r.revision_used > 0 ? " · 1 revision" : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-line p-4">
        <div className="glass-soft rounded-2xl border border-line2 focus-within:border-[rgba(69,227,255,0.45)] transition-colors">
          <textarea
            className="w-full bg-transparent resize-none outline-none px-4 pt-3 text-[13.5px] min-h-[56px] max-h-[160px]"
            placeholder={activeTeam ? "Team objective — e.g. ‘Write and test a CSV parser in the workspace.’"
              : "Create a team first (top right)."}
            value={task} disabled={running || !activeTeam}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void run(); } }}
          />
          <div className="flex items-center gap-2 px-3 pb-2.5">
            <span className="text-[9.5px] text-faint flex items-center gap-1.5">
              <BotIcon className="w-3 h-3" /> executor approvals use the same cards as Agent Mode
            </span>
            {running ? (
              <button className="btn btn-danger !text-[11.5px] !py-1.5 !px-3.5 ml-auto" onClick={stop}>
                <StopIcon className="w-3.5 h-3.5" /> Stop team
              </button>
            ) : (
              <button className="btn btn-accent !text-[11.5px] !py-1.5 !px-3.5 ml-auto"
                disabled={!task.trim() || !activeTeam} onClick={() => void run()}>
                <SendIcon className="w-3.5 h-3.5" /> Run team
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
