"""Dialog windows for the desktop edition (add / edit a monitored site)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from ..services.site_service import MAX_INTERVAL, MIN_INTERVAL, validate_site_data


class SiteDialog(QDialog):
    """Modal dialog to create or edit a site."""

    def __init__(self, parent=None, initial: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Добавить сайт" if not initial else "Изменить сайт")
        self.setMinimumWidth(420)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Например: Основной сайт")
        self.name_edit.setMaxLength(200)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com")
        self.url_edit.setMaxLength(2048)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(MIN_INTERVAL, MAX_INTERVAL)
        self.interval_spin.setValue(60)
        self.interval_spin.setSuffix(" с")

        if initial:
            self.name_edit.setText(initial.get("name", ""))
            self.url_edit.setText(initial.get("url", ""))
            self.interval_spin.setValue(int(initial.get("interval_seconds", 60)))

        form = QFormLayout()
        form.addRow("Название", self.name_edit)
        form.addRow("URL", self.url_edit)
        form.addRow("Интервал проверки", self.interval_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._buttons = buttons

    def _on_accept(self) -> None:
        ok, message = validate_site_data(
            self.name_edit.text(),
            self.url_edit.text(),
            self.interval_spin.value(),
        )
        if not ok:
            QMessageBox.warning(self, "Проверка данных", message)
            return
        self.accept()

    def values(self) -> tuple[str, str, int]:
        return (
            self.name_edit.text().strip(),
            self.url_edit.text().strip(),
            self.interval_spin.value(),
        )

    @staticmethod
    def get_site(parent=None, initial: dict | None = None):
        """Convenience: open the dialog and return (name, url, interval)
        or ``None`` when the user cancels."""
        dialog = SiteDialog(parent, initial)
        if dialog.exec() == QDialog.Accepted:
            return dialog.values()
        return None