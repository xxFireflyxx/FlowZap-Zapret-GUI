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
    "dark": Palette(
        is_dark=True,
        bg_root="#131418",      bg_sidebar="#18191e",
        bg_card="#1d1f25",      bg_input="#25272e",
        bg_hover="#2b2e36",
        border="#2c2f37",       border_strong="#3b3f49",
        text_primary="#eceef2", text_secondary="#a6abb7",
        text_muted="#6f7480",
        accent="#5b8cff",       accent_hover="#7aa2ff",
        accent_text="#ffffff",
        success="#3ecf8e",      warning="#f2b544",
        error="#f2555a",
    ),
    "carbon": Palette(
        is_dark=True,
        bg_root="#000000",      bg_sidebar="#0a0a0b",
        bg_card="#111113",      bg_input="#1a1a1d",
        bg_hover="#222226",
        border="#222226",       border_strong="#323238",
        text_primary="#f3f3f4", text_secondary="#a1a1aa",
        text_muted="#64646c",
        accent="#2dd4bf",       accent_hover="#5eead4",
        accent_text="#03201c",
        success="#34d399",      warning="#fbbf24",
        error="#f87171",
    ),
    "earthy": Palette(
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
    "peach": Palette(
        is_dark=False,
        bg_root="#f6f1ea",      bg_sidebar="#fbf8f4",
        bg_card="#ffffff",      bg_input="#f4ede4",
        bg_hover="#ece3d8",
        border="#e7ddd0",       border_strong="#d6c8b6",
        text_primary="#2a211a", text_secondary="#6b5d50",
        text_muted="#a29483",
        accent="#dd6b33",       accent_hover="#c45a26",
        accent_text="#ffffff",
        success="#3f9a5b",      warning="#c98a1b",
        error="#cf4a3c",
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

# Ключи (dark/carbon/earthy/peach) остаются прежними — они уже записаны в
# config.toml у пользователей; меняются только названия и сами цвета.
THEME_NAMES: Dict[str, str] = {
    "earthy": "Светлая",
    "peach":  "Тёплая",
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
    card_bg = _rgba(p.bg_card, 0.82) if aurora else p.bg_card
    tile_on = _mix(p.bg_card, p.accent, 0.12 if p.is_dark else 0.07)
    tile_on_bg = _rgba(tile_on, 0.85) if aurora else tile_on
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

    QLabel[tone="success"] {{ color: {p.success}; }}
    QLabel[tone="warning"] {{ color: {p.warning}; }}
    QLabel[tone="error"]   {{ color: {p.error}; }}
    QLabel[tone="accent"]  {{ color: {p.accent}; }}
    QLabel[tone="muted"]   {{ color: {p.text_muted}; }}
    QLabel[role="pill"][tone="success"] {{ background-color: {p.success_soft}; }}
    QLabel[role="pill"][tone="warning"] {{ background-color: {p.warning_soft}; }}
    QLabel[role="pill"][tone="error"]   {{ background-color: {p.error_soft}; }}
    QLabel[role="pill"][tone="accent"]  {{ background-color: {p.accent_soft}; }}
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
    QPushButton[variant="segment"]:checked {{
        background-color: {p.bg_card}; color: {p.text_primary}; border: 1px solid {p.border_strong};
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
        self.palette    = THEMES["earthy"]
        self.typography = Typography()
        self.metrics    = Metrics()
        self._current   = "earthy"
        self.aurora     = True    # ui/widgets/aurora — шары на фоне окна
        self.animations = True    # ui.animations — волна, вкладки, каскад, тряска, полоса проверки

    def set_theme(self, name: str) -> None:
        if name not in THEMES:
            name = "earthy"
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
