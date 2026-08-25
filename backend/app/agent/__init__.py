"""Agent Mode (P3) — real, gateway-guarded autonomous runs.

The engine lives in ``agent.engine``. Every machine-facing action a
model proposes passes: permission policy (security.permissions) →
argument validation → human approval (write/exec tiers) → sandboxed
execution (workspace/) → an audit row in ``executions``. Nothing reaches
the machine outside this path.
"""
