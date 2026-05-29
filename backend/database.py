from __future__ import annotations

import aiosqlite
from pathlib import Path
from datetime import datetime
from typing import Optional

from backend.utils import get_logger

logger = get_logger("database")

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "chat_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    raw_content TEXT,
    vision_content TEXT,
    qq_id TEXT,
    character TEXT,
    is_bot INTEGER DEFAULT 0,
    sender_name TEXT,
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_key, id);

CREATE TABLE IF NOT EXISTS compression_markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    marker_message_id INTEGER NOT NULL,
    compressed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compression_session ON compression_markers(session_key, id);
"""


class ChatDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info(f"Database initialized at {self._db_path}")

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def save_message(
        self,
        session_key: str,
        role: str,
        content: str,
        raw_content: str = None,
        vision_content: str = None,
        qq_id: str = None,
        character: str = None,
        is_bot: bool = False,
        sender_name: str = None,
        timestamp: float = None,
    ) -> int:
        if not self._db:
            raise RuntimeError("Database not initialized")

        if timestamp is None:
            timestamp = datetime.now().timestamp()

        cursor = await self._db.execute(
            """INSERT INTO messages
               (session_key, role, content, raw_content, vision_content, qq_id, character, is_bot, sender_name, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_key, role, content, raw_content, vision_content, qq_id, character,
             1 if is_bot else 0, sender_name, timestamp),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def load_messages(self, session_key: str) -> list[dict]:
        if not self._db:
            raise RuntimeError("Database not initialized")

        marker_id = await self.get_latest_compression_marker(session_key)

        if marker_id is not None:
            cursor = await self._db.execute(
                """SELECT id, session_key, role, content, raw_content, vision_content, qq_id,
                          character, is_bot, sender_name, timestamp
                   FROM messages WHERE session_key = ? AND id > ? ORDER BY id ASC""",
                (session_key, marker_id),
            )
        else:
            cursor = await self._db.execute(
                """SELECT id, session_key, role, content, raw_content, vision_content, qq_id,
                          character, is_bot, sender_name, timestamp
                   FROM messages WHERE session_key = ? ORDER BY id ASC""",
                (session_key,),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_messages_by_ids(self, ids: list[int]):
        if not self._db or not ids:
            return

        placeholders = ",".join("?" * len(ids))
        await self._db.execute(
            f"DELETE FROM messages WHERE id IN ({placeholders})", ids
        )
        await self._db.commit()

    async def clear_session(self, session_key: str):
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute(
            "DELETE FROM messages WHERE session_key = ?", (session_key,)
        )
        await self._db.commit()

    async def save_compression_marker(self, session_key: str, marker_message_id: int) -> int:
        if not self._db:
            raise RuntimeError("Database not initialized")

        timestamp = datetime.now().timestamp()
        cursor = await self._db.execute(
            "INSERT INTO compression_markers (session_key, marker_message_id, compressed_at) VALUES (?, ?, ?)",
            (session_key, marker_message_id, timestamp),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_latest_compression_marker(self, session_key: str) -> Optional[int]:
        if not self._db:
            return None

        cursor = await self._db.execute(
            "SELECT marker_message_id FROM compression_markers WHERE session_key = ? ORDER BY id DESC LIMIT 1",
            (session_key,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


_db = None


def init_db():
    global _db
    if _db is None:
        _db = ChatDatabase()
    return _db


def __getattr__(name):
    if name == "db":
        if _db is None:
            return init_db()
        return _db
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
