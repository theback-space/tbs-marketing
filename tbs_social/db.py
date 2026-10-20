"""SQLite storage: posts, metrics snapshots, inbound interactions, outbound actions, audit log."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    body TEXT NOT NULL,
    image_url TEXT,
    pillar TEXT,
    image_idea TEXT,
    status TEXT NOT NULL DEFAULT 'draft',      -- draft | approved | published | failed | rejected
    scheduled_for TEXT,                          -- ISO-8601 UTC; NULL = publish on next cycle once approved
    external_id TEXT,
    compliance_notes TEXT,                       -- JSON list of issues found by the checker
    error TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    scope TEXT NOT NULL,                         -- 'account' or 'post'
    external_id TEXT NOT NULL,
    data TEXT NOT NULL,                          -- JSON {metric: value}
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    kind TEXT NOT NULL,                          -- comment | review | mention | message
    post_external_id TEXT,
    author TEXT,
    text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    sentiment TEXT,
    intent TEXT,
    phi_risk INTEGER NOT NULL DEFAULT 0,
    needs_human INTEGER NOT NULL DEFAULT 1,
    triage_reason TEXT,
    draft_reply TEXT,
    status TEXT NOT NULL DEFAULT 'new',          -- new | drafted | replied | dismissed
    liked INTEGER NOT NULL DEFAULT 0,
    UNIQUE(platform, external_id)
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    kind TEXT NOT NULL,                          -- like | reply | publish | follow_suggestion
    target TEXT NOT NULL,
    detail TEXT,
    automated INTEGER NOT NULL,
    outcome TEXT NOT NULL,                       -- ok | error | suggested
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- generic helpers -------------------------------------------------
    def _insert(self, table: str, row: dict[str, Any]) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
        self.conn.commit()
        return cur.lastrowid

    def _update(self, table: str, row_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*fields.values(), row_id])
        self.conn.commit()

    # --- posts -----------------------------------------------------------
    def add_post(self, platform: str, body: str, *, pillar: str | None = None,
                 image_idea: str | None = None, image_url: str | None = None,
                 scheduled_for: str | None = None,
                 compliance_notes: list[str] | None = None) -> int:
        return self._insert("posts", {
            "platform": platform, "body": body, "pillar": pillar, "image_idea": image_idea,
            "image_url": image_url,
            "scheduled_for": scheduled_for, "status": "draft",
            "compliance_notes": json.dumps(compliance_notes or []), "created_at": now(),
        })

    def update_post(self, post_id: int, **fields: Any) -> None:
        self._update("posts", post_id, **fields)

    def get_post(self, post_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()

    def list_posts(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute(
                "SELECT * FROM posts WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
        return self.conn.execute("SELECT * FROM posts ORDER BY id DESC").fetchall()

    def due_posts(self, at: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM posts WHERE status = 'approved' "
            "AND (scheduled_for IS NULL OR scheduled_for <= ?) ORDER BY id", (at,)).fetchall()

    # --- metrics ---------------------------------------------------------
    def add_metrics(self, platform: str, scope: str, external_id: str, data: dict[str, Any]) -> None:
        self._insert("metrics", {
            "platform": platform, "scope": scope, "external_id": external_id,
            "data": json.dumps(data), "captured_at": now(),
        })

    def latest_metrics(self, scope: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT m.* FROM metrics m JOIN ("
            "  SELECT platform, external_id, MAX(id) AS id FROM metrics WHERE scope = ?"
            "  GROUP BY platform, external_id) latest ON m.id = latest.id ORDER BY m.platform",
            (scope,)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def metric_history(self, platform: str, scope: str, external_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM metrics WHERE platform = ? AND scope = ? AND external_id = ? "
            "ORDER BY id DESC LIMIT ?", (platform, scope, external_id, limit)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in reversed(rows)]

    # --- interactions ----------------------------------------------------
    def upsert_interaction(self, platform: str, external_id: str, kind: str, text: str, *,
                           author: str | None, post_external_id: str | None, received_at: str) -> int | None:
        """Insert a new interaction; returns its id, or None if we've already seen it."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO interactions "
            "(platform, external_id, kind, post_external_id, author, text, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (platform, external_id, kind, post_external_id, author, text, received_at))
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def update_interaction(self, interaction_id: int, **fields: Any) -> None:
        self._update("interactions", interaction_id, **fields)

    def get_interaction(self, interaction_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM interactions WHERE id = ?", (interaction_id,)).fetchone()

    def list_interactions(self, statuses: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        if statuses:
            marks = ", ".join("?" for _ in statuses)
            return self.conn.execute(
                f"SELECT * FROM interactions WHERE status IN ({marks}) "
                "ORDER BY phi_risk DESC, needs_human DESC, id DESC", statuses).fetchall()
        return self.conn.execute("SELECT * FROM interactions ORDER BY id DESC").fetchall()

    # --- actions / audit -------------------------------------------------
    def log_action(self, platform: str, kind: str, target: str, *, detail: str = "",
                   automated: bool, outcome: str) -> None:
        self._insert("actions", {
            "platform": platform, "kind": kind, "target": target, "detail": detail,
            "automated": int(automated), "outcome": outcome, "created_at": now(),
        })

    def list_actions(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
