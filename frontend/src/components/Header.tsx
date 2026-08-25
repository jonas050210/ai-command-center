// Header — brand, Ollama status, €0 protection badge, panel toggles.
import { useStore } from "../store";
import { formatEuro } from "../utils";
import { sendJSON } from "../api";
import { formatBytes } from "../utils";
import {
  ChevronLeftIcon, ChevronRightIcon, LogoIcon, SearchIcon, SettingsIcon,
  ShieldIcon, XIcon,
} from "../icons";

export function Header() {
  const { system, costs, settings, leftOpen, rightOpen, toggleLeft, toggleRight,
    setSettingsOpen, setPaletteOpen, refreshSystem, notify } = useStore();
  const ollama = system?.ollama;
  const running = ollama?.status === "running";
  const freeOnly = settings?.free_only ?? true;
  const loaded = ollama?.loaded ?? [];

  return (
    <header className="glass hud-header flex items-center gap-2 px-3 h-[54px] shrink-0 border-x-0 border-t-0"
      style={{ borderRadius: 0 }}>
      <button className="icon-btn" onClick={toggleLeft} title={leftOpen ? "Collapse sidebar" : "Expand sidebar"}>
        {leftOpen ? <ChevronLeftIcon /> : <ChevronRightIcon />}
      </button>

      <div className="flex items-center gap-2.5 text-accent select-none">
        <span className="logo-glow"><LogoIcon /></span>
        <div className="leading-none">
          <div className="font-display text-[13.5px] font-bold tracking-[0.14em] text-ink">AI COMMAND CENTER</div>
          <div className="text-[9px] tracking-[0.28em] text-faint mt-[3px] font-mono">LOCAL · €0 · SANDBOXED</div>
        </div>
      </div>

      {/* command palette trigger */}
      <button
        className="hidden md:flex items-center gap-2.5 ml-4 pl-3 pr-2 py-[5px] rounded-lg
          border border-line2 bg-[rgba(7,10,16,0.55)] text-faint text-[11.5px]
          hover:border-[rgba(69,227,255,0.35)] hover:text-dim transition-all min-w-[190px]"
        onClick={() => setPaletteOpen(true)}
        title="Command palette — navigate, search chats, switch models">
        <SearchIcon className="w-3.5 h-3.5 shrink-0" />
        <span className="flex-1 text-left">Commands, chats, models…</span>
        <kbd className="kbd">Ctrl K</kbd>
      </button>

      <div className="flex-1" />

      <div className="hud-scroll hidden sm:flex items-center gap-2 max-w-[46vw] pr-1">
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

      {loaded.map((m) => (
        <div key={m.name} className="chip"
          title={`Resident in ${m.device === "gpu" ? "VRAM" : "RAM/CPU"} · ${formatBytes(m.size_vram ?? m.size_bytes ?? null)}`}>
          <span className={`inline-block w-[7px] h-[7px] rounded-full ${m.device === "gpu" ? "bg-good" : "bg-warn"}`} />
          <span className="font-mono text-ink">{m.name}</span>
          <span className="text-faint">{m.device === "gpu" ? "GPU" : "CPU"}</span>
          <button className="icon-btn !w-4 !h-4 !p-0" title="Unload from VRAM"
            onClick={() => {
              void sendJSON("POST", "/api/models/unload", { name: m.name, provider: "ollama" })
                .then(() => { notify(`Unloaded ${m.name}`); void refreshSystem(); })
                .catch((e) => notify(e instanceof Error ? e.message : "Unload failed", "bad"));
            }}>
            <XIcon className="w-2.5 h-2.5" />
          </button>
        </div>
      ))}

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
