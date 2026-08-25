// Command palette (Ctrl+K) — every item is REAL: navigate to views, open
// conversations, switch the active model, run app actions. 100% keyboard
// navigable: ↑/↓ move · Enter run · Esc close.
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import { cx, timeAgo } from "../utils";
import type { View } from "../types";
import {
  ArchiveIcon, BotIcon, ChatIcon, ChevronLeftIcon, ChevronRightIcon,
  CodeIcon, FolderIcon, GaugeIcon, GitIcon, KeyboardIcon, ModelsIcon, PlusIcon,
  ResearchIcon, SearchIcon, SettingsIcon, StarIcon, UsersIcon,
} from "../icons";

interface Item {
  id: string;
  section: string;
  icon: React.ReactNode;
  label: string;
  hint?: string;          // right-aligned detail (kbd or metadata)
  keywords: string;
  run: () => void;
}

/** Simple real ranking: exact-prefix > word-prefix > substring, then label. */
function score(query: string, text: string): number {
  if (!query) return 1;
  const t = text.toLowerCase();
  const q = query.toLowerCase();
  if (t.startsWith(q)) return 100 - (t.length - q.length) * 0.01;
  const wordIdx = t.search(new RegExp(`\\b${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
  if (wordIdx >= 0) return 60 - wordIdx * 0.1;
  const idx = t.indexOf(q);
  if (idx >= 0) return 30 - idx * 0.1;
  return -1;
}

const VIEW_META: Array<{ view: View; label: string; icon: React.ReactNode }> = [
  { view: "chat", label: "Chat", icon: <ChatIcon className="w-4 h-4" /> },
  { view: "models", label: "Model Center", icon: <ModelsIcon className="w-4 h-4" /> },
  { view: "agent", label: "Agent Mode", icon: <BotIcon className="w-4 h-4" /> },
  { view: "coder", label: "Coder Mode", icon: <CodeIcon className="w-4 h-4" /> },
  { view: "compare", label: "Compare Mode", icon: <GaugeIcon className="w-4 h-4" /> },
  { view: "team", label: "Team Mode", icon: <UsersIcon className="w-4 h-4" /> },
  { view: "research", label: "Research", icon: <ResearchIcon className="w-4 h-4" /> },
  { view: "projects", label: "Projects", icon: <FolderIcon className="w-4 h-4" /> },
  { view: "git", label: "Git / GitHub", icon: <GitIcon className="w-4 h-4" /> },
];

export function CommandPalette() {
  const {
    paletteOpen, setPaletteOpen, setHelpOpen, view, setView,
    conversations, setActiveId, models, currentModel, setCurrentModel,
    toggleLeft, toggleRight, setSettingsOpen, setShowArchived, setConvSearch,
  } = useStore();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (paletteOpen) {
      setQuery("");
      setActive(0);
      window.setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [paletteOpen]);

  const items = useMemo<Item[]>(() => {
    const close = () => setPaletteOpen(false);
    const out: Item[] = [];

    const go = (v: View) => () => { setView(v); close(); };
    out.push({
      id: "act-new-chat", section: "Actions",
      icon: <PlusIcon className="w-4 h-4" />, label: "New chat",
      hint: "Ctrl+Alt+N", keywords: "new chat conversation create",
      run: () => { setActiveId(null); setView("chat"); close(); },
    });
    out.push({
      id: "act-settings", section: "Actions",
      icon: <SettingsIcon className="w-4 h-4" />, label: "Open settings",
      hint: "Ctrl+,", keywords: "settings preferences config",
      run: () => { setSettingsOpen(true); close(); },
    });
    out.push({
      id: "act-left", section: "Actions",
      icon: <ChevronLeftIcon className="w-4 h-4" />, label: "Toggle sidebar",
      hint: "Ctrl+B", keywords: "toggle sidebar left panel",
      run: () => { toggleLeft(); close(); },
    });
    out.push({
      id: "act-right", section: "Actions",
      icon: <ChevronRightIcon className="w-4 h-4" />, label: "Toggle inspector",
      hint: "Ctrl+.", keywords: "toggle inspector right panel details",
      run: () => { toggleRight(); close(); },
    });
    out.push({
      id: "act-archived", section: "Actions",
      icon: <ArchiveIcon className="w-4 h-4" />, label: "Show archived chats",
      keywords: "archived chats history",
      run: () => { setShowArchived(true); setView("chat"); close(); },
    });
    out.push({
      id: "act-shortcuts", section: "Actions",
      icon: <KeyboardIcon className="w-4 h-4" />, label: "Keyboard shortcuts",
      hint: "Ctrl+/", keywords: "keyboard shortcuts help keys",
      run: () => { setHelpOpen(true); close(); },
    });

    for (const meta of VIEW_META) {
      out.push({
        id: `go-${meta.view}`, section: "Go to",
        icon: meta.icon, label: meta.label,
        hint: view === meta.view ? "current" : undefined,
        keywords: `go to open ${meta.label} ${meta.view} view page`,
        run: go(meta.view),
      });
    }

    const convs = conversations.slice(0, 60);
    for (const c of convs) {
      out.push({
        id: `conv-${c.id}`, section: "Chats",
        icon: c.favorite ? <StarIcon className="w-4 h-4 text-warn" />
          : <ChatIcon className="w-4 h-4" />,
        label: c.title,
        hint: timeAgo(c.updated_at),
        keywords: `chat conversation open ${c.title} ${c.model ?? ""}`,
        run: () => { setConvSearch(""); setActiveId(c.id); setView("chat"); close(); },
      });
    }

    const available = models.filter((m) => m.available).slice(0, 40);
    for (const m of available) {
      out.push({
        id: `model-${m.provider}/${m.name}`, section: "Models",
        icon: <ModelsIcon className="w-4 h-4" />,
        label: m.display_name,
        hint: m.name === currentModel ? "active" : m.provider,
        keywords: `model switch use ${m.name} ${m.display_name} ${m.provider}`,
        run: () => { setCurrentModel(m.name); setView("chat"); close(); },
      });
    }
    return out;
  }, [paletteOpen, view, conversations, models, currentModel, setView,
    setActiveId, setCurrentModel, toggleLeft, toggleRight, setSettingsOpen,
    setPaletteOpen, setHelpOpen, setShowArchived, setConvSearch]);

  const filtered = useMemo(() => {
    const q = query.trim();
    const ranked = items
      .map((it) => ({ it, s: Math.max(score(q, it.label), score(q, it.keywords) - 12) }))
      .filter((r) => r.s >= 0);
    if (!q) return ranked.map((r) => r.it);
    ranked.sort((a, b) => b.s - a.s);
    // stable section grouping after ranking
    const bySection = new Map<string, Item[]>();
    for (const r of ranked) {
      const list = bySection.get(r.it.section) ?? [];
      list.push(r.it);
      bySection.set(r.it.section, list);
    }
    const order = ["Actions", "Go to", "Chats", "Models"];
    return order.flatMap((sec) => bySection.get(sec) ?? []);
  }, [items, query]);

  useEffect(() => { setActive(0); }, [query]);

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>("[data-active='1']");
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!paletteOpen) return null;

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, filtered.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(filtered.length - 1); }
    else if (e.key === "Enter") { e.preventDefault(); const it = filtered[active]; if (it) it.run(); }
    else if (e.key === "Escape") { e.preventDefault(); setPaletteOpen(false); }
  };

  let flatIdx = -1;
  let lastSection = "";

  return (
    <div className="palette-backdrop anim-fade-in" onMouseDown={() => setPaletteOpen(false)}>
      <div className="palette-panel anim-fade-up" onMouseDown={(e) => e.stopPropagation()}
        role="dialog" aria-label="Command palette">
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-line">
          <SearchIcon className="w-4 h-4 text-faint shrink-0" />
          <input
            ref={inputRef}
            className="flex-1 bg-transparent outline-none text-[13.5px] placeholder:text-faint"
            placeholder="Type a command, chat, or model…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
          />
          <kbd className="kbd">Esc</kbd>
        </div>

        <div ref={listRef} className="max-h-[380px] overflow-y-auto p-1.5" onKeyDown={onKey}>
          {filtered.length === 0 && (
            <div className="text-center text-[12px] text-faint py-8">
              No matches for “{query}”.
            </div>
          )}
          {filtered.map((it) => {
            flatIdx += 1;
            const idx = flatIdx;
            const showSection = it.section !== lastSection;
            lastSection = it.section;
            return (
              <div key={it.id}>
                {showSection && <div className="micro-label px-2.5 pt-2 pb-1">{it.section}</div>}
                <button
                  data-active={idx === active ? "1" : "0"}
                  className={cx(
                    "w-full flex items-center gap-2.5 px-2.5 py-[7px] rounded-lg text-left",
                    idx === active ? "bg-accentdim text-accent" : "text-dim hover:bg-hover hover:text-ink")}
                  onMouseEnter={() => setActive(idx)}
                  onClick={() => it.run()}
                >
                  <span className="shrink-0 opacity-80">{it.icon}</span>
                  <span className="flex-1 min-w-0 truncate text-[12.5px] font-medium">{it.label}</span>
                  {it.hint && (
                    <span className={cx("shrink-0 text-[10px]",
                      it.hint.length <= 10 && (it.hint.includes("+") || it.hint === "Esc")
                        ? "kbd" : "text-faint")}>{it.hint}</span>
                  )}
                </button>
              </div>
            );
          })}
        </div>

        <div className="flex items-center gap-3 px-4 py-2 border-t border-line text-[10px] text-faint">
          <span><kbd className="kbd">↑↓</kbd> navigate</span>
          <span><kbd className="kbd">Enter</kbd> select</span>
          <span><kbd className="kbd">Esc</kbd> close</span>
          <span className="flex-1" />
          <span>{filtered.length} result{filtered.length === 1 ? "" : "s"}</span>
        </div>
      </div>
    </div>
  );
}

const SHORTCUTS: Array<{ keys: string[]; label: string }> = [
  { keys: ["Ctrl", "K"], label: "Command palette" },
  { keys: ["Ctrl", "B"], label: "Toggle sidebar" },
  { keys: ["Ctrl", "."], label: "Toggle inspector" },
  { keys: ["Ctrl", ","], label: "Settings" },
  { keys: ["Ctrl", "Alt", "N"], label: "New chat" },
  { keys: ["Ctrl", "/"], label: "This help" },
  { keys: ["Enter"], label: "Send message (in composer)" },
  { keys: ["Shift", "Enter"], label: "Newline (in composer)" },
];

export function ShortcutsHelp() {
  const { helpOpen, setHelpOpen } = useStore();
  if (!helpOpen) return null;
  return (
    <div className="palette-backdrop anim-fade-in" onMouseDown={() => setHelpOpen(false)}>
      <div className="palette-panel anim-fade-up !w-[420px]" onMouseDown={(e) => e.stopPropagation()}
        role="dialog" aria-label="Keyboard shortcuts">
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-line">
          <KeyboardIcon className="w-4 h-4 text-accent" />
          <span className="text-[13px] font-semibold flex-1">Keyboard shortcuts</span>
          <kbd className="kbd">Esc</kbd>
        </div>
        <div className="p-2.5 max-h-[380px] overflow-y-auto">
          {SHORTCUTS.map((s) => (
            <div key={s.label} className="flex items-center justify-between px-2.5 py-2 rounded-lg hover:bg-hover">
              <span className="text-[12.5px] text-dim">{s.label}</span>
              <span className="flex items-center gap-1">
                {s.keys.map((k) => <kbd key={k} className="kbd">{k}</kbd>)}
              </span>
            </div>
          ))}
        </div>
        <div className="px-4 py-2.5 border-t border-line text-[10.5px] text-faint">
          On macOS use ⌘ instead of Ctrl.
        </div>
      </div>
    </div>
  );
}

/** Global keyboard shortcuts — registered once in App. */
export function useGlobalShortcuts() {
  const {
    setPaletteOpen, setHelpOpen, toggleLeft, toggleRight, setSettingsOpen,
    setView, setActiveId, paletteOpen, helpOpen, settingsOpen,
  } = useStore();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const target = e.target as HTMLElement | null;
      const typing = !!target && (target.tagName === "INPUT"
        || target.tagName === "TEXTAREA" || target.isContentEditable);

      if (e.key === "Escape") {
        // close the topmost overlay, top layer first
        if (paletteOpen) { setPaletteOpen(false); e.preventDefault(); }
        else if (helpOpen) { setHelpOpen(false); e.preventDefault(); }
        else if (settingsOpen) { setSettingsOpen(false); e.preventDefault(); }
        return;
      }
      if (!mod) return;

      const k = e.key.toLowerCase();
      if (k === "k") { e.preventDefault(); setPaletteOpen(!paletteOpen); }
      else if (k === "/" || e.key === "?") { e.preventDefault(); setHelpOpen(!helpOpen); }
      else if (k === "b" && !e.shiftKey && !e.altKey) { e.preventDefault(); toggleLeft(); }
      else if (e.key === ".") { e.preventDefault(); toggleRight(); }
      else if (e.key === ",") { e.preventDefault(); setSettingsOpen(true); }
      else if (k === "n" && e.altKey && !typing) {
        e.preventDefault(); setActiveId(null); setView("chat");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, helpOpen, settingsOpen, setPaletteOpen, setHelpOpen,
    toggleLeft, toggleRight, setSettingsOpen, setView, setActiveId]);
}
