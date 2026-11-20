"""Core monitoring engine.

Responsibilities:
    * validate URLs and protect against SSRF (private/loopback/link-local IPs);
    * perform an HTTP check, measuring the response time and classifying errors;
    * follow redirects manually and re-validate every hop;
    * persist a check into history and update the live site status;
    * manage outage incidents (open on failure, close on recovery).

The check itself is executed on a worker thread; every site is guarded by an
atomic per-site "checking" flag so a site is never checked twice concurrently,
even when the web and the desktop editions run at the same time against the
shared SQLite database.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime

import requests
from sqlalchemy import update

from .database import DatabaseSession, SessionLocal
from .models import CheckHistory, Incident, MonitoredSite

logger = logging.getLogger("website_monitor.core.monitor")

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_REDIRECTS = 5
VALID_SCHEMES = ("http", "https")


@dataclass
class CheckResult:
    """Outcome of a single HTTP check (values about the last hop)."""

    site_id: int
    is_available: bool
    response_time_ms: float
    http_status: int | None = None
    error: str | None = None
    checked_at: datetime = field(default_factory=datetime.utcnow)


def _private_allowed() -> bool:
    """Whether private/local addresses are permitted (test/demo override)."""
    value = os.environ.get("WEBSITE_MONITOR_ALLOW_PRIVATE", "").lower()
    return value in {"1", "true", "yes", "on"}


def _address_allowed(ip_str: str) -> bool:
    """Return True if the given IP may be contacted."""
    if _private_allowed():
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    try:
        return bool(ip.is_global)
    except ValueError:
        return False


def _resolve_host(hostname: str) -> list[str]:
    """Resolve a hostname to a list of IP addresses."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    addrs = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in addrs:
            addrs.append(ip)
    return addrs


def check_url_allowed(raw_url: str) -> tuple[bool, str]:
    """Validate a URL against SSRF policy. Returns (allowed, reason)."""
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except ValueError:
        return False, "Некорректный URL"
    if parsed.scheme not in VALID_SCHEMES:
        return False, "Поддерживаются только протоколы http и https"
    if not parsed.hostname:
        return False, "В URL отсутствует имя хоста"
    host = parsed.hostname.rstrip(".")

    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False

    if is_literal:
        addresses = [host]
    else:
        addresses = _resolve_host(host)
        if not addresses:
            return False, "Ошибка DNS: имя хоста не разрешено"

    for addr in addresses:
        if _address_allowed(addr):
            return True, ""
    return False, "Адрес заблокирован политикой SSRF (private/loopback/link-local)"


def classify_error(exc: Exception) -> str:
    """Turn a requests exception into a human-readable Russian message."""
    if isinstance(exc, requests.exceptions.SSLError):
        return "Ошибка SSL/TLS: не удалось установить защищённое соединение"
    if isinstance(exc, requests.exceptions.MissingSchema):
        return "Протокол не поддерживается (нужен http или https)"
    if isinstance(exc, requests.exceptions.InvalidSchema):
        return "Неподдерживаемая схема (нужен http или https)"
    if isinstance(exc, requests.exceptions.InvalidURL):
        return "Некорректный URL"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "Таймаут соединения"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "Таймаут ожидания ответа"
    if isinstance(exc, requests.exceptions.Timeout):
        return "Таймаут запроса"
    if isinstance(exc, requests.exceptions.ConnectionError):
        message = str(exc).lower()
        if any(
            marker in message
            for marker in ("name or service not known", "nodename nor servname",
                           "getaddrinfo failed", "temporary failure in name resolution")
        ):
            return "DNS-ошибка: имя хоста не разрешено"
        return "Ошибка соединения: хост недоступен или отказал в подключении"
    if isinstance(exc, requests.exceptions.RequestException):
        return f"Ошибка HTTP-запроса: {exc.__class__.__name__}"
    return f"Ошибка: {exc.__class__.__name__}: {exc}"


def perform_check(site, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> CheckResult:
    """Run an HTTP GET against ``site`` and return a CheckResult.

    Never raises: connectivity problems are returned as "unavailable" results.
    The SSRF policy is enforced on the initial host and on every redirect hop
    (redirects are followed manually, up to MAX_REDIRECTS).
    """
    start = time.perf_counter()

    allowed, reason = check_url_allowed(site.url)
    if not allowed:
        elapsed = (time.perf_counter() - start) * 1000.0
        return CheckResult(
            site_id=site.id,
            is_available=False,
            response_time_ms=round(elapsed, 2),
            error=reason,
        )

    current_url = site.url
    http_status = None
    error = None
    is_available = False

    with requests.Session() as session:
        for _hop in range(MAX_REDIRECTS + 1):
            allowed, reason = check_url_allowed(current_url)
            if not allowed:
                error = reason
                break
            try:
                with session.get(
                    current_url, timeout=timeout, allow_redirects=False
                ) as resp:
                    http_status = resp.status_code
                    resp.content  # ensure the body is read before closing
                    location = resp.headers.get("location")
            except requests.exceptions.RequestException as exc:
                error = classify_error(exc)
                break

            if http_status in (301, 302, 303, 307, 308) and location:
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            break
        else:
            error = "Слишком много редиректов"

    elapsed = (time.perf_counter() - start) * 1000.0

    if error is None:
        is_available = 200 <= http_status <= 399
        if not is_available:
            error = f"HTTP-ответ {http_status} (ошибка сервера)"

    return CheckResult(
        site_id=site.id,
        is_available=is_available,
        response_time_ms=round(elapsed, 2),
        http_status=http_status,
        error=error,
    )


def _manage_incident(session, site_id: int, is_available: bool,
                     error: str | None, now: datetime) -> None:
    """Open/update/close an incident based on the new availability state."""
    open_incident = (
        session.query(Incident)
        .filter_by(site_id=site_id, is_resolved=False)
        .order_by(Incident.started_at.desc())
        .first()
    )
    if is_available:
        if open_incident is not None:
            open_incident.is_resolved = True
            open_incident.resolved_at = now
            open_incident.duration_seconds = int(
                (now - open_incident.started_at).total_seconds()
            )
    else:
        if open_incident is None:
            session.add(
                Incident(
                    site_id=site_id,
                    started_at=now,
                    reason=error or "Сайт недоступен",
                )
            )
        else:
            open_incident.reason = error or "Сайт недоступен"


def apply_check_result(session, site_id: int, result: CheckResult) -> CheckHistory:
    """Persist a check result: history entry, site status and incident."""
    site = session.get(MonitoredSite, site_id)
    if site is None:
        raise ValueError(f"Site {site_id} not found")

    check = CheckHistory(
        site_id=site_id,
        checked_at=result.checked_at,
        is_available=result.is_available,
        response_time=result.response_time_ms,
        http_status=result.http_status,
        error_message=result.error,
    )
    session.add(check)

    site.last_checked_at = result.checked_at
    site.last_response_time = result.response_time_ms
    site.last_http_status = result.http_status
    site.last_error = result.error
    site.is_checking = False
    site.current_status = (
        MonitoredSite.STATUS_AVAILABLE
        if result.is_available
        else MonitoredSite.STATUS_UNAVAILABLE
    )

    _manage_incident(session, site_id, result.is_available, result.error,
                     result.checked_at)
    return check


def _acquire_site_check(site_id: int) -> MonitoredSite | None:
    """Atomically mark a site as being checked; returns the loaded site.

    The conditional ``UPDATE`` is committed immediately. Because SQLite
    serialises writers, a site can never be checked twice concurrently —
    even between the web and desktop editions sharing one database.
    """
    session = SessionLocal()
    try:
        res = session.execute(
            update(MonitoredSite)
            .where(MonitoredSite.id == site_id,
                   MonitoredSite.is_checking.is_(False))
            .values(is_checking=True)
        )
        session.commit()
        if res.rowcount == 0:
            return None
        return session.get(MonitoredSite, site_id)
    finally:
        session.close()


def reset_stale_check_flags() -> None:
    """Un-stick ``is_checking`` flags left behind by a crashed process."""
    stale_threshold = DEFAULT_TIMEOUT_SECONDS * 2 + 30
    with DatabaseSession() as db:
        stale = db.session.query(MonitoredSite).filter(
            MonitoredSite.is_checking.is_(True),
        ).all()
        for site in stale:
            last = site.last_checked_at
            if last is None or (datetime.utcnow() - last).total_seconds() > stale_threshold:
                logger.warning("Resetting stale check flag for site %s", site.id)
                site.is_checking = False


def check_site_now(site_id: int) -> tuple[CheckResult | None, str | None]:
    """Guard one site, run the HTTP check and persist it.

    Returns ``(result, note)``. When the check could not start, ``result`` is
    ``None`` and ``note`` is either ``"already-checking"`` or ``"not-found"``
    or ``"error"`` (an unexpected exception was caught and recorded).
    """
    site = _acquire_site_check(site_id)
    if site is None:
        exists = False
        with DatabaseSession() as db:
            exists = db.session.get(MonitoredSite, site_id) is not None
        return (None, "not-found" if not exists else "already-checking")

    try:
        result = perform_check(site)
    except Exception as exc:  # noqa: BLE001 - safety net
        logger.exception("Unexpected error during check of site %s", site_id)
        msg = classify_error(exc)
        with DatabaseSession() as db:
            now = datetime.utcnow()
            db.session.add(
                CheckHistory(
                    site_id=site_id, checked_at=now, is_available=False,
                    error_message=msg,
                )
            )
            s = db.session.get(MonitoredSite, site_id)
            if s is not None:
                s.is_checking = False
                s.current_status = MonitoredSite.STATUS_UNAVAILABLE
                s.last_checked_at = now
                s.last_error = msg
        return (None, "error")

    with DatabaseSession() as db:
        apply_check_result(db.session, site_id, result)
    return (result, None)