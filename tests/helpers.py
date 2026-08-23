"""Fixtures that keep public tests runnable without the original assessment pack."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED_DATABASE = ROOT / "seed" / "parcelpilot.db"


def prepare_database(destination: Path) -> None:
    if not SEED_DATABASE.exists():
        raise RuntimeError("The committed seed database is missing.")
    shutil.copy2(SEED_DATABASE, destination)
