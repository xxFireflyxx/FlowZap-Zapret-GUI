"""
ui/widgets/aurora.py
------------
Фон окна с «северным сиянием»: три размытых цветных шара медленно летают
по прямой и отскакивают от краёв, как логотип DVD на старых телевизорах.

- Видимый шар ≈ радиус 150 px (вместе с ореолом — пятно 440×360); от края
  он отскакивает, зайдя за него на 25 px.
- Старт случайный при каждом включении: центры не ближе трети диагонали окна,
  и не все три летят в одну сторону.
- Шары проходят друг сквозь друга — сталкиваются только с краями.
- Таймер работает, только пока окно видно и сияние включено: в трее и
  в свёрнутом окне процессор не тратится.

Цвета — ui.theme.AURORA_DARK / AURORA_LIGHT; выключенное сияние — просто
заливка bg_root.
"""

import math
import random
import time

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRadialGradient, QTransform
from PySide6.QtWidgets import QWidget

from ui.theme import AURORA_DARK, AURORA_LIGHT, theme

VISIBLE_R = 150      # радиус видимого шара
OVERSHOOT = 25       # насколько видимый шар заходит за край перед отскоком
BLOB_W, BLOB_H = 440, 360
FPS = 15      # пятна размытые и медленные (~2 px за кадр) — 15 кадров хватает, окно перерисовывается реже

# Профиль свечения: гаусс с σ ≈ 115 px, обрезанный к нулю на GLOW_R —
# полуяркость на ~135 px от центра, как у размытого пятна в макете.
GLOW_R = 340
GLOW_SIGMA = 115
GLOW_STOPS = 16
_tail = math.exp(-0.5 * (GLOW_R / GLOW_SIGMA) ** 2)
_GLOW_PROFILE = [
    max(0.0, (math.exp(-0.5 * (i / GLOW_STOPS * GLOW_R / GLOW_SIGMA) ** 2) - _tail) / (1 - _tail))
    for i in range(GLOW_STOPS + 1)
]

# Скорости, px/с (подобраны на макете для окна 1280×800):
# зелёное/акцентное, синее/фиолетовое, красное/розовое.
SPEEDS = [(27.4, 28.6), (32.3, 17.6), (24.9, 17.8)]


def _noise_tile(size: int = 128) -> QPixmap:
    """Мелкий шум для дизеринга: у плавного градиента на 8-битном экране
    видны ступеньки-кольца; почти невидимый шум поверх их разбивает."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    rnd = random.Random(1)
    for y in range(size):
        for x in range(size):
            v = 255 if rnd.random() < 0.5 else 0
            a = rnd.randint(1, 3)
            img.setPixelColor(x, y, QColor(v, v, v, a))
    return QPixmap.fromImage(img)


class AuroraBackground(QWidget):
    """Центральный виджет окна: рисует фон и шары, остальной интерфейс — поверх."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("aurora")
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._enabled = False
        self._paused = False
        self._blobs: list[list[float]] = []      # [x, y, vx, vy] — центр и скорость
        self._last = 0.0
        self._noise: QPixmap | None = None
        self._sprites: list[QPixmap] = []     # готовые картинки шаров — градиент считается один раз
        self._sprites_key = None
        self._hold_until = 0.0                # пауза перерисовки на время коротких анимаций
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // FPS)
        self._timer.timeout.connect(self._tick)

    # ── управление ─────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self._enabled:
            self._blobs = []          # новый случайный старт при каждом включении
        self._enabled = enabled
        self._sync_timer()
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sync_timer()

    def pause(self, paused: bool) -> None:
        """Окно свёрнуто / развёрнуто — MainWindow.changeEvent."""
        self._paused = paused
        self._sync_timer()

    def hold(self, seconds: float = 0.4) -> None:
        """Не перерисовывать фон ближайшие seconds: короткая анимация (подсветка
        вкладок) получает всё время кадра. Шары продолжают «лететь» — после
        паузы просто окажутся дальше."""
        self._hold_until = max(self._hold_until, time.monotonic() + seconds)

    def _sync_timer(self) -> None:
        run = self._enabled and self.isVisible() and not self._paused
        if run and not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()
        elif not run and self._timer.isActive():
            self._timer.stop()

    # ── движение ───────────────────────────────────────────────────────

    def _bounds(self) -> tuple[float, float, float, float]:
        lo = VISIBLE_R - OVERSHOOT
        return lo, lo, max(lo + 1, self.width() - lo), max(lo + 1, self.height() - lo)

    def _spawn(self) -> None:
        x0, y0, x1, y1 = self._bounds()
        min_dist = math.hypot(x1 - x0, y1 - y0) / 3
        for _ in range(200):
            pts = [(random.uniform(x0, x1), random.uniform(y0, y1)) for _ in SPEEDS]
            dirs = [(random.choice((-1, 1)), random.choice((-1, 1))) for _ in SPEEDS]
            far = all(math.dist(a, b) >= min_dist for i, a in enumerate(pts) for b in pts[i + 1:])
            if far and len(set(dirs)) > 1:
                break
        self._blobs = [[x, y, sx * dx, sy * dy] for (x, y), (sx, sy), (dx, dy) in zip(pts, SPEEDS, dirs)]

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last)       # после паузы — без скачка
        self._last = now
        if not self._blobs:
            return
        x0, y0, x1, y1 = self._bounds()
        for b in self._blobs:
            b[0] += b[2] * dt
            b[1] += b[3] * dt
            if b[0] < x0 or b[0] > x1:
                b[2] = -b[2]
                b[0] = min(max(b[0], x0), x1)
            if b[1] < y0 or b[1] > y1:
                b[3] = -b[3]
                b[1] = min(max(b[1], y0), y1)
        if now >= self._hold_until:
            self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._blobs:                       # окно стало меньше — вернуть шары в поле
            x0, y0, x1, y1 = self._bounds()
            for b in self._blobs:
                b[0], b[1] = min(max(b[0], x0), x1), min(max(b[1], y0), y1)

    # ── отрисовка ──────────────────────────────────────────────────────

    def _colors(self) -> list[QColor]:
        pal = theme.palette
        names = AURORA_DARK if pal.is_dark else AURORA_LIGHT
        alpha = 0.34 if pal.is_dark else 0.5
        colors = []
        for name in names:
            c = QColor(getattr(pal, name) if not name.startswith("#") else name)
            c.setAlphaF(alpha)
            colors.append(c)
        return colors

    def _get_sprites(self) -> list[QPixmap]:
        """Картинки шаров под текущую тему: гауссов спад вместо CSS blur(70px)
        из макета — яркий центр, мягкий хвост, без видимой границы."""
        key = (theme.current, self.devicePixelRatioF())
        if self._sprites_key == key:
            return self._sprites
        dpr = self.devicePixelRatioF()
        w, h = GLOW_R * 2, round(GLOW_R * 2 * BLOB_H / BLOB_W)
        self._sprites = []
        for color in self._colors():
            img = QImage(round(w * dpr), round(h * dpr), QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            grad = QRadialGradient(QPointF(0, 0), GLOW_R)
            for i in range(GLOW_STOPS + 1):
                c = QColor(color)
                c.setAlphaF(color.alphaF() * _GLOW_PROFILE[i])
                grad.setColorAt(i / GLOW_STOPS, c)
            p.setTransform(QTransform().translate(w / 2, h / 2).scale(1.0, BLOB_H / BLOB_W))
            p.setBrush(grad)
            p.drawEllipse(QPointF(0, 0), GLOW_R, GLOW_R)
            p.end()
            self._sprites.append(QPixmap.fromImage(img))
        self._sprites_key = key
        return self._sprites

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.palette.bg_root))
        if not self._enabled:
            return
        if not self._blobs:
            self._spawn()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for (x, y, _vx, _vy), sprite in zip(self._blobs, self._get_sprites()):
            painter.drawPixmap(round(x - sprite.width() / 2), round(y - sprite.height() / 2), sprite)
        if self._noise is None:
            self._noise = _noise_tile()
        painter.drawTiledPixmap(self.rect(), self._noise)
