// Honest placeholder for future-phase features — never a fake demo.
import { useStore, type View } from "../store";
import { GitIcon, ModelsIcon } from "../icons";

const INFO: Record<string, { title: string; icon: React.ReactNode; phase: string; body: string }> = {
  git: {
    title: "Git / GitHub Integration", phase: "Phase 7",
    icon: <GitIcon className="w-7 h-7" />,
    body: "Commit, branch, diff and push from the workspace — always through the permission system with full execution logging.",
  },
};

export function NotImplemented({ view }: { view: View }) {
  const { setView } = useStore();
  const info = INFO[view] ?? INFO.git;
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
