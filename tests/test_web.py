"""Tests for the Flask web edition (routes, forms and JSON API)."""
from __future__ import annotations

import time

import pytest

from app.core.database import DatabaseSession
from app.core import statistics as stats
from app.core.models import CheckHistory
from app.services import site_service
from app.web import create_app


@pytest.fixture()
def client(clean_db):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_pages_render(client):
    for path in ("/", "/sites", "/history", "/incidents", "/statistics",
                 "/api/overview", "/api/sites", "/api/statistics"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_add_edit_toggle_delete_site(client):
    # добавление
    response = client.post("/sites/add", data={
        "name": "Тестовый сайт",
        "url": "https://example.com/",
        "interval_seconds": "30",
    }, follow_redirects=True)
    assert response.status_code == 200
    sites = stats.get_sites_list()
    assert len(sites) == 1
    site_id = sites[0]["id"]

    # некорректная добавка отклоняется
    response = client.post("/sites/add", data={
        "name": "", "url": "broken", "interval_seconds": "30",
    }, follow_redirects=True)
    assert len(stats.get_sites_list()) == 1

    # редактирование
    response = client.post(f"/sites/{site_id}/edit", data={
        "name": "Новое имя", "url": "https://example.com/v2",
        "interval_seconds": "60",
    }, follow_redirects=True)
    assert response.status_code == 200
    sites = stats.get_sites_list()
    assert sites[0]["name"] == "Новое имя"
    assert sites[0]["url"] == "https://example.com/v2"
    assert sites[0]["interval_seconds"] == 60

    # выключить мониторинг (active=0)
    client.post(f"/sites/{site_id}/toggle", data={"active": "0"},
                follow_redirects=True)
    assert stats.get_sites_list()[0]["is_active"] is False
    # включить обратно
    client.post(f"/sites/{site_id}/toggle", data={"active": "1"},
                follow_redirects=True)
    assert stats.get_sites_list()[0]["is_active"] is True

    # удаление
    client.post(f"/sites/{site_id}/delete", follow_redirects=True)
    assert stats.get_sites_list() == []


def test_manual_check_endpoint(client, http_server):
    url = f"http://127.0.0.1:{http_server.server_port}/ok"
    site_id, _ = site_service.add_site("Локальный", url, 60)
    client.post(f"/sites/{site_id}/check", follow_redirects=True)
    deadline = time.time() + 5
    count = 0
    while time.time() < deadline:
        with DatabaseSession() as db:
            count = db.session.query(CheckHistory).count()
        if count >= 1:
            break
        time.sleep(0.2)
    assert count >= 1

    history_response = client.get(f"/history?site_id={site_id}&period=24h")
    assert history_response.status_code == 200


def test_history_clear(client):
    site_id, _ = site_service.add_site("Очистка", "https://example.com/", 60)
    with DatabaseSession() as db:
        db.session.add(CheckHistory(site_id=site_id, is_available=True,
                                    response_time=5.0, http_status=200))
    client.post(f"/sites/{site_id}/history/clear", follow_redirects=True)
    with DatabaseSession() as db:
        assert db.session.query(CheckHistory).count() == 0


def test_api_statistics_shapes(client, http_server):
    from datetime import datetime  # noqa: PLC0415

    site_id, _ = site_service.add_site("Апишек", "https://example.com/", 60)
    with DatabaseSession() as db:
        db.session.add(CheckHistory(site_id=site_id, checked_at=datetime.utcnow(),
                                    is_available=True, response_time=42.0,
                                    http_status=200))
    response = client.get(f"/api/statistics?site_id={site_id}&period=24h")
    assert response.status_code == 200
    data = response.get_json()
    assert data["site_id"] == site_id
    assert data["period"] == "24h"
    assert set(data["series"]) == {"labels", "response", "availability"}
    assert len(data["series"]["labels"]) == 24