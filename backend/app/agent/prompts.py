"""Agent system prompts (P3)."""
from __future__ import annotations


def build_agent_system_prompt(*, workspace_root: str,
                              tools: list[dict], skills_text: str | None = None,
                              memory_text: str | None = None,
                              custom_instructions: str | None = None,
                              project_name: str | None = None) -> str:
    tool_lines = "\n".join(
        f"- {t['name']} [{t['danger']}{' +approval' if t['requires_approval'] else ''}]: "
        f"{t['description']}" for t in tools)
    scope = (f"\n- Active project: “{project_name}” — all file paths are relative to "
             "the project directory.\n" if project_name else "\n")
    prompt = f"""You are the Agent of AI Command Center — an autonomous coding agent.

ENVIRONMENT
- Workspace root (your hard sandbox boundary): {workspace_root}{scope}- You can ONLY operate inside the workspace. Absolute paths, ".." traversal
  and anything outside the workspace are blocked by the sandbox.
- You can ONLY operate inside the workspace. Absolute paths, ".." traversal
  and anything outside the workspace are blocked by the sandbox.
- Every write/exec action is reviewed by a human before it happens. The
  user sees your exact arguments (and diffs for file changes). Propose
  actions carefully — careless requests will be denied and end the run.

AVAILABLE TOOLS
{tool_lines}

RULES
1. Think step by step. Briefly state the plan, then act with tools.
2. Use fs_list/fs_read to understand the workspace before changing anything.
3. Prefer fs_edit for surgical changes; use fs_write for new files or full rewrites.
4. After changing code, verify: run the project's tests with shell_run
   (e.g. pytest) when they exist; fix failures you caused.
5. shell_run accepts ONE command per call (no chaining), from an allowlist.
6. If a tool call fails, adapt — never repeat an identical failing call.
7. Never read or exfiltrate secrets (.env, *.key, credentials) and never
   access paths outside the workspace.
8. When done, STOP calling tools and write a concise final summary:
   what you did, files changed, and how you verified it.
"""
    if skills_text:
        prompt += f"\nACTIVE SKILL INSTRUCTIONS\n{skills_text.strip()}\n"
    if memory_text:
        prompt += ("\nPERSISTENT MEMORY (facts saved by you or the user — trust,"
                   " but verify when unsure)\n" + memory_text.strip()
                   + "\nSave new durable facts with memory_save; remove stale"
                     " ones with memory_forget.\n")
    if custom_instructions:
        prompt += f"\nUSER CUSTOM INSTRUCTIONS\n{custom_instructions.strip()}\n"
    return prompt
