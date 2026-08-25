// App shell — header + 3-panel workspace (left nav / center / inspector).
import { useStore } from "./store";
import { Header } from "./components/Header";
import { LeftSidebar } from "./components/LeftSidebar";
import { RightInspector } from "./components/RightInspector";
import { ChatView } from "./components/ChatView";
import { AgentView } from "./components/AgentView";
import { CoderView } from "./components/CoderView";
import { CompareView } from "./components/CompareView";
import { TeamView } from "./components/TeamView";
import { ResearchView } from "./components/ResearchView";
import { GitView } from "./components/GitView";
import { ProjectsView } from "./components/ProjectsView";
import { ModelCenter } from "./components/ModelCenter";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { CommandPalette, ShortcutsHelp, useGlobalShortcuts } from "./components/CommandPalette";

function Toasts() {
  const { toasts } = useStore();
  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind === "bad" ? "toast-bad" : t.kind === "good" ? "toast-good" : ""}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const { view, leftOpen, rightOpen } = useStore();
  useGlobalShortcuts();
  return (
    <>
      <div className="ambient" aria-hidden="true">
        <div className="ambient-aurora" />
        <div className="ambient-grid" />
        <div className="ambient-scan" />
      </div>
      <div className="deck flex flex-col h-full">
        <Header />
        <div className="flex flex-1 min-h-0">
          {leftOpen && <LeftSidebar />}
          <main className="flex-1 min-w-0 flex flex-col min-h-0">
            <div key={view} className="view-stage flex-1 min-h-0 flex flex-col">
              {view === "chat" && <ChatView />}
              {view === "agent" && <AgentView />}
              {view === "coder" && <CoderView />}
              {view === "compare" && <CompareView />}
              {view === "team" && <TeamView />}
              {view === "research" && <ResearchView />}
              {view === "git" && <GitView />}
              {view === "projects" && <ProjectsView />}
              {view === "models" && <ModelCenter />}
            </div>
          </main>
          {rightOpen && view === "chat" && <RightInspector />}
        </div>
        <SettingsDrawer />
        <CommandPalette />
        <ShortcutsHelp />
        <Toasts />
      </div>
    </>
  );
}
