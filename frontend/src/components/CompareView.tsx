// Compare Mode — one prompt to 2–4 models, streamed side by side.
// Locals run sequentially per provider (VRAM safety, shown as "queued"),
// clouds run in parallel. A CostGuard block fails only its own slot.
import { useCallback, useRef, useState } from "react";
import { streamSSE, ApiError } from "../api";
import { useStore } from "../store";
import type { CompareEvent } from "../types";
import { cx, formatNumber } from "../utils";
import { AlertIcon, GaugeIcon, SendIcon, StopIcon, ZapIcon } from "../icons";
import { Markdown } from "./Markdown";

const MAX_SLOTS = 4;

interface Slot {
  index: number;
  provider: string;
  model: string;
  isLocal: boolean;
  status: "idle" | "queued" | "running" | "complete" | "stopped" | "error" | "cancelled";
  content: string;
  inTok: number | null;
  outTok: number | null;
  tps: number | null;
  elapsed: number | null;
  error: string | null;
  method: string | null;
}

export function CompareView() {
  const { models, notify, refreshCosts, refreshTokens } = useStore();
  const [selected, setSelected] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [running, setRunning] = useState(false);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [bannerError, setBannerError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const toggleModel = useCallback((key: string) => {
    setSelected((cur) => {
      if (cur.includes(key)) return cur.filter((k) => k !== key);
      if (cur.length >= MAX_SLOTS) return cur;
      return [...cur, key];
    });
  }, []);

  const modelLabel = useCallback((key: string) => {
    const [provider, name] = key.includes("/") ? key.split("/", 2) : ["", key];
    return { provider, name };
  }, []);

  const run = useCallback(async () => {
    if (running || selected.length < 2 || !prompt.trim()) return;
    setRunning(true);
    setBannerError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    setSlots(selected.map((key, i) => {
      const { provider, name } = modelLabel(key);
      return { index: i, provider, model: name, isLocal: false, status: "idle",
        content: "", inTok: null, outTok: null, tps: null, elapsed: null,
        error: null, method: null };
    }));

    const patch = (index: number, partial: Partial<Slot>) =>
      setSlots((cur) => cur.map((s) => (s.index === index ? { ...s, ...partial } : s)));

    try {
      await streamSSE("/api/compare/runs", { prompt: prompt.trim(), models: selected },
        (ev: CompareEvent) => {
          if (ev.type === "meta") {
            for (const c of ev.comparisons) {
              patch(c.index, { provider: c.provider, model: c.model, isLocal: c.is_local });
            }
          } else if (ev.type === "slot_status") {
            patch(ev.index, { status: ev.status as Slot["status"] });
          } else if (ev.type === "delta") {
            setSlots((cur) => cur.map((s) => (s.index === ev.index
              ? { ...s, status: "running", content: s.content + ev.content } : s)));
          } else if (ev.type === "model_done") {
            patch(ev.index, {
              status: ev.status as Slot["status"],
              inTok: ev.input_tokens ?? null, outTok: ev.output_tokens ?? null,
              tps: ev.tokens_per_second ?? null, elapsed: ev.elapsed_s ?? null,
              method: ev.token_method ?? null,
              error: ev.status === "error" ? (ev.message ?? ev.code ?? "error") : null,
            });
          } else if (ev.type === "error") {
            setBannerError(`${ev.code}: ${ev.message}`);
          }
        }, controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) {
        setBannerError(e instanceof ApiError ? `${e.code}: ${e.message}`
          : "Lost connection to the backend.");
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
      void refreshCosts();
      void refreshTokens();
    }
  }, [running, selected, prompt, modelLabel, refreshCosts, refreshTokens]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    notify("Compare stopped", "info");
  }, [notify]);

  const groups = (() => {
    const locals = models.filter((m) => m.is_local);
    const clouds = models.filter((m) => !m.is_local);
    return { locals, clouds };
  })();

  const ModelPill = ({ m }: { m: (typeof models)[number] }) => {
    const key = `${m.provider}/${m.name}`;
    const active = selected.includes(key);
    return (
      <button key={key} disabled={running || (!active && selected.length >= MAX_SLOTS)}
        onClick={() => toggleModel(key)}
        className={cx("chip !text-[10px] !px-2.5 !py-1 transition-all",
          active ? "chip-good !border-[rgba(52,211,153,0.6)]" : "hover:border-line2",
          !active && selected.length >= MAX_SLOTS && "opacity-40")}>
        {active && "✓ "}{m.name}
        <span className="opacity-60 ml-1">{m.provider}</span>
      </button>
    );
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-6 pt-4 pb-3 border-b border-line space-y-3">
        <div className="flex items-center gap-2.5">
          <GaugeIcon className="w-[18px] h-[18px] text-accent" />
          <span className="text-[15px] font-bold">Compare Mode</span>
          <span className="chip !text-[9px]">{selected.length}/{MAX_SLOTS} models</span>
          <span className="text-[9.5px] text-faint ml-auto">
            local models run one-at-a-time (VRAM) · clouds parallel · CostGuard per slot
          </span>
        </div>
        <div className="space-y-1.5">
          {groups.locals.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="micro-label w-14 shrink-0">Local</span>
              {groups.locals.map((m) => <ModelPill key={`${m.provider}/${m.name}`} m={m} />)}
            </div>
          )}
          {groups.clouds.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="micro-label w-14 shrink-0">Cloud</span>
              {groups.clouds.map((m) => <ModelPill key={`${m.provider}/${m.name}`} m={m} />)}
            </div>
          )}
          {models.length === 0 && (
            <div className="text-[11px] text-faint">No models synced — open Model Center first.</div>
          )}
        </div>
        <div className="glass-soft rounded-xl border border-line2 focus-within:border-[rgba(69,227,255,0.45)] transition-colors flex items-end gap-2 p-2">
          <textarea
            className="flex-1 bg-transparent resize-none outline-none px-2 pt-1 text-[13px] min-h-[40px] max-h-[120px]"
            placeholder="One prompt for every selected model…"
            value={prompt} disabled={running}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void run(); }
            }}
          />
          {running ? (
            <button className="btn btn-danger !text-[11px] !py-1.5 !px-3" onClick={stop}>
              <StopIcon className="w-3.5 h-3.5" /> Stop
            </button>
          ) : (
            <button className="btn btn-accent !text-[11px] !py-1.5 !px-3"
              disabled={selected.length < 2 || !prompt.trim()}
              onClick={() => void run()}>
              <SendIcon className="w-3.5 h-3.5" /> Compare
            </button>
          )}
        </div>
      </div>

      {bannerError && (
        <div className="mx-6 mt-3 flex items-start gap-2.5 rounded-xl border px-4 py-3
          border-[rgba(248,113,113,0.4)] bg-[rgba(248,113,113,0.07)]">
          <AlertIcon className="w-4 h-4 text-bad mt-[1px] shrink-0" />
          <div className="text-[12.5px] text-bad/90">{bannerError}</div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto min-h-0 p-4">
        {slots.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center anim-fade-up">
            <GaugeIcon className="w-10 h-10 text-accent opacity-80 mb-4" />
            <h1 className="text-[17px] font-bold">Race your models</h1>
            <p className="text-[12px] text-dim mt-1.5 max-w-[440px]">
              Pick 2–{MAX_SLOTS} models, send one prompt, watch the answers stream
              side by side — with real token counts and speed per model.
            </p>
          </div>
        ) : (
          <div className={cx("grid gap-3 h-min",
            slots.length === 2 ? "grid-cols-2" : slots.length === 3 ? "grid-cols-3"
              : slots.length >= 4 ? "grid-cols-2 xl:grid-cols-4" : "grid-cols-1")}>
            {slots.map((s) => (
              <div key={s.index} className="glass-soft rounded-xl border border-line flex flex-col min-h-[220px]">
                <div className="px-3.5 pt-3 pb-2 border-b border-line flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-[11.5px] font-semibold text-ink truncate max-w-[70%]">
                    {s.model}
                  </span>
                  <span className={cx("chip !text-[8.5px]", s.isLocal ? "chip-good" : "")}>
                    {s.isLocal ? "LOCAL" : s.provider}
                  </span>
                  <span className={cx("chip !text-[8.5px] ml-auto",
                    s.status === "complete" && "chip-good",
                    (s.status === "queued" || s.status === "stopped") && "chip-warn",
                    s.status === "error" && "text-bad border-[rgba(248,113,113,0.35)] bg-[rgba(248,113,113,0.1)]")}>
                    {s.status === "running" ? (
                      <span className="flex items-center gap-1"><ZapIcon className="w-2.5 h-2.5" />streaming</span>
                    ) : s.status}
                  </span>
                </div>
                <div className="flex-1 overflow-y-auto px-3.5 py-2.5 text-[12px] leading-relaxed min-h-[80px]">
                  {s.error ? (
                    <div className="text-bad/90 text-[11px] whitespace-pre-wrap">{s.error}</div>
                  ) : s.content ? (
                    <Markdown content={s.content} />
                  ) : (
                    <div className="text-[10.5px] text-faint">
                      {s.status === "queued" ? "Queued — waiting for the local provider (VRAM safety)."
                        : s.status === "idle" ? "Assigned…" : "…"}
                    </div>
                  )}
                </div>
                <div className="px-3.5 py-2 border-t border-line text-[9px] text-faint flex items-center gap-2 flex-wrap">
                  {s.inTok !== null && <span>{formatNumber(s.inTok)} in</span>}
                  {s.outTok !== null && <span>{formatNumber(s.outTok)} out</span>}
                  {s.method && <span className="chip !text-[8px] !py-[0px]">{s.method}</span>}
                  {s.tps !== null && <span className="ml-auto">{s.tps} tok/s</span>}
                  {s.elapsed !== null && <span>{s.elapsed}s</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
