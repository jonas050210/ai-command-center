// Projects — named sandboxed workspaces for Agent Mode. Directories are
// real (under the workspace), archiving never deletes files, and each
// card shows its real on-disk state + linked agent runs.
import { useCallback, useEffect, useState } from "react";
import { getJSON, sendJSON } from "../api";
import { useStore } from "../store";
import type { AgentRunRow, ProjectRow } from "../types";
import { cx, formatNumber, timeAgo } from "../utils";
import { BotIcon, CodeIcon, FolderIcon, PlusIcon, XIcon } from "../icons";

function ProjectCard({ project, onChanged }: { project: ProjectRow; onChanged: () => void }) {
  const { notify, setView } = useStore();
  const [listing, setListing] = useState<string[] | null>(null);
  const [runs, setRuns] = useState<AgentRunRow[] | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);

  const open = useCallback(async () => {
    if (listing !== null) { setListing(null); setRuns(null); return; }
    try {
      const d = await getJSON<{ listing: string[] }>(`/api/projects/${project.id}`);
      setListing(d.listing);
      const r = await getJSON<{ runs: AgentRunRow[] }>(`/api/projects/${project.id}/runs`);
      setRuns(r.runs);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to open project", "bad");
    }
  }, [listing, project.id, notify]);

  const archive = useCallback(async () => {
    try {
      await sendJSON("PATCH", `/api/projects/${project.id}`,
        { status: project.status === "archived" ? "active" : "archived" });
      onChanged();
      notify(project.status === "archived" ? "Project restored" : "Project archived — files kept on disk");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to update project", "bad");
    }
  }, [project, notify, onChanged]);

  return (
    <div className="glass-soft rounded-xl border border-line p-4 space-y-2.5 anim-fade-up">
      <div className="flex items-start gap-2.5">
        <FolderIcon className="w-4 h-4 text-accent mt-[2px] shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-[13.5px] font-semibold truncate">{project.name}</div>
          <div className="text-[10px] text-faint font-mono truncate">{project.display_path}</div>
        </div>
        {project.linked && <span className="chip chip-accent !text-[9px]">linked</span>}
        {project.status === "archived" && <span className="chip chip-warn !text-[9px]">archived</span>}
        {project.missing && <span className="chip text-bad !text-[9px] border-[rgba(248,113,113,0.35)]">dir missing</span>}
      </div>
      {project.description && (
        <div className="text-[11.5px] text-dim leading-snug">{project.description}</div>
      )}
      <div className="text-[9.5px] text-faint flex items-center gap-2">
        <span>{project.file_count < 0 ? "?" : formatNumber(project.file_count)} files</span>
        <span>·</span><span>created {timeAgo(project.created_at)}</span>
      </div>
      <div className="flex items-center gap-1.5 pt-1">
        <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2.5" onClick={() => void open()}>
          {listing !== null ? "Close" : "Browse"}
        </button>
        <button className={cx("btn btn-ghost !text-[10.5px] !py-1 !px-2.5",
          confirmArchive && "btn-danger !text-bad")}
          onClick={() => {
            if (!confirmArchive && project.status !== "archived") { setConfirmArchive(true); return; }
            setConfirmArchive(false);
            void archive();
          }}>
          {confirmArchive ? "Confirm archive?" : project.status === "archived" ? "Restore" : "Archive"}
        </button>
        {confirmArchive && (
          <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2" onClick={() => setConfirmArchive(false)}>
            <XIcon className="w-3 h-3" />
          </button>
        )}
        {project.status === "active" && (
          <>
            <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2.5 ml-auto"
              onClick={() => {
                sessionStorage.setItem("aicc.agentProject", String(project.id));
                setView("agent");
              }}>
              <BotIcon className="w-3 h-3" /> Agent here
            </button>
            <button className="btn btn-accent !text-[10.5px] !py-1 !px-2.5"
              onClick={() => {
                sessionStorage.setItem("aicc.coderProject", String(project.id));
                setView("coder");
              }}>
              <CodeIcon className="w-3 h-3" /> Code here
            </button>
          </>
        )}
      </div>
      {listing !== null && (
        <div className="border-t border-line pt-2 space-y-1.5">
          <div className="flex flex-wrap gap-1">
            {listing.length === 0 && <span className="text-[10px] text-faint">Empty directory</span>}
            {listing.map((f) => (
              <span key={f} className="chip !text-[9px] font-mono">{f}</span>
            ))}
          </div>
          {runs !== null && runs.length > 0 && (
            <div className="space-y-1">
              <div className="micro-label">Agent runs</div>
              {runs.slice(0, 5).map((r) => (
                <div key={r.id} className="text-[10px] text-dim flex items-center gap-2">
                  <span className={cx("chip !text-[8.5px]", r.status === "complete" ? "chip-good" : "")}>{r.status}</span>
                  <span className="truncate flex-1">{r.task}</span>
                  <span className="text-faint shrink-0">{timeAgo(r.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ProjectsView() {
  const { notify } = useStore();
  const [projects, setProjects] = useState<ProjectRow[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [attachPath, setAttachPath] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const d = await getJSON<{ projects: ProjectRow[] }>(
        `/api/projects${showArchived ? "?archived=true" : ""}`);
      setProjects(d.projects);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to load projects", "bad");
    }
  }, [notify, showArchived]);

  useEffect(() => { void refresh(); }, [refresh]);

  const create = useCallback(async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await sendJSON("POST", "/api/projects", { name: name.trim(), description: desc.trim() });
      setName(""); setDesc(""); setCreating(false);
      await refresh();
      notify("Project created", "good");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to create project", "bad");
    } finally {
      setBusy(false);
    }
  }, [name, desc, refresh, notify]);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-6 pt-4 pb-3 border-b border-line flex items-center gap-3">
        <FolderIcon className="w-[18px] h-[18px] text-accent" />
        <div>
          <div className="text-[15px] font-bold">Projects</div>
          <div className="text-[10px] text-faint">
            Isolated workspace directories — the agent's file tools are confined to the selected project
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2.5"
            onClick={() => setShowArchived((v) => !v)}>
            {showArchived ? "Active" : "Archived"}
          </button>
          <button className="btn btn-accent !text-[11px] !py-1.5 !px-3"
            onClick={() => setCreating((v) => !v)}>
            <PlusIcon className="w-3.5 h-3.5" /> New project
          </button>
        </div>
      </div>

      {creating && (
        <div className="mx-6 mt-3 glass-soft rounded-xl border border-[rgba(69,227,255,0.3)] p-4 space-y-2.5 anim-fade-up">
          <input className="input" placeholder="Project name (e.g. trading-bot)" value={name}
            autoFocus onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void create(); }} />
          <textarea className="input resize-none h-[64px]" placeholder="What is this project for? (optional)"
            value={desc} onChange={(e) => setDesc(e.target.value)} />
          <div className="flex items-center gap-2">
            <button className="btn btn-accent !text-[11px] !py-1.5 !px-3" disabled={!name.trim() || busy}
              onClick={() => void create()}>
              {busy ? "Creating…" : "Create directory + project"}
            </button>
            <button className="btn btn-ghost !text-[11px]" onClick={() => setCreating(false)}>Cancel</button>
            <span className="text-[9.5px] text-faint ml-auto">
              Created as <span className="font-mono">projects/&lt;slug&gt;</span> inside the sandbox
            </span>
          </div>
          <div className="border-t border-line pt-2.5 space-y-1.5">
            <div className="text-[11px] font-semibold">Or attach an existing folder</div>
            <div className="text-[10px] text-faint">Absolute path. Files stay put — archive never deletes them. Tools stay inside that folder.</div>
            <div className="flex items-center gap-2">
              <input className="input font-mono !text-[11.5px]" placeholder="D:\\code\\my-app"
                value={attachPath} onChange={(e) => setAttachPath(e.target.value)} />
              <button className="btn !text-[11px] !py-1.5 !px-3" disabled={!attachPath.trim() || busy}
                onClick={() => {
                  setBusy(true);
                  void sendJSON("POST", "/api/projects/attach", { path: attachPath.trim(), name: name.trim() })
                    .then(() => { setAttachPath(""); setName(""); setCreating(false); return refresh(); })
                    .then(() => notify("Folder attached", "good"))
                    .catch((e) => notify(e instanceof Error ? e.message : "Attach failed", "bad"))
                    .finally(() => setBusy(false));
                }}>
                Attach
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto min-h-0 p-6">
        {projects === null ? (
          <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton h-32" />)}
          </div>
        ) : projects.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center anim-fade-up">
            <FolderIcon className="w-10 h-10 text-accent opacity-80 mb-4" />
            <h1 className="text-[17px] font-bold">
              {showArchived ? "No archived projects" : "No projects yet"}
            </h1>
            <p className="text-[12px] text-dim mt-1.5 max-w-[400px]">
              Projects give agent runs their own sandboxed directory so experiments
              never leak into the global workspace.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
            {projects.map((p) => (
              <ProjectCard key={p.id} project={p} onChanged={() => void refresh()} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
