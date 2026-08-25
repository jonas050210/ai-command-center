// App shell — header + 3-panel workspace (left nav / center / inspector).
import { useStore } from "./store";
import { Header } from "./components/Header";
import { LeftSidebar } from "./components/LeftSidebar";
import { RightInspector } from "./components/RightInspector";
import { ChatView } from "./components/ChatView";
import { ModelCenter } from "./components/ModelCenter";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { NotImplemented } from "./components/NotImplemented";

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
          {view !== "chat" && view !== "models" && <NotImplemented view={view} />}
        </main>
        {rightOpen && view === "chat" && <RightInspector />}
      </div>
      <SettingsDrawer />
      <Toasts />
    </div>
  );
}
