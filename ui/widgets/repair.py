"""
ui/widgets/repair.py
--------------------
Окно подтверждения исправления (core/repairs.py): что сломано и что будет
сделано, текст скрипта по кнопке, с какими правами он выполнится.
Выполняет скрипт не окно, а DashboardTab — после accept().
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from ui.theme import theme
from ui.widgets.base import button, label


class RepairDialog(QDialog):
    def __init__(self, item: dict, elevated: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FlowZap — исправление")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(label(item["title"], role="section", wrap=True))
        layout.addWidget(label(item["text"], role="muted", wrap=True))

        rights = "с правами администратора (FlowZap запущен от администратора)" if elevated \
            else "с вашими правами, без прав администратора"
        notes = [f"Исправление подписано автором FlowZap — подпись проверена. Выполнится {rights}."]
        if item.get("exit_flowzap"):
            notes.append("FlowZap закроется на время исправления и запустится снова.")
        layout.addWidget(label(" ".join(notes), role="hint", wrap=True))

        self._btn_script = button("Показать скрипт", variant="link")
        self._btn_script.clicked.connect(self._toggle_script)
        layout.addWidget(self._btn_script, alignment=Qt.AlignLeft)
        self._script = QPlainTextEdit(item["script"].strip("\r\n"))
        self._script.setReadOnly(True)
        # С переносом: горизонтальная прокрутка в теме спрятана (theme.py), а
        # скрипт, который выполнится, человек должен видеть целиком
        self._script.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self._script.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        # Шрифт — стилем самого поля: общий QSS (* { font-family }) перебивает setFont()
        self._script.setStyleSheet(
            f"font-family: '{theme.typography.family_mono}', 'Consolas', monospace; font-size: 12px;")
        self._script.setMinimumHeight(220)
        self._script.hide()
        layout.addWidget(self._script)

        buttons = QDialogButtonBox()
        apply = buttons.addButton("Применить", QDialogButtonBox.AcceptRole)
        apply.setProperty("variant", "primary")
        cancel = buttons.addButton("Не сейчас", QDialogButtonBox.RejectRole)
        for b in (apply, cancel):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(110)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_script(self) -> None:
        shown = not self._script.isVisible()
        self._script.setVisible(shown)
        self._btn_script.setText("Скрыть скрипт" if shown else "Показать скрипт")
        self.adjustSize()
