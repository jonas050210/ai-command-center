// Coder Mode — project workspace + file tree + agent loop.
// Reads come from /api/coder/* (sandboxed). Writes still go through
// the existing agent gateway (approval + audit). No OpenCode process.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON, streamSSE, ApiError } from "../api";
import { useStore } from "../store";
import type {
  AgentEvent, CoderFile, CoderProfile, CoderTree, CoderTreeEntry,
  GitStatus, ProjectRow,
} from "../types";
import { cx, formatNumber } from "../utils";
import {
  AlertIcon, BotIcon, CodeIcon, FileIcon, FolderIcon, GitIcon,
  PlusIcon, RefreshIcon, ShieldIcon, StopIcon, TerminalIcon,
} from "../icons";
import { ApprovalCard, ToolCallCard, statusChip } from "./agentUi";

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

function FileTree({ entries, selected, onOpen, depth = 0 }: {
  entries: CoderTreeEntry[]; selected: string | null;
  onOpen: (e: CoderTreeEntry) => void; depth?: number;
}) {
  return (
    <div>
      {entries.map((e) => {
        const pad = 8 + depth * 12;
        if (e.kind === "dir") {
          return (
            <div key={e.path}>
              <div className="flex items-center gap-1.5 py-[3px] text-[11.5px] text-dim"
                style={{ paddingLeft: pad }}>
                <FolderIcon className="w-3 h-3 text-accent shrink-0" />
                <span className="truncate">{e.name}</span>
              </div>
              {e.children && e.children.length > 0 && (
                <FileTree entries={e.children} selected={selected} onOpen={onOpen} depth={depth + 1} />
              )}
            </div>
          );
        }
        return (
          <button key={e.path} onClick={() => onOpen(e)}
            className={cx(
              "w-full flex items-center gap-1.5 py-[3px] pr-2 text-left text-[11.5px] rounded-md",
              selected === e.path ? "bg-accentdim text-accent" : "text-dim hover:bg-hover hover:text-ink")}
            style={{ paddingLeft: pad }}>
            <FileIcon className="w-3 h-3 shrink-0 opacity-70" />
            <span className="truncate flex-1">{e.name}</span>
            {e.size != null && (
              <span className="text-[9px] text-faint shrink-0">{formatNumber(e.size)}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function CoderView() {
  const { models, settings, currentModel, refreshCosts, refreshTokens, notify, setView } = useStore();
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [tree, setTree] = useState<CoderTree | null>(null);
  const [file, setFile] = useState<CoderFile | null>(null);
  const [git, setGit] = useState<GitStatus | null>(null);
  const [gitNote, setGitNote] = useState<string | null>(null);
  const [profile, setProfile] = useState<CoderProfile | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ input: number; output: number; steps: number; elapsed: number } | null>(null);
  const [streamError, setStreamError] = useState<{ code: string; message: string } | null>(null);
  const [deciding, setDeciding] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const effectiveModel = model
    ?? profile?.selected?.name
    ?? currentModel
    ?? settings?.default_model
    ?? "";
  const modelCaps = models.find((m) => m.name === effectiveModel)?.capabilities ?? [];
  const modelHasTools = modelCaps.includes("tools");
  const selectedProject = projects.find((p) => p.id === projectId) ?? null;

  const loadProjects = useCallback(async () => {
    try {
      const d = await getJSON<{ projects: ProjectRow[] }>("/api/projects");
      setProjects(d.projects);
      const wanted = sessionStorage.getItem("aicc.coderProject")
        ?? localStorage.getItem("aicc.coderProject");
      sessionStorage.removeItem("aicc.coderProject");
      if (wanted) {
        const found = d.projects.find((p) => String(p.id) === wanted);
        if (found) setProjectId(found.id);
        else if (d.projects[0]) setProjectId(d.projects[0].id);
      } else {
        setProjectId((cur) => cur ?? d.projects[0]?.id ?? null);
      }
    } catch { /* keep */ }
  }, []);

  const loadTree = useCallback(async (pid: number | null) => {
    try {
      const q = pid != null ? `?project_id=${pid}` : "";
      const t = await getJSON<CoderTree>(`/api/coder/tree${q}`);
      setTree(t);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to load tree", "bad");
    }
  }, [notify]);

  const [attachPath, setAttachPath] = useState("");
  const [pulling, setPulling] = useState<string | null>(null);
  const [canUndo, setCanUndo] = useState(false);
  const [undoBusy, setUndoBusy] = useState(false);

  const loadGit = useCallback(async (pid: number | null, rel: string | undefined) => {
    try {
      const q = pid != null
        ? `/api/git/status?project_id=${pid}`
        : `/api/git/status?path=${encodeURIComponent(rel || ".")}`;
      const s = await getJSON<GitStatus>(q);
      setGit(s);
      setGitNote(null);
    } catch (e) {
      setGit(null);
      const msg = e instanceof ApiError ? e.message : "Not a git repository.";
      setGitNote(msg);
    }
  }, []);

  useEffect(() => {
    getJSON<CoderProfile>("/api/coder/profile").then(setProfile).catch(() => undefined);
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    void loadTree(projectId);
    const rel = projects.find((p) => p.id === projectId)?.root_path;
    void loadGit(projectId, rel);
    setFile(null);
    if (projectId != null) localStorage.setItem("aicc.coderProject", String(projectId));
  }, [projectId, projects, loadTree, loadGit]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries, finalStatus]);

  const openFile = useCallback(async (entry: CoderTreeEntry) => {
    if (entry.kind !== "file") return;
    try {
      const q = new URLSearchParams({ path: entry.path });
      if (projectId != null) q.set("project_id", String(projectId));
      setFile(await getJSON<CoderFile>(`/api/coder/file?${q.toString()}`));
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to read file", "bad");
    }
  }, [projectId, notify]);

  const createProject = useCallback(async () => {
    if (!newName.trim()) return;
    try {
      const r = await sendJSON<{ project: ProjectRow }>("POST", "/api/projects", {
        name: newName.trim(), description: "Coder Mode project",
      });
      setNewName("");
      setCreating(false);
      await loadProjects();
      setProjectId(r.project.id);
      notify("Project created", "good");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "bad");
    }
  }, [newName, loadProjects, notify]);

  const run = useCallback(async () => {
    const text = task.trim();
    if (!text || running) return;
    setRunning(true);
    setFinalStatus(null);
    setUsage(null);
    setStreamError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    let textKey: string | null = null;
    const upsert = (fn: (list: Entry[]) => Entry[]) => setEntries((list) => fn(list));

    const onEvent = (ev: AgentEvent) => {
      if (ev.type === "meta") setRunId(ev.run_id);
      else if (ev.type === "note") {
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
        if (ev.error) setStreamError({ code: ev.status.toUpperCase(), message: ev.error });
      } else if (ev.type === "error") {
        setStreamError({ code: ev.code, message: ev.message });
      }
    };

    let liveRunId: string | null = null;
    try {
      const onEventTracked = (ev: AgentEvent) => {
        if (ev.type === "meta") liveRunId = ev.run_id;
        onEvent(ev);
      };
      await streamSSE("/api/agent/runs", {
        task: text,
        model: effectiveModel || undefined,
        project_id: projectId ?? undefined,
        skills: profile?.skills ?? undefined,
        mode: "coder",
      }, onEventTracked, controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) {
        setStreamError((prev) => prev ?? (e instanceof ApiError
          ? { code: e.code, message: e.message }
          : { code: "NETWORK", message: "Lost connection to the backend." }));
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
      void loadTree(projectId);
      void loadGit(projectId, selectedProject?.root_path);
      void refreshCosts();
      void refreshTokens();
      if (liveRunId) {
        getJSON<{ exists: boolean }>(`/api/agent/runs/${liveRunId}/snapshot`)
          .then((s) => setCanUndo(s.exists)).catch(() => setCanUndo(false));
      }
    }
  }, [task, running, effectiveModel, projectId, profile, selectedProject,
    loadTree, loadGit, refreshCosts, refreshTokens]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    if (runId) sendJSON("POST", `/api/agent/runs/${runId}/stop`).catch(() => undefined);
  }, [runId]);

  const decide = useCallback(async (approvalId: string, approve: boolean) => {
    setDeciding(true);
    try {
      const r = await sendJSON<{ status: string }>(
        "POST", `/api/agent/approvals/${approvalId}`, { approve });
      setEntries((l) => l.map((e) => (e.kind === "approval" && e.id === approvalId)
        ? { ...e, status: r.status as Extract<Entry, { kind: "approval" }>["status"] } : e));
      notify(approve ? "Action approved" : "Denied — run stopping", approve ? "good" : "info");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "bad");
    } finally {
      setDeciding(false);
    }
  }, [notify]);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-5 pt-3.5 pb-2.5 border-b border-line space-y-2">
        <div className="flex items-center gap-2.5 flex-wrap">
          <CodeIcon className="w-[18px] h-[18px] text-accent" />
          <span className="font-display text-[16px] font-bold tracking-wide">Coder Mode</span>
          <span className="chip chip-good !text-[9px]"><ShieldIcon className="w-3 h-3" /> same sandbox · same approvals</span>
          <span className="chip !text-[9px]" title={profile?.note}>Ollama runtime · 8k ctx</span>
          <div className="ml-auto flex items-center gap-2">
            <select className="input !w-auto !py-1 !px-2 !text-[11px]"
              value={projectId ?? ""} disabled={running}
              onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">workspace (global)</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2"
              onClick={() => setCreating((v) => !v)}>
              <PlusIcon className="w-3 h-3" /> Project
            </button>
            <select className="input !w-auto !py-1 !px-2 !text-[11px] font-mono"
              value={effectiveModel} disabled={running || models.length === 0}
              onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={`${m.provider}/${m.name}`} value={m.name}>
                  {m.name}{m.capabilities.includes("tools") ? " · tools" : ""}
                  {profile?.too_big_installed.includes(m.name) ? " · too big for 8GB" : ""}
                </option>
              ))}
              {models.length === 0 && <option value="">no models synced</option>}
            </select>
          </div>
        </div>
        {profile && (
          <div className="flex items-center gap-1.5 flex-wrap text-[10px] text-faint">
            <span>{profile.hardware.usable_vram_note}</span>
            {profile.selected && (
              <span className="chip chip-accent !text-[9px]">
                suggested {profile.selected.name}
              </span>
            )}
            {!profile.selected && (
              <button className="chip chip-warn !text-[9px] cursor-pointer"
                disabled={!!pulling}
                onClick={() => {
                  const name = profile.pull;
                  setPulling(name);
                  void streamSSE("/api/models/pull", { name }, () => undefined)
                    .then(() => { notify(`Ready: ${name}`, "good"); void getJSON<CoderProfile>("/api/coder/profile").then(setProfile); })
                    .catch((e) => notify(e instanceof Error ? e.message : "Pull failed", "bad"))
                    .finally(() => setPulling(null));
                }}>
                {pulling ? `pulling ${profile.pull}…` : `pull ${profile.pull}`}
              </button>
            )}
            {profile.too_big_installed.length > 0 && (
              <span className="chip chip-warn !text-[9px]">
                installed but won't fit: {profile.too_big_installed.join(", ")}
              </span>
            )}
            {!modelHasTools && effectiveModel && (
              <span className="chip chip-warn !text-[9px]">
                {effectiveModel} doesn't advertise tools
              </span>
            )}
          </div>
        )}
        {creating && (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <input className="input !py-1 !text-[12px] max-w-[260px]" placeholder="New empty project name"
                value={newName} autoFocus
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void createProject(); }} />
              <button className="btn btn-accent !text-[11px] !py-1 !px-2.5"
                disabled={!newName.trim()} onClick={() => void createProject()}>Create</button>
              <button className="btn btn-ghost !text-[11px] !py-1" onClick={() => setCreating(false)}>Cancel</button>
            </div>
            <div className="flex items-center gap-2">
              <input className="input !py-1 !text-[12px] flex-1 font-mono"
                placeholder="Or attach an existing folder — absolute path, e.g. D:\\code\\my-app"
                value={attachPath} onChange={(e) => setAttachPath(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") document.getElementById("coder-attach")?.click(); }} />
              <button id="coder-attach" className="btn !text-[11px] !py-1 !px-2.5"
                disabled={!attachPath.trim()}
                onClick={() => {
                  void sendJSON<{ project: ProjectRow }>("POST", "/api/projects/attach", {
                    path: attachPath.trim(),
                  }).then(async (r) => {
                    setAttachPath("");
                    setCreating(false);
                    await loadProjects();
                    setProjectId(r.project.id);
                    notify("Folder attached — files stay where they are", "good");
                  }).catch((e) => notify(e instanceof Error ? e.message : "Attach failed", "bad"));
                }}>
                Attach
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-1 min-h-0">
        {/* file tree */}
        <aside className="w-[220px] shrink-0 border-r border-line flex flex-col min-h-0">
          <div className="px-3 py-2 flex items-center justify-between border-b border-line">
            <span className="micro-label truncate">
              {selectedProject ? selectedProject.name : "workspace"}
            </span>
            <button className="icon-btn !w-6 !h-6" title="Refresh tree"
              onClick={() => { void loadTree(projectId); void loadGit(projectId, selectedProject?.root_path); }}>
              <RefreshIcon className="w-3 h-3" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-1.5 py-1.5 min-h-0">
            {tree && tree.entries.length === 0 && (
              <div className="text-[11px] text-faint px-2 py-6 text-center">
                Empty project. Ask the agent to scaffold files.
              </div>
            )}
            {tree && <FileTree entries={tree.entries} selected={file?.path ?? null} onOpen={(e) => void openFile(e)} />}
            {tree?.truncated && (
              <div className="text-[9.5px] text-faint px-2 pt-2">Listing truncated.</div>
            )}
          </div>
          <div className="border-t border-line px-3 py-2 text-[10px] text-faint space-y-1">
            <div className="flex items-center gap-1.5">
              <GitIcon className="w-3 h-3" />
              {git ? (
                <span>{git.branch}{git.clean ? " · clean" : ` · ${git.files.length} changed`}</span>
              ) : (
                <span>{gitNote ?? "no git repo"}</span>
              )}
            </div>
            <button className="text-accent hover:underline" onClick={() => setView("git")}>
              Open Git view
            </button>
          </div>
        </aside>

        {/* file preview */}
        <section className="w-[38%] min-w-0 border-r border-line flex flex-col min-h-0">
          <div className="px-3 py-2 border-b border-line text-[11px] text-faint font-mono truncate">
            {file?.path ?? "No file selected"}
            {file?.truncated && <span className="text-warn ml-2">truncated</span>}
          </div>
          <div className="flex-1 overflow-auto min-h-0">
            {!file && (
              <div className="h-full flex flex-col items-center justify-center text-center px-6 text-faint text-[12px]">
                Pick a file on the left. This pane is read-only —
                the agent applies edits after you approve the diff.
              </div>
            )}
            {file?.binary && (
              <div className="p-4 text-[12px] text-dim">{file.note}</div>
            )}
            {file && !file.binary && file.content != null && (
              <pre className="code-body !p-3 text-[11.5px] font-mono leading-relaxed whitespace-pre">
                {file.content || "(empty file)"}
              </pre>
            )}
          </div>
        </section>

        {/* agent */}
        <section className="flex-1 min-w-0 flex flex-col min-h-0">
          {streamError && (
            <div className="mx-3 mt-2 flex items-start gap-2 rounded-xl border px-3 py-2
              border-[rgba(248,113,113,0.4)] bg-[rgba(248,113,113,0.07)]">
              <AlertIcon className="w-4 h-4 text-bad mt-[1px] shrink-0" />
              <div className="text-[12px]">
                <div className="font-semibold text-bad">{streamError.code}</div>
                <div className="text-bad/80 whitespace-pre-wrap">{streamError.message}</div>
              </div>
            </div>
          )}
          <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0 py-2">
            {entries.length === 0 && !finalStatus && (
              <div className="h-full flex flex-col items-center justify-center px-5 text-center">
                <BotIcon className="w-8 h-8 text-accent opacity-80 mb-3" />
                <h2 className="text-[14px] font-bold">Describe a coding task</h2>
                <p className="text-[11.5px] text-dim mt-1 max-w-[360px]">
                  Same agent engine as Agent Mode — scoped to this project.
                  Writes and shell need your approval. OpenCode is not in this loop.
                </p>
              </div>
            )}
            {entries.map((e) => {
              if (e.kind === "note") {
                return (
                  <div key={e.key} className={cx("mx-3 my-1 text-[10.5px] flex items-start gap-1.5",
                    e.level === "warn" ? "text-warn" : "text-faint")}>
                    <AlertIcon className="w-3 h-3 mt-[1px] shrink-0" /> {e.text}
                  </div>
                );
              }
              if (e.kind === "text") {
                return (
                  <div key={e.key} className="mx-3 my-1.5">
                    <div className="rounded-2xl rounded-tl-md glass-soft px-3 py-2 text-[12.5px] whitespace-pre-wrap leading-relaxed">
                      {e.text}
                    </div>
                  </div>
                );
              }
              if (e.kind === "tool") {
                return (
                  <div key={e.key} className="mx-3">
                    <ToolCallCard tool={e.tool} args={e.args} status={e.status}
                      output={e.output} diff={e.diff} danger={e.danger} ms={e.ms}
                      callId={e.callId} />
                  </div>
                );
              }
              return (
                <div key={e.key} className="mx-3">
                  <ApprovalCard entry={e} onDecide={decide} deciding={deciding} />
                </div>
              );
            })}
            {finalStatus && (
              <div className="mx-3 my-2 glass-soft rounded-xl border border-line p-2.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] font-bold">Run {finalStatus}</span>
                  <span className={statusChip(finalStatus)}>{finalStatus.toUpperCase()}</span>
                  {usage && (
                    <span className="text-[10px] text-faint">
                      {usage.elapsed}s · {usage.steps} steps · {formatNumber(usage.input + usage.output)} tok
                    </span>
                  )}
                  {canUndo && runId && (
                    <button className="btn btn-ghost !text-[10px] !py-0.5 !px-2 ml-auto"
                      disabled={undoBusy}
                      onClick={() => {
                        setUndoBusy(true);
                        void sendJSON("POST", `/api/agent/runs/${runId}/undo`)
                          .then(() => {
                            notify("Run undone — files restored", "good");
                            setCanUndo(false);
                            void loadTree(projectId);
                          })
                          .catch((e) => notify(e instanceof Error ? e.message : "Undo failed", "bad"))
                          .finally(() => setUndoBusy(false));
                      }}>
                      Undo file changes
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
          <div className="border-t border-line p-3">
            <div className="glass-soft rounded-2xl border border-line2 focus-within:border-[rgba(69,227,255,0.45)]">
              <textarea
                className="w-full bg-transparent resize-none outline-none px-3 pt-2.5 text-[13px] min-h-[56px] max-h-[140px]"
                placeholder="e.g. Add a FastAPI /health route and a pytest for it."
                value={task} disabled={running}
                onChange={(e) => setTask(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void run(); }
                }}
              />
              <div className="flex items-center gap-2 px-3 pb-2">
                <span className="text-[9.5px] text-faint flex items-center gap-1">
                  <TerminalIcon className="w-3 h-3" />
                  Enter to run · files stay in the project sandbox
                </span>
                {running ? (
                  <button className="btn btn-danger !text-[11px] !py-1 !px-3 ml-auto" onClick={stop}>
                    <StopIcon className="w-3.5 h-3.5" /> Stop
                  </button>
                ) : (
                  <button className="btn btn-accent !text-[11px] !py-1 !px-3 ml-auto"
                    disabled={!task.trim() || models.length === 0} onClick={() => void run()}>
                    <CodeIcon className="w-3.5 h-3.5" /> Run coder
                  </button>
                )}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
