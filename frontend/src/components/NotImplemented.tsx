// Honest placeholder for future-phase features — never a fake demo.
import { useStore, type View } from "../store";
import {
  BotIcon, FolderIcon, GitIcon, ModelsIcon, ResearchIcon, UsersIcon,
} from "../icons";

const INFO: Record<string, { title: string; icon: React.ReactNode; phase: string; body: string }> = {
  agent: {
    title: "Agent Mode", phase: "Phase 4",
    icon: <BotIcon className="w-7 h-7" />,
    body: "Autonomous task execution with a file-system sandbox, allow-listed commands, permission checks and a full audit log. The security foundation is already in place.",
  },
  team: {
    title: "Team Mode — Multi-Model AI Team", phase: "Phase 5",
    icon: <UsersIcon className="w-7 h-7" />,
    body: "The flagship feature: 2–4 models analyze one complex task together, create a master plan, divide work by strength, review each other, test, fix and deliver. Database foundations (teams, team_members, tasks, per-model token tracking) are already migrated.",
  },
  research: {
    title: "Research Mode", phase: "Phase 6",
    icon: <ResearchIcon className="w-7 h-7" />,
    body: "Structured research workspace: questions, source tracking, synthesis and reports. The research table already exists in the schema.",
  },
  projects: {
    title: "Projects", phase: "Phase 4",
    icon: <FolderIcon className="w-7 h-7" />,
    body: "Project workspaces with files, tasks and context that chats, agents and teams can attach to. Projects and files tables already exist in the schema.",
  },
  git: {
    title: "Git / GitHub Integration", phase: "Phase 7",
    icon: <GitIcon className="w-7 h-7" />,
    body: "Commit, branch, diff and push from the workspace — always through the permission system with full execution logging.",
  },
};

export function NotImplemented({ view }: { view: View }) {
  const { setView } = useStore();
  const info = INFO[view] ?? INFO.agent;
  return (
    <div className="flex-1 flex items-center justify-center px-6">
      <div className="glass rounded-2xl max-w-[520px] w-full p-8 text-center anim-fade-up">
        <div className="mx-auto w-14 h-14 rounded-2xl bg-accentdim border border-[rgba(69,227,255,0.25)]
          flex items-center justify-center text-accent mb-5">
          {info.icon}
        </div>
        <div className="chip chip-warn mx-auto mb-3">NOT IMPLEMENTED</div>
        <h2 className="text-[18px] font-bold">{info.title}</h2>
        <div className="text-[11px] text-accent2 font-semibold mt-1 tracking-wide uppercase">{info.phase} · see ROADMAP</div>
        <p className="text-[12.5px] text-dim leading-relaxed mt-4">{info.body}</p>
        <p className="text-[11px] text-faint mt-4">
          This interface deliberately shows no fake functionality. The API boundary
          (<code className="inline-code">/api/{view}</code>) answers HTTP 501 until the phase ships.
        </p>
        <button className="btn btn-accent mx-auto mt-6" onClick={() => setView("chat")}>
          <ModelsIcon className="w-3.5 h-3.5" /> Back to what works: Chat + Model Center
        </button>
      </div>
    </div>
  );
}
