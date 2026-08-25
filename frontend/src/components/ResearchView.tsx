// Research Mode — real multi-source research with citations.
// Sources come only from the configured search engine; failures are honest.
import { useCallback, useEffect, useState } from "react";
import { getJSON, streamSSE } from "../api";
import { useStore } from "../store";
import type { ResearchEvent, ResearchRun, ResearchSource } from "../types";
import { copyText } from "../utils";
import { CheckIcon, CopyIcon, PlayIcon, SearchIcon } from "../icons";
import { Empty, Panel } from "./ui";

export function ResearchView() {
  const { notify } = useStore();
  const [query, setQuery] = useState("");
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [summary, setSummary] = useState("");
  const [report, setReport] = useState("");
  const [history, setHistory] = useState<ResearchRun[]>([]);
  const [rid, setRid] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try { setHistory((await getJSON<{ runs: ResearchRun[] }>("/api/research/runs")).runs ?? []); } catch { /* offline */ }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const start = async () => {
    if (!query.trim() || running) return;
    setRunning(true);
    setSources([]); setSummary(""); setReport(""); setStatus("starting…"); setRid(null);
    try {
      await streamSSE<ResearchEvent>("/api/research/runs", { query: query.trim(), synthesize: true }, (ev) => {
        if (ev.type === "run") setRid(ev.research_id);
        else if (ev.type === "status") setStatus(ev.message ?? ev.status ?? "…");
        else if (ev.type === "source") {
          setSources((s) => [...s, { title: ev.title ?? "", url: ev.url ?? "", snippet: ev.snippet ?? "" }]);
        } else if (ev.type === "summary") setSummary(ev.summary ?? "");
        else if (ev.type === "done") { setStatus("complete"); setRunning(false); void load(); }
        else if (ev.type === "error") { setStatus(`error: ${ev.message}`); setRunning(false); }
      });
    } catch (e) {
      notify(e instanceof Error ? e.message : "Research failed", "bad");
      setRunning(false);
    } finally {
      setRunning(false);
      void load();
    }
  };

  const open = async (id: number) => {
    try {
      const r = await getJSON<ResearchRun>(`/api/research/runs/${id}`);
      setRid(id); setQuery(r.query); setSources(r.sources ?? []); setSummary(r.summary);
      setReport(r.result); setStatus(r.status);
    } catch (e) { notify(e instanceof Error ? e.message : "load failed", "bad"); }
  };

  const exportMd = async () => {
    if (!rid) return;
    const res = await fetch(`/api/research/runs/${rid}/export`);
    const text = await res.text();
    if (await copyText(text)) {
      setCopied(true); window.setTimeout(() => setCopied(false), 1500);
      notify("Research report copied (markdown).", "good");
    }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
      <div>
        <h1 className="text-[17px] font-bold">Research Mode</h1>
        <p className="text-[11.5px] text-faint mt-0.5">
          Real multi-source web research with citations. Sources are only ever real search results — no fabricated sources.
        </p>
      </div>

      <Panel title="New research" sub="Search engine: DuckDuckGo (configurable in Settings)">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <SearchIcon className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
            <input className="input !pl-8" placeholder="Research question, e.g. local-first AI architecture patterns"
              value={query} onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void start(); }} />
          </div>
          <button className="btn btn-accent" disabled={!query.trim() || running} onClick={() => void start()}>
            <PlayIcon className="w-3.5 h-3.5" /> {running ? "Researching…" : "Research"}
          </button>
        </div>
        {status && <div className="text-[10.5px] text-faint mt-2">{status}</div>}
      </Panel>

      {(sources.length > 0 || summary || report) && (
        <div className="grid grid-cols-1 xl:grid-cols-[380px_1fr] gap-4">
          <Panel title={`Sources · ${sources.length}`} sub="Real results — every URL was returned by the search engine"
            right={rid && (
              <button className="btn !text-[10.5px] !py-1 !px-2" onClick={() => void exportMd()}>
                {copied ? <CheckIcon className="w-3 h-3 text-good" /> : <CopyIcon className="w-3 h-3" />} Export
              </button>
            )}>
            <div className="space-y-2 max-h-[460px] overflow-y-auto">
              {sources.map((s, i) => (
                <div key={`${i}-${s.url}`} className="glass-soft rounded-lg px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    <span className="chip !text-[8.5px] shrink-0">{i + 1}</span>
                    <div className="min-w-0">
                      <a href={s.url} target="_blank" rel="noreferrer noopener"
                        className="text-[12px] font-semibold text-accent hover:underline break-words">{s.title || s.url}</a>
                      <div className="text-[9.5px] text-faint break-all mt-[1px]">{s.url}</div>
                      {s.snippet && <div className="text-[10.5px] text-dim mt-1">{s.snippet}</div>}
                    </div>
                  </div>
                </div>
              ))}
              {sources.length === 0 && (
                <div className="text-[11px] text-faint py-4 text-center">
                  {status === "no_results" ? "No results found — nothing was invented." : "Sources will appear here."}
                </div>
              )}
            </div>
          </Panel>

          <div className="space-y-4">
            {summary && (
              <Panel title="Summary" sub="Written by your local model with [n] citations from the sources above">
                <div className="text-[12px] text-dim whitespace-pre-wrap max-h-[320px] overflow-y-auto">{summary}</div>
              </Panel>
            )}
            {report && (
              <Panel title="Full report" sub="Notes with source excerpts">
                <div className="text-[11.5px] text-dim whitespace-pre-wrap max-h-[380px] overflow-y-auto font-mono">{report}</div>
              </Panel>
            )}
          </div>
        </div>
      )}

      <Panel title="Research history" sub="Persisted research runs">
        {history.length === 0 ? <Empty text="No research yet." /> : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5 my-0">
            {history.map((r) => (
              <button key={r.id} className="glass-soft rounded-xl px-3.5 py-3 text-left hover:bg-hover"
                onClick={() => void open(r.id)}>
                <div className="text-[12px] font-semibold text-ink truncate">{r.query}</div>
                <div className="text-[9.5px] text-faint mt-1">#{r.id} · {r.status} · {r.sources?.length ?? 0} sources</div>
              </button>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
