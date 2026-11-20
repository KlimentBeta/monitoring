"""Shared test fixtures.

Important: environment variables are configured BEFORE any ``app.*`` module is
imported, because the database engine is bound to a path at import time.
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("WEBSITE_MONITOR_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="wm_test_"),
                                   "monitor.db"))
os.environ.setdefault("WEBSITE_MONITOR_ALLOW_PRIVATE", "1")

# allow `import app` to work regardless of where pytest is invoked from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app.core import database  # noqa: E402
from app.core.database import DatabaseSession  # noqa: E402
from app.core.models import CheckHistory, Incident, MonitoredSite  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    database.init_db()
    yield


@pytest.fixture()
def clean_db():
    """Reset all tables before a test."""
    with DatabaseSession() as db:
        db.session.query(Incident).delete()
        db.session.query(CheckHistory).delete()
        db.session.query(MonitoredSite).delete()
    yield
    with DatabaseSession() as db:
        db.session.query(Incident).delete()
        db.session.query(CheckHistory).delete()
        db.session.query(MonitoredSite).delete()


def get_closed_port() -> int:
    """Return a TCP port that currently accepts no connections."""
    import socket  # noqa: PLC0415

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# register the session-scoped HTTP server fixture defined alongside the helper
from tests.test_server import http_server  # noqa: E402,F401