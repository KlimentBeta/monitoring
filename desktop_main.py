"""Entry point for the desktop edition of WebsiteMonitor (PySide6).

Initialises the shared SQLite database, starts the same background monitoring
scheduler as the web edition and shows the main window. The GUI never blocks on
network: every HTTP check happens on a worker thread of the core scheduler.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from app.core.database import init_db
from app.core.scheduler import MonitorScheduler
from app.desktop.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> int:
    init_db()
    scheduler = MonitorScheduler()
    scheduler.start()

    app = QApplication(sys.argv)
    app.setApplicationName("WebsiteMonitor")
    app.setOrganizationName("WebsiteMonitor")

    window = MainWindow(scheduler)
    window.show()

    exit_code = app.exec()
    scheduler.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())