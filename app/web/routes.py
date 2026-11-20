"""HTTP routes for the web edition."""
from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, url_for

from ..core.statistics import (
    PERIOD_KEYS,
    get_incidents,
    get_overview,
    get_site_history,
    get_site_statistics,
    get_sites_list,
)
from ..services import check_service, site_service


def register_routes(app) -> None:  # noqa: ANN001
    @app.get("/")
    def dashboard():
        overview = get_overview()
        sites = get_sites_list()
        return render_template("dashboard.html", overview=overview, sites=sites,
                               title="Главная")

    @app.get("/sites")
    def sites_page():
        return render_template("sites.html", sites=get_sites_list(),
                               title="Мониторинг сайтов")

    @app.get("/history")
    def history_page():
        site_id = _int_arg("site_id")
        period = _period_arg()
        records = get_site_history(site_id, period, limit=300)
        return render_template(
            "history.html", sites=get_sites_list(), records=records,
            selected_site=site_id, period=period, title="История проверок",
        )

    @app.get("/incidents")
    def incidents_page():
        site_id = _int_arg("site_id")
        status = request.args.get("status", "all")
        only_open = status == "open"
        records = get_incidents(site_id, only_open, limit=300)
        return render_template(
            "incidents.html", sites=get_sites_list(), records=records,
            selected_site=site_id, status=status, title="История сбоев",
        )

    @app.get("/statistics")
    def statistics_page():
        site_id = _int_arg("site_id")
        period = _period_arg()
        stats = get_site_statistics(site_id, period)
        return render_template(
            "statistics.html", sites=get_sites_list(), stats=stats,
            selected_site=site_id, period=period, title="Статистика",
        )

    # ---- site management actions ------------------------------------------

    @app.post("/sites/add")
    def sites_add():
        name = request.form.get("name", "")
        url = request.form.get("url", "")
        interval = _int_form("interval_seconds", 60)
        site_id, error = site_service.add_site(name, url, interval)
        if error:
            flash(error, "error")
        else:
            flash(f"Сайт «{name.strip()}» добавлен", "success")
        return redirect(url_for("sites_page"))

    @app.post("/sites/<int:site_id>/edit")
    def sites_edit(site_id):
        name = request.form.get("name", "")
        url = request.form.get("url", "")
        interval = _int_form("interval_seconds", 60)
        ok, error = site_service.update_site(site_id, name, url, interval)
        if ok:
            flash("Изменения сохранены", "success")
        else:
            flash(error or "Не удалось сохранить", "error")
        return redirect(url_for("sites_page"))

    @app.post("/sites/<int:site_id>/toggle")
    def sites_toggle(site_id):
        # Поле "active" несёт целевое состояние: включить (1) или выключить (0).
        site_service.set_active(site_id, request.form.get("active") == "1")
        return redirect(url_for("sites_page"))

    @app.post("/sites/<int:site_id>/delete")
    def sites_delete(site_id):
        site_service.delete_site(site_id)
        flash("Сайт удалён вместе с историей", "info")
        return redirect(url_for("sites_page"))

    @app.post("/sites/<int:site_id>/check")
    def sites_check(site_id):
        check_service.run_check_async(site_id)
        flash("Проверка запущена. Результат появится через несколько секунд.",
              "info")
        return redirect(url_for("sites_page"))

    @app.post("/sites/<int:site_id>/history/clear")
    def sites_history_clear(site_id):
        removed = site_service.purge_history(site_id)
        flash(f"История очищена (удалено записей: {removed})", "info")
        return redirect(request.referrer or url_for("history_page"))

    # ---- JSON API (used for auto-refresh and charts) -----------------------

    @app.get("/api/overview")
    def api_overview():
        return jsonify(get_overview())

    @app.get("/api/sites")
    def api_sites():
        return jsonify(get_sites_list())

    @app.get("/api/statistics")
    def api_statistics():
        site_id = _int_arg("site_id")
        period = _period_arg()
        return jsonify(get_site_statistics(site_id, period))


def _int_arg(name: str) -> int | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _int_form(name: str, default: int) -> int:
    raw = request.form.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value


def _period_arg() -> str:
    period = request.args.get("period", "24h")
    return period if period in PERIOD_KEYS else "24h"