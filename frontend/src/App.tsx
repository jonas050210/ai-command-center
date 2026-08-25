// App shell — header + 3-panel workspace (left nav / center / inspector).
import { useStore } from "./store";
import { Header } from "./components/Header";
import { LeftSidebar } from "./components/LeftSidebar";
import { RightInspector } from "./components/RightInspector";
import { ChatView } from "./components/ChatView";
import { ModelCenter } from "./components/ModelCenter";
import { AgentView } from "./components/AgentView";
import { TeamView } from "./components/TeamView";
import { CompareView } from "./components/CompareView";
import { ResearchView } from "./components/ResearchView";
import { ProjectsView } from "./components/ProjectsView";
import { GitView } from "./components/GitView";
import { SettingsDrawer } from "./components/SettingsDrawer";

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
  return (
    <div className="flex flex-col h-full">
      <Header />
      <div className="flex flex-1 min-h-0">
        {leftOpen && <LeftSidebar />}
        <main className="flex-1 min-w-0 flex flex-col min-h-0">
          {view === "chat" && <ChatView />}
          {view === "models" && <ModelCenter />}
          {view === "agent" && <AgentView />}
          {view === "team" && <TeamView />}
          {view === "compare" && <CompareView />}
          {view === "research" && <ResearchView />}
          {view === "projects" && <ProjectsView />}
          {view === "git" && <GitView />}
        </main>
        {rightOpen && view === "chat" && <RightInspector />}
      </div>
      <SettingsDrawer />
      <Toasts />
    </div>
  );
}
