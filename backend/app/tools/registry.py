"""Tool system (P3) — the registry every model-facing capability goes through.

A tool is: JSON-schema contract + danger tier + required capability +
async handler. The agent engine can ONLY reach the machine through
registered tools; each execution additionally passes the permission
policy, the approval system (for write/exec tiers) and the audit log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from ..security.permissions import Capability


class ToolDanger(str, Enum):
    READ = "read"        # no mutation — auto-approved, still policy-gated
    WRITE = "write"      # mutates files — requires human approval
    EXEC = "exec"        # runs processes / network mutation — approval


@dataclass
class ToolResult:
    ok: bool
    output: str
    exit_code: int | None = None
    ms: float = 0.0
    diff: str | None = None         # unified diff for file mutations (honest preview)
    error: str | None = None

    def as_text(self, max_chars: int = 6000) -> str:
        text = self.output if self.ok else f"ERROR: {self.error or self.output}"
        if len(text) > max_chars:
            half = max_chars // 2
            text = (text[:half] + f"\n…[truncated {len(text) - max_chars} chars]…\n"
                    + text[-half:])
        return text


@dataclass
class ToolContext:
    """Per-run execution context handed to handlers."""
    workspace_root: Any              # Path — hard sandbox boundary
    project_root: Any | None = None  # Path | None (P4 projects)
    run_id: str | None = None
    actor: str = "agent"
    memory: Any | None = None        # MemoryService | None (P8)
    snapshot: Any | None = None      # RunSnapshot | None (P12)

    @property
    def root(self):
        return self.project_root or self.workspace_root


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]      # JSON Schema (object)
    danger: ToolDanger
    capability: Capability
    handler: ToolHandler
    requires_approval: bool = field(default=False)

    def __post_init__(self):
        if self.danger != ToolDanger.READ:
            self.requires_approval = True

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }}

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "danger": self.danger.value, "capability": self.capability.value,
                "requires_approval": self.requires_approval,
                "parameters": self.parameters}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, only: list[str] | None = None) -> list[dict[str, Any]]:
        names = only if only else self.names()
        return [self._tools[n].schema() for n in names if n in self._tools]

    def describe_all(self) -> list[dict[str, Any]]:
        return [self._tools[n].describe() for n in self.names()]

    def validate_args(self, spec: ToolSpec, args: dict[str, Any]) -> str | None:
        """Minimal structural validation (required keys, unknown keys,
        string coercions). Returns an error message or None."""
        if not isinstance(args, dict):
            return "tool arguments must be a JSON object"
        schema = spec.parameters or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        missing = [k for k in required if k not in args]
        if missing:
            return f"missing required argument(s): {', '.join(missing)}"
        unknown = [k for k in args if k not in props]
        if unknown:
            return f"unknown argument(s): {', '.join(unknown)}"
        for key, value in args.items():
            expected = (props.get(key) or {}).get("type")
            if expected == "string" and value is not None and not isinstance(value, str):
                return f"argument '{key}' must be a string"
            if expected == "integer" and value is not None and not isinstance(value, int):
                return f"argument '{key}' must be an integer"
            if expected == "boolean" and value is not None and not isinstance(value, bool):
                return f"argument '{key}' must be a boolean"
        return None
