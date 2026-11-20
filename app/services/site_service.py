"""Service: management of monitored sites (CRUD + validation)."""
from __future__ import annotations

import re
import urllib.parse

from sqlalchemy import delete

from ..core.database import DatabaseSession
from ..core.models import CheckHistory, Incident, MonitoredSite
from ..core.monitor import check_url_allowed, VALID_SCHEMES

_NAME_RE = re.compile(r"^\s*\S.{0,198}$")  # 1..200 non-blank characters
MIN_INTERVAL = 5
MAX_INTERVAL = 86400 * 7  # 1 week

# How many history rows we keep per site; older rows are cleaned automatically.
HISTORY_LIMIT_PER_SITE = 5000


def validate_site_data(name: str, url: str, interval_seconds: int) -> tuple[bool, str]:
    """Validate form input. Returns (ok, error_message)."""
    name = (name or "").strip()
    url = (url or "").strip()
    if not name:
        return False, "Укажите название сайта"
    if not _NAME_RE.match(name):
        return False, "Название слишком длинное (максимум 200 символов)"
    if not url:
        return False, "Укажите URL сайта"
    if len(url) > 2048:
        return False, "URL слишком длинный"
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False, "URL имеет неверный формат"
    if parsed.scheme not in VALID_SCHEMES:
        return False, "Укажите http:// или https:// в начале адреса"
    if not parsed.hostname or "." not in parsed.hostname and parsed.hostname != "localhost":
        if not parsed.hostname:
            return False, "В URL отсутствует имя хоста"
    if not 5 <= interval_seconds <= MAX_INTERVAL:
        return False, f"Интервал проверки — от 5 секунд до {MAX_INTERVAL} секунд"
    return True, ""


def add_site(name: str, url: str, interval_seconds: int) -> tuple[MonitoredSite | None, str]:
    """Create a site. Returns (site_or_None, error_message)."""
    ok, message = validate_site_data(name, url, interval_seconds)
    if not ok:
        return None, message
    with DatabaseSession() as db:
        site = MonitoredSite(
            name=name.strip(),
            url=url.strip(),
            interval_seconds=interval_seconds,
            is_active=True,
            current_status=MonitoredSite.STATUS_UNKNOWN,
        )
        db.session.add(site)
        db.session.flush()
        site_id = site.id
    return site_id, ""


def update_site(
    site_id: int, name: str, url: str, interval_seconds: int
) -> tuple[bool, str]:
    """Edit an existing site. Returns (ok, error_message)."""
    ok, message = validate_site_data(name, url, interval_seconds)
    if not ok:
        return False, message
    with DatabaseSession() as db:
        site = db.session.get(MonitoredSite, site_id)
        if site is None:
            return False, "Сайт не найден"
        site.name = name.strip()
        site.url = url.strip()
        site.interval_seconds = interval_seconds
    return True, ""


def delete_site(site_id: int) -> bool:
    """Remove a site together with its history and incidents."""
    with DatabaseSession() as db:
        db.session.execute(delete(CheckHistory).where(CheckHistory.site_id == site_id))
        db.session.execute(delete(Incident).where(Incident.site_id == site_id))
        db.session.execute(delete(MonitoredSite).where(MonitoredSite.id == site_id))
    return True


def set_active(site_id: int, is_active: bool) -> bool:
    """Enable/disable monitoring for a site."""
    with DatabaseSession() as db:
        site = db.session.get(MonitoredSite, site_id)
        if site is None:
            return False
        site.is_active = bool(is_active)
    return True


def purge_history(site_id: int) -> int:
    """Delete all check history for a site. Returns number of removed rows."""
    with DatabaseSession() as db:
        result = db.session.execute(
            delete(CheckHistory).where(CheckHistory.site_id == site_id)
        )
    return result.rowcount


def purge_history_older_than(age_seconds: int = 60 * 60 * 24 * 14) -> int:
    """Delete history older than ``age_seconds`` (except open incidents)."""
    from datetime import datetime, timedelta  # noqa: PLC0415

    cutoff = datetime.utcnow() - timedelta(seconds=age_seconds)
    with DatabaseSession() as db:
        result = db.session.execute(
            delete(CheckHistory).where(CheckHistory.checked_at < cutoff)
        )
    return result.rowcount


def enforce_history_limit(site_id: int, limit: int = HISTORY_LIMIT_PER_SITE) -> int:
    """Keep at most ``limit`` rows per site; delete the oldest excess rows."""
    from sqlalchemy import func, select  # noqa: PLC0415

    with DatabaseSession() as db:
        count = (
            db.session.query(func.count(CheckHistory.id))
            .filter(CheckHistory.site_id == site_id).scalar() or 0
        )
        if count <= limit:
            return 0
        excess = count - limit
        oldest = (
            select(CheckHistory.id)
            .where(CheckHistory.site_id == site_id)
            .order_by(CheckHistory.checked_at.asc())
            .limit(excess)
        )
        result = db.session.execute(
            delete(CheckHistory).where(CheckHistory.id.in_(oldest))
        )
        return result.rowcount