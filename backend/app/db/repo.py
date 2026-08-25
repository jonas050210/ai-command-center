"""Repository layer — all SQL for domain objects in one module.

Keeps routers/services free of raw SQL and gives every later phase
(Team, Agent, Research) a clean data-access API.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .database import Database


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    return uuid.uuid4().hex


# ────────────────────────────── settings ──────────────────────────────
class SettingsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, key: str) -> str | None:
        row = await self.db.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    async def set(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, utcnow()),
        )

    async def all(self) -> dict[str, str]:
        rows = await self.db.fetchall("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}


# ────────────────────────────── providers ─────────────────────────────
class ProvidersRepo:
    def __init__(self, db: Database):
        self.db = db

    async def upsert(self, name: str, display_name: str, is_local: bool, base_url: str | None,
                     cost_in: float = 0.0, cost_out: float = 0.0) -> None:
        await self.db.execute(
            "INSERT INTO providers (name, display_name, is_local, base_url,"
            " cost_input_per_mtok, cost_output_per_mtok) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET display_name=excluded.display_name,"
            " is_local=excluded.is_local, base_url=excluded.base_url, updated_at=?",
            (name, display_name, int(is_local), base_url, cost_in, cost_out, utcnow()),
        )

    async def set_status(self, name: str, status: str) -> None:
        await self.db.execute(
            "UPDATE providers SET status=?, last_seen_at=?, updated_at=? WHERE name=?",
            (status, utcnow(), utcnow(), name),
        )

    async def get(self, name: str) -> dict | None:
        return await self.db.fetchone("SELECT * FROM providers WHERE name=?", (name,))

    async def list(self) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM providers ORDER BY name")


# ─────────────────────────────── models ───────────────────────────────
class ModelsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def upsert_from_provider(self, m: dict[str, Any]) -> None:
        await self.db.execute(
            """INSERT INTO models (provider, name, display_name, is_local, is_free,
                   cost_input_per_mtok, cost_output_per_mtok, context_length, size_bytes,
                   parameter_size, quantization, family, families_json, capabilities_json,
                   categories_json, available, status, last_seen_at, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider, name) DO UPDATE SET
                   display_name=excluded.display_name, is_local=excluded.is_local,
                   is_free=excluded.is_free, context_length=excluded.context_length,
                   size_bytes=excluded.size_bytes, parameter_size=excluded.parameter_size,
                   quantization=excluded.quantization, family=excluded.family,
                   families_json=excluded.families_json,
                   capabilities_json=excluded.capabilities_json,
                   categories_json=excluded.categories_json,
                   available=excluded.available, status=excluded.status,
                   last_seen_at=excluded.last_seen_at, raw_json=excluded.raw_json,
                   updated_at=?""",
            (
                m["provider"], m["name"], m["display_name"], int(m["is_local"]),
                int(m["is_free"]), m.get("cost_input_per_mtok", 0.0),
                m.get("cost_output_per_mtok", 0.0), m.get("context_length"),
                m.get("size_bytes"), m.get("parameter_size"), m.get("quantization"),
                m.get("family"), json.dumps(m.get("families") or []),
                json.dumps(m.get("capabilities") or []),
                json.dumps(m.get("categories") or []), int(m.get("available", True)),
                m.get("status", "available"), utcnow(), json.dumps(m.get("raw") or {}),
                utcnow(),
            ),
        )

    async def mark_missing(self, provider: str, present_names: list[str]) -> None:
        """Models not seen in the latest provider scan become unavailable."""
        rows = await self.db.fetchall("SELECT name FROM models WHERE provider=?", (provider,))
        for r in rows:
            if r["name"] not in present_names:
                await self.db.execute(
                    "UPDATE models SET available=0, status='unavailable', updated_at=?"
                    " WHERE provider=? AND name=?",
                    (utcnow(), provider, r["name"]),
                )

    async def get(self, provider: str, name: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM models WHERE provider=? AND name=?", (provider, name))

    async def get_by_id(self, model_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM models WHERE id=?", (model_id,))

    async def list(self, *, q: str | None = None, category: str | None = None,
                   favorites: bool = False, available_only: bool = False,
                   sort: str = "name") -> list[dict]:
        sql, params = "SELECT * FROM models WHERE 1=1", []
        if q:
            sql += " AND (name LIKE ? OR display_name LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if category:
            sql += " AND categories_json LIKE ?"
            params.append(f'%"{category}"%')
        if favorites:
            sql += " AND favorite=1"
        if available_only:
            sql += " AND available=1"
        order = {
            "name": "name COLLATE NOCASE ASC",
            "size": "size_bytes DESC",
            "recent": "last_used_at IS NULL, last_used_at DESC",
            "speed": "measured_tps IS NULL, measured_tps DESC",
            "favorite": "favorite DESC, name COLLATE NOCASE ASC",
        }.get(sort, "name COLLATE NOCASE ASC")
        sql += f" ORDER BY {order}"
        return await self.db.fetchall(sql, params)

    async def set_favorite(self, provider: str, name: str, fav: bool) -> None:
        await self.db.execute(
            "UPDATE models SET favorite=?, updated_at=? WHERE provider=? AND name=?",
            (int(fav), utcnow(), provider, name))

    async def delete(self, provider: str, name: str) -> None:
        await self.db.execute("DELETE FROM models WHERE provider=? AND name=?", (provider, name))

    async def record_usage(self, provider: str, name: str, in_tok: int, out_tok: int,
                           tps: float | None) -> None:
        await self.db.execute(
            "UPDATE models SET total_input_tokens=total_input_tokens+?,"
            " total_output_tokens=total_output_tokens+?, usage_count=usage_count+1,"
            " last_used_at=?, measured_tps=COALESCE(?, measured_tps),"
            " measured_at=CASE WHEN ? IS NOT NULL THEN ? ELSE measured_at END,"
            " updated_at=? WHERE provider=? AND name=?",
            (in_tok, out_tok, utcnow(), tps, tps, utcnow(), utcnow(), provider, name))

    async def totals(self) -> dict:
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(total_input_tokens),0) AS i,"
            " COALESCE(SUM(total_output_tokens),0) AS o FROM models")
        return {"input": row["i"], "output": row["o"]}


# ──────────────────────────── conversations ───────────────────────────
class ConversationsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, title: str, model: str | None, provider: str | None,
                     system_prompt: str | None,
                     project_id: int | None = None) -> dict:
        cid = new_id()
        await self.db.execute(
            "INSERT INTO conversations (id, title, model, provider, system_prompt,"
            " project_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (cid, title, model, provider, system_prompt, project_id, utcnow(), utcnow()))
        return await self.get(cid)  # type: ignore[return-value]

    async def get(self, cid: str) -> dict | None:
        return await self.db.fetchone("SELECT * FROM conversations WHERE id=?", (cid,))

    async def list(self, *, q: str | None = None, archived: bool = False,
                   include_archived: bool = False) -> list[dict]:
        sql = ("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id)"
               " AS message_count FROM conversations c WHERE 1=1")
        params: list[Any] = []
        if not include_archived:
            sql += " AND c.archived=?"
            params.append(int(archived))
        if q:
            sql += (" AND (c.title LIKE ? OR EXISTS (SELECT 1 FROM messages m"
                    " WHERE m.conversation_id=c.id AND m.content LIKE ?))")
            params += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY c.pinned DESC, c.updated_at DESC"
        return await self.db.fetchall(sql, params)

    async def update(self, cid: str, **fields: Any) -> None:
        allowed = {"title", "model", "provider", "system_prompt",
                   "pinned", "archived", "favorite", "project_id"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(int(v) if isinstance(v, bool) else v)
        if not sets:
            return
        sets.append("updated_at=?")
        params += [utcnow(), cid]
        await self.db.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id=?", params)

    async def touch(self, cid: str) -> None:
        await self.db.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                              (utcnow(), cid))

    async def add_tokens(self, cid: str, in_tok: int, out_tok: int) -> None:
        await self.db.execute(
            "UPDATE conversations SET total_input_tokens=total_input_tokens+?,"
            " total_output_tokens=total_output_tokens+?, updated_at=? WHERE id=?",
            (in_tok, out_tok, utcnow(), cid))

    async def delete(self, cid: str) -> None:
        await self.db.execute("DELETE FROM conversations WHERE id=?", (cid,))


# ─────────────────────────────── messages ─────────────────────────────
class MessagesRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, conversation_id: str, role: str, content: str,
                     model: str | None = None, provider: str | None = None,
                     status: str = "complete") -> dict:
        mid = new_id()
        await self.db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, model, provider,"
            " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (mid, conversation_id, role, content, model, provider, status, utcnow()))
        return await self.get(mid)  # type: ignore[return-value]

    async def get(self, mid: str) -> dict | None:
        return await self.db.fetchone("SELECT * FROM messages WHERE id=?", (mid,))

    async def list_for(self, cid: str) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at, rowid", (cid,))

    async def finalize(self, mid: str, *, content: str, status: str,
                       input_tokens: int | None, output_tokens: int | None,
                       method: str, error: str | None = None) -> None:
        await self.db.execute(
            "UPDATE messages SET content=?, status=?, input_tokens=?, output_tokens=?,"
            " token_method=?, error=? WHERE id=?",
            (content, status, input_tokens, output_tokens, method, error, mid))

    async def delete(self, mid: str) -> None:
        await self.db.execute("DELETE FROM messages WHERE id=?", (mid,))


# ──────────────────────────── credentials ────────────────────────────
class CredentialsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, provider: str) -> dict | None:
        return await self.db.fetchone("SELECT * FROM credentials WHERE provider=?",
                                      (provider,))

    async def upsert(self, provider: str, ciphertext: str) -> None:
        await self.db.execute(
            "INSERT INTO credentials (provider, ciphertext, created_at, updated_at)"
            " VALUES (?,?,?,?) ON CONFLICT(provider) DO UPDATE SET"
            " ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
            (provider, ciphertext, utcnow(), utcnow()))

    async def delete(self, provider: str) -> None:
        await self.db.execute("DELETE FROM credentials WHERE provider=?", (provider,))


# ────────────────────────────── usage/costs ───────────────────────────
class UsageRepo:
    def __init__(self, db: Database):
        self.db = db

    async def record(self, *, conversation_id: str | None, message_id: str | None,
                     model: str | None, provider: str | None, input_tokens: int,
                     output_tokens: int, method: str, cost_eur: float,
                     team_id: int | None = None, team_member_id: int | None = None) -> None:
        await self.db.execute(
            "INSERT INTO usage_events (conversation_id, message_id, team_id, team_member_id,"
            " model, provider, input_tokens, output_tokens, method, cost_eur, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (conversation_id, message_id, team_id, team_member_id, model, provider,
             input_tokens, output_tokens, method, cost_eur, utcnow()))

    async def totals(self) -> dict:
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o,"
            " COALESCE(SUM(cost_eur),0.0) AS c, COUNT(*) AS n FROM usage_events")
        return {"input_tokens": row["i"], "output_tokens": row["o"],
                "cost_eur": float(row["c"]), "events": row["n"]}

    async def per_model(self) -> list[dict]:
        return await self.db.fetchall(
            "SELECT provider, model, SUM(input_tokens) AS i, SUM(output_tokens) AS o,"
            " SUM(cost_eur) AS c FROM usage_events WHERE model IS NOT NULL"
            " GROUP BY provider, model ORDER BY (i+o) DESC")


# ────────────────────────────── executions ────────────────────────────
class ExecutionsRepo:
    """Tool/command execution log (security foundation for Phase 4+)."""

    def __init__(self, db: Database):
        self.db = db

    async def log(self, *, kind: str, status: str, command: str | None = None,
                  actor: str = "user", exit_code: int | None = None,
                  log_text: str = "") -> int:
        cur = await self.db.execute(
            "INSERT INTO executions (kind, status, command, actor, exit_code, log,"
            " started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
            (kind, status, command, actor, exit_code, log_text, utcnow(), utcnow()))
        return int(cur.lastrowid or 0)

    async def list(self, limit: int = 100) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM executions ORDER BY id DESC LIMIT ?", (limit,))


# ─────────────────────── projects (first-class) ──────────────────────
class ProjectsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, name: str, description: str = "", root_path: str | None = None,
                     settings_json: str = "{}") -> dict:
        cur = await self.db.execute(
            "INSERT INTO projects (name, description, root_path, settings_json,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, description, root_path, settings_json, utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM projects WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def get(self, pid: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM projects WHERE id=?", (pid,))

    async def list(self) -> list[dict]:
        return await self.db.fetchall(
            "SELECT p.*, (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id)"
            " AS task_count, (SELECT COUNT(*) FROM conversations c WHERE c.project_id=p.id)"
            " AS chat_count FROM projects p ORDER BY updated_at DESC")

    async def update(self, pid: int, **fields: Any) -> None:
        allowed = {"name", "description", "root_path", "settings_json", "status"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        params += [utcnow(), pid]
        await self.db.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", params)

    async def delete(self, pid: int) -> None:
        await self.db.execute("DELETE FROM projects WHERE id=?", (pid,))

    # workspace files metadata (content lives on disk, sandboxed)
    async def add_file(self, project_id: int, path: str, name: str,
                       size_bytes: int | None, mime: str | None) -> None:
        await self.db.execute(
            "INSERT INTO files (project_id, path, name, size_bytes, mime, created_at)"
            " VALUES (?,?,?,?,?,?)", (project_id, path, name, size_bytes, mime, utcnow()))

    async def list_files(self, project_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM files WHERE project_id=? ORDER BY path", (project_id,))

    async def clear_files(self, project_id: int) -> None:
        await self.db.execute("DELETE FROM files WHERE project_id=?", (project_id,))

    # tasks
    async def add_task(self, project_id: int, title: str, description: str = "") -> dict:
        cur = await self.db.execute(
            "INSERT INTO tasks (project_id, title, description, status, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?)",
            (project_id, title, description, "todo", utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM tasks WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def list_tasks(self, project_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM tasks WHERE project_id=? ORDER BY id", (project_id,))

    async def update_task(self, task_id: int, **fields: Any) -> None:
        allowed = {"title", "description", "status", "team_id"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        params += [utcnow(), task_id]
        await self.db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", params)

    async def delete_task(self, task_id: int) -> None:
        await self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))


# ─────────────────────────────── teams ───────────────────────────────
class TeamsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, name: str, task: str = "", master_plan: str = "",
                     status: str = "pending", project_id: int | None = None) -> dict:
        cur = await self.db.execute(
            "INSERT INTO teams (name, task, master_plan, status, project_id,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (name, task, master_plan, status, project_id, utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM teams WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def get(self, team_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM teams WHERE id=?", (team_id,))

    async def list(self) -> list[dict]:
        return await self.db.fetchall(
            "SELECT t.*, (SELECT COUNT(*) FROM team_members m WHERE m.team_id=t.id)"
            " AS member_count FROM teams t ORDER BY id DESC")

    async def update(self, team_id: int, **fields: Any) -> None:
        allowed = {"name", "task", "master_plan", "status", "project_id"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        params += [utcnow(), team_id]
        await self.db.execute(f"UPDATE teams SET {', '.join(sets)} WHERE id=?", params)

    async def delete(self, team_id: int) -> None:
        await self.db.execute("DELETE FROM teams WHERE id=?", (team_id,))

    # members
    async def add_member(self, team_id: int, model: str, provider: str | None,
                         role: str = "member", responsibility: str = "") -> dict:
        cur = await self.db.execute(
            "INSERT INTO team_members (team_id, provider, model, role, responsibility,"
            " status) VALUES (?,?,?,?,?,?)",
            (team_id, provider, model, role, responsibility, "idle"))
        return await self.db.fetchone("SELECT * FROM team_members WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def members(self, team_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM team_members WHERE team_id=? ORDER BY id", (team_id,))

    async def update_member(self, member_id: int, **fields: Any) -> None:
        allowed = {"role", "responsibility", "status", "input_tokens", "output_tokens"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        params += [member_id]
        await self.db.execute(f"UPDATE team_members SET {', '.join(sets)} WHERE id=?", params)

    async def add_tokens(self, member_id: int, in_tok: int, out_tok: int) -> None:
        await self.db.execute(
            "UPDATE team_members SET input_tokens=input_tokens+?,"
            " output_tokens=output_tokens+? WHERE id=?", (in_tok, out_tok, member_id))

    # shared board
    async def add_event(self, team_id: int, phase: str, kind: str, content: str,
                        actor: str | None = None) -> int:
        cur = await self.db.execute(
            "INSERT INTO team_events (team_id, phase, actor, kind, content, created_at)"
            " VALUES (?,?,?,?,?,?)", (team_id, phase, actor, kind, content, utcnow()))
        return int(cur.lastrowid or 0)

    async def events(self, team_id: int, limit: int = 400) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM team_events WHERE team_id=? ORDER BY id DESC LIMIT ?",
            (team_id, limit))

    async def add_task(self, team_id: int, title: str, description: str = "",
                       assignee: str | None = None,
                       dependencies: str = "") -> dict:
        cur = await self.db.execute(
            "INSERT INTO team_tasks (team_id, title, description, assignee, status,"
            " dependencies, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (team_id, title, description, assignee, "todo", dependencies, utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM team_tasks WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def tasks(self, team_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM team_tasks WHERE team_id=? ORDER BY id", (team_id,))

    async def update_task(self, task_id: int, **fields: Any) -> None:
        allowed = {"title", "description", "assignee", "status", "progress", "error"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        params += [utcnow(), task_id]
        await self.db.execute(f"UPDATE team_tasks SET {', '.join(sets)} WHERE id=?", params)

    async def token_totals(self, team_id: int) -> dict:
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o"
            " FROM team_members WHERE team_id=?", (team_id,))
        cost = await self.db.fetchone(
            "SELECT COALESCE(SUM(cost_eur),0.0) AS c FROM usage_events WHERE team_id=?",
            (team_id,))
        return {"input_tokens": row["i"], "output_tokens": row["o"],
                "total_tokens": row["i"] + row["o"],
                "cost_eur": float(cost["c"])}


# ─────────────────────────────── agent ───────────────────────────────
class AgentRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, task: str, project_id: int | None, workspace: str) -> dict:
        cur = await self.db.execute(
            "INSERT INTO agent_runs (project_id, task, workspace, status, stage,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (project_id, task, workspace, "pending", "plan", utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM agent_runs WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def get(self, run_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM agent_runs WHERE id=?", (run_id,))

    async def list(self, limit: int = 50) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,))

    async def update(self, run_id: int, **fields: Any) -> None:
        allowed = {"plan", "status", "stage", "summary", "error", "workspace",
                   "input_tokens", "output_tokens", "cost_eur"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        params += [utcnow(), run_id]
        await self.db.execute(f"UPDATE agent_runs SET {', '.join(sets)} WHERE id=?", params)

    async def add_tokens(self, run_id: int, in_tok: int, out_tok: int, cost: float) -> None:
        await self.db.execute(
            "UPDATE agent_runs SET input_tokens=input_tokens+?,"
            " output_tokens=output_tokens+?, cost_eur=cost_eur+?, updated_at=?"
            " WHERE id=?", (in_tok, out_tok, cost, utcnow(), run_id))

    async def add_step(self, run_id: int, seq: int, stage: str, tool: str | None,
                       target: str | None, summary: str, status: str,
                       detail: str = "", in_tok: int = 0,
                       out_tok: int = 0) -> dict:
        cur = await self.db.execute(
            "INSERT INTO agent_steps (run_id, seq, stage, tool, target, summary, status,"
            " detail, input_tokens, output_tokens, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, seq, stage, tool, target, summary, status, detail,
             in_tok, out_tok, utcnow()))
        return await self.db.fetchone("SELECT * FROM agent_steps WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def steps(self, run_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM agent_steps WHERE run_id=? ORDER BY seq", (run_id,))


# ────────────────────────────── research ─────────────────────────────
class ResearchRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, query: str, project_id: int | None = None) -> dict:
        cur = await self.db.execute(
            "INSERT INTO research (query, status, project_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?)", (query, "pending", project_id, utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM research WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def get(self, rid: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM research WHERE id=?", (rid,))

    async def list(self, limit: int = 50) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM research ORDER BY id DESC LIMIT ?", (limit,))

    async def finish(self, rid: int, *, status: str, result: str, sources_json: str,
                     notes: str = "", summary: str = "", comparison: str = "") -> None:
        await self.db.execute(
            "UPDATE research SET status=?, result=?, sources_json=?, notes=?,"
            " summary=?, comparison=?, updated_at=? WHERE id=?",
            (status, result, sources_json, notes, summary, comparison, utcnow(), rid))

    async def delete(self, rid: int) -> None:
        await self.db.execute("DELETE FROM research WHERE id=?", (rid,))


# ────────────────────────────── compare ──────────────────────────────
class CompareRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, prompt: str, project_id: int | None = None) -> dict:
        cur = await self.db.execute(
            "INSERT INTO compare_runs (prompt, project_id, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?)", (prompt, project_id, "pending", utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM compare_runs WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def get(self, run_id: int) -> dict | None:
        return await self.db.fetchone("SELECT * FROM compare_runs WHERE id=?", (run_id,))

    async def list(self, limit: int = 50) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM compare_runs ORDER BY id DESC LIMIT ?", (limit,))

    async def finish(self, run_id: int, *, status: str | None = None,
                     selected: str | None = None, combined: str = "") -> None:
        if status is None and selected is None and not combined:
            return
        await self.db.execute(
            "UPDATE compare_runs SET"
            " status=COALESCE(?, status),"
            " selected_model=COALESCE(?, selected_model),"
            " combined=COALESCE(?, combined), updated_at=? WHERE id=?",
            (status, selected, combined or None, utcnow(), run_id))

    async def add_answer(self, run_id: int, model: str, provider: str, answer: str,
                         in_tok: int, out_tok: int, method: str, cost: float,
                         status: str = "complete", error: str | None = None) -> dict:
        cur = await self.db.execute(
            "INSERT INTO compare_answers (run_id, model, provider, answer, input_tokens,"
            " output_tokens, token_method, cost_eur, status, error, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, model, provider, answer, in_tok, out_tok, method, cost,
             status, error, utcnow()))
        return await self.db.fetchone("SELECT * FROM compare_answers WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def answers(self, run_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM compare_answers WHERE run_id=? ORDER BY id", (run_id,))

    async def select(self, run_id: int, answer_id: int) -> None:
        await self.db.execute("UPDATE compare_answers SET selected=0 WHERE run_id=?",
                              (run_id,))
        await self.db.execute(
            "UPDATE compare_answers SET selected=1 WHERE run_id=? AND id=?",
            (run_id, answer_id))
