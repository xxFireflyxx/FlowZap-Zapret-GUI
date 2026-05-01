"""
ui/dashboard.py — Главная вкладка FlowZap.
"""

import customtkinter as ctk
from pathlib import Path
from typing import Optional
from core.manager import ZapretManager, ServiceState
from core.bat_parser import list_presets
from core.ping_checker import PresetPingManager, PingStatus
from ui.theme import theme

_PING_DOT_COLOR = {
    PingStatus.UNKNOWN:  "#4a5568",
    PingStatus.CHECKING: "#f59e0b",
    PingStatus.OK:       "#22c55e",
    PingStatus.WARN:     "#f59e0b",
    PingStatus.FAIL:     "#ef4444",
}
_PING_SYMBOL = {
    PingStatus.UNKNOWN:  "○",
    PingStatus.CHECKING: "◌",
    PingStatus.OK:       "●",
    PingStatus.WARN:     "●",
    PingStatus.FAIL:     "●",
}
_PING_TEXT = {
    PingStatus.UNKNOWN:  "не проверялось",
    PingStatus.CHECKING: "проверка…",
    PingStatus.OK:       "работает",
    PingStatus.WARN:     "частично работает",
    PingStatus.FAIL:     "не работает",
}


def _preset_label(name: str, status: PingStatus) -> str:
    return f"{_PING_SYMBOL[status]} {name}"


def _strip_symbol(label: str) -> str:
    for sym in ("● ", "○ ", "◌ "):
        if label.startswith(sym):
            return label[len(sym):]
    return label


class DashboardTab(ctk.CTkFrame):

    def __init__(self, parent, manager: ZapretManager, config: dict = None) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self.manager = manager
        self._config = config or {}
        self._presets: list = []
        self._selected_preset: Optional[dict] = None

        zapret_dir = Path(__file__).parent.parent / "zapret"
        self._ping_mgr = PresetPingManager(
            zapret_dir=zapret_dir,
            on_update=self._on_ping_update,
            on_tests_done=self._on_tests_done,
        )

        self._build()

        # Сначала загружаем пресеты, потом кэш — порядок важен!
        self._load_presets()
        # Кэш загружаем через after() чтобы UI успел отрисоваться
        self.after(200, self._load_cache_silent)

    # ──────────────────────────────────────────────
    #  Построение UI
    # ──────────────────────────────────────────────

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="Панель управления",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg,
               pady=(m.padding_lg, m.padding_md))

        # ── Статус ────────────────────────────────
        sc = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        sc.grid(row=1, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))
        inner = ctk.CTkFrame(sc, fg_color="transparent")
        inner.pack(fill="x", padx=m.padding_md, pady=m.padding_md)
        inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(inner, text="Состояние", font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).grid(row=0, column=0, sticky="w")
        self._status_label = ctk.CTkLabel(
            inner, text="● Остановлен",
            font=(t.family_ui, t.size_lg, "bold"), text_color=p.error)
        self._status_label.grid(row=1, column=0, sticky="w")
        self._pid_label = ctk.CTkLabel(inner, text="PID: —",
            font=(t.family_ui, t.size_sm), text_color=p.text_muted)
        self._pid_label.grid(row=0, column=1, rowspan=2, sticky="e",
                             padx=(m.padding_md, 0))

        # ── Пресеты ───────────────────────────────
        pc = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        pc.grid(row=2, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))
        pc.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(pc, text="Пресет", font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).grid(
            row=0, column=0, padx=(m.padding_md, 8), pady=(m.padding_md, 4), sticky="w")

        self._preset_var = ctk.StringVar(value="— нет пресетов —")
        self._preset_menu = ctk.CTkOptionMenu(
            pc, variable=self._preset_var, values=["— загрузка —"],
            fg_color=p.bg_input,
            button_color=p.accent, button_hover_color=p.accent_dim,
            text_color=p.text_primary,
            dropdown_fg_color=p.bg_card,
            dropdown_text_color=p.text_primary,
            dropdown_hover_color=p.bg_hover,
            corner_radius=m.corner_radius_sm,
            command=self._on_preset_select,
            height=m.button_height,
        )
        self._preset_menu.grid(row=0, column=1, padx=(0, 4),
                               pady=(m.padding_md, 4), sticky="ew")

        self._btn_refresh = ctk.CTkButton(
            pc, text="↻", width=m.button_height, height=m.button_height,
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_secondary, corner_radius=m.corner_radius_sm,
            command=self._on_refresh_presets, font=(t.family_ui, t.size_lg))
        self._btn_refresh.grid(row=0, column=2, padx=(0, m.padding_md),
                               pady=(m.padding_md, 4))

        self._preset_desc = ctk.CTkLabel(
            pc, text="", font=(t.family_ui, t.size_xs),
            text_color=p.text_muted, anchor="w")
        self._preset_desc.grid(row=1, column=0, columnspan=3,
                               padx=m.padding_md, pady=(0, 4), sticky="w")

        self._args_label = ctk.CTkLabel(
            pc, text="", font=(t.family_ui, t.size_xs),
            text_color=p.text_muted, anchor="w", wraplength=580, justify="left")
        self._args_label.grid(row=2, column=0, columnspan=3,
                              padx=m.padding_md, pady=(0, 4), sticky="ew")

        self._auto_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            pc, text="Авто-рестарт при смене пресета",
            variable=self._auto_var,
            progress_color=p.accent, button_color=p.text_primary,
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
        ).grid(row=3, column=0, columnspan=3,
               padx=m.padding_md, pady=(0, m.padding_md), sticky="w")

        # ── Индикатор пинга текущего пресета ─────
        ping_card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        ping_card.grid(row=3, column=0, sticky="ew", padx=m.padding_lg,
                       pady=(0, m.padding_md))
        ping_card.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(ping_card, text="Пинг пресета",
                     font=(t.family_ui, t.size_sm), text_color=p.text_secondary).grid(
            row=0, column=0, padx=(m.padding_md, 8), pady=m.padding_md, sticky="w")

        self._ping_dot = ctk.CTkLabel(
            ping_card, text="○",
            font=(t.family_ui, 18, "bold"),
            text_color=_PING_DOT_COLOR[PingStatus.UNKNOWN], width=20)
        self._ping_dot.grid(row=0, column=1, sticky="w", pady=m.padding_md)

        self._ping_text = ctk.CTkLabel(
            ping_card, text="не проверялось",
            font=(t.family_ui, t.size_sm), text_color=p.text_muted)
        self._ping_text.grid(row=0, column=2, sticky="w", padx=(6, 0),
                             pady=m.padding_md)

        self._btn_run_tests = ctk.CTkButton(
            ping_card, text="Запустить тесты",
            height=m.button_height - 4,
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color="#000000", corner_radius=m.corner_radius_sm,
            font=(t.family_ui, t.size_sm, "bold"),
            command=self._on_run_tests,
        )
        self._btn_run_tests.grid(row=0, column=3, padx=m.padding_md,
                                 pady=m.padding_md)

        self._ping_status_label = ctk.CTkLabel(
            ping_card, text="",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted, anchor="w")
        self._ping_status_label.grid(row=1, column=0, columnspan=4,
                                     padx=m.padding_md, pady=(0, 8), sticky="w")

        # ── Кнопки управления ─────────────────────
        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.grid(row=4, column=0, padx=m.padding_lg, pady=(0, m.padding_md), sticky="w")

        btn_cfg = dict(height=m.button_height + 8, corner_radius=m.corner_radius,
                       font=(t.family_ui, t.size_md, "bold"), width=140)

        self._btn_start = ctk.CTkButton(
            bf, text="▶  Запуск",
            fg_color=p.accent, hover_color=p.accent_dim, text_color="#000000",
            command=self._on_start, **btn_cfg)
        self._btn_start.pack(side="left", padx=(0, 8))

        self._btn_stop = ctk.CTkButton(
            bf, text="■  Стоп",
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.error, border_width=1, border_color=p.error,
            command=self._on_stop, **btn_cfg)
        self._btn_stop.pack(side="left", padx=8)

        self._btn_restart = ctk.CTkButton(
            bf, text="↺  Рестарт",
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, border_width=1, border_color=p.border_light,
            command=self._on_restart, **btn_cfg)
        self._btn_restart.pack(side="left", padx=8)

        # ── Кнопка DNS ────────────────────────
        self._dns_enabled = False
        self._btn_dns = ctk.CTkButton(
            bf, text="DNS",
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, border_width=1, border_color=p.border_light,
            corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_md, "bold"),
            height=m.button_height + 8, width=100,
            command=self._on_dns_toggle,
        )
        self._btn_dns.pack(side="left", padx=(16, 0))

        self._update_buttons()

    # ──────────────────────────────────────────────
    #  Пресеты
    # ──────────────────────────────────────────────

    def _load_presets(self) -> None:
        base = Path(__file__).parent.parent
        self._presets = list_presets(base / "zapret")

        if not self._presets:
            self._preset_menu.configure(values=["— пресеты не найдены —"])
            self._preset_var.set("— пресеты не найдены —")
            return

        self._refresh_menu_values()
        names = [p['name'] for p in self._presets]
        current = _strip_symbol(self._preset_var.get())
        target = current if current in names else names[0]
        self._preset_var.set(_preset_label(target, self._ping_mgr.get_status(target)))
        self._on_preset_select(self._preset_var.get(), auto_start=False)

    def _load_cache_silent(self) -> None:
        """Загружает кэш тихо — без сообщений об ошибках если файла нет."""
        self._ping_mgr.load_cached()

    def _refresh_menu_values(self) -> None:
        """Обновить все пункты меню с актуальными символами пинга."""
        values = [
            _preset_label(p['name'], self._ping_mgr.get_status(p['name']))
            for p in self._presets
        ]
        self._preset_menu.configure(values=values)
        # Обновить текущий выбор
        if self._selected_preset:
            name = self._selected_preset['name']
            self._preset_var.set(_preset_label(name, self._ping_mgr.get_status(name)))

    def _on_refresh_presets(self) -> None:
        self._btn_refresh.configure(state="disabled", text="…")
        self.after(50, self._do_refresh)

    def _do_refresh(self) -> None:
        self._load_presets()
        self._load_cache_silent()
        self._btn_refresh.configure(state="normal", text="↻")

    # ──────────────────────────────────────────────
    #  Выбор пресета
    # ──────────────────────────────────────────────

    def _on_preset_select(self, display_name: str, auto_start: bool = True) -> None:
        name = _strip_symbol(display_name)
        preset = next((p for p in self._presets if p['name'] == name), None)
        self._selected_preset = preset

        if preset:
            args_str = ' '.join(preset['args'])
            if len(args_str) > 130:
                args_str = args_str[:127] + '…'
            self._args_label.configure(text=args_str or '(нет аргументов)')
            self._preset_desc.configure(text=preset.get('desc', ''))

        self._update_ping_indicator(name)

        if auto_start and self._auto_var.get() and preset:
            if self.manager.is_running:
                self.manager.restart(preset['args'])
            else:
                self.manager.start(preset['args'])

    def _update_ping_indicator(self, preset_name: str) -> None:
        status = self._ping_mgr.get_status(preset_name)
        self._ping_dot.configure(
            text=_PING_SYMBOL[status],
            text_color=_PING_DOT_COLOR[status],
        )
        self._ping_text.configure(
            text=_PING_TEXT[status],
            text_color=_PING_DOT_COLOR[status],
        )

    # ──────────────────────────────────────────────
    #  Тестирование
    # ──────────────────────────────────────────────

    def _on_run_tests(self) -> None:
        if self._ping_mgr.is_testing:
            return
        self._btn_run_tests.configure(state="disabled", text="Тестирование…")
        self._ping_status_label.configure(
            text="⏳ Тесты запущены (несколько минут)…",
            text_color=theme.palette.warning)
        self._ping_mgr.run_tests()

    def _on_ping_update(self, preset_name: str, status: PingStatus) -> None:
        self.after(0, self._apply_ping_update, preset_name, status)

    def _apply_ping_update(self, preset_name: str, status: PingStatus) -> None:
        self._refresh_menu_values()
        if self._selected_preset and self._selected_preset['name'] == preset_name:
            self._update_ping_indicator(preset_name)

    def _on_tests_done(self, success: bool, message: str) -> None:
        self.after(0, self._apply_tests_done, success, message)

    def _apply_tests_done(self, success: bool, message: str) -> None:
        p = theme.palette
        self._btn_run_tests.configure(state="normal", text="Запустить тесты")
        self._ping_status_label.configure(
            text=f"✓ {message}" if success else f"✗ {message}",
            text_color=p.success if success else p.error,
        )

    # ──────────────────────────────────────────────
    #  Управление процессом
    # ──────────────────────────────────────────────

    def _get_current_args(self) -> list:
        return self._selected_preset['args'] if self._selected_preset else []

    def _on_start(self) -> None:
        self.manager.start(self._get_current_args())

    def _on_stop(self) -> None:
        self.manager.stop()

    def _on_restart(self) -> None:
        self.manager.restart(self._get_current_args())

    def on_state_change(self, state: ServiceState) -> None:
        labels = {
            ServiceState.STOPPED:  ("● Остановлен",  theme.palette.error),
            ServiceState.STARTING: ("● Запускается…", theme.palette.warning),
            ServiceState.RUNNING:  ("● Активен",      theme.palette.success),
            ServiceState.STOPPING: ("● Остановка…",   theme.palette.warning),
            ServiceState.ERROR:    ("● Ошибка",        theme.palette.error),
        }
        text, color = labels.get(state, ("● Неизвестно", theme.palette.text_muted))
        self._status_label.configure(text=text, text_color=color)
        pid = self.manager.pid
        self._pid_label.configure(text=f"PID: {pid}" if pid else "PID: —")
        self._update_buttons()

    def _update_buttons(self) -> None:
        running = self.manager.is_running
        self._btn_start.configure(state="disabled" if running else "normal")
        self._btn_stop.configure(state="normal" if running else "disabled")
        self._btn_restart.configure(state="normal" if running else "disabled")

    # ──────────────────────────────────────────────
    #  DNS-кнопка
    # ──────────────────────────────────────────────
    def _get_active_interface(self) -> str:
        """Автоматически находит имя активного сетевого адаптера (поддерживает русские имена)."""
        import subprocess
        try:
            result = subprocess.run(
                'netsh interface show interface',
                capture_output=True, text=True, shell=True, encoding='cp866', errors='replace'
            )
            for line in result.stdout.splitlines():
                if 'Подключен' in line or 'Connected' in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        return ' '.join(parts[3:])
        except Exception:
            pass
        return "Ethernet"

    def _on_dns_toggle(self) -> None:
        """Переключить DNS — запускает netsh в фоновом потоке, UI не зависает."""
        import tkinter.messagebox as mb

        self._dns_enabled = not self._dns_enabled
        p = theme.palette

        servers = self._config.get("dns", {}).get("servers", [])
        dns1 = servers[0] if len(servers) > 0 else ""
        dns2 = servers[1] if len(servers) > 1 else ""

        if self._dns_enabled:
            if not dns1:
                self._dns_enabled = False
                mb.showwarning(
                    "FlowZap — DNS",
                    "Сначала добавьте DNS-адреса во вкладке «Параметры» и нажмите «Применить»."
                )
                self._btn_dns.configure(
                    fg_color=p.bg_card, hover_color=p.bg_hover,
                    text_color=p.text_secondary, border_color=p.border_light,
                )
                return

        # Показываем промежуточное состояние — кнопка серая и заблокирована
        self._btn_dns.configure(text="DNS…", state="disabled",
                                fg_color=p.bg_hover, text_color=p.text_muted,
                                border_color=p.border)

        import threading
        interface = self._get_active_interface()
        threading.Thread(
            target=self._dns_worker,
            args=(self._dns_enabled, dns1, dns2, interface),
            daemon=True,
        ).start()

    def _dns_worker(self, enable: bool, dns1: str, dns2: str, interface: str) -> None:
        """Выполняется в фоновом потоке. UI обновляется через after()."""
        import subprocess, logging
        log = logging.getLogger(__name__)
        error = ""
        try:
            if enable:
                cmd1 = f'netsh interface ip set dns name="{interface}" source=static addr={dns1} validate=no'
                log.debug(f"DNS cmd: {cmd1}")
                r1 = subprocess.run(cmd1, shell=True, capture_output=True,
                                    text=True, encoding='cp866', errors='replace')
                if r1.returncode != 0:
                    raise RuntimeError(r1.stdout.strip() or r1.stderr.strip() or f"код {r1.returncode}")
                if dns2:
                    cmd2 = f'netsh interface ip add dns name="{interface}" addr={dns2} index=2 validate=no'
                    subprocess.run(cmd2, shell=True, capture_output=True,
                                   text=True, encoding='cp866', errors='replace')
                log.info(f"DNS установлен: {dns1}" + (f", {dns2}" if dns2 else ""))
            else:
                cmd = f'netsh interface ip set dns name="{interface}" source=dhcp'
                log.debug(f"DNS reset cmd: {cmd}")
                r = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, encoding='cp866', errors='replace')
                if r.returncode != 0:
                    raise RuntimeError(r.stdout.strip() or r.stderr.strip() or f"код {r.returncode}")
                log.info("DNS сброшен на DHCP")
        except Exception as e:
            error = str(e)
            log.error(f"Ошибка DNS: {e}")

        self.after(0, self._dns_done, enable, interface, error)

    def _dns_done(self, enable: bool, interface: str, error: str) -> None:
        """Вызывается в главном потоке после завершения фоновой операции."""
        import tkinter.messagebox as mb
        p = theme.palette
        self._btn_dns.configure(text="DNS", state="normal")

        if error:
            self._dns_enabled = not enable  # откатить состояние
            mb.showerror(
                "FlowZap — DNS",
                f"Не удалось {'установить' if enable else 'сбросить'} DNS.\n\n"
                f"Интерфейс: {interface}\n"
                f"Ошибка: {error}\n\n"
                f"Убедитесь что приложение запущено от администратора."
            )
            self._btn_dns.configure(
                fg_color=p.bg_card, hover_color=p.bg_hover,
                text_color=p.text_secondary, border_color=p.border_light,
            )
        elif enable:
            self._btn_dns.configure(
                fg_color="#1a3d2b", hover_color="#1f4a33",
                text_color=p.success, border_color=p.success,
            )
        else:
            self._btn_dns.configure(
                fg_color=p.bg_card, hover_color=p.bg_hover,
                text_color=p.text_secondary, border_color=p.border_light,
            )
