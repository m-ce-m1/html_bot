from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterable, Sequence

import aiosqlite


QuestionPayload = dict[str, str | int]


@dataclass(slots=True)
class TopicFilters:
    topic_id: int | None = None
    user_id: int | None = None
    date_from: str | None = None  # ISO format
    date_to: str | None = None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def setup(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA foreign_keys = ON;")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'student'
                );

                CREATE TABLE IF NOT EXISTS topics (
                    topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL UNIQUE,
                    is_available INTEGER NOT NULL DEFAULT 0,
                    attempt_limit INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS questions (
                    question_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    option1 TEXT NOT NULL,
                    option2 TEXT NOT NULL,
                    option3 TEXT NOT NULL,
                    option4 TEXT NOT NULL,
                    correct_option INTEGER NOT NULL CHECK (correct_option BETWEEN 1 AND 4),
                    FOREIGN KEY (topic_id) REFERENCES topics (topic_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    score INTEGER NOT NULL,
                    max_score INTEGER NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (topic_id) REFERENCES topics (topic_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS materials (
                    material_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER,
                    type TEXT NOT NULL CHECK (type IN ('link', 'file', 'text')),
                    content TEXT NOT NULL,
                    title TEXT NOT NULL,
                    FOREIGN KEY (topic_id) REFERENCES topics (topic_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    is_answered INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (to_user_id) REFERENCES users (user_id) ON DELETE CASCADE
                );
                """
            )
            await db.commit()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON;")
            yield db

    async def upsert_user(self, user_id: int, full_name: str, role: str = "student") -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO users (user_id, full_name, role)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, role=excluded.role;
                """,
                (user_id, full_name, role),
            )
            await db.commit()

    async def get_user(self, user_id: int) -> dict | None:
        async with self._connect() as db:
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?;", (user_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def add_topic(self, title: str, attempt_limit: int | None = 1) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO topics (title, is_available, attempt_limit) VALUES (?, 0, ?);",
                (title.strip(), attempt_limit),
            )
            await db.commit()
            return cursor.lastrowid

    async def list_topics(self, include_hidden: bool = False) -> list[dict]:
        query = "SELECT * FROM topics"
        if not include_hidden:
            query += " WHERE is_available = 1"
        query += " ORDER BY title;"
        async with self._connect() as db:
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_topic(self, topic_id: int) -> dict | None:
        async with self._connect() as db:
            cursor = await db.execute("SELECT * FROM topics WHERE topic_id = ?;", (topic_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_topic_availability(self, topic_id: int, is_available: bool) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE topics SET is_available = ? WHERE topic_id = ?;",
                (1 if is_available else 0, topic_id),
            )
            await db.commit()

    async def set_topic_attempt_limit(self, topic_id: int, attempt_limit: int | None) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE topics SET attempt_limit = ? WHERE topic_id = ?;",
                (attempt_limit, topic_id),
            )
            await db.commit()

    async def add_questions(self, topic_id: int, questions: Sequence[QuestionPayload]) -> int:
        async with self._connect() as db:
            await db.executemany(
                """
                INSERT INTO questions (topic_id, text, option1, option2, option3, option4, correct_option)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                [
                    (
                        topic_id,
                        q["text"],
                        q["option1"],
                        q["option2"],
                        q["option3"],
                        q["option4"],
                        q["correct_option"],
                    )
                    for q in questions
                ],
            )
            await db.commit()
            return len(questions)

    async def fetch_random_questions(self, topic_id: int, limit: int = 10) -> list[dict]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT * FROM questions
                WHERE topic_id = ?
                ORDER BY RANDOM()
                LIMIT ?;
                """,
                (topic_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_attempt_count(self, user_id: int, topic_id: int) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM attempts WHERE user_id=? AND topic_id=?;",
                (user_id, topic_id),
            )
            row = await cursor.fetchone()
            return int(row["cnt"])

    async def save_attempt(
        self,
        user_id: int,
        topic_id: int,
        score: int,
        max_score: int,
        attempt_number: int | None = None,
    ) -> None:
        async with self._connect() as db:
            if attempt_number is None:
                cursor = await db.execute(
                    "SELECT COUNT(*) as cnt FROM attempts WHERE user_id=? AND topic_id=?;",
                    (user_id, topic_id),
                )
                row = await cursor.fetchone()
                attempt_number = int(row["cnt"]) + 1
            await db.execute(
                """
                INSERT INTO attempts (user_id, topic_id, score, max_score, attempt_number)
                VALUES (?, ?, ?, ?, ?);
                """,
                (user_id, topic_id, score, max_score, attempt_number),
            )
            await db.commit()

    async def get_attempts_by_user(self, user_id: int) -> list[dict]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT a.*, t.title
                FROM attempts a
                JOIN topics t ON a.topic_id = t.topic_id
                WHERE a.user_id = ?
                ORDER BY timestamp DESC;
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_statistics(self, filters: TopicFilters) -> list[dict]:
        query = """
            SELECT a.*, u.full_name, t.title
            FROM attempts a
            JOIN users u ON u.user_id = a.user_id
            JOIN topics t ON t.topic_id = a.topic_id
            WHERE 1=1
        """
        params: list = []
        if filters.topic_id:
            query += " AND a.topic_id = ?"
            params.append(filters.topic_id)
        if filters.user_id:
            query += " AND a.user_id = ?"
            params.append(filters.user_id)
        if filters.date_from:
            query += " AND a.timestamp >= ?"
            params.append(filters.date_from)
        if filters.date_to:
            query += " AND a.timestamp <= ?"
            params.append(filters.date_to)
        query += " ORDER BY a.timestamp DESC;"

        async with self._connect() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def record_message(
        self, from_user_id: int, to_user_id: int, text: str, is_answered: bool = False
    ) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO messages (from_user_id, to_user_id, text, is_answered)
                VALUES (?, ?, ?, ?);
                """,
                (from_user_id, to_user_id, text, 1 if is_answered else 0),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_open_questions(self) -> list[dict]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT m.*, u.full_name
                FROM messages m
                JOIN users u ON u.user_id = m.from_user_id
                WHERE is_answered = 0
                ORDER BY timestamp ASC;
                """
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def mark_message_answered(self, message_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE messages SET is_answered = 1 WHERE message_id = ?;", (message_id,)
            )
            await db.commit()

    async def get_message(self, message_id: int) -> dict | None:
        async with self._connect() as db:
            cursor = await db.execute("SELECT * FROM messages WHERE message_id = ?;", (message_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def add_material(
        self, title: str, content: str, material_type: str, topic_id: int | None = None
    ) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO materials (topic_id, type, content, title)
                VALUES (?, ?, ?, ?);
                """,
                (topic_id, material_type, content, title),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_materials(
        self, topic_id: int | None = None, include_general: bool = True
    ) -> list[dict]:
        async with self._connect() as db:
            if topic_id is not None:
                query = """
                    SELECT * FROM materials
                    WHERE topic_id = ?
                """
                params: list = [topic_id]
                if include_general:
                    query += " OR topic_id IS NULL"
                query += " ORDER BY (topic_id IS NULL), material_id DESC;"
                cursor = await db.execute(query, params)
            else:
                query = "SELECT * FROM materials"
                if not include_general:
                    query += " WHERE topic_id IS NULL"
                query += " ORDER BY (topic_id IS NULL), material_id DESC;"
                cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_material(self, material_id: int) -> dict | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM materials WHERE material_id = ?;", (material_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_material(self, material_id: int) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM materials WHERE material_id = ?;", (material_id,))
            await db.commit()

    async def backup_file(self) -> Path:
        backup_path = self.path.with_suffix(".backup.db")
        from shutil import copy2

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: copy2(self.path, backup_path))
        return backup_path

    async def list_users(self, role: str | None = None) -> list[dict]:
        query = "SELECT * FROM users"
        params: list = []
        if role:
            query += " WHERE role = ?"
            params.append(role)
        query += " ORDER BY full_name;"
        async with self._connect() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

