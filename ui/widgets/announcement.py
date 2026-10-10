"""
ui/widgets/announcement.py
--------------------------
Плашка объявления на главной (core/announcements.py): значок, заголовок,
текст, ссылка и крестик. Закрытие — сигнал dismissed(id); запомнить его и
убрать плашку — дело DashboardTab.
"""

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ui.widgets.base import Glyph, button, glyph_font, label, set_tone
from ui.widgets.controls import IconButton


class AnnouncementBar(QFrame):
    dismissed = Signal(str)

    def __init__(self, item: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("announce")
        self.setProperty("tone", item["tone"])
        self.setProperty("aid", item["id"])
        self._id = item["id"]

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 14, 10, 14)
        row.setSpacing(14)

        icon = QLabel(Glyph.WARNING if item["tone"] == "warning" else Glyph.INFO)
        # Шрифт значков — в стиле самой надписи: общий QSS перебивает setFont()
        icon.setStyleSheet(f"font-family: '{glyph_font(18).family()}'; font-size: 18px;")
        set_tone(icon, "warning" if item["tone"] == "warning" else "accent")
        row.addWidget(icon, alignment=Qt.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        if item["title"]:
            texts.addWidget(label(item["title"], role="strong", wrap=True))
        texts.addWidget(label(item["text"], role="muted", wrap=True))
        row.addLayout(texts, stretch=1)

        if item["link"]:
            link = button(item["link_text"] + " ↗", variant="link")
            link.setToolTip(item["link"])
            link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(item["link"])))
            row.addWidget(link, alignment=Qt.AlignVCenter)

        close = IconButton(Glyph.CLOSE, "Скрыть — больше не покажется", size=28)
        close.clicked.connect(lambda: self.dismissed.emit(self._id))
        row.addWidget(close, alignment=Qt.AlignTop)
