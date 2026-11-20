"""Flask web application for WebsiteMonitor."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Flask, render_template

from ..core.database import init_db

SECRET_KEY = "website-monitor-local-only"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("website_monitor.web")


def create_app() -> Flask:
    """Application factory used both by ``web_main.py`` and the tests."""
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SECRET_KEY,
        JSON_AS_ASCII=False,
    )
    init_db()

    register_template_filters(app)
    register_routes(app)

    @app.errorhandler(404)
    def not_found(_error):  # noqa: ANN001
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):  # noqa: ANN001
        logger.exception("Unhandled server error")
        return render_template("500.html"), 500

    return app


def register_template_filters(app: Flask) -> None:
    @app.template_filter("dt")
    def _dt(value):
        """Format an UTC naive datetime as local time -> '31.12.2025 23:59:59'."""
        if value is None:
            return "—"
        try:
            local = value.replace(tzinfo=timezone.utc).astimezone()
            return local.strftime("%d.%m.%Y %H:%M:%S")
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter("ms")
    def _ms(value):
        """Format response time in milliseconds as a readable string."""
        if value is None:
            return "—"
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "—"
        if value >= 1000:
            return f"{value / 1000:.2f} с"
        return f"{value:.1f} мс"

    @app.template_filter("dur")
    def _duration(seconds):
        """Format a duration in seconds as 'Xд Yч Zм' / 'N с'."""
        if seconds is None:
            return "—"
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return "—"
        if seconds < 60:
            return f"{seconds} с"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes} мин {sec} с"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours} ч {minutes} мин"
        days, hours = divmod(hours, 24)
        return f"{days} д {hours} ч"

    @app.template_filter("status_text")
    def _status_text(status):
        return {
            "available": "Доступен",
            "unavailable": "Недоступен",
            "unknown": "Не проверялся",
        }.get(status, status or "—")


# Import is placed here to avoid circular imports (web packages are imported
# only after the helpers above are defined).
from .routes import register_routes  # noqa: E402,F401