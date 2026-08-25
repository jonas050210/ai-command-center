"""Team Mode member prompts — deterministic, tool-free roles get plain text."""
from __future__ import annotations


def build_planner_prompt(*, responsibility: str, task: str, prior_plan: str) -> str:
    resp = f"\nYour specialty: {responsibility}" if responsibility else ""
    prior = (f"\n\nEarlier planning stage refined the plan to:\n{prior_plan}\n"
             "Improve it — keep what is good, fix what is missing."
             if prior_plan else "")
    return (f"You are the PLANNER of a small AI team.{resp}\n"
            f"Break the user's task into a precise, numbered execution plan for the "
            f"team EXECUTOR. The executor can list/read/write files and run allowed "
            f"commands (pytest, python, git, …) inside a sandboxed workspace — every "
            f"write/exec is human-approved, so plan only safe, verifiable steps.\n"
            f"Rules: max 8 steps; end with a verification step (tests/command); "
            f"no fluff, just the plan.{prior}\n\nTASK:\n{task}")


def build_reviewer_prompt(*, responsibility: str, task: str, plan: str,
                          result: str) -> str:
    resp = f"\nYour specialty: {responsibility}" if responsibility else ""
    return (f"You are the REVIEWER of a small AI team.{resp}\n"
            "Judge whether the executor's work fulfils the task, judging only the "
            "facts shown below. Respond with the FIRST line exactly either\n"
            "VERDICT: ACCEPTED\nor\nVERDICT: CHANGES_REQUESTED\n"
            "then bullet points with concrete required changes (if any). Be strict "
            "about correctness but pragmatic about scope.\n\n"
            f"TASK:\n{task}\n\nPLAN:\n{plan or '(no plan recorded)'}\n\n"
            f"EXECUTOR RESULT:\n{result or '(executor produced no summary)'}")


def build_executor_task(*, task: str, plan: str, review_feedback: str) -> str:
    parts = [task.strip()]
    if plan.strip():
        parts.append("TEAM PLAN (follow it):\n" + plan.strip())
    if review_feedback.strip():
        parts.append("THE TEAM REVIEWER REQUESTED THESE CHANGES — address them:\n"
                     + review_feedback.strip())
    return "\n\n".join(parts)


def parse_verdict(review_text: str) -> str | None:
    """'accepted' | 'changes_requested' | None (model didn't follow format)."""
    import re
    m = re.search(r"VERDICT:\s*(ACCEPTED|CHANGES_REQUESTED)", review_text or "",
                  re.IGNORECASE)
    if not m:
        return None
    return "accepted" if m.group(1).lower() == "accepted" else "changes_requested"
