"""Tests for the core HTTP monitoring engine."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from tests.conftest import get_closed_port

from app.core.database import DatabaseSession
from app.core.models import CheckHistory, Incident, MonitoredSite
from app.core.monitor import (
    check_site_now,
    check_url_allowed,
    classify_error,
    perform_check,
)
from app.services import site_service


def _site(site_id: int, url: str):
    return SimpleNamespace(id=site_id, url=url)


def test_perform_check_ok(http_server):
    result = perform_check(_site(1, f"http://127.0.0.1:{http_server.server_port}/ok"))
    assert result.is_available is True
    assert result.http_status == 200
    assert result.response_time_ms >= 0
    assert result.error is None


def test_perform_check_http_500_is_failure(http_server):
    result = perform_check(_site(1, f"http://127.0.0.1:{http_server.server_port}/fail"))
    assert result.is_available is False
    assert result.http_status == 500
    assert "500" in result.error


def test_perform_check_follows_redirect(http_server):
    result = perform_check(_site(1, f"http://127.0.0.1:{http_server.server_port}/redirect"))
    assert result.is_available is True
    assert result.http_status == 200


def test_perform_check_redirect_loop_fails(http_server):
    result = perform_check(
        _site(1, f"http://127.0.0.1:{http_server.server_port}/redirect-loop"))
    assert result.is_available is False
    assert result.error is not None


def test_perform_check_connection_refused():
    port = get_closed_port()
    result = perform_check(_site(1, f"http://127.0.0.1:{port}/"))
    assert result.is_available is False
    assert result.error is not None
    assert result.http_status is None


def test_perform_check_dns_error():
    result = perform_check(_site(1, "http://host.does-not-exist-xyz.invalid/"),
                           timeout=3)
    assert result.is_available is False
    assert "DNS" in (result.error or "")


def test_perform_check_timeout(http_server):
    url = f"http://127.0.0.1:{http_server.server_port}/sleep?s=2"
    start = time.perf_counter()
    result = perform_check(_site(1, url), timeout=0.5)
    elapsed = time.perf_counter() - start
    assert result.is_available is False
    assert "Таймаут" in (result.error or "")
    assert elapsed < 3


def test_ssrf_blocks_private_addresses(monkeypatch):
    monkeypatch.delenv("WEBSITE_MONITOR_ALLOW_PRIVATE", raising=False)
    assert not check_url_allowed("http://127.0.0.1/")[0]
    assert not check_url_allowed("http://localhost/")[0]
    assert not check_url_allowed("http://[::1]/")[0]
    assert not check_url_allowed("http://169.254.169.254/latest/meta-data/")[0]
    assert not check_url_allowed("http://192.168.1.10/")[0]
    assert not check_url_allowed("http://10.0.0.5/")[0]
    assert not check_url_allowed("file:///etc/passwd")[0]
    assert "SSRF" in check_url_allowed("http://127.0.0.1/")[1]


def test_ssrf_allow_private_override(monkeypatch):
    monkeypatch.setenv("WEBSITE_MONITOR_ALLOW_PRIVATE", "1")
    assert check_url_allowed("http://127.0.0.1/")[0]


def test_check_site_now_records_history_and_incident(http_server, clean_db):
    url_ok = f"http://127.0.0.1:{http_server.server_port}/ok"
    port = get_closed_port()
    url_down = f"http://127.0.0.1:{port}/"

    site_id, error = site_service.add_site("Тест", url_ok, 60)
    assert error == ""

    # 1) доступен — одна запись истории, инцидентов нет
    result, note = check_site_now(site_id)
    assert result is not None and result.is_available
    assert note is None
    with DatabaseSession() as db:
        assert db.session.query(CheckHistory).count() == 1
        assert db.session.query(Incident).count() == 0
        site = db.session.get(MonitoredSite, site_id)
        assert site.current_status == MonitoredSite.STATUS_AVAILABLE
        assert site.last_http_status == 200
        assert site.is_checking is False

    # 2) переводим сайт на недоступный адрес — создаётся инцидент
    site_service.update_site(site_id, "Тест", url_down, 60)
    result, note = check_site_now(site_id)
    assert result is not None and not result.is_available
    with DatabaseSession() as db:
        site = db.session.get(MonitoredSite, site_id)
        assert site.current_status == MonitoredSite.STATUS_UNAVAILABLE
        incidents = db.session.query(Incident).all()
        assert len(incidents) == 1
        assert incidents[0].is_resolved is False
        assert db.session.query(CheckHistory).count() == 2

    # 3) повторный сбой НЕ создаёт второй инцидент
    result, note = check_site_now(site_id)
    assert result is not None and not result.is_available
    with DatabaseSession() as db:
        assert db.session.query(Incident).count() == 1
        assert db.session.query(CheckHistory).count() == 3

    # 4) восстановление — инцидент закрывается, длительность учитывается
    site_service.update_site(site_id, "Тест", url_ok, 60)
    result, note = check_site_now(site_id)
    assert result is not None and result.is_available
    with DatabaseSession() as db:
        incident = db.session.query(Incident).one()
        assert incident.is_resolved is True
        assert incident.resolved_at is not None
        assert incident.duration_seconds is not None
        assert incident.duration_seconds >= 0
        site = db.session.get(MonitoredSite, site_id)
        assert site.current_status == MonitoredSite.STATUS_AVAILABLE


def test_no_double_check_for_one_site(http_server, clean_db):
    url = f"http://127.0.0.1:{http_server.server_port}/sleep?s=1.2"
    site_id, error = site_service.add_site("Медленный", url, 60)
    assert error == ""

    holder = {}

    def first_check():
        holder["r"] = check_site_now(site_id)

    thread = threading.Thread(target=first_check)
    thread.start()
    time.sleep(0.5)  # первый запрос ещё выполняется

    second = check_site_now(site_id)
    thread.join()

    assert second == (None, "already-checking")
    assert holder["r"][0] is not None  # первый завершился успешно

    with DatabaseSession() as db:
        assert db.session.query(CheckHistory).count() == 1


def test_classify_error_messages():
    import requests  # noqa: PLC0415

    assert "SSL" in classify_error(requests.exceptions.SSLError("x"))
    assert "Таймаут" in classify_error(requests.exceptions.Timeout("x"))
    assert "соединения" in classify_error(
        requests.exceptions.ConnectionError("Unable to connect"))
    assert "DNS" in classify_error(
        requests.exceptions.ConnectionError(
            "[Errno -2] Name or service not known"))