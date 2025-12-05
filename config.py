from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


BASE_DIR = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "8146818066:AAGN7UqPcxNUE3SeNMoKuvvx9oXcNpL6F58")
    admin_ids: tuple[int, ...] = tuple(
        int(uid.strip())
        for uid in os.getenv("ADMIN_IDS", "1462995775").split(",")
        if uid.strip()
    )
    database_path: Path = BASE_DIR / "database.db"
    materials_dir: Path = BASE_DIR / "materials"
    stats_export_dir: Path = BASE_DIR / "exports"
    attempt_limit_per_topic: int | None = 1  # None -> unlimited attempts by default


settings = Settings()

# ensure folders exist during import
settings.materials_dir.mkdir(parents=True, exist_ok=True)
settings.stats_export_dir.mkdir(parents=True, exist_ok=True)







