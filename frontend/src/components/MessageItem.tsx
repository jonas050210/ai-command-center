// Message bubble — markdown for assistant, token chips (exact/estimated),
// actions: copy, regenerate, retry. Clear error states.
import { useState } from "react";
import type { ChatMessageData } from "../types";
import { cx, copyText, formatNumber } from "../utils";
import { Markdown } from "./Markdown";
import { AlertIcon, BotIcon, CheckIcon, CopyIcon, EditIcon, RefreshIcon } from "../icons";

export function TokenChip({ msg }: { msg: ChatMessageData }) {
  if (msg.role !== "assistant" || msg.input_tokens == null) return null;
  const total = (msg.input_tokens ?? 0) + (msg.output_tokens ?? 0);
  return (
    <span className="chip !text-[9.5px]" title={`in ${msg.input_tokens} · out ${msg.output_tokens}`}>
      {formatNumber(total)} tok · {msg.token_method === "exact" ? (
        <span className="text-good">exact</span>
      ) : (
        <span className="text-warn">estimated</span>
      )}
    </span>
  );
}

export function MessageItem({ msg, isLast, onRegenerate, onEdit }: {
  msg: ChatMessageData;
  isLast: boolean;
  onRegenerate: (messageId: string) => void;
  onEdit?: (messageId: string, content: string) => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);
  const [saving, setSaving] = useState(false);

  const commitEdit = async () => {
    if (!onEdit || saving) return;
    const text = draft.trim();
    if (!text) return;
    setSaving(true);
    try {
      await onEdit(msg.id, text);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  if (msg.role === "user") {
    return (
      <div className="anim-fade-up flex justify-end px-6 py-1.5">
        <div className="max-w-[78%] min-w-[120px]">
          {editing ? (
            <div className="glass-soft rounded-2xl p-2.5">
              <textarea className="input !text-[13px] resize-none" rows={3} autoFocus
                value={draft} onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void commitEdit();
                  if (e.key === "Escape") { setEditing(false); setDraft(msg.content); }
                }} />
              <div className="flex items-center gap-1.5 mt-2">
                <button className="btn !text-[11px] !py-1 !px-2.5 btn-accent" disabled={saving || !draft.trim()}
                  onClick={() => void commitEdit()}>{saving ? "Saving…" : "Save"}</button>
                <button className="btn btn-ghost !text-[11px] !py-1 !px-2.5"
                  onClick={() => { setEditing(false); setDraft(msg.content); }}>Cancel</button>
                <span className="text-[9px] text-faint ml-auto">editing rewrites this turn (messages after it are removed)</span>
              </div>
            </div>
          ) : (
            <div className="rounded-2xl rounded-br-md px-4 py-2.5 text-[13.5px] leading-relaxed
              bg-[rgba(69,227,255,0.1)] border border-[rgba(69,227,255,0.2)] whitespace-pre-wrap break-words">
              {msg.content}
            </div>
          )}
          <div className="flex justify-end mt-1 opacity-0 hover:opacity-100 transition-opacity">
            {!editing && (
              <button className="icon-btn !w-6 !h-6" title="Edit message"
                onClick={() => { setDraft(msg.content); setEditing(true); }}>
                <EditIcon className="w-3 h-3" />
              </button>
            )}
            {!editing && (
              <button className="icon-btn !w-6 !h-6" title="Copy"
                onClick={async () => {
                  if (await copyText(msg.content)) { setCopied(true); window.setTimeout(() => setCopied(false), 1200); }
                }}>
                {copied ? <CheckIcon className="w-3 h-3 text-good" /> : <CopyIcon className="w-3 h-3" />}
              </button>
            )}
            {editing && (
              <span className="text-[9.5px] text-faint mr-1">Esc cancels</span>
            )}
          </div>
        </div>
      </div>
    );
  }

  const failed = msg.status === "error";
  const stopped = msg.status === "stopped";

  return (
    <div className="anim-fade-up px-6 py-2">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-5 h-5 rounded-md bg-accentdim border border-[rgba(69,227,255,0.25)]
          flex items-center justify-center text-accent">
          <BotIcon className="w-3 h-3" />
        </span>
        <span className="text-[11px] font-semibold text-dim">{msg.model ?? "assistant"}</span>
        <TokenChip msg={msg} />
        {stopped && <span className="chip chip-warn !text-[9.5px]">stopped</span>}
        {failed && <span className="chip chip-bad !text-[9.5px]">error</span>}
        <div className="flex-1" />
        <div className="flex items-center gap-0.5 opacity-60 hover:opacity-100 transition-opacity">
          {onEdit && (
            <button className="icon-btn !w-6 !h-6" title="Edit response"
              onClick={() => { setDraft(msg.content); setEditing(true); }}>
              <EditIcon className="w-3 h-3" />
            </button>
          )}
          <button className="icon-btn !w-6 !h-6" title="Copy response"
            onClick={async () => {
              if (await copyText(msg.content)) { setCopied(true); window.setTimeout(() => setCopied(false), 1200); }
            }}>
            {copied ? <CheckIcon className="w-3 h-3 text-good" /> : <CopyIcon className="w-3 h-3" />}
          </button>
          {isLast && (
            <button className="icon-btn !w-6 !h-6" title={failed ? "Retry" : "Regenerate"}
              onClick={() => onRegenerate(msg.id)}>
              <RefreshIcon className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>

      {failed && (
        <div className="mb-2 flex items-start gap-2 rounded-lg border border-[rgba(248,113,113,0.35)]
          bg-[rgba(248,113,113,0.06)] px-3 py-2">
          <AlertIcon className="w-3.5 h-3.5 text-bad mt-[2px] shrink-0" />
          <div className="text-[12px] text-bad/90">
            <div className="font-semibold">Generation failed</div>
            <div className="text-bad/75">{msg.error ?? "Unknown error"}</div>
            <button className="btn btn-danger !text-[10.5px] !py-1 !px-2 mt-1.5"
              onClick={() => onRegenerate(msg.id)}>
              <RefreshIcon className="w-3 h-3" /> Retry
            </button>
          </div>
        </div>
      )}

      <div className="pl-7">
        {editing ? (
          <div className="glass-soft rounded-xl p-2.5">
            <textarea className="input !text-[12.5px] resize-none" rows={6} autoFocus
              value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void commitEdit();
                if (e.key === "Escape") { setEditing(false); setDraft(msg.content); }
              }} />
            <div className="flex items-center gap-1.5 mt-2">
              <button className="btn !text-[11px] !py-1 !px-2.5 btn-accent" disabled={saving || !draft.trim()}
                onClick={() => void commitEdit()}>{saving ? "Saving…" : "Save"}</button>
              <button className="btn btn-ghost !text-[11px] !py-1 !px-2.5"
                onClick={() => { setEditing(false); setDraft(msg.content); }}>Cancel</button>
              <span className="text-[9px] text-faint ml-auto">saves the corrected response in place</span>
            </div>
          </div>
        ) : msg.content ? <Markdown content={msg.content} /> :
          !failed && <span className="text-faint text-[12px] italic">Empty response</span>}
      </div>
    </div>
  );
}

export function StreamingBubble({ content, model }: { content: string; model: string }) {
  return (
    <div className="px-6 py-2 anim-fade-in">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-5 h-5 rounded-md bg-accentdim border border-[rgba(69,227,255,0.25)]
          flex items-center justify-center text-accent pulse-soft">
          <BotIcon className="w-3 h-3" />
        </span>
        <span className="text-[11px] font-semibold text-dim">{model}</span>
        <span className={cx("chip chip-accent !text-[9.5px]")}>streaming</span>
      </div>
      <div className="pl-7">
        {content ? <Markdown content={content} /> : null}
        <span className="stream-caret" />
      </div>
    </div>
  );
}
