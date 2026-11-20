"""Integration tests for the background scheduler."""
from __future__ import annotations

import time

from app.core.database import DatabaseSession
from app.core.models import CheckHistory
from app.core.scheduler import MonitorScheduler
from app.services import site_service


def test_scheduler_checks_active_sites(http_server, clean_db):
    url = f"http://127.0.0.1:{http_server.server_port}/ok"
    site_id, _ = site_service.add_site("Автоматический", url, interval_seconds=5)

    scheduler = MonitorScheduler(max_workers=2)
    scheduler.start()
    try:
        deadline = time.time() + 15
        count = 0
        while time.time() < deadline:
            with DatabaseSession() as db:
                count = db.session.query(CheckHistory).count()
            if count >= 2:
                break
            time.sleep(0.5)
    finally:
        scheduler.stop()

    assert count >= 2
    with DatabaseSession() as db:
        records = db.session.query(CheckHistory).all()
        assert all(r.is_available for r in records)


def test_scheduler_skips_inactive_sites(http_server, clean_db):
    url = f"http://127.0.0.1:{http_server.server_port}/ok"
    site_id, _ = site_service.add_site("Неактивный", url, interval_seconds=5)
    site_service.set_active(site_id, False)

    scheduler = MonitorScheduler(max_workers=2)
    scheduler.start()
    try:
        time.sleep(3.5)
    finally:
        scheduler.stop()

    with DatabaseSession() as db:
        assert db.session.query(CheckHistory).count() == 0