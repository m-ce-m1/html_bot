from __future__ import annotations

from typing import Optional

from database.db import Database

_db_instance: Optional[Database] = None


def set_db_instance(db: Database) -> None:
    """Store database instance for global access."""
    global _db_instance
    _db_instance = db


def get_db_instance() -> Database:
    if _db_instance is None:
        raise RuntimeError("Database instance is not initialized")
    return _db_instance



