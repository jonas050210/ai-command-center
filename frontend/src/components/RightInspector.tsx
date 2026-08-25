// Right inspector — Model / Context / Tokens / Cost / Status.
// Collapsible sections; honest values (Unknown when undeterminable,
// exact vs estimated always labelled).
import { useState } from "react";
import { useStore } from "../store";
import { cx, formatEuro, formatNumber } from "../utils";
import { ChevronDownIcon, CpuIcon, KeyboardIcon, ShieldIcon, ZapIcon } from "../icons";

function Section({ title, children, defaultOpen = true, right }: {
  title: string; children: React.ReactNode; defaultOpen?: boolean; right?: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-line">
      <button className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-hover transition-colors"
        onClick={() => setOpen((o) => !o)}>
        <span className="micro-label">{title}</span>
        <span className="flex items-center gap-1.5 text-faint">
          {right}
          <ChevronDownIcon className={cx("w-3 h-3 transition-transform", !open && "-rotate-90")} />
        </span>
      </button>
      {open && <div className="px-4 pb-3.5 anim-fade-in">{children}</div>}
    </div>
  );
}

function Row({ label, value, tone, title }: {
  label: string; value: React.ReactNode; tone?: string; title?: string;
}) {
  return (
    <div className="flex items-center justify-between py-[3px] text-[12px]" title={title}>
      <span className="text-faint">{label}</span>
      <span className={cx("font-medium text-ink tabular-nums text-right", tone)}>{value}</span>
    </div>
  );
}

export function RightInspector() {
  const { activeConv, settings, costs, tokens, system, models, currentModel,
    patchConversation } = useStore();
  const [sysDraft, setSysDraft] = useState<string | null>(null);

  const modelName = activeConv?.model ?? currentModel ?? settings?.default_model ?? null;
  const modelRow = models.find((m) => m.name === modelName) ?? null;
  const ctxLimit = modelRow?.context_length ?? settings?.num_ctx ?? null;
  const usedTokens = (activeConv?.total_input_tokens ?? 0) + (activeConv?.total_output_tokens ?? 0);
  const ctxPct = ctxLimit ? Math.min(100, (usedTokens / ctxLimit) * 100) : null;
  const ollama = system?.ollama;

  return (
    <aside className="glass border-y-0 border-r-0 w-[306px] shrink-0 flex flex-col min-h-0 overflow-y-auto"
      style={{ borderRadius: 0 }}>

      <Section title="Model" right={modelRow?.is_free ? <span className="chip chip-good !text-[8.5px] !py-[1px]">€0 FREE</span> : undefined}>
        <div className="glass-soft rounded-xl p-3 mb-2">
          <div className="flex items-center gap-2">
            <CpuIcon className="w-4 h-4 text-accent shrink-0" />
            <div className="text-[12.5px] font-semibold text-ink truncate">
              {modelName ?? <span className="text-faint">No model selected</span>}
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            <span className="chip !text-[9.5px]">{modelRow?.provider ?? "ollama"}</span>
            <span className="chip !text-[9.5px]">{modelRow?.location ?? "local"}</span>
            {modelRow?.quantization && <span className="chip !text-[9.5px]">{modelRow.quantization}</span>}
            {modelRow && !modelRow.available && <span className="chip chip-bad !text-[9.5px]">unavailable</span>}
          </div>
        </div>
        <Row label="Context length" value={modelRow?.context_length ? formatNumber(modelRow.context_length) : "Unknown"} />
        <Row label="Parameters" value={modelRow?.parameter_size ?? "Unknown"} />
        <Row label="Speed (measured)" title={modelRow?.measured_tps ? "Last measured throughput" : "Not measured yet — run a Test in Model Center"}
          value={modelRow?.measured_tps ? `${modelRow.measured_tps.toFixed(1)} tok/s` : "Unknown"} />
        <Row label="Cost" tone="text-good" value="€0.00" />
      </Section>

      <Section title="Context">
        {ctxPct !== null ? (
          <>
            <div className={cx("meter mb-1.5", ctxPct > 80 && "warn")}>
              <div style={{ width: `${Math.max(2, ctxPct)}%` }} />
            </div>
            <Row label="Used in this chat"
              title="Sum of exact tokens stored for this conversation"
              value={<>{formatNumber(usedTokens)} <span className="text-faint">/ {formatNumber(ctxLimit)}</span></>} />
            <Row label="Window" value={`${ctxPct.toFixed(1)}%`} tone={ctxPct > 80 ? "text-warn" : undefined} />
          </>
        ) : (
          <div className="text-[11.5px] text-faint py-1">Context window: Unknown</div>
        )}
        <div className="mt-2.5">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10.5px] text-faint font-medium">System prompt (this chat)</span>
            {sysDraft !== null && (
              <button className="text-[10px] text-accent hover:underline"
                onClick={async () => {
                  if (activeConv) {
                    await patchConversation(activeConv.id, { system_prompt: sysDraft || null });
                    setSysDraft(null);
                  }
                }}>
                Save
              </button>
            )}
          </div>
          <textarea
            className="input !text-[11.5px] !leading-relaxed resize-none h-[68px]"
            placeholder={activeConv ? "Optional system prompt for this chat…" : "Start a chat to set a system prompt"}
            disabled={!activeConv}
            value={sysDraft ?? activeConv?.system_prompt ?? ""}
            onChange={(e) => setSysDraft(e.target.value)}
          />
          {settings?.custom_instructions && (
            <div className="mt-1.5 text-[10px] text-faint" title="Global custom instructions are appended to every chat">
              + global custom instructions active
            </div>
          )}
        </div>
      </Section>

      <Section title="Tokens">
        <Row label="This chat — in" value={formatNumber(activeConv?.total_input_tokens ?? 0)} />
        <Row label="This chat — out" value={formatNumber(activeConv?.total_output_tokens ?? 0)} />
        <div className="h-px bg-line my-1.5" />
        <Row label="Session (in / out)"
          title="Since backend started"
          value={`${formatNumber(tokens?.session.input_tokens ?? 0)} / ${formatNumber(tokens?.session.output_tokens ?? 0)}`} />
        <Row label="Lifetime (in / out)"
          value={`${formatNumber(tokens?.total.input_tokens ?? 0)} / ${formatNumber(tokens?.total.output_tokens ?? 0)}`} />
        <div className="text-[10px] text-faint mt-1.5 leading-snug">
          Counts reported by Ollama are labelled <span className="text-good font-semibold">exact</span>;
          pre-send guesses are labelled <span className="text-warn font-semibold">estimated</span>.
        </div>
      </Section>

      <Section title="Cost" right={<ShieldIcon className="w-3 h-3 text-good" />}>
        <div className="glass-soft rounded-xl p-3">
          <div className="grid grid-cols-3 gap-2 text-center">
            {([
              ["Current", costs?.current],
              ["Session", costs?.session],
              ["Total", costs?.total],
            ] as const).map(([label, v]) => (
              <div key={label}>
                <div className="text-[14px] font-bold text-good tabular-nums">
                  {formatEuro(v ?? 0, costs?.currency)}
                </div>
                <div className="micro-label !text-[8.5px] mt-0.5">{label}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-2 flex items-center gap-1.5 text-[10.5px]">
          <span className={cx("w-[7px] h-[7px] rounded-full", settings?.free_only ? "bg-good" : "bg-warn")} />
          <span className="text-dim">
            {settings?.free_only
              ? "Free-only mode ON — paid models are blocked before any request."
              : `Free-only OFF — budget cap ${formatEuro(settings?.max_spend ?? 0)}.`}
          </span>
        </div>
      </Section>

      <Section title="Status">
        <Row label="Ollama" tone={ollama?.status === "running" ? "text-good" : "text-bad"}
          value={ollama?.status ?? "unknown"} />
        <Row label="Version" value={ollama?.version ?? "Unknown"} />
        <Row label="Latency" value={ollama?.latency_ms != null ? `${ollama.latency_ms.toFixed(0)} ms` : "Unknown"} />
        <Row label="Host" value={<span className="text-[10.5px]">{ollama?.host ?? "Unknown"}</span>} />
        <Row label="Models installed" value={ollama?.models_count != null ? formatNumber(ollama.models_count) : "Unknown"} />
        {(system?.providers ?? []).filter((p) => !p.is_local).map((p) => (
          <Row key={p.name} label={p.display_name}
            tone={p.configured && p.last_status === "running" ? "text-good" : "text-warn"}
            value={p.configured ? (p.last_status ?? "unknown") : "no key"} />
        ))}
        <div className="h-px bg-line my-1.5" />
        <Row label="API uptime"
          value={system ? `${Math.floor(system.metrics.uptime_s / 60)}m ${Math.floor(system.metrics.uptime_s % 60)}s` : "Unknown"} />
        <Row label="Chat requests" value={formatNumber(system?.metrics.chat_requests ?? 0)} />
        <Row label="Blocked paid req." tone="text-good"
          title="Paid requests blocked by the CostGuard (backend, pre-network)"
          value={formatNumber(system?.metrics.blocked_paid_requests ?? 0)} />
        <div className="mt-2 flex items-center gap-1.5 text-[10px] text-faint">
          <ZapIcon className="w-3 h-3" />
          <span>Enforcement: backend CostGuard · UI never bypasses it</span>
        </div>
      </Section>

      <Section title="Shortcuts" defaultOpen={false}
        right={<KeyboardIcon className="w-3 h-3 text-faint" />}>
        {([
          ["Ctrl K", "Command palette"],
          ["Ctrl B", "Toggle sidebar"],
          ["Ctrl .", "Toggle inspector"],
          ["Ctrl ,", "Settings"],
          ["Ctrl Alt N", "New chat"],
          ["Ctrl /", "Shortcuts help"],
        ] as const).map(([keys, label]) => (
          <div key={keys} className="flex items-center justify-between py-[3px] text-[11.5px]">
            <span className="text-faint">{label}</span>
            <kbd className="kbd">{keys}</kbd>
          </div>
        ))}
      </Section>
    </aside>
  );
}
