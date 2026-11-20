"""Tests for statistics calculations based on real database records."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core import statistics as stats
from app.core.database import DatabaseSession
from app.core.models import CheckHistory, Incident, MonitoredSite
from app.services import site_service


def _add_checks(site_id: int, n_ok: int, n_fail: int, base_ms: float) -> None:
    now = datetime.utcnow()
    with DatabaseSession() as db:
        # успешные: времена отклика 100,200,...,n_ok*100
        for i in range(n_ok):
            db.session.add(CheckHistory(
                site_id=site_id,
                checked_at=now - timedelta(minutes=n_ok - i),
                is_available=True,
                response_time=base_ms + (i + 1) * 100.0,
                http_status=200,
            ))
        for i in range(n_fail):
            db.session.add(CheckHistory(
                site_id=site_id,
                checked_at=now - timedelta(minutes=1),
                is_available=False,
                http_status=500,
                error_message="HTTP-ответ 500 (ошибка сервера)",
            ))


def test_statistics_metrics(clean_db):
    site_id, _ = site_service.add_site("Статс", "https://example.com/", 60)
    _add_checks(site_id, n_ok=3, n_fail=1, base_ms=0)
    # времена отклика: 100, 200, 300
    data = stats.get_site_statistics(site_id, "24h")
    assert data["checks_total"] == 4
    assert data["checks_ok"] == 3
    assert data["checks_fail"] == 1
    assert data["avg_response"] == 200.0
    assert data["max_response"] == 300.0
    assert data["min_response"] == 100.0
    assert data["availability_pct"] == 75.0


def test_statistics_series_shapes(clean_db):
    site_id, _ = site_service.add_site("Сериес", "https://example.com/", 60)
    _add_checks(site_id, n_ok=2, n_fail=0, base_ms=50)
    data = stats.get_site_statistics(site_id, "24h")
    series = data["series"]
    assert len(series["labels"]) == 24
    assert len(series["response"]) == 24
    assert len(series["availability"]) == 24
    # хотя бы в одном бакете есть данные
    assert any(v is not None for v in series["availability"])


def test_statistics_no_data(clean_db):
    data = stats.get_site_statistics(None, "24h")
    assert data["checks_total"] == 0
    assert data["avg_response"] is None
    assert data["availability_pct"] == 0


def test_get_overview_and_incidents(clean_db):
    site_id, _ = site_service.add_site("Ов", "https://example.com/", 60)
    _add_checks(site_id, n_ok=2, n_fail=1, base_ms=10)
    with DatabaseSession() as db:
        db.session.add(Incident(site_id=site_id, is_resolved=False))

    overview = stats.get_overview()
    assert overview["total"] == 1
    assert overview["available"] == 0  # last check was a failure
    assert overview["incidents"] == 1
    assert overview["open_incidents"] == 1
    assert len(overview["recent_events"]) >= 3

    incidents = stats.get_incidents()
    assert len(incidents) == 1
    assert incidents[0]["is_resolved"] is False

    history = stats.get_site_history(site_id, "30d", limit=10)
    assert len(history) == 3