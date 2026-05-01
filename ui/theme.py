"""
ui/theme.py
-----------
Централизованная тема FlowZap.
Все цвета, шрифты и стили хранятся здесь — менять только здесь.
"""

from dataclasses import dataclass, field
from typing import Dict


# ─────────────────────────────────────────────
#  Цветовая палитра
# ─────────────────────────────────────────────

@dataclass(frozen=True)
class Palette:
    # Фоны
    bg_root:    str = "#0d0f11"      # самый тёмный — корень окна
    bg_sidebar: str = "#111417"      # боковая панель
    bg_card:    str = "#161a1f"      # карточки / панели
    bg_input:   str = "#1c2128"      # поля ввода
    bg_hover:   str = "#1e2530"      # hover на кнопках навигации

    # Акцент — teal/голубой
    accent:         str = "#00b4d8"
    accent_dim:     str = "#0096b4"   # чуть темнее (pressed)
    accent_glow:    str = "#00b4d820" # полупрозрачный для рамок

    # Текст
    text_primary:   str = "#e8edf2"
    text_secondary: str = "#8a9ab0"
    text_muted:     str = "#4a5568"

    # Состояния
    success: str = "#22c55e"
    warning: str = "#f59e0b"
    error:   str = "#ef4444"
    info:    str = "#00b4d8"

    # Разделители
    border:       str = "#232b35"
    border_light: str = "#2d3748"


@dataclass(frozen=True)
class Typography:
    # Шрифты (CustomTkinter использует системные + Google Fonts через ctk)
    family_ui:   str = "Segoe UI"          # Windows; fallback — системный
    family_mono: str = "Cascadia Code"     # для логов; fallback — Consolas

    size_xs:  int = 10
    size_sm:  int = 11
    size_md:  int = 13
    size_lg:  int = 15
    size_xl:  int = 18
    size_xxl: int = 24
    size_hero: int = 32

    weight_normal: str = "normal"
    weight_bold:   str = "bold"


# ─────────────────────────────────────────────
#  Константы виджетов
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
#  Цвета для лог-парсера
# ─────────────────────────────────────────────

LOG_COLORS: Dict[str, str] = {
    "INFO":    "#22c55e",   # зелёный
    "ERROR":   "#ef4444",   # красный
    "WARN":    "#f59e0b",   # жёлтый
    "WARNING": "#f59e0b",
    "DEBUG":   "#8a9ab0",   # серый
    "DPI":     "#00b4d8",   # голубой (акцент)
    "CONNECT": "#a78bfa",   # фиолетовый
    "BLOCK":   "#ef4444",
    "BYPASS":  "#22c55e",
    "START":   "#22c55e",
    "STOP":    "#f59e0b",
    "DEFAULT": "#e8edf2",
}

# ─────────────────────────────────────────────
#  Singleton-доступ к теме
# ─────────────────────────────────────────────

class Theme:
    """
    Главный объект темы. Импортируй и используй:
        from ui.theme import theme
        color = theme.palette.accent
    """
    palette:    Palette    = Palette()
    typography: Typography = Typography()
    metrics:    Metrics    = Metrics()
    log_colors: Dict[str, str] = field(default_factory=lambda: LOG_COLORS)

    def __init__(self) -> None:
        self.palette    = Palette()
        self.typography = Typography()
        self.metrics    = Metrics()
        self.log_colors = LOG_COLORS

    def get_log_color(self, tag: str) -> str:
        """Вернуть цвет для лог-тега. Если не найден — дефолтный."""
        return self.log_colors.get(tag.upper(), self.log_colors["DEFAULT"])

    def apply_ctk_theme(self) -> None:
        """
        Применить глобальные настройки CustomTkinter.
        Вызывать ОДИН раз до создания виджетов.
        """
        import customtkinter as ctk
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")


# Глобальный экземпляр — импортируй его везде
theme = Theme()
