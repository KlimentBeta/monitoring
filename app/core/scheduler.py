"""Background monitor scheduler.

Runs a lightweight daemon thread that every second decides which active sites
are due for a check and dispatches them to a small pool of worker threads.
Checks never run on the UI thread, so neither the web UI nor the desktop GUI
can freeze while requests are being made.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from .database import DatabaseSession
from .models import MonitoredSite
from .monitor import check_site_now, reset_stale_check_flags

logger = logging.getLogger("website_monitor.core.scheduler")

TICK_SECONDS = 1.0


class MonitorScheduler:
    """Periodically checks active sites with respect to their intervals."""

    def __init__(self, max_workers: int = 5) -> None:
        self._max_workers = max_workers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._pool = ThreadPoolExecutor(max_workers=self._max_workers,
                                        thread_name_prefix="wm-check")
        self._thread = threading.Thread(target=self._loop, name="wm-scheduler",
                                        daemon=True)
        self._thread.start()
        logger.info("Monitor scheduler started")

    def stop(self) -> None:
        """Stop the loop and wait for workers to finish current checks."""
        if not self.running:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=False)
        logger.info("Monitor scheduler stopped")

    def _due_sites(self, now: datetime) -> list[int]:
        """Return ids of active sites that are due for a check."""
        with DatabaseSession() as db:
            sites = (
                db.session.query(MonitoredSite)
                .filter(MonitoredSite.is_active.is_(True),
                        MonitoredSite.is_checking.is_(False))
                .all()
            )
            due: list[int] = []
            for site in sites:
                last = site.last_checked_at
                if (last is None
                        or now - last >= timedelta(seconds=site.interval_seconds)):
                    due.append(site.id)
            return due

    def _loop(self) -> None:
        logger.info("Scheduler loop started")
        last_sweep = 0.0
        while not self._stop.is_set():
            now = datetime.utcnow()
            try:
                # Occasionally clear flags that a crashed process may have left.
                if time.monotonic() - last_sweep > 120:
                    reset_stale_check_flags()
                    last_sweep = time.monotonic()
                due = self._due_sites(now)
                for site_id in due:
                    if self._stop.is_set():
                        break
                    self._pool.submit(check_site_now, site_id)
            except Exception:  # noqa: BLE001 - loop must never die
                logger.exception("Scheduler iteration failed")
            self._stop.wait(TICK_SECONDS)
        logger.info("Scheduler loop stopped")