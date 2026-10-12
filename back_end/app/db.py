from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    agent_id TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    agent_id TEXT,
                    step_id TEXT,
                    step_title TEXT,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_prompts (
                    agent_id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def create_session(self, kind: str, title: str, agent_id: str | None = None) -> dict:
        now = utcnow()
        row = {
            "id": new_id(),
            "kind": kind,
            "agent_id": agent_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (id, kind, agent_id, title, created_at, updated_at)
                VALUES (:id, :kind, :agent_id, :title, :created_at, :updated_at)
                """,
                row,
            )
            self._conn.commit()
        return row

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, kind: str | None = None, agent_id: str | None = None) -> list[dict]:
        clauses = []
        params: list[str] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM sessions {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def touch(self, session_id: str, title: str | None = None) -> None:
        with self._lock:
            if title is None:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (utcnow(), session_id),
                )
            else:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                    (utcnow(), title, session_id),
                )
            self._conn.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        agent_id: str | None = None,
        step_id: str | None = None,
        step_title: str | None = None,
    ) -> dict:
        row = {
            "id": new_id(),
            "session_id": session_id,
            "role": role,
            "agent_id": agent_id,
            "step_id": step_id,
            "step_title": step_title,
            "content": content,
            "created_at": utcnow(),
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO messages
                    (id, session_id, role, agent_id, step_id, step_title, content, created_at)
                VALUES
                    (:id, :session_id, :role, :agent_id, :step_id, :step_title, :content, :created_at)
                """,
                row,
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (row["created_at"], session_id),
            )
            self._conn.commit()
        return row

    def list_messages(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def agent_documents(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT agent_id, document FROM agent_prompts").fetchall()
        return {row["agent_id"]: row["document"] for row in rows}

    def save_agent_document(self, agent_id: str, document: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_prompts (agent_id, document, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    document = excluded.document,
                    updated_at = excluded.updated_at
                """,
                (agent_id, document, utcnow()),
            )
            self._conn.commit()

    def delete_agent_document(self, agent_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM agent_prompts WHERE agent_id = ?", (agent_id,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def title_from_message(message: str) -> str:
    line = " ".join(message.strip().split())
    if len(line) <= 36:
        return line or "Untitled task"
    return line[:36] + "…"
