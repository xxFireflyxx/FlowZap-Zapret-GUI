"""
ui/theme.py
-----------
Централизованная тема FlowZap.
Поддерживает несколько тем — переключается в настройках.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Palette:
    bg_root:        str
    bg_sidebar:     str
    bg_card:        str
    bg_input:       str
    bg_hover:       str
    accent:         str
    accent_dim:     str
    accent_glow:    str
    text_primary:   str
    text_secondary: str
    text_muted:     str
    success:        str
    warning:        str
    error:          str
    info:           str
    border:         str
    border_light:   str
    # Цвета активных (включённых) кнопок
    btn_on_bg:      str
    btn_on_hover:   str
    btn_on_text:    str
    btn_on_border:  str


@dataclass(frozen=True)
class Typography:
    family_ui:     str = "Segoe UI"
    family_mono:   str = "Cascadia Code"
    size_xs:       int = 12
    size_sm:       int = 13
    size_md:       int = 14
    size_lg:       int = 16
    size_xl:       int = 20
    size_xxl:      int = 24
    size_hero:     int = 32
    weight_normal: str = "normal"
    weight_bold:   str = "bold"


@dataclass(frozen=True)
class Metrics:
    sidebar_width:    int = 200
    corner_radius:    int = 8
    corner_radius_sm: int = 4
    padding_md:       int = 16
    padding_lg:       int = 24
    nav_item_height:  int = 40
    button_height:    int = 38
    icon_size:        int = 18


LOG_COLORS: Dict[str, str] = {
    "INFO":    "#22c55e",
    "ERROR":   "#ef4444",
    "WARN":    "#f59e0b",
    "WARNING": "#f59e0b",
    "DEBUG":   "#8a9ab0",
    "DPI":     "#00b4d8",
    "CONNECT": "#a78bfa",
    "BLOCK":   "#ef4444",
    "BYPASS":  "#22c55e",
    "START":   "#22c55e",
    "STOP":    "#f59e0b",
    "DEFAULT": "#e8edf2",
}

THEMES: Dict[str, Palette] = {
    "dark": Palette(
        bg_root="#222831",      bg_sidebar="#393E46",
        bg_card="#393E46",      bg_input="#222831",
        bg_hover="#2e3540",
        accent="#948979",       accent_dim="#7a7060",
        accent_glow="#94897920",
        text_primary="#DFD0B8", text_secondary="#a89e8a",
        text_muted="#6e6658",
        success="#4a9c6a",      warning="#c49a3a",
        error="#c05040",        info="#948979",
        border="#4a4e56",       border_light="#3e4350",
        btn_on_bg="#3a3630",    btn_on_hover="#44403a",
        btn_on_text="#DFD0B8",  btn_on_border="#948979",
    ),
    "earthy": Palette(
        bg_root="#f0ebe3",      bg_sidebar="#e4dccf",
        bg_card="#e4dccf",      bg_input="#f0ebe3",
        bg_hover="#d8d0c4",
        accent="#7d9d9c",       accent_dim="#6a8a89",
        accent_glow="#7d9d9c20",
        text_primary="#2d3a3c", text_secondary="#576f72",
        text_muted="#8a9e9f",
        success="#4a7c59",      warning="#b07840",
        error="#a0403a",        info="#7d9d9c",
        border="#ccc5b8",       border_light="#d8d0c4",
        btn_on_bg="#d0e8e6",    btn_on_hover="#c0dbd8",
        btn_on_text="#4a7c59",  btn_on_border="#4a7c59",
    ),
    "carbon": Palette(
        bg_root="#161616",      bg_sidebar="#1e1e1e",
        bg_card="#1e1e1e",      bg_input="#161616",
        bg_hover="#2a2a2a",
        accent="#1f6feb",       accent_dim="#1a5fd4",
        accent_glow="#1f6feb20",
        text_primary="#e8edf2", text_secondary="#a0aab4",
        text_muted="#555e6d",
        success="#22c55e",      warning="#f59e0b",
        error="#ef4444",        info="#1f6feb",
        border="#2e2e2e",       border_light="#333333",
        btn_on_bg="#1a3a5c",    btn_on_hover="#1e4470",
        btn_on_text="#e8edf2",  btn_on_border="#1f6feb",
    ),
    "peach": Palette(
        bg_root="#fcf9ea",      bg_sidebar="#badfdb",
        bg_card="#badfdb",      bg_input="#fcf9ea",
        bg_hover="#a8d4cf",
        accent="#f8a978",       accent_dim="#e09060",
        accent_glow="#f8a97820",
        text_primary="#2d2010", text_secondary="#4a7a76",
        text_muted="#7ab0ac",
        success="#4a7c59",      warning="#e07840",
        error="#c05040",        info="#4a7a76",
        border="#9ecfca",       border_light="#b8ddd8",
        btn_on_bg="#e8f5ec",    btn_on_hover="#d8eede",
        btn_on_text="#4a7c59",  btn_on_border="#4a7c59",
    ),
}

THEME_NAMES: Dict[str, str] = {
    "earthy": "Земляная",
    "peach":  "Персиковая",
    "dark":   "Тёмная",
    "carbon": "Карбон",
}


class Theme:
    def __init__(self) -> None:
        self.palette    = THEMES["earthy"]
        self.typography = Typography()
        self.metrics    = Metrics()
        self.log_colors = LOG_COLORS
        self._current   = "earthy"

    def set_theme(self, name: str) -> None:
        if name in THEMES:
            self.palette  = THEMES[name]
            self._current = name
            self.typography = Typography()

    @property
    def current(self) -> str:
        return self._current

    def get_log_color(self, tag: str) -> str:
        return self.log_colors.get(tag.upper(), self.log_colors["DEFAULT"])

    def apply_ctk_theme(self) -> None:
        import customtkinter as ctk
        mode = "light"
        ctk.set_appearance_mode(mode)
        ctk.set_default_color_theme("dark-blue")


theme = Theme()
