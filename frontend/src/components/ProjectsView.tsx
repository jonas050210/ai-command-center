// Projects — first-class project objects: workspace, files, tasks, chats.
import { useCallback, useEffect, useState } from "react";
import { getJSON, sendJSON } from "../api";
import { useStore } from "../store";
import type { Project, ProjectDetail } from "../types";
import { formatBytes } from "../utils";
import { FolderIcon, PlusIcon, TrashIcon } from "../icons";
import { Empty, Field, Panel } from "./ui";

export function ProjectsView() {
  const { notify } = useStore();
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [newTask, setNewTask] = useState("");

  const load = useCallback(async () => {
    try { setProjects((await getJSON<{ projects: Project[] }>("/api/projects")).projects ?? []); }
    catch { /* offline */ }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (detail) void open(detail.id);
  }, [detail?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = async (id: number) => {
    try { setDetail(await getJSON<ProjectDetail>(`/api/projects/${id}`)); }
    catch (e) { notify(e instanceof Error ? e.message : "load failed", "bad"); }
  };

  const create = async () => {
    if (!name.trim()) return;
    try {
      const p = await sendJSON<Project>("POST", "/api/projects", { name: name.trim(), description });
      setName(""); setDescription("");
      await load();
      void open(p.id);
      notify(`Project '${p.name}' created.`, "good");
    } catch (e) { notify(e instanceof Error ? e.message : "create failed", "bad"); }
  };

  const remove = async (id: number) => {
    try {
      await sendJSON("DELETE", `/api/projects/${id}`);
      setDetail(null);
      await load();
      notify("Project deleted.", "good");
    } catch (e) { notify(e instanceof Error ? e.message : "delete failed", "bad"); }
  };

  const addTask = async () => {
    if (!detail || !newTask.trim()) return;
    try {
      await sendJSON("POST", `/api/projects/${detail.id}/tasks`, { title: newTask.trim() });
      setNewTask("");
      void open(detail.id);
      void load();
    } catch (e) { notify(e instanceof Error ? e.message : "task add failed", "bad"); }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
      <div>
        <h1 className="text-[17px] font-bold">Projects</h1>
        <p className="text-[11.5px] text-faint mt-0.5">
          First-class workspaces: each project has a sandboxed folder, files, tasks, chats and optional team/agent runs.
        </p>
      </div>

      <Panel title="Create project" sub="A sandboxed workspace is created under data/workspace/projects/…">
        <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr_auto] gap-3 items-end">
          <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="My project" /></Field>
          <Field label="Description (optional)"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this project about?" /></Field>
          <button className="btn btn-accent" disabled={!name.trim()} onClick={() => void create()}><PlusIcon className="w-3.5 h-3.5" /> Create</button>
        </div>
      </Panel>

      <div className="grid grid-cols-1 xl:grid-cols-[320px_1fr] gap-4">
        <Panel title={`Projects · ${projects.length}`}>
          {projects.length === 0 ? <Empty text="No projects yet." /> : (
            <div className="space-y-1.5 max-h-[520px] overflow-y-auto">
              {projects.map((p) => (
                <div key={p.id} className={`glass-soft rounded-xl px-3 py-2.5 cursor-pointer hover:bg-hover ${detail?.id === p.id ? "border-[rgba(69,227,255,0.35)]" : ""}`}
                  onClick={() => void open(p.id)}>
                  <div className="flex items-center gap-2">
                    <FolderIcon className="w-3.5 h-3.5 text-accent shrink-0" />
                    <span className="text-[12.5px] font-semibold text-ink truncate flex-1">{p.name}</span>
                    <button className="icon-btn !w-6 !h-6 danger shrink-0" title="Delete project"
                      onClick={(e) => { e.stopPropagation(); if (window.confirm(`Delete project '${p.name}'?`)) void remove(p.id); }}>
                      <TrashIcon className="w-3 h-3" />
                    </button>
                  </div>
                  <div className="text-[9.5px] text-faint mt-1">
                    {p.task_count} tasks · {p.chat_count} chats · updated {p.updated_at}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        {detail ? (
          <div className="space-y-4">
            <Panel title={detail.name} sub={detail.description || "No description"}
              right={<span className="chip">{detail.status}</span>}>
              <div className="flex flex-wrap gap-1.5">
                <span className="chip">workspace: {detail.root_path}</span>
                <span className="chip">{detail.task_count} tasks</span>
                <span className="chip">{detail.chat_count} chats</span>
              </div>
            </Panel>

            <Panel title={`Files · ${detail.files.length}`} sub="Sandboxed project workspace (refresh to rescan)">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 max-h-[220px] overflow-y-auto">
                {detail.files.length === 0 ? (
                  <div className="text-[11px] text-faint py-3 col-span-2">No files yet. Use Agent Mode with this project selected.</div>
                ) : detail.files.map((f) => (
                  <div key={f.path} className="glass-soft rounded-lg px-2.5 py-1.5 flex items-center gap-2">
                    <span className="text-[11.5px] text-ink truncate flex-1 font-mono">{f.path}</span>
                    <span className="text-[9.5px] text-faint">{formatBytes(f.size_bytes)}</span>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="Tasks" sub="Project task list">
              <div className="flex items-center gap-2 mb-3">
                <input className="input flex-1" placeholder="Add a task…" value={newTask}
                  onChange={(e) => setNewTask(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void addTask(); }} />
                <button className="btn btn-accent" disabled={!newTask.trim()} onClick={() => void addTask()}>Add</button>
              </div>
              <div className="space-y-1.5 max-h-[220px] overflow-y-auto">
                {detail.tasks.length === 0 ? (
                  <div className="text-[11px] text-faint py-3 text-center">No tasks yet.</div>
                ) : detail.tasks.map((t) => (
                  <div key={t.id} className="glass-soft rounded-lg px-3 py-2 flex items-start gap-2">
                    <span className={`chip !text-[8.5px] shrink-0 mt-[2px] ${t.status === "todo" ? "chip-warn" : "chip-good"}`}>{t.status}</span>
                    <div className="min-w-0">
                      <div className="text-[12px] text-ink">{t.title}</div>
                      {t.description && <div className="text-[10px] text-faint">{t.description}</div>}
                    </div>
                    <button className="icon-btn !w-6 !h-6 danger ml-auto shrink-0" title="Delete task"
                      onClick={async () => { await sendJSON("DELETE", `/api/projects/${detail.id}/tasks/${t.id}`); void open(detail.id); void load(); }}>
                      <TrashIcon className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title={`Chats · ${detail.conversations.length}`} sub="Conversations linked to this project">
              {detail.conversations.length === 0 ? (
                <div className="text-[11px] text-faint py-3">No chats yet — send a chat with this project selected.</div>
              ) : detail.conversations.map((c) => (
                <div key={c.id} className="glass-soft rounded-lg px-3 py-1.5 text-[11.5px] text-ink truncate">{c.title}</div>
              ))}
            </Panel>
          </div>
        ) : (
          <Empty text="Select a project" sub="Or create one above to see its workspace, files, tasks and chats." />
        )}
      </div>
    </div>
  );
}
