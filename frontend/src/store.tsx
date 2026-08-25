// App store — single React context holding shared state.
// All data comes from the backend; UI never invents values.
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import type { ReactNode } from "react";
import { getJSON, sendJSON } from "./api";
import type {
  ConversationData, Costs, ModelCardData, ModelsResponse, RuntimeSettings,
  SystemStatus, TokenUsage, View,
} from "./types";

export type { View };

interface Toast { id: number; text: string; kind: "good" | "bad" | "info" }

interface Store {
  view: View;
  setView: (v: View) => void;

  settings: RuntimeSettings | null;
  refreshSettings: () => Promise<void>;
  saveSettings: (patch: Partial<RuntimeSettings>) => Promise<void>;

  system: SystemStatus | null;
  refreshSystem: () => Promise<void>;
  costs: Costs | null;
  refreshCosts: () => Promise<void>;
  tokens: TokenUsage | null;
  refreshTokens: () => Promise<void>;

  models: ModelCardData[];
  modelsRecent: ModelCardData[];
  modelsCategories: string[];
  modelsLoaded: boolean;
  refreshModels: () => Promise<void>;

  conversations: ConversationData[];
  convSearch: string;
  setConvSearch: (q: string) => void;
  showArchived: boolean;
  setShowArchived: (b: boolean) => void;
  refreshConversations: () => Promise<void>;

  activeId: string | null;
  setActiveId: (id: string | null) => void;
  activeConv: ConversationData | null;
  setActiveConv: (c: ConversationData | null) => void;

  currentModel: string | null;
  setCurrentModel: (m: string) => void;

  leftOpen: boolean;
  rightOpen: boolean;
  toggleLeft: () => void;
  toggleRight: () => void;
  settingsOpen: boolean;
  setSettingsOpen: (b: boolean) => void;

  toasts: Toast[];
  notify: (text: string, kind?: Toast["kind"]) => void;
  archiveConversation: (id: string, archived: boolean) => Promise<void>;
  removeConversation: (id: string) => Promise<void>;
  patchConversation: (id: string, patch: Record<string, unknown>) => Promise<void>;
}

const Ctx = createContext<Store | null>(null);
let toastSeq = 1;

export function StoreProvider({ children }: { children: ReactNode }) {
  const [view, setView] = useState<View>("chat");
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [costs, setCosts] = useState<Costs | null>(null);
  const [tokens, setTokens] = useState<TokenUsage | null>(null);
  const [models, setModels] = useState<ModelCardData[]>([]);
  const [modelsRecent, setModelsRecent] = useState<ModelCardData[]>([]);
  const [modelsCategories, setModelsCategories] = useState<string[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [conversations, setConversations] = useState<ConversationData[]>([]);
  const [convSearch, setConvSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [activeId, setActiveIdState] = useState<string | null>(null);
  const [activeConv, setActiveConv] = useState<ConversationData | null>(null);
  const [currentModel, setCurrentModelState] = useState<string | null>(
    () => localStorage.getItem("aicc.model"));
  const [leftOpen, setLeftOpen] = useState(() => localStorage.getItem("aicc.left") !== "0");
  const [rightOpen, setRightOpen] = useState(() => localStorage.getItem("aicc.right") !== "0");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const searchRef = useRef(convSearch);
  searchRef.current = convSearch;

  const notify = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = toastSeq++;
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const refreshSettings = useCallback(async () => {
    try {
      const s = await getJSON<RuntimeSettings>("/api/settings");
      setSettings(s);
      setCurrentModelState((m) => m ?? s.default_model);
    } catch { /* backend offline → stays null, UI shows Unknown */ }
  }, []);

  const refreshSystem = useCallback(async () => {
    try { setSystem(await getJSON<SystemStatus>("/api/system/status")); } catch { /* ignore */ }
  }, []);

  const refreshCosts = useCallback(async () => {
    try { setCosts(await getJSON<Costs>("/api/costs")); } catch { /* ignore */ }
  }, []);

  const refreshTokens = useCallback(async () => {
    try { setTokens(await getJSON<TokenUsage>("/api/usage/tokens")); } catch { /* ignore */ }
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      const data = await getJSON<ModelsResponse>("/api/models");
      setModels(data.models);
      setModelsRecent(data.recent);
      setModelsCategories(data.categories);
      setModelsLoaded(true);
    } catch { /* keep old */ }
  }, []);

  const refreshConversations = useCallback(async () => {
    const q = searchRef.current.trim();
    const params = new URLSearchParams();
    if (q) params.set("query", q);
    try {
      const url = `/api/conversations?${params.toString()}`;
      const data = await getJSON<{ conversations: ConversationData[] }>(
        showArchived ? `${url}&archived=true` : url);
      setConversations(data.conversations);
    } catch { /* keep old */ }
  }, [showArchived]);

  const patchConversation = useCallback(async (id: string, patch: Record<string, unknown>) => {
    try {
      const updated = await sendJSON<ConversationData>("PATCH", `/api/conversations/${id}`, patch);
      setConversations((list) => list.map((c) => (c.id === id ? { ...c, ...updated } : c)));
      setActiveConv((c) => (c && c.id === id ? { ...c, ...updated } : c));
    } catch (e) {
      notify("Update failed", "bad");
    }
  }, [notify]);

  const archiveConversation = useCallback(async (id: string, archived: boolean) => {
    await patchConversation(id, { archived });
    setActiveIdState((cur) => (archived && cur === id ? null : cur));
    await refreshConversations();
  }, [patchConversation, refreshConversations]);

  const removeConversation = useCallback(async (id: string) => {
    try {
      await sendJSON("DELETE", `/api/conversations/${id}`);
      setActiveIdState((cur) => (cur === id ? null : cur));
      setActiveConv((c) => (c && c.id === id ? null : c));
      await refreshConversations();
      notify("Conversation deleted");
    } catch {
      notify("Delete failed", "bad");
    }
  }, [notify, refreshConversations]);

  const setActiveId = useCallback((id: string | null) => {
    setActiveIdState(id);
  }, []);

  const setCurrentModel = useCallback((m: string) => {
    setCurrentModelState(m);
    localStorage.setItem("aicc.model", m);
  }, []);

  const toggleLeft = useCallback(() => setLeftOpen((v) => {
    localStorage.setItem("aicc.left", v ? "0" : "1");
    return !v;
  }), []);
  const toggleRight = useCallback(() => setRightOpen((v) => {
    localStorage.setItem("aicc.right", v ? "0" : "1");
    return !v;
  }), []);

  // boot + polling
  useEffect(() => { void refreshSettings(); }, [refreshSettings]);
  useEffect(() => { void refreshSystem(); void refreshCosts(); void refreshTokens(); void refreshModels(); },
    [refreshSystem, refreshCosts, refreshTokens, refreshModels]);
  useEffect(() => {
    const t = window.setInterval(() => { void refreshSystem(); }, 10000);
    return () => window.clearInterval(t);
  }, [refreshSystem]);
  useEffect(() => {
    const t = window.setInterval(() => { void refreshCosts(); void refreshTokens(); }, 20000);
    return () => window.clearInterval(t);
  }, [refreshCosts, refreshTokens]);
  useEffect(() => { void refreshConversations(); }, [refreshConversations, convSearch, showArchived]);

  const value = useMemo<Store>(() => ({
    view, setView, settings, refreshSettings,
    saveSettings: async (patch) => {
      const s = await sendJSON<RuntimeSettings>("PUT", "/api/settings", patch);
      setSettings(s);
      notify("Settings saved", "good");
    },
    system, refreshSystem, costs, refreshCosts, tokens, refreshTokens,
    models, modelsRecent, modelsCategories, modelsLoaded, refreshModels,
    conversations, convSearch, setConvSearch, showArchived, setShowArchived,
    refreshConversations, activeId, setActiveId, activeConv, setActiveConv,
    currentModel, setCurrentModel, leftOpen, rightOpen, toggleLeft, toggleRight,
    settingsOpen, setSettingsOpen, toasts, notify,
    archiveConversation, removeConversation, patchConversation,
  }), [view, settings, refreshSettings, system, refreshSystem, costs, refreshCosts,
    tokens, refreshTokens, models, modelsRecent, modelsCategories, modelsLoaded,
    refreshModels, conversations, convSearch, showArchived, refreshConversations,
    activeId, setActiveId, activeConv, currentModel, setCurrentModel, leftOpen,
    rightOpen, toggleLeft, toggleRight, settingsOpen, toasts, notify,
    archiveConversation, removeConversation, patchConversation]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const store = useContext(Ctx);
  if (!store) throw new Error("useStore outside provider");
  return store;
}
