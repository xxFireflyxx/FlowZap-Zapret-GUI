"""
ui/widgets/popup.py
-------------------
Всплывающие панели (Qt.Popup — Qt сам закрывает их по клику мимо, при
переключении окна и сворачивании) и значок «?» с подсказкой (HelpIcon).
"""

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QFrame, QHBoxLayout, QVBoxLayout, QWidget,
)

from ui.theme import theme
from ui.widgets.base import label
from ui.widgets.controls import StatusDot


class _Popup(QFrame):
    """Qt.Popup, который закрывается повторным кликом по своей кнопке.
    Обычно клик мимо попапа закрывает его и тут же «переигрывается» на
    виджет под курсором — кнопка получала клик и открывала попап снова.
    Для клика по самой кнопке переигрывание отключаем."""

    def __init__(self, anchor: QWidget) -> None:
        super().__init__(anchor, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self._anchor = anchor

    def mousePressEvent(self, event) -> None:
        pos = event.globalPosition().toPoint()
        anchor_rect = QRect(self._anchor.mapToGlobal(QPoint(0, 0)), self._anchor.size())
        if not self.rect().contains(event.position().toPoint()) and anchor_rect.contains(pos):
            self.setAttribute(Qt.WA_NoMouseReplay)
        super().mousePressEvent(event)


def make_popup(anchor: QWidget) -> tuple[QFrame, QVBoxLayout]:
    """Всплывающая панель со скруглёнными углами (Qt.Popup закрывается
    сам по клику мимо, а по своей кнопке — без повторного открытия).
    Возвращает (окно, раскладка содержимого)."""
    outer = _Popup(anchor)
    outer.setAttribute(Qt.WA_TranslucentBackground)
    outer.setAttribute(Qt.WA_DeleteOnClose)
    outer_layout = QVBoxLayout(outer)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    inner = QFrame()
    inner.setObjectName("popup")
    outer_layout.addWidget(inner)
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(2)
    return outer, layout


def show_popup_below(popup: QWidget, anchor: QWidget, gap: int = 6) -> None:
    """Показать поп-ап у anchor, не выходя за границы окна приложения:
    под ним, а если снизу места мало — над ним; правый край — по окну.
    Если не влезает ни туда, ни туда — уменьшаем высоту (списки прокрутятся)."""
    popup.adjustSize()
    screen = (anchor.screen() or QApplication.primaryScreen()).availableGeometry()
    window = anchor.window()
    top_left = window.mapToGlobal(QPoint(0, 0))
    bounds = QRect(top_left, window.size()).adjusted(8, 8, -8, -8).intersected(screen)

    a_top = anchor.mapToGlobal(QPoint(0, 0)).y()
    a_bottom = a_top + anchor.height()
    below = bounds.bottom() - (a_bottom + gap)
    above = (a_top - gap) - bounds.top()
    h = popup.height()
    if h > below and above > below:
        h = min(h, above)
        y = a_top - gap - h
    else:
        h = min(h, below)
        y = a_bottom + gap
    if h != popup.height():
        popup.setFixedHeight(max(80, h))

    x = anchor.mapToGlobal(QPoint(0, 0)).x()
    x = min(x, bounds.right() - popup.width())
    popup.move(max(bounds.left(), x), y)
    popup.show()


class HelpIcon(QAbstractButton):
    """Значок «?». legend: [(palette_attr, подпись), ...]."""

    def __init__(self, text: str, legend: list | None = None, popup_width: int = 280, parent=None) -> None:
        super().__init__(parent)
        self._help_text = text
        self._legend = legend or []
        self._popup_width = popup_width
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Подсказка")
        self.clicked.connect(self._open_popup)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        pal = theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(pal.accent if self.underMouse() else pal.text_muted)
        painter.setPen(QPen(color, 1.3))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(1.5, 1.5, self.width() - 3, self.height() - 3))
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, "?")

    def _open_popup(self) -> None:
        popup, layout = make_popup(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        text = label(self._help_text, role="muted", wrap=True)
        text.setFixedWidth(self._popup_width)
        layout.addWidget(text)
        if self._legend:
            layout.addSpacing(2)
        for color_attr, caption in self._legend:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = StatusDot(9)
            dot.set_color(color_attr)
            row.addWidget(dot)
            row.addWidget(label(caption, role="muted"))
            row.addStretch(1)
            layout.addLayout(row)
        show_popup_below(popup, self)
