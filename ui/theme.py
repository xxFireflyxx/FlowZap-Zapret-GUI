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
    "default": Palette(
        bg_root="#0d0f11",      bg_sidebar="#111417",
        bg_card="#161a1f",      bg_input="#1c2128",
        bg_hover="#1e2530",
        accent="#00b4d8",       accent_dim="#0096b4",
        accent_glow="#00b4d820",
        text_primary="#e8edf2", text_secondary="#8a9ab0",
        text_muted="#4a5568",
        success="#22c55e",      warning="#f59e0b",
        error="#ef4444",        info="#00b4d8",
        border="#232b35",       border_light="#2d3748",
        btn_on_bg="#0d2e1a",    btn_on_hover="#0f3820",
        btn_on_text="#22c55e",  btn_on_border="#22c55e",
    ),
    "indigo": Palette(
        bg_root="#13151a",      bg_sidebar="#0e1014",
        bg_card="#1a1c24",      bg_input="#1e2030",
        bg_hover="#1a1c2e",
        accent="#6366f1",       accent_dim="#4f52d4",
        accent_glow="#6366f120",
        text_primary="#e0e0e8", text_secondary="#6468a0",
        text_muted="#444860",
        success="#4ade80",      warning="#fbbf24",
        error="#f87171",        info="#818cf8",
        border="#1e2028",       border_light="#2a2c3e",
        btn_on_bg="#0d2e1a",    btn_on_hover="#0f3820",
        btn_on_text="#4ade80",  btn_on_border="#4ade80",
    ),
    "paper": Palette(
        bg_root="#faf8f5",      bg_sidebar="#f2ede6",
        bg_card="#ffffff",      bg_input="#f7f4ef",
        bg_hover="#ede8e0",
        accent="#c4894a",       accent_dim="#a8733a",
        accent_glow="#c4894a20",
        text_primary="#2d2520", text_secondary="#8a7060",
        text_muted="#b0a090",
        success="#4a7c59",      warning="#c4894a",
        error="#a0403a",        info="#4a6fa0",
        border="#e8e4de",       border_light="#ddd8d0",
        btn_on_bg="#e8f5ec",    btn_on_hover="#d8eede",
        btn_on_text="#4a7c59",  btn_on_border="#4a7c59",
    ),
    "terminal": Palette(
        bg_root="#0a0a0a",      bg_sidebar="#0a0a0a",
        bg_card="#111111",      bg_input="#161616",
        bg_hover="#1a1a1a",
        accent="#ff3c00",       accent_dim="#cc3000",
        accent_glow="#ff3c0020",
        text_primary="#ffffff", text_secondary="#666666",
        text_muted="#333333",
        success="#00ff41",      warning="#ffaa00",
        error="#ff3c00",        info="#ff3c00",
        border="#1a1a1a",       border_light="#222222",
        btn_on_bg="#001a0a",    btn_on_hover="#002010",
        btn_on_text="#00ff41",  btn_on_border="#00ff41",
    ),
}

THEME_NAMES: Dict[str, str] = {
    "default":  "Default",
    "indigo":   "Indigo",
    "paper":    "Paper",
    "terminal": "Terminal",
}


class Theme:
    def __init__(self) -> None:
        self.palette    = THEMES["default"]
        self.typography = Typography()
        self.metrics    = Metrics()
        self.log_colors = LOG_COLORS
        self._current   = "default"

    def set_theme(self, name: str) -> None:
        if name in THEMES:
            self.palette  = THEMES[name]
            self._current = name
            if name == "terminal":
                self.typography = Typography(family_ui="Cascadia Code")
            else:
                self.typography = Typography()

    @property
    def current(self) -> str:
        return self._current

    def get_log_color(self, tag: str) -> str:
        return self.log_colors.get(tag.upper(), self.log_colors["DEFAULT"])

    def apply_ctk_theme(self) -> None:
        import customtkinter as ctk
        mode = "light" if self._current == "paper" else "dark"
        ctk.set_appearance_mode(mode)
        ctk.set_default_color_theme("dark-blue")


theme = Theme()
