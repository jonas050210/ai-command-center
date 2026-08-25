// Composer — model selector, autosizing textarea, token estimate,
// send / stop-generation.
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import { cx, estimateTokensClient, formatNumber } from "../utils";
import { CaretDownIcon, CheckIcon, SendIcon, StopIcon } from "../icons";

function ModelSelector({ value, onChange, disabled }: {
  value: string | null; onChange: (m: string) => void; disabled: boolean;
}) {
  const { models, settings } = useStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const available = models.filter((m) => m.available);
  const label = value ?? settings?.default_model ?? "Select model";

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        className={cx("btn !text-[11.5px] !py-[6px] max-w-[240px]", disabled && "opacity-50")}
        onClick={() => !disabled && setOpen((o) => !o)}
        title="Model for the next message"
      >
        <span className="truncate">{label}</span>
        <CaretDownIcon className="w-3 h-3 shrink-0" />
      </button>
      {open && (
        <div className="dropdown-panel bottom-[38px] left-0 w-[290px] anim-fade-up">
          <div className="p-2 border-b border-line micro-label">
            Models · {available.length} available
          </div>
          <div className="max-h-[300px] overflow-y-auto p-1">
            {available.length === 0 && (
              <div className="text-[11.5px] text-faint px-3 py-4 text-center">
                No models in catalog.
                <br />Open Model Center → Refresh (requires Ollama).
              </div>
            )}
            {available.map((m) => (
              <button key={`${m.provider}/${m.name}`}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg hover:bg-hover text-left"
                onClick={() => { onChange(m.name); setOpen(false); }}>
                <div className="flex-1 min-w-0">
                  <div className="text-[12.5px] text-ink truncate">{m.display_name}</div>
                  <div className="text-[10px] text-faint">
                    {m.provider} · {m.parameter_size ?? "Unknown size"} · {m.context_length
                      ? `${formatNumber(m.context_length)} ctx` : "ctx Unknown"}
                  </div>
                </div>
                {m.name === value && <CheckIcon className="w-3.5 h-3.5 text-accent shrink-0" />}
                {m.favorite && <span className="text-warn text-[10px]">★</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function Composer({ streaming, onSend, onStop, modelOverride }: {
  streaming: boolean;
  onSend: (content: string, model: string | null) => void;
  onStop: () => void;
  modelOverride?: string | null;
}) {
  const { currentModel, setCurrentModel, settings } = useStore();
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const model = modelOverride ?? currentModel ?? settings?.default_model ?? null;
  const estimate = estimateTokensClient(value);
  const canSend = value.trim().length > 0 && !streaming;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = Math.min(el.scrollHeight, 190) + "px";
  }, [value]);

  const suggestions = useMemo(() => [
    "Explain how a large language model generates text, with a small markdown table.",
    "Write a Python function that parses SSE streams, with comments.",
    "Give me a checklist for securing a local-first app.",
  ], []);

  return (
    <div className="shrink-0 px-6 pb-4 pt-1">
      {!value && !streaming && (
        <div className="flex flex-wrap gap-2 mb-2.5">
          {suggestions.map((s) => (
            <button key={s}
              className="chip hover:border-[rgba(69,227,255,0.4)] hover:text-ink transition-all !py-1.5"
              onClick={() => onSend(s, model)}>
              {s.length > 56 ? s.slice(0, 56) + "…" : s}
            </button>
          ))}
        </div>
      )}

      <div className="glass rounded-2xl p-2.5 focus-within:border-[rgba(69,227,255,0.4)] transition-colors">
        <textarea
          ref={ref}
          className="w-full bg-transparent resize-none outline-none text-[13.5px] leading-relaxed
            placeholder:text-faint px-2 pt-1 max-h-[190px]"
          rows={1}
          placeholder={streaming ? "Generating…" : "Message AI Command Center…  (Enter to send, Shift+Enter for newline)"}
          value={value}
          disabled={streaming}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && canSend) {
              e.preventDefault();
              onSend(value, model);
              setValue("");
            }
          }}
        />
        <div className="flex items-center gap-2 pt-1.5 px-1">
          <ModelSelector value={model} onChange={setCurrentModel} disabled={streaming} />
          {value && (
            <span className="text-[10px] text-faint" title="Pre-send estimate — never exact">
              ~{formatNumber(estimate)} tok <span className="text-warn">est</span>
            </span>
          )}
          <div className="flex-1" />
          {streaming ? (
            <button className="btn btn-danger !py-[7px]" onClick={onStop} title="Stop generation">
              <StopIcon className="w-3.5 h-3.5" /> Stop
            </button>
          ) : (
            <button
              className="btn btn-accent !py-[7px]"
              disabled={!canSend}
              onClick={() => { onSend(value, model); setValue(""); }}
              title="Send">
              <SendIcon className="w-3.5 h-3.5" /> Send
            </button>
          )}
        </div>
      </div>
      <div className="text-center text-[9.5px] text-faint mt-1.5 tracking-wide">
        Local inference via Ollama · strict €0 cost protection enforced server-side
      </div>
    </div>
  );
}
