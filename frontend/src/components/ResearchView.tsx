// Research Mode — web-grounded answers with citations.
// Pipeline (all real): DuckDuckGo search → SSRF-guarded fetch/extract →
// model answer with [n] citations. Sources that fail are dropped and
// said so. History is persisted server-side.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON, streamSSE, ApiError } from "../api";
import { useStore } from "../store";
import type { ResearchEvent, ResearchRunRow, ResearchSource } from "../types";
import { cx, formatNumber, timeAgo } from "../utils";
import { AlertIcon, CheckIcon, ResearchIcon, SendIcon, StopIcon } from "../icons";
import { Markdown } from "./Markdown";

type Stage = "searching" | "fetching" | "answering";
const STAGES: Stage[] = ["searching", "fetching", "answering"];

function SourceChip({ s }: { s: ResearchSource }) {
  return (
    <a href={s.url} target="_blank" rel="noopener noreferrer"
      title={s.url}
      className="chip !text-[9.5px] hover:!border-accent hover:text-accent transition-colors max-w-[280px] truncate">
      [{s.index}] {s.title || s.url}
    </a>
  );
}

export function ResearchView() {
  const { models, settings, currentModel, refreshCosts, refreshTokens, notify } = useStore();
  const [question, setQuestion] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [researchId, setResearchId] = useState<number | null>(null);
  const [stage, setStage] = useState<Stage | "done" | null>(null);
  const [statusMsg, setStatusMsg] = useState("");
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [citations, setCitations] = useState<ResearchSource[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [answer, setAnswer] = useState("");
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const [usage, setUsage] = useState<{ in: number; out: number; model: string;
    provider: string; elapsed: number } | null>(null);
  const [streamError, setStreamError] = useState<{ code: string; message: string } | null>(null);
  const [history, setHistory] = useState<ResearchRunRow[]>([]);
  const [openRow, setOpenRow] = useState<ResearchRunRow | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const answerRef = useRef<HTMLDivElement>(null);

  const effectiveModel = model ?? currentModel ?? settings?.default_model ?? "";

  const refreshHistory = useCallback(async () => {
    try {
      const data = await getJSON<{ runs: ResearchRunRow[] }>("/api/research/history");
      setHistory(data.runs);
    } catch { /* keep old */ }
  }, []);

  useEffect(() => { void refreshHistory(); }, [refreshHistory]);

  useEffect(() => {
    answerRef.current?.scrollTo({ top: answerRef.current.scrollHeight });
  }, [answer]);

  const resetRun = () => {
    setStage(null); setStatusMsg(""); setSources([]); setCitations([]);
    setNotes([]); setAnswer(""); setFinalStatus(null); setUsage(null);
    setStreamError(null); setOpenRow(null);
  };

  const run = useCallback(async () => {
    const q = question.trim();
    if (running || !q) return;
    setRunning(true);
    resetRun();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamSSE("/api/research/query",
        { question: q, model: effectiveModel || undefined },
        (ev: ResearchEvent) => {
          if (ev.type === "meta") {
            setResearchId(ev.research_id);
          } else if (ev.type === "status") {
            setStage(ev.stage); setStatusMsg(ev.message);
          } else if (ev.type === "sources") {
            setSources(ev.sources);
          } else if (ev.type === "note") {
            setNotes((n) => [...n, ev.message]);
          } else if (ev.type === "delta") {
            setAnswer((a) => a + ev.content);
          } else if (ev.type === "citations") {
            setCitations(ev.citations);
          } else if (ev.type === "usage") {
            setUsage({ in: ev.input_tokens, out: ev.output_tokens,
              model: ev.model, provider: ev.provider, elapsed: ev.elapsed_s });
          } else if (ev.type === "done") {
            setFinalStatus(ev.status);
            setStage("done");
            if (ev.answer) setAnswer(ev.answer);
          } else if (ev.type === "error") {
            setStreamError({ code: ev.code, message: ev.message });
            setStage("done");
          }
        }, controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) {
        setStreamError({ code: "CONNECTION",
          message: e instanceof ApiError ? e.message : "Lost connection to the backend." });
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
      void refreshCosts();
      void refreshTokens();
      void refreshHistory();
    }
  }, [running, question, effectiveModel, refreshCosts, refreshTokens, refreshHistory]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    if (researchId !== null) {
      sendJSON("POST", `/api/research/${researchId}/stop`).catch(() => undefined);
    }
    notify("Research stopped", "info");
  }, [researchId, notify]);

  const openHistoryRow = useCallback(async (id: number) => {
    try {
      const data = await getJSON<{ run: ResearchRunRow }>(`/api/research/${id}`);
      resetRun();
      setOpenRow(data.run);
      setAnswer(data.run.result);
      setCitations(data.run.sources);
      setFinalStatus(data.run.status);
      setStage("done");
    } catch { notify("Could not load research run", "bad"); }
  }, [notify]);

  const stageState = (s: Stage) => {
    if (!stage) return "idle";
    const idx = STAGES.indexOf(s);
    const cur = stage === "done" ? STAGES.length : STAGES.indexOf(stage as Stage);
    if (idx < cur) return "done";
    if (idx === cur) return stage === "done" ? "done" : "active";
    return "idle";
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* header */}
      <div className="px-6 pt-4 pb-3 border-b border-line space-y-2.5">
        <div className="flex items-center gap-2.5 flex-wrap">
          <ResearchIcon className="w-[18px] h-[18px] text-accent" />
          <span className="text-[15px] font-bold">Research Mode</span>
          <span className="chip chip-good !text-[9px]">web-grounded · cited sources</span>
          <div className="ml-auto flex items-center gap-2">
            <select className="input !w-auto !py-1 !px-2 !text-[11px] font-mono"
              value={effectiveModel} disabled={running || models.length === 0}
              onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={`${m.provider}/${m.name}`} value={m.name}>{m.name}</option>
              ))}
              {models.length === 0 && <option value="">no models synced</option>}
            </select>
            <button className="btn btn-ghost !text-[10.5px] !py-1 !px-2.5"
              onClick={() => void refreshHistory()}>
              History ({history.length})
            </button>
          </div>
        </div>
        <div className="text-[9.5px] text-faint">
          search → fetch (SSRF-guarded, size-capped) → answer with [n] citations ·
          gated by the network:fetch capability · CostGuard applies to the answer pass
        </div>
      </div>

      <div className="flex-1 min-h-0 flex">
        {/* main column */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div ref={answerRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
            {!stage && !openRow && history.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center text-center gap-2 opacity-70">
                <ResearchIcon className="w-10 h-10 text-faint" />
                <div className="text-[13px] text-dim font-medium">Ask a research question</div>
                <div className="text-[10.5px] text-faint max-w-[380px]">
                  The app searches the web, reads the top sources, and the model answers
                  with numbered citations. Sources that can't be read are dropped — never invented.
                </div>
              </div>
            )}

            {/* pipeline stages */}
            {stage && (
              <div className="flex items-center gap-2 flex-wrap">
                {STAGES.map((s, i) => {
                  const st = stageState(s);
                  return (
                    <div key={s} className="flex items-center gap-2">
                      {i > 0 && <span className="text-faint text-[10px]">→</span>}
                      <span className={cx("chip !text-[9px]",
                        st === "active" && "chip-accent animate-pulse",
                        st === "done" && "chip-good")}>
                        {st === "done" && <CheckIcon className="w-3 h-3" />}
                        {s}
                      </span>
                    </div>
                  );
                })}
                {statusMsg && stage !== "done" &&
                  <span className="text-[10px] text-faint">{statusMsg}</span>}
                {finalStatus &&
                  <span className={cx("chip !text-[9px]",
                    finalStatus === "complete" ? "chip-good" : "chip-warn")}>
                    {finalStatus}
                  </span>}
              </div>
            )}

            {/* sources */}
            {sources.length > 0 && stage !== "done" && (
              <div className="space-y-1.5">
                <div className="micro-label">Sources found</div>
                <div className="space-y-1">
                  {sources.map((s) => (
                    <div key={s.url} className="text-[10.5px] flex gap-2 items-baseline">
                      <span className="text-faint shrink-0">[{s.index}]</span>
                      <a href={s.url} target="_blank" rel="noopener noreferrer"
                        className="text-accent hover:underline truncate">{s.title || s.url}</a>
                      {s.snippet &&
                        <span className="text-faint truncate hidden xl:inline">{s.snippet}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {notes.map((n, i) => (
              <div key={i} className="text-[10px] text-warn flex items-center gap-1.5">
                <AlertIcon className="w-3 h-3 shrink-0" /> {n}
              </div>
            ))}

            {streamError && (
              <div className="panel border-bad/40 bg-bad/5 px-3 py-2.5 space-y-1">
                <div className="text-[11px] text-bad font-semibold flex items-center gap-1.5">
                  <AlertIcon className="w-3.5 h-3.5" /> {streamError.code}
                </div>
                <div className="text-[10.5px] text-dim">{streamError.message}</div>
                {streamError.code === "RESEARCH_DISABLED" && (
                  <div className="text-[10px] text-faint">
                    Enable it under Settings → Agent permissions → Network fetch.
                  </div>
                )}
              </div>
            )}

            {/* answer */}
            {answer && (
              <div className="panel px-4 py-3">
                <Markdown content={answer} />
              </div>
            )}

            {/* citations */}
            {citations.length > 0 && (
              <div className="space-y-1.5">
                <div className="micro-label">Citations</div>
                <div className="flex flex-wrap gap-1.5">
                  {citations.map((c) => <SourceChip key={c.url} s={c} />)}
                </div>
              </div>
            )}

            {usage && (
              <div className="text-[9.5px] text-faint">
                {usage.provider}/{usage.model} · in {formatNumber(usage.in)} ·
                out {formatNumber(usage.out)} · {usage.elapsed}s
              </div>
            )}
          </div>

          {/* composer */}
          <div className="border-t border-line px-6 py-3">
            <div className="flex gap-2 items-end">
              <textarea
                className="input flex-1 resize-none !text-[12px] !py-2"
                rows={2}
                placeholder="e.g. What changed in the latest Ollama release?"
                value={question}
                disabled={running}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void run(); }
                }} />
              {running ? (
                <button className="btn btn-ghost !text-bad !px-3 !py-2"
                  onClick={stop} title="Stop this research run">
                  <StopIcon className="w-4 h-4" />
                </button>
              ) : (
                <button className="btn btn-accent !px-3 !py-2"
                  disabled={!question.trim() || models.length === 0}
                  onClick={() => void run()} title="Run research">
                  <SendIcon className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* history rail */}
        {history.length > 0 && (
          <div className="w-[240px] shrink-0 border-l border-line overflow-y-auto p-3 space-y-1.5">
            <div className="micro-label">Past research</div>
            {history.map((h) => (
              <button key={h.id} onClick={() => void openHistoryRow(h.id)}
                className={cx("w-full text-left panel !p-2 hover:border-line2 transition-colors",
                  openRow?.id === h.id && "!border-accent/50")}>
                <div className="text-[10.5px] text-dim line-clamp-2">{h.query}</div>
                <div className="flex items-center gap-1.5 mt-1">
                  <span className={cx("chip !text-[8.5px] !px-1.5 !py-0",
                    h.status === "complete" ? "chip-good"
                      : h.status === "stopped" ? "chip-warn" : "chip-bad")}>
                    {h.status}
                  </span>
                  <span className="text-[8.5px] text-faint">{timeAgo(h.created_at)}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
