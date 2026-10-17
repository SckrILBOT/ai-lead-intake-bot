from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'model')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages (user_id, id);
CREATE TABLE IF NOT EXISTS sessions (
    user_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path, history_limit: int = 15) -> None:
        self._path = path
        self._limit = history_limit
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database is not connected"
        return self._conn

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        await self.conn.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        # keep only the last N messages per user
        await self.conn.execute(
            "DELETE FROM messages WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, self._limit),
        )
        await self.conn.commit()

    async def get_history(self, user_id: int) -> list[tuple[str, str]]:
        """Return up to N last messages as (role, content), oldest first.

        Gemini requires the history to start with a user turn, so a leading
        model message (left after trimming) is dropped.
        """
        async with self.conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, self._limit),
        ) as cur:
            rows = await cur.fetchall()
        history = [(r[0], r[1]) for r in reversed(rows)]
        while history and history[0][0] != "user":
            history.pop(0)
        return history

    async def get_status(self, user_id: int) -> str:
        async with self.conn.execute(
            "SELECT status FROM sessions WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else "active"

    async def set_status(self, user_id: int, status: str) -> None:
        await self.conn.execute(
            "INSERT INTO sessions (user_id, status, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET status = excluded.status, "
            "updated_at = CURRENT_TIMESTAMP",
            (user_id, status),
        )
        await self.conn.commit()

    async def reset(self, user_id: int) -> None:
        """Clear the dialog and start a fresh active session."""
        await self.conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await self.conn.commit()
