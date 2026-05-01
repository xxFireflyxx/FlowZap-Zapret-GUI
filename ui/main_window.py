"""
ui/main_window.py
-----------------
Главное окно FlowZap.
Содержит: sidebar с навигацией, область контента, статус-бар.
Вкладки подключаются как фреймы — переключение через sidebar.
"""

import customtkinter as ctk
from typing import Dict, Callable, Optional
from pathlib import Path

from ui.theme import theme
from core.manager import ZapretManager, ServiceState


# ─────────────────────────────────────────────
#  Иконки (unicode — не требуют файлов)
# ─────────────────────────────────────────────

NAV_ICONS: Dict[str, str] = {
    "dashboard":  "⬡",
    "parameters": "⚙",
    "logs":       "≡",
    "updates":    "↑",
    "settings":   "◎",
}


# ─────────────────────────────────────────────
#  Кнопка навигации (sidebar item)
# ─────────────────────────────────────────────

class NavButton(ctk.CTkButton):
    """
    Кнопка боковой панели. Показывает иконку + текст,
    подсвечивается акцентом при активности.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        label: str,
        icon: str,
        command: Callable,
        **kwargs,
    ) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        super().__init__(
            parent,
            text=f"  {icon}  {label}",
            command=command,
            anchor="w",
            height=m.nav_item_height,
            corner_radius=m.corner_radius_sm,
            fg_color="transparent",
            hover_color=p.bg_hover,
            text_color=p.text_secondary,
            font=(t.family_ui, t.size_md),
            **kwargs,
        )
        self._active = False

    def set_active(self, active: bool) -> None:
        """Включить/выключить активное состояние."""
        p = theme.palette
        t = theme.typography
        self._active = active
        if active:
            self.configure(
                fg_color=p.bg_hover,
                text_color=p.accent,
                font=(t.family_ui, t.size_md, "bold"),
            )
        else:
            self.configure(
                fg_color="transparent",
                text_color=p.text_secondary,
                font=(t.family_ui, t.size_md),
            )


# ─────────────────────────────────────────────
#  Статус-индикатор (в sidebar)
# ─────────────────────────────────────────────

class StatusBadge(ctk.CTkFrame):
    """Маленький бейдж с цветной точкой и текстом состояния."""

    _STATE_LABELS: Dict[ServiceState, tuple[str, str]] = {
        ServiceState.STOPPED:  ("●  Остановлен", "#ef4444"),
        ServiceState.STARTING: ("●  Запускается", "#f59e0b"),
        ServiceState.RUNNING:  ("●  Активен",    "#22c55e"),
        ServiceState.STOPPING: ("●  Остановка",  "#f59e0b"),
        ServiceState.ERROR:    ("●  Ошибка",      "#ef4444"),
    }

    def __init__(self, parent: ctk.CTkFrame) -> None:
        p = theme.palette
        super().__init__(parent, fg_color="transparent")

        self._label = ctk.CTkLabel(
            self,
            text="●  Остановлен",
            text_color=p.error,
            font=(theme.typography.family_ui, theme.typography.size_sm),
        )
        self._label.pack()

    def update_state(self, state: ServiceState) -> None:
        text, color = self._STATE_LABELS.get(
            state, ("● Неизвестно", theme.palette.text_muted)
        )
        self._label.configure(text=text, text_color=color)


# ─────────────────────────────────────────────
#  Главное окно
# ─────────────────────────────────────────────

class MainWindow(ctk.CTk):
    """
    Корневое окно приложения FlowZap.

    Структура
    ---------
    ┌──────────────────────────────────────────┐
    │  Sidebar (200px)  │  Content Area        │
    │  ─ Logo           │  (активная вкладка)  │
    │  ─ Nav buttons    │                      │
    │  ─ Status badge   │                      │
    │  ─ Version        │                      │
    └──────────────────────────────────────────┘
    """

    def __init__(self, manager: ZapretManager, config: dict = None, config_path=None) -> None:
        super().__init__()
        self.config = config or {}
        self.config_path = config_path
        self.manager = manager

        # ── Настройки окна ────────────────────
        p = theme.palette
        m = theme.metrics

        self.title("FlowZap")
        self.geometry("960x640")
        self.minsize(800, 520)
        self.configure(fg_color=p.bg_root)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Сетка: sidebar | content ──────────
        self.grid_columnconfigure(0, weight=0, minsize=m.sidebar_width)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Инициализация словарей ДО sidebar ─
        # (sidebar обращается к _nav_buttons при построении)
        self._tabs:        Dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: Dict[str, NavButton]    = {}
        self._active_tab:  str = ""

        # ── Sidebar ───────────────────────────
        self._sidebar = self._build_sidebar()
        self._sidebar.grid(row=0, column=0, sticky="nsew")

        # ── Content area ──────────────────────
        self._content = ctk.CTkFrame(self, fg_color=p.bg_root, corner_radius=0)
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # ── Подключить коллбэк менеджера ──────
        self.manager.zapret.on_state_change = self._on_state_change

        # ── Показать первую вкладку ───────────
        self._load_tabs()
        self.show_tab("dashboard")

    # ─────────────────────────────────────────
    #  Сохранение конфига
    # ─────────────────────────────────────────

    def save_config(self) -> None:
        """Сохранить config в config.toml."""
        if not self.config_path:
            return
        try:
            import tomli_w
            with open(self.config_path, "wb") as f:
                tomli_w.dump(self.config, f)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Ошибка сохранения конфига: {exc}")

    # ─────────────────────────────────────────
    #  DNS логика
    # ─────────────────────────────────────────

    def setup_dns_logic(self, dns_address, action="enable"):
        import subprocess
        interface_name = "Ethernet"

        try:
            if action == "enable":
                cmd = f'netsh interface ip set dns name="{interface_name}" source=static addr={dns_address}'
                subprocess.run(cmd, shell=True, check=True)
                self.append_log(f"DNS {dns_address} успешно применен")
            else:
                cmd = f'netsh interface ip set dns name="{interface_name}" source=dhcp'
                subprocess.run(cmd, shell=True, check=True)
                self.append_log("DNS сброшен к системным настройкам")
        except Exception as e:
            self.append_log(f"Ошибка DNS: {e}")

    # ─────────────────────────────────────────
    #  Построение sidebar
    # ─────────────────────────────────────────

    def _build_sidebar(self) -> ctk.CTkFrame:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        sidebar = ctk.CTkFrame(
            self,
            width=m.sidebar_width,
            fg_color=p.bg_sidebar,
            corner_radius=0,
        )
        sidebar.pack_propagate(False)

        # Логотип
        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", padx=m.padding_md, pady=(m.padding_lg, m.padding_md))

        ctk.CTkLabel(
            logo_frame,
            text="FlowZap",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.accent,
        ).pack(side="left")

        ctk.CTkLabel(
            logo_frame,
            text=" β",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_muted,
        ).pack(side="left", pady=(6, 0))

        # Разделитель
        ctk.CTkFrame(sidebar, height=1, fg_color=p.border).pack(fill="x", padx=m.padding_md)

        # Навигационные кнопки
        nav_items = [
            ("dashboard",  "Главная",    NAV_ICONS["dashboard"]),
            ("parameters", "Параметры",  NAV_ICONS["parameters"]),
            ("logs",       "Логи",       NAV_ICONS["logs"]),
            ("updates",    "Обновления", NAV_ICONS["updates"]),
            ("settings",   "Настройки",  NAV_ICONS["settings"]),
        ]

        nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", padx=m.padding_md // 2, pady=m.padding_md)

        for tab_id, label, icon in nav_items:
            btn = NavButton(
                nav_frame,
                label=label,
                icon=icon,
                command=lambda tid=tab_id: self.show_tab(tid),
            )
            btn.pack(fill="x", pady=2)
            self._nav_buttons[tab_id] = btn

        # Распорка — статус и версия уйдут вниз
        ctk.CTkFrame(sidebar, fg_color="transparent").pack(fill="both", expand=True)

        # Разделитель
        ctk.CTkFrame(sidebar, height=1, fg_color=p.border).pack(fill="x", padx=m.padding_md)

        # Статус-бейдж
        self._status_badge = StatusBadge(sidebar)
        self._status_badge.pack(padx=m.padding_md, pady=(m.padding_md, 4))

        # Версия
        ctk.CTkLabel(
            sidebar,
            text="v0.1.0",
            font=(t.family_ui, t.size_xs),
            text_color=p.text_muted,
        ).pack(pady=(0, m.padding_md))

        return sidebar

    # ─────────────────────────────────────────
    #  Управление вкладками
    # ─────────────────────────────────────────

    def _load_tabs(self) -> None:
        try:
            from ui.dashboard    import DashboardTab
            from ui.parameters   import ParametersTab
            from ui.logs_tab     import LogsTab
            from ui.updates_tab  import UpdatesTab
            from ui.settings_tab import SettingsTab

            tab_classes = {
                "dashboard":  (DashboardTab,  {"manager": self.manager, "config": self.config}),
                "parameters": (ParametersTab, {"manager": self.manager, "config": self.config}),
                "logs":       (LogsTab,       {"manager": self.manager}),
                "updates":    (UpdatesTab,    {}),
                "settings":   (SettingsTab,   {"manager": self.manager, "config": self.config}),
            }

            for tab_id, (cls, kwargs) in tab_classes.items():
                tab = cls(self._content, **kwargs)
                tab.grid(row=0, column=0, sticky="nsew")
                tab.grid_remove()
                self._tabs[tab_id] = tab

            logs_tab = self._tabs.get("logs")
            if logs_tab:
                original_on_log = self.manager.zapret.on_log
                def combined_log(msg: str) -> None:
                    original_on_log(msg)
                    logs_tab.append_log(msg)
                self.manager.zapret.on_log = combined_log

        except Exception:
            import traceback, logging
            logging.getLogger(__name__).error(f"ОШИБКА В _load_tabs:\n{traceback.format_exc()}")

    def show_tab(self, tab_id: str) -> None:
        """Показать вкладку и обновить состояние кнопок."""
        if tab_id not in self._tabs:
            return

        # Скрыть текущую
        if self._active_tab and self._active_tab in self._tabs:
            self._tabs[self._active_tab].grid_remove()
            self._nav_buttons[self._active_tab].set_active(False)

        # Показать новую
        self._tabs[tab_id].grid()
        self._nav_buttons[tab_id].set_active(True)
        self._active_tab = tab_id

        # Уведомить вкладку об активации (если поддерживает)
        tab = self._tabs[tab_id]
        if hasattr(tab, "on_activate"):
            tab.on_activate()

    # ─────────────────────────────────────────
    #  Коллбэки
    # ─────────────────────────────────────────

    def _on_state_change(self, state: ServiceState) -> None:
        """Обновить статус-бейдж при смене состояния zapret."""
        self.after(0, self._status_badge.update_state, state)

        dashboard = self._tabs.get("dashboard")
        if dashboard and hasattr(dashboard, "on_state_change"):
            self.after(0, dashboard.on_state_change, state)

    def _on_close(self) -> None:
        """Корректное завершение: остановить сервисы перед выходом."""
        # Сбросить DNS на DHCP если был включён
        dashboard = self._tabs.get("dashboard")
        if dashboard and getattr(dashboard, "_dns_enabled", False):
            try:
                import subprocess
                interface = dashboard._get_active_interface()
                subprocess.run(
                    f'netsh interface ip set dns name="{interface}" source=dhcp',
                    shell=True, capture_output=True, timeout=5,
                )
            except Exception:
                pass

        self.manager.shutdown_all()
        self.destroy()
