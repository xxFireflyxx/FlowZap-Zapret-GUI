"""
ui/settings_tab.py
------------------
Вкладка «Настройки».
Содержит: автозапуск, стиль статус-бара, тема интерфейса.
Всё сохраняется автоматически — кнопка «Сохранить» не нужна.
"""

import sys
import customtkinter as ctk
from pathlib import Path
from ui.theme import theme, THEME_NAMES
from core.manager import ZapretManager

REG_KEY   = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME  = "FlowZap"


class _SimpleDropdown(ctk.CTkFrame):
    """Кастомный дропдаун в стиле параметров — без кнопки-блока, только стрелка."""

    def __init__(self, parent, values: list, initial: str, command=None, **kw):
        p = theme.palette
        t = theme.typography
        super().__init__(parent, fg_color=p.bg_input, corner_radius=6, **kw)
        self._values = values
        self._command = command
        self._open = False

        self.grid_columnconfigure(0, weight=1)

        # Текущее значение
        self._label = ctk.CTkLabel(
            self, text=initial,
            font=(t.family_ui, 13),
            text_color=p.text_primary,
            anchor="w", cursor="hand2",
        )
        self._label.grid(row=0, column=0, sticky="ew", padx=(12, 4), pady=8)
        self._label.bind("<Button-1>", lambda e: self._toggle())

        # Стрелка
        self._arrow = ctk.CTkLabel(
            self, text="▼",
            font=("Segoe UI", 10),
            text_color=p.accent,
            cursor="hand2", width=20,
        )
        self._arrow.grid(row=0, column=1, padx=(0, 8))
        self._arrow.bind("<Button-1>", lambda e: self._toggle())

        # Popup
        self._popup = None

    def get(self) -> str:
        return self._label.cget("text")

    def set(self, value: str) -> None:
        self._label.configure(text=value)

    def _toggle(self) -> None:
        if self._open:
            self._close()
        else:
            self._show()

    def _show(self) -> None:
        p = theme.palette
        t = theme.typography
        self._open = True
        self._arrow.configure(text="▲")

        self._popup = ctk.CTkToplevel(self)
        self._popup.overrideredirect(True)
        self._popup.configure(fg_color=p.bg_card)
        self._popup.lift()
        self._popup.focus_set()
        self._popup.bind("<FocusOut>", lambda e: self._close())

        for val in self._values:
            btn = ctk.CTkButton(
                self._popup, text=val,
                fg_color="transparent",
                hover_color=p.bg_hover,
                text_color=p.text_primary,
                font=(t.family_ui, 13),
                anchor="w", corner_radius=4,
                command=lambda v=val: self._select(v),
            )
            btn.pack(fill="x", padx=4, pady=2)

        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        w = self.winfo_width()
        self._popup.geometry(f"{w}x{len(self._values)*36+8}+{x}+{y}")

    def _select(self, value: str) -> None:
        self._label.configure(text=value)
        self._close()
        if self._command:
            self._command(value)

    def _close(self) -> None:
        self._open = False
        self._arrow.configure(text="▼")
        if self._popup:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None


class SettingsTab(ctk.CTkFrame):
    def __init__(
        self,
        parent: ctk.CTkFrame,
        manager: ZapretManager,
        config: dict = None,
        on_core_updated=None,   # оставляем для совместимости, не используется
        on_dns_changed=None,
    ) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self.manager = manager
        self._config = config or {}
        self._on_dns_changed = on_dns_changed
        self._app_dir = Path(__file__).parent.parent
        self._build()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="Настройки",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg,
               pady=(m.padding_lg, m.padding_md))

        # ── Автозапуск ────────────────────────────
        auto_card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        auto_card.grid(row=1, column=0, sticky="ew", padx=m.padding_lg,
                       pady=(0, m.padding_md))

        ctk.CTkLabel(
            auto_card, text="Автозапуск",
            font=(t.family_ui, t.size_md, "bold"),
            text_color=p.text_primary,
        ).pack(anchor="w", padx=m.padding_md, pady=(m.padding_md, 4))

        self._autostart_var = ctk.BooleanVar(
            value=self._config.get("zapret", {}).get("autostart", False))
        ctk.CTkSwitch(
            auto_card,
            text="Запускать zapret при старте FlowZap",
            variable=self._autostart_var,
            progress_color=p.accent, button_color=p.text_primary,
            font=(t.family_ui, t.size_md), text_color=p.text_primary,
            command=self._on_autostart_change,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, 8))

        self._win_autostart_var = ctk.BooleanVar(value=self._get_win_autostart())
        ctk.CTkSwitch(
            auto_card,
            text="Запускать FlowZap вместе с Windows",
            variable=self._win_autostart_var,
            progress_color=p.accent, button_color=p.text_primary,
            font=(t.family_ui, t.size_md), text_color=p.text_primary,
            command=self._on_win_autostart_change,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, 8))

        self._win_autostart_status = ctk.CTkLabel(
            auto_card, text="",
            font=(t.family_ui, t.size_xs),
            text_color=p.text_muted, anchor="w",
            height=0,
        )
        self._win_autostart_status.pack(anchor="w", padx=m.padding_md, pady=(0, 4))

        # ── Стиль статус-бара ─────────────────────
        style_card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        style_card.grid(row=2, column=0, sticky="ew", padx=m.padding_lg,
                        pady=(0, m.padding_md))
        style_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(style_card, text="Подсветка",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, columnspan=2, sticky="w",
            padx=m.padding_md, pady=(m.padding_md, 8))

        _bar_style_labels = {
            "default": "По умолчанию",
            "rainbow": "Радуга",
            "candy":   "Конфетти",
            "none":    "Выключить",
        }
        _bar_style_current = self._config.get("ui", {}).get("bar_style", "default")
        self._bar_style_labels = _bar_style_labels
        self._bar_style_dd = _SimpleDropdown(
            style_card,
            values=list(_bar_style_labels.values()),
            initial=_bar_style_labels.get(_bar_style_current, "По умолчанию"),
            command=self._on_bar_style_change,
        )
        self._bar_style_dd.grid(row=1, column=0, columnspan=2, sticky="ew",
               padx=m.padding_md, pady=(0, m.padding_md))

        # ── Тема интерфейса ───────────────────────
        theme_card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        theme_card.grid(row=3, column=0, sticky="ew", padx=m.padding_lg,
                        pady=(0, m.padding_md))
        theme_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(theme_card, text="Тема интерфейса",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, columnspan=2, sticky="w",
            padx=m.padding_md, pady=(m.padding_md, 4))

        ctk.CTkLabel(theme_card, text="Применится после перезапуска приложения",
                     font=(t.family_ui, t.size_xs),
                     text_color=p.text_muted).grid(
            row=1, column=0, columnspan=2, sticky="w",
            padx=m.padding_md, pady=(0, 8))

        _current_key = self._config.get("ui", {}).get("theme_name", list(THEME_NAMES.keys())[0])
        _current_display = THEME_NAMES.get(_current_key, list(THEME_NAMES.values())[0])
        self._theme_dd = _SimpleDropdown(
            theme_card,
            values=list(THEME_NAMES.values()),
            initial=_current_display,
            command=self._on_theme_change,
        )
        self._theme_dd.grid(row=2, column=0, columnspan=2, sticky="ew",
               padx=m.padding_md, pady=(0, m.padding_md))

        # ── Логи ─────────────────────────────────
        logs_card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        logs_card.grid(row=4, column=0, sticky="ew", padx=m.padding_lg,
                       pady=(0, m.padding_md))
        logs_card.grid_columnconfigure(0, weight=1)

        logs_row = ctk.CTkFrame(logs_card, fg_color="transparent")
        logs_row.grid(row=0, column=0, sticky="ew", padx=m.padding_md, pady=m.padding_md)
        logs_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(logs_row, text="Логи приложения",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            logs_row, text="Открыть папку",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_primary, height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._open_logs_folder,
        ).grid(row=0, column=1, sticky="e")

    # ──────────────────────────────────────────────
    #  Обработчики
    # ──────────────────────────────────────────────

    # ──────────────────────────────────────────────
    #  Запуск с Windows (реестр)
    # ──────────────────────────────────────────────

    @staticmethod
    def _get_exe_path() -> str:
        """Путь к запускаемому файлу — exe или python скрипт."""
        if getattr(sys, "frozen", False):
            return str(Path(sys.executable))
        # Режим разработки — запускаем через pythonw чтобы не было консоли
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        script = Path(__file__).parent.parent / "main.py"
        if pythonw.exists():
            return f'"{pythonw}" "{script}"'
        return f'"{sys.executable}" "{script}"'

    def _get_win_autostart(self) -> bool:
        """Проверить есть ли задача FlowZap в планировщике задач."""
        try:
            import subprocess
            result = subprocess.run(
                ["schtasks", "/query", "/tn", "FlowZap"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _on_win_autostart_change(self) -> None:
        import subprocess
        enable = self._win_autostart_var.get()
        try:
            if enable:
                exe = self._get_exe_path()
                # Создать задачу с правами администратора через планировщик
                cmd = [
                    "schtasks", "/create", "/tn", "FlowZap",
                    "/tr", exe,
                    "/sc", "ONLOGON",
                    "/rl", "HIGHEST",   # запускать с наивысшими правами
                    "/f",               # перезаписать если уже есть
                ]
                result = subprocess.run(cmd, capture_output=True,
                                        text=True, timeout=10,
                                        encoding="cp866", errors="replace")
                if result.returncode == 0:
                    self._win_autostart_status.configure(
                        text="✓ FlowZap добавлен в автозапуск (с правами администратора)",
                        text_color=theme.palette.success,
                    )
                else:
                    raise RuntimeError(result.stdout.strip() or result.stderr.strip())
            else:
                result = subprocess.run(
                    ["schtasks", "/delete", "/tn", "FlowZap", "/f"],
                    capture_output=True, text=True, timeout=10,
                    encoding="cp866", errors="replace",
                )
                if result.returncode == 0:
                    self._win_autostart_status.configure(
                        text="Убран из автозапуска",
                        text_color=theme.palette.text_muted,
                    )
                else:
                    raise RuntimeError(result.stdout.strip() or result.stderr.strip())

            self.after(4000, lambda: self._win_autostart_status.configure(text=""))

        except Exception as e:
            self._win_autostart_var.set(not enable)
            self._win_autostart_status.configure(
                text=f"Ошибка: {e}",
                text_color=theme.palette.error,
            )

    def _open_logs_folder(self) -> None:
        import subprocess, os
        logs_dir = self._app_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            subprocess.Popen(["explorer", str(logs_dir)])
        else:
            subprocess.Popen(["xdg-open", str(logs_dir)])

    def _on_autostart_change(self) -> None:
        if "zapret" not in self._config:
            self._config["zapret"] = {}
        self._config["zapret"]["autostart"] = self._autostart_var.get()
        self._save()

    def _on_theme_change(self, value: str) -> None:
        # Находим ключ темы по отображаемому имени
        key = next((k for k, v in THEME_NAMES.items() if v == value), "default")
        if "ui" not in self._config:
            self._config["ui"] = {}
        self._config["ui"]["theme_name"] = key
        self._save()
        root = self.winfo_toplevel()
        if hasattr(root, "save_config"):
            root.save_config()

    def _on_bar_style_change(self, value: str) -> None:
        # Переводим русское название обратно в ключ
        key = next((k for k, v in self._bar_style_labels.items() if v == value), value)
        if "ui" not in self._config:
            self._config["ui"] = {}
        self._config["ui"]["bar_style"] = key
        root = self.winfo_toplevel()
        if hasattr(root, "set_bar_style"):
            root.set_bar_style(key)
        self._save()

    def _save(self) -> None:
        root = self.winfo_toplevel()
        if hasattr(root, "save_config"):
            root.save_config()

    def on_activate(self) -> None:
        pass
