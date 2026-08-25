// Compare Mode — multiple models answer one prompt side-by-side.
// Per-answer token usage, select best, combine answers.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON, streamSSE } from "../api";
import { useStore } from "../store";
import type { CompareEvent, CompareRun } from "../types";
import { cx, formatNumber } from "../utils";
import { CheckIcon, PlayIcon } from "../icons";
import { Empty, Field, Panel } from "./ui";

interface LiveAnswer { model: string; content: string; done: boolean; input: number; output: number; method: string; error: string | null }

export function CompareView() {
  const { models, notify } = useStore();
  const [prompt, setPrompt] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [live, setLive] = useState<LiveAnswer[]>([]);
  const [run, setRun] = useState<CompareRun | null>(null);
  const [history, setHistory] = useState<CompareRun[]>([]);
  const [combined, setCombined] = useState("");
  const cancelRef = useRef<AbortController | null>(null);
  const currentRef = useRef<Map<string, LiveAnswer>>(new Map());

  const available = models.filter((m) => m.available);

  const load = useCallback(async () => {
    try {
      const d = await getJSON<{ runs: CompareRun[] }>("/api/compare/runs");
      setHistory(d.runs.slice(0, 12));
    } catch { /* offline */ }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const toggle = (name: string) => {
    if (running) return;
    if (selected.includes(name)) setSelected((s) => s.filter((x) => x !== name));
    else if (selected.length < 6) setSelected((s) => [...s, name]);
    else notify("At most 6 models per comparison.", "bad");
  };

  const start = async () => {
    if (!prompt.trim() || !selected.length || running) return;
    setRunning(true);
    setCombined("");
    setRun(null);
    currentRef.current = new Map(selected.map((m) => [m, { model: m, content: "", done: false, input: 0, output: 0, method: "estimated", error: null }]));
    const refresh = () => setLive([...currentRef.current.values()]);
    refresh();
    const controller = new AbortController();
    cancelRef.current = controller;
    try {
      await streamSSE<CompareEvent>("/api/compare/runs", {
        prompt: prompt.trim(), models: selected,
      }, (ev) => {
        if (ev.type === "run") {
          setRun((r) => r ? r : { id: ev.run_id, prompt, status: "running", selected_model: null, combined: "", created_at: "" });
        } else if (ev.type === "delta" && ev.model) {
          const cur = currentRef.current.get(ev.model);
          if (cur) { cur.content += ev.content ?? ""; refresh(); }
        } else if (ev.type === "answer_done" && ev.model) {
          const cur = currentRef.current.get(ev.model);
          if (cur) {
            cur.done = true; cur.input = ev.input_tokens ?? 0; cur.output = ev.output_tokens ?? 0;
            cur.method = ev.token_method ?? "estimated"; cur.error = ev.error ?? null;
            refresh();
          }
        } else if (ev.type === "done") {
          setRunning(false);
        } else if (ev.type === "error") {
          notify(ev.message ?? "Compare run failed", "bad");
          setRunning(false);
        }
      }, controller.signal);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Compare run failed", "bad");
      setRunning(false);
    } finally {
      cancelRef.current = null;
      setRunning(false);
      void load();
    }
  };

  const openRun = async (id: number) => {
    try {
      const r = await getJSON<CompareRun>(`/api/compare/runs/${id}`);
      setRun(r); setCombined(r.combined); setPrompt(r.prompt);
      setLive((r.answers ?? []).map((a) => ({ model: a.model, content: a.answer, done: a.status === "complete", input: a.input_tokens, output: a.output_tokens, method: a.token_method, error: a.error })));
    } catch (e) { notify(e instanceof Error ? e.message : "load failed", "bad"); }
  };

  const selectBest = async (model: string) => {
    if (!run) return;
    const ans = live.find((a) => a.model === model);
    if (!ans) return;
    try {
      const answers = (await getJSON<CompareRun>(`/api/compare/runs/${run.id}`)).answers ?? [];
      const aid = answers.find((a) => a.model === model)?.id;
      if (!aid) return;
      await sendJSON("POST", `/api/compare/runs/${run.id}/select`, { answer_id: aid });
      notify(`Selected ${model} as best answer.`, "good");
      void openRun(run.id);
    } catch (e) { notify(e instanceof Error ? e.message : "select failed", "bad"); }
  };

  const combine = async () => {
    if (!run) return;
    try {
      const r = await sendJSON<{ combined: string }>("POST", `/api/compare/runs/${run.id}/combine`);
      setCombined(r.combined);
      notify("Answers combined.", "good");
    } catch (e) { notify(e instanceof Error ? e.message : "combine failed", "bad"); }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
      <div>
        <h1 className="text-[17px] font-bold">Compare Mode</h1>
        <p className="text-[11.5px] text-faint mt-0.5">
          One prompt, several models. Side-by-side responses with real token usage — select the best answer or combine all answers.
        </p>
      </div>

      <Panel title="Run a comparison" sub={`${selected.length} model(s) selected`}>
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-4">
          <Field label="Prompt">
            <textarea className="input resize-none h-[88px]" placeholder="Ask the same question to every selected model…"
              value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </Field>
          <div className="flex flex-wrap content-start gap-1.5">
            {available.map((m) => (
              <button key={m.name}
                className={cx("chip !py-1.5 cursor-pointer", selected.includes(m.name) && "chip-accent")}
                onClick={() => toggle(m.name)}>
                {m.display_name}
              </button>
            ))}
            {available.length === 0 && <span className="text-[10.5px] text-faint">No models in catalog.</span>}
          </div>
        </div>
        <button className="btn btn-accent mt-3" disabled={!prompt.trim() || !selected.length || running}
          onClick={() => void start()}>
          <PlayIcon className="w-3.5 h-3.5" /> {running ? "Running…" : `Compare ${selected.length} model(s)`}
        </button>
      </Panel>

      {(live.length > 0 || run) && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {live.map((a) => (
              <Panel key={a.model} title={a.model}
                sub={a.done ? `in ${formatNumber(a.input)} · out ${formatNumber(a.output)} · ${a.method} · €0.00` : "streaming…"}
                right={run && (
                  <button className="btn !text-[10.5px] !py-1 !px-2" disabled={!a.done}
                    onClick={() => void selectBest(a.model)} title="Mark as the best answer">
                    <CheckIcon className="w-3 h-3" /> Best
                  </button>
                )}>
                {a.error ? (
                  <div className="text-[11.5px] text-bad">{a.error}</div>
                ) : (
                  <div className="text-[12px] text-dim whitespace-pre-wrap max-h-[340px] overflow-y-auto">
                    {a.content || (a.done ? "Empty response." : "Generating…")}
                  </div>
                )}
              </Panel>
            ))}
          </div>

          {run && live.every((a) => a.done) && (
            <div className="flex items-center gap-2">
              <button className="btn" onClick={() => void combine()} disabled={!run.id}>Combine answers</button>
              <span className="text-[10px] text-faint">Combine merges all answers into one coherent response (local model).</span>
            </div>
          )}

          {combined && (
            <Panel title="Combined answer" sub="Synthesized from all answers" right={run && <span className="chip">run #{run.id}</span>}>
              <div className="text-[12px] text-dim whitespace-pre-wrap max-h-[420px] overflow-y-auto">{combined}</div>
            </Panel>
          )}
        </>
      )}

      <Panel title="History" sub="Persisted comparison runs">
        {history.length === 0 ? <Empty text="No comparisons yet." /> : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
            {history.map((r) => (
              <button key={r.id} className="glass-soft rounded-xl px-3.5 py-3 text-left hover:bg-hover"
                onClick={() => void openRun(r.id)}>
                <div className="text-[12px] font-semibold text-ink truncate">{r.prompt.slice(0, 90)}</div>
                <div className="text-[9.5px] text-faint mt-1">#{r.id} · {r.status}
                  {r.selected_model ? ` · best: ${r.selected_model}` : ""}</div>
              </button>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
