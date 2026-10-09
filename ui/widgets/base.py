"""
ui/widgets/base.py
------------------
Базовые помощники: значки-глифы, подписи и кнопки по ролям,
перекраска по свойствам (restyle/set_tone), разделитель, точка-иконка.

Цвета здесь не вшиваются в QSS: самописные виджеты читают theme.palette
прямо в paintEvent, остальные получают цвет из общего QSS по свойствам
role / tone / variant (см. ui/theme.build_stylesheet) — смена темы на лету
перекрашивает всё без пересоздания окна.
"""

from PySide6.QtCore import QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QIcon
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QWidget

from ui.theme import theme


class Glyph:
    """Коды значков шрифта Segoe MDL2 Assets (есть в Windows 10 и 11)."""
    HOME      = ""
    SLIDERS   = ""
    SYNC      = ""
    SETTINGS  = ""
    REFRESH   = ""
    ADD       = ""
    DELETE    = ""
    CLOSE     = ""
    EDIT      = ""
    FOLDER    = ""
    PLAY      = ""
    STOP      = ""
    CHEVRON   = ""
    CHECK     = ""
    INFO      = ""
    GAME      = ""
    GLOBE     = ""
    SEND      = ""
    SHIELD    = ""
    DOWNLOAD  = ""
    OPEN      = ""
    COPY      = ""
    WARNING   = ""
    PALETTE   = ""
    MONITOR   = ""
    LIST      = ""


def restyle(widget: QWidget) -> None:
    """Пере-применить QSS после смены динамического свойства (role/tone/...)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_tone(label: QWidget, tone: str | None) -> None:
    """Цвет текста по смыслу: success / warning / error / accent / muted / None."""
    if label.property("tone") == (tone or ""):
        return
    label.setProperty("tone", tone or "")
    restyle(label)


class WrapLabel(QLabel):
    """Надпись с переносом строк, которая не обрезает последнюю строку.

    Layout считает высоту такой надписи по её sizeHint (ширина «как в одну
    строку»), а не по реальной ширине — в узкой колонке (например, в
    SettingRow) строк выходит больше, и нижняя обрезалась. Здесь минимальная
    высота подгоняется под настоящую ширину при каждом изменении размера и
    шрифта (QSS по role меняет его уже после первой раскладки)."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def _fit_height(self) -> None:
        # Не heightForWidth(): у QLabel он даёт лишнюю строку (460 px — 4
        # строки вместо 3), и под текстом оставалась пустота
        m = self.contentsMargins()
        width = self.width() - m.left() - m.right() - 2 * self.margin()
        if width <= 0:
            return
        rect = self.fontMetrics().boundingRect(
            QRect(0, 0, width, 100000), int(Qt.TextWordWrap | self.alignment()), self.text())
        h = rect.height() + m.top() + m.bottom() + 2 * self.margin()
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_height()

    def setText(self, text: str) -> None:
        super().setText(text)
        self._fit_height()


def label(text: str = "", role: str | None = None, tone: str | None = None,
          wrap: bool = False) -> QLabel:
    lbl = WrapLabel(text) if wrap else QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if tone:
        lbl.setProperty("tone", tone)
    return lbl


class AutoHideLabel(QLabel):
    """Строка статуса, которая не занимает места, пока в ней нет текста."""

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setVisible(bool(text))


def button(text: str, variant: str | None = None, size: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    if variant:
        btn.setProperty("variant", variant)
    if size:
        btn.setProperty("size", size)
    return btn


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


def glyph_font(px: int) -> QFont:
    font = QFont(theme.typography.family_icon)
    font.setPixelSize(px)
    return font


def dot_icon(color: str, size: int = 10) -> QIcon:
    """Цветная точка — иконка для строк QListWidget."""
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    painter.end()
    icon = QIcon()
    # Одинаковая картинка для всех режимов — иначе стиль тонирует
    # иконку выделенной строки цветом выделения.
    for mode in (QIcon.Normal, QIcon.Selected, QIcon.Active):
        icon.addPixmap(pix, mode)
    return icon
