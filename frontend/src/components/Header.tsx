// Header — brand, Ollama status, €0 protection badge, panel toggles.
import { useStore } from "../store";
import { formatEuro } from "../utils";
import {
  ChevronLeftIcon, ChevronRightIcon, LogoIcon, SettingsIcon, ShieldIcon,
} from "../icons";

export function Header() {
  const { system, costs, settings, leftOpen, rightOpen, toggleLeft, toggleRight,
    setSettingsOpen } = useStore();
  const ollama = system?.ollama;
  const running = ollama?.status === "running";
  const freeOnly = settings?.free_only ?? true;

  return (
    <header className="glass flex items-center gap-2 px-3 h-[52px] shrink-0 border-x-0 border-t-0"
      style={{ borderRadius: 0 }}>
      <button className="icon-btn" onClick={toggleLeft} title={leftOpen ? "Collapse sidebar" : "Expand sidebar"}>
        {leftOpen ? <ChevronLeftIcon /> : <ChevronRightIcon />}
      </button>

      <div className="flex items-center gap-2.5 text-accent select-none">
        <LogoIcon />
        <div className="leading-none">
          <div className="text-[13px] font-bold tracking-[0.18em] text-ink">AI COMMAND CENTER</div>
          <div className="text-[9.5px] tracking-[0.22em] text-faint mt-[3px]">LOCAL-FIRST AI WORKSPACE</div>
        </div>
      </div>

      <div className="flex-1" />

      {/* Ollama status */}
      <div className="chip" title={ollama?.detail ?? `Ollama @ ${ollama?.host ?? "Unknown"}`}>
        <span className={`inline-block w-[7px] h-[7px] rounded-full ${running ? "bg-good" : "bg-bad pulse-soft"}`} />
        <span>Ollama</span>
        <span className="text-ink font-semibold">
          {running ? `running${ollama?.version ? ` v${ollama.version}` : ""}` : "unavailable"}
        </span>
        {running && ollama?.latency_ms != null && (
          <span className="text-faint">{ollama.latency_ms.toFixed(0)}ms</span>
        )}
      </div>

      {/* Cloud providers (OpenRouter) — never silently active */}
      {(system?.providers ?? []).filter((p) => !p.is_local).map((p) => {
        const online = p.configured && p.last_status === "running";
        return (
          <div key={p.name} className="chip"
            title={p.configured ? `${p.display_name} · ${p.last_status ?? "unknown"}` : `${p.display_name} · no API key stored`}>
            <span className={`inline-block w-[7px] h-[7px] rounded-full ${online ? "bg-good" : "bg-warn"}`} />
            <span>{p.name}</span>
            <span className="text-ink font-semibold">
              {p.configured ? (p.last_status === "running" ? "online" : p.last_status ?? "unknown") : "no key"}
            </span>
          </div>
        );
      })}

      {/* €0 protection */}
      <div className={`chip ${freeOnly ? "chip-good" : "chip-warn"}`}
        title="Strict €0 cost protection — enforced in the backend before any provider request">
        <ShieldIcon className="w-3 h-3" />
        {freeOnly ? "FREE-ONLY" : `BUDGET ${formatEuro(settings?.max_spend ?? 0)}`}
      </div>

      <div className="chip" title="Session spend / total spend (EUR)">
        <span className="text-faint">Session</span>
        <span className="text-ink font-semibold">{formatEuro(costs?.session ?? 0, costs?.currency)}</span>
        <span className="text-faint">·</span>
        <span className="text-faint">Total</span>
        <span className="text-ink font-semibold">{formatEuro(costs?.total ?? 0, costs?.currency)}</span>
      </div>

      <button className="icon-btn" onClick={() => setSettingsOpen(true)} title="Settings">
        <SettingsIcon />
      </button>
      <button className="icon-btn" onClick={toggleRight} title={rightOpen ? "Collapse inspector" : "Expand inspector"}>
        {rightOpen ? <ChevronRightIcon /> : <ChevronLeftIcon />}
      </button>
    </header>
  );
}
