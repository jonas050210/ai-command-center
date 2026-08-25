// Git / GitHub — real local git inside the workspace sandbox + GitHub REST.
// Mutations (init/branch/commit/push) need the git:operate capability and
// are audited server-side. The PAT is vault-stored, always shown masked.
import { useCallback, useEffect, useState } from "react";
import { getJSON, sendJSON, ApiError } from "../api";
import { useStore } from "../store";
import type { GitBranchRow, GitCommitRow, GitStatus, GithubRepoRow,
  GithubUser } from "../types";
import { cx } from "../utils";
import { AlertIcon, CheckIcon, FolderIcon, GitIcon, RefreshIcon } from "../icons";
import { DiffBlock } from "./agentUi";

function errText(e: unknown): { code: string; message: string } {
  if (e instanceof ApiError) return { code: e.code, message: e.message };
  return { code: "ERROR", message: "Request failed." };
}

export function GitView() {
  const { notify, refreshCosts } = useStore();
  const [path, setPath] = useState(".");
  const [capOn, setCapOn] = useState<boolean | null>(null);
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [notARepo, setNotARepo] = useState(false);
  const [commits, setCommits] = useState<GitCommitRow[]>([]);
  const [branches, setBranches] = useState<GitBranchRow[]>([]);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [diffFor, setDiffFor] = useState<string | null>(null);
  const [diffText, setDiffText] = useState("");
  const [diffTrunc, setDiffTrunc] = useState(false);

  const [commitMsg, setCommitMsg] = useState("");
  const [commitResult, setCommitResult] = useState<string | null>(null);
  const [newBranch, setNewBranch] = useState("");
  const [pushResult, setPushResult] = useState<string | null>(null);
  const [remoteUrl, setRemoteUrl] = useState("");

  const [ghConfigured, setGhConfigured] = useState(false);
  const [ghMasked, setGhMasked] = useState<string | null>(null);
  const [ghTokenInput, setGhTokenInput] = useState("");
  const [ghUser, setGhUser] = useState<GithubUser | null>(null);
  const [ghRepos, setGhRepos] = useState<GithubRepoRow[]>([]);
  const [ghRepoName, setGhRepoName] = useState("");
  const [ghError, setGhError] = useState<string | null>(null);

  const refreshGithub = useCallback(async () => {
    try {
      const st = await getJSON<{ configured: boolean; masked: string | null }>(
        "/api/git/github/status");
      setGhConfigured(st.configured);
      setGhMasked(st.masked);
      if (st.configured) {
        try {
          const u = await getJSON<GithubUser>("/api/git/github/user");
          setGhUser(u);
          const r = await getJSON<{ repos: GithubRepoRow[] }>("/api/git/github/repos");
          setGhRepos(r.repos);
        } catch (e) {
          setGhError(errText(e).message);
          setGhUser(null);
        }
      }
    } catch { /* status unavailable */ }
  }, []);

  const refresh = useCallback(async (p: string) => {
    setError(null);
    setNotARepo(false);
    setStatus(null);
    setDiffFor(null);
    try {
      const st = await getJSON<GitStatus>(
        `/api/git/status?path=${encodeURIComponent(p)}`);
      setStatus(st);
      const [lg, br] = await Promise.all([
        getJSON<{ commits: GitCommitRow[] }>(
          `/api/git/log?path=${encodeURIComponent(p)}&limit=20`),
        getJSON<{ branches: GitBranchRow[] }>(
          `/api/git/branches?path=${encodeURIComponent(p)}`),
      ]);
      setCommits(lg.commits);
      setBranches(br.branches);
    } catch (e) {
      const er = errText(e);
      if (er.code === "GIT_NOT_A_REPO") setNotARepo(true);
      else setError(er);
      setCommits([]);
      setBranches([]);
    }
  }, []);

  useEffect(() => {
    getJSON<{ capabilities: Record<string, boolean> }>("/api/agent/capabilities")
      .then((d) => setCapOn(d.capabilities["git:operate"] === true))
      .catch(() => undefined);
    void refresh(".");
    void refreshGithub();
  }, [refresh, refreshGithub]);

  const act = useCallback(async (what: string, fn: () => Promise<void>) => {
    setBusy(what);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
      void refreshCosts();
    }
  }, [refreshCosts]);

  const showDiff = useCallback(async (file: string, staged: boolean) => {
    if (diffFor === file) { setDiffFor(null); return; }
    try {
      const d = await getJSON<{ diff: string; truncated: boolean }>(
        `/api/git/diff?path=${encodeURIComponent(path)}`
        + `&file=${encodeURIComponent(file)}&staged=${staged}`);
      setDiffFor(file);
      setDiffText(d.diff || "(no diff — untracked file has no baseline)");
      setDiffTrunc(d.truncated);
    } catch (e) {
      setError(errText(e));
    }
  }, [diffFor, path]);

  const capHint = (
    <div className="panel !border-[rgba(251,191,36,0.35)] bg-[rgba(251,191,36,0.05)] px-3 py-2.5 flex items-center gap-2">
      <AlertIcon className="w-3.5 h-3.5 text-warn shrink-0" />
      <span className="text-[10.5px] text-dim">
        Git mutations are disabled. Enable <b>Settings → Agent permissions → Git operations</b> to
        init, branch, commit and push. Reads (status/diff/log) stay available.
        Every mutation is written to the audit log.
      </span>
    </div>
  );

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-6 pt-4 pb-3 border-b border-line space-y-2.5">
        <div className="flex items-center gap-2.5 flex-wrap">
          <GitIcon className="w-[18px] h-[18px] text-accent" />
          <span className="text-[15px] font-bold">Git / GitHub</span>
          <span className={cx("chip !text-[9px]", capOn ? "chip-good" : "chip-warn")}>
            {capOn === null ? "…" : capOn ? "git:operate ON" : "git:operate OFF"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <input className="input !w-[220px] !py-1 !px-2 !text-[11px] font-mono"
              value={path} onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void refresh(path.trim() || ".")}
              placeholder="repo path in workspace ('.' = root)" />
            <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2.5"
              onClick={() => { void refresh(path.trim() || "."); void refreshGithub(); }}>
              <RefreshIcon className="w-3 h-3" /> Refresh
            </button>
          </div>
        </div>
        <div className="text-[9.5px] text-faint">
          repos live inside the workspace sandbox · mutations audited ·
          destructive ops (reset --hard, force push, clean) deliberately not offered
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {capOn === false && capHint}
        {error && (
          <div className="panel border-bad/40 bg-bad/5 px-3 py-2.5">
            <div className="text-[11px] text-bad font-semibold flex items-center gap-1.5">
              <AlertIcon className="w-3.5 h-3.5" /> {error.code}
            </div>
            <div className="text-[10.5px] text-dim mt-0.5">{error.message}</div>
          </div>
        )}

        {notARepo && (
          <div className="panel px-4 py-6 text-center space-y-3">
            <FolderIcon className="w-8 h-8 text-faint mx-auto" />
            <div className="text-[12px] text-dim">
              <code className="inline-code">{path}</code> is not a git repository.
            </div>
            <button className="btn btn-accent mx-auto" disabled={!capOn || busy === "init"}
              onClick={() => void act("init", async () => {
                const r = await sendJSON<{ branch: string }>("POST", "/api/git/init",
                  { path: path.trim() || "." });
                notify(`Repository initialized (${r.branch})`, "good");
                await refresh(path.trim() || ".");
              })}>
              {busy === "init" ? "Initializing…" : "git init here"}
            </button>
            {!capOn && <div className="text-[10px] text-faint">needs the git:operate capability</div>}
          </div>
        )}

        {status && (
          <>
            {/* status card */}
            <div className="panel px-4 py-3 space-y-2.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="chip chip-accent !text-[10px]">
                  <GitIcon className="w-3 h-3" /> {status.branch}
                </span>
                {status.ahead > 0 && <span className="chip chip-good !text-[9px]">↑ {status.ahead} ahead</span>}
                {status.behind > 0 && <span className="chip chip-warn !text-[9px]">↓ {status.behind} behind</span>}
                {status.clean
                  ? <span className="chip chip-good !text-[9px]"><CheckIcon className="w-3 h-3" /> clean</span>
                  : <span className="chip chip-warn !text-[9px]">{status.files.length} changed</span>}
                {status.remote && (
                  <span className="text-[9.5px] text-faint font-mono truncate max-w-[320px]">
                    origin: {status.remote}
                  </span>)}
              </div>

              {status.files.length > 0 && (
                <div className="space-y-1">
                  {status.files.map((f) => (
                    <button key={f.path} onClick={() => void showDiff(f.path, f.staged)}
                      className={cx("w-full flex items-center gap-2 text-left px-2 py-1 rounded-lg hover:bg-hover transition-colors",
                        diffFor === f.path && "bg-hover")}>
                      <span className={cx("chip !text-[8.5px] !px-1.5 !py-0 font-mono",
                        f.untracked ? "chip-warn" : f.staged ? "chip-good" : "chip-accent")}>
                        {f.untracked ? "??" : `${f.x || " "}${f.y || " "}`}
                      </span>
                      <span className="text-[11px] text-dim font-mono truncate">{f.path}</span>
                      <span className="text-[9px] text-faint ml-auto shrink-0">
                        {f.untracked ? "untracked" : f.staged ? "staged" : "modified"}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              {diffFor && (
                <div className="border-t border-line pt-2.5">
                  <div className="micro-label mb-1.5">diff — {diffFor}
                    {diffTrunc && <span className="text-warn"> (truncated)</span>}
                  </div>
                  <DiffBlock diff={diffText} />
                </div>
              )}
            </div>

            {/* commit card */}
            <div className="panel px-4 py-3 space-y-2">
              <div className="micro-label">Commit</div>
              <div className="flex gap-2">
                <input className="input flex-1 !py-1.5 !px-2.5 !text-[11.5px]"
                  placeholder={status.files.length
                    ? "Commit message (stages all changes)…"
                    : "Nothing to commit"}
                  value={commitMsg} disabled={!capOn || status.files.length === 0}
                  onChange={(e) => setCommitMsg(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && commitMsg.trim()
                    && void act("commit", async () => {
                      const r = await sendJSON<{ sha: string }>(
                        "POST", "/api/git/commit",
                        { path, message: commitMsg.trim() });
                      setCommitResult(`Committed ${r.sha}`);
                      setCommitMsg("");
                      await refresh(path);
                    })} />
                <button className="btn btn-accent !py-1.5"
                  disabled={!capOn || !commitMsg.trim() || status.files.length === 0
                    || busy === "commit"}
                  onClick={() => void act("commit", async () => {
                    const r = await sendJSON<{ sha: string }>(
                      "POST", "/api/git/commit", { path, message: commitMsg.trim() });
                    setCommitResult(`Committed ${r.sha}`);
                    setCommitMsg("");
                    await refresh(path);
                  })}>
                  {busy === "commit" ? "Committing…" : "Commit"}
                </button>
              </div>
              {commitResult && (
                <div className="text-[10px] text-good flex items-center gap-1.5">
                  <CheckIcon className="w-3 h-3" /> {commitResult}
                </div>)}
            </div>

            {/* branches + push */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="panel px-4 py-3 space-y-2">
                <div className="micro-label">Branches</div>
                <div className="flex flex-wrap gap-1.5">
                  {branches.map((b) => (
                    <span key={b.name} className={cx("chip !text-[9.5px] font-mono",
                      b.current ? "chip-accent" : "")}>
                      {b.current && "● "}{b.name}
                    </span>
                  ))}
                  {branches.length === 0 && <span className="text-[10px] text-faint">none</span>}
                </div>
                <div className="flex gap-2">
                  <input className="input flex-1 !py-1 !px-2 !text-[11px] font-mono"
                    placeholder="new branch name" value={newBranch} disabled={!capOn}
                    onChange={(e) => setNewBranch(e.target.value)} />
                  <button className="btn btn-ghost !text-[10.5px] !py-1"
                    disabled={!capOn || !newBranch.trim() || busy === "branch"}
                    onClick={() => void act("branch", async () => {
                      await sendJSON("POST", "/api/git/branches",
                        { path, name: newBranch.trim() });
                      notify(`Branch '${newBranch.trim()}' created + switched`, "good");
                      setNewBranch("");
                      await refresh(path);
                    })}>
                    Create + switch
                  </button>
                </div>
              </div>

              <div className="panel px-4 py-3 space-y-2">
                <div className="micro-label">Push</div>
                <div className="text-[10px] text-faint">
                  pushes the current branch to origin · file:// remotes work offline;
                  HTTPS remotes use the stored GitHub token (github.com only)
                </div>
                {!status.remote && (
                  <div className="flex gap-2">
                    <input className="input flex-1 !py-1 !px-2 !text-[11px] font-mono"
                      placeholder="https://github.com/you/repo.git" value={remoteUrl}
                      disabled={!capOn}
                      onChange={(e) => setRemoteUrl(e.target.value)} />
                    <button className="btn btn-ghost !text-[10.5px] !py-1"
                      disabled={!capOn || !remoteUrl.trim() || busy === "remote"}
                      onClick={() => void act("remote", async () => {
                        await sendJSON("POST", "/api/git/remote",
                          { path, url: remoteUrl.trim(), remote: "origin" });
                        notify("Remote origin configured", "good");
                        setRemoteUrl("");
                        await refresh(path);
                      })}>
                      Add remote
                    </button>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <button className="btn btn-accent !py-1.5"
                    disabled={!capOn || busy === "push" || !status.remote}
                    title={status.remote ? `push to ${status.remote}` : "add a remote first"}
                    onClick={() => void act("push", async () => {
                      const r = await sendJSON<{ branch: string; remote: string }>(
                        "POST", "/api/git/push",
                        { path, remote: "origin", set_upstream: status.ahead === 0 });
                      setPushResult(`Pushed ${r.branch} → ${r.remote}`);
                      notify("Push complete", "good");
                      await refresh(path);
                    })}>
                    {busy === "push" ? "Pushing…" : "Push to origin"}
                  </button>
                  {pushResult && <span className="text-[10px] text-good">{pushResult}</span>}
                </div>
              </div>
            </div>

            {/* history */}
            <div className="panel px-4 py-3 space-y-1.5">
              <div className="micro-label">History ({commits.length})</div>
              {commits.map((c) => (
                <div key={c.sha} className="flex items-baseline gap-2.5 text-[11px]">
                  <span className="font-mono text-accent shrink-0">{c.sha}</span>
                  <span className="text-dim truncate flex-1">{c.message}</span>
                  {c.decorations && (
                    <span className="chip chip-accent !text-[8px] !px-1.5 !py-0 shrink-0">
                      {c.decorations.replace(/HEAD -> /, "")}
                    </span>)}
                  <span className="text-[9px] text-faint shrink-0">{c.author} · {c.date}</span>
                </div>
              ))}
              {commits.length === 0 && <div className="text-[10px] text-faint">no commits yet</div>}
            </div>
          </>
        )}

        {/* GitHub card — always visible */}
        <div className="panel px-4 py-3 space-y-2.5">
          <div className="flex items-center gap-2">
            <div className="micro-label">GitHub</div>
            {ghConfigured
              ? <span className="chip chip-good !text-[9px]">token stored ({ghMasked})</span>
              : <span className="chip chip-warn !text-[9px]">no token</span>}
          </div>
          <div className="flex gap-2">
            <input className="input flex-1 !py-1 !px-2 !text-[11px] font-mono"
              type="password" placeholder="Personal access token (repo scope)"
              value={ghTokenInput} onChange={(e) => setGhTokenInput(e.target.value)} />
            <button className="btn btn-ghost !text-[10.5px] !py-1"
              disabled={!ghTokenInput.trim() || busy === "ghtoken"}
              onClick={() => void act("ghtoken", async () => {
                await sendJSON("PUT", "/api/git/github/token",
                  { token: ghTokenInput.trim() });
                setGhTokenInput("");
                setGhError(null);
                notify("GitHub token stored (encrypted)", "good");
                await refreshGithub();
              })}>
              Save
            </button>
            {ghConfigured && (
              <button className="btn btn-ghost !text-[10.5px] !py-1 !text-bad"
                onClick={() => void act("ghtoken", async () => {
                  await sendJSON("DELETE", "/api/git/github/token");
                  setGhUser(null); setGhRepos([]);
                  notify("GitHub token removed", "info");
                  await refreshGithub();
                })}>
                Remove
              </button>)}
          </div>
          {ghError && <div className="text-[10px] text-bad">{ghError}</div>}
          {ghUser && (
            <div className="text-[10.5px] text-dim">
              signed in as <a href={ghUser.html_url ?? "#"} target="_blank"
                rel="noopener noreferrer" className="text-accent hover:underline">
                {ghUser.login}</a>
            </div>
          )}
          {ghRepos.length > 0 && (
            <div className="space-y-1">
              <div className="micro-label">Your repositories</div>
              {ghRepos.map((r) => (
                <a key={r.full_name} href={r.html_url} target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-hover transition-colors">
                  <span className="text-[11px] text-accent truncate">{r.full_name}</span>
                  {r.private && <span className="chip !text-[8px] !px-1.5 !py-0">private</span>}
                  <span className="text-[9px] text-faint ml-auto">{r.default_branch}</span>
                </a>
              ))}
            </div>
          )}
          {ghConfigured && (
            <div className="flex gap-2">
              <input className="input flex-1 !py-1 !px-2 !text-[11px] font-mono"
                placeholder="new-repo-name" value={ghRepoName}
                onChange={(e) => setGhRepoName(e.target.value)} />
              <button className="btn btn-ghost !text-[10.5px] !py-1"
                disabled={!ghRepoName.trim() || busy === "ghcreate"}
                onClick={() => void act("ghcreate", async () => {
                  const r = await sendJSON<{ repo: GithubRepoRow }>(
                    "POST", "/api/git/github/repos",
                    { name: ghRepoName.trim(), private: true });
                  notify(`Created ${r.repo.full_name} (private)`, "good");
                  setGhRepoName("");
                  await refreshGithub();
                })}>
                Create private repo
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
