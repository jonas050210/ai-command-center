// Shared agent/team UI primitives — danger/status chips, JSON & diff
// blocks, and the human approval card. Used by AgentView and TeamView;
// single implementation, no duplicates.
import { cx } from "../utils";
import { CheckIcon, ShieldIcon, XIcon } from "../icons";

export function dangerChip(danger: string) {
  if (danger === "exec") return "chip !text-[9px] bg-[rgba(248,113,113,0.12)] text-bad border-[rgba(248,113,113,0.35)]";
  if (danger === "write") return "chip chip-warn !text-[9px]";
  return "chip chip-good !text-[9px]";
}

export function statusChip(status: string) {
  const map: Record<string, string> = {
    complete: "chip-good", ok: "chip-good", approved: "chip-good", accepted: "chip-good",
    running: "", pending: "",
    stopped: "chip-warn", expired: "chip-warn", changes_requested: "chip-warn",
    denied: "text-bad border-[rgba(248,113,113,0.35)] bg-[rgba(248,113,113,0.1)]",
    denied_by_user: "text-bad border-[rgba(248,113,113,0.35)] bg-[rgba(248,113,113,0.1)]",
    error: "text-bad border-[rgba(248,113,113,0.35)] bg-[rgba(248,113,113,0.1)]",
  };
  return `chip !text-[9px] ${map[status] ?? ""}`;
}

export function JsonBlock({ data }: { data: Record<string, unknown> }) {
  return (
    <pre className="code-block !text-[10.5px] !p-2.5 max-h-[200px] overflow-auto">
      {JSON.stringify(data, null, 1)}
    </pre>
  );
}

export function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre className="code-block !text-[10.5px] !p-2.5 max-h-[260px] overflow-auto">
      {diff.split("\n").map((line, i) => (
        <div key={i} className={
          line.startsWith("+") && !line.startsWith("+++" ) ? "text-good"
            : line.startsWith("-") && !line.startsWith("---") ? "text-bad"
            : line.startsWith("@@") ? "text-accent" : "text-dim"}>
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

export function ApprovalCard({ entry, onDecide, deciding }: {
  entry: { id: string; tool: string; args: Record<string, unknown>;
           preview: string | null; danger: string;
           status: "pending" | "approved" | "denied" | "expired" | string };
  onDecide: (id: string, approve: boolean) => void;
  deciding: boolean;
}) {
  const pending = entry.status === "pending";
  return (
    <div className={cx(
      "my-2.5 rounded-xl border p-3.5 anim-fade-up",
      pending ? "border-[rgba(251,191,36,0.45)] bg-[rgba(251,191,36,0.05)]" : "border-line glass-soft")}>
      <div className="flex items-center gap-2 flex-wrap">
        <ShieldIcon className={cx("w-4 h-4 shrink-0", pending ? "text-warn" : "text-dim")} />
        <span className="text-[12.5px] font-semibold">
          Approval required: <span className="font-mono text-accent">{entry.tool}</span>
        </span>
        <span className={dangerChip(entry.danger)}>{entry.danger.toUpperCase()}</span>
        <span className={cx(statusChip(entry.status), "ml-auto")}>{entry.status}</span>
      </div>
      <details className="mt-2">
        <summary className="text-[10.5px] text-faint cursor-pointer hover:text-dim">
          Arguments
        </summary>
        <JsonBlock data={entry.args} />
      </details>
      {entry.preview && (
        <div className="mt-2">
          <div className="text-[10.5px] text-faint mb-1">Exact change you are approving:</div>
          <DiffBlock diff={entry.preview} />
        </div>
      )}
      {pending && (
        <div className="flex items-center gap-2 mt-3">
          <button className="btn !text-[11px] !py-1.5 !px-3.5 border-[rgba(74,222,128,0.5)] text-good hover:!bg-[rgba(74,222,128,0.1)]"
            disabled={deciding} onClick={() => onDecide(entry.id, true)}>
            <CheckIcon className="w-3.5 h-3.5" /> Approve
          </button>
          <button className="btn btn-danger !text-[11px] !py-1.5 !px-3.5"
            disabled={deciding} onClick={() => onDecide(entry.id, false)}>
            <XIcon className="w-3.5 h-3.5" /> Deny & stop run
          </button>
          <span className="text-[9.5px] text-faint ml-1">
            Nothing executes before you decide. Denying stops the whole run.
          </span>
        </div>
      )}
    </div>
  );
}

export function ToolCallCard({ tool, args, status, output, diff, danger, ms, callId }: {
  tool: string; args: Record<string, unknown>; status: string;
  output?: string; diff?: string | null; danger: string; ms?: number; callId?: string;
}) {
  return (
    <div className="my-2 glass-soft rounded-xl border border-line p-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-[12px] font-semibold text-accent">{tool}</span>
        {danger && <span className={dangerChip(danger)}>{danger.toUpperCase()}</span>}
        <span className={statusChip(status)}>
          {status === "running" ? "running…" : status}
        </span>
        {ms !== undefined && <span className="text-[9.5px] text-faint">{Math.round(ms)} ms</span>}
        {callId && <span className="text-[9px] text-faint ml-auto">#{callId}</span>}
      </div>
      <details className="mt-2">
        <summary className="text-[10.5px] text-faint cursor-pointer hover:text-dim">Arguments</summary>
        <JsonBlock data={args} />
      </details>
      {output && (
        <pre className={cx("code-block !text-[10.5px] !p-2.5 mt-2 max-h-[220px] overflow-auto",
          status === "error" && "!border-[rgba(248,113,113,0.35)] text-bad/90")}>
          {output}
        </pre>
      )}
      {diff && <div className="mt-2"><DiffBlock diff={diff} /></div>}
    </div>
  );
}
