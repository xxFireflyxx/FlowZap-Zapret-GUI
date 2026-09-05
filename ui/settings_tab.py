"""
ui/settings_tab.py
------------------
Вкладка «Настройки».
Содержит: автозапуск, стиль статус-бара, тема интерфейса.
Всё сохраняется автоматически — кнопка «Сохранить» не нужна.
"""

import sys
import logging
import customtkinter as ctk
from pathlib import Path
from ui.theme import theme, THEME_NAMES
from ui.help_tooltip import add_help_icon
from core.manager import ZapretManager

REG_KEY   = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME  = "FlowZap"

# Версия схемы задачи автозапуска в Планировщике заданий. Увеличивать
# при любом изменении параметров задачи (задержка, флаги питания и т.п.),
# чтобы существующие у пользователей задачи, созданные более старой
# версией FlowZap, тихо пересоздавались с новыми настройками при
# следующем запуске приложения — без участия пользователя.
AUTOSTART_TASK_VERSION = 3
AUTOSTART_DELAY_SECONDS = 15


def _get_exe_action() -> tuple[str, str]:
    """(execute, argument) для запускаемого файла — exe или python скрипт."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable)), ""
    # Режим разработки — запускаем через pythonw чтобы не было консоли
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    script = Path(__file__).parent.parent / "main.py"
    exe = str(pythonw) if pythonw.exists() else str(sys.executable)
    return exe, f'"{script}"'


def register_win_autostart_task(delay_seconds: int = AUTOSTART_DELAY_SECONDS) -> None:
    """Регистрирует задачу автозапуска FlowZap в планировщике через
    PowerShell. Не зависит от UI — используется и настройками, и
    автоматической миграцией при старте. Бросает исключение при ошибке."""
    import os, subprocess, tempfile
    exe, arg = _get_exe_action()
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    # New-ScheduledTaskAction -Argument не принимает пустую строку (валится
    # с "Аргумент пуст или NULL") — для собранного exe arg всегда "",
    # поэтому параметр добавляем только когда он реально есть.
    action_line = (
        f"$Action = New-ScheduledTaskAction -Execute '{exe}' -Argument '{arg}'"
        if arg else
        f"$Action = New-ScheduledTaskAction -Execute '{exe}'"
    )
    # schtasks /create не даёт отключить условие "только при питании от
    # сети" — по умолчанию Windows создаёт такие задачи с
    # DisallowStartIfOnBatteries=true, из-за чего на ноутбуке от батареи
    # задача молча не срабатывает при входе в систему без какой-либо
    # ошибки. Регистрируем через PowerShell, чтобы явно выставить
    # AllowStartIfOnBatteries / DontStopIfGoingOnBatteries.
    #
    # $ErrorActionPreference = 'Stop' + try/catch с exit 1 — без этого
    # PowerShell по умолчанию не считает ошибку внутри cmdlet (например,
    # Register-ScheduledTask) поводом завершить процесс ненулевым кодом:
    # скрипт просто продолжит выполнение, powershell.exe вернёт 0, и
    # Python решит, что задача создана, хотя на деле её нет.
    ps_script = f'''
$ErrorActionPreference = 'Stop'
try {{
    $Trigger1 = New-ScheduledTaskTrigger -AtLogOn -User '{user}'
    $Trigger1.Delay = 'PT{delay_seconds}S'
    $Trigger2 = New-ScheduledTaskTrigger -AtStartup
    $Trigger2.Delay = 'PT{delay_seconds}S'
    {action_line}
    $Principal = New-ScheduledTaskPrincipal -UserId '{user}' -RunLevel Highest -LogonType Interactive
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Unregister-ScheduledTask -TaskName 'FlowZap' -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName 'FlowZap' -Action $Action -Trigger @($Trigger1, $Trigger2) -Principal $Principal -Settings $Settings -Force | Out-Null
}} catch {{
    Write-Error $_.Exception.Message
    exit 1
}}
'''
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as f:
        f.write(ps_script)
        ps_path = f.name
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ps_path],
            capture_output=True, text=True, timeout=15,
            encoding="cp866", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        try:
            os.unlink(ps_path)
        except Exception:
            pass
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    # Второй уровень подстраховки: даже при returncode == 0 явно
    # перепроверяем, что задача реально появилась в планировщике.
    if not win_autostart_task_exists():
        raise RuntimeError("Задача не найдена в планировщике после создания")


def win_autostart_task_exists() -> bool:
    """Проверить есть ли задача FlowZap в планировщике задач."""
    import subprocess
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", "FlowZap"],
            capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except Exception:
        return False


def ensure_win_autostart_migrated(config: dict) -> bool:
    """Если задача автозапуска уже была включена пользователем, но
    создана более старой версией FlowZap (устаревшая схема — старая
    задержка, отсутствие флагов работы от батареи и т.п.) — тихо
    пересоздаёт её с актуальными настройками. Не требует участия
    пользователя и не трогает config.toml сама — вызывающий код должен
    сохранить config после вызова, если вернулось True.
    Возвращает True, если конфиг был изменён (нужно сохранить)."""
    import logging
    log = logging.getLogger("flowzap.autostart")
    ui_cfg = config.setdefault("ui", {})
    stored_version = ui_cfg.get("autostart_task_version", 0)
    if stored_version >= AUTOSTART_TASK_VERSION:
        return False
    if win_autostart_task_exists():
        try:
            register_win_autostart_task()
            log.info(f"Задача автозапуска обновлена до версии {AUTOSTART_TASK_VERSION}")
        except Exception as e:
            log.warning(f"Не удалось обновить задачу автозапуска: {e}")
    ui_cfg["autostart_task_version"] = AUTOSTART_TASK_VERSION
    return True


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
        self._app_dir = Path(self._config.get("_app_dir", Path(__file__).parent.parent))
        self._build()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text="Настройки",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg,
               pady=(m.padding_lg, m.padding_md))

        # Скроллируемый контейнер для всех карточек
        scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=p.border,
            scrollbar_button_hover_color=p.accent,
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        scroll.grid_columnconfigure(0, weight=1)

        # ── Автозапуск ────────────────────────────
        auto_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        auto_card.grid(row=1, column=0, sticky="ew", padx=m.padding_lg,
                       pady=(0, m.padding_md))

        auto_header = ctk.CTkFrame(auto_card, fg_color="transparent")
        auto_header.pack(anchor="w", padx=m.padding_md, pady=(m.padding_md, 4))
        ctk.CTkLabel(
            auto_header, text="Автозапуск",
            font=(t.family_ui, t.size_md, "bold"),
            text_color=p.text_primary,
        ).pack(side="left")
        add_help_icon(
            auto_header,
            "Настройки автоматического поведения при запуске FlowZap:\n"
            "• включать zapret сразу при старте\n"
            "• запускать FlowZap вместе с Windows\n"
            "• восстанавливать состояние DNS/TG Proxy как в прошлый раз\n"
            "• при закрытии окна не завершать работу, а сворачивать в трей",
            popup_width=280,
        )

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
        # Не паковать сразу — будет показан через pack() только когда
        # появится текст статуса (см. _on_win_autostart_change), чтобы
        # пустой лейбл не создавал визуальный зазор между переключателями.

        # Запоминать состояние сервисов (zapret/DNS/TG Proxy) между запусками
        self._restore_state_var = ctk.BooleanVar(
            value=self._config.get("ui", {}).get("restore_state", True))
        ctk.CTkSwitch(
            auto_card,
            text="Запоминать активные сервисы для следующего запуска",
            variable=self._restore_state_var,
            progress_color=p.accent, button_color=p.text_primary,
            font=(t.family_ui, t.size_md), text_color=p.text_primary,
            command=self._on_restore_state_change,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, 8))

        # Трей
        self._tray_var = ctk.BooleanVar(
            value=self._config.get("ui", {}).get("tray_enabled", True))
        ctk.CTkSwitch(
            auto_card, text="Сворачивать в трей при закрытии окна",
            variable=self._tray_var,
            progress_color=p.accent, button_color=p.text_primary,
            font=(t.family_ui, t.size_md), text_color=p.text_primary,
            command=self._on_tray_change,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, m.padding_md))

        # ── Стиль статус-бара ─────────────────────
        style_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
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
        theme_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
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

        # ── Ярлык ────────────────────────────────
        shortcut_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        shortcut_card.grid(row=4, column=0, sticky="ew", padx=m.padding_lg,
                           pady=(0, m.padding_md))
        shortcut_card.grid_columnconfigure(0, weight=1)

        shortcut_row = ctk.CTkFrame(shortcut_card, fg_color="transparent")
        shortcut_row.grid(row=0, column=0, sticky="ew", padx=m.padding_md, pady=(10, 10))
        shortcut_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(shortcut_row, text="Ярлык",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(row=0, column=0, sticky="w")

        self._shortcut_btn = ctk.CTkButton(
            shortcut_row, text="Создать на рабочем столе",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_primary, height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._create_shortcut,
        )
        self._shortcut_btn.grid(row=0, column=1, sticky="e")

        # ── Логи ─────────────────────────────────
        logs_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        logs_card.grid(row=5, column=0, sticky="ew", padx=m.padding_lg,
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

    def _get_win_autostart(self) -> bool:
        """Проверить есть ли задача FlowZap в планировщике задач."""
        return win_autostart_task_exists()

    def _set_win_autostart_status(self, text: str, text_color=None) -> None:
        """Показать/скрыть статус-лейбл — пустой текст убирает его из layout
        чтобы не было визуального зазора между переключателями."""
        if text:
            kwargs = {"text": text}
            if text_color:
                kwargs["text_color"] = text_color
            self._win_autostart_status.configure(**kwargs)
            self._win_autostart_status.pack(anchor="w", padx=theme.metrics.padding_md, pady=(0, 4))
        else:
            self._win_autostart_status.configure(text="")
            self._win_autostart_status.pack_forget()

    def _on_win_autostart_change(self) -> None:
        import subprocess
        enable = self._win_autostart_var.get()
        try:
            if enable:
                register_win_autostart_task()
                # Задача создана вручную сейчас — она заведомо актуальной
                # схемы, дальнейшая автомиграция при старте не нужна.
                self._config.setdefault("ui", {})["autostart_task_version"] = AUTOSTART_TASK_VERSION
                self._save()
                self._set_win_autostart_status(
                    "✓ FlowZap добавлен в автозапуск",
                    theme.palette.success,
                )
            else:
                result = subprocess.run(
                    ["schtasks", "/delete", "/tn", "FlowZap", "/f"],
                    capture_output=True, text=True, timeout=10,
                    encoding="cp866", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    self._set_win_autostart_status(
                        "Автозапуск отключён",
                        theme.palette.text_muted,
                    )
                else:
                    raise RuntimeError(result.stdout.strip() or result.stderr.strip())

            self.after(4000, lambda: self._set_win_autostart_status(""))

        except Exception as e:
            logging.getLogger("flowzap.autostart").error(
                f"Не удалось изменить автозапуск Windows (enable={enable}): {e}"
            )
            self._win_autostart_var.set(not enable)
            self._set_win_autostart_status("Ошибка автозапуска", theme.palette.error)

    def _open_logs_folder(self) -> None:
        import subprocess, os
        logs_dir = self._app_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            subprocess.Popen(["explorer", str(logs_dir)])
        else:
            subprocess.Popen(["xdg-open", str(logs_dir)])

    def _on_restore_state_change(self) -> None:
        if "ui" not in self._config:
            self._config["ui"] = {}
        self._config["ui"]["restore_state"] = self._restore_state_var.get()
        root = self.winfo_toplevel()
        if hasattr(root, "save_config"):
            root.save_config()

    def _on_tray_change(self) -> None:
        enabled = self._tray_var.get()
        if "ui" not in self._config:
            self._config["ui"] = {}
        self._config["ui"]["tray_enabled"] = enabled
        root = self.winfo_toplevel()
        if hasattr(root, "set_tray_enabled"):
            root.set_tray_enabled(enabled)
        if hasattr(root, "save_config"):
            root.save_config()

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

    def _create_shortcut(self) -> None:
        """Создать ярлык FlowZap на рабочем столе."""
        import os
        try:
            exe_path = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).parent.parent / "main.py"
            desktop = Path(os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"))
            shortcut_path = desktop / "FlowZap.lnk"

            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(shortcut_path))
            shortcut.Targetpath = str(exe_path)
            shortcut.WorkingDirectory = str(exe_path.parent)
            icon_path = self._app_dir / "assets" / "icon.ico"
            if icon_path.exists():
                shortcut.IconLocation = str(icon_path)
            shortcut.save()

            self._shortcut_btn.configure(text="✓ Ярлык создан на рабочем столе", text_color=theme.palette.success)
        except ImportError:
            # win32com недоступен — используем PowerShell
            try:
                import subprocess
                exe_path = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).parent.parent / "main.py"
                desktop = Path(os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"))
                shortcut_path = desktop / "FlowZap.lnk"
                icon_path = self._app_dir / "assets" / "icon.ico"
                ps = (
                    f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{shortcut_path}");'
                    f'$s.TargetPath="{exe_path}";'
                    f'$s.WorkingDirectory="{exe_path.parent}";'
                )
                if icon_path.exists():
                    ps += f'$s.IconLocation="{icon_path}";'
                ps += "$s.Save()"
                result = subprocess.run(
                    ["powershell", "-Command", ps],
                    capture_output=True, text=True, timeout=10,
                    encoding="cp866", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip())
                self._shortcut_btn.configure(text="✓ Ярлык создан на рабочем столе", text_color=theme.palette.success)
            except Exception as e:
                logging.getLogger("flowzap.shortcut").error(f"Ошибка создания ярлыка: {e}")
                self._shortcut_btn.configure(text="Ошибка создания ярлыка", text_color=theme.palette.error)
        except Exception as e:
            logging.getLogger("flowzap.shortcut").error(f"Ошибка создания ярлыка: {e}")
            self._shortcut_btn.configure(text="Ошибка создания ярлыка", text_color=theme.palette.error)
        self.after(3000, lambda: self._shortcut_btn.configure(
            text="Создать на рабочем столе", text_color=theme.palette.text_primary))

    def _save(self) -> None:
        root = self.winfo_toplevel()
        if hasattr(root, "save_config"):
            root.save_config()

    def on_activate(self) -> None:
        pass
