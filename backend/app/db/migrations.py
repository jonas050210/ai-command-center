"""Migration-ready schema management.

Migrations are ordered, immutable (name, sql) tuples applied inside a
transaction and recorded in ``schema_migrations``. To evolve the schema,
append a new migration — never edit an old one.
"""
from __future__ import annotations

import logging

from .database import Database

log = logging.getLogger("aicc.db.migrations")

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    is_local INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    base_url TEXT,
    cost_input_per_mtok REAL NOT NULL DEFAULT 0.0,
    cost_output_per_mtok REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_local INTEGER NOT NULL DEFAULT 1,
    is_free INTEGER NOT NULL DEFAULT 1,
    cost_input_per_mtok REAL NOT NULL DEFAULT 0.0,
    cost_output_per_mtok REAL NOT NULL DEFAULT 0.0,
    context_length INTEGER,
    size_bytes INTEGER,
    parameter_size TEXT,
    quantization TEXT,
    family TEXT,
    families_json TEXT,
    capabilities_json TEXT,
    categories_json TEXT,
    available INTEGER NOT NULL DEFAULT 1,
    favorite INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'available',
    measured_tps REAL,
    measured_at TEXT,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    last_seen_at TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, name)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT,
    provider TEXT,
    system_prompt TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content TEXT NOT NULL DEFAULT '',
    model TEXT,
    provider TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    token_method TEXT NOT NULL DEFAULT 'estimated'
        CHECK(token_method IN ('exact','estimated')),
    status TEXT NOT NULL DEFAULT 'complete'
        CHECK(status IN ('pending','streaming','complete','stopped','error')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    message_id TEXT,
    team_id INTEGER,
    team_member_id INTEGER,
    model TEXT,
    provider TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'estimated' CHECK(method IN ('exact','estimated')),
    cost_eur REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_events(provider, model);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    root_path TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    team_id INTEGER,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',
    master_plan TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    provider TEXT,
    model TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    responsibility TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'idle'
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    size_bytes INTEGER,
    mime TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    command TEXT,
    actor TEXT NOT NULL DEFAULT 'user',
    exit_code INTEGER,
    log TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS research (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT NOT NULL DEFAULT '',
    sources_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL UNIQUE,
    ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','complete','error','stopped','denied')),
    result TEXT NOT NULL DEFAULT '',
    error TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    skills TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    step INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('model','tool_call','tool_result','approval','note')),
    content TEXT NOT NULL DEFAULT '',
    data_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps(run_id, id);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES agent_runs(id) ON DELETE CASCADE,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    preview TEXT,
    danger TEXT NOT NULL DEFAULT 'write',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','denied','expired')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id);
"""

SCHEMA_V3 = """
ALTER TABLE agent_runs ADD COLUMN project_id INTEGER REFERENCES projects(id)
    ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_id);
"""

SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS team_runs (
    id TEXT PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','complete','error','stopped','denied')),
    plan_text TEXT NOT NULL DEFAULT '',
    result_text TEXT NOT NULL DEFAULT '',
    review_text TEXT NOT NULL DEFAULT '',
    verdict TEXT CHECK(verdict IN ('accepted','changes_requested') OR verdict IS NULL),
    revision_used INTEGER NOT NULL DEFAULT 0,
    executor_run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_runs_team ON team_runs(team_id);
"""

SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial_schema", SCHEMA_V1),
    (2, "agent_mode", SCHEMA_V2),
    (3, "agent_run_projects", SCHEMA_V3),
    (4, "team_runs", SCHEMA_V4),
    (5, "memories", SCHEMA_V5),
]


async def migrate(db: Database) -> list[int]:
    """Apply pending migrations in order. Returns applied versions."""
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    applied: set[int] = {
        row["version"] for row in await db.fetchall("SELECT version FROM schema_migrations")
    }
    newly: list[int] = []
    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        log.info("applying migration %s: %s", version, name)
        async with db._lock:  # single transactional unit
            await db.conn.executescript(sql)
            await db.conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (version, name)
            )
            await db.conn.commit()
        newly.append(version)
    return newly
