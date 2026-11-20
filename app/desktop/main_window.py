"""Main window of the desktop edition of WebsiteMonitor (PySide6).

Pages are switched with a QStackedWidget. All monitoring and HTTP checks run on
background threads (the core scheduler / worker pool), so the GUI never freezes.
The window refreshes itself with QTimer and re-reads the shared SQLite database.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import statistics as stats
from ..services import check_service, site_service

# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

STATUS_TEXT = {
    "available": "Доступен",
    "unavailable": "Недоступен",
    "unknown": "Не проверялся",
}
STATUS_COLOR = {
    "available": QColor("#1e8449"),
    "unavailable": QColor("#c0392b"),
    "unknown": QColor("#6c7a89"),
}
PERIOD_LABELS = [("24h", "Последние 24 часа"),
                 ("7d", "Последние 7 дней"),
                 ("30d", "Последние 30 дней")]


def fmt_dt(value):
    """Format an UTC naive datetime as local time."""
    if not value:
        return "—"
    try:
        local = value.replace(tzinfo=timezone.utc).astimezone()
        return local.strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, TypeError):
        return str(value)


def fmt_ms(value):
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if value >= 1000:
        return f"{value / 1000:.2f} с"
    return f"{value:.1f} мс"


def fmt_duration(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds)
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


def app_icon() -> QIcon:
    """Load the application icon (works both from source and PyInstaller)."""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.dirname(
                       os.path.abspath(__file__)))))
    for name in ("assets/icon.png", "assets/icon.ico"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            return QIcon(path)
    return QIcon()


class LineChartWidget(QWidget):
    """A tiny self-contained line chart drawn with QPainter."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._values: list[float | None] = []
        self._color = QColor("#3b82f6")
        self._ymin = 0.0
        self._ymax = 100.0
        self._y_text = ""
        self.setMinimumHeight(240)

    def set_data(self, labels, values, y_text="", color="#3b82f6",
                 y_min=0.0, y_max=None) -> None:
        self._labels = list(labels)
        self._values = list(values)
        self._y_text = y_text
        self._color = QColor(color)
        valid = [v for v in values if v is not None]
        if y_max is None:
            y_max = max(valid) * 1.15 if valid else 100.0
        self._ymax = y_max if y_max > y_min else y_min + 1.0
        self._ymin = y_min
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        if rect.width() < 10 or rect.height() < 10:
            painter.end()
            return

        painter.setPen(QPen(QColor("#e3e8ee"), 1))
        grid_steps = 5
        for i in range(grid_steps + 1):
            y = rect.top() + rect.height() * i / grid_steps
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            val = self._ymax - (self._ymax - self._ymin) * i / grid_steps
            painter.setPen(QPen(QColor("#7f8c9b"), 1))
            painter.drawText(int(rect.left()) + 2, int(y) - 4,
                             f"{val:.0f}")
            painter.setPen(QPen(QColor("#e3e8ee"), 1))

        n = len(self._values)
        if n == 0:
            painter.setPen(QPen(QColor("#7f8c9b"), 1))
            painter.drawText(rect, Qt.AlignCenter, "Нет данных за период")
            painter.end()
            return

        def to_point(idx, value) -> tuple[float, float]:
            x = rect.left() + rect.width() * idx / max(n - 1, 1)
            span = max(self._ymax - self._ymin, 0.0001)
            y = rect.bottom() - rect.height() * (value - self._ymin) / span
            return x, y

        painter.setPen(QPen(self._color, 2))
        prev: tuple[float, float] | None = None
        for i, value in enumerate(self._values):
            if value is None:
                prev = None
                continue
            x, y = to_point(i, value)
            if prev is not None:
                painter.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
            prev = (x, y)

        # points
        painter.setBrush(self._color)
        for i, value in enumerate(self._values):
            if value is None:
                continue
            x, y = to_point(i, value)
            painter.drawEllipse(int(x) - 3, int(y) - 3, 6, 6)

        # first/last label
        painter.setPen(QPen(QColor("#7f8c9b"), 1))
        if self._labels:
            painter.drawText(int(rect.left()), int(rect.bottom()) + 16,
                             self._labels[0])
        painter.end()


# --------------------------------------------------------------------------
# table helpers
# --------------------------------------------------------------------------

def _make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _item(text: str, color: QColor | None = None, align=None) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    if color is not None:
        item.setForeground(color)
    if align is not None:
        item.setTextAlignment(align)
    return item


def _status_item(status: str) -> QTableWidgetItem:
    return _item(STATUS_TEXT.get(status, status or "—"), STATUS_COLOR.get(status))


class BasePage(QWidget):
    """Base class for all pages: knows the main window and builds a title."""

    PAGE_TITLE = ""

    def __init__(self, main: "MainWindow") -> None:
        super().__init__()
        self.main = main
        layout = QVBoxLayout(self)
        title = QLabel(self.PAGE_TITLE)
        title.setStyleSheet("font-size:20px; font-weight:600;")
        layout.addWidget(title)
        self.body = QVBoxLayout()
        layout.addLayout(self.body)
        layout.addStretch(1)

    def refresh(self) -> None:
        """Reload data from the database. Overridden by subclasses."""


class DashboardPage(BasePage):
    PAGE_TITLE = "Главная панель"

    def __init__(self, main: "MainWindow") -> None:
        super().__init__(main)
        self.value_labels: dict[str, QLabel] = {}
        cards = QHBoxLayout()
        for key, label in [
            ("all", "Всего сайтов"), ("active", "В мониторинге"),
            ("ok", "Доступны"), ("bad", "Недоступны"),
            ("avg", "Среднее время"), ("incidents", "Сбои всего"),
            ("open", "Открытые сбои"),
        ]:
            box = QFrame()
            box.setFrameShape(QFrame.StyledPanel)
            box.setStyleSheet("QFrame { background: white; border:1px solid #e3e8ee; border-radius:10px; }")
            v = QVBoxLayout(box)
            lab = QLabel(label)
            lab.setStyleSheet("color:#7f8c9b; font-size:12px;")
            val = QLabel("—")
            val.setStyleSheet("font-size:22px; font-weight:bold; color:#1f2d3d;")
            v.addWidget(lab)
            v.addWidget(val)
            self.value_labels[key] = val
            cards.addWidget(box, stretch=1)
        self.body.addLayout(cards)

        sites_title = QLabel("Обзор сайтов")
        sites_title.setStyleSheet("font-size:16px; font-weight:600; margin-top:14px;")
        self.body.addWidget(sites_title)
        self.sites_table = _make_table(
            ["Название", "URL", "Статус", "Отклик", "HTTP", "Последняя проверка", "Интервал"])
        self.body.addWidget(self.sites_table)

        events_title = QLabel("Последние события мониторинга")
        events_title.setStyleSheet("font-size:16px; font-weight:600; margin-top:14px;")
        self.body.addWidget(events_title)
        self.events_table = _make_table(
            ["Время", "Сайт", "Результат", "HTTP", "Время отклика", "Ошибка"])
        self.body.addWidget(self.events_table)

    def refresh(self) -> None:
        overview = stats.get_overview()
        self.value_labels["all"].setText(str(overview["total"]))
        self.value_labels["active"].setText(str(overview["active"]))
        self.value_labels["ok"].setText(str(overview["available"]))
        self.value_labels["ok"].setStyleSheet(
            "font-size:22px; font-weight:bold; color:#1e8449;")
        self.value_labels["bad"].setText(str(overview["unavailable"]))
        self.value_labels["bad"].setStyleSheet(
            "font-size:22px; font-weight:bold; color:#c0392b;")
        self.value_labels["avg"].setText(fmt_ms(overview["avg_response"]))
        self.value_labels["incidents"].setText(str(overview["incidents"]))
        self.value_labels["open"].setText(str(overview["open_incidents"]))

        self._fill_sites(stats.get_sites_list())
        self._fill_events(overview["recent_events"])

    def _fill_sites(self, sites: list[dict]) -> None:
        table = self.sites_table
        table.setRowCount(len(sites))
        for row, s in enumerate(sites):
            table.setItem(row, 0, _item(s["name"]))
            table.setItem(row, 1, _item(s["url"], QColor("#7f8c9b")))
            table.setItem(row, 2, _status_item(s["current_status"]))
            table.setItem(row, 3, _item(fmt_ms(s["last_response_time"])))
            table.setItem(row, 4, _item(s["last_http_status"] if s["last_http_status"] is not None else "—"))
            table.setItem(row, 5, _item(fmt_dt(s["last_checked_at"]), QColor("#7f8c9b")))
            table.setItem(row, 6, _item(f"{s['interval_seconds']} с"))

    def _fill_events(self, events: list[dict]) -> None:
        table = self.events_table
        table.setRowCount(len(events))
        for row, e in enumerate(events):
            table.setItem(row, 0, _item(fmt_dt(e["checked_at"]), QColor("#7f8c9b")))
            table.setItem(row, 1, _item(e["site_name"]))
            table.setItem(row, 2, _item(
                "Доступен" if e["is_available"] else "Недоступен",
                QColor("#1e8449") if e["is_available"] else QColor("#c0392b")))
            table.setItem(row, 3, _item(e["http_status"] if e["http_status"] is not None else "—"))
            table.setItem(row, 4, _item(fmt_ms(e["response_time"])))
            table.setItem(row, 5, _item(e["error_message"] or "—", QColor("#7f8c9b")))


class SitesPage(BasePage):
    PAGE_TITLE = "Мониторинг сайтов"

    def __init__(self, main: "MainWindow") -> None:
        super().__init__(main)
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Добавить сайт")
        add_btn.setStyleSheet("background:#3b82f6; color:white; border:none;"
                              " border-radius:6px; padding:8px 14px; font-weight:600;")
        add_btn.clicked.connect(self._on_add)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addStretch(1)
        self.body.addLayout(toolbar)

        self.table = _make_table(
            ["Название", "URL", "Статус", "Отклик", "Интервал", "Активен", "Действия"])
        self.table.setColumnWidth(6, 260)
        self.body.addWidget(self.table)

    def refresh(self) -> None:
        sites = stats.get_sites_list()
        table = self.table
        table.setRowCount(len(sites))
        for row, s in enumerate(sites):
            table.setItem(row, 0, _item(s["name"]))
            table.setItem(row, 1, _item(s["url"], QColor("#7f8c9b")))
            table.setItem(row, 2, _status_item(s["current_status"]))
            table.setItem(row, 3, _item(fmt_ms(s["last_response_time"])))
            table.setItem(row, 4, _item(f"{s['interval_seconds']} с"))

            check = QCheckBox()
            check.setChecked(s["is_active"])
            check.toggled.connect(
                lambda enabled, sid=s["id"]: self._on_toggle(sid, enabled))
            wrapper = QWidget()
            lay = QHBoxLayout(wrapper)
            lay.setContentsMargins(6, 0, 6, 0)
            lay.addStretch(1)
            lay.addWidget(check)
            lay.addStretch(1)
            table.setCellWidget(row, 5, wrapper)

            table.setCellWidget(row, 6, self._action_widget(s))

    def _action_widget(self, site: dict) -> QWidget:
        wrapper = QWidget()
        lay = QHBoxLayout(wrapper)
        lay.setContentsMargins(2, 0, 2, 0)

        check_btn = QPushButton("Проверить")
        check_btn.clicked.connect(lambda _=False, sid=site["id"]: self._on_check(sid))
        edit_btn = QPushButton("Изменить")
        edit_btn.clicked.connect(lambda _=False, sid=site["id"]: self._on_edit(sid))
        delete_btn = QPushButton("Удалить")
        delete_btn.setStyleSheet("color:#c0392b;")
        delete_btn.clicked.connect(lambda _=False, sid=site["id"]: self._on_delete(sid))

        for btn in (check_btn, edit_btn, delete_btn):
            btn.setFixedWidth(84)
            lay.addWidget(btn)
        return wrapper

    def _on_add(self) -> None:
        from .dialogs import SiteDialog  # noqa: PLC0415

        result = SiteDialog.get_site(self)
        if result is None:
            return
        name, url, interval = result
        site_id, error = site_service.add_site(name, url, interval)
        if error:
            QMessageBox.warning(self, "Ошибка", error)
        else:
            self.refresh()

    def _on_edit(self, site_id: int) -> None:
        from .dialogs import SiteDialog  # noqa: PLC0415

        current = None
        for s in stats.get_sites_list():
            if s["id"] == site_id:
                current = s
                break
        if current is None:
            return
        result = SiteDialog.get_site(self, current)
        if result is None:
            return
        name, url, interval = result
        ok, error = site_service.update_site(site_id, name, url, interval)
        if not ok:
            QMessageBox.warning(self, "Ошибка", error)
        self.refresh()

    def _on_delete(self, site_id: int) -> None:
        answer = QMessageBox.question(
            self, "Удаление сайта",
            "Удалить сайт вместе со всей историей и инцидентами?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            site_service.delete_site(site_id)
            self.refresh()

    def _on_toggle(self, site_id: int, enabled: bool) -> None:
        site_service.set_active(site_id, enabled)
        self.main.statusBar().showMessage(
            "Мониторинг включён" if enabled else "Мониторинг отключён", 3000)

    def _on_check(self, site_id: int) -> None:
        check_service.run_check_async(site_id)
        self.main.statusBar().showMessage(
            "Проверка запущена, результат появится через несколько секунд…", 5000)
        QTimer.singleShot(2500, self.refresh)


class HistoryPage(BasePage):
    PAGE_TITLE = "История проверок"

    def __init__(self, main: "MainWindow") -> None:
        super().__init__(main)
        toolbar = QHBoxLayout()
        self.site_combo = QComboBox()
        self.site_combo.currentIndexChanged.connect(self.refresh)
        self.period_combo = QComboBox()
        for key, label in PERIOD_LABELS:
            self.period_combo.addItem(label, key)
        self.period_combo.currentIndexChanged.connect(self.refresh)
        clear_btn = QPushButton("Очистить историю")
        clear_btn.setStyleSheet("background:#f39c12; color:white; border:none;"
                                " border-radius:6px; padding:8px 14px;")
        clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(QLabel("Сайт:"))
        toolbar.addWidget(self.site_combo, 1)
        toolbar.addWidget(QLabel("Период:"))
        toolbar.addWidget(self.period_combo)
        toolbar.addWidget(clear_btn)
        toolbar.addStretch(1)
        self.body.addLayout(toolbar)

        self.table = _make_table(
            ["Дата и время", "Сайт", "Результат", "HTTP-код", "Время отклика",
             "Ошибка"])
        self.body.addWidget(self.table)

    def _selected_site_id(self) -> int | None:
        data = self.site_combo.currentData()
        return data if data else None

    def _reload_site_combo(self) -> None:
        current = self._selected_site_id()
        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        self.site_combo.addItem("Все сайты", None)
        for s in stats.get_sites_list():
            self.site_combo.addItem(s["name"], s["id"])
        idx = self.site_combo.findData(current)
        self.site_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.site_combo.blockSignals(False)

    def refresh(self) -> None:
        self._reload_site_combo()
        period = self.period_combo.currentData() or "24h"
        records = stats.get_site_history(self._selected_site_id(), period,
                                         limit=300)
        table = self.table
        table.setRowCount(len(records))
        for row, r in enumerate(records):
            table.setItem(row, 0, _item(fmt_dt(r["checked_at"]), QColor("#7f8c9b")))
            table.setItem(row, 1, _item(r["site_name"]))
            table.setItem(row, 2, _item(
                "Доступен" if r["is_available"] else "Ошибка",
                QColor("#1e8449") if r["is_available"] else QColor("#c0392b")))
            table.setItem(row, 3, _item(r["http_status"] if r["http_status"] is not None else "—"))
            table.setItem(row, 4, _item(fmt_ms(r["response_time"])))
            table.setItem(row, 5, _item(r["error_message"] or "—", QColor("#7f8c9b")))

    def _on_clear(self) -> None:
        site_id = self._selected_site_id()
        if site_id is None:
            QMessageBox.information(self, "Очистка истории",
                                    "Выберите конкретный сайт для очистки его истории.")
            return
        answer = QMessageBox.question(
            self, "Очистка истории",
            "Удалить всю историю проверок выбранного сайта?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            removed = site_service.purge_history(site_id)
            self.main.statusBar().showMessage(f"Удалено записей: {removed}", 4000)
            self.refresh()


class IncidentsPage(BasePage):
    PAGE_TITLE = "История сбоев"

    def __init__(self, main: "MainWindow") -> None:
        super().__init__(main)
        toolbar = QHBoxLayout()
        self.site_combo = QComboBox()
        self.site_combo.currentIndexChanged.connect(self.refresh)
        self.status_combo = QComboBox()
        for label, key in [("Все инциденты", "all"),
                           ("Только открытые", "open"),
                           ("Только закрытые", "resolved")]:
            self.status_combo.addItem(label, key)
        self.status_combo.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(QLabel("Сайт:"))
        toolbar.addWidget(self.site_combo, 1)
        toolbar.addWidget(QLabel("Статус:"))
        toolbar.addWidget(self.status_combo)
        toolbar.addStretch(1)
        self.body.addLayout(toolbar)

        self.table = _make_table(
            ["Сайт", "Начало сбоя", "Восстановление", "Продолжительность",
             "Статус", "Причина"])
        self.body.addWidget(self.table)

    def _selected_site_id(self) -> int | None:
        data = self.site_combo.currentData()
        return data if data else None

    def _reload_site_combo(self) -> None:
        current = self._selected_site_id()
        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        self.site_combo.addItem("Все сайты", None)
        for s in stats.get_sites_list():
            self.site_combo.addItem(s["name"], s["id"])
        idx = self.site_combo.findData(current)
        self.site_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.site_combo.blockSignals(False)

    def refresh(self) -> None:
        self._reload_site_combo()
        only_open = self.status_combo.currentData() == "open"
        records = stats.get_incidents(self._selected_site_id(), only_open,
                                      limit=300)
        table = self.table
        table.setRowCount(len(records))
        for row, inc in enumerate(records):
            table.setItem(row, 0, _item(inc["site_name"]))
            table.setItem(row, 1, _item(fmt_dt(inc["started_at"]), QColor("#7f8c9b")))
            table.setItem(row, 2, _item(fmt_dt(inc["resolved_at"]), QColor("#7f8c9b")))
            table.setItem(row, 3, _item(fmt_duration(inc["duration_seconds"])))
            resolved = inc["is_resolved"]
            table.setItem(row, 4, _item(
                "Закрыт" if resolved else "Открыт",
                QColor("#1e8449") if resolved else QColor("#c0392b")))
            table.setItem(row, 5, _item(inc["reason"] or "—", QColor("#7f8c9b")))


class StatisticsPage(BasePage):
    PAGE_TITLE = "Статистика"

    def __init__(self, main: "MainWindow") -> None:
        super().__init__(main)
        toolbar = QHBoxLayout()
        self.site_combo = QComboBox()
        self.site_combo.currentIndexChanged.connect(self.refresh)
        self.period_combo = QComboBox()
        for key, label in PERIOD_LABELS:
            self.period_combo.addItem(label, key)
        self.period_combo.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(QLabel("Сайт:"))
        toolbar.addWidget(self.site_combo, 1)
        toolbar.addWidget(QLabel("Период:"))
        toolbar.addWidget(self.period_combo)
        toolbar.addStretch(1)
        self.body.addLayout(toolbar)

        self.metric_labels: dict[str, QLabel] = {}
        cards = QHBoxLayout()
        for key, label in [("avg", "Среднее время"), ("max", "Максимум"),
                           ("min", "Минимум"), ("ok", "Успешных"),
                           ("fail", "Неудачных"), ("avail", "Доступность"),
                           ("inc", "Сбоев")]:
            box = QFrame()
            box.setFrameShape(QFrame.StyledPanel)
            box.setStyleSheet("QFrame { background:white; border:1px solid #e3e8ee; border-radius:10px; }")
            v = QVBoxLayout(box)
            lab = QLabel(label)
            lab.setStyleSheet("color:#7f8c9b; font-size:12px;")
            val = QLabel("—")
            val.setStyleSheet("font-size:20px; font-weight:bold;")
            v.addWidget(lab)
            v.addWidget(val)
            self.metric_labels[key] = val
            cards.addWidget(box, stretch=1)
        self.body.addLayout(cards)

        charts_row = QHBoxLayout()
        resp_box = QFrame()
        resp_box.setFrameShape(QFrame.StyledPanel)
        resp_box.setStyleSheet("QFrame { background:white; border:1px solid #e3e8ee; border-radius:10px; }")
        rv = QVBoxLayout(resp_box)
        rt = QLabel("Время отклика, мс")
        rt.setStyleSheet("color:#7f8c9b; font-size:12px;")
        self.response_chart = LineChartWidget()
        self.response_chart.set_data([], [], y_text="мс", color="#3b82f6")
        rv.addWidget(rt)
        rv.addWidget(self.response_chart)
        charts_row.addWidget(resp_box, stretch=1)

        avail_box = QFrame()
        avail_box.setFrameShape(QFrame.StyledPanel)
        avail_box.setStyleSheet("QFrame { background:white; border:1px solid #e3e8ee; border-radius:10px; }")
        av = QVBoxLayout(avail_box)
        at = QLabel("Доступность по времени, %")
        at.setStyleSheet("color:#7f8c9b; font-size:12px;")
        self.avail_chart = LineChartWidget()
        self.avail_chart.set_data([], [], y_text="%", color="#2ecc71", y_max=100)
        av.addWidget(at)
        av.addWidget(self.avail_chart)
        charts_row.addWidget(avail_box, stretch=1)
        self.body.addLayout(charts_row)

    def _selected_site_id(self) -> int | None:
        data = self.site_combo.currentData()
        return data if data else None

    def _reload_site_combo(self) -> None:
        current = self._selected_site_id()
        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        self.site_combo.addItem("Все сайты", None)
        for s in stats.get_sites_list():
            self.site_combo.addItem(s["name"], s["id"])
        idx = self.site_combo.findData(current)
        self.site_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.site_combo.blockSignals(False)

    def refresh(self) -> None:
        self._reload_site_combo()
        period = self.period_combo.currentData() or "24h"
        data = stats.get_site_statistics(self._selected_site_id(), period)
        self.metric_labels["avg"].setText(fmt_ms(data["avg_response"]))
        self.metric_labels["max"].setText(fmt_ms(data["max_response"]))
        self.metric_labels["min"].setText(fmt_ms(data["min_response"]))
        self.metric_labels["ok"].setText(str(data["checks_ok"]))
        self.metric_labels["fail"].setText(str(data["checks_fail"]))
        self.metric_labels["fail"].setStyleSheet(
            "font-size:20px; font-weight:bold; color:#c0392b;")
        self.metric_labels["avail"].setText(f"{data['availability_pct']}%")
        self.metric_labels["avail"].setStyleSheet(
            "font-size:20px; font-weight:bold; color:#1e8449;")
        self.metric_labels["inc"].setText(str(data["incidents"]))

        series = data["series"]
        labels = series["labels"]
        self.response_chart.set_data(labels, series["response"],
                                    y_text="мс", color="#3b82f6", y_min=0)
        self.avail_chart.set_data(labels, series["availability"],
                                  y_text="%", color="#2ecc71",
                                  y_min=0, y_max=100)


class MainWindow(QMainWindow):
    """Main application window with a sidebar and stacked pages."""

    PAGES = [
        ("Главная", DashboardPage),
        ("Мониторинг сайтов", SitesPage),
        ("История проверок", HistoryPage),
        ("История сбоев", IncidentsPage),
        ("Статистика", StatisticsPage),
    ]

    def __init__(self, scheduler=None) -> None:
        super().__init__()
        self.scheduler = scheduler
        self.setWindowTitle("WebsiteMonitor — мониторинг сайтов и API")
        self.setWindowIcon(app_icon())
        self.resize(1150, 720)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- sidebar ---
        sidebar = QFrame()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(
            "QFrame { background:#1f2d3d; }"
            "QPushButton { color:#c7d0da; background:transparent; border:none;"
            " text-align:left; padding:12px 18px; font-size:14px; }"
            "QPushButton:hover { background:rgba(255,255,255,.08); color:white; }"
            "QPushButton:checked { background:#3b82f6; color:white; font-weight:600; }")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 12, 0, 12)

        brand = QLabel("WebsiteMonitor")
        brand.setStyleSheet("color:white; font-size:16px; font-weight:700;"
                            " padding:8px 18px 16px 18px;")
        sidebar_layout.addWidget(brand)

        self.page_group = QButtonGroup(self)
        self.page_group.setExclusive(True)
        self.stack = QStackedWidget()
        self.pages: dict[str, BasePage] = {}

        for name, page_class in self.PAGES:
            page = page_class(self)
            self.stack.addWidget(page)
            self.pages[name] = page
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, n=name: self._switch(n))
            self.page_group.addButton(btn)
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch(1)
        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # status bar
        self.statusBar().showMessage("Мониторинг запущен в фоновом потоке")

        # periodic refresh
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_current)
        self.timer.start(3000)

        self._switch("Главная")

    def _switch(self, name: str) -> None:
        index = self.stack.indexOf(self.pages[name])
        self.stack.setCurrentIndex(index)
        btn = self.page_group.buttons()[index]
        btn.setChecked(True)
        self.pages[name].refresh()

    def _refresh_current(self) -> None:
        page = self.stack.currentWidget()
        if isinstance(page, BasePage):
            page.refresh()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self.timer.stop()
        if self.scheduler is not None:
            try:
                self.scheduler.stop()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)