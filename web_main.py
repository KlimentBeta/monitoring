"""Entry point for the web edition of WebsiteMonitor.

Starts the Flask server on 127.0.0.1 (localhost only), initialises the shared
SQLite database, starts the background monitoring scheduler and opens the
default browser. The server stops gracefully on Ctrl+C / window close.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
import webbrowser

from app.core.database import init_db
from app.core.scheduler import MonitorScheduler
from app.web import create_app

logger = logging.getLogger("website_monitor.web_main")

HOST = "127.0.0.1"
FIRST_PORT = 8000
PORT_RANGE = 200


def find_free_port(start: int = FIRST_PORT, count: int = PORT_RANGE) -> int:
    """Find the first free TCP port on localhost starting from ``start``."""
    for port in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
            return port
    return start  # (unlikely) last resort; Flask will report the conflict


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    def _run() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            logger.exception("Could not open the browser automatically")

    threading.Thread(target=_run, daemon=True).start()


def main() -> None:
    init_db()
    scheduler = MonitorScheduler()
    scheduler.start()

    app = create_app()
    port = find_free_port()
    url = f"http://{HOST}:{port}/"

    print("=" * 62)
    print("  WebsiteMonitor — веб-версия")
    print(f"  Адрес интерфейса: {url}")
    print("  Для выхода нажмите Ctrl+C в этом окне.")
    print("=" * 62)

    _open_browser_later(url)

    try:
        app.run(host=HOST, port=port, debug=False, threaded=True,
                use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        logger.info("Web edition stopped")


if __name__ == "__main__":
    main()