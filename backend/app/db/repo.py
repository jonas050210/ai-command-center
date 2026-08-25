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
                     system_prompt: str | None) -> dict:
        cid = new_id()
        await self.db.execute(
            "INSERT INTO conversations (id, title, model, provider, system_prompt,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (cid, title, model, provider, system_prompt, utcnow(), utcnow()))
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
                   "pinned", "archived", "favorite"}
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


# ────────────────────── future-phase foundations ──────────────────────
class ProjectsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, name: str, description: str = "", root_path: str | None = None) -> dict:
        cur = await self.db.execute(
            "INSERT INTO projects (name, description, root_path, created_at, updated_at)"
            " VALUES (?,?,?,?,?)", (name, description, root_path, utcnow(), utcnow()))
        return await self.db.fetchone("SELECT * FROM projects WHERE id=?",
                                      (cur.lastrowid,))  # type: ignore[return-value]

    async def list(self) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM projects ORDER BY updated_at DESC")


class TeamsRepo:
    def __init__(self, db: Database):
        self.db = db

    async def list(self) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM teams ORDER BY created_at DESC")
