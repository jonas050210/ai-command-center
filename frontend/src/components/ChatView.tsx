// ChatView — the premium chat workspace. Real SSE streaming against the
// backend (which talks to Ollama). No simulated responses anywhere.
import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON, streamSSE, ApiError } from "../api";
import { useStore } from "../store";
import type { ChatMessageData, ConversationData, SSEvent } from "../types";
import { AlertIcon, LogoIcon, ShieldIcon } from "../icons";
import { Composer } from "./Composer";
import { MessageItem, StreamingBubble } from "./MessageItem";

interface StreamState {
  requestId: string | null;
  assistantId: string | null;
  conversationId: string | null;
  content: string;
  model: string;
}

export function ChatView() {
  const {
    activeId, setActiveId, activeConv, setActiveConv, refreshConversations,
    refreshCosts, refreshTokens, refreshModels, system, settings, notify,
  } = useStore();

  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [loading, setLoading] = useState(false);
  const [stream, setStream] = useState<StreamState | null>(null);
  const [bannerError, setBannerError] = useState<{ code: string; message: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const streamingRef = useRef(false);

  // ── load conversation ────────────────────────────────────────────
  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      setActiveConv(null);
      setBannerError(null);
      return;
    }
    if (streamingRef.current) return; // don't clobber an in-flight stream
    let cancelled = false;
    setLoading(true);
    getJSON<ConversationData>(`/api/conversations/${activeId}`)
      .then((conv) => {
        if (cancelled) return;
        setActiveConv(conv);
        setMessages(conv.messages ?? []);
        setBannerError(null);
      })
      .catch((e: ApiError) => {
        if (!cancelled) {
          setBannerError({ code: e.code, message: e.message });
          setMessages([]);
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [activeId, setActiveConv]);

  // ── auto-scroll (stick to bottom when near it) ───────────────────
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 240;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [messages, stream?.content]);

  // ── shared stream consumer ───────────────────────────────────────
  const consume = useCallback(async (url: string, body: unknown, optimisticUser?: string) => {
    setBannerError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    streamingRef.current = true;
    const tempUserId = optimisticUser ? `temp-${Date.now()}` : null;
    if (optimisticUser) {
      setMessages((m) => [...m, {
        id: tempUserId!, role: "user", content: optimisticUser, model: null,
        provider: null, input_tokens: null, output_tokens: null,
        token_method: "estimated", status: "complete", error: null,
        created_at: new Date().toISOString(),
      }]);
    }
    let state: StreamState = {
      requestId: null, assistantId: null, conversationId: activeId, content: "",
      model: (body as { model?: string }).model ?? "",
    };
    setStream(state);
    let usage: { input: number; output: number; method: string; tps: number | null } | null = null;
    let finalStatus = "complete";
    let streamError: { code: string; message: string } | null = null;

    try {
      await streamSSE(url, body, (ev: SSEvent) => {
        if (ev.type === "meta") {
          state = { ...state, requestId: ev.request_id, assistantId: ev.assistant_message_id,
            conversationId: ev.conversation_id, model: ev.model };
          setStream({ ...state });
          if (!activeId) setActiveId(ev.conversation_id);
          void refreshConversations();
        } else if (ev.type === "delta") {
          state = { ...state, content: state.content + ev.content };
          setStream({ ...state });
        } else if (ev.type === "usage") {
          usage = { input: ev.input_tokens, output: ev.output_tokens,
            method: ev.method, tps: ev.tokens_per_second };
        } else if (ev.type === "done") {
          finalStatus = ev.status;
        } else if (ev.type === "error") {
          streamError = { code: ev.code, message: ev.message };
        }
      }, controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) {
        streamError = streamError ?? { code: "NETWORK", message: "Lost connection to the backend." };
      }
    }

    // ── finalize ──
    streamingRef.current = false;
    abortRef.current = null;
    const finished = { ...state };
    setStream(null);

    if (finished.conversationId) {
      // reload the conversation from the server — single source of truth
      try {
        const conv = await getJSON<ConversationData>(
          `/api/conversations/${finished.conversationId}`);
        setActiveConv(conv);
        setMessages(conv.messages ?? []);
      } catch {
        if (finished.content || streamError) {
          setMessages((m) => [...m.filter((x) => x.id !== tempUserId), {
            id: finished.assistantId ?? `local-${Date.now()}`,
            role: "assistant", content: finished.content,
            model: finished.model, provider: "ollama",
            input_tokens: usage?.input ?? null, output_tokens: usage?.output ?? null,
            token_method: (usage?.method as "exact" | "estimated") ?? "estimated",
            status: streamError ? "error" : finalStatus,
            error: streamError?.message ?? null,
            created_at: new Date().toISOString(),
          }]);
        }
      }
    }
    if (streamError) {
      setBannerError(streamError);
    }
    void refreshConversations();
    void refreshCosts();
    void refreshTokens();
    void refreshModels();
    // auto-title may land shortly after completion — refresh list once more
    window.setTimeout(() => void refreshConversations(), 2600);
  }, [activeId, setActiveId, setActiveConv, refreshConversations, refreshCosts,
    refreshTokens, refreshModels]);

  const send = useCallback((content: string, model: string | null) => {
    const text = content.trim();
    if (!text) return;
    void consume("/api/chat/completions", {
      conversation_id: activeId, content: text, model: model ?? undefined,
    }, text);
  }, [activeId, consume]);

  const regenerate = useCallback((messageId: string) => {
    void consume("/api/chat/regenerate", { message_id: messageId });
  }, [consume]);

  const editMessage = useCallback(async (messageId: string, content: string) => {
    try {
      const res = await sendJSON<{ conversation: { id: string } }>(
        "PATCH", `/api/messages/${messageId}`, { content });
      if (res.conversation?.id) {
        const conv = await getJSON<ConversationData>(
          `/api/conversations/${res.conversation.id}`);
        setActiveConv(conv);
        setMessages(conv.messages ?? []);
      }
      void refreshConversations();
      void refreshCosts();
      void refreshTokens();
      notify("Message updated.", "good");
    } catch (e) {
      setBannerError({ code: "EDIT_FAILED", message: e instanceof Error ? e.message : "Edit failed" });
      notify(e instanceof Error ? e.message : "Edit failed", "bad");
    }
  }, [setActiveConv, refreshConversations, refreshCosts, refreshTokens, notify]);

  const stop = useCallback(() => {
    const reqId = stream?.requestId;
    abortRef.current?.abort();
    if (reqId) {
      sendJSON("POST", "/api/chat/stop", { request_id: reqId }).catch(() => undefined);
    }
  }, [stream?.requestId]);

  const ollama = system?.ollama;
  const ollamaDown = ollama && ollama.status !== "running";
  const isNew = !activeId;
  const lastAssistantId = [...messages].reverse().find((m) => m.role === "assistant")?.id;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* error banners (clear error states) */}
      {bannerError && (
        <div className="mx-6 mt-4 flex items-start gap-2.5 rounded-xl border px-4 py-3 anim-fade-up
          border-[rgba(248,113,113,0.4)] bg-[rgba(248,113,113,0.07)]">
          <AlertIcon className="w-4 h-4 text-bad mt-[1px] shrink-0" />
          <div className="text-[12.5px]">
            <div className="font-semibold text-bad">{bannerError.code}</div>
            <div className="text-bad/80 whitespace-pre-wrap">{bannerError.message}</div>
          </div>
        </div>
      )}
      {ollamaDown && !bannerError && (
        <div className="mx-6 mt-4 flex items-start gap-2.5 rounded-xl border px-4 py-3
          border-[rgba(251,191,36,0.35)] bg-[rgba(251,191,36,0.06)]">
          <AlertIcon className="w-4 h-4 text-warn mt-[1px] shrink-0" />
          <div className="text-[12.5px]">
            <div className="font-semibold text-warn">Ollama unavailable</div>
            <div className="text-dim">{ollama?.detail ?? "Start Ollama and retry."}</div>
          </div>
        </div>
      )}

      {/* messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0 py-4">
        {isNew && messages.length === 0 && !stream && (
          <div className="h-full flex flex-col items-center justify-center px-6 text-center anim-fade-up">
            <div className="text-accent mb-5 opacity-90"><LogoIcon className="scale-[2.2]" /></div>
            <h1 className="text-[19px] font-bold tracking-wide">AI Command Center</h1>
            <p className="text-[12.5px] text-dim mt-1.5 max-w-[430px]">
              Premium local-first AI chat. Real streaming from Ollama — nothing is simulated.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-2 mt-5">
              <span className="chip chip-good"><ShieldIcon className="w-3 h-3" /> FREE-ONLY · €0.00</span>
              <span className="chip">
                Default: <span className="text-ink font-semibold ml-1">{settings?.default_model ?? "…"}</span>
              </span>
              <span className="chip">
                Ollama: <span className={ollama?.status === "running" ? "text-good ml-1" : "text-bad ml-1"}>
                  {ollama?.status ?? "unknown"}</span>
              </span>
            </div>
            <p className="text-[10.5px] text-faint mt-6">Type below or pick a suggestion to start a conversation.</p>
          </div>
        )}

        {!isNew && loading && (
          <div className="px-6 space-y-3 pt-2">
            <div className="skeleton h-9 w-[52%] ml-auto" />
            <div className="skeleton h-24 w-[86%]" />
            <div className="skeleton h-9 w-[46%] ml-auto" />
          </div>
        )}

        {messages.map((msg) => (
          <MessageItem key={msg.id} msg={msg}
            isLast={msg.id === lastAssistantId && !stream}
            onRegenerate={regenerate}
            onEdit={editMessage} />
        ))}

        {stream && <StreamingBubble content={stream.content} model={stream.model || "assistant"} />}
        <div className="h-2" />
      </div>

      <Composer streaming={!!stream} onSend={send} onStop={stop}
        modelOverride={activeConv?.model ?? undefined} />
    </div>
  );
}
