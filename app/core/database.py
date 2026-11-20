"""Database setup for WebsiteMonitor.

The database file lives in a *permanent* user-level directory so that both the
web and the desktop editions share the exact same SQLite file and the data
survives restarts. When frozen with PyInstaller we never write into the
temporary extraction folder.

Locations:
    Windows : %LOCALAPPDATA%/WebsiteMonitor/data/monitor.db
    Linux   : ~/.local/share/WebsiteMonitor/data/monitor.db

The concrete location can be overridden for tests by setting the environment
variable WEBSITE_MONITOR_DB_PATH (an absolute path without the "sqlite:///"
prefix).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger("website_monitor.database")

# A dedicated lock protecting write transactions. SQLite only allows one writer
# at a time; we take the lock around logical write units so that both threads of
# one process and two running processes (web + desktop) reconcile cleanly. The
# WAL mode additionally lets readers coexist with a writer.
_write_lock = threading.RLock()


def get_data_dir() -> Path:
    """Return the permanent application data directory."""
    override = os.environ.get("WEBSITE_MONITOR_DB_PATH")
    if override:
        return Path(override).parent

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "WebsiteMonitor"
    # Linux / macOS
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "share"
    return base / "WebsiteMonitor"


def get_db_path() -> Path:
    """Return the full path to the SQLite database file."""
    override = os.environ.get("WEBSITE_MONITOR_DB_PATH")
    if override:
        return Path(override)
    return get_data_dir() / "data" / "monitor.db"


def _create_engine(db_path: Path | str) -> Engine:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    return engine


engine = _create_engine(get_db_path())
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    from . import models  # noqa: PLC0415

    models.Base.metadata.create_all(bind=engine)


class DatabaseSession:
    """Context manager exposing a bound session and the write lock."""

    def __init__(self) -> None:
        self.session = None

    def __enter__(self) -> "DatabaseSession":
        self._lock = _write_lock
        self._lock.acquire()
        self.session = SessionLocal()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            self.session.close()
            self._lock.release()
        return False


def get_session():
    """Open a write-locked session. Prefer DatabaseSession context manager."""
    return DatabaseSession()