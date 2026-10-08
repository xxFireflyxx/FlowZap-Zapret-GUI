"""
ui/widgets/controls.py
----------------------
Самописные контролы: переключатель, кнопка-иконка, круглый значок, поле
выбора, сегменты, точка статуса.

Цвета здесь не вшиваются в QSS: самописные виджеты читают theme.palette
прямо в paintEvent, остальные получают цвет из общего QSS по свойствам
role / tone / variant (см. ui/theme.build_stylesheet) — смена темы на лету
перекрашивает всё без пересоздания окна.
"""

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QPushButton, QSizePolicy, QWidget,
)

from ui.theme import theme, _mix
from ui.widgets.base import Glyph, button, glyph_font


class Switch(QAbstractButton):
    """Переключатель-тумблер. clicked эмитится только от пользователя;
    setChecked() — тихий. Если задан text — подпись справа, вся строка кликабельна.
    large=True — крупный тумблер плиток главной.
    Смена положения (и от клика, и от setChecked) — плавная: кружок
    переезжает, цвета дорожки и кружка перетекают (если включены анимации
    интерфейса; иначе и пока виджет не показан — сразу)."""

    _TRACK_W, _TRACK_H, _GAP = 40, 22, 10
    ANIM_MS = 220

    def __init__(self, text: str = "", parent=None, large: bool = False) -> None:
        super().__init__(parent)
        if large:
            self._TRACK_W, self._TRACK_H = 58, 32
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._pos = 0.0             # 0 — выкл, 1 — вкл; между — идёт анимация
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate_to)

    def _animate_to(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if not (theme.animations and self.isVisible()):
            self._anim.stop()
            self._pos = target
            self.update()
            return
        self._anim.stop()
        # смена start/end у QVariantAnimation сама эмитит valueChanged —
        # без блокировки кружок на кадр прыгал бы в конец
        self._anim.blockSignals(True)
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.blockSignals(False)
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._pos = float(value)
        self.update()

    def showEvent(self, event) -> None:
        # Пока скрыт, анимация не шла — положение могло отстать от состояния
        if not self._anim.state() == QVariantAnimation.Running:
            self._pos = 1.0 if self.isChecked() else 0.0
        super().showEvent(event)

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text()) + self._GAP if self.text() else 0
        return QSize(self._TRACK_W + text_w, max(self._TRACK_H, self.fontMetrics().height()) + 4)

    def paintEvent(self, _event) -> None:
        pal = theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        t = self._pos
        h = self._TRACK_H
        y = (self.height() - h) / 2
        track = QRectF(0.5, y + 0.5, self._TRACK_W - 1, h - 1)
        border = QColor(pal.border_strong)
        border.setAlphaF(border.alphaF() * (1 - t))     # у включённого рамки нет
        painter.setPen(QPen(border, 1) if t < 1 else Qt.NoPen)
        painter.setBrush(QColor(_mix(pal.bg_input, pal.accent, t)))
        painter.drawRoundedRect(track, h / 2, h / 2)

        inset = 4
        d = h - inset * 2
        x = inset + (self._TRACK_W - d - inset * 2) * t
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_mix(pal.text_muted, pal.accent_text, t)))
        painter.drawEllipse(QRectF(x, y + inset, d, d))

        if self.text():
            painter.setOpacity(1.0 if self.isEnabled() else 0.45)
            painter.setPen(QColor(pal.text_primary))
            painter.drawText(
                QRectF(self._TRACK_W + self._GAP, 0, self.width() - self._TRACK_W - self._GAP, self.height()),
                Qt.AlignVCenter | Qt.AlignLeft, self.text(),
            )


class IconButton(QAbstractButton):
    """Квадратная кнопка-значок (обновить, удалить, …) с подсветкой при наведении."""

    def __init__(self, glyph: str, tooltip: str = "", size: int = 32, danger: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self._danger = danger
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

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
        hovered = self.underMouse() and self.isEnabled()
        if hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal.error_soft if self._danger else pal.bg_hover))
            painter.drawRoundedRect(QRectF(self.rect()), 8, 8)
        if not self.isEnabled():
            color = pal.text_muted
        elif hovered:
            color = pal.error if self._danger else pal.text_primary
        else:
            color = pal.text_secondary
        painter.setPen(QColor(color))
        painter.setFont(glyph_font(max(12, self.height() // 2 - 1)))
        painter.drawText(self.rect(), Qt.AlignCenter, self._glyph)


class CircleBadge(QWidget):
    """Значок в круге — «аватар» плиток главной. active=True — акцентный цвет."""

    def __init__(self, glyph: str, size: int = 46, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._glyph = glyph
        self._active = False

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def paintEvent(self, _event) -> None:
        pal = theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_mix(pal.bg_card, pal.accent, 0.22) if self._active else pal.bg_input))
        painter.drawEllipse(QRectF(self.rect()))
        painter.setPen(QColor(pal.accent if self._active else pal.text_secondary))
        painter.setFont(glyph_font(self.height() // 2 - 3))
        painter.drawText(self.rect(), Qt.AlignCenter, self._glyph)


class FieldButton(QAbstractButton):
    """Кнопка в виде поля (фон как у полей ввода, высота 44): точка статуса
    слева, текст, приглушённое пояснение и значок справа (шеврон / стрелка).
    Для выбора из списка и действий внутри плиток."""

    def __init__(self, text: str = "", trailing: str = Glyph.CHEVRON, parent=None) -> None:
        super().__init__(parent)
        self.setText(text)
        self._secondary = ""
        self._dot: str | None = None
        self._trailing = trailing
        self._centered = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_secondary(self, text: str) -> None:
        self._secondary = text
        self.update()

    def set_dot(self, palette_attr: str | None) -> None:
        self._dot = palette_attr
        self.update()

    def set_centered(self, centered: bool) -> None:
        self._centered = centered
        self.update()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        pal, t = theme.palette, theme.typography
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        hovered = enabled and self.underMouse()
        painter.setPen(QPen(QColor(pal.border_strong if hovered else pal.border), 1))
        painter.setBrush(QColor(pal.bg_hover if hovered else pal.bg_input))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

        font = QFont(t.family_ui)
        font.setPixelSize(t.size_md)
        font.setWeight(QFont.DemiBold)
        sec_font = QFont(t.family_ui)
        sec_font.setPixelSize(t.size_xs)
        fm, sfm = QFontMetricsF(font), QFontMetricsF(sec_font)
        text_w = fm.horizontalAdvance(self.text())
        sec_w = sfm.horizontalAdvance(self._secondary) + 8 if self._secondary else 0
        trailing_w = 22 if self._trailing else 0
        dot_w = 20 if self._dot else 0

        right = self.width() - 14
        content_w = dot_w + text_w + sec_w + (trailing_w + 6 if self._centered and self._trailing else 0)
        x = (self.width() - content_w) / 2 if self._centered else 14
        h = self.height()

        if self._dot:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(getattr(pal, self._dot, pal.text_muted)))
            painter.drawEllipse(QRectF(x, h / 2 - 5, 10, 10))
            x += dot_w

        avail = right - x - (0 if self._centered else trailing_w + 6)
        painter.setFont(font)
        painter.setPen(QColor(pal.text_primary if enabled else pal.text_muted))
        shown = fm.elidedText(self.text(), Qt.ElideRight, max(0, avail - sec_w))
        painter.drawText(QRectF(x, 0, avail, h), Qt.AlignVCenter | Qt.AlignLeft, shown)
        x += fm.horizontalAdvance(shown)

        if self._secondary and x + 8 < right - trailing_w:
            painter.setFont(sec_font)
            painter.setPen(QColor(pal.text_muted))
            sec = sfm.elidedText(self._secondary, Qt.ElideRight, right - trailing_w - x - 8)
            painter.drawText(QRectF(x + 8, 0, right - x, h), Qt.AlignVCenter | Qt.AlignLeft, sec)
            x += 8 + sfm.horizontalAdvance(sec)

        if self._trailing:
            painter.setPen(QColor(pal.text_secondary if enabled else pal.text_muted))
            painter.setFont(glyph_font(11))
            tx = x + 6 if self._centered else right - trailing_w
            painter.drawText(QRectF(tx, 0, trailing_w, h), Qt.AlignCenter, self._trailing)


class SegmentedControl(QFrame):
    """Выбор одного из нескольких вариантов (TCP / UDP / Все).
    changed(key) — только по клику пользователя; set_current() — тихий."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in options:
            btn = button(text, variant="segment")
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(btn)
            layout.addWidget(btn)
            self._buttons[key] = btn

    def set_current(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)


class StatusDot(QWidget):
    """Цветная точка состояния; цвет — имя поля палитры (success, error, …)."""

    def __init__(self, size: int = 10, parent=None) -> None:
        super().__init__(parent)
        self._color_attr = "text_muted"
        self._size = size
        self.setFixedSize(size + 2, size + 2)

    def set_color(self, palette_attr: str) -> None:
        self._color_attr = palette_attr
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(getattr(theme.palette, self._color_attr, theme.palette.text_muted)))
        painter.drawEllipse(QRectF(1, 1, self._size, self._size))
