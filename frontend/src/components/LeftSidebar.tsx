// Left sidebar — navigation, chat history (search/rename/pin/star/
// archive/delete), projects boundary.
import { useMemo, useState } from "react";
import { useStore } from "../store";
import type { ConversationData, View } from "../types";
import { cx, dayBucket, formatNumber, timeAgo } from "../utils";
import {
  ArchiveIcon, BotIcon, ChatIcon, CheckIcon, CodeIcon, EditIcon, FolderIcon, GaugeIcon,
  GitIcon, ModelsIcon, PinIcon, PlusIcon, ResearchIcon, SearchIcon, StarIcon,
  TrashIcon, UsersIcon, XIcon,
} from "../icons";

function NavItem({ view, icon, label }: {
  view: View; icon: React.ReactNode; label: string;
}) {
  const { view: active, setView } = useStore();
  return (
    <button
      onClick={() => setView(view)}
      data-active={active === view ? "1" : "0"}
      className={cx(
        "nav-item w-full flex items-center gap-2.5 px-2.5 py-[7px] rounded-lg text-[12.5px] transition-all",
        active === view ? "bg-accentdim text-accent border border-[rgba(69,227,255,0.22)]"
          : "text-dim hover:text-ink hover:bg-hover border border-transparent")}
    >
      <span className="shrink-0 opacity-90">{icon}</span>
      <span className="flex-1 text-left font-medium">{label}</span>
    </button>
  );
}

function ConversationRow({ conv }: { conv: ConversationData }) {
  const { activeId, setActiveId, setView, patchConversation, archiveConversation,
    removeConversation, refreshConversations } = useStore();
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(conv.title);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const active = activeId === conv.id;

  const commitRename = async () => {
    setRenaming(false);
    const title = draft.trim();
    if (title && title !== conv.title) {
      await patchConversation(conv.id, { title });
      await refreshConversations();
    } else {
      setDraft(conv.title);
    }
  };

  return (
    <div
      className={cx(
        "group relative rounded-lg px-2.5 py-2 cursor-pointer transition-all border",
        active ? "bg-accentdim border-[rgba(69,227,255,0.22)]"
          : "border-transparent hover:bg-hover")}
      onClick={() => { if (!renaming) { setActiveId(conv.id); setView("chat"); } }}
    >
      {renaming ? (
        <div className="flex items-center gap-1">
          <input
            className="input !py-1 !px-2 !text-[12px]"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commitRename();
              if (e.key === "Escape") { setRenaming(false); setDraft(conv.title); }
            }}
            onClick={(e) => e.stopPropagation()}
          />
          <button className="icon-btn shrink-0" onClick={(e) => { e.stopPropagation(); void commitRename(); }}>
            <CheckIcon className="w-3.5 h-3.5 text-good" />
          </button>
          <button className="icon-btn shrink-0" onClick={(e) => { e.stopPropagation(); setRenaming(false); setDraft(conv.title); }}>
            <XIcon className="w-3.5 h-3.5" />
          </button>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-1.5">
            {conv.pinned && <PinIcon className="w-3 h-3 text-accent shrink-0" />}
            {conv.favorite && <StarIcon className="w-3 h-3 text-warn shrink-0" />}
            <div className="text-[12.5px] text-ink font-medium truncate flex-1">{conv.title}</div>
          </div>
          <div className="flex items-center gap-2 mt-[3px] text-[10px] text-faint">
            <span>{timeAgo(conv.updated_at)}</span>
            <span>·</span>
            <span className="truncate">{conv.model ?? "default model"}</span>
            <span>·</span>
            <span>{formatNumber(conv.total_tokens)} tok</span>
          </div>

          {/* hover actions */}
          <div className="absolute right-1.5 top-1.5 hidden group-hover:flex items-center gap-0.5 bg-raised rounded-lg border border-line p-[2px]">
            <button className={cx("icon-btn !w-6 !h-6", conv.pinned && "active")}
              title="Pin" onClick={(e) => { e.stopPropagation(); void patchConversation(conv.id, { pinned: !conv.pinned }); }}>
              <PinIcon className="w-3 h-3" />
            </button>
            <button className={cx("icon-btn !w-6 !h-6", conv.favorite && "active")}
              title="Favorite" onClick={(e) => { e.stopPropagation(); void patchConversation(conv.id, { favorite: !conv.favorite }); }}>
              <StarIcon className="w-3 h-3" />
            </button>
            <button className="icon-btn !w-6 !h-6" title="Rename"
              onClick={(e) => { e.stopPropagation(); setConfirmDelete(false); setRenaming(true); }}>
              <EditIcon className="w-3 h-3" />
            </button>
            <button className="icon-btn !w-6 !h-6" title={conv.archived ? "Unarchive" : "Archive"}
              onClick={(e) => { e.stopPropagation(); void archiveConversation(conv.id, !conv.archived); }}>
              <ArchiveIcon className="w-3 h-3" />
            </button>
            <button className="icon-btn danger !w-6 !h-6" title="Delete"
              onClick={(e) => { e.stopPropagation(); setConfirmDelete(true); }}>
              <TrashIcon className="w-3 h-3" />
            </button>
          </div>

          {confirmDelete && (
            <div className="mt-2 flex items-center gap-1.5 anim-fade-in" onClick={(e) => e.stopPropagation()}>
              <span className="text-[10.5px] text-bad flex-1">Delete permanently?</span>
              <button className="btn !py-[3px] !px-2 !text-[10px] btn-danger"
                onClick={() => { setConfirmDelete(false); void removeConversation(conv.id); }}>
                Delete
              </button>
              <button className="btn btn-ghost !py-[3px] !px-2 !text-[10px]" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function LeftSidebar() {
  const { convSearch, setConvSearch, conversations, showArchived, setShowArchived,
    setActiveId, setView } = useStore();

  const { pinned, groups } = useMemo(() => {
    const p = conversations.filter((c) => c.pinned);
    const r = conversations.filter((c) => !c.pinned);
    const ordered = ["Today", "Yesterday", "Previous 7 days", "Older"];
    const map = new Map<string, ConversationData[]>();
    for (const c of r) {
      const bucket = convSearch.trim() ? "Results" : dayBucket(c.updated_at);
      const list = map.get(bucket) ?? [];
      list.push(c);
      map.set(bucket, list);
    }
    const keys = convSearch.trim() ? [...map.keys()]
      : ordered.filter((k) => map.has(k));
    return { pinned: p, groups: keys.map((k) => [k, map.get(k)!] as const) };
  }, [conversations, convSearch]);

  return (
    <aside className="glass border-y-0 border-l-0 w-[276px] shrink-0 flex flex-col min-h-0"
      style={{ borderRadius: 0 }}>
      <div className="p-3 pb-2 space-y-2">
        <div className="micro-label px-1">Deck</div>
        <button className="btn btn-accent w-full justify-center !py-2"
          onClick={() => { setActiveId(null); setView("chat"); }}>
          <PlusIcon className="w-3.5 h-3.5" /> New chat
        </button>
        <nav className="space-y-[2px] pt-1">
          <div className="micro-label px-1 pt-1 pb-0.5">Core</div>
          <NavItem view="chat" icon={<ChatIcon className="w-4 h-4" />} label="Chat" />
          <NavItem view="coder" icon={<CodeIcon className="w-4 h-4" />} label="Coder Mode" />
          <NavItem view="agent" icon={<BotIcon className="w-4 h-4" />} label="Agent Mode" />
          <NavItem view="models" icon={<ModelsIcon className="w-4 h-4" />} label="Model Center" />
          <div className="micro-label px-1 pt-2.5 pb-0.5">Ops</div>
          <NavItem view="projects" icon={<FolderIcon className="w-4 h-4" />} label="Projects" />
          <NavItem view="git" icon={<GitIcon className="w-4 h-4" />} label="Git / GitHub" />
          <div className="micro-label px-1 pt-2.5 pb-0.5">Labs</div>
          <NavItem view="compare" icon={<GaugeIcon className="w-4 h-4" />} label="Compare" />
          <NavItem view="team" icon={<UsersIcon className="w-4 h-4" />} label="Team" />
          <NavItem view="research" icon={<ResearchIcon className="w-4 h-4" />} label="Research" />
        </nav>
      </div>

      <div className="px-3 pt-2 pb-1.5 flex items-center justify-between border-t border-line">
        <div className="micro-label">{showArchived ? "Archived" : "Chats"}</div>
        <button className="text-[10px] text-faint hover:text-accent transition-colors"
          onClick={() => setShowArchived(!showArchived)}>
          {showArchived ? "← Back to chats" : "Archived"}
        </button>
      </div>

      <div className="px-3 pb-2">
        <div className="relative">
          <SearchIcon className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
          <input
            className="input !pl-8 !py-[7px] !text-[12px]"
            placeholder="Search chats…"
            value={convSearch}
            onChange={(e) => setConvSearch(e.target.value)}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3 min-h-0">
        {pinned.length > 0 && !showArchived && (
          <>
            <div className="micro-label px-1.5 pt-1 pb-1">Pinned</div>
            <div className="space-y-[2px]">{pinned.map((c) => <ConversationRow key={c.id} conv={c} />)}</div>
          </>
        )}
        {groups.map(([label, list]) => (
          <div key={label}>
            <div className="micro-label px-1.5 pt-3 pb-1">{label}</div>
            <div className="space-y-[2px]">{list.map((c) => <ConversationRow key={c.id} conv={c} />)}</div>
          </div>
        ))}
        {conversations.length === 0 && (
          <div className="text-center text-[11.5px] text-faint py-8 px-3">
            {convSearch ? "No chats match your search." : showArchived
              ? "No archived chats." : "No conversations yet. Start a new chat above."}
          </div>
        )}
      </div>

    </aside>
  );
}
