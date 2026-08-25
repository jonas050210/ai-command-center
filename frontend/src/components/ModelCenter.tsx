// Model Center (Phase 3) — real model catalog from Ollama: discovery,
// search, category filters, sorting, favorites, recently used, select,
// speed tests, safe pull/delete. Unknown values are shown as "Unknown".
import { useCallback, useEffect, useState } from "react";
import { getJSON, sendJSON, streamSSE } from "../api";
import { useStore } from "../store";
import type { ModelCardData, ModelTestResult } from "../types";
import { cx, formatBytes, formatNumber, timeAgo } from "../utils";
import {
  AlertIcon, CheckIcon, DatabaseIcon, DownloadIcon, GaugeIcon, RefreshIcon,
  SearchIcon, StarIcon, TrashIcon, ZapIcon,
} from "../icons";

const SORTS: Array<[string, string]> = [
  ["name", "Name"], ["size", "Size"], ["recent", "Recently used"],
  ["speed", "Speed"], ["favorite", "Favorites"],
];

function Badge({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return <span className={cx("chip !text-[9px]", tone)}>{children}</span>;
}

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="bg-[rgba(7,10,16,0.6)] rounded-lg px-2.5 py-1.5 border border-line" title={title}>
      <div className="micro-label !text-[8px]">{label}</div>
      <div className="text-[11.5px] font-semibold text-ink mt-[1px] truncate">{value}</div>
    </div>
  );
}

function ModelCard({ model, onChanged }: { model: ModelCardData; onChanged: () => void }) {
  const { currentModel, setCurrentModel, setView, notify } = useStore();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ModelTestResult | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const selected = currentModel === model.name;

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const r = await sendJSON<ModelTestResult>("POST", "/api/models/test",
        { provider: model.provider, name: model.name });
      setResult(r);
      onChanged();
    } catch (e) {
      notify(`Test failed: ${e instanceof Error ? e.message : "Unknown error"}`, "bad");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className={cx("glass hover-glow rounded-2xl p-4 flex flex-col gap-3 anim-fade-up",
      selected && "border-[rgba(69,227,255,0.4)]")}>
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <div className="text-[14px] font-bold text-ink truncate" title={model.name}>
              {model.display_name}
            </div>
            <button
              className={cx("icon-btn !w-6 !h-6 shrink-0", model.favorite && "!text-warn")}
              title={model.favorite ? "Remove favorite" : "Favorite"}
              onClick={async () => {
                await sendJSON("POST", `/api/models/${model.provider}/${model.name}/favorite`,
                  { favorite: !model.favorite }).catch(() => undefined);
                onChanged();
              }}>
              <StarIcon className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="text-[10.5px] text-faint mt-[2px]">{model.provider} · {model.name}</div>
        </div>
        <span className={cx("chip", model.available && model.status === "available" ? "chip-good" : "chip-bad")}>
          {model.available ? "Available" : "Unavailable"}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Badge tone={model.is_free ? "chip-good" : "chip-bad"}>{model.is_free ? "FREE" : "PAID"}</Badge>
        <Badge tone="chip-accent">{model.location.toUpperCase()}</Badge>
        {model.categories.filter((c) => !["general", "local", "free"].includes(c)).map((c) => (
          <Badge key={c} tone="chip-violet">{c.toUpperCase()}</Badge>
        ))}
        {model.capabilities.includes("tools") && <Badge>TOOLS</Badge>}
      </div>

      <div className="grid grid-cols-3 gap-1.5">
        <Stat label="Context" value={model.context_length ? formatNumber(model.context_length) : "Unknown"} />
        <Stat label="Size" value={formatBytes(model.size_bytes)} />
        <Stat label="Params" value={model.parameter_size ?? "Unknown"} />
        <Stat label="Quant" value={model.quantization ?? "Unknown"} />
        <Stat label="Speed"
          value={model.measured_tps ? `${model.measured_tps.toFixed(1)} tok/s` : "Unknown"}
          title={model.measured_tps ? "Measured throughput" : "Not measured — run Test"} />
        <Stat label="Tokens used" value={formatNumber(model.total_tokens)}
          title={`in ${formatNumber(model.total_input_tokens)} · out ${formatNumber(model.total_output_tokens)}`} />
      </div>

      <div className="flex items-center justify-between text-[10.5px] text-faint">
        <span>Cost: <span className="text-good font-semibold">€0.00</span></span>
        <span>{model.usage_count > 0 ? `used ${model.usage_count}× · ${timeAgo(model.last_used_at)}` : "never used"}</span>
      </div>

      {result && (
        <div className={cx("rounded-lg border px-3 py-2 text-[11.5px] anim-fade-up",
          "border-[rgba(52,211,153,0.35)] bg-[rgba(52,211,153,0.06)]")}>
          <span className="text-good font-semibold">Test OK</span>
          <span className="text-dim"> · {result.tokens_per_second
            ? `${result.tokens_per_second.toFixed(1)} tok/s` : "speed Unknown"}
            {" · "}{result.latency_ms.toFixed(0)} ms
            {" · "}{result.output_tokens ?? "?"} out tok
            {" · "}<span className={result.token_method === "exact" ? "text-good" : "text-warn"}>
              {result.token_method}</span>
            {" · "}<span className="text-good">€0.00</span></span>
        </div>
      )}

      <div className="flex items-center gap-1.5 pt-1 border-t border-line mt-auto">
        <button
          className={cx("btn !text-[11px] flex-1 justify-center", selected ? "btn-accent" : "")}
          disabled={!model.available}
          onClick={() => { setCurrentModel(model.name); setView("chat"); }}>
          {selected ? <><CheckIcon className="w-3.5 h-3.5" /> Selected</> : "Select"}
        </button>
        <button className="btn !text-[11px]" disabled={!model.available || testing}
          onClick={() => void runTest()} title="Run one real inference and measure speed">
          <GaugeIcon className="w-3.5 h-3.5" /> {testing ? "Testing…" : "Test"}
        </button>
        {confirmDelete ? (
          <>
            <button className="btn btn-danger !text-[10.5px] !px-2" title="Confirm delete"
              onClick={async () => {
                setConfirmDelete(false);
                const r = await sendJSON<{ deleted: boolean }>(
                  "DELETE", `/api/models/${model.provider}/${model.name}`).catch(() => null);
                if (r?.deleted) { notify(`Deleted ${model.name}`); onChanged(); }
                else notify("Delete failed", "bad");
              }}>
              Sure?
            </button>
            <button className="btn btn-ghost !text-[10.5px] !px-2" onClick={() => setConfirmDelete(false)}>No</button>
          </>
        ) : (
          <button className="btn btn-ghost !px-2" title="Delete from Ollama"
            onClick={() => setConfirmDelete(true)}>
            <TrashIcon className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

export function ModelCenter() {
  const { system, refreshSystem, notify } = useStore();
  const [data, setData] = useState<{ models: ModelCardData[]; recent: ModelCardData[]; categories: string[] }>({
    models: [], recent: [], categories: [],
  });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [sort, setSort] = useState("name");
  const [favOnly, setFavOnly] = useState(false);
  const [pullName, setPullName] = useState("");
  const [pulling, setPulling] = useState<{ status: string; percent: number | null } | null>(null);

  const ollama = system?.ollama;
  const ollamaDown = ollama ? ollama.status !== "running" : false;

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (category) params.set("category", category);
    if (favOnly) params.set("favorites", "true");
    params.set("sort", sort);
    try {
      const d = await getJSON<{ models: ModelCardData[]; recent: ModelCardData[]; categories: string[] }>(
        `/api/models?${params.toString()}`);
      setData(d);
    } catch { /* backend offline */ }
    setLoading(false);
  }, [q, category, sort, favOnly]);

  useEffect(() => {
    const t = window.setTimeout(() => void load(), q ? 180 : 0);
    return () => window.clearTimeout(t);
  }, [load, q]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      const res = await sendJSON<{ results: Record<string, { synced?: number; error?: string }> }>(
        "POST", "/api/models/refresh");
      const o = res.results?.ollama;
      if (o?.error) notify(`Ollama: ${o.error}`, "bad");
      else notify(`Discovered ${o?.synced ?? 0} models from Ollama`, "good");
      void refreshSystem();
    } catch (e) {
      notify(`Refresh failed: ${e instanceof Error ? e.message : "error"}`, "bad");
    } finally {
      setRefreshing(false);
      void load();
    }
  };

  const pull = async () => {
    const name = pullName.trim();
    if (!name || pulling) return;
    setPulling({ status: "starting…", percent: null });
    try {
      await streamSSE<{ status: string; percent: number | null; message?: string }>(
        "/api/models/pull", { name }, (ev) => {
          setPulling({ status: ev.status, percent: ev.percent });
        });
      setPullName("");
      notify(`Model ready: ${name}`, "good");
      await load();
      void refreshSystem();
    } catch (e) {
      notify(`Pull failed: ${e instanceof Error ? e.message : "error"}`, "bad");
    } finally {
      window.setTimeout(() => setPulling(null), 1200);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto min-h-0 px-6 py-5">
      {/* header */}
      <div className="flex flex-wrap items-center gap-2.5 mb-4">
        <div>
          <h2 className="text-[17px] font-bold tracking-wide">Model Center</h2>
          <div className="text-[11px] text-faint">
            Real catalog from Ollama · nothing simulated · "Unknown" where data can't be determined
          </div>
        </div>
        <div className="flex-1" />
        <div className="relative">
          <SearchIcon className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
          <input className="input !pl-8 !w-[210px]" placeholder="Search models…"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="input !w-[140px] cursor-pointer" value={sort}
          onChange={(e) => setSort(e.target.value)} title="Sort models">
          {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <button className={cx("btn !py-2", favOnly && "btn-accent")} title="Show favorites only"
          onClick={() => setFavOnly((f) => !f)}>
          <StarIcon className="w-3.5 h-3.5" />
        </button>
        <button className="btn btn-accent !py-2" disabled={refreshing}
          onClick={() => void refresh()} title="Discover models from Ollama (live)">
          <RefreshIcon className={cx("w-3.5 h-3.5", refreshing && "animate-spin")} />
          {refreshing ? "Discovering…" : "Refresh"}
        </button>
      </div>

      {ollamaDown && (
        <div className="mb-4 flex items-start gap-2.5 rounded-xl border px-4 py-3
          border-[rgba(251,191,36,0.35)] bg-[rgba(251,191,36,0.06)]">
          <AlertIcon className="w-4 h-4 text-warn mt-[1px] shrink-0" />
          <div className="text-[12.5px]">
            <div className="font-semibold text-warn">Ollama unavailable</div>
            <div className="text-dim">{ollama?.detail ?? "Start Ollama, then press Refresh."}
              {" "}Showing the last known catalog — entries may be stale.</div>
          </div>
        </div>
      )}

      {/* pull */}
      <div className="glass rounded-xl p-3 mb-5 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <DatabaseIcon className="w-4 h-4 text-accent shrink-0" />
          <input className="input !w-[280px]" placeholder="Pull a model, e.g. qwen3:0.6b"
            value={pullName} disabled={!!pulling}
            onChange={(e) => setPullName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void pull(); }} />
          <button className="btn btn-accent !py-2" disabled={!pullName.trim() || !!pulling || ollamaDown}
            onClick={() => void pull()}>
            <DownloadIcon className="w-3.5 h-3.5" /> {pulling ? "Pulling…" : "Pull"}
          </button>
          {pulling && (
            <div className="flex-1 flex items-center gap-2 min-w-[180px]">
              <div className="meter flex-1"><div style={{ width: `${pulling.percent ?? 8}%` }} /></div>
              <span className="text-[10px] text-dim whitespace-nowrap">
                {pulling.status}{pulling.percent != null ? ` · ${pulling.percent}%` : ""}
              </span>
            </div>
          )}
          {!pulling && (
            <span className="text-[10px] text-faint">
              Downloads to your local Ollama. Any name from the Ollama library works.
            </span>
          )}
        </div>
      </div>

      {/* categories */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        <button className={cx("chip !py-1.5 cursor-pointer", !category && "chip-accent")}
          onClick={() => setCategory(null)}>All</button>
        {data.categories.map((c) => (
          <button key={c} className={cx("chip !py-1.5 cursor-pointer capitalize", category === c && "chip-accent")}
            onClick={() => setCategory(category === c ? null : c)}>
            {c}
          </button>
        ))}
      </div>

      {/* recently used */}
      {data.recent.length > 0 && !category && !q && (
        <div className="mb-5">
          <div className="micro-label mb-2">Recently used</div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {data.recent.map((m) => (
              <div key={`recent-${m.name}`}
                className="glass-soft rounded-xl px-3.5 py-2.5 flex items-center gap-2.5 shrink-0">
                <ZapIcon className="w-3.5 h-3.5 text-accent" />
                <div>
                  <div className="text-[12px] font-semibold">{m.display_name}</div>
                  <div className="text-[9.5px] text-faint">{timeAgo(m.last_used_at)} · {formatNumber(m.total_tokens)} tok</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* grid */}
      {loading ? (
        <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-3.5">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton h-[260px]" />)}
        </div>
      ) : data.models.length === 0 ? (
        <div className="glass rounded-2xl py-16 text-center">
          <div className="text-[13.5px] text-dim font-medium">No models in the catalog</div>
          <div className="text-[11.5px] text-faint mt-1.5 max-w-[420px] mx-auto">
            {ollamaDown
              ? "Ollama is not reachable. Start it and press Refresh to discover your installed models."
              : "Press Refresh to discover installed models, or Pull one above (e.g. your configured default model)."}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-3.5 pb-6">
          {data.models.map((m) => (
            <ModelCard key={`${m.provider}/${m.name}`} model={m} onChanged={() => void load()} />
          ))}
        </div>
      )}
    </div>
  );
}
