"""Service: running checks and maintaining history hygiene."""
from __future__ import annotations

import logging

from ..core.monitor import check_site_now
from .site_service import enforce_history_limit, purge_history_older_than

logger = logging.getLogger("website_monitor.services.check")


def run_check_async(site_id: int) -> None:
    """Run a manual check on a worker thread (fire and forget).

    Used by the UI so the interface never blocks on the network.
    """
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    def _worker() -> None:
        try:
            _, note = check_site_now(site_id)
            if note:
                logger.info("Manual check of site %s skipped: %s", site_id, note)
        except Exception:  # noqa: BLE001
            logger.exception("Manual check of site %s failed", site_id)

    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(_worker)


def run_check_sync(site_id: int) -> tuple[dict | None, str | None]:
    """Run a manual check synchronously (useful for tests / API)."""
    result, note = check_site_now(site_id)
    if result is None:
        return None, note
    return {
        "site_id": result.site_id,
        "is_available": result.is_available,
        "response_time": result.response_time_ms,
        "http_status": result.http_status,
        "error": result.error,
        "checked_at": result.checked_at.isoformat(),
    }, None


def maintenance() -> dict:
    """Periodic hygiene: cap history per site and drop very old rows."""
    from ..core.statistics import get_sites_list  # noqa: PLC0415

    trimmed = 0
    purged = 0
    for site in get_sites_list():
        trimmed += enforce_history_limit(site["id"])
    purged = purge_history_older_than(60 * 60 * 24 * 14)
    logger.info("Maintenance done: trimmed=%d purged=%d", trimmed, purged)
    return {"trimmed": trimmed, "purged": purged}