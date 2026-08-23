"""Runnable application entrypoint for local development and container hosting."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from parcelpilot.api import DEVELOPMENT_SESSION_SECRET, create_app
from parcelpilot.ingestion import ingest_all


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_DATABASE = PROJECT_ROOT / "seed" / "parcelpilot.db"


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


def _database_is_current(database_path: Path) -> bool:
    """Detect an older local demo DB and upgrade it through deterministic ingestion."""
    if not database_path.exists():
        return False
    try:
        with sqlite3.connect(database_path) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        return {"accounts", "document_sources", "action_proposals", "audit_events"}.issubset(names)
    except sqlite3.DatabaseError:
        return False


def create_application():
    """Build the app and seed the immutable assessment database if needed."""
    environment = os.getenv("PARCELPILOT_ENV", "development").strip().lower()
    database_path = Path(os.getenv("PARCELPILOT_DATABASE_PATH", str(PROJECT_ROOT / "data" / "parcelpilot.db")))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if not _database_is_current(database_path):
        if SEED_DATABASE.exists():
            shutil.copy2(SEED_DATABASE, database_path)
        else:
            # This path is useful only to assessment authors with the original
            # pack. Published/deployed builds use the committed seed database.
            ingest_all(PROJECT_ROOT, database_path)

    session_secret = os.getenv("SESSION_SECRET")
    if not session_secret:
        if environment == "production":
            raise RuntimeError("SESSION_SECRET must be set when PARCELPILOT_ENV=production")
        session_secret = DEVELOPMENT_SESSION_SECRET
    return create_app(
        database_path,
        session_secret=session_secret,
        secure_cookies=_flag("PARCELPILOT_SECURE_COOKIES", environment == "production"),
    )


app = create_application()
