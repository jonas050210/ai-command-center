// Shared UI primitives for the Phase 4-7 views.
import type { ReactNode } from "react";
import { cx } from "../utils";

export function Panel({ title, sub, right, children, className }: {
  title: string; sub?: string; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <div className={cx("glass rounded-2xl p-4", className)}>
      <div className="flex items-center gap-2 mb-3">
        <div>
          <div className="text-[13px] font-bold text-ink">{title}</div>
          {sub && <div className="text-[10px] text-faint mt-[1px]">{sub}</div>}
        </div>
        <div className="flex-1" />
        {right}
      </div>
      {children}
    </div>
  );
}

export function Empty({ text, sub }: { text: string; sub?: string }) {
  return (
    <div className="glass rounded-2xl py-12 text-center anim-fade-up">
      <div className="text-[13px] text-dim font-medium">{text}</div>
      {sub && <div className="text-[11px] text-faint mt-1.5 max-w-[440px] mx-auto px-6">{sub}</div>}
    </div>
  );
}

export function LogRow({ tag, text, tone }: { tag: string; text: string; tone?: string }) {
  return (
    <div className="flex items-start gap-2 py-[3px] text-[11.5px] leading-snug">
      <span className={cx("chip !text-[8.5px] !py-[1px] shrink-0 mt-[1px]", tone)}>{tag}</span>
      <span className="text-dim break-words min-w-0 whitespace-pre-wrap">{text}</span>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="micro-label">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}
