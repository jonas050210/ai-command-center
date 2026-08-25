// Git / GitHub — real Git in sandboxed project workspaces + honest GitHub state.
import { useCallback, useEffect, useState } from "react";
import { getJSON, sendJSON } from "../api";
import { useStore } from "../store";
import type { GitStatus, GithubState, Project } from "../types";
import { GitIcon } from "../icons";
import { Panel } from "./ui";

export function GitView() {
  const { notify } = useStore();
  const [projects, setProjects] = useState<Project[]>([]);
  const [pid, setPid] = useState<number | null>(null);
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [branches, setBranches] = useState<string[]>([]);
  const [log, setLog] = useState<string[]>([]);
  const [diff, setDiff] = useState("");
  const [message, setMessage] = useState("");
  const [gh, setGh] = useState<GithubState | null>(null);
  const [repos, setRepos] = useState<NonNullable<GithubState["repositories"]>>([]);
  const [selectedRepo, setSelectedRepo] = useState<string>("");
  const [issues, setIssues] = useState<NonNullable<GithubState["issues"]>>([]);
  const [pulls, setPulls] = useState<NonNullable<GithubState["pulls"]>>([]);
  const [token, setToken] = useState("");

  const loadProjects = useCallback(async () => {
    try { setProjects((await getJSON<{ projects: Project[] }>("/api/projects")).projects ?? []); } catch { /* */ }
  }, []);
  useEffect(() => { void loadProjects(); }, [loadProjects]);

  const loadGit = useCallback(async (id: number | null) => {
    const qs = id != null ? `?project_id=${id}` : "";
    try {
      const [s, b, l] = await Promise.all([
        getJSON<GitStatus>(`/api/git/status${qs}`),
        getJSON<{ branches: string[] }>(`/api/git/branches${qs}`),
        getJSON<{ entries: string[] }>(`/api/git/log${qs}`),
      ]);
      setStatus(s); setBranches(b.branches ?? []); setLog(l.entries ?? []);
      const d = await getJSON<{ diff: string }>(`/api/git/diff${qs}`);
      setDiff(d.diff ?? "");
    } catch (e) { notify(e instanceof Error ? e.message : "git load failed", "bad"); }
  }, [notify]);

  useEffect(() => { void loadGit(pid); }, [pid, loadGit]);

  const commit = async () => {
    if (!message.trim()) return;
    try {
      const r = await sendJSON<{ ok: boolean; commit?: string; error?: string }>(
        "POST", `/api/git/commit${pid != null ? `?project_id=${pid}` : ""}`,
        { message: message.trim() });
      if (r.ok) {
        notify(r.commit ?? "Committed.", "good");
        setMessage("");
        void loadGit(pid);
      } else {
        notify(r.error ?? "Commit failed", "bad");
      }
    } catch (e) { notify(e instanceof Error ? e.message : "commit failed", "bad"); }
  };

  const loadGh = useCallback(async () => {
    try {
      const st = await getJSON<GithubState>("/api/github/state");
      setGh(st);
      if (st.authenticated) {
        const r = await getJSON<GithubState>("/api/github/repositories");
        setRepos(r.repositories ?? []);
      }
    } catch { /* offline */ }
  }, []);
  useEffect(() => { void loadGh(); }, [loadGh]);

  const openRepo = async (fullName: string) => {
    setSelectedRepo(fullName);
    try {
      const [i, p] = await Promise.all([
        getJSON<GithubState>(`/api/github/${fullName}/issues`),
        getJSON<GithubState>(`/api/github/${fullName}/pulls`),
      ]);
      setIssues(i.issues ?? []);
      setPulls(p.pulls ?? []);
      if (i.error || p.error) notify(i.error ?? p.error ?? "", "bad");
    } catch (e) { notify(e instanceof Error ? e.message : "github load failed", "bad"); }
  };

  const storeToken = async () => {
    if (!token.trim()) return;
    try {
      await sendJSON("PUT", "/api/github/credentials", { token: token.trim() });
      setToken("");
      notify("GitHub token stored (encrypted).", "good");
      void loadGh();
    } catch (e) { notify(e instanceof Error ? e.message : "token store failed", "bad"); }
  };

  const clearToken = async () => {
    try {
      await sendJSON("DELETE", "/api/github/credentials");
      notify("GitHub token cleared.", "good");
      void loadGh();
    } catch (e) { notify(e instanceof Error ? e.message : "clear failed", "bad"); }
  };

  const initRepo = async () => {
    try {
      const r = await sendJSON<{ ok: boolean; already?: boolean; error?: string }>(
        "POST", `/api/git/init${pid != null ? `?project_id=${pid}` : ""}`);
      if (r.ok) {
        notify(r.already ? "Already a git repository." : "Repository initialized in the sandboxed workspace.", "good");
        void loadGit(pid);
      } else {
        notify(r.error ?? "Git init failed", "bad");
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Git init failed", "bad");
    }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
      <div>
        <h1 className="text-[17px] font-bold">Git & GitHub</h1>
        <p className="text-[11.5px] text-faint mt-0.5">
          Real local Git inside sandboxed project workspaces. GitHub features activate only with a token — otherwise they clearly show as unavailable.
        </p>
      </div>

      <Panel title="Local repository" sub="Choose a project workspace (or the default one) to inspect and commit"
        right={pid != null && (
          <button className="btn !text-[10.5px] !py-1 !px-2" onClick={() => void initRepo()}
            title="Initialize a real git repository in this workspace">Init</button>
        )}>
        <div className="flex items-center gap-2 mb-3">
          <select className="input cursor-pointer" value={pid ?? ""}
            onChange={(e) => setPid(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Default workspace</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="btn" onClick={() => void loadGit(pid)}>Refresh</button>
        </div>

        {status && !status.is_repo && (
          <div className="text-[11.5px] text-dim rounded-lg border border-line px-3 py-2.5">
            <GitIcon className="w-3.5 h-3.5 inline mr-1.5 text-accent" />
            Not a git repository yet: <code className="inline-code">{status.detail ?? status.path}</code>
            <div className="text-[10px] text-faint mt-1">Run <code className="inline-code">git init</code> in this workspace (or via Agent Mode) to enable status/log/diff/commit.</div>
          </div>
        )}

        {status?.is_repo && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-3">
              <div className="flex flex-wrap gap-1.5">
                <span className="chip chip-accent">branch: {status.branch}</span>
                <span className={status.clean ? "chip chip-good" : "chip chip-warn"}>
                  {status.clean ? "clean" : `${status.changes} changed file(s)`}
                </span>
              </div>
              <div className="micro-label">Changes</div>
              <div className="max-h-[160px] overflow-y-auto font-mono text-[10.5px] text-dim space-y-0.5">
                {status.porcelain?.map((l, i) => <div key={i}>{l}</div>)}
                {status.porcelain?.length === 0 && <div className="text-faint">(no unstaged changes)</div>}
              </div>
              <div className="micro-label">Branches</div>
              <div className="flex flex-wrap gap-1.5">
                {branches.map((b) => <span key={b} className="chip !text-[9.5px]">{b.trim()}</span>)}
              </div>
              <div className="micro-label">History</div>
              <div className="max-h-[160px] overflow-y-auto font-mono text-[10.5px] text-dim space-y-0.5">
                {log.map((l, i) => <div key={i}>{l}</div>)}
              </div>
            </div>
            <div>
              <div className="micro-label">Diff</div>
              <div className="glass-soft rounded-lg p-2.5 max-h-[220px] overflow-y-auto font-mono text-[10.5px] text-dim whitespace-pre-wrap">
                {diff || "(no diff)"}
              </div>
              <div className="micro-label mt-3">Commit</div>
              <div className="flex items-center gap-2">
                <input className="input flex-1" placeholder="Commit message (single line)" value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void commit(); }} />
                <button className="btn btn-accent" disabled={!message.trim()} onClick={() => void commit()}>Commit</button>
              </div>
              <div className="text-[9.5px] text-faint mt-1.5">
                Allowed: status · log · branch · diff · add · commit. Push/pull/reset are intentionally not exposed — never fake remote operations.
              </div>
            </div>
          </div>
        )}
      </Panel>

      <Panel title="GitHub" sub="Requires a token — states are always honest"
        right={<span className={`chip ${gh?.authenticated ? "chip-good" : "chip-warn"}`}>
          {gh?.authenticated ? `connected as ${gh.login ?? "…"}` : "unauthenticated"}</span>}>
        {!gh?.authenticated ? (
          <div className="text-[11.5px] text-dim space-y-3">
            <div className="rounded-lg border border-[rgba(251,191,36,0.3)] bg-[rgba(251,191,36,0.06)] px-3 py-2.5">
              {gh?.message ?? "Checking GitHub state…"}
            </div>
            <div className="flex items-center gap-2 max-w-[420px]">
              <input className="input flex-1 font-mono" type="password" placeholder="GitHub personal access token"
                value={token} onChange={(e) => setToken(e.target.value)} />
              <button className="btn" disabled={!token.trim()} onClick={() => void storeToken()}>Store (encrypted)</button>
            </div>
            <div className="text-[10px] text-faint">
              Tokens are encrypted with the local Fernet key and never written in plaintext. Set GITHUB_TOKEN in the environment instead if you prefer.
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <select className="input flex-1 cursor-pointer" value={selectedRepo}
                onChange={(e) => { if (e.target.value) void openRepo(e.target.value); }}>
                <option value="">Select a repository…</option>
                {(repos ?? []).map((r) => <option key={r.full_name} value={r.full_name}>{r.private ? "🔒 " : ""}{r.full_name}</option>)}
              </select>
              <button className="btn" onClick={() => void loadGh()}>Refresh</button>
              <button className="btn btn-ghost" onClick={() => void clearToken()}>Disconnect</button>
            </div>
            {selectedRepo && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div>
                  <div className="micro-label">Open issues · {issues.length}</div>
                  <div className="space-y-1.5 max-h-[220px] overflow-y-auto">
                    {issues.map((i) => (
                      <a key={i.number} href={i.html_url} target="_blank" rel="noreferrer noopener"
                        className="block glass-soft rounded-lg px-3 py-1.5 text-[11.5px] text-ink hover:bg-hover">
                        #{i.number} {i.title} <span className="text-faint">· {i.user}</span>
                      </a>
                    ))}
                    {issues.length === 0 && <div className="text-[10.5px] text-faint py-2">No open issues.</div>}
                  </div>
                </div>
                <div>
                  <div className="micro-label">Open pull requests · {pulls.length}</div>
                  <div className="space-y-1.5 max-h-[220px] overflow-y-auto">
                    {pulls.map((p) => (
                      <a key={p.number} href={p.html_url} target="_blank" rel="noreferrer noopener"
                        className="block glass-soft rounded-lg px-3 py-1.5 text-[11.5px] text-ink hover:bg-hover">
                        #{p.number} {p.title} <span className="text-faint">· {p.head} → {p.base}</span>
                      </a>
                    ))}
                    {pulls.length === 0 && <div className="text-[10.5px] text-faint py-2">No open pull requests.</div>}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}
