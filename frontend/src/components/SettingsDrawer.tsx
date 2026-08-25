// Settings drawer — runtime configuration + provider management.
// FREE_ONLY / MAX_SPEND are displayed prominently; enforcement is
// backend-side. Provider API keys are write-only: saved through the
// encrypted vault, only masked hints are ever shown.
import { useEffect, useState } from "react";
import { useStore } from "../store";
import type { ProviderInfo } from "../types";
import { formatEuro } from "../utils";
import { CheckIcon, ShieldIcon, XIcon } from "../icons";

function ProviderRow({ provider }: { provider: ProviderInfo }) {
  const { saveProviderKey, removeProviderKey } = useStore();
  const [keyDraft, setKeyDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const ok = provider.status === "running";

  return (
    <div className="glass-soft rounded-xl p-3.5 space-y-2.5">
      <div className="flex items-center gap-2">
        <span className={`w-[7px] h-[7px] rounded-full shrink-0 ${ok ? "bg-good" : "bg-warn"}`} />
        <span className="text-[12.5px] font-semibold truncate">{provider.display_name}</span>
        <span className="chip !text-[9px] ml-auto shrink-0">{provider.is_local ? "LOCAL" : "CLOUD"}</span>
        <span className={`chip !text-[9px] shrink-0 ${ok ? "chip-good" : "chip-warn"}`}>
          {provider.status}
        </span>
      </div>
      {provider.detail && (
        <div className="text-[10.5px] text-faint leading-snug">{provider.detail}</div>
      )}
      {provider.requires_api_key && (
        <div className="space-y-2">
          {provider.key_configured ? (
            <div className="flex items-center gap-2">
              <span className="chip chip-good !text-[9.5px]">
                <CheckIcon className="w-3 h-3" /> key stored · {provider.key_masked}
              </span>
              <button
                className="btn btn-ghost !text-[10.5px] !py-1 !px-2 ml-auto"
                disabled={saving}
                onClick={async () => {
                  setSaving(true);
                  try { await removeProviderKey(provider.name); } finally { setSaving(false); }
                }}>
                Remove key
              </button>
            </div>
          ) : (
            <>
              <input
                className="input font-mono !text-[11.5px]"
                type="password"
                autoComplete="off"
                placeholder={`Paste your ${provider.display_name} API key…`}
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
              />
              <div className="flex items-center gap-2">
                <button
                  className="btn btn-accent !text-[10.5px] !py-1 !px-2.5"
                  disabled={!keyDraft.trim() || saving}
                  onClick={async () => {
                    setSaving(true);
                    try {
                      if (await saveProviderKey(provider.name, keyDraft.trim())) setKeyDraft("");
                    } finally { setSaving(false); }
                  }}>
                  {saving ? "Checking…" : "Save & verify"}
                </button>
                <span className="text-[9.5px] text-faint leading-snug">
                  Write-only: encrypted in the local vault, validated, never shown again.
                  Get a key at openrouter.ai/keys — :free models stay €0.00.
                </span>
              </div>
            </>
          )}
        </div>
      )}
      {!provider.requires_api_key && (
        <div className="text-[10px] text-faint">
          No key required · local inference is always €0.00
        </div>
      )}
    </div>
  );
}

export function SettingsDrawer() {
  const { settings, settingsOpen, setSettingsOpen, saveSettings, costs,
    providers, refreshProviders } = useStore();
  const [draft, setDraft] = useState({
    free_only: true, max_spend: "0.00", default_model: "", default_provider: "ollama",
    num_ctx: "8192", custom_instructions: "", eur_per_usd: "0.92",
    cap_filesystem_read: true, cap_filesystem_write: true, cap_command_execute: true,
    cap_network_fetch: true, cap_git_operate: false,
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (settingsOpen && settings) {
      setDraft({
        free_only: settings.free_only,
        max_spend: String(settings.max_spend),
        default_model: settings.default_model,
        default_provider: settings.default_provider ?? "ollama",
        num_ctx: String(settings.num_ctx),
        custom_instructions: settings.custom_instructions ?? "",
        eur_per_usd: String(settings.eur_per_usd ?? 0.92),
        cap_filesystem_read: settings.cap_filesystem_read ?? true,
        cap_filesystem_write: settings.cap_filesystem_write ?? true,
        cap_command_execute: settings.cap_command_execute ?? true,
        cap_network_fetch: settings.cap_network_fetch ?? true,
        cap_git_operate: settings.cap_git_operate ?? false,
      });
      void refreshProviders();
    }
  }, [settingsOpen, settings, refreshProviders]);

  const capToggle = (key: "cap_filesystem_read" | "cap_filesystem_write"
    | "cap_command_execute" | "cap_network_fetch" | "cap_git_operate",
    title: string, hint: string, dangerous = false) => (
    <label className="flex items-center justify-between gap-3 cursor-pointer">
      <div>
        <div className="text-[12.5px] font-medium">
          {title}{dangerous && <span className="chip chip-warn !text-[8.5px] !px-1.5 !py-[0px] ml-1.5">approval-gated</span>}
        </div>
        <div className="text-[10.5px] text-faint leading-snug">{hint}</div>
      </div>
      <button
        className={`w-[42px] h-[23px] rounded-full transition-all shrink-0 border ${draft[key] ? "bg-good/25 border-good/50" : "bg-hover border-line2"}`}
        onClick={() => setDraft((d) => ({ ...d, [key]: !d[key] }))}
        title={`Toggle ${title}`}>
        <div className={`w-[17px] h-[17px] rounded-full bg-white translate-x-[3px] transition-transform ${draft[key] ? "translate-x-[21px] !bg-good" : ""}`} />
      </button>
    </label>
  );

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
                  Block every paid model before any provider network request —
                  on every provider, local or cloud. Unknown cloud models are
                  blocked fail-closed.
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

          {/* providers */}
          <div className="space-y-2.5">
            <div className="micro-label">Providers</div>
            {providers.map((p) => <ProviderRow key={p.name} provider={p} />)}
          </div>

          {/* agent capabilities */}
          <div className="glass-soft rounded-xl p-4 space-y-3.5">
            <div className="flex items-center gap-2">
              <ShieldIcon className="w-4 h-4 text-accent" />
              <span className="text-[13px] font-bold">Agent permissions</span>
              <span className="chip !text-[9px] ml-auto">every write/exec asks you first</span>
            </div>
            <div className="text-[10.5px] text-faint leading-snug -mt-1">
              What the agent is even <em>allowed</em> to request. Off = the security
              gateway blocks the tool class outright (audited as a denial).
            </div>
            {capToggle("cap_filesystem_read", "Read files (sandbox)",
              "fs_list / fs_read inside the workspace only.")}
            {capToggle("cap_filesystem_write", "Write files (sandbox)",
              "fs_write / fs_edit with exact diff preview before anything lands.", true)}
            {capToggle("cap_command_execute", "Run commands (allow-list)",
              "python/pytest/git/etc. in the sandbox. rm/curl/etc. are hard-blocked.", true)}
            {capToggle("cap_network_fetch", "Network fetch",
              "web_search / web_fetch + Research Mode. SSRF-guarded, read-only.")}
            {capToggle("cap_git_operate", "Git operations",
              "git init/branch/commit/push in the workspace sandbox — audited, opt-in.")}
          </div>

          {/* model defaults */}
          <div className="space-y-3">
            <div className="micro-label">Model defaults</div>
            <div>
              <div className="text-[12.5px] font-medium mb-1">Default provider</div>
              <select className="input font-mono" value={draft.default_provider}
                onChange={(e) => setDraft((d) => ({ ...d, default_provider: e.target.value }))}>
                {providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}{p.requires_api_key && !p.key_configured ? " (key required)" : ""}
                  </option>
                ))}
              </select>
              <div className="text-[10px] text-faint mt-1">
                Used when a request names no provider. Never a silent paid fallback.
              </div>
            </div>
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
                Capped automatically to the selected model's real context length.
                8192 is comfortable for 8GB VRAM (RTX 4060 Ti).
              </div>
            </div>
            <div>
              <div className="text-[12.5px] font-medium mb-1">EUR per 1 USD</div>
              <input className="input font-mono" type="number" min="0.2" max="5" step="0.01"
                value={draft.eur_per_usd}
                onChange={(e) => setDraft((d) => ({ ...d, eur_per_usd: e.target.value }))} />
              <div className="text-[10px] text-faint mt-1">
                Cloud pricing is USD — the catalog converts to EUR for one honest budget.
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
                  default_provider: draft.default_provider || undefined,
                  num_ctx: Number(draft.num_ctx) || 8192,
                  custom_instructions: draft.custom_instructions,
                  eur_per_usd: Number(draft.eur_per_usd) || 0.92,
                  cap_filesystem_read: draft.cap_filesystem_read,
                  cap_filesystem_write: draft.cap_filesystem_write,
                  cap_command_execute: draft.cap_command_execute,
                  cap_network_fetch: draft.cap_network_fetch,
                  cap_git_operate: draft.cap_git_operate,
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
