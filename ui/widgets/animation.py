"""
ui/widgets/animation.py
-----------------------
Анимации интерфейса (выключаются в «Настройках»): заставка при запуске,
волна по клику, каскад «блоки всплывают» при открытии вкладки, встряска при
ошибке, полоса проверки.
"""

import math
import time

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPointF, QRectF, QSequentialAnimationGroup, QSize, Qt, QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsEffect, QWidget

from ui.theme import theme


class IntroSplash(QWidget):
    """Заставка при запуске: значок и «FlowZap» по центру окна проявляются,
    держатся и растворяются, чуть поднимаясь. on_fade_out зовётся в начале
    растворения — блоки страницы начинают всплывать, пока логотип тает.
    Прозрачна для мыши; закрывает собой parent и следит за его размером."""

    # Пауза — сначала видно само окно с сиянием, потом рождается логотип
    DELAY_MS, FADE_IN_MS, HOLD_MS, FADE_OUT_MS = 250, 1200, 900, 650
    SCALE_FROM = 0.90
    ICON = 84
    RISE = 16          # на сколько px логотип поднимается, растворяясь

    def __init__(self, parent: QWidget, icon: QIcon, on_fade_out) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._icon = icon
        self._pix = None            # картинка логотипа (_logo) — один раз
        self._on_fade_out = on_fade_out
        self._opacity = 0.0
        self._scale = self.SCALE_FROM
        self._lift = 0.0
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

        fade_in = QVariantAnimation(self)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setDuration(self.FADE_IN_MS)
        fade_in.valueChanged.connect(self._step_in)      # линейно; кривые — в _step_in
        self._ease_opacity = QEasingCurve(QEasingCurve.InOutSine)
        self._ease_scale = QEasingCurve(QEasingCurve.OutCubic)
        fade_out = QVariantAnimation(self)
        fade_out.setStartValue(0.0)
        fade_out.setEndValue(1.0)
        fade_out.setDuration(self.FADE_OUT_MS)
        fade_out.setEasingCurve(QEasingCurve.InOutCubic)
        fade_out.valueChanged.connect(self._step_out)
        self._group = QSequentialAnimationGroup(self)
        self._group.addPause(self.DELAY_MS)
        self._group.addAnimation(fade_in)
        self._group.addPause(self.HOLD_MS)
        self._group.addAnimation(fade_out)
        self._group.currentAnimationChanged.connect(
            lambda a: a is fade_out and self._on_fade_out and self._on_fade_out())
        self._group.finished.connect(self._finish)

    def start(self) -> None:
        self.raise_()
        self.show()
        self._group.start()

    def _step_in(self, t) -> None:
        """Прозрачность — мягко с обоих концов (логотип не «вспыхивает» в
        первые доли секунды), приближение — быстрее в начале и плавно в конце."""
        t = float(t)
        self._opacity = self._ease_opacity.valueForProgress(t)
        self._scale = self.SCALE_FROM + (1.0 - self.SCALE_FROM) * self._ease_scale.valueForProgress(t)
        self.update()

    def _step_out(self, t) -> None:
        t = float(t)
        self._opacity, self._lift = 1.0 - t, self.RISE * t
        self.update()

    def _finish(self) -> None:
        self.parentWidget().removeEventFilter(self)
        self.hide()
        self.deleteLater()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
        return False

    GAP, TEXT_H, TEXT_W = 14, 40, 220
    SUPERSAMPLE = 2    # картинка логотипа с запасом чёткости — на экране она всегда уменьшается

    def _logo(self) -> QPixmap:
        """Значок и надпись — одной картинкой, один раз. Все кадры масштабируют
        эту картинку одинаково: если рисовать текст на каждом кадре, Qt на
        масштабе ровно 100 % переключается на текст с привязкой к пикселям, и
        в последний момент приближения надпись дёргается (а до этого кегль
        округлялся до целых пикселей — 28 → 29 → 30)."""
        k = self.devicePixelRatioF() * self.SUPERSAMPLE
        if self._pix is not None and self._pix.devicePixelRatio() == k:
            return self._pix
        w, h = self.TEXT_W, self.ICON + self.GAP + self.TEXT_H
        pix = QPixmap(round(w * k), round(h * k))
        pix.setDevicePixelRatio(k)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        icon = self._icon.pixmap(QSize(self.ICON, self.ICON), k)
        p.drawPixmap(QRectF((w - self.ICON) / 2, 0, self.ICON, self.ICON), icon, QRectF(icon.rect()))
        title = QFont(theme.typography.family_ui)
        title.setPixelSize(30)
        title.setWeight(QFont.DemiBold)
        p.setFont(title)
        p.setPen(QColor(theme.palette.text_primary))
        p.drawText(QRectF(0, self.ICON + self.GAP, w, self.TEXT_H), Qt.AlignHCenter | Qt.AlignTop, "FlowZap")
        p.end()
        self._pix = pix
        return pix

    def paintEvent(self, _event) -> None:
        if self._opacity <= 0.0:
            return
        logo = self._logo()
        w, h = self.TEXT_W * self._scale, (self.ICON + self.GAP + self.TEXT_H) * self._scale
        # Центр значка — на 30 px выше середины окна; группа приближается вокруг своего центра
        icon_cy = self.height() / 2 - 30 - self._lift
        group_cy = icon_cy - self.ICON / 2 + (self.ICON + self.GAP + self.TEXT_H) / 2
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(self._opacity)
        p.drawPixmap(QRectF(self.width() / 2 - w / 2, group_cy - h / 2, w, h), logo, QRectF(logo.rect()))
        p.end()


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

    # В конце кривой блок долго ползёт по долям пикселя, его картинку на
    # каждом кадре по-разному размывает (см. draw), и он «рябит». Поэтому
    # последние SNAP_PX не рисуем — блок встаёт на место и дальше только
    # проявляется, чётким. 0.5 px — прыжок и смена резкости заметны; 0.15 —
    # картинка уже почти чёткая, переход не виден. MOVE_END < 1 ускоряет
    # подъём, но тогда резче и начало — оставлено 1.0.
    MOVE_END = 1.0
    SNAP_PX = 0.15
    _EASE = QEasingCurve(QEasingCurve.OutQuart)

    def __init__(self, parent=None, lift: int = 18) -> None:
        super().__init__(parent)
        self._t = 0.0           # прозрачность
        self._shift = float(lift)
        self.LIFT = lift

    def set_progress(self, t: float) -> None:
        """t — линейное время анимации 0…1; кривые — здесь."""
        self._t = self._EASE.valueForProgress(t) if t < 1.0 else 1.0
        shift = (1.0 - self._EASE.valueForProgress(min(1.0, t / self.MOVE_END))) * self.LIFT
        self._shift = shift if shift >= self.SNAP_PX else 0.0
        self.update()

    def set_state(self, opacity: float, shift: float) -> None:
        """Прозрачность и сдвиг напрямую — для исчезновения (fade_out): кривые
        всплытия, прокрученные назад, давали «стоит, потом резко пропадает»."""
        self._t = min(opacity, 0.999)       # 1.0 = рисовать как есть, без сдвига
        self._shift = shift
        self.update()

    def boundingRectFor(self, rect):
        return QRectF(rect).adjusted(0, -1, 0, self.LIFT + 1)

    def draw(self, painter) -> None:
        if self._t >= 1.0:
            self.drawSource(painter)
            return
        if self._shift == 0.0:
            # На месте, только проявляется — без масштаба и размытия, чётко
            pix = self._snapshot()
            painter.save()
            painter.setOpacity(self._t)
            painter.drawPixmap(self.sourceBoundingRect(Qt.LogicalCoordinates).topLeft()
                               - QPointF(0, 1.0 / pix.devicePixelRatio()), pix)
            painter.restore()
            return
        # drawSource не учитывает прозрачность и сдвиг кисти — рисуем картинку сами
        pix = self._snapshot()
        offset = self.sourceBoundingRect(Qt.LogicalCoordinates).topLeft()
        pad = 1.0 / pix.devicePixelRatio()
        painter.save()
        painter.setOpacity(self._t)
        # Сдвиг на доли пикселя. Без этого Qt ставит картинку на целые пиксели,
        # и в медленном конце подъёма (при запуске окна) блок несколько кадров
        # стоит, потом прыгает на пиксель — заметные рывки. Чистый сдвиг Qt
        # всегда округляет, поэтому чуть-чуть масштабируем (на глаз не видно):
        # тогда работает сглаживание. Прозрачная рамка — чтобы края тоже
        # смешивались, а не «прилипали» к пикселям.
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(offset.x(), offset.y() - pad + self._shift)
        painter.scale(1.0, 1.0000001)
        painter.drawPixmap(0, 0, pix)
        painter.restore()

    SNAPSHOT_MS = 200

    def _snapshot(self) -> QPixmap:
        """Картинка блока, пока он всплывает. Qt заново рисует содержимое блока
        на каждом кадре (фон-сияние под ним перерисовывается постоянно), и
        кадры выходят то лёгкими, то тяжёлыми — движение неровное. Поэтому
        берём картинку раз в SNAPSHOT_MS: если в блоке что-то поменялось,
        это видно с задержкой не больше неё, а в конце блок рисуется как есть."""
        now = time.monotonic()
        if getattr(self, "_snap", None) is None or now - self._snap_at > self.SNAPSHOT_MS / 1000:
            self._snap = self._padded(self.sourcePixmap(Qt.LogicalCoordinates, mode=QGraphicsEffect.PixmapPadMode.NoPad))
            self._snap_at = now
        return self._snap

    def _padded(self, pix: QPixmap) -> QPixmap:
        """Картинка блока с прозрачной строкой пикселей сверху и снизу.
        Кэш по ключу картинки: Qt отдаёт ту же, пока блок не перерисовался."""
        key = pix.cacheKey()
        if getattr(self, "_pad_key", None) != key:
            padded = QPixmap(pix.width(), pix.height() + 2)
            padded.setDevicePixelRatio(pix.devicePixelRatio())
            padded.fill(Qt.transparent)
            p = QPainter(padded)
            p.drawPixmap(QPointF(0, 1.0 / pix.devicePixelRatio()), pix)
            p.end()
            self._pad_key, self._pad_pix = key, padded
        return self._pad_pix


def page_blocks(page: QWidget) -> list[QWidget]:
    """Крупные блоки страницы (карточки и плитки) в порядке появления:
    сверху вниз, в одном ряду — слева направо. Вложенные карточки не в счёт."""
    blocks = []
    for w in page.findChildren(QFrame):
        if w.objectName() not in ("card", "tile", "announce") or not w.isVisibleTo(page):
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


def cascade_hide(steps: list[list[QWidget]]) -> None:
    """Спрятать блоки до первой отрисовки окна — иначе при запуске они на миг
    видны на местах, а потом пропадают и «всплывают». Ступени тут нужны только
    как список блоков: раскладка в showEvent ещё не окончательная, поэтому
    сам каскад (cascade_in) считает их позже."""
    if not theme.animations:
        return
    for w in (w for step in steps for w in step):
        old = getattr(w, "_fz_rise", None)
        if old is not None:
            old.stop()
            w._fz_rise = None
        effect = _RiseEffect(w)
        w._fz_rise_effect = effect          # без ссылки Python-обёртку соберёт GC
        w.setGraphicsEffect(effect)


def cascade_in(steps: list[list[QWidget]], first_delay_ms: int = 60, step_ms: int = 90,
               duration_ms: int = 550, lift: int = 18) -> None:
    """Каскад появления (как в макете): ступени (см. page_steps) по очереди
    всплывают на lift px и проявляются за duration_ms. При запуске окна —
    медленнее и с большей высоты (приветствие). Повторный вызов прерывает
    предыдущий."""
    if not theme.animations:
        return
    for i, w in ((i, w) for i, step in enumerate(steps) for w in step):
        if getattr(w, "_fz_fading", False):
            continue        # уже исчезает (fade_out) — не перебивать
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
        # без кривой: кривые прозрачности и подъёма — в _RiseEffect.set_progress
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


def cascade_settle_ms(steps: int, first_delay_ms: int, step_ms: int, duration_ms: int) -> int:
    """Через сколько после старта cascade_in последний блок встанет на место."""
    return first_delay_ms + max(0, steps - 1) * step_ms + duration_ms if steps else 0


QWIDGETSIZE_MAX = 16777215


def animate_height(widget: QWidget, start: int, end: int, duration_ms: int, on_done=None,
                   easing=QEasingCurve.OutCubic) -> None:
    """Плавно менять высоту блока: всё, что ниже в раскладке, плавно
    съезжает. Высота на время анимации жёсткая (и min, и max) — иначе при
    сжатии раскладка сразу ужимает блок до содержимого (скачок). В конце
    ограничения снимаются (если end — не 0)."""
    old = getattr(widget, "_fz_height_anim", None)
    if old is not None:
        old.stop()
    anim = QVariantAnimation(widget)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(duration_ms)
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(lambda v: widget.setFixedHeight(int(v)))
    widget.setFixedHeight(start)

    def _done() -> None:
        widget._fz_height_anim = None
        widget.setMinimumHeight(0)
        if end:
            widget.setMaximumHeight(QWIDGETSIZE_MAX)
        if on_done:
            on_done()

    anim.finished.connect(_done)
    widget._fz_height_anim = anim
    anim.start()


def fade_out(widget: QWidget, duration_ms: int = 220, lift: int = 8, on_done=None) -> None:
    """Блок тает (равномерно с первого кадра) и чуть опускается."""
    # Ещё всплывает (закрыли сразу) — остановить: по окончании всплытие
    # снимает эффект с блока, и исчезновение оборвалось бы на полпути
    rise = getattr(widget, "_fz_rise", None)
    if rise is not None:
        widget._fz_rise = None
        rise.stop()
    widget._fz_fading = True        # cascade_in его больше не трогает
    effect = _RiseEffect(widget, lift)
    effect.set_state(1.0, 0.0)
    widget._fz_rise_effect = effect
    widget.setGraphicsEffect(effect)
    anim = QVariantAnimation(widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(duration_ms)
    # Прозрачность — линейно, сдвиг — с разгоном: уходит, а не прыгает
    anim.valueChanged.connect(lambda v: effect.set_state(1.0 - float(v), lift * float(v) ** 2))
    if on_done:
        anim.finished.connect(on_done)
    widget._fz_fade = anim
    anim.start()


class _ShakeEffect(QGraphicsEffect):
    """Рисует картинку виджета со сдвигом по горизонтали. Сам виджет стоит на
    месте: move() у виджета в раскладке сбрасывался первым же пересчётом
    (ошибка тут же меняет подсказку и цвет плитки) — тряски не было видно."""

    AMPLITUDE = 7

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dx = 0.0

    def set_offset(self, dx: float) -> None:
        self._dx = dx
        self.update()

    def boundingRectFor(self, rect):
        return QRectF(rect).adjusted(-self.AMPLITUDE - 1, 0, self.AMPLITUDE + 1, 0)

    def draw(self, painter) -> None:
        pix = self.sourcePixmap(Qt.LogicalCoordinates, mode=QGraphicsEffect.PixmapPadMode.NoPad)
        offset = self.sourceBoundingRect(Qt.LogicalCoordinates).topLeft()
        painter.drawPixmap(offset + QPointF(self._dx, 0), pix)


def shake(widget: QWidget) -> None:
    """Короткая тряска по горизонтали (ошибка) — затухающая синусоида, ~0.55 с."""
    if not theme.animations or getattr(widget, "_shaking", False):
        return
    rise = getattr(widget, "_fz_rise", None)
    if rise is not None and rise.state() == QVariantAnimation.Running:
        return      # блок ещё всплывает — у виджета один графический эффект, не перебиваем
    widget._shaking = True
    effect = _ShakeEffect(widget)
    widget._fz_shake_effect = effect        # без ссылки Python-обёртку соберёт GC
    widget.setGraphicsEffect(effect)
    anim = QVariantAnimation(widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(550)

    def step(t):
        if widget._fz_shake_effect is None or widget.graphicsEffect() is not effect:
            return      # эффект сменил начавшийся каскад (переход на вкладку)
        t = float(t)
        effect.set_offset(math.sin(t * math.pi * 6) * _ShakeEffect.AMPLITUDE * (1 - t))

    def done():
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        widget._fz_shake_effect = None
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
