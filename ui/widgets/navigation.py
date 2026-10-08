"""
ui/widgets/navigation.py
------------------------
Вкладки главного окна: кнопка вкладки (с пульсирующей точкой) и полоса вкладок.
"""

import time

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QHBoxLayout

from ui.theme import theme


class TabButton(QAbstractButton):
    """Вкладка верхней панели — «таблетка»; set_badge(True) — точка «есть новое».
    С анимациями интерфейса от точки расходятся затухающие кольца (пульс)."""

    PULSE_SEC = 1.6

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setText(text)
        self._badge = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self._pulse_t0 = time.monotonic()
        self._pulse = QTimer(self)
        self._pulse.setInterval(40)
        self._pulse.timeout.connect(self._pulse_tick)

    def set_badge(self, visible: bool) -> None:
        if visible and not self._badge:
            self._pulse_t0 = time.monotonic()
        self._badge = visible
        self.updateGeometry()
        self.update()

    def _dot_center(self) -> QPointF:
        text_w = QFontMetricsF(self._font()).horizontalAdvance(self.text())
        return QPointF(18 + text_w + 9, self.height() / 2)

    def _pulse_tick(self) -> None:
        win = self.window()
        if not (self._badge and theme.animations and self.isVisible()) or win.isMinimized():
            self._pulse.stop()      # перезапустится из paintEvent, когда снова понадобится
            self.update()
            return
        c = self._dot_center()
        self.update(QRectF(c.x() - 11, c.y() - 11, 22, 22).toAlignedRect())

    def _font(self) -> QFont:
        font = QFont(theme.typography.family_ui)
        font.setPixelSize(theme.typography.size_md)
        font.setWeight(QFont.DemiBold)   # ширина не прыгает при выборе
        return font

    def sizeHint(self) -> QSize:
        w = QFontMetricsF(self._font()).horizontalAdvance(self.text())
        return QSize(int(w) + 36 + (13 if self._badge else 0), 36)

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
        rect = QRectF(self.rect())
        font = self._font()
        if not self.isChecked():
            font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor(pal.text_primary if self.isChecked() or self.underMouse() else pal.text_secondary))
        text_w = QFontMetricsF(self._font()).horizontalAdvance(self.text())
        x = 18
        painter.drawText(QRectF(x, 0, text_w + 2, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        if self._badge:
            center = QPointF(x + text_w + 9, rect.height() / 2)
            color = QColor(pal.warning)
            painter.setPen(Qt.NoPen)
            if theme.animations:
                if not self._pulse.isActive():
                    self._pulse.start()
                phase = ((time.monotonic() - self._pulse_t0) % self.PULSE_SEC) / self.PULSE_SEC
                ease = 1 - (1 - phase) ** 3          # кольцо быстро вылетает и медленно тает
                halo = QColor(color)
                halo.setAlphaF(0.55 * (1 - phase))
                painter.setBrush(halo)
                r = 3.5 + 6.5 * ease
                painter.drawEllipse(center, r, r)
            painter.setBrush(color)
            painter.drawEllipse(center, 3.5, 3.5)


class TabBar(QFrame):
    """Горизонтальные вкладки в шапке окна. currentChanged(index) — по клику.
    Подсветку выбранной вкладки («таблетку») рисует сама панель — при смене
    вкладки она плавно переезжает (если включены анимации интерфейса)."""

    currentChanged = Signal(int)

    def __init__(self, titles: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tabs")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[TabButton] = []
        for index, title in enumerate(titles):
            btn = TabButton(title)
            btn.clicked.connect(lambda _=False, i=index: self.currentChanged.emit(i))
            self._group.addButton(btn)
            layout.addWidget(btn)
            self._buttons.append(btn)
        if self._buttons:
            self._buttons[0].setChecked(True)
        self._current = 0
        self._pill: QRectF | None = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(340)
        # Плавный разгон: первый кадр анимации в Qt приходит с задержкой (~30 мс),
        # и при резком старте (OutCubic) подсветка делала на нём большой скачок.
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.valueChanged.connect(self._on_pill_moved)

    def set_current(self, index: int) -> None:
        self._buttons[index].setChecked(True)
        if index == self._current:
            return
        self._current = index
        target = QRectF(self._buttons[index].geometry())
        if theme.animations and self._pill is not None and self.isVisible():
            aurora = getattr(self.window(), "aurora", None)
            if aurora is not None:
                aurora.hold(self._anim.duration() / 1000 + 0.05)
            self._anim.stop()
            # Смена start/end у остановленной анимации сразу шлёт valueChanged с
            # конечной точкой — подсветка на кадр прыгала в цель и обратно.
            self._anim.blockSignals(True)
            self._anim.setStartValue(self._pill)
            self._anim.setEndValue(target)
            self._anim.blockSignals(False)
            self._anim.start()
        else:
            self._pill = target
            self.update()

    def _on_pill_moved(self, rect) -> None:
        self._pill = rect
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._anim.stop()
        self._pill = None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._buttons:
            return
        if self._pill is None:
            self._pill = QRectF(self._buttons[self._current].geometry())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.palette.bg_card))
        r = self._pill.height() / 2
        painter.drawRoundedRect(self._pill, r, r)

    def set_badge(self, index: int, visible: bool) -> None:
        self._buttons[index].set_badge(visible)
