"""
ui/widgets/animation.py
-----------------------
Анимации интерфейса (выключаются в «Настройках»): волна по клику, каскад
«блоки всплывают» при открытии вкладки, встряска при ошибке, полоса проверки.
"""

import math
import time

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QRectF, QSequentialAnimationGroup, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QGraphicsEffect, QWidget

from ui.theme import theme


class RippleOverlay(QWidget):
    """Волна по карточке: круг цвета акцента расходится из точки и гаснет.
    Прозрачен для мыши; обрезается по скруглению карточки."""

    def __init__(self, parent: QWidget, radius: float = 16) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._radius = radius
        self._center = QPointF()
        self._t = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self.hide)
        self.hide()

    def play(self, center: QPointF) -> None:
        if not theme.animations:
            return
        self.setGeometry(self.parentWidget().rect())
        self._center = center
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.start()

    def _step(self, t) -> None:
        self._t = float(t)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), self._radius, self._radius)
        painter.setClipPath(clip)
        reach = math.hypot(max(self._center.x(), self.width() - self._center.x()),
                           max(self._center.y(), self.height() - self._center.y()))
        color = QColor(theme.palette.accent)
        color.setAlphaF((0.30 if theme.palette.is_dark else 0.20) * (1 - self._t))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        r = 10 + reach * self._t
        painter.drawEllipse(self._center, r, r)


class _RiseEffect(QGraphicsEffect):
    """Блок «всплывает»: прозрачность и сдвиг вниз уходят к нулю. Рисует
    готовую картинку виджета со сдвигом — сам виджет не двигается, поэтому
    раскладку не ломают ни быстрые переключения вкладок, ни смена размера."""

    def __init__(self, parent=None, lift: int = 18) -> None:
        super().__init__(parent)
        self._t = 0.0
        self.LIFT = lift

    def set_progress(self, t: float) -> None:
        self._t = t
        self.update()

    def boundingRectFor(self, rect):
        return QRectF(rect).adjusted(0, 0, 0, self.LIFT)

    def draw(self, painter) -> None:
        if self._t >= 1.0:
            self.drawSource(painter)
            return
        # drawSource не учитывает прозрачность и сдвиг кисти — рисуем картинку сами
        pix = self.sourcePixmap(Qt.LogicalCoordinates, mode=QGraphicsEffect.PixmapPadMode.NoPad)
        offset = self.sourceBoundingRect(Qt.LogicalCoordinates).topLeft()
        painter.save()
        painter.setOpacity(self._t)
        painter.drawPixmap(offset + QPointF(0, (1.0 - self._t) * self.LIFT), pix)
        painter.restore()


def page_blocks(page: QWidget) -> list[QWidget]:
    """Крупные блоки страницы (карточки и плитки) в порядке появления:
    сверху вниз, в одном ряду — слева направо. Вложенные карточки не в счёт."""
    blocks = []
    for w in page.findChildren(QFrame):
        if w.objectName() not in ("card", "tile") or not w.isVisibleTo(page):
            continue
        parent, nested = w.parentWidget(), False
        while parent is not None and parent is not page:
            if parent.objectName() in ("card", "tile"):
                nested = True
                break
            parent = parent.parentWidget()
        if not nested:
            blocks.append(w)
    pos = {w: w.mapTo(page, QPoint(0, 0)) for w in blocks}
    return sorted(blocks, key=lambda w: (pos[w].y() // 24, pos[w].x()))


def page_steps(page: QWidget) -> list[list[QWidget]]:
    """Ступени каскада: блоки одной ступени всплывают вместе. Если на
    странице несколько колонок, карточки одной колонки (то же x и та же
    ширина, друг под другом) — одна ступень: иначе в «Настройках» карточки
    разной высоты появлялись зигзагом по одной. Плитки в ряд и блок на всю
    ширину под ними (главная) остаются отдельными ступенями, как в макете."""
    blocks = page_blocks(page)
    if page.property("cascade") == "together":
        return [blocks] if blocks else []      # страница всплывает целиком
    xs = {w.mapTo(page, QPoint(0, 0)).x() for w in blocks}
    if len(xs) < 2:
        return [[w] for w in blocks]
    steps: list[list[QWidget]] = []
    columns: dict[tuple[int, int], list[QWidget]] = {}
    for w in blocks:
        key = (w.mapTo(page, QPoint(0, 0)).x(), w.width())
        if key in columns:
            columns[key].append(w)
        else:
            columns[key] = [w]
            steps.append(columns[key])
    return steps


def cascade_in(steps: list[list[QWidget]], first_delay_ms: int = 60, step_ms: int = 90,
               duration_ms: int = 550, lift: int = 18) -> None:
    """Каскад появления (как в макете): ступени (см. page_steps) по очереди
    всплывают на lift px и проявляются за duration_ms. При запуске окна —
    медленнее и с большей высоты (приветствие). Повторный вызов прерывает
    предыдущий."""
    if not theme.animations:
        return
    for i, w in ((i, w) for i, step in enumerate(steps) for w in step):
        old = getattr(w, "_fz_rise", None)
        if old is not None:
            old.stop()
        effect = _RiseEffect(w, lift)
        w._fz_rise_effect = effect          # без ссылки Python-обёртку соберёт GC
        w.setGraphicsEffect(effect)

        anim = QVariantAnimation(w)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(duration_ms)
        anim.setEasingCurve(QEasingCurve.OutQuart)
        anim.valueChanged.connect(lambda v, e=effect: e.set_progress(float(v)))
        group = QSequentialAnimationGroup(w)
        group.addPause(first_delay_ms + i * step_ms)
        group.addAnimation(anim)

        def _done(w=w, group=group) -> None:
            if getattr(w, "_fz_rise", None) is group:
                w.setGraphicsEffect(None)      # без эффекта Qt рисует виджет напрямую
                w._fz_rise = None
                w._fz_rise_effect = None

        group.finished.connect(_done)
        w._fz_rise = group
        group.start()


def shake(widget: QWidget) -> None:
    """Короткая тряска по горизонтали (ошибка) — затухающая синусоида, ~0.55 с."""
    if not theme.animations or getattr(widget, "_shaking", False):
        return
    widget._shaking = True
    base = widget.pos()
    anim = QVariantAnimation(widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(550)

    def step(t):
        t = float(t)
        widget.move(base.x() + round(math.sin(t * math.pi * 6) * 7 * (1 - t)), base.y())

    def done():
        widget.move(base)
        widget._shaking = False

    anim.valueChanged.connect(step)
    anim.finished.connect(done)
    anim.start(QVariantAnimation.DeleteWhenStopped)


class CheckProgress(QWidget):
    """Полоса проверки пресетов. Стартует с нуля и едет плавно: пока
    проверяется пресет №k, заполнение равномерно ползёт к k/N так, чтобы
    дойти до него примерно к концу шага (STEP_SEC). Пресет закончился раньше —
    полоса догоняет быстрее. Незаполненная часть «дышит», по ней бежит блик —
    видно, что работа идёт. Без анимаций — просто доля готовых."""

    STEP_SEC = 7.0       # ~5–8 с на пресет: ждём готовности winws + HTTP-проверки (core/zapret/preset_checker.py)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(8)
        self._total = 1
        self._done = 0
        self._shown = 0.0
        self._t0 = time.monotonic()
        self._last = self._t0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self, total: int) -> None:
        self._total = max(1, total)
        self._done = 0
        self._shown = 0.0
        self._t0 = self._last = time.monotonic()
        self.show()
        self._timer.start()
        self.update()

    def set_done(self, done: int) -> None:
        """Сколько пресетов уже проверено (0…N); сейчас идёт пресет done+1."""
        self._done = max(self._done, min(done, self._total))

    def finish(self) -> None:
        self._done = self._total
        if not theme.animations:
            self.stop()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = now - self._last, now
        done_frac = self._done / self._total
        cap = min(1.0, (self._done + 1) / self._total)
        if self._shown < done_frac:                       # шаг закончился раньше — догоняем
            self._shown = min(done_frac, self._shown + max(0.6 / self._total, (done_frac - self._shown) * 4) * dt)
        else:                                             # ползём к концу текущего шага
            self._shown = min(cap, self._shown + dt / (self.STEP_SEC * self._total))
        if self._done >= self._total and self._shown >= 0.999:
            self._timer.stop()
            QTimer.singleShot(400, self.stop)
        self.update()

    def paintEvent(self, _event) -> None:
        pal = theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        r = h / 2
        frac = self._shown if theme.animations else self._done / self._total
        fill_w = w * frac
        t = time.monotonic() - self._t0
        accent = QColor(pal.accent)

        # дорожка (незаполненная часть): «дыхание» + бегущий блик
        track = QColor(accent)
        breath = 0.5 + 0.5 * math.sin(t * math.pi / 1.1) if theme.animations else 0.5
        track.setAlphaF(0.08 + 0.10 * breath)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        rest = QRectF(fill_w, 0, w - fill_w, h)
        if theme.animations and rest.width() > 4:
            span = max(24.0, rest.width() * 0.25)
            x = rest.x() - span + (rest.width() + span) * ((t % 1.8) / 1.8)
            painter.save()
            painter.setClipRect(rest)
            painter.setBrush(_glint(x, span, accent, 0.5))
            painter.drawRoundedRect(QRectF(0, 0, w, h), r, r)
            painter.restore()

        # заполненная часть + белый блик по ней
        if fill_w > 0:
            fill = QRectF(0, 0, fill_w, h)
            painter.setBrush(accent)
            painter.drawRoundedRect(fill, r, r)
            if theme.animations and fill_w > 20:
                span = max(30.0, fill_w * 0.4)
                x = -span + (fill_w + span) * ((t % 1.3) / 1.3)
                painter.save()
                painter.setClipRect(fill)
                painter.setBrush(_glint(x, span, QColor(255, 255, 255), 0.45))
                painter.drawRoundedRect(fill, r, r)
                painter.restore()


def _glint(x: float, span: float, color: QColor, peak: float) -> QLinearGradient:
    grad = QLinearGradient(x, 0, x + span, 0)
    c = QColor(color)
    c.setAlphaF(0.0)
    grad.setColorAt(0.0, c)
    c.setAlphaF(peak)
    grad.setColorAt(0.5, c)
    c.setAlphaF(0.0)
    grad.setColorAt(1.0, c)
    return grad
