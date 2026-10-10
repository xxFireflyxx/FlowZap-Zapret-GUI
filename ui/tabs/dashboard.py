"""
ui/tabs/dashboard.py — Главная вкладка FlowZap (PySide6).

Раскладка «Пульт»: три равные плитки — обход (zapret), свой DNS и
TG Proxy, у каждой свой тумблер и одинаковый скелет. Под ними блок
«Пресеты» — единственное место выбора пресета: лучший по последней
проверке, ещё подходящие и все остальные; там же Game Filter.

Пресеты — core/zapret/presets.py, их статусы — core/zapret/preset_checker.py.

TG Proxy — тумблер управляет core/tgproxy/manager.TgProxyManager через
set_running_async(); при первом включении сервер сначала скачивается.
Тумблер всегда показывает ФАКТИЧЕСКОЕ состояние, а не оптимистичное. Пока
прокси работает, раз в несколько секунд смотрим, кто к нему подключён
(poll_async): слот плитки показывает «Подключить в Telegram», пока Telegram
ни разу не подключался с текущими портом и секретом, а дальше — подключён
ли он сейчас. То же состояние показывают «Параметры» (tgStatusChanged).

DNS — тумблер читает активную пару из config["dns"]["pairs"][0]
(core/dns/manager.get_active_pair) и применяет её через netsh
(core/dns/manager.apply_dns_async). Если пара меняется в ParametersTab,
он зовёт on_dns_changed() у этой вкладки, чтобы переприменить DNS "на
лету", когда он уже включён.
"""

import logging
import time
import threading
from pathlib import Path

from PySide6.QtCore import QEasingCurve, Qt, Signal, QTimer, QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QSizePolicy,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
)

from ui.theme import theme
from ui.widgets.popup import HelpIcon
from ui.widgets.animation import (
    QWIDGETSIZE_MAX, CheckProgress, RippleOverlay, animate_height, cascade_hide, cascade_in, fade_out,
    shake,
)
from ui.widgets.announcement import AnnouncementBar
from ui.widgets.base import Glyph, button, dot_icon, label, restyle, set_tone
from ui.widgets.controls import CircleBadge, FieldButton, StatusDot, Switch
from ui.widgets.layout import Page
from ui.widgets.popup import make_popup, show_popup_below
from core.zapret.presets import list_presets
from core.zapret.preset_checker import MAIN_SERVICES, PresetPingManager, PingStatus
from core.zapret.manager import ServiceState
from core.updates.zapret import download_and_install_core
from core.dns import manager as dns_manager
from core.service import client as service_client
from core.tgproxy import settings as tg_settings
from core.tgproxy.manager import TgProxyManager, client_title
from core.updates.tgproxy import download_and_install_tg_proxy
from core.system.idle import get_idle_seconds

log = logging.getLogger(__name__)

# Цвет точки статуса пресета — имя поля палитры (ui/theme.Palette).
_STATUS_COLORS = {
    PingStatus.UNKNOWN:  "text_muted",
    PingStatus.CHECKING: "accent",
    PingStatus.OK:       "success",
    PingStatus.WARN:     "warning",
    PingStatus.FAIL:     "error",
}

_STATUS_LEGEND = [
    ("success",    "Работает"),
    ("warning",    "Нестабильно"),
    ("error",      "Не работает"),
    ("accent",     "Идёт проверка"),
    ("text_muted", "Не проверялся"),
]

# (подпись, tone подписи, цвет точки)
_SERVICE_STATE_LABELS = {
    ServiceState.STOPPED:  ("Остановлен",  None,      "text_muted"),
    ServiceState.STARTING: ("Запускается", "warning", "warning"),
    ServiceState.RUNNING:  ("Активен",     "success", "success"),
    ServiceState.STOPPING: ("Остановка",   "warning", "warning"),
    ServiceState.ERROR:    ("Ошибка",      "error",   "error"),
}

# Фоновая перепроверка пресетов (changelog v0.5.1) — раз в 3 дня, и
# только если система простаивает от 2 часов (см. _maybe_run_idle_preset_check).
IDLE_PRESET_CHECK_INTERVAL_SEC       = 3 * 24 * 3600  # раз в 3 дня
IDLE_PRESET_CHECK_IDLE_THRESHOLD_SEC = 2 * 3600        # простой от 2 часов
IDLE_PRESET_CHECK_POLL_MS            = 10 * 60 * 1000  # как часто проверяем условия

# «Запускается / Останавливается» на плитке — только если дольше этого
ZAPRET_BUSY_DELAY_MS = 500

# TG Proxy: как часто смотреть, кто подключён, и сколько ждать запущенный
# Telegram, прежде чем сказать, что он ходит мимо прокси
TG_POLL_MS = 3000
TG_POLL_FAST_MS = 1000      # сразу после включения — чаще, чтобы зелёный появлялся без задержки
TG_CONNECT_GRACE_SEC = 20
# Адреса Telegram на этом компьютере: ссылка «Подключить» всегда на 127.0.0.1
_LOCAL_HOSTS = {"127.0.0.1", "::1"}

# Цвет точки состояния Telegram на плитке — по виду из tg_connection_status()
TG_DOT_COLORS = {
    "connect":    "text_muted",
    "ready":      "text_muted",
    "connected":  "success",
    "waiting":    "text_muted",
    "connecting": "text_muted",     # плитка оставляет прежний цвет, см. _refresh_tg_tile
    "bypass":     "warning",
    "rejected":   "warning",
    "direct":     "warning",
    "installing": "accent",
}


def _status_color(status: PingStatus) -> str:
    return getattr(theme.palette, _STATUS_COLORS.get(status, "text_muted"))


def _glyph_label(glyph: str, px: int = 13) -> QLabel:
    """Значок шрифтом Segoe MDL2 в QLabel. Шрифт задаётся через QSS самого
    виджета — общий QSS (`* { font-family }`) перебил бы setFont()."""
    lbl = QLabel(glyph)
    lbl.setStyleSheet(f"font-family: '{theme.typography.family_icon}'; font-size: {px}px;")
    lbl.setProperty("tone", "muted")
    return lbl


def _service_groups(services: dict) -> tuple[list[str], list[str], list[str]]:
    """(работают полностью, частично — «YouTube 3/4», не работают)."""
    full, part, fail = [], [], []
    for name, (ok, total) in services.items():
        if total and ok == total:
            full.append(name)
        elif ok:
            part.append(f"{name} {ok}/{total}")
        else:
            fail.append(name)
    return full, part, fail


def _status_word(status: PingStatus, counts: tuple[int, int] | None,
                 services: dict | None = None) -> tuple[str, str | None]:
    """Короткое описание результата проверки пресета: (текст, tone). С
    итогом по сервисам (preset_checker, utils/targets.txt) — по сервисам:
    «работают: Discord, YouTube · нет: Cloudflare»; «5 из 8 проверок» не
    говорило, что именно не работает. Без него (результаты до проверки по
    сервисам) — как раньше. Время ответа не пишем: «205 / 254 мс» —
    шум для пользователя, оно только для выбора лучшего (ranked)."""
    ok, total = counts or (0, 0)
    if services and status in (PingStatus.OK, PingStatus.WARN, PingStatus.FAIL):
        full, part, fail = _service_groups(services)
        tone = {PingStatus.OK: None, PingStatus.WARN: "warning"}.get(status, "error")
        if not part and not fail:
            return f"{', '.join(full)} работают", tone
        pieces = []
        if full:
            pieces.append("работают: " + ", ".join(full))
        if part:
            pieces.append("частично: " + ", ".join(part))
        if fail:
            pieces.append("нет: " + ", ".join(fail))
        return " · ".join(pieces), tone
    if status == PingStatus.OK:
        return ("Discord и YouTube открываются" if total and ok == total else f"{ok} из {total} проверок"), None
    if status == PingStatus.WARN:
        return (f"частично · {ok} из {total} проверок" if total else "работает нестабильно"), "warning"
    if status == PingStatus.FAIL:
        return "не работает", "error"
    if status == PingStatus.CHECKING:
        return "проверяется…", "accent"
    return "не проверялся", "muted"


def _service_chips(services: dict, role: str) -> QHBoxLayout:
    """Чипы по сервисам (как в макете): зелёный — работает, жёлтый —
    частично (с числом адресов), красный — нет. Время ответа не пишем —
    «205 / 254 мс» для пользователя шум; оно только для выбора лучшего.
    role: pill — заливные (лучший пресет), chip — рамкой (остальные)."""
    chips = QHBoxLayout()
    chips.setSpacing(6)
    for name, (ok, total) in services.items():
        tone = "success" if total and ok == total else ("warning" if ok else "error")
        chip = label(name if tone != "warning" else f"{name} {ok}/{total}", role=role, tone=tone)
        chip.setToolTip(f"{name}: открылось {ok} из {total} адресов")
        chips.addWidget(chip, alignment=Qt.AlignVCenter)
    chips.addStretch(1)
    return chips


_STATUS_ORDER = {PingStatus.OK: 0, PingStatus.WARN: 1, PingStatus.FAIL: 2}
# С какой x (от левого края блока «Пресеты») начинаются чипы сервисов — и у
# лучшего пресета, и в строках под ним. Хватает на «ЛУЧШИЙ» + самое длинное
# имя Flowseal («general (FAKE TLS AUTO ALT3)»).
_CHIPS_X = 420


class _OptionItem(QAbstractButton):
    """Пункт всплывающего списка: название + пояснение под ним, выбранный подсвечен."""

    def __init__(self, title: str, description: str = "", selected: bool = False,
                 dot: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setText(title)
        self._desc = description
        self._selected = selected
        self._dot = dot
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(50 if description else 34)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

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
        if self._selected or self.underMouse():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal.accent_soft if self._selected else pal.bg_hover))
            painter.drawRoundedRect(QRectF(self.rect()), 8, 8)
        title_rect_h = 26 if self._desc else self.height()
        title_top = 5 if self._desc else 0
        x = 10
        if self._dot:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(getattr(pal, self._dot, pal.text_muted)))
            painter.drawEllipse(QRectF(x, title_top + title_rect_h / 2 - 4.5, 9, 9))
            x += 19
        font = QFont(t.family_ui)
        font.setPixelSize(t.size_sm)
        font.setWeight(QFont.DemiBold if self._selected else QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor(pal.text_primary))
        painter.drawText(QRectF(x, title_top, self.width() - x - 8, title_rect_h),
                         Qt.AlignVCenter | Qt.AlignLeft, self.text())
        if self._desc:
            small = QFont(t.family_ui)
            small.setPixelSize(t.size_xs)
            painter.setFont(small)
            painter.setPen(QColor(pal.text_muted))
            painter.drawText(QRectF(x, 27, self.width() - x - 8, 18), Qt.AlignVCenter | Qt.AlignLeft, self._desc)


def _open_options(anchor: QWidget, options: list[tuple], current, on_pick, width: int,
                  intro: str = "") -> None:
    """Всплывающий список вариантов под anchor (или над ним, если снизу нет места).
    options: [(key, title, description, dot)]; on_pick(key)."""
    popup, layout = make_popup(anchor)
    if intro:
        hint = label(intro, role="hint", wrap=True)
        hint.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(hint)
    for key, title, desc, dot in options:
        item = _OptionItem(title, desc, selected=(key == current), dot=dot)

        def _clicked(_=False, k=key):
            popup.close()
            on_pick(k)

        item.clicked.connect(_clicked)
        layout.addWidget(item)
    popup.setFixedWidth(width)
    show_popup_below(popup, anchor)


def _clear_layout(layout) -> None:
    """Убрать всё из раскладки сразу: deleteLater() сам по себе удаляет виджет
    только при возврате в цикл событий, а до того старые строки висели бы поверх новых."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())
            item.layout().deleteLater()


class ServiceTile(QFrame):
    """Плитка сервиса на главной. У всех трёх одинаковый скелет, чтобы
    плитки стояли ровно: шапка (значок, название, состояние, тумблер),
    подпись, слот 44 px под контрол (self.slot), подсказка внизу."""

    def __init__(self, glyph: str, title: str, slot_caption: str, help_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tile")
        self.setProperty("active", False)
        self.setProperty("error", False)
        self._user_toggled = False      # волна — только когда включил сам пользователь
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(14)
        self.badge = CircleBadge(glyph, 46)
        top.addWidget(self.badge)
        names = QVBoxLayout()
        names.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addWidget(label(title, role="muted"))
        if help_text:
            title_row.addWidget(HelpIcon(help_text))
        title_row.addStretch(1)
        names.addLayout(title_row)
        self.state = label("Выключен", role="status")
        names.addWidget(self.state)
        top.addLayout(names, stretch=1)
        self.switch = Switch(large=True)
        self.switch.clicked.connect(self._mark_user_toggle)
        top.addWidget(self.switch, alignment=Qt.AlignVCenter)
        layout.addLayout(top)

        layout.addSpacing(20)
        layout.addWidget(label(slot_caption, role="hint"))
        layout.addSpacing(6)
        slot_box = QWidget()
        slot_box.setFixedHeight(44)
        self.slot = QHBoxLayout(slot_box)
        self.slot.setContentsMargins(0, 0, 0, 0)
        self.slot.setSpacing(10)
        layout.addWidget(slot_box)

        layout.addStretch(1)
        layout.addSpacing(14)
        self.hint = label("", role="hint", wrap=True)
        self.hint.setMinimumHeight(34)
        self.hint.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        layout.addWidget(self.hint)

        self._ripple = RippleOverlay(self, radius=theme.metrics.corner_radius)

    def _mark_user_toggle(self) -> None:
        self._user_toggled = not self.property("active")

    def set_state(self, active: bool, text: str, tone: str | None = None) -> None:
        self.state.setText(text)
        set_tone(self.state, tone or ("accent" if active else None))
        self.badge.set_active(active)
        if self.property("active") != active:
            self.setProperty("active", active)
            restyle(self)
            if active and self._user_toggled:
                self._user_toggled = False
                self._ripple.play(QPointF(self.switch.mapTo(self, self.switch.rect().center())))

    def set_error(self, error: bool) -> None:
        """Красная плитка: горит, пока пользователь снова не попробует включить."""
        if self.property("error") != error:
            self.setProperty("error", error)
            restyle(self)
        if error:
            shake(self)

    def set_hint(self, text: str, tone: str | None = None) -> None:
        self.hint.setText(text)
        set_tone(self.hint, tone)


class _PresetRow(QFrame):
    """Строка списка «ещё подходящих»: место, имя, итог проверки, точка, «Выбрать»."""

    picked = Signal(dict)

    def __init__(self, rank: int, preset: dict, status: PingStatus, counts, active: bool,
                 services: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("presetRow")
        self.setFixedHeight(44)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 4, 0)
        row.setSpacing(12)
        num = label(str(rank), role="hint")
        num.setFixedWidth(18)
        row.addWidget(num)
        # Колонка имени — фиксированной ширины: чипы начинаются там же, где
        # у лучшего пресета (_CHIPS_X)
        name_col = QWidget()
        name_col.setFixedWidth(_CHIPS_X - 6 - 18 - 12 * 2)
        name_box = QHBoxLayout(name_col)
        name_box.setContentsMargins(0, 0, 0, 0)
        name_box.setSpacing(8)
        name_box.addWidget(label(preset["name"], role="strong" if active else None), alignment=Qt.AlignVCenter)
        if active:
            name_box.addWidget(label("сейчас", role="pill", tone="accent"), alignment=Qt.AlignVCenter)
        name_box.addStretch(1)
        row.addWidget(name_col)
        if services and status in (PingStatus.OK, PingStatus.WARN, PingStatus.FAIL):
            # Как у лучшего, но рамкой: длинная строка «Discord, YouTube, …
            # работают» в каждой строке списка — шум
            row.addLayout(_service_chips(services, role="chip"), stretch=1)
        else:
            text, tone = _status_word(status, counts, services)
            status_lbl = label(text, role="muted", tone=tone if tone != "muted" else None)
            status_lbl.setToolTip(text)
            row.addWidget(status_lbl, stretch=1)
        dot = StatusDot(9)
        dot.set_color(_STATUS_COLORS.get(status, "text_muted"))
        row.addWidget(dot)
        pick = button("Выбрать", variant="link")
        pick.setFixedWidth(96)
        pick.clicked.connect(lambda: self.picked.emit(preset))
        row.addWidget(pick)
        # У текущего кнопки нет, но место под неё держим — колонки не прыгают.
        size_policy = pick.sizePolicy()
        size_policy.setRetainSizeWhenHidden(True)
        pick.setSizePolicy(size_policy)
        pick.setVisible(not active)


class _ElideLabel(QLabel):
    """Подпись в одну строку: не влезает — обрезается с «…», полный текст в
    подсказке. Подсказка — только у обрезанной: подпись растянута на всё
    свободное место шапки, и иначе она всплывала над пустым местом блока."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class PresetsPanel(QFrame):
    """Блок «Пресеты» — единственное место выбора пресета: лучший по
    последней проверке, под ним свёрнутый список ещё подходящих (до 5) и
    «Все пресеты» для ручного выбора. В шапке — проверка и Game Filter."""

    presetSelected = Signal(dict)
    MAX_OTHERS = 5
    MESSAGE_MS = 8000     # итог/ошибка висят в шапке столько, потом снова дата проверки

    def __init__(self, ping_mgr: PresetPingManager, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._ping = ping_mgr
        self._presets: list[dict] = []
        self._statuses: dict[str, PingStatus] = {}
        self._selected: dict | None = None
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 16, 22, 16)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label("Пресеты", role="section"))
        head.addWidget(HelpIcon(
            "Пресеты — стратегии обхода из Flowseal/zapret-discord-youtube. Проверка по очереди "
            "запускает каждый пресет и смотрит, открываются ли Discord, YouTube, Google и "
            "Cloudflare — каждый по нескольким адресам (список Flowseal, utils/targets.txt). "
            "Лучший — тот, с которым полностью работают Discord и YouTube, потом остальные "
            "сервисы, а при равенстве — самый быстрый.",
            legend=_STATUS_LEGEND,
        ))
        head.addSpacing(6)
        # Здесь же — сообщения (ход проверки, итог, ошибки): отдельной
        # строкой они раздвигали блок, и интерфейс прыгал.
        self._meta = _ElideLabel()
        self._meta.setProperty("role", "hint")
        head.addWidget(self._meta, stretch=1, alignment=Qt.AlignVCenter)
        self._meta_text = ""
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.setInterval(self.MESSAGE_MS)
        self._message_timer.timeout.connect(lambda: self.set_message(""))
        self.btn_check = button("Проверить заново", variant="ghost")
        self.btn_check.setToolTip("По очереди запустить каждый пресет и проверить, открываются ли "
                                  "Discord, YouTube, Google и Cloudflare")
        head.addWidget(self.btn_check)
        self.btn_game = button("Game Filter: выкл  ▾")
        self.btn_game.setToolTip("Обход для онлайн-игр")
        head.addWidget(self.btn_game)
        outer.addLayout(head)

        # Полоса проверки: место под неё есть всегда — появление ничего не сдвигает
        outer.addSpacing(6)
        self.progress = CheckProgress()
        policy = self.progress.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.progress.setSizePolicy(policy)
        outer.addWidget(self.progress)
        outer.addSpacing(6)

        self._body = QVBoxLayout()
        self._body.setSpacing(8)
        outer.addLayout(self._body)

    # ── данные ──
    @property
    def presets(self) -> list[dict]:
        return self._presets

    def set_presets(self, presets: list[dict]) -> None:
        self._presets = presets
        names = {p["name"] for p in presets}
        if not self._selected or self._selected["name"] not in names:
            self._selected = presets[0] if presets else None
        self.render()

    def select_preset(self, preset: dict, emit: bool = True) -> None:
        self._selected = preset
        self.render()
        if emit:
            self.presetSelected.emit(preset)

    def selected_preset(self) -> dict | None:
        return self._selected

    def update_status(self, name: str, status: PingStatus) -> None:
        self._statuses[name] = status
        self._update_all_item(name)
        # Во время проверки список не перестраиваем на каждый пресет — только в конце.
        if not self._ping.is_testing:
            self.render()

    def status_of(self, name: str) -> PingStatus:
        return self._statuses.get(name, PingStatus.UNKNOWN)

    def ranked(self) -> list[dict]:
        """Пресеты, прошедшие проверку (полностью или частично), от лучшего к худшему:
        статус → сколько из главных сервисов (Discord, YouTube) работает
        полностью → сколько сервисов всего → доля открывшихся адресов →
        среднее время ответа (быстрее — выше) → порядок Flowseal."""
        index = {p["name"]: i for i, p in enumerate(self._presets)}

        def key(p):
            ok, total = self._ping.get_counts(p["name"]) or (0, 0)
            ms = self._ping.get_ms(p["name"])
            services = self._ping.get_services(p["name"]) or {}
            full = [n for n, (s_ok, s_total) in services.items() if s_total and s_ok == s_total]
            main = sum(1 for n in MAIN_SERVICES if n in full)
            return (_STATUS_ORDER.get(self.status_of(p["name"]), 3), -main, -len(full),
                    -(ok / total if total else 0), ms if ms is not None else float("inf"),
                    index[p["name"]])

        good = [p for p in self._presets if self.status_of(p["name"]) in (PingStatus.OK, PingStatus.WARN)]
        return sorted(good, key=key)

    def best(self) -> dict | None:
        ranked = self.ranked()
        return ranked[0] if ranked else None

    def set_message(self, text: str, tone: str | None = None) -> None:
        """Сообщение в шапке блока вместо даты проверки. Ход работы (tone
        accent) висит, пока его не сменит следующее; итог и ошибки —
        MESSAGE_MS, потом шапка возвращается к дате. "" — сразу вернуть."""
        self._message_timer.stop()
        if text:
            self._meta.set_full_text(text)
            set_tone(self._meta, tone)
            if tone != "accent":
                self._message_timer.start()
        else:
            self._meta.set_full_text(self._meta_text)
            set_tone(self._meta, None)

    # ── отрисовка ──
    def render(self) -> None:
        _clear_layout(self._body)

        if self._ping.checked_at:
            when = time.strftime("%d.%m.%Y", time.localtime(self._ping.checked_at))
            self._meta_text = f"проверка {when} · {len(self._presets)} пресетов"
        else:
            self._meta_text = f"{len(self._presets)} пресетов" if self._presets else ""
        if not self._message_timer.isActive() and self._meta.property("tone") in (None, ""):
            self._meta.set_full_text(self._meta_text)
        self.btn_check.setText("Проверить заново" if self._ping.checked_at else "Проверить")
        self.btn_check.setVisible(bool(self._presets))

        if not self._presets:
            self._body.addWidget(label(
                "Пресеты не установлены. Включите обход — zapret скачается и установится автоматически.",
                role="muted", wrap=True))
            return

        ranked = self.ranked()
        if not ranked:
            text = ("Пресеты ещё не проверялись. Нажмите «Проверить» — FlowZap по очереди попробует "
                    "каждый и найдёт рабочий (около пары минут)."
                    if not self._ping.checked_at else
                    "Ни один пресет не прошёл последнюю проверку. Проверьте ещё раз или выберите пресет вручную.")
            self._body.addWidget(label(text, role="muted", wrap=True))
            self._body.addLayout(self._footer(0))
            return

        best = ranked[0]
        self._body.addWidget(self._best_row(best))
        others = ranked[1:1 + self.MAX_OTHERS]
        if self._expanded and others:
            box = QVBoxLayout()
            box.setSpacing(0)
            for i, p in enumerate(others):
                row = _PresetRow(i + 2, p, self.status_of(p["name"]), self._ping.get_counts(p["name"]),
                                 active=bool(self._selected and self._selected["name"] == p["name"]),
                                 services=self._ping.get_services(p["name"]))
                row.picked.connect(self.select_preset)
                box.addWidget(row)
            self._body.addLayout(box)
        self._body.addLayout(self._footer(len(others)))

    def _best_row(self, best: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("bestRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 12, 14, 12)
        row.setSpacing(14)
        # Значок и имя — в колонке той же ширины, что имя в строках ниже
        # (рамка 1 px + поле 16 + промежуток 14): чипы стоят в одну линию
        head = QWidget()
        head.setFixedWidth(_CHIPS_X - 1 - 16 - 14)
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(14)
        head_row.addWidget(label("ЛУЧШИЙ", role="badge"))
        head_row.addWidget(label(best["name"], role="value"))
        head_row.addStretch(1)
        row.addWidget(head)
        services = self._ping.get_services(best["name"])
        if services:
            row.addLayout(_service_chips(services, role="pill"))
        else:
            text, tone = _status_word(self.status_of(best["name"]), self._ping.get_counts(best["name"]))
            row.addWidget(label(text, role="pill", tone="accent" if tone is None else tone))
        row.addStretch(1)
        if self._selected and self._selected["name"] == best["name"]:
            used = label("Используется", role="strong", tone="accent")
            used.setContentsMargins(0, 0, 8, 0)
            row.addWidget(used)
        else:
            use = button("Использовать", variant="primary")
            use.setMinimumHeight(40)
            use.clicked.connect(lambda: self.select_preset(best))
            row.addWidget(use)
        return frame

    def _footer(self, others: int) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        if others:
            more = button(("Свернуть  ▴" if self._expanded else f"Ещё {others} подходящих  ▾"), variant="ghost")
            more.clicked.connect(self._toggle_expanded)
            row.addWidget(more)
        row.addStretch(1)
        all_btn = button(f"Все пресеты ({len(self._presets)})", variant="ghost")
        all_btn.setToolTip("Выбрать любой пресет вручную")
        all_btn.clicked.connect(lambda: self._open_all(all_btn))
        row.addWidget(all_btn)
        return row

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self.render()

    def _all_item_view(self, preset: dict):
        """(значок, текст, подсказка) строки пресета в списке «Все пресеты».
        Текст короткий — «работает»: перечень сервисов в каждой строке не
        читался; он — в подсказке."""
        name = preset["name"]
        status = self.status_of(name)
        short = {PingStatus.OK: "работает", PingStatus.WARN: "частично",
                 PingStatus.FAIL: "не работает", PingStatus.CHECKING: "проверяется…"}.get(status, "не проверялся")
        detail, _tone = _status_word(status, self._ping.get_counts(name), self._ping.get_services(name))
        tip = "\n".join(t for t in (detail, preset.get("desc", "")) if t)
        return dot_icon(_status_color(status)), f"{name}   ·   {short}", tip

    def _update_all_item(self, name: str) -> None:
        """Открытый список «Все пресеты» — обновить строку пресета сразу
        (во время проверки блок не перестраивается, а список открыт)."""
        items = getattr(self, "_all_items", None)
        if not items or name not in items:
            return
        item = items[name]
        icon, text, tip = self._all_item_view(item.data(Qt.UserRole))
        item.setIcon(icon)
        item.setText(text)
        item.setToolTip(tip)

    def _open_all(self, anchor: QWidget) -> None:
        popup, layout = make_popup(anchor)
        list_widget = QListWidget()
        list_widget.setFrameShape(QFrame.NoFrame)
        list_widget.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        list_widget.setIconSize(QSize(10, 10))
        current = None
        items = self._all_items = {}

        def _forget() -> None:
            # Список удаляется при закрытии (WA_DeleteOnClose) вместе со
            # строками — обращаться к ним после этого нельзя
            if self._all_items is items:
                self._all_items = None

        popup.destroyed.connect(_forget)
        for preset in self._presets:
            icon, text, tip = self._all_item_view(preset)
            item = QListWidgetItem(icon, text)
            item.setData(Qt.UserRole, preset)
            item.setToolTip(tip)
            list_widget.addItem(item)
            self._all_items[preset["name"]] = item
            if self._selected and preset["name"] == self._selected["name"]:
                current = item
        layout.addWidget(list_widget)

        def _pick(item: QListWidgetItem) -> None:
            preset = item.data(Qt.UserRole)
            popup.close()
            self.select_preset(preset)

        list_widget.itemClicked.connect(_pick)
        popup.setFixedWidth(460)     # «general (FAKE TLS AUTO ALT3) · не проверялся»
        popup.setFixedHeight(min(34 * len(self._presets) + 16, 380))
        show_popup_below(popup, anchor)
        if current is not None:
            list_widget.setCurrentItem(current)
            list_widget.scrollToItem(current, QListWidget.PositionAtCenter)


class DashboardTab(QWidget):

    _pingStatusUpdated   = Signal(str, object)   # (preset_name, PingStatus)
    _pingTestsDone       = Signal(bool, str)
    _managerStateChanged = Signal(object)        # ServiceState — может прийти из watcher-потока
    _coreInstallProgress = Signal(str)
    _coreInstallDone     = Signal(bool, str)
    _dnsToggleDone       = Signal(bool, str)     # (ok, error) — из dns_manager.apply_dns_async
    _tgProxyDone         = Signal(bool, str)     # (running, error) — из TgProxyManager.*_async
    _tgPolled            = Signal(int, bool, object, bool, str, object)  # (поколение, жив, клиенты, Telegram запущен, его exe, кому отказ)
    _tgMigrated          = Signal()              # переход со старого TgWsProxy завершён
    _tgInstallProgress   = Signal(str)
    _tgInstallDone       = Signal(bool, str)
    _warmupDone          = Signal()              # прогрев WinDivert завершён (core/windivert_warmup)

    # Состояние TG Proxy изменилось — «Параметры» перерисовывают свою строку статуса
    tgStatusChanged      = Signal()

    def __init__(self, parent=None, manager=None, config: dict = None, save_config_fn=None):
        super().__init__(parent)
        self.manager = manager
        self._config = config or {}
        self._save_config_fn = save_config_fn

        self._zapret_dir = Path(self._config.get("_app_dir", ".")) / self._config.get(
            "zapret", {}
        ).get("presets_dir", "zapret")

        # запоминается даже когда фильтр выключен — применяется при
        # следующем включении (как в старом dashboard.py)
        self._init_game_filter_state()

        self._ping_mgr = PresetPingManager(
            self._zapret_dir,
            on_update=lambda name, status: self._pingStatusUpdated.emit(name, status),
            on_tests_done=lambda ok, msg: self._pingTestsDone.emit(ok, msg),
        )
        self._pingStatusUpdated.connect(self._on_ping_status_updated)
        self._pingTestsDone.connect(self._on_ping_tests_done)
        self._coreInstallProgress.connect(self._on_core_install_progress)
        self._coreInstallDone.connect(self._on_core_install_done)
        self._installing_core = False
        self._auto_testing = False              # True только для авто-теста сразу после установки Core
        # Идёт проверка пресетов: свой флаг, а не PresetPingManager.is_testing —
        # тот сбрасывается в фоновом потоке и может опередить/отстать от сигнала «готово»
        self._checking = False
        self._pending_start_after_test = False   # запрос "остановить ручной тест и сразу стартовать"

        self._zapret_error = False   # плитка обхода красная, пока пользователь не включит снова
        self._dnsToggleDone.connect(self._on_dns_toggle_done)
        self._dns_enabled = False
        # Отмечает, идёт ли сейчас apply_dns_async в фоновом потоке — shutdown()
        # дожидается его завершения перед своим решением, иначе быстрое закрытие
        # сразу после включения/выключения DNS гоняло бы netsh параллельно с
        # собственным сбросом DNS при выходе (см. shutdown()).
        self._dns_toggle_event = threading.Event()
        self._dns_toggle_event.set()

        self._tgProxyDone.connect(self._on_tg_proxy_done)
        self._tgPolled.connect(self._on_tg_polled)
        self._tgMigrated.connect(self._on_tg_migrated)
        self._tgInstallProgress.connect(self._on_tg_install_progress)
        self._tgInstallDone.connect(self._on_tg_install_done)
        self._tg_running = False
        self._tg_done_cb = None     # одноразовый колбэк из set_tg_proxy()
        self._tg_error = ""         # почему не запустился — пока пользователь не попробует снова
        self._tg_installing = False
        self._tg_open_after_start = False   # «Подключить» при выключенном прокси: включить и открыть
        self._tg_restart_pending = False    # настройки поменяли, пока шла другая операция
        self._tg_clients: list[str] = []    # exe клиентов, подключённых сейчас
        self._tg_rejected = False           # прокси отказывает Telegram этого компьютера
        self._tg_relink = False             # сменили порт/секрет — открыть «Подключить» после перезапуска
        self._tg_started_fp = ""            # fingerprint порта и секрета работающего сервера
        self._tg_app_running = False        # запущен ли Telegram на компьютере
        self._tg_wait_since: float | None = None   # с какого момента Telegram запущен, но не подключён
        self._tg_gen = 0                    # меняется при каждом запуске/остановке — старый опрос не в счёт
        self._tg_polling = False
        self._tg_fast_until = 0.0
        self._tg_launch_app = False         # включили тумблером — после запуска открыть Telegram
        self._tg_restoring_off = False      # временная остановка из кода (обновление) — не запоминать
        # Задаёт MainWindow: перечитать список DNS в «Параметрах» / открыть «Параметры».
        self.on_pairs_changed = None
        self.on_open_parameters = None
        self._tg = TgProxyManager(Path(self._config.get("_app_dir", ".")), self._config)
        # Та же защита, что и у DNS выше, но для TG Proxy.
        self._tg_toggle_event = threading.Event()
        self._tg_toggle_event.set()
        self._tg_poll_timer = QTimer(self)
        self._tg_poll_timer.setInterval(TG_POLL_MS)
        self._tg_poll_timer.timeout.connect(self._poll_tg)

        # ServiceManager хранит только один колбэк (не список подписчиков) —
        # на этом экране мы его единственный владелец, как раньше делал
        # MainWindow (self.manager.zapret.on_state_change = self._on_state_change)
        self._managerStateChanged.connect(self._on_manager_state_change)
        self._zapret_busy_timer = QTimer(self)
        self._zapret_busy_timer.setSingleShot(True)
        self._zapret_busy_timer.setInterval(ZAPRET_BUSY_DELAY_MS)
        self._zapret_busy_timer.timeout.connect(self._show_zapret_busy)
        if self.manager is not None:
            self.manager.zapret.on_state_change = lambda state: self._managerStateChanged.emit(state)

        self._build()
        self._load_presets()
        self._refresh_game_filter_ui()

        # Тумблер заблокирован, пока не убран exe автора из старой схемы (до 1.1):
        # если он запущен из нашей папки, он держит тот же порт.
        self._sw_tg.setEnabled(False)
        self._tile_tg.set_state(False, "Проверяю состояние…")
        self._tg.migrate_legacy_async(lambda: self._tgMigrated.emit())
        self._restore_dns()

        self._on_manager_state_change(self.manager.state if self.manager is not None else ServiceState.STOPPED)

        self._warmupDone.connect(self._on_warmup_done)
        self._start_windivert_warmup()

        self._idle_check_timer = QTimer(self)
        self._idle_check_timer.timeout.connect(self._maybe_run_idle_preset_check)
        self._idle_check_timer.start(IDLE_PRESET_CHECK_POLL_MS)

    # ── Прогрев WinDivert при старте ──────────────

    def _start_windivert_warmup(self) -> None:
        """Короткий прогрев WinDivert сразу при создании вкладки — первый
        запуск winws.exe после перезагрузки/установки Windows требует
        загрузки драйвера в ядро, без прогрева это может дать ложный
        FAIL первому пинг-тесту. «Запуск» и «Проверить все» заблокированы на время
        прогрева (~3-5 с); сам прогрев (core.zapret.manager.ServiceManager.
        warmup_windivert) пропускается, если Core не установлен или
        winws.exe уже где-то запущен — тогда on_done приходит почти
        сразу. Без manager (не должно случаться в реальном приложении)
        прогрев просто пропускаем."""
        if self.manager is None:
            return
        self._btn_toggle.setEnabled(False)
        self._btn_refresh.setEnabled(False)
        self.manager.warmup_windivert(on_done=lambda: self._warmupDone.emit())

    def _on_warmup_done(self) -> None:
        self._btn_toggle.setEnabled(True)
        self._btn_refresh.setEnabled(True)
        self._maybe_autostart_zapret()

    def _maybe_autostart_zapret(self) -> None:
        """zapret.autostart: запустить zapret с последним использованным
        пресетом (zapret.last_preset) сразу при старте FlowZap — без
        пинг-теста и без попытки установить Core. Если Core не
        установлен (пресетов нет) — ничего не делаем и ничего не ждём:
        при следующем запуске FlowZap, когда Core уже будет установлен
        вручную, автозапуск сработает сам."""
        if not self._config.get("zapret", {}).get("autostart", False):
            return
        if self.manager is None or self.manager.is_running:
            return
        presets = self._preset_menu.presets
        if not presets:
            return

        last_name = self._config.get("zapret", {}).get("last_preset", "")
        preset = next((p for p in presets if p["name"] == last_name), None) or presets[0]
        self._preset_menu.select_preset(preset, emit=False)
        log.info(f"Автозапуск zapret: {preset['name']}")
        self.manager.start_async(bat_path=preset["path"])

    # ── Фоновая перепроверка пресетов при простое ──

    def _maybe_run_idle_preset_check(self) -> None:
        """Еженедельная (на практике — раз в 3 дня) фоновая перепроверка
        пресетов: провайдер мог сменить блокировки, и старый пресет
        перестал работать, а пользователь давно этого не видел.
        Запускается только когда точно никому не мешает:
          - zapret сейчас не запущен — тест ненадолго держит WinDivert и
            завершает winws.exe через taskkill; если бы zapret работал,
            тест оборвал бы реальное соединение пользователя;
          - никакой пинг-тест уже не идёт;
          - система простаивает (GetLastInputInfo — ввод с клавиатуры
            или мыши где угодно в Windows, не только в FlowZap) от
            IDLE_PRESET_CHECK_IDLE_THRESHOLD_SEC;
          - с прошлой такой проверки прошло не меньше
            IDLE_PRESET_CHECK_INTERVAL_SEC.
        Идёт тем же путём, что и ручной клик «Проверить все» (auto=False) —
        кнопка «Запустить» остаётся кликабельной и мгновенно прерывает
        фоновый тест, если пользователь вернулся за компьютер раньше,
        чем тест успел закончиться."""
        if self._ping_mgr.is_testing:
            return
        if self.manager is None or self.manager.is_running:
            return
        if not self._preset_menu.presets:
            return

        zapret_cfg = self._config.setdefault("zapret", {})
        last_check = zapret_cfg.get("last_idle_preset_check", 0)
        if time.time() - last_check < IDLE_PRESET_CHECK_INTERVAL_SEC:
            return
        if get_idle_seconds() < IDLE_PRESET_CHECK_IDLE_THRESHOLD_SEC:
            return

        log.info(
            "Простой системы ≥ 2 ч, с прошлой фоновой проверки пресетов "
            "прошло ≥ 3 дней — запускаю перепроверку"
        )
        # Пишем метку времени ДО запуска, а не после — иначе следующий
        # тик таймера (через 10 мин) во время того же многочасового
        # простоя запустил бы вторую проверку сразу за первой.
        zapret_cfg["last_idle_preset_check"] = time.time()
        if self._save_config_fn:
            self._save_config_fn()
        self._on_refresh_ping(auto=False, user=False)   # фоновая: без окна UAC

    # ── Построение UI ──────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        page = Page("")
        root.addWidget(page)

        # Объявления автора (core/announcements.py) — над плитками. Отступ до
        # плиток — внутри контейнера: при раскрытии высота растёт с нуля,
        # и плитки съезжают плавно, без скачка на величину отступа.
        top = QVBoxLayout()
        top.setSpacing(0)
        self._announce_holder = QWidget()
        self._announce_box = QVBoxLayout(self._announce_holder)
        self._announce_box.setContentsMargins(0, 0, 0, theme.metrics.padding_md)
        self._announce_box.setSpacing(theme.metrics.padding_md)
        self._announce_holder.hide()
        top.addWidget(self._announce_holder)
        top.addLayout(self._build_services_row())
        page.body.addLayout(top)

        self._presets_panel = PresetsPanel(self._ping_mgr)
        self._presets_panel.presetSelected.connect(self._on_preset_selected)
        self._presets_panel.btn_check.clicked.connect(lambda: self._on_refresh_ping())
        self._presets_panel.btn_game.clicked.connect(self._open_game_filter_menu)
        page.body.addWidget(self._presets_panel)

        # Старые имена — их использует логика ниже (прогрев, проверка, автозапуск).
        self._preset_menu = self._presets_panel
        self._btn_refresh = self._presets_panel.btn_check
        self._btn_toggle = self._sw_zapret

    # ── Объявления ──

    ANNOUNCE_OPEN_MS = 360      # плитки съезжают вниз
    ANNOUNCE_FADE_MS = 260      # плашка тает
    ANNOUNCE_CLOSE_MS = 360     # потом плитки поднимаются (как при появлении — наоборот)

    def _announce_bars(self) -> list[AnnouncementBar]:
        return [self._announce_box.itemAt(i).widget() for i in range(self._announce_box.count())
                if isinstance(self._announce_box.itemAt(i).widget(), AnnouncementBar)]

    def _announce_height(self) -> int:
        """Высота контейнера с плашками при его текущей ширине."""
        box = self._announce_box
        box.activate()
        width = self._announce_holder.width() or self.width()
        return box.totalHeightForWidth(width) if box.hasHeightForWidth() \
            else box.totalSizeHint().height()

    def show_announcements(self, items: list[dict], animate: bool = False) -> None:
        """Показать объявления (уже отобранные под эту версию) вместо прежних.
        animate — плитки плавно съезжают вниз, в открывшееся место всплывают
        плашки (как блоки при запуске окна)."""
        for bar in self._announce_bars():
            self._announce_box.removeWidget(bar)
            bar.deleteLater()
        if not items:
            self._announce_holder.hide()
            return
        bars = []
        for item in items:
            bar = AnnouncementBar(item, self._announce_holder)
            bar.dismissed.connect(self._dismiss_announcement)
            self._announce_box.addWidget(bar)
            bars.append(bar)
        if not (animate and theme.animations and self.isVisible()):
            self._announce_holder.setMaximumHeight(QWIDGETSIZE_MAX)
            self._announce_holder.show()
            return
        cascade_hide([[b] for b in bars])          # содержимое — до раскрытия не видно
        self._announce_holder.setMaximumHeight(0)
        self._announce_holder.show()

        def _expand() -> None:
            animate_height(self._announce_holder, 0, self._announce_height(), self.ANNOUNCE_OPEN_MS,
                           on_done=lambda: cascade_in([[b] for b in bars], first_delay_ms=0,
                                                      step_ms=140, duration_ms=900, lift=18))
        QTimer.singleShot(0, _expand)               # после раскладки — ширина известна

    def _dismiss_announcement(self, aid: str) -> None:
        dismissed = self._config.setdefault("announcements", {}).setdefault("dismissed", [])
        if aid not in dismissed:
            dismissed.append(aid)
            if self._save_config_fn:
                self._save_config_fn()
        log.info(f"Объявление «{aid}» скрыто")
        bar = next((b for b in self._announce_bars() if b.property("aid") == aid), None)
        if bar is None:
            return
        holder = self._announce_holder

        def _collapse() -> None:
            start = holder.height()
            holder.setMaximumHeight(start)          # без скачка, пока плашка ещё видна
            self._announce_box.removeWidget(bar)    # дотаивает поверх, её обрежет контейнер
            left = bool(self._announce_bars())
            end = self._announce_height() if left else 0
            if not theme.animations:
                bar.deleteLater()
                holder.setMaximumHeight(QWIDGETSIZE_MAX)
                holder.setVisible(left)
                return
            animate_height(holder, start, end, self.ANNOUNCE_CLOSE_MS,
                           on_done=None if left else holder.hide, easing=QEasingCurve.InOutCubic)

        if not theme.animations:
            _collapse()
            return
        # Зеркально появлению: сначала плашка тает, потом плитки поднимаются

        def _faded() -> None:
            _collapse()
            bar.deleteLater()

        fade_out(bar, self.ANNOUNCE_FADE_MS, on_done=_faded)

    def _build_services_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.metrics.padding_md)

        # ── Обход (zapret) ──
        self._tile_zapret = ServiceTile(
            Glyph.SHIELD, "Обход блокировок", "Пресет",
            "Запускает zapret (winws.exe) с выбранным пресетом. Пресет выбирается "
            "в блоке «Пресеты» ниже — там же видно, какой сейчас работает лучше всего.",
        )
        self._sw_zapret = self._tile_zapret.switch
        self._sw_zapret.clicked.connect(self._on_toggle)
        self._zapret_dot = StatusDot(10)
        self._tile_zapret.slot.addWidget(self._zapret_dot, alignment=Qt.AlignVCenter)
        self._zapret_preset_lbl = label("—", role="value")
        self._tile_zapret.slot.addWidget(self._zapret_preset_lbl, alignment=Qt.AlignVCenter)
        self._zapret_gf_chip = label("", role="pill")
        self._tile_zapret.slot.addWidget(self._zapret_gf_chip, alignment=Qt.AlignVCenter)
        self._tile_zapret.slot.addStretch(1)
        row.addWidget(self._tile_zapret, stretch=1)

        # ── DNS ──
        self._tile_dns = ServiceTile(
            Glyph.GLOBE, "Свой DNS", "Сервер",
            "Переключает DNS сетевого адаптера на выбранный сервер. Список серверов — "
            "во вкладке «Параметры». При выходе из FlowZap DNS возвращается "
            "к автоматическому (DHCP).",
        )
        self._sw_dns = self._tile_dns.switch
        self._sw_dns.clicked.connect(self._on_dns_toggle)
        self._dns_field = FieldButton()
        self._dns_field.clicked.connect(self._open_dns_menu)
        self._tile_dns.slot.addWidget(self._dns_field)
        row.addWidget(self._tile_dns, stretch=1)

        # ── TG Proxy ──
        self._tile_tg = ServiceTile(
            Glyph.SEND, "Telegram Proxy", "Telegram",
            "Локальный прокси для Telegram (TG WS Proxy от Flowseal) — работает внутри "
            "FlowZap, отдельно от обхода. Telegram подключается к нему один раз, дальше "
            "FlowZap сам видит, идёт ли он через прокси. Настройки — во вкладке «Параметры».",
        )
        self._sw_tg = self._tile_tg.switch
        self._sw_tg.clicked.connect(self._on_tg_proxy_toggle)
        # В слоте — либо кнопка подключения (пока Telegram ни разу не подключался
        # с текущими портом и секретом), либо состояние Telegram
        self._btn_tg_open = FieldButton("Подключить в Telegram", trailing=Glyph.OPEN)
        self._btn_tg_open.set_centered(True)
        self._btn_tg_open.clicked.connect(self.connect_telegram)
        self._tile_tg.slot.addWidget(self._btn_tg_open)
        self._tg_status_row = QWidget()
        status = QHBoxLayout(self._tg_status_row)
        status.setContentsMargins(0, 0, 0, 0)
        status.setSpacing(10)
        self._tg_dot = StatusDot(10)
        status.addWidget(self._tg_dot, alignment=Qt.AlignVCenter)
        self._tg_state_lbl = label("", role="value")
        status.addWidget(self._tg_state_lbl, alignment=Qt.AlignVCenter)
        status.addStretch(1)
        self._btn_tg_reconnect = button("Подключить", variant="link")
        self._btn_tg_reconnect.setToolTip("Открыть Telegram и подключить прокси заново")
        self._btn_tg_reconnect.clicked.connect(self.connect_telegram)
        status.addWidget(self._btn_tg_reconnect, alignment=Qt.AlignVCenter)
        self._tile_tg.slot.addWidget(self._tg_status_row)
        row.addWidget(self._tile_tg, stretch=1)

        self._refresh_dns_button_style()
        self._refresh_tg_tile()
        return row

    def _set_message(self, text: str, tone: str | None = None) -> None:
        """Строка сообщений в блоке «Пресеты». tone — success / warning / error / accent."""
        self._presets_panel.set_message(text, tone)

    def _refresh_zapret_tile(self) -> None:
        """Слот плитки обхода: текущий пресет, его итог проверки и подсказка о лучшем."""
        preset = self._presets_panel.selected_preset()
        if preset is None:
            self._zapret_preset_lbl.setText("не установлен")
            self._zapret_dot.set_color("text_muted")
        else:
            self._zapret_preset_lbl.setText(preset["name"])
            self._zapret_dot.set_color(_STATUS_COLORS.get(self._presets_panel.status_of(preset["name"]), "text_muted"))
        self._zapret_preset_lbl.setToolTip(self._zapret_preset_lbl.text())

        mode = {"tcp": "TCP", "udp": "UDP", "all": "TCP и UDP"}[self._game_filter_mode]
        self._zapret_gf_chip.setText(f"+ Game Filter {mode}")
        self._zapret_gf_chip.setVisible(self._game_filter_enabled)

        if self._zapret_error:
            reason = self.manager.last_error if self.manager is not None else None
            self._tile_zapret.set_hint(
                reason or "winws.exe завершился с ошибкой — выберите другой пресет ниже и включите снова", "error")
            return
        if preset is None:
            self._tile_zapret.set_hint("Включите — zapret скачается и установится сам")
            return
        if self._checking:
            # Подсказку не трогаем — остаётся вывод прошлой проверки. Новый
            # вывод о лучшем — только по полной проверке, а не по её части.
            return
        best = self._presets_panel.best()
        if best is None:
            self._tile_zapret.set_hint("Пресеты не проверены — проверка в блоке ниже")
        elif best["name"] == preset["name"]:
            self._tile_zapret.set_hint("Лучший по последней проверке")
        else:
            self._tile_zapret.set_hint(f"Есть лучше: {best['name']} — в списке ниже ↓", "warning")

    # ── Game Filter (кнопка в блоке «Пресеты») ──

    _GAME_FILTER_OPTIONS = [
        ("off", "Выключен", "Игровой трафик не трогается"),
        ("tcp", "TCP", "Для игр с подключением по TCP"),
        ("udp", "UDP", "Для большинства онлайн-игр и голоса"),
        ("all", "TCP и UDP", "Максимальный охват, чуть больше нагрузка"),
    ]

    def _open_game_filter_menu(self) -> None:
        current = self._game_filter_mode if self._game_filter_enabled else "off"
        _open_options(
            self._presets_panel.btn_game,
            [(k, t, d, None) for k, t, d in self._GAME_FILTER_OPTIONS],
            current, self._on_game_filter_pick, width=300,
            intro="Обход для онлайн-игр: игровые порты и списки. Нужен, только если не работают игры. "
                  "Если обход включён, он перезапустится.",
        )

    def _on_game_filter_pick(self, key: str) -> None:
        enabled = key != "off"
        if enabled == self._game_filter_enabled and (not enabled or key == self._game_filter_mode):
            return
        self._game_filter_enabled = enabled
        if enabled:
            self._game_filter_mode = key
        self._apply_game_filter_state()

    # ── Пресеты и пинг ──────────────────────────────

    def _load_presets(self) -> None:
        presets = list_presets(self._zapret_dir)
        self._ping_mgr.load_cached()
        self._preset_menu.set_presets(presets)
        last_name = self._config.get("zapret", {}).get("last_preset", "")
        last = next((p for p in presets if p["name"] == last_name), None)
        if last is not None:
            self._preset_menu.select_preset(last, emit=False)
        self._set_message("")
        self._refresh_zapret_tile()

    def _on_preset_selected(self, preset: dict) -> None:
        log.debug(f"Пресет выбран: {preset['name']}")
        self._config.setdefault("zapret", {})["last_preset"] = preset["name"]
        if self._save_config_fn:
            self._save_config_fn()
        self._refresh_zapret_tile()
        # Выбор применяется сразу: если обход работает — перезапуск с новым пресетом.
        if self.manager is not None and self.manager.is_running:
            self.manager.restart_async(bat_path=preset["path"])

    def _on_refresh_ping(self, auto: bool = False, user: bool = True) -> None:
        """user — проверку запустил пользователь (или она идёт сразу после его
        «Запустить»): тогда без фоновой службы можно показать окно её установки."""
        if self._ping_mgr.is_testing:
            return
        if self.manager is not None and self.manager.is_running:
            self._set_message(
                "Сначала остановите zapret — проверка запускает пресеты по очереди.", "warning"
            )
            return
        self._auto_testing = auto
        self._checking = True
        self._refresh_zapret_tile()
        self._btn_refresh.setEnabled(False)
        # Ручной тест можно прервать кнопкой «Запустить» (см. _on_toggle);
        # автоматический (сразу после установки Core) — нет, ждём до конца.
        self._btn_toggle.setEnabled(not auto)
        self._set_message("Проверка пресетов…", "accent")
        self._presets_panel.progress.start(len(self._preset_menu.presets))
        self._ping_mgr.run_tests(self._preset_menu.presets, allow_install=user)

    def _on_ping_status_updated(self, name: str, status: PingStatus) -> None:
        self._preset_menu.update_status(name, status)
        if status == PingStatus.CHECKING:
            names = [p["name"] for p in self._preset_menu.presets]
            pos = names.index(name) + 1 if name in names else 0
            self._set_message(f"Проверяю {pos} из {len(names)}: {name}…", "accent")
            self._presets_panel.progress.set_done(pos - 1)
        self._refresh_zapret_tile()

    def _on_ping_tests_done(self, ok: bool, msg: str) -> None:
        self._checking = False
        if ok:
            self._presets_panel.progress.finish()
        else:
            self._presets_panel.progress.stop()
        self._btn_refresh.setEnabled(True)
        self._btn_toggle.setEnabled(True)
        self._set_message(msg, None if ok else "error")
        self._auto_testing = False
        self._preset_menu.render()
        self._refresh_zapret_tile()
        if not ok:
            log.error(f"Тестирование пресетов завершилось с ошибкой: {msg}")

        if self._pending_start_after_test:
            self._pending_start_after_test = False
            bat = self._get_current_bat()
            if bat is not None and self.manager is not None:
                self.manager.start_async(bat_path=bat, allow_install=True)

    # ── Старт/стоп zapret ────────────────────────────

    def _get_current_bat(self) -> Path | None:
        """Путь к .bat выбранного в дропдауне пресета (или None, если пресетов нет)."""
        preset = self._preset_menu.selected_preset()
        return preset["path"] if preset else None

    def _on_manager_state_change(self, state: ServiceState) -> None:
        on = state in (ServiceState.RUNNING, ServiceState.STARTING)
        if state in (ServiceState.STARTING, ServiceState.STOPPING):
            # Запуск/остановка обычно занимают доли секунды — жёлтое
            # «Запускается» только мелькало бы. Плитку не трогаем; надпись
            # появится, только если операция затянулась (_show_zapret_busy).
            self._sw_zapret.setEnabled(False)
            self._sw_zapret.setChecked(on)
            self._zapret_busy_timer.start()
            return
        self._zapret_busy_timer.stop()
        text, tone, _dot = _SERVICE_STATE_LABELS.get(state, ("Неизвестно", None, "text_muted"))
        if state == ServiceState.ERROR and not self._zapret_error:
            self._zapret_error = True
            self._tile_zapret.set_error(True)
        elif on:
            self._clear_zapret_error()
        text = {ServiceState.RUNNING: "Работает", ServiceState.STOPPED: "Выключен"}.get(state, text)
        if self._zapret_error and state in (ServiceState.ERROR, ServiceState.STOPPED):
            text, tone = "Ошибка", "error"
        self._tile_zapret.set_state(state == ServiceState.RUNNING, text,
                                    tone if state != ServiceState.RUNNING else None)
        busy = state in (ServiceState.STARTING, ServiceState.STOPPING)
        self._sw_zapret.setEnabled(not busy and not self._installing_core)
        self._sw_zapret.setChecked(on)
        pid = self.manager.pid if self.manager is not None else None
        self._sw_zapret.setToolTip(f"winws.exe · PID {pid}" if pid and on else "")
        self._refresh_zapret_tile()

    def _show_zapret_busy(self) -> None:
        """Запуск/остановка идёт дольше ZAPRET_BUSY_DELAY_MS — показать это на плитке."""
        state = self.manager.state if self.manager is not None else ServiceState.STOPPED
        if state not in (ServiceState.STARTING, ServiceState.STOPPING):
            return
        # Как у плиток DNS и TG Proxy: обычный цвет, «…ю…» (без жёлтого)
        text = "Запускаю…" if state == ServiceState.STARTING else "Останавливаю…"
        self._tile_zapret.set_state(self._tile_zapret.property("active"), text)

    # ── Game Filter ──────────────────────────────────

    def _game_filter_flag_file(self) -> Path:
        return self._zapret_dir / "utils" / "game_filter.enabled"

    def _init_game_filter_state(self) -> None:
        self._game_filter_mode = "all"
        self._game_filter_enabled = False
        flag_file = self._game_filter_flag_file()
        try:
            if flag_file.exists():
                content = flag_file.read_text(encoding="utf-8", errors="replace").strip().lower()
                if content in ("tcp", "udp", "all"):
                    self._game_filter_mode = content
                self._game_filter_enabled = True
        except Exception as exc:
            log.warning(f"Не удалось прочитать game_filter.enabled: {exc}")

    def _refresh_game_filter_ui(self) -> None:
        mode = {"tcp": "TCP", "udp": "UDP", "all": "TCP и UDP"}[self._game_filter_mode]
        self._presets_panel.btn_game.setText(
            f"Game Filter: {mode if self._game_filter_enabled else 'выкл'}  ▾")
        self._refresh_zapret_tile()

    def _apply_game_filter_state(self) -> None:
        """Записать/удалить utils/game_filter.enabled под текущее состояние
        и, если zapret уже запущен, перезапустить его — аргументы winws.exe
        фиксируются при старте процесса и на лету не подхватятся (та же
        логика, что и при смене DNS в старом коде)."""
        flag_file = self._game_filter_flag_file()
        try:
            flag_file.parent.mkdir(parents=True, exist_ok=True)
            if self._game_filter_enabled:
                flag_file.write_text(self._game_filter_mode, encoding="utf-8")
            elif flag_file.exists():
                flag_file.unlink()
        except Exception as exc:
            log.error(f"Не удалось обновить game_filter.enabled: {exc}")
            self._set_message(f"Ошибка Game Filter: {exc}", "error")
            self._game_filter_enabled = flag_file.exists()
            self._refresh_game_filter_ui()
            return

        self._refresh_game_filter_ui()

        if self.manager is not None and self.manager.is_running:
            bat = self._get_current_bat()
            if bat is not None:
                self.manager.restart_async(bat_path=bat)

    # ── Запуск / DNS / TG Proxy ──────────────────────

    def _clear_zapret_error(self) -> None:
        if self._zapret_error:
            self._zapret_error = False
            self._tile_zapret.set_error(False)

    def _on_toggle(self) -> None:
        state = self.manager.state if self.manager is not None else ServiceState.STOPPED
        self._sw_zapret.setChecked(state in (ServiceState.RUNNING, ServiceState.STARTING))
        self._clear_zapret_error()      # новая попытка — красное гаснет
        self._refresh_zapret_tile()
        if self.manager is None:
            log.warning("_on_toggle: ZapretManager не передан")
            return

        if self.manager.is_running:
            self.manager.stop_async()
            return

        if self._ping_mgr.is_testing:
            # Сюда попадаем только во время РУЧНОГО теста — во время
            # авто-теста после установки Core кнопка заблокирована
            # (_on_refresh_ping(auto=True)), клик до неё не доходит.
            self._pending_start_after_test = True
            self._btn_toggle.setEnabled(False)
            self._set_message("Останавливаю проверку пресетов…", "accent")
            self._ping_mgr.stop_tests()
            return

        bat = self._get_current_bat()
        if bat is None:
            self._install_core_then_test()
            return

        self.manager.start_async(bat_path=bat, allow_install=True)

    def _install_core_then_test(self) -> None:
        """Пресетов нет — качаем Core, затем прогоняем пинг-тест.
        Не стартуем zapret вслепую на непроверенном пресете."""
        if self._installing_core:
            return
        self._installing_core = True
        self._btn_toggle.setEnabled(False)
        self._tile_zapret.set_state(False, "Установка…", "accent")
        self._set_message("Пресеты не найдены — устанавливаю zapret…", "accent")

        download_and_install_core(
            self._zapret_dir,
            on_progress=lambda msg: self._coreInstallProgress.emit(msg),
            on_done=lambda ok, msg: self._coreInstallDone.emit(ok, msg),
        )

    def _on_core_install_progress(self, msg: str) -> None:
        self._set_message(msg, "accent")

    def _on_core_install_done(self, ok: bool, msg: str) -> None:
        self._installing_core = False
        self._btn_toggle.setEnabled(True)
        self._tile_zapret.set_state(False, "Выключен")
        self._set_message(msg, "success" if ok else "error")

        if not ok:
            log.error(f"Установка Core не удалась: {msg}")
            return

        self._load_presets()
        if self._preset_menu.presets:
            self._on_refresh_ping(auto=True)

    def _on_dns_toggle(self) -> None:
        pair = dns_manager.get_active_pair(self._config)
        if not self._dns_enabled and pair is None:
            self._tile_dns.set_hint("Нет серверов — добавьте их во вкладке «Параметры»", "warning")
            self._sw_dns.setChecked(False)
            return

        target_enabled = not self._dns_enabled
        self._tile_dns.set_error(False)     # новая попытка — красное гаснет
        self._sw_dns.setEnabled(False)
        self._tile_dns.set_state(target_enabled, "Применяю…")

        self._apply_dns_async_tracked(target_enabled, pair, user=True)
        # Оптимистично фиксируем целевое состояние сразу — если применение
        # не удастся, _on_dns_toggle_done откатит его обратно.
        self._dns_enabled = target_enabled

    def _apply_dns_async_tracked(self, enable: bool, pair, user: bool = False) -> None:
        """dns_manager.apply_dns_async + отмечаем операцию как "в процессе"
        до её реального завершения в фоновом потоке (self._dns_toggle_event) —
        нужно только shutdown()'у, чтобы не гонять netsh параллельно со своим
        сбросом DNS при быстром выходе сразу после включения/выключения."""
        self._dns_toggle_event.clear()

        def _on_done(ok: bool, err: str) -> None:
            self._dns_toggle_event.set()
            self._dnsToggleDone.emit(ok, err if not ok else "")

        # user — нажал сам: без фоновой службы можно показать окно её установки
        dns_manager.apply_dns_async(enable=enable, pair=pair, on_done=_on_done,
                                    allow_install=user, engine_dir=self._zapret_dir / "bin")

    def _on_dns_toggle_done(self, ok: bool, error: str) -> None:
        self._sw_dns.setEnabled(True)

        if not ok:
            self._dns_enabled = not self._dns_enabled  # откат
            self._refresh_dns_button_style()
            self._tile_dns.set_hint(f"Не удалось: {error}", "error")
            self._tile_dns.set_error(True)  # как у обхода: тряска и красная плитка
            return

        self._remember_state("dns", self._dns_enabled)
        self._refresh_dns_button_style()

    def _refresh_dns_button_style(self) -> None:
        self._sw_dns.setChecked(self._dns_enabled)
        pair = dns_manager.get_active_pair(self._config)
        if pair is None:
            self._dns_field.setText("Добавить сервер")
            self._dns_field.set_secondary("")
        else:
            self._dns_field.setText(pair.get("name") or pair.get("ipv4_main") or "")
            self._dns_field.set_secondary(pair.get("ipv4_main") or pair.get("ipv6_main") or "")
        self._tile_dns.set_state(self._dns_enabled, "Включён" if self._dns_enabled else "Выключен")
        self._tile_dns.set_hint("При выходе вернётся DNS провайдера" if self._dns_enabled
                                else "Сейчас используется DNS провайдера")

    def _open_dns_menu(self) -> None:
        """Выбор активного сервера: он всегда первый в config["dns"]["pairs"]
        (см. core/dns/manager.py), поэтому выбор — это перенос пары в начало."""
        pairs = [p for p in self._config.get("dns", {}).get("pairs", []) if isinstance(p, dict)]
        if not pairs:
            if self.on_open_parameters:
                self.on_open_parameters()
            return
        options = []
        for i, p in enumerate(pairs):
            ips = " · ".join(x for x in (p.get("ipv4_main") or p.get("main", ""), p.get("ipv4_backup") or p.get("backup", "")) if x)
            options.append((i, p.get("name") or ips, ips, None))
        _open_options(self._dns_field, options, 0, self._on_dns_pair_pick, width=self._dns_field.width())

    def _on_dns_pair_pick(self, index: int) -> None:
        pairs = self._config.get("dns", {}).get("pairs", [])
        if index <= 0 or index >= len(pairs):
            return
        pairs.insert(0, pairs.pop(index))
        if self._save_config_fn:
            self._save_config_fn()
        if self.on_pairs_changed:
            self.on_pairs_changed()
        self.on_dns_changed()

    # ── Восстановление при запуске (Настройки → «Восстанавливать DNS и TG Proxy») ──

    def _restore_enabled(self) -> bool:
        return self._config.get("ui", {}).get("restore_state", True)

    def _remember_state(self, key: str, enabled: bool) -> None:
        """Что включено — в config["state"]. Пишется при каждом включении/
        выключении, а не при выходе: выход сам всё выключает, а после
        аварийного закрытия запись тоже должна остаться верной."""
        state = self._config.setdefault("state", {})
        if state.get(key) != enabled:
            state[key] = enabled
            if self._save_config_fn:
                self._save_config_fn()

    def _restore_dns(self) -> None:
        if not self._restore_enabled() or not self._config.get("state", {}).get("dns"):
            return
        pair = dns_manager.get_active_pair(self._config)
        if pair is None:
            return
        log.info("Восстанавливаю DNS — был включён при выходе")
        self._sw_dns.setEnabled(False)
        self._tile_dns.set_state(True, "Применяю…")
        # Без окна установки службы: его показываем только на нажатие пользователя
        self._apply_dns_async_tracked(True, pair)
        self._dns_enabled = True

    def on_dns_changed(self) -> None:
        """Вызывается ParametersTab (on_dns_changed=...), когда пользователь
        выбрал другую активную DNS-пару. Если DNS сейчас включён —
        переприменяем новую пару немедленно; если выключен — только
        обновляем подпись, новая пара применится сама при следующем включении."""
        if not self._dns_enabled:
            self._refresh_dns_button_style()
            return

        pair = dns_manager.get_active_pair(self._config)
        if pair is None:
            # Последнюю пару удалили, пока DNS был включён — держать
            # включённым нечего, сбрасываем на DHCP.
            self._dns_enabled = False

        self._tile_dns.set_error(False)
        self._sw_dns.setEnabled(False)
        self._tile_dns.set_state(self._dns_enabled, "Применяю…")

        self._apply_dns_async_tracked(pair is not None, pair)

    def _on_tg_migrated(self) -> None:
        self._sw_tg.setEnabled(True)
        self._tg_restart_pending = False    # прокси не работал — перезапускать нечего
        self._refresh_tg_tile()
        # Опрос идёт всегда: и при выключенном прокси точка показывает,
        # запущен ли Telegram (идёт мимо прокси — жёлтая)
        self._tg_poll_timer.start()
        self._poll_tg()
        if self._restore_enabled() and self._config.get("state", {}).get("tgproxy"):
            log.info("Восстанавливаю TG Proxy — был включён при выходе")
            self._on_tg_proxy_toggle()
            self._tg_launch_app = False     # Telegram при восстановлении сам не открываем

    def _on_tg_proxy_toggle(self) -> None:
        target_enabled = not self._tg_running
        self._tg_error = ""
        self._tile_tg.set_error(False)      # новая попытка — красное гаснет
        if target_enabled and not self._tg.is_available:
            self._install_tg_then_start()
            return
        self._sw_tg.setEnabled(False)
        self._tile_tg.set_state(target_enabled, "Запускаю…" if target_enabled else "Останавливаю…")
        self._tg_toggle_event.clear()
        self._tg_gen += 1
        self._tg_launch_app = target_enabled
        self._tg.set_running_async(target_enabled, on_done=self._tg_worker_done)

    def _tg_worker_done(self, running: bool, err: str) -> None:
        """Из фонового потока TgProxyManager. _tg_running пишем сразу здесь, а
        не только в _on_tg_proxy_done (GUI-поток, доставка через Qt-очередь) —
        иначе shutdown() при быстром выходе сразу после включения/выключения
        TG Proxy мог бы не увидеть актуальное значение."""
        self._tg_running = running
        self._tg_toggle_event.set()
        self._tgProxyDone.emit(running, err)

    def _install_tg_then_start(self) -> None:
        """Первое включение: сервера ещё нет — скачиваем, потом включаем."""
        if self._tg_installing:
            return
        self._tg_installing = True
        self._sw_tg.setEnabled(False)
        self._sw_tg.setChecked(False)
        self._tile_tg.set_state(False, "Установка…", "accent")
        self._refresh_tg_tile()
        download_and_install_tg_proxy(
            self._tg.dir,
            on_progress=lambda msg: self._tgInstallProgress.emit(msg),
            on_done=lambda ok, msg: self._tgInstallDone.emit(ok, msg),
        )

    def _on_tg_install_progress(self, msg: str) -> None:
        if not msg.startswith(("✓", "✗")):
            self._tile_tg.set_hint(msg, "accent")

    def _on_tg_install_done(self, ok: bool, msg: str) -> None:
        self._tg_installing = False
        self._sw_tg.setEnabled(True)
        if not ok or not self._tg.is_available:
            self._set_tg_error(f"Не удалось установить: {msg}")
            self._tg_open_after_start = False
            self._tg_restart_pending = False
            self._refresh_tg_tile()
            cb, self._tg_done_cb = self._tg_done_cb, None
            if cb:
                cb(False, self._tg_error)
            return
        self._on_tg_proxy_toggle()      # установлен — теперь включаем

    def _on_tg_proxy_done(self, running: bool, error: str) -> None:
        self._tg_running = running
        if not self._tg_restoring_off:
            self._remember_state("tgproxy", running)
        self._sw_tg.setEnabled(True)
        self._set_tg_error("" if running else error)
        # Запущен ли Telegram — не сбрасываем: он от прокси не зависит, иначе до
        # первой проверки точка мигала бы серым
        self._tg_clients, self._tg_wait_since, self._tg_rejected = [], None, False
        # С какими портом и секретом сервер запущен: настройки могли уже
        # смениться (перезапуск через 600 мс), а клиенты ещё старые
        self._tg_started_fp = tg_settings.fingerprint(self._tg.settings) if running else ""
        if running:
            self._tg_poll_timer.setInterval(TG_POLL_FAST_MS)
            self._tg_fast_until = time.monotonic() + TG_CONNECT_GRACE_SEC
            if self._tg_open_after_start:
                self._tg.open_in_telegram()     # ссылка сама откроет Telegram
            elif self._tg_launch_app and self._tg.settings.get("launch_telegram", True):
                self._tg.launch_telegram_async()
        else:
            self._tg_poll_timer.setInterval(TG_POLL_MS)
        self._poll_tg()
        self._tg_open_after_start = False
        self._tg_launch_app = False
        self._refresh_tg_tile()
        self._tg_restoring_off = False
        cb, self._tg_done_cb = self._tg_done_cb, None
        if cb:
            cb(running, error)
        if self._tg_restart_pending:
            self._tg_restart_pending = False
            self.on_tg_settings_changed()

    # ── TG Proxy: подключён ли Telegram ──

    def _poll_tg(self) -> None:
        if self._tg_polling:
            return
        self._tg_polling = True
        gen = self._tg_gen
        self._tg.poll_async(lambda alive, clients, app, path, rejected:
                            self._tgPolled.emit(gen, alive, clients, app, path, rejected))

    def _on_tg_polled(self, gen: int, alive: bool, clients: list, app_running: bool, app_path: str,
                      rejected: set) -> None:
        self._tg_polling = False
        # Пока шёл опрос, прокси включали, выключали или перезапускали — ответ устарел
        if gen != self._tg_gen or not self._sw_tg.isEnabled():
            return
        tg = self._tg.settings
        if app_path and tg.get("client_path") != app_path:
            tg["client_path"] = app_path        # отсюда запустим Telegram вместе с прокси
            if self._save_config_fn:
                self._save_config_fn()
        if not self._tg_running:
            if self._tg_app_running != app_running:
                self._tg_app_running = app_running
                self._refresh_tg_tile()
            return
        if not alive:
            log.error("TG WS Proxy неожиданно остановился")
            self._tg_gen += 1
            self._tg.stop()
            self._tg_running = False
            self._tg_clients, self._tg_rejected = [], False
            self._set_tg_error("TG WS Proxy неожиданно остановился — подробности в logs/tgproxy.log")
            self._refresh_tg_tile()
            return
        self._tg_clients = list(clients)
        self._tg_app_running = app_running
        # Telegram этого компьютера стучится со старым секретом: соединения
        # в таблице есть, но сервер их не пускает — это не «подключён»
        rejected_here = bool(rejected & _LOCAL_HOSTS)
        if rejected_here != self._tg_rejected:
            self._tg_rejected = rejected_here
            if rejected_here:
                log.warning("Telegram подключается к прокси со старым секретом или портом — "
                            "прокси отказывает (bad handshake в logs/tgproxy.log)")
        if self._tg_poll_timer.interval() != TG_POLL_MS and (clients or time.monotonic() > self._tg_fast_until):
            self._tg_poll_timer.setInterval(TG_POLL_MS)
        if clients or not app_running:
            self._tg_wait_since = None
        elif self._tg_wait_since is None:
            self._tg_wait_since = time.monotonic()
        if rejected_here:
            self._forget_tg_link()
        elif clients:
            self._remember_tg_link(clients)
        self._refresh_tg_tile()

    def _forget_tg_link(self) -> None:
        """Сервер отказывает Telegram — значит, в Telegram не эти порт и
        секрет: снова показать «Подключить в Telegram»."""
        tg = self._tg.settings
        if tg.get("linked"):
            tg["linked"] = ""
            if self._save_config_fn:
                self._save_config_fn()

    def _remember_tg_link(self, clients: list[str]) -> None:
        """Telegram подключился с текущими портом и секретом — кнопка
        «Подключить» больше не нужна, пока их не сменят."""
        tg = self._tg.settings
        fp = self._tg_started_fp
        if not fp or fp != tg_settings.fingerprint(tg):
            return      # порт/секрет уже сменили — эти клиенты подключены со старыми
        named = next((c for c in clients if c), "")
        changed = False
        if tg.get("linked") != fp:
            tg["linked"] = fp
            changed = True
            log.info(f"Telegram подключился к прокси: {client_title(named)}")
        if named and tg.get("client") != named:
            tg["client"] = named        # чтобы узнавать и неизвестные клиенты Telegram
            changed = True
        if changed and self._save_config_fn:
            self._save_config_fn()

    def tg_connection_status(self) -> tuple[str, str, str, str | None]:
        """(вид, коротко, пояснение, tone пояснения) — общее для плитки и
        «Параметров». Вид — ключ TG_DOT_COLORS; «connect» — показать кнопку
        «Подключить в Telegram»."""
        tg = self._tg.settings
        linked = tg_settings.is_linked(tg)
        if self._tg_installing:
            return "installing", "Скачивается…", "Скачиваю TG WS Proxy — это нужно один раз", "accent"
        if not self._tg_running:
            if not linked:
                kind, text = "connect", "Не подключён"
            elif self._tg_app_running:
                kind, text = "direct", "Без прокси"
            else:
                kind, text = "ready", "Не запущен"
            if self._tg_error:
                return kind, text, self._tg_error, "error"
            if kind == "direct":
                return kind, text, "Telegram открыт, но работает без прокси — включите прокси на главной", None
            if linked:
                return kind, text, "Включите прокси — Telegram подключится сам", None
            return kind, text, "Нажмите — прокси включится, а Telegram спросит «Подключить»", None
        if self._tg_rejected:
            return ("rejected", "Старый секрет",
                    "Telegram подключается со старым секретом или портом, и прокси его не пускает — "
                    "нажмите «Подключить в Telegram» и подтвердите", "warning")
        if self._tg_clients:
            names = ", ".join(dict.fromkeys(client_title(c) for c in self._tg_clients))
            return "connected", "Подключён", f"Через прокси: {names}", None
        if not linked:
            return "connect", "Не подключён", "Нажмите и подтвердите в Telegram «Подключить»", None
        if not self._tg_app_running:
            return "waiting", "Ждёт Telegram", "Запустите Telegram — он подключится сам", None
        if self._tg_wait_since is None or time.monotonic() - self._tg_wait_since < TG_CONNECT_GRACE_SEC:
            return "connecting", "Подключается…", "Telegram запущен, ждём подключения", None
        return ("bypass", "Не через прокси",
                "Telegram запущен, но прокси не использует — нажмите «Подключить»", "warning")

    def _refresh_tg_tile(self) -> None:
        """Плитка — как у обхода: в слоте имя клиента и цветная точка (состояние
        прокси и так написано сверху), подсказка внизу — только когда что-то
        не так. Подробные слова — в «Параметрах» (tg_connection_status)."""
        kind, _text, _detail, _tone = self.tg_connection_status()
        if not self._tg_installing:
            self._sw_tg.setChecked(self._tg_running)
            self._tile_tg.set_state(self._tg_running, "Работает" if self._tg_running else "Выключен")
        # Старый секрет — та же большая кнопка, что при первом подключении
        show_button = kind in ("connect", "rejected")
        self._btn_tg_open.setVisible(show_button)
        self._tg_status_row.setVisible(not show_button)
        # Пока Telegram подключается, точка держит прежний цвет: был жёлтым
        # (шёл мимо прокси) — станет сразу зелёным, без серого посередине
        if kind != "connecting":
            self._tg_dot.set_color(TG_DOT_COLORS.get(kind, "text_muted"))
        names = list(dict.fromkeys(client_title(c) for c in self._tg_clients))
        self._tg_state_lbl.setText(", ".join(names) if kind == "connected"
                                   else client_title(self._tg.settings.get("client") or "Telegram.exe"))
        self._btn_tg_reconnect.setVisible(kind == "bypass")
        if self._tg_error:
            self._tile_tg.set_hint(self._tg_error, "error")
        elif kind == "bypass":
            self._tile_tg.set_hint("Telegram не использует прокси", "warning")
        elif kind == "rejected":
            self._tile_tg.set_hint("Telegram со старым секретом — нажмите и подтвердите", "warning")
        elif kind == "installing":
            self._tile_tg.set_hint("Скачиваю TG WS Proxy…", "accent")
        else:
            self._tile_tg.set_hint("")
        self.tgStatusChanged.emit()

    def _set_tg_error(self, error: str) -> None:
        """Ошибка — как у обхода: тряска и красная плитка, пока не попробуют снова."""
        self._tg_error = error
        if error:
            self._tile_tg.set_error(True)

    # ── TG Proxy: действия для «Параметров» ──

    @property
    def tg_manager(self) -> TgProxyManager:
        return self._tg

    def connect_telegram(self) -> None:
        """«Подключить в Telegram»: прокси выключен — включить (и установить,
        если его ещё нет), потом открыть ссылку; Telegram спросит «Подключить»."""
        if self._tg_running:
            self._tg.open_in_telegram()
            return
        if not self._sw_tg.isEnabled():
            return          # идёт другая операция — по её окончании нажмут ещё раз
        self._tg_open_after_start = True
        self._on_tg_proxy_toggle()

    def relink_telegram(self) -> bool:
        """Сменили порт или секрет — Telegram с прежними работать не сможет,
        а настройки прокси в нём меняются только через ссылку с
        подтверждением. Если прокси работает и Telegram открыт — после
        перезапуска прокси сразу откроем ссылку. True — откроем."""
        self._tg_relink = self._tg_running and self._tg_app_running
        return self._tg_relink

    def on_tg_settings_changed(self) -> None:
        """«Параметры» изменили настройки прокси: если он работает — перезапустить."""
        if self._tg_installing or not self._sw_tg.isEnabled():
            self._tg_restart_pending = True     # применим, когда закончится текущая операция
            return
        if not self._tg_running:
            self._refresh_tg_tile()
            return
        if self._tg_relink:
            self._tg_open_after_start = True    # после перезапуска Telegram спросит «Подключить»
        self._tg_relink = False
        self._tg_error = ""
        self._tile_tg.set_error(False)
        self._sw_tg.setEnabled(False)
        self._tile_tg.set_state(True, "Перезапускаю…")
        self._tg_toggle_event.clear()
        self._tg_gen += 1
        self._tg.restart_async(on_done=self._tg_worker_done)

    # ── Публичный API для других вкладок (UpdatesTab) ────────────────────

    @property
    def zapret_dir(self) -> Path:
        """Папка zapret, из которой Dashboard читает пресеты. Обновление Core
        должно ставиться именно сюда, а не в жёстко заданный <app>/zapret."""
        return self._zapret_dir

    @property
    def tg_proxy_running(self) -> bool:
        return self._tg_running

    @property
    def is_testing_presets(self) -> bool:
        """Идёт ли сейчас пинг-тест пресетов (ручной, авто после установки
        Core или фоновый при простое — см. _maybe_run_idle_preset_check).
        UpdatesTab проверяет это перед обновлением Core: тест держит
        отдельный, не связанный с ZapretManager процесс winws.exe, и
        обновление Core прибило бы его через taskkill в середине
        прогона — тестируемый в этот момент пресет получил бы ложный
        FAIL не из-за себя, а из-за самого обновления."""
        return self._ping_mgr.is_testing

    def set_tg_proxy(self, enable: bool, on_done=None) -> None:
        """Привести TG Proxy к состоянию enable через тот же тумблер (его вид остаётся
        синхронным). on_done(running, error) зовётся в GUI-потоке по завершении;
        если состояние уже нужное или тумблер занят другой операцией — сразу."""
        if enable == self._tg_running:
            if on_done:
                on_done(self._tg_running, "")
            return
        if not self._sw_tg.isEnabled():
            if on_done:
                on_done(self._tg_running, "TG Proxy сейчас занят другой операцией — повторите позже.")
            return
        self._tg_done_cb = on_done
        self._tg_restoring_off = not enable
        self._on_tg_proxy_toggle()
        self._tg_launch_app = False     # вызов из кода (обновление), не пользователь — Telegram не открываем

    def on_core_updated(self) -> None:
        """Вызывается UpdatesTab после успешного обновления Core — перечитать пресеты."""
        self._load_presets()

    def shutdown(self) -> None:
        """Синхронно остановить всё, что включил пользователь: сбросить DNS на
        DHCP, остановить zapret и TG Proxy. Зовётся MainWindow из
        QApplication.aboutToQuit при РЕАЛЬНОМ выходе (не при сворачивании в трей).
        Асинхронные *_async тут не годятся — процесс завершится раньше, чем
        потоки успеют отработать. Шаги независимы: сбой одного не пропускает
        остальные. DNS трогаем только если он был включён нами.

        Если пользователь закрыл приложение сразу после клика по DNS/TG Proxy,
        фоновый поток apply_dns_async/set_running_async ещё может выполняться —
        сначала дожидаемся его (до 5 с), а не полагаемся на _dns_enabled/
        _tg_running, которые в этот момент ещё могут отражать не завершившуюся
        операцию. Без этого ожидания собственный синхронный сброс DNS ниже мог
        бы гонять netsh параллельно с тем же фоновым потоком."""
        # Окно прячем сразу: уборка ниже может занять несколько секунд, и всё
        # это время окно висело бы «замёрзшим».
        win = self.window()
        if win is not None:
            win.hide()
            QApplication.processEvents()

        if not self._dns_toggle_event.wait(timeout=5):
            log.warning("DNS-операция не завершилась за 5 с при выходе — сбрасываем DNS принудительно")
        if service_client.service_installed():
            # Через службу ждать не нужно: при отключении FlowZap она сама
            # сбрасывает включённый им DNS и останавливает его winws.
            if self._ping_mgr.is_testing:
                self._ping_mgr.stop_tests()
            log.info("Выход: DNS и обход сбросит фоновая служба")
            service_client.session.close()
        else:
            if self._dns_enabled:
                error = dns_manager.apply_dns(False, None)
                if error:
                    log.error(f"Не удалось сбросить DNS при выходе: {error}")
            if self.manager is not None:
                try:
                    self.manager.shutdown_all()   # блокирующий: terminate + wait(3) + kill
                except Exception:
                    log.exception("Не удалось остановить zapret при выходе")

        if not self._tg_toggle_event.wait(timeout=5):
            log.warning("Операция с TG Proxy не завершилась за 5 с при выходе")
        if self._tg_running:
            error = self._tg.stop_all()
            if error:
                log.error(f"TG Proxy при выходе: {error}")

        # Последним — может ждать до ~10 с (см. PresetPingManager.stop_tests_and_wait);
        # тестовый winws.exe не должен пережить приложение.
        if self._ping_mgr.is_testing and not service_client.service_installed():
            self._ping_mgr.stop_tests_and_wait()
