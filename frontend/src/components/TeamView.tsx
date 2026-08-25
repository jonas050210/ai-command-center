// Team Mode — the flagship. 2–4 models: task → planning → master plan →
// roles → execution → review → fix → final review → delivery.
// Live phase/activity feed, task board, per-model token table + TEAM TOTAL.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON, streamSSE } from "../api";
import { useStore } from "../store";
import type { TeamEventStream, TeamRun, TeamTask } from "../types";
import { cx, formatNumber } from "../utils";
import { CheckIcon, PlayIcon, ShieldIcon, UsersIcon, XIcon } from "../icons";
import { Empty, Field, Panel } from "./ui";

const ROLES = ["", "Architect", "Developer", "QA Engineer", "Security Reviewer", "Researcher", "Documentation"];

export function TeamView() {
  const { models, notify } = useStore();
  const [task, setTask] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [teamId, setTeamId] = useState<number | null>(null);
  const [masterPlan, setMasterPlan] = useState("");
  const [deliverable, setDeliverable] = useState("");
  const [activity, setActivity] = useState<Array<{ actor: string; kind: string; content: string }>>([]);
  const [tasks, setTasks] = useState<TeamTask[]>([]);
  const [memberTokens, setMemberTokens] = useState<Array<{ model: string; role: string; input_tokens: number; output_tokens: number; total_tokens: number }>>([]);
  const [teamTotal, setTeamTotal] = useState<{ input_tokens: number; output_tokens: number; total_tokens: number; cost_eur: number } | null>(null);
  const [history, setHistory] = useState<TeamRun[]>([]);
  const cancelRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const available = models.filter((m) => m.available);

  const loadHistory = useCallback(async () => {
    try { setHistory((await getJSON<{ teams: TeamRun[] }>("/api/team/runs")).teams); } catch { /* offline */ }
  }, []);

  useEffect(() => { void loadHistory(); }, [loadHistory]);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activity]);

  const toggleModel = (name: string) => {
    if (selected.includes(name)) {
      setSelected((s) => s.filter((x) => x !== name));
    } else if (selected.length < 4) {
      setSelected((s) => [...s, name]);
    } else {
      notify("Teams support at most 4 models.", "bad");
    }
  };

  const loadBoard = useCallback(async (id: number) => {
    try {
      const d = await getJSON<{ board: Record<string, TeamTask[]>; tasks: TeamTask[] }>(`/api/team/runs/${id}/board`);
      setTasks(d.tasks);
    } catch { /* ignore */ }
  }, []);

  const start = async () => {
    if (!task.trim() || selected.length < 2 || running) return;
    setRunning(true);
    setActivity([]);
    setTasks([]);
    setMasterPlan("");
    setDeliverable("");
    setTeamId(null);
    setMemberTokens([]);
    setTeamTotal(null);
    const controller = new AbortController();
    cancelRef.current = controller;
    try {
      await streamSSE<TeamEventStream>("/api/team/runs", {
        task: task.trim(), models: selected,
        roles: Object.fromEntries(
          Object.entries(roles).filter(([m, v]) => v && selected.includes(m)),
        ) as Record<string, string>,
      }, (ev) => {
        if (ev.type === "team") {
          setTeamId(ev.team_id);
          void loadBoard(ev.team_id);
        } else if (ev.type === "phase") {
              if (ev.phase === "master_plan" && ev.content) setMasterPlan(ev.content);
          setActivity((a) => [...a.slice(-300), { actor: "system", kind: "phase", content: `${ev.phase} — ${ev.status}` }]);
        } else if (ev.type === "activity") {
          setActivity((a) => [...a.slice(-300), { actor: ev.actor ?? "system", kind: ev.kind ?? "log", content: String(ev.content ?? "") }]);

        } else if (ev.type === "tokens") {
          setMemberTokens(ev.members ?? []);
          setTeamTotal(ev.total ?? null);
        } else if (ev.type === "done") {
          setDeliverable(ev.deliverable ?? "");
          setRunning(false);
          setActivity((a) => [...a.slice(-300), { actor: "system", kind: "done", content: `DELIVERED — ${(ev.deliverable ?? "").slice(0, 300)}` }]);
        } else if (ev.type === "error") {
          notify(`${ev.code}: ${ev.message}`, "bad");
          setRunning(false);
        }
      }, controller.signal);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Team run failed", "bad");
      setRunning(false);
    } finally {
      cancelRef.current = null;
      setRunning(false);
      if (teamId) void loadBoard(teamId);
      void loadHistory();
    }
  };

  const openRun = async (id: number) => {
    try {
      const r = await getJSON<TeamRun>(`/api/team/runs/${id}`);
      setTeamId(id);
      setMasterPlan(r.master_plan);
      setDeliverable(r.deliverable);
      setActivity((r.events ?? []).map((e) => ({ actor: e.actor ?? "system", kind: e.kind, content: e.content })).reverse());
      setTasks(r.tasks ?? []);
      setMemberTokens((r.members ?? []).map((m) => ({
        model: m.model, role: m.role, input_tokens: m.input_tokens,
        output_tokens: m.output_tokens, total_tokens: m.input_tokens + m.output_tokens,
      })));
      setTeamTotal(r.tokens ?? null);

    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not load run", "bad");
    }
  };

  const moveTask = async (taskId: number, status: string) => {
    if (!teamId) return;
    try {
      await sendJSON("PATCH", `/api/team/runs/${teamId}/tasks/${taskId}`, { status });
      await loadBoard(teamId);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "bad");
    }
  };

  const boardCols: Array<[string, string]> = [["todo", "TODO"], ["in_progress", "IN PROGRESS"], ["review", "REVIEW"], ["done", "DONE"]];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
      <div className="flex items-start gap-3">
        <div>
          <h1 className="text-[17px] font-bold">Multi-Model Team</h1>
          <p className="text-[11.5px] text-faint mt-0.5">
            2–4 models: joint planning → master plan → role assignment → execution → review → fix → delivery.
            Shared board · per-model tokens · strict €0.
          </p>
        </div>
        <span className="chip chip-good ml-auto"><ShieldIcon className="w-3 h-3" /> COST €0.00</span>
      </div>

      <Panel title="Launch a team" sub={`${selected.length}/4 models selected — pick at least 2`}>
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-4">
          <Field label="One complex task">
            <textarea className="input resize-none h-[96px]"
              placeholder="e.g. Design and implement a small markdown-to-HTML converter with a test suite and docs."
              value={task} onChange={(e) => setTask(e.target.value)} />
          </Field>
          <div>
            <div className="micro-label">Team models (+ optional role override)</div>
            <div className="mt-1 space-y-1 max-h-[190px] overflow-y-auto pr-1">
              {available.map((m) => {
                const on = selected.includes(m.name);
                return (
                  <div key={m.name} className={cx("glass-soft rounded-lg px-2.5 py-1.5 flex items-center gap-2",
                    on && "border-[rgba(69,227,255,0.35)]")}>
                    <button className="flex-1 text-left min-w-0" onClick={() => toggleModel(m.name)}>
                      <div className="text-[12px] font-medium text-ink truncate">{m.display_name}</div>
                      <div className="text-[9.5px] text-faint">{m.parameter_size ?? "?"} · {m.context_length ? `${formatNumber(m.context_length)} ctx` : "ctx ?"}</div>
                    </button>
                    {on && (
                      <select className="input !w-[118px] !py-[3px] !text-[10px] cursor-pointer"
                        value={roles[m.name] ?? ""}
                        onChange={(e) => setRoles((r) => ({ ...r, [m.name]: e.target.value }))}
                        title="Role override (optional — auto-assign otherwise)">
                        {ROLES.map((r) => <option key={r} value={r}>{r || "auto role"}</option>)}
                      </select>
                    )}
                    <button className={cx("icon-btn !w-6 !h-6 shrink-0", on && "!text-accent")}
                      onClick={() => toggleModel(m.name)} title={on ? "Remove" : "Add"}>
                      {on ? <CheckIcon className="w-3.5 h-3.5" /> : <XIcon className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                );
              })}
              {available.length === 0 && (
                <div className="text-[11px] text-faint px-2 py-3">No models in catalog — refresh in Model Center.</div>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 mt-3">
          {running ? (
            <button className="btn btn-danger" onClick={() => cancelRef.current?.abort()}>Stop team</button>
          ) : (
            <button className="btn btn-accent" disabled={!task.trim() || selected.length < 2} onClick={() => void start()}>
              <PlayIcon className="w-3.5 h-3.5" /> Launch team ({selected.length})
            </button>
          )}
          <span className="text-[10px] text-faint">Models run sequentially (one GPU). Planning happens before any execution.</span>
          {teamTotal && (
            <span className="chip chip-good ml-auto">
              TEAM TOTAL {formatNumber(teamTotal.total_tokens)} tok · €{teamTotal.cost_eur.toFixed(2)}
            </span>
          )}
        </div>
      </Panel>

      {/* token table */}
      {(memberTokens.length > 0 || teamTotal) && (
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_280px] gap-4">
          <Panel title="Token usage per model" sub="Exact/estimated from each model call, ledger-backed">
            <table className="w-full text-[11.5px]">
              <thead>
                <tr className="text-faint text-[9.5px] uppercase tracking-wide">
                  <th className="text-left pb-1.5">Model</th><th className="text-left pb-1.5">Role</th>
                  <th className="text-right pb-1.5">In</th><th className="text-right pb-1.5">Out</th>
                  <th className="text-right pb-1.5">Total</th>
                </tr>
              </thead>
              <tbody>
                {memberTokens.map((m) => (
                  <tr key={m.model} className="border-t border-line">
                    <td className="py-1.5 font-medium text-ink">{m.model}</td>
                    <td className="py-1.5 text-dim">{m.role}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatNumber(m.input_tokens)}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatNumber(m.output_tokens)}</td>
                    <td className="py-1.5 text-right tabular-nums font-semibold">{formatNumber(m.total_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel title="TEAM TOTAL" sub="All members, lifetime" className="flex flex-col justify-center text-center">
            <div className="text-[26px] font-bold text-ink tabular-nums">
              {formatNumber(teamTotal?.total_tokens ?? 0)}
            </div>
            <div className="micro-label">tokens</div>
            <div className="text-[16px] font-bold text-good tabular-nums mt-2">
              €{(teamTotal?.cost_eur ?? 0).toFixed(2)}
            </div>
            <div className="micro-label">COST · strict €0 protection</div>
          </Panel>
        </div>
      )}

      {/* master plan + activity */}
      <div className="grid grid-cols-1 xl:grid-cols-[380px_1fr] gap-4">
        <Panel title="Master plan" sub="Synthesized by the orchestrator from all analyses" className="min-h-[220px]">
          {masterPlan ? (
            <div className="text-[11.5px] text-dim whitespace-pre-wrap max-h-[400px] overflow-y-auto font-mono">{masterPlan}</div>
          ) : (
            <div className="text-[11.5px] text-faint py-6 text-center">Created after the planning phase.</div>
          )}
        </Panel>

        <Panel title="Team activity" sub="Decisions · actions · findings · reviews — concise, no chain-of-thought" className="min-h-[220px]">
          <div ref={logRef} className="max-h-[400px] overflow-y-auto">
            {activity.length === 0 ? (
              <div className="text-[11.5px] text-faint py-6 text-center">No activity yet.</div>
            ) : activity.map((a, i) => (
              <div key={i} className="flex items-start gap-2 py-[3px] text-[11.5px] leading-snug">
                <span className={cx("chip !text-[8.5px] !py-[1px] shrink-0", a.kind === "error" ? "chip-bad" : a.kind === "done" ? "chip-good" : a.kind === "phase" ? "chip-violet" : "")}>{a.kind}</span>
                <span className="text-faint shrink-0">{a.actor}</span>
                <span className="text-dim break-words min-w-0 whitespace-pre-wrap">{a.content}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* board */}
      <Panel title="Team task board" sub="TODO · IN PROGRESS · REVIEW · DONE (click a card to move it)" right={<span className="chip">{tasks.length} tasks</span>}>
        {tasks.length === 0 ? (
          <div className="text-[11.5px] text-faint py-4 text-center">Tasks appear after the master plan.</div>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
            {boardCols.map(([col, label]) => (
              <div key={col} className="rounded-xl border border-line bg-[rgba(7,10,16,0.5)] p-2 min-h-[140px]">
                <div className="micro-label mb-2">{label}</div>
                <div className="space-y-1.5">
                  {tasks.filter((t) => t.status === col).map((t) => (
                    <button key={t.id}
                      className="w-full text-left glass-soft rounded-lg px-2.5 py-2 hover:bg-hover"
                      title={`${t.title} · ${t.assignee ?? "unassigned"}`}
                      onClick={() => {
                        const next = col === "todo" ? "in_progress" : col === "in_progress" ? "review" : col === "review" ? "done" : "done";
                        void moveTask(t.id, next);
                      }}>
                      <div className="text-[11px] font-medium text-ink leading-snug">{t.title}</div>
                      <div className="flex items-center gap-1.5 mt-1">
                        <UsersIcon className="w-3 h-3 text-accent shrink-0" />
                        <span className="text-[9px] text-faint truncate">{t.assignee ?? "unassigned"}</span>
                      </div>
                      <div className="meter mt-1.5"><div style={{ width: `${Math.max(4, t.progress)}%` }} /></div>
                    </button>
                  ))}
                  {tasks.filter((t) => t.status === col).length === 0 && (
                    <div className="text-[9.5px] text-faint text-center py-3">empty</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* deliverable */}
      {deliverable && (
        <Panel title="Deliverable" sub="Final composed output from the team" right={<span className="chip chip-good">DELIVERED</span>}>
          <div className="text-[12px] text-dim whitespace-pre-wrap max-h-[420px] overflow-y-auto">{deliverable}</div>
        </Panel>
      )}

      {/* history */}
      <Panel title="Team runs" sub="Persisted teams">
        {history.length === 0 ? <Empty text="No team runs yet." /> : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
            {history.map((r) => (
              <button key={r.id} className="glass-soft rounded-xl px-3.5 py-3 text-left hover:bg-hover"
                onClick={() => void openRun(r.id)}>
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-semibold text-ink truncate flex-1">{r.name}</span>
                  <span className={`chip !text-[8.5px] ${r.status === "delivered" ? "chip-good" : r.status === "error" ? "chip-bad" : r.status === "cancelled" ? "chip-warn" : ""}`}>{r.status}</span>
                </div>
                <div className="text-[9.5px] text-faint mt-1">#{r.id} · {r.member_count ?? 0} members · {r.task.slice(0, 90)}</div>
              </button>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
