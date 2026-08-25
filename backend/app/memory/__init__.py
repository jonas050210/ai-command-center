"""Long-term memory + workspace skills (P8) — REAL and wired.

Two sources shape every agent run:
* ``AGENT.md`` in the workspace root (and optionally the project root) —
  user-authored standing instructions, loaded at run start;
* ``memories`` table rows — durable facts saved by the user or by agent
  runs through the ``memory_*`` tools (gateway-approved, audited).

Both are injected into the agent system prompt in clearly delimited,
honestly-labeled sections. See memory/service.py.
"""
