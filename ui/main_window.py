"""
ui/main_window.py
-----------------
Главное окно FlowZap.
Содержит: sidebar с навигацией, область контента, статус-бар.
Вкладки подключаются как фреймы — переключение через sidebar.
"""

import customtkinter as ctk
import tkinter as tk
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

    def __init__(self, manager: ZapretManager, config: dict = None, config_path=None) -> None:
        super().__init__()
        self.config = config or {}
        self.config_path = config_path
        self.manager = manager

        p = theme.palette
        m = theme.metrics

        self.title("FlowZap")
        self.geometry("960x640")
        self.minsize(800, 520)
        self.configure(fg_color=p.bg_root)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.grid_columnconfigure(0, weight=0, minsize=m.sidebar_width)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)  # полоска
        self.grid_rowconfigure(1, weight=1)  # контент

        self._tabs:        Dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: Dict[str, NavButton]    = {}
        self._active_tab:  str = ""

        # ── Градиентная полоска ───────────────
        self._bar_canvas = tk.Canvas(
            self, height=3, bd=0, highlightthickness=0, bg=p.bg_root
        )
        self._bar_canvas.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._bar_offset = 0.0
        self._bar_style  = self.config.get("ui", {}).get("bar_style", "default")
        self.after(16, self._animate_bar)

        # ── Sidebar ───────────────────────────
        self._sidebar = self._build_sidebar()
        self._sidebar.grid(row=1, column=0, sticky="nsew")

        # ── Content area ──────────────────────
        self._content = ctk.CTkFrame(self, fg_color=p.bg_root, corner_radius=0)
        self._content.grid(row=1, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        self.manager.zapret.on_state_change = self._on_state_change

        self._load_tabs()
        self.show_tab("dashboard")

    # ─────────────────────────────────────────
    #  Градиентная полоска
    # ─────────────────────────────────────────

    def set_bar_style(self, style: str) -> None:
        self._bar_style = style
        if "ui" not in self.config:
            self.config["ui"] = {}
        self.config["ui"]["bar_style"] = style
        self.save_config()

    def _animate_bar(self) -> None:
        try:
            c = self._bar_canvas
            w = c.winfo_width()
            if w < 2:
                self.after(16, self._animate_bar)
                return

            style = self._bar_style
            off   = self._bar_offset

            def hsv_to_hex(h):
                h = h % 1.0
                i = int(h * 6)
                f = h * 6 - i
                q = 1 - f
                combos = [
                    (1, f, 0), (q, 1, 0), (0, 1, f),
                    (0, q, 1), (f, 0, 1), (1, 0, q),
                ]
                r, g, b = combos[i % 6]
                return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

            def lerp_hex(stops, t):
                t = t % 1.0
                n = len(stops) - 1
                seg = t * n
                i = int(seg)
                frac = seg - i
                a = stops[i % len(stops)]
                b = stops[(i + 1) % len(stops)]
                ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
                br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
                r = int(ar + (br - ar) * frac)
                g = int(ag + (bg - ag) * frac)
                b2 = int(ab + (bb - ab) * frac)
                return f"#{r:02x}{g:02x}{b2:02x}"

            STYLES = {
                "default": ["#00b4d8", "#00d4a8", "#00b4d8"],
                "candy":   ["#ff50b4", "#a050ff", "#50c8ff", "#ff50b4"],
                "rainbow": None,
                "none":    None,
            }

            c.delete("bar")

            if style == "none":
                self.after(16, self._animate_bar)
                return

            stops = STYLES.get(style)
            step  = max(1, w // 120)

            for x in range(0, w, step):
                t = (x / w + off) % 1.0
                if style == "rainbow":
                    color = hsv_to_hex(t)
                else:
                    color = lerp_hex(stops, t)
                x2 = min(x + step + 1, w)
                c.create_rectangle(x, 0, x2, 3, fill=color, outline="", tags="bar")

            self._bar_offset = (off + 0.0015) % 1.0

        except Exception:
            pass

        self.after(16, self._animate_bar)

    # ─────────────────────────────────────────
    #  Сохранение конфига
    # ─────────────────────────────────────────

    def save_config(self) -> None:
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

        ctk.CTkFrame(sidebar, height=1, fg_color=p.border).pack(fill="x", padx=m.padding_md)

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

        ctk.CTkFrame(sidebar, fg_color="transparent").pack(fill="both", expand=True)

        ctk.CTkFrame(sidebar, height=1, fg_color=p.border).pack(fill="x", padx=m.padding_md)

        self._status_badge = StatusBadge(sidebar)
        self._status_badge.pack(padx=m.padding_md, pady=(m.padding_md, 4))

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
                "updates":    (UpdatesTab,    {"config": self.config}),
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
        if tab_id not in self._tabs:
            return

        if self._active_tab and self._active_tab in self._tabs:
            self._tabs[self._active_tab].grid_remove()
            self._nav_buttons[self._active_tab].set_active(False)

        self._tabs[tab_id].grid()
        self._nav_buttons[tab_id].set_active(True)
        self._active_tab = tab_id

        tab = self._tabs[tab_id]
        if hasattr(tab, "on_activate"):
            tab.on_activate()

    # ─────────────────────────────────────────
    #  Коллбэки
    # ─────────────────────────────────────────

    def _on_state_change(self, state: ServiceState) -> None:
        self.after(0, self._status_badge.update_state, state)

        dashboard = self._tabs.get("dashboard")
        if dashboard and hasattr(dashboard, "on_state_change"):
            self.after(0, dashboard.on_state_change, state)

    def _on_close(self) -> None:
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
