"""
ui/theme.py
-----------
Централизованная тема FlowZap.

Все цвета живут здесь, виджеты их напрямую не вшивают: вместо
setStyleSheet(f"color: {p.error}") им ставят динамические свойства
(role / tone / variant — см. build_stylesheet) и подхватывают цвет из
общего QSS приложения. Поэтому тему можно сменить на лету
(Theme.apply) — достаточно пересобрать QSS и перерисовать окна.
Виджеты, которые рисуют себя сами (ui/widgets/), читают
theme.palette в paintEvent и тоже обновляются автоматически.
"""

from dataclasses import dataclass
from typing import Dict


def _mix(c1: str, c2: str, t: float) -> str:
    """Смешать два #RRGGBB: t=0 → c1, t=1 → c2. Для «мягких» фонов
    (подсветка активного пункта, плашки статусов) — сплошной цвет,
    а не rgba: QColor не разбирает rgba()-строки из QSS."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _luminance(c: str) -> float:
    def ch(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable(fg: str, bg: str, toward: str, minimum: float = 4.5) -> str:
    """Цветной текст на фоне — читаемым: если контраст ниже minimum (4.5:1 —
    порог для обычного текста), цвет понемногу смешивается с toward
    (основной цвет текста темы), пока не станет читаемым. Нужно для
    светлых акцентов вроде персикового: на мятной плашке #f8a978 почти
    не читался. Где контраста хватает — цвет не меняется."""
    for step in range(21):
        c = _mix(fg, toward, step / 20)
        if _contrast(c, bg) >= minimum:
            return c
    return toward


@dataclass(frozen=True)
class Palette:
    is_dark:        bool
    bg_root:        str     # фон окна
    bg_sidebar:     str
    bg_card:        str
    bg_input:       str     # поля ввода, вторичные кнопки
    bg_hover:       str
    border:         str
    border_strong:  str
    text_primary:   str
    text_secondary: str
    text_muted:     str
    accent:         str
    accent_hover:   str
    accent_text:    str     # текст на акцентном фоне
    success:        str
    warning:        str
    error:          str
    # Три цвета «северного сияния» (ui/widgets/aurora.py); пусто — общие
    # AURORA_DARK / AURORA_LIGHT. Свои у каждой темы: с общими темы
    # различались только кнопками — сияние сквозь карточки всё сглаживало.
    aurora:         tuple = ()
    aurora_alpha:   float = 0.0     # сила сияния; 0 — обычная (0.34 тёмные / 0.5 светлые)
    card_alpha:     float = 0.82    # непрозрачность карточек поверх сияния

    # Производные «мягкие» цвета — фон плашек и подсветок.
    @property
    def accent_soft(self) -> str:
        return _mix(self.bg_card, self.accent, 0.16 if self.is_dark else 0.10)

    @property
    def success_soft(self) -> str:
        return _mix(self.bg_card, self.success, 0.16 if self.is_dark else 0.12)

    @property
    def warning_soft(self) -> str:
        return _mix(self.bg_card, self.warning, 0.16 if self.is_dark else 0.12)

    @property
    def error_soft(self) -> str:
        return _mix(self.bg_card, self.error, 0.16 if self.is_dark else 0.10)

    @property
    def segment_on(self) -> str:
        """Ползунок выбранного пункта SegmentedControl — мягкий акцент от фона
        полоски (bg_input), а не карточки: иначе в «Персиковой» он мятный."""
        return _mix(self.bg_input, self.accent, 0.20 if self.is_dark else 0.16)

    @property
    def info(self) -> str:
        return self.accent


@dataclass(frozen=True)
class Typography:
    family_ui:   str = "Segoe UI"
    family_mono: str = "Cascadia Code"
    family_icon: str = "Segoe MDL2 Assets"
    size_xs:     int = 12
    size_sm:     int = 13
    size_md:     int = 14
    size_lg:     int = 16
    size_xl:     int = 20
    size_xxl:    int = 24


@dataclass(frozen=True)
class Metrics:
    sidebar_width:    int = 220
    corner_radius:    int = 16    # карточки, плитки
    corner_radius_sm: int = 8     # кнопки, поля
    padding_sm:       int = 8
    padding_md:       int = 16
    padding_lg:       int = 28
    nav_item_height:  int = 40
    button_height:    int = 34
    content_max_width: int = 1120


THEMES: Dict[str, Palette] = {
    # Нейтральная светлая — тема по умолчанию: белые карточки, синий акцент.
    "light": Palette(
        is_dark=False,
        bg_root="#f3f4f6",      bg_sidebar="#fbfbfc",
        bg_card="#ffffff",      bg_input="#f2f3f5",
        bg_hover="#e9ebef",
        border="#e2e5ea",       border_strong="#cfd4db",
        text_primary="#16181d", text_secondary="#4b5260",
        text_muted="#8b919c",
        accent="#2563eb",       accent_hover="#1d4ed8",
        accent_text="#ffffff",
        success="#16a34a",      warning="#d97706",
        error="#dc2626",
    ),
    # Палитры dark / earthy / peach / carbon — подобранные автором для
    # v0.5.x (фон, карточки, текст, акцент, статусы — те же значения;
    # text_muted подтянут до контраста 3:1 с карточкой — на крупных
    # карточках нового интерфейса прежний еле читался).
    # Чего в старой теме не было (рамка посильнее, текст на акцентной
    # кнопке, наведение) — выведено из её же цветов.
    # «Кофе с песком»: текст, статусы — из v0.5.x; холодный сине-серый фон
    # (#222831 / #393E46) заменён тёплым — на больших карточках нового
    # интерфейса он выглядел тускло, а песочный акцент на нём терялся.
    "dark": Palette(
        is_dark=True,
        bg_root="#1f1d1a",      bg_sidebar="#2e2a25",
        bg_card="#2e2a25",      bg_input="#26231f",
        bg_hover="#3a352f",
        border="#3f3a33",       border_strong="#51493f",
        text_primary="#DFD0B8", text_secondary="#a89e8a",
        text_muted="#7f7566",
        accent="#b8a586",       accent_hover="#c9b796",
        accent_text="#1f1d1a",
        success="#4a9c6a",      warning="#c49a3a",
        error="#c05040",
        aurora=("#c49a3a", "#a0674a", "#4a9c6a"),
    ),
    "carbon": Palette(
        is_dark=True,
        bg_root="#161616",      bg_sidebar="#1e1e1e",
        bg_card="#1e1e1e",      bg_input="#262626",
        bg_hover="#2a2a2a",
        border="#2e2e2e",       border_strong="#3a3a3a",
        text_primary="#e8edf2", text_secondary="#a0aab4",
        text_muted="#646c7a",
        accent="#1f6feb",       accent_hover="#3b82f6",
        accent_text="#ffffff",
        success="#22c55e",      warning="#f59e0b",
        error="#ef4444",
        aurora=("#1f6feb", "#0ea5e9", "#6366f1"),
    ),
    # Земляная: пастельный кремовый с тёмно-изумрудным акцентом (бежевый +
    # изумруд); текст и статусы — из v0.5.x.
    "earthy": Palette(
        is_dark=False,
        bg_root="#f8f3ea",      bg_sidebar="#f0e8da",
        bg_card="#f0e8da",      bg_input="#faf6ef",
        bg_hover="#e8dfcf",
        border="#e2d8c6",       border_strong="#d3c7b2",
        text_primary="#2d3a3c", text_secondary="#4f6a60",
        text_muted="#738586",
        accent="#1f6b4f",       accent_hover="#185a42",
        accent_text="#ffffff",
        success="#2f8f6a",      warning="#b07840",
        error="#a0403a",
        aurora=("#2f8f6a", "#e3a1a1", "#c9b8e8"),     # изумруд, пыльная роза, лаванда
        aurora_alpha=0.65,                             # пастель на кремовом еле видна при обычных 0.5
    ),
    "peach": Palette(
        is_dark=False,
        bg_root="#fcf9ea",      bg_sidebar="#badfdb",
        bg_card="#badfdb",      bg_input="#fcf9ea",
        bg_hover="#a8d4cf",
        border="#9ecfca",       border_strong="#86c2bc",
        text_primary="#2d2010", text_secondary="#4a7a76",
        text_muted="#5f7e75",
        accent="#f8a978",       accent_hover="#e09060",
        accent_text="#2d2010",
        success="#4a7c59",      warning="#e07840",
        error="#c05040",
        aurora=("#ff8a65", "#a78bdb", "#6fb7ef"),     # коралл, сирень, небо — мятное сливалось с карточками
        aurora_alpha=0.55,
    ),
    "neon": Palette(
        is_dark=True,
        bg_root="#07080f",      bg_sidebar="#0a0c17",
        bg_card="#0d0f1c",      bg_input="#121528",
        bg_hover="#1a1e38",
        border="#1b1f38",       border_strong="#2a2f52",
        text_primary="#e8ecff", text_secondary="#9aa0c8",
        text_muted="#6f7499",
        accent="#22e4ff",       accent_hover="#5aebff",
        accent_text="#04161c",
        success="#3cffa0",      warning="#ffb84d",
        error="#ff5c7a",
    ),
}

DEFAULT_THEME = "light"

# Ключи (dark/carbon/earthy/peach) остаются прежними — они уже записаны в
# config.toml у пользователей; меняются только названия и сами цвета.
THEME_NAMES: Dict[str, str] = {
    "light":  "Светлая",
    "earthy": "Земляная",
    "peach":  "Персиковая",
    "dark":   "Тёмная",
    "carbon": "Карбон",
    "neon":   "Неон",
}

# Цвета шаров «северного сияния» (ui/widgets/aurora.py): тёмные темы — неон,
# светлые — насыщенная пастель (бледная сливается с почти белым фоном).
AURORA_DARK  = ("#20e39c", "#7c5cff", "#ff3cac")   # зелёный — свой, не акцент: у бирюзового/синего акцента он сливался с фиолетовым
AURORA_LIGHT = ("#5b9bf8", "#a07cf2", "#fdba74")


def _rgba(color: str, alpha: float) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {round(alpha * 255)})"


def build_stylesheet(p: Palette, t: Typography, m: Metrics, aurora: bool = False) -> str:
    """Общий QSS приложения. Словарь свойств, которые понимают виджеты:
      QLabel[role]     — title / subtitle / section / caption / muted / hint / pill / dot
      [tone]           — success / warning / error / accent / muted (цвет текста; у pill — и фон)
      QPushButton[variant] — primary / danger / ghost / segment; [size="lg"] — крупная
      QFrame#card / #divider / #sidebar / #popup / #segmented / #tile[active]
    После смены свойства на лету — ui.widgets.restyle(widget)."""
    r, rs = m.corner_radius, m.corner_radius_sm
    # С сиянием фон окна рисует ui/widgets/aurora.AuroraBackground, страницы прозрачны,
    # а карточки чуть просвечивают — шары видно и сквозь них, не только в зазорах.
    card_bg = _rgba(p.bg_card, p.card_alpha) if aurora else p.bg_card
    tile_on = _mix(p.bg_card, p.accent, 0.12 if p.is_dark else 0.07)
    tile_on_bg = _rgba(tile_on, max(0.85, p.card_alpha)) if aurora else tile_on
    return f"""
    * {{
        font-family: '{t.family_ui}';
        font-size: {t.size_md}px;
        color: {p.text_primary};
        outline: 0;
    }}
    QMainWindow, QDialog, QMessageBox {{
        background-color: {p.bg_root};
    }}
    #page, #aurora, QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}
    QScrollArea, QScrollArea > QWidget > QWidget#pageContent {{
        background: transparent;
        border: none;
    }}

    /* ── Шапка окна с вкладками ── */
    #topbar {{ background: transparent; }}
    QFrame#tabs {{
        background-color: {_mix(p.bg_root, p.text_primary, 0.10 if p.is_dark else 0.055)};
        border-radius: 22px;
    }}
    QLabel#brand {{ font-size: 17px; font-weight: 600; }}

    /* ── Карточки ── */
    QFrame#card {{
        background-color: {card_bg};
        border: 1px solid {p.border};
        border-radius: {r}px;
    }}
    QFrame#tile {{
        background-color: {card_bg};
        border: 1px solid {p.border};
        border-radius: {r}px;
    }}
    QFrame#tile[active="true"] {{
        background-color: {tile_on_bg};
        border: 1px solid {_mix(p.bg_card, p.accent, 0.40)};
    }}
    QFrame#tile[error="true"] {{
        background-color: {_mix(p.bg_card, p.error, 0.14 if p.is_dark else 0.06)};
        border: 1px solid {_mix(p.bg_card, p.error, 0.70)};
    }}
    QFrame#bestRow {{
        background-color: {_mix(p.bg_card, p.accent, 0.10 if p.is_dark else 0.05)};
        border: 1px solid {_mix(p.bg_card, p.accent, 0.30)};
        border-radius: 14px;
    }}
    QFrame#presetRow {{ border: none; border-top: 1px solid {p.border}; background: transparent; }}
    QFrame#listRow {{ border: none; border-top: 1px solid {p.border}; background: transparent; }}
    QFrame#notes {{ background-color: {p.bg_input}; border: none; border-radius: 12px; }}
    QProgressBar {{ background-color: {p.bg_input}; border: none; border-radius: 3px; }}
    QProgressBar::chunk {{ background-color: {p.accent}; border-radius: 3px; }}
    QFrame#presetRow:hover {{ background-color: {p.bg_hover}; }}
    QFrame#divider {{
        background-color: {p.border};
        border: none;
        max-height: 1px;
        min-height: 1px;
    }}
    QFrame#iconBadge {{
        background-color: {p.bg_input};
        border-radius: 10px;
    }}
    QFrame#iconBadge[active="true"] {{
        background-color: {p.accent_soft};
    }}

    /* ── Текст ── */
    QLabel {{ background: transparent; }}
    QLabel[role="title"]    {{ font-size: {t.size_xxl}px; font-weight: 600; }}
    QLabel[role="subtitle"] {{ font-size: {t.size_sm}px; color: {p.text_secondary}; }}
    QLabel[role="section"]  {{ font-size: {t.size_lg}px; font-weight: 600; }}
    QLabel[role="strong"]   {{ font-weight: 600; }}
    QLabel[role="caption"]  {{ font-size: {t.size_xs}px; font-weight: 600; color: {p.text_muted}; }}
    QLabel[role="muted"]    {{ font-size: {t.size_sm}px; color: {p.text_secondary}; }}
    QLabel[role="hint"]     {{ font-size: {t.size_xs}px; color: {p.text_muted}; }}
    QLabel[role="status"]   {{ font-size: 22px; font-weight: 600; }}
    QLabel[role="value"]    {{ font-size: 17px; font-weight: 600; }}
    QLabel[role="dot"]      {{ font-size: 10px; color: {p.text_muted}; }}
    QLabel[role="glyph"]    {{ font-family: '{t.family_icon}'; font-size: 13px; color: {p.text_muted}; }}
    QLabel[role="pill"] {{
        font-size: {t.size_xs}px; font-weight: 600;
        color: {p.text_secondary}; background-color: {p.bg_input};
        border-radius: 10px; padding: 2px 10px;
    }}

    QLabel[tone="success"] {{ color: {readable(p.success, p.bg_card, p.text_primary)}; }}
    QLabel[tone="warning"] {{ color: {readable(p.warning, p.bg_card, p.text_primary)}; }}
    QLabel[tone="error"]   {{ color: {readable(p.error, p.bg_card, p.text_primary)}; }}
    QLabel[tone="accent"]  {{ color: {readable(p.accent, p.bg_card, p.text_primary)}; }}
    QLabel[tone="muted"]   {{ color: {p.text_muted}; }}
    QLabel[role="pill"][tone="success"] {{ background-color: {p.success_soft}; color: {readable(p.success, p.success_soft, p.text_primary)}; }}
    QLabel[role="pill"][tone="warning"] {{ background-color: {p.warning_soft}; color: {readable(p.warning, p.warning_soft, p.text_primary)}; }}
    QLabel[role="pill"][tone="error"]   {{ background-color: {p.error_soft}; color: {readable(p.error, p.error_soft, p.text_primary)}; }}
    QLabel[role="pill"][tone="accent"]  {{ background-color: {p.accent_soft}; color: {readable(p.accent, p.accent_soft, p.text_primary)}; }}
    QLabel[role="badge"] {{
        font-size: 11px; font-weight: 700; letter-spacing: 1px;
        color: {p.accent_text}; background-color: {p.accent};
        border-radius: 7px; padding: 3px 9px;
    }}

    QLabel a {{ color: {p.accent}; }}

    /* ── Кнопки ── */
    QPushButton {{
        background-color: {p.bg_input};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: {rs}px;
        padding: 0 14px;
        min-height: {m.button_height}px;
        font-weight: 600;
    }}
    QPushButton:hover   {{ background-color: {p.bg_hover}; border-color: {p.border_strong}; }}
    QPushButton:pressed {{ background-color: {p.border}; }}
    QPushButton:disabled {{ color: {p.text_muted}; background-color: {p.bg_input}; border-color: {p.border}; }}

    QPushButton[variant="primary"] {{
        background-color: {p.accent}; color: {p.accent_text}; border: 1px solid {p.accent};
    }}
    QPushButton[variant="primary"]:hover   {{ background-color: {p.accent_hover}; border-color: {p.accent_hover}; }}
    QPushButton[variant="primary"]:disabled {{
        background-color: {p.bg_hover}; color: {p.text_muted}; border-color: {p.bg_hover};
    }}
    QPushButton[variant="danger"] {{
        background-color: {p.error_soft}; color: {p.error}; border: 1px solid {p.error_soft};
    }}
    QPushButton[variant="danger"]:hover {{ border-color: {p.error}; }}
    QPushButton[variant="ghost"] {{
        background: transparent; border: 1px solid transparent; color: {p.text_secondary};
        padding: 0 10px;
    }}
    QPushButton[variant="ghost"]:hover {{ background-color: {p.bg_hover}; color: {p.text_primary}; }}
    QPushButton[variant="ghost"]:disabled {{ background: transparent; color: {p.text_muted}; }}
    QPushButton[variant="link"] {{
        background: transparent; border: 1px solid transparent; color: {p.accent};
        padding: 0 10px;
    }}
    QPushButton[variant="link"]:hover {{ background-color: {p.accent_soft}; }}
    QPushButton[size="lg"] {{
        min-height: 46px; border-radius: 10px; padding: 0 26px; font-size: {t.size_lg}px;
    }}

    /* Сегментированный переключатель */
    QFrame#segmented {{
        background-color: {p.bg_input};
        border: 1px solid {p.border};
        border-radius: {rs}px;
    }}
    QPushButton[variant="segment"] {{
        background: transparent; border: 1px solid transparent; border-radius: 6px;
        min-height: 26px; padding: 0 8px; color: {p.text_secondary}; font-weight: 600;
        font-size: {t.size_sm}px;
    }}
    QPushButton[variant="segment"]:hover   {{ color: {p.text_primary}; }}
    /* Ползунок выбранного пункта рисует сам SegmentedControl (переезжает плавно) */
    QPushButton[variant="segment"]:checked {{
        background: transparent; border: 1px solid transparent;
        color: {readable(p.accent, p.segment_on, p.text_primary)};
    }}
    QPushButton[variant="segment"]:disabled {{ color: {p.text_muted}; }}

    /* ── Поля ввода ── */
    QLineEdit, QComboBox {{
        background-color: {p.bg_input};
        border: 1px solid {p.border};
        border-radius: {rs}px;
        padding: 0 12px;
        min-height: {m.button_height}px;
        selection-background-color: {p.accent};
        selection-color: {p.accent_text};
    }}
    QLineEdit:hover, QComboBox:hover {{ border-color: {p.border_strong}; }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {p.accent}; }}
    QLineEdit[readOnly="true"] {{ color: {p.text_secondary}; }}
    QLineEdit[error="true"] {{ border: 1px solid {p.error}; }}
    QComboBox::drop-down {{ border: none; width: 30px; }}
    QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
    QComboBox QAbstractItemView {{
        background-color: {p.bg_card};
        border: 1px solid {p.border_strong};
        border-radius: {rs}px;
        padding: 4px;
        selection-background-color: {p.accent_soft};
        selection-color: {p.text_primary};
    }}

    /* ── Поп-апы и списки ── */
    QFrame#popup {{
        background-color: {p.bg_card};
        border: 1px solid {p.border_strong};
        border-radius: 10px;
    }}
    QListWidget {{
        background: transparent;
        border: none;
    }}
    QListWidget::item {{
        padding: 6px 8px;
        border-radius: 6px;
        color: {p.text_primary};
    }}
    QListWidget::item:hover    {{ background-color: {p.bg_hover}; }}
    QListWidget::item:selected {{ background-color: {p.accent_soft}; color: {p.text_primary}; }}

    QFrame#dnsRow {{
        background-color: transparent;
        border: 1px solid {p.border};
        border-radius: 10px;
    }}
    QFrame#dnsRow:hover {{ background-color: {p.bg_hover}; }}
    QFrame#dnsRow[active="true"] {{
        background-color: {p.accent_soft};
        border-color: {p.accent};
    }}

    QToolTip {{
        background-color: {p.bg_card};
        color: {p.text_primary};
        border: 1px solid {p.border_strong};
        padding: 6px 8px;
        border-radius: 6px;
    }}
    QMenu {{
        background-color: {p.bg_card};
        border: 1px solid {p.border_strong};
        padding: 4px;
    }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background-color: {p.bg_hover}; }}

    /* ── Скроллбары ── */
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border_strong}; border-radius: 3px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}
    """


class Theme:
    def __init__(self) -> None:
        self.palette    = THEMES[DEFAULT_THEME]
        self.typography = Typography()
        self.metrics    = Metrics()
        self._current   = DEFAULT_THEME
        self.aurora     = True    # ui/widgets/aurora — шары на фоне окна
        self.animations = True    # ui.animations — волна, вкладки, каскад, тряска, полоса проверки

    def set_theme(self, name: str) -> None:
        if name not in THEMES:
            name = DEFAULT_THEME
        self.palette  = THEMES[name]
        self._current = name

    @property
    def current(self) -> str:
        return self._current

    def apply(self, app) -> None:
        """Применить текущую тему ко всему приложению: стиль Fusion
        (одинаково выглядит на Windows 10/11 и не спорит с QSS),
        палитра Qt (для того, что QSS не покрывает) и общий QSS."""
        from PySide6.QtGui import QColor, QPalette

        p = self.palette
        app.setStyle("Fusion")
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(p.bg_root))
        pal.setColor(QPalette.WindowText, QColor(p.text_primary))
        pal.setColor(QPalette.Base, QColor(p.bg_input))
        pal.setColor(QPalette.AlternateBase, QColor(p.bg_card))
        pal.setColor(QPalette.Text, QColor(p.text_primary))
        pal.setColor(QPalette.Button, QColor(p.bg_input))
        pal.setColor(QPalette.ButtonText, QColor(p.text_primary))
        pal.setColor(QPalette.Highlight, QColor(p.accent))
        pal.setColor(QPalette.HighlightedText, QColor(p.accent_text))
        pal.setColor(QPalette.ToolTipBase, QColor(p.bg_card))
        pal.setColor(QPalette.ToolTipText, QColor(p.text_primary))
        pal.setColor(QPalette.PlaceholderText, QColor(p.text_muted))
        pal.setColor(QPalette.Link, QColor(p.accent))
        app.setPalette(pal)
        app.setStyleSheet(build_stylesheet(p, self.typography, self.metrics, aurora=self.aurora))
        for w in app.allWidgets():
            w.update()


theme = Theme()
