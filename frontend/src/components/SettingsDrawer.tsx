// Settings drawer — runtime configuration. FREE_ONLY / MAX_SPEND are
// displayed prominently; enforcement is backend-side (the UI toggle is
// only a preference the backend persists and enforces).
import { useEffect, useState } from "react";
import { useStore } from "../store";
import { formatEuro } from "../utils";
import { ShieldIcon, XIcon } from "../icons";

export function SettingsDrawer() {
  const { settings, settingsOpen, setSettingsOpen, saveSettings, costs } = useStore();
  const [draft, setDraft] = useState({
    free_only: true, max_spend: "0.00", default_model: "", num_ctx: "8192",
    custom_instructions: "",
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (settingsOpen && settings) {
      setDraft({
        free_only: settings.free_only,
        max_spend: String(settings.max_spend),
        default_model: settings.default_model,
        num_ctx: String(settings.num_ctx),
        custom_instructions: settings.custom_instructions ?? "",
      });
    }
  }, [settingsOpen, settings]);

  if (!settingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 anim-fade-in" onClick={() => setSettingsOpen(false)}>
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" />
      <div className="absolute right-0 top-0 h-full w-[400px] glass border-y-0 border-r-0 flex flex-col anim-fade-up"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-line">
          <div>
            <div className="text-[15px] font-bold">Settings</div>
            <div className="text-[10.5px] text-faint">Persisted server-side in SQLite</div>
          </div>
          <button className="icon-btn" onClick={() => setSettingsOpen(false)}><XIcon /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* cost protection */}
          <div className="glass-soft rounded-xl p-4 space-y-3">
            <div className="flex items-center gap-2">
              <ShieldIcon className="w-4 h-4 text-good" />
              <span className="text-[13px] font-bold">€0 Cost Protection</span>
              <span className="chip chip-good !text-[9px] ml-auto">
                lifetime spend {formatEuro(costs?.total ?? 0, costs?.currency)}
              </span>
            </div>
            <label className="flex items-center justify-between gap-3 cursor-pointer">
              <div>
                <div className="text-[12.5px] font-medium">Free-only mode</div>
                <div className="text-[10.5px] text-faint leading-snug">
                  Block every paid model before any provider network request.
                  Never falls back to a paid provider.
                </div>
              </div>
              <button
                className={`w-[42px] h-[23px] rounded-full transition-all shrink-0 border ${draft.free_only ? "bg-good/25 border-good/50" : "bg-hover border-line2"}`}
                onClick={() => setDraft((d) => ({ ...d, free_only: !d.free_only }))}
                title="Toggle free-only mode">
                <div className={`w-[17px] h-[17px] rounded-full bg-white translate-x-[3px] transition-transform ${draft.free_only ? "translate-x-[21px] !bg-good" : ""}`} />
              </button>
            </label>
            <div>
              <div className="text-[12.5px] font-medium mb-1">Max lifetime spend (EUR)</div>
              <input className="input font-mono" type="number" min="0" step="0.01"
                value={draft.max_spend}
                onChange={(e) => setDraft((d) => ({ ...d, max_spend: e.target.value }))} />
              <div className="text-[10px] text-faint mt-1">
                Default 0.00 — requests over budget are blocked, nothing is spent.
              </div>
            </div>
          </div>

          {/* model defaults */}
          <div className="space-y-3">
            <div className="micro-label">Model defaults</div>
            <div>
              <div className="text-[12.5px] font-medium mb-1">Default model</div>
              <input className="input font-mono" value={draft.default_model}
                onChange={(e) => setDraft((d) => ({ ...d, default_model: e.target.value }))} />
              <div className="text-[10px] text-faint mt-1">
                Used when a request doesn't name a model. Nothing is hardcoded.
              </div>
            </div>
            <div>
              <div className="text-[12.5px] font-medium mb-1">Context window (num_ctx)</div>
              <input className="input font-mono" type="number" min="512" step="512"
                value={draft.num_ctx}
                onChange={(e) => setDraft((d) => ({ ...d, num_ctx: e.target.value }))} />
              <div className="text-[10px] text-faint mt-1">
                8192 is comfortable for 8GB VRAM (RTX 4060 Ti). Lower = faster + less VRAM.
              </div>
            </div>
            <div>
              <div className="text-[12.5px] font-medium mb-1">Global custom instructions</div>
              <textarea className="input resize-none h-[90px]" value={draft.custom_instructions}
                placeholder="e.g. Answer concisely. Prefer code over prose."
                onChange={(e) => setDraft((d) => ({ ...d, custom_instructions: e.target.value }))} />
              <div className="text-[10px] text-faint mt-1">Appended to every chat's system prompt.</div>
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-line flex items-center gap-2">
          <button className="btn btn-accent flex-1 justify-center" disabled={saving}
            onClick={async () => {
              setSaving(true);
              try {
                await saveSettings({
                  free_only: draft.free_only,
                  max_spend: Number(draft.max_spend) || 0,
                  default_model: draft.default_model.trim() || undefined,
                  num_ctx: Number(draft.num_ctx) || 8192,
                  custom_instructions: draft.custom_instructions,
                });
                setSettingsOpen(false);
              } finally {
                setSaving(false);
              }
            }}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </div>
    </div>
  );
}
