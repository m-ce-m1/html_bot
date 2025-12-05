from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd


def export_attempts_to_excel(data: Iterable[dict], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "full_name",
                "topic",
                "score",
                "max_score",
                "attempt_number",
                "timestamp",
            ]
        )
    df = df.rename(columns={"title": "topic"})
    df.to_excel(destination, index=False)
    return destination







