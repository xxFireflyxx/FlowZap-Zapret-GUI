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
from core.updater import GUI_VERSION
from core.manager import ZapretManager, ServiceState
from core.tray import TrayManager


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
                fg_color=p.accent,
                hover_color=p.accent,
                text_color=p.bg_root,
                font=(t.family_ui, t.size_md, "bold"),
            )
            dot_bg = p.accent
        else:
            self.configure(
                fg_color="transparent",
                hover_color=p.bg_hover,
                text_color=p.text_secondary,
                font=(t.family_ui, t.size_md),
            )
            dot_bg = p.bg_sidebar
        # Обновляем фон точки если она есть
        if hasattr(self, "_dot_label"):
            try:
                self._dot_label.configure(bg=dot_bg)
            except Exception:
                pass

    def show_dot(self) -> None:
        """Показать пульсирующую оранжевую точку рядом с кнопкой."""
        if hasattr(self, "_dot_label"):
            return
        import tkinter as tk
        p = theme.palette
        dot = tk.Label(
            self,
            text="●",
            font=(theme.typography.family_ui, 12),
            fg="#f97316",
            bg=p.accent if self._active else p.bg_sidebar,
            bd=0,
            highlightthickness=0,
        )
        dot.place(relx=1.0, rely=0.5, x=-16, anchor="center")
        self._dot_label = dot
        self._dot_phase = 0

        # Следим за hover на кнопке и точке
        def _on_enter(e):
            if hasattr(self, "_dot_label"):
                bg = p.accent if self._active else p.bg_hover
                self._dot_label.configure(bg=bg)

        def _on_leave(e):
            if hasattr(self, "_dot_label"):
                bg = p.accent if self._active else p.bg_sidebar
                self._dot_label.configure(bg=bg)

        self.bind("<Enter>", _on_enter, add="+")
        self.bind("<Leave>", _on_leave, add="+")
        dot.bind("<Enter>", _on_enter)
        dot.bind("<Leave>", _on_leave)

        self._animate_dot()

    def hide_dot(self) -> None:
        """Скрыть точку обновления."""
        if hasattr(self, "_dot_label"):
            try:
                self._dot_label.destroy()
            except Exception:
                pass
            del self._dot_label

    def _animate_dot(self) -> None:
        if not hasattr(self, "_dot_label"):
            return
        try:
            if not self._dot_label.winfo_exists():
                return
        except Exception:
            return
        import math
        self._dot_phase = (self._dot_phase + 0.07) % (2 * math.pi)
        alpha = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(self._dot_phase))
        r = int(0xf9 * alpha + 0x1a * (1 - alpha))
        g = int(0x73 * alpha + 0x1a * (1 - alpha))
        b = int(0x16 * alpha + 0x1a * (1 - alpha))
        color = f"#{r:02x}{g:02x}{b:02x}"
        try:
            self._dot_label.configure(fg=color)
            self.after(50, self._animate_dot)
        except Exception:
            pass


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

        # Загрузить тему из конфига
        # Загружаем GitHub токен из конфига
        _gh_token = self.config.get("github", {}).get("token", "")
        if _gh_token:
            from core.updater import set_github_token
            set_github_token(_gh_token)

        saved_theme = self.config.get("ui", {}).get("theme_name", "default")
        theme.set_theme(saved_theme)

        p = theme.palette
        m = theme.metrics

        self.title("FlowZap")
        self.geometry("960x640")
        self.minsize(800, 520)
        self.configure(fg_color=p.bg_root)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Иконка окна и таскбара
        import sys
        _icon_ico = Path(__file__).parent.parent / "assets" / "icon.ico"
        if _icon_ico.exists():
            try:
                self.wm_iconbitmap(str(_icon_ico))
            except Exception:
                pass
            try:
                from PIL import Image, ImageTk
                _img = Image.open(str(_icon_ico))
                _photo = ImageTk.PhotoImage(_img)
                self.wm_iconphoto(True, _photo)
                self._icon_photo = _photo  # держим ссылку чтобы GC не удалил
            except Exception:
                pass

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

        # ── Трей ─────────────────────────────
        tray_enabled = self.config.get("ui", {}).get("tray_enabled", True)
        self._tray_enabled = tray_enabled
        self._tray = TrayManager(
            on_show=self._show_window,
            on_toggle=self._tray_toggle,
            on_quit=self._quit_app,
            on_dns=self._tray_dns_toggle,
            on_tg_proxy=self._tray_tg_toggle,
            on_tg_open=self._tray_tg_open,
            is_running_fn=lambda: self.manager.is_running,
            is_dns_on_fn=lambda: getattr(self._tabs.get("dashboard"), "_dns_enabled", False),
            is_tg_running_fn=lambda: getattr(
                getattr(self._tabs.get("dashboard"), "_tg_proxy", None), "is_running", False),
            tg_available_fn=lambda: getattr(
                getattr(self._tabs.get("dashboard"), "_tg_proxy", None), "is_available", False),
        )
        self._tray.start()

        # Автопроверка обновлений: при старте и затем каждый час
        self._update_dots: dict = {}   # tab_id -> bool (есть ли обновление)
        self.after(3000, self._check_updates_bg)          # 3 сек после старта
        self._schedule_periodic_update_check()

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
            # Убираем служебные ключи (начинающиеся с "_") — они невалидны в TOML
            data = {k: v for k, v in self.config.items() if not k.startswith("_")}
            with open(self.config_path, "wb") as f:
                tomli_w.dump(data, f)
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
            text="",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_muted,
        ).pack(side="left", pady=(6, 0))

        ctk.CTkFrame(sidebar, height=1, fg_color=p.border).pack(fill="x", padx=m.padding_md)

        nav_items = [
            ("dashboard",  "Главная",    NAV_ICONS["dashboard"]),
            ("parameters", "Параметры",  NAV_ICONS["parameters"]),
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
            text=f"v{GUI_VERSION}",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_muted,
        ).pack(pady=(0, 2))

        _author_lbl = ctk.CTkLabel(
            sidebar,
            text="by xxFireflyxx",
            font=(t.family_ui, t.size_xs),
            text_color=p.text_muted,
            cursor="hand2",
        )
        _author_lbl.pack(pady=(0, m.padding_md))
        _author_lbl.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://github.com/xxFireflyxx"))

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
                "dashboard":  (DashboardTab,  {"manager": self.manager, "config": self.config, "save_config_fn": self.save_config}),
                "parameters": (ParametersTab, {"manager": self.manager, "config": self.config,
                                                   **( {"on_dns_changed": self._on_dns_changed}
                                                       if "on_dns_changed" in ParametersTab.__init__.__code__.co_varnames
                                                       else {} )}),
                "logs":       (LogsTab,       {"manager": self.manager}),
                "updates":    (UpdatesTab,    {"config": self.config, "manager": self.manager, "on_core_updated": self._on_core_updated}),
                "settings":   (SettingsTab,   {"manager": self.manager, "config": self.config,
                                               **( {"on_dns_changed": self._on_dns_changed}
                                                   if "on_dns_changed" in SettingsTab.__init__.__code__.co_varnames
                                                   else {} )}),
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

    def _on_dns_changed(self) -> None:
        """Вызывается когда пользователь сменил активный DNS в parameters.
        Если DNS сейчас включён — переподключаемся к новому адресу.
        """
        dashboard = self._tabs.get("dashboard")
        if dashboard and getattr(dashboard, "_dns_enabled", False):
            self.after(0, dashboard._restart_dns)

    def _on_core_updated(self) -> None:
        """Вызывается после успешного обновления Core.
        1. Перезагружает список пресетов (новые bat файлы)
        2. Запускает тесты пинга
        """
        dashboard = self._tabs.get("dashboard")
        if not dashboard:
            return

        # Перезагружаем пресеты — после обновления могут появиться новые bat
        if hasattr(dashboard, "_load_presets"):
            self.after(0, dashboard._load_presets)

        # Запускаем тесты пинга с задержкой — WinDivert должен успеть выгрузиться
        if hasattr(dashboard, "_ping_mgr"):
            self.after(8000, dashboard._ping_mgr.run_tests)

    def _on_state_change(self, state: ServiceState) -> None:
        self.after(0, self._status_badge.update_state, state)

        dashboard = self._tabs.get("dashboard")
        if dashboard and hasattr(dashboard, "on_state_change"):
            self.after(0, dashboard.on_state_change, state)

        # Обновить иконку трея
        running = state == ServiceState.RUNNING
        self.after(0, lambda: self._tray.update_icon(running))

    # ─────────────────────────────────────────
    #  Автопроверка обновлений
    # ─────────────────────────────────────────

    UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000  # 1 час

    def _schedule_periodic_update_check(self) -> None:
        self.after(self.UPDATE_CHECK_INTERVAL_MS, self._periodic_update_check)

    def _periodic_update_check(self) -> None:
        self._check_updates_bg()
        self._schedule_periodic_update_check()

    def _check_updates_bg(self) -> None:
        """Запускает проверку обновлений в фоновом потоке."""
        import threading
        threading.Thread(target=self._do_check_updates, daemon=True,
                         name="update-checker").start()

    def _do_check_updates(self) -> None:
        """Фоновая проверка: сравниваем текущие версии с GitHub."""
        from core.updater import (
            get_latest_release, GUI_VERSION,
            FLOWZAP_REPO, get_installed_core_version,
        )
        from pathlib import Path

        def _ver(v: str) -> tuple:
            """
            Сравнивает версии вида 1.9.8c, 1.9.9a корректно.
            Буква после цифры — суффикс релиза: a < b < c...
            1.9.8c < 1.9.9a потому что 8 < 9 (числа сравниваются первыми).
            """
            import re as _re
            try:
                parts = v.lstrip("v").split(".")
                result = []
                for p in parts:
                    m = _re.match(r"(\d+)([a-zA-Z]*)", p)
                    if m:
                        num = int(m.group(1))
                        suffix = m.group(2).lower()
                        # Числа идут первыми, буква — вторичный ключ
                        result.append((num, suffix))
                    else:
                        result.append((0, ""))
                return tuple(result)
            except Exception:
                return ((0, ""),)

        has_app_update = False
        has_core_update = False

        import time as _time

        # ── FlowZap GUI ──────────────────────────────────────────────────────
        try:
            rel = get_latest_release(FLOWZAP_REPO)
            if rel:
                latest_tag = rel.get("tag_name", "").lstrip("v")
                current = GUI_VERSION.lstrip("v")
                if latest_tag and _ver(latest_tag) > _ver(current):
                    has_app_update = True
        except Exception:
            pass

        _time.sleep(1)  # пауза между запросами чтобы не превысить rate limit

        # ── Zapret core ──────────────────────────────────────────────────────
        try:
            # Репо запрета — всегда Flowseal, не путать с репо FlowZap GUI
            rel_core = get_latest_release("Flowseal/zapret-discord-youtube")
            if rel_core:
                latest_core = rel_core.get("tag_name", "").lstrip("v")
                app_dir = Path(self.config.get("_app_dir", "."))
                zapret_dir = app_dir / self.config.get("zapret", {}).get(
                    "presets_dir", "zapret"
                )
                installed = (get_installed_core_version(zapret_dir) or "").lstrip("v")
                _log2 = __import__("logging").getLogger(__name__)
                _log2.info(f"Core версия: installed='{installed}' latest='{latest_core}' ver_cmp={_ver(latest_core)} > {_ver(installed)} = {_ver(latest_core) > _ver(installed)}")
                # Если Core не установлен или версия устарела — показываем точку
                if latest_core and (not installed or _ver(latest_core) > _ver(installed)):
                    has_core_update = True
        except Exception:
            pass

        _time.sleep(1)  # пауза между запросами

        # ── TG Proxy ─────────────────────────────────────────────────────────
        has_tgproxy_update = False
        try:
            from core.updater import get_latest_release as _get_rel
            rel_tg = _get_rel("Flowseal/tg-ws-proxy")
            if rel_tg:
                latest_tg = rel_tg.get("tag_name", "").lstrip("v")
                app_dir = Path(self.config.get("_app_dir", "."))
                ver_file = app_dir / "tgproxy" / "version.txt"
                if ver_file.exists():
                    installed_tg = ver_file.read_text(encoding="utf-8").strip().lstrip("v")
                    if latest_tg and _ver(latest_tg) > _ver(installed_tg):
                        has_tgproxy_update = True
                else:
                    # Не установлен — тоже показываем точку
                    has_tgproxy_update = True
        except Exception:
            pass

        # ── Обновить точки в UI (только в main thread) ───────────────────────
        import logging as _log
        _log.getLogger(__name__).info(
            f"Проверка обновлений: has_app={has_app_update}, has_core={has_core_update}, has_tgproxy={has_tgproxy_update}"
        )
        self.after(0, lambda: self._apply_update_dots(has_app_update, has_core_update or has_tgproxy_update))

    def _apply_update_dots(self, has_app: bool, has_core: bool) -> None:
        """Показать/скрыть точки на кнопках навигации."""
        # Точка «Обновления» — если есть хоть одно обновление
        updates_btn = self._nav_buttons.get("updates")
        if updates_btn:
            if has_app or has_core:
                updates_btn.show_dot()
            else:
                updates_btn.hide_dot()

        # Уведомить вкладку Updates чтобы она тоже подсветила что именно
        updates_tab = self._tabs.get("updates")
        if updates_tab and hasattr(updates_tab, "set_update_flags"):
            updates_tab.set_update_flags(has_app=has_app, has_core=has_core)

    def _on_close(self) -> None:
        """При закрытии окна — свернуть в трей или выйти."""
        if getattr(self, "_tray_enabled", True):
            self.withdraw()
        else:
            self._quit_app()

    def _show_window(self) -> None:
        """Показать окно из трея."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_toggle(self) -> None:
        """Запустить или остановить zapret из трея."""
        if self.manager.is_running:
            self.manager.stop()
        else:
            dashboard = self._tabs.get("dashboard")
            bat = None
            if dashboard and hasattr(dashboard, "_get_current_bat"):
                bat = dashboard._get_current_bat()
            self.manager.start(bat_path=bat)
        self.after(500, lambda: self._tray.update_icon(self.manager.is_running))

    def set_tray_enabled(self, enabled: bool) -> None:
        """Включить или выключить трей."""
        self._tray_enabled = enabled
        if enabled:
            self._tray.start()
        else:
            self._tray.stop()

    def _tray_dns_toggle(self) -> None:
        """Включить/выключить DNS из трея."""
        dashboard = self._tabs.get("dashboard")
        if dashboard and hasattr(dashboard, "_on_dns_toggle"):
            self.after(0, dashboard._on_dns_toggle)
        self.after(600, lambda: self._tray.update_icon(self.manager.is_running))

    def _tray_tg_toggle(self) -> None:
        """Включить/выключить TG Proxy из трея."""
        dashboard = self._tabs.get("dashboard")
        if dashboard and hasattr(dashboard, "_on_tg_proxy_toggle"):
            self.after(0, dashboard._on_tg_proxy_toggle)
        self.after(800, lambda: self._tray._update_menu())

    def _tray_tg_open(self) -> None:
        """Открыть TG Proxy в Telegram из трея."""
        dashboard = self._tabs.get("dashboard")
        if dashboard and hasattr(dashboard, "_tg_proxy"):
            self.after(0, dashboard._tg_proxy.open_in_telegram)

    def _quit_app(self) -> None:
        """Полный выход из приложения."""
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
        self._tray.stop()
        self.after(0, self.destroy)
