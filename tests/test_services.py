"""Tests for the site/check services and the SQLite schema."""
from __future__ import annotations

from sqlalchemy import text

from app.core import statistics as stats
from app.core.database import DatabaseSession, engine, get_data_dir, get_db_path
from app.core.models import CheckHistory, Incident, MonitoredSite
from app.services import site_service


def test_database_is_created_and_has_schema():
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    assert {"monitored_site", "check_history", "incident"} <= tables


def test_get_db_path_uses_env_override():
    import os  # noqa: PLC0415

    db_path = get_db_path()
    assert db_path.name == "monitor.db"
    assert get_data_dir().exists()


def test_add_site_validation():
    assert site_service.validate_site_data("", "https://x.com", 60)[0] is False
    assert site_service.validate_site_data("Имя", "not-a-url", 60)[0] is False
    assert site_service.validate_site_data("Имя", "ftp://x.com", 60)[0] is False
    assert site_service.validate_site_data("Имя", "https://x.com", 2)[0] is False
    assert site_service.validate_site_data("Имя", "https://x.com", 60)[0] is True


def test_crud_cycle(clean_db):
    # добавление
    site_id, error = site_service.add_site("Яндекс", "https://ya.ru/", 30)
    assert error == ""
    with DatabaseSession() as db:
        site = db.session.get(MonitoredSite, site_id)
        assert site.name == "Яндекс"
        assert site.interval_seconds == 30
        assert site.is_active is True
        assert site.current_status == MonitoredSite.STATUS_UNKNOWN

    # добавление с ошибкой
    bad_id, error = site_service.add_site("", "https://x.ru/", 30)
    assert bad_id is None and error

    # поиск
    sites = {s["name"]: s for s in stats.get_sites_list()}
    assert "Яндекс" in sites

    # редактирование
    ok, error = site_service.update_site(site_id, "Яндекс 2", "https://ya.ru/new", 90)
    assert ok and not error
    with DatabaseSession() as db:
        site = db.session.get(MonitoredSite, site_id)
        assert site.name == "Яндекс 2"
        assert site.url == "https://ya.ru/new"
        assert site.interval_seconds == 90

    # включение/выключение
    assert site_service.set_active(site_id, False)
    with DatabaseSession() as db:
        assert db.session.get(MonitoredSite, site_id).is_active is False

    # удаление
    assert site_service.delete_site(site_id)
    assert site_id not in {s["id"] for s in stats.get_sites_list()}


def test_delete_removes_history_and_incidents(clean_db):
    site_id, _ = site_service.add_site("Сайт", "https://example.com/", 60)
    with DatabaseSession() as db:
        db.session.add(CheckHistory(site_id=site_id, is_available=True,
                                    response_time=12.3, http_status=200))
        db.session.add(Incident(site_id=site_id, is_resolved=False))
    site_service.delete_site(site_id)
    with DatabaseSession() as db:
        assert db.session.query(CheckHistory).count() == 0
        assert db.session.query(Incident).count() == 0
        assert db.session.get(MonitoredSite, site_id) is None


def test_history_limit_enforcement(clean_db):
    site_id, _ = site_service.add_site("Сайт", "https://example.com/", 60)
    with DatabaseSession() as db:
        for i in range(20):
            db.session.add(CheckHistory(site_id=site_id, is_available=True,
                                        response_time=float(i)))
    removed = site_service.enforce_history_limit(site_id, limit=10)
    assert removed == 10
    with DatabaseSession() as db:
        assert db.session.query(CheckHistory).count() == 10


def test_clear_history_removes_rows(clean_db):
    site_id, _ = site_service.add_site("Сайт", "https://example.com/", 60)
    with DatabaseSession() as db:
        for i in range(3):
            db.session.add(CheckHistory(site_id=site_id, is_available=False,
                                        error_message="ошибка"))
    assert site_service.purge_history(site_id) == 3
    with DatabaseSession() as db:
        assert db.session.query(CheckHistory).count() == 0