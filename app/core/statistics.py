"""Statistics computed from real records in the SQLite database."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func

from .database import DatabaseSession
from .models import CheckHistory, Incident, MonitoredSite

# bucket sizes (seconds) per period so the charts look reasonable
_PERIOD_BUCKETS_SECONDS = {"24h": 3600, "7d": 6 * 3600, "30d": 24 * 3600}
# canonical period keys used by the UI and API
PERIOD_KEYS = ("24h", "7d", "30d")
PERIOD_DELTAS = {"24h": timedelta(hours=24),
                 "7d": timedelta(days=7),
                 "30d": timedelta(days=30)}


def _delta_for(period: str) -> timedelta:
    return PERIOD_DELTAS.get(period, PERIOD_DELTAS["24h"])


def _bucket_seconds_for(period: str) -> int:
    return _PERIOD_BUCKETS_SECONDS.get(period, 3600)


def get_overview() -> dict:
    """Aggregate numbers for the main dashboard."""
    with DatabaseSession() as db:
        total = db.session.query(func.count(MonitoredSite.id)).scalar() or 0
        active = (
            db.session.query(func.count(MonitoredSite.id))
            .filter(MonitoredSite.is_active.is_(True)).scalar() or 0
        )
        available = (
            db.session.query(func.count(MonitoredSite.id))
            .filter(MonitoredSite.current_status
                    == MonitoredSite.STATUS_AVAILABLE).scalar() or 0
        )
        unavailable = (
            db.session.query(func.count(MonitoredSite.id))
            .filter(MonitoredSite.current_status
                    == MonitoredSite.STATUS_UNAVAILABLE).scalar() or 0
        )
        unknown = (
            db.session.query(func.count(MonitoredSite.id))
            .filter(MonitoredSite.current_status
                    == MonitoredSite.STATUS_UNKNOWN).scalar() or 0
        )
        avg_response = (
            db.session.query(func.avg(CheckHistory.response_time)).scalar()
        )
        incidents = db.session.query(func.count(Incident.id)).scalar() or 0
        open_incidents = (
            db.session.query(func.count(Incident.id))
            .filter(Incident.is_resolved.is_(False)).scalar() or 0
        )

        recent = (
            db.session.query(CheckHistory)
            .order_by(CheckHistory.checked_at.desc()).limit(10).all()
        )
        recent_events = [
            {
                "id": ch.id,
                "site_id": ch.site_id,
                "site_name": _site_name(db.session, ch.site_id),
                "checked_at": ch.checked_at,
                "is_available": ch.is_available,
                "response_time": ch.response_time,
                "http_status": ch.http_status,
                "error_message": ch.error_message,
            }
            for ch in recent
        ]

    return {
        "total": total,
        "active": active,
        "available": available,
        "unavailable": unavailable,
        "unknown": unknown,
        "avg_response": round(avg_response, 2) if avg_response is not None else None,
        "incidents": incidents,
        "open_incidents": open_incidents,
        "recent_events": recent_events,
    }


def _site_name(session, site_id: int) -> str:
    site = session.get(MonitoredSite, site_id)
    return site.name if site else f"#{site_id}"


def get_sites_list() -> list[dict]:
    """Serialisable list of sites for the sites page / desktop table."""
    with DatabaseSession() as db:
        sites = db.session.query(MonitoredSite).order_by(MonitoredSite.id).all()
        return [{
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "interval_seconds": s.interval_seconds,
            "is_active": s.is_active,
            "current_status": s.current_status,
            "last_checked_at": s.last_checked_at,
            "last_response_time": s.last_response_time,
            "last_http_status": s.last_http_status,
            "last_error": s.last_error,
        } for s in sites]


def get_site_history(site_id: int | None, period: str = "24h",
                     limit: int = 200) -> list[dict]:
    """Latest checks, optionally filtered to one site and a time period."""
    delta = _delta_for(period)
    since = datetime.utcnow() - delta
    with DatabaseSession() as db:
        q = db.session.query(CheckHistory).filter(
            CheckHistory.checked_at >= since
        )
        if site_id is not None:
            q = q.filter(CheckHistory.site_id == site_id)
        records = q.order_by(CheckHistory.checked_at.desc()).limit(limit).all()
        return [{
            "id": r.id,
            "site_id": r.site_id,
            "site_name": _site_name(db.session, r.site_id),
            "checked_at": r.checked_at,
            "is_available": r.is_available,
            "response_time": r.response_time,
            "http_status": r.http_status,
            "error_message": r.error_message,
        } for r in records]


def get_incidents(site_id: int | None = None, only_open: bool = False,
                  limit: int = 200) -> list[dict]:
    """Incident history."""
    with DatabaseSession() as db:
        q = db.session.query(Incident)
        if site_id is not None:
            q = q.filter(Incident.site_id == site_id)
        if only_open:
            q = q.filter(Incident.is_resolved.is_(False))
        records = q.order_by(Incident.started_at.desc()).limit(limit).all()
        return [{
            "id": inc.id,
            "site_id": inc.site_id,
            "site_name": _site_name(db.session, inc.site_id),
            "started_at": inc.started_at,
            "resolved_at": inc.resolved_at,
            "duration_seconds": inc.duration_seconds,
            "reason": inc.reason,
            "is_resolved": inc.is_resolved,
        } for inc in records]


def _series(records: list[CheckHistory], period: str) -> dict:
    """Build response-time and availability series bucketed over ``period``."""
    if not records:
        return {"labels": [], "response": [], "availability": []}

    bucket_seconds = _bucket_seconds_for(period)
    now = datetime.utcnow()
    delta = _delta_for(period)

    n_buckets = max(1, int(delta.total_seconds() // bucket_seconds))
    labels = []
    for i in range(n_buckets):
        bucket_start = now - delta + timedelta(seconds=i * bucket_seconds)
        labels.append(bucket_start.strftime("%m-%d %H:%M"))

    buckets = {i: {"sum": 0.0, "count": 0, "ok": 0, "total": 0}
               for i in range(n_buckets)}
    for r in records:
        idx = int((r.checked_at - (now - delta)).total_seconds() // bucket_seconds)
        if idx < 0 or idx >= n_buckets:
            continue
        buckets[idx]["total"] += 1
        if r.is_available:
            buckets[idx]["ok"] += 1
        if r.response_time is not None:
            buckets[idx]["sum"] += r.response_time
            buckets[idx]["count"] += 1

    response = []
    availability = []
    for i in range(n_buckets):
        b = buckets[i]
        response.append(round(b["sum"] / b["count"], 2) if b["count"] else None)
        availability.append(round(b["ok"] / b["total"] * 100, 1)
                            if b["total"] else None)
    return {"labels": labels, "response": response, "availability": availability}


def get_site_statistics(site_id: int | None, period: str = "24h") -> dict:
    """Metrics + chart series for one site (or all sites when None)."""
    delta = _delta_for(period)
    since = datetime.utcnow() - delta
    with DatabaseSession() as db:
        q = db.session.query(CheckHistory).filter(CheckHistory.checked_at >= since)
        qi = db.session.query(Incident).filter(Incident.started_at >= since)
        if site_id is not None:
            q = q.filter(CheckHistory.site_id == site_id)
            qi = qi.filter(Incident.site_id == site_id)
        records = q.order_by(CheckHistory.checked_at.asc()).all()
        inc_records = qi.all()

        if records:
            total = len(records)
            ok = sum(1 for r in records if r.is_available)
            times = [r.response_time for r in records
                     if r.response_time is not None]
            avg = round(sum(times) / len(times), 2) if times else None
            mx = round(max(times), 2) if times else None
            mn = round(min(times), 2) if times else None
            availability = round(ok / total * 100, 2)
        else:
            total = ok = availability = 0
            avg = mx = mn = None

        series = _series(records, period)
        incidents_in_period = len(inc_records)
        site_name = _site_name(db.session, site_id) if site_id else "Все сайты"

    return {
        "period": period,
        "site_id": site_id,
        "site_name": site_name,
        "checks_total": total,
        "checks_ok": ok,
        "checks_fail": total - ok,
        "availability_pct": availability,
        "avg_response": avg,
        "max_response": mx,
        "min_response": mn,
        "incidents": incidents_in_period,
        "series": series,
    }