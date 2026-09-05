"""
ui/dashboard.py — Главная вкладка FlowZap.
"""

import customtkinter as ctk
import time
from pathlib import Path
from typing import Optional
from core.manager import ZapretManager, ServiceState
from core.bat_parser import list_presets
from core.ping_checker import PresetPingManager, PingStatus
from ui.theme import theme
from ui.help_tooltip import add_help_icon
from core.tg_proxy import TgProxyManager




_PING_COLOR = {
    PingStatus.UNKNOWN:  "#4a5568",
    PingStatus.CHECKING: "#60a5fa",  # синий — активная проверка
    PingStatus.OK:       "#22c55e",
    PingStatus.WARN:     "#f59e0b",
    PingStatus.FAIL:     "#ef4444",
}


class PresetDropdown(ctk.CTkFrame):
    """
    Кастомный дропдаун для выбора пресета с цветными индикаторами пинга.
    API:
      set_items([(name, PingStatus), ...])
      set_selected(name, status)
      get_selected_name() -> str
      on_select: callable(name) — коллбэк при выборе
    """

    def __init__(self, parent, on_select=None, on_refresh=None, fg_color="#1c2128",
                 text_color="#e8edf2", height=38, corner_radius=4, **kwargs):
        p = theme.palette
        super().__init__(parent, fg_color="transparent", corner_radius=0)
        self.grid_columnconfigure(0, weight=1)

        self._on_select = on_select
        self._on_refresh = on_refresh
        self._items: list = []           # [(name, PingStatus)]
        self._selected_name: str = ""
        self._selected_status = PingStatus.UNKNOWN
        self._popup = None
        self._popup_dots: dict = {}  # name -> dot CTkLabel для обновления в реалтайме
        self._popup_stop: dict = {}   # name -> [bool] флаг остановки пульсации

        self._fg   = fg_color
        self._tc   = text_color
        self._h    = height
        self._cr   = corner_radius
        self._p    = p

        # Кнопка-заголовок
        self._btn_frame = ctk.CTkFrame(self, fg_color=self._fg,
                                        corner_radius=self._cr, cursor="hand2")
        self._btn_frame.grid(row=0, column=0, sticky="ew")
        self._btn_frame.grid_columnconfigure(1, weight=1)
        self._btn_frame.grid_columnconfigure(2, weight=0)
        self._btn_frame.bind("<Button-1>", self._toggle_popup)

        self._dot = ctk.CTkLabel(self._btn_frame, text="●", width=26,
                                  font=("Segoe UI", 18, "bold"),
                                  text_color=_PING_COLOR[PingStatus.UNKNOWN])
        self._dot.grid(row=0, column=0, padx=(10, 4), pady=10)
        self._dot.bind("<Button-1>", self._toggle_popup)

        self._lbl = ctk.CTkLabel(self._btn_frame, text="— загрузка —",
                                  text_color=self._tc, anchor="w",
                                  font=("Segoe UI", 12))
        self._lbl.grid(row=0, column=1, sticky="ew", pady=8)
        self._lbl.bind("<Button-1>", self._toggle_popup)

        self._arrow = ctk.CTkLabel(self._btn_frame, text="▼", width=20,
                                    text_color=p.accent,
                                    font=("Segoe UI", 10))
        self._arrow.grid(row=0, column=3, padx=(0, 4), pady=8)
        self._arrow.bind("<Button-1>", self._toggle_popup)

        # Кнопка обновления — внутри строки, крайняя справа
        self._refresh_btn = ctk.CTkButton(
            self._btn_frame, text="↻", width=28, height=28,
            fg_color="transparent", hover_color=p.bg_hover,
            text_color=p.text_secondary, corner_radius=self._cr,
            font=("Segoe UI", 14),
            command=lambda: self._on_refresh() if self._on_refresh else None,
        )
        self._refresh_btn.grid(row=0, column=4, padx=(0, 6), pady=4)

    # ── Публичный API ─────────────────────

    def set_items(self, items: list) -> None:
        """items: [(name, PingStatus), ...]"""
        self._items = list(items)

    def set_selected(self, name: str, status) -> None:
        self._selected_name = name
        self._selected_status = status
        self._dot.configure(text_color=_PING_COLOR.get(status, "#4a5568"))
        self._lbl.configure(text=name or "— нет пресетов —")
        # Пульсация для активной проверки
        if status == PingStatus.CHECKING:
            self._start_pulse()
        else:
            self._stop_pulse()

    def _start_pulse(self) -> None:
        if getattr(self, "_pulsing", False):
            return
        self._pulsing = True
        self._pulse_step = 0
        self._pulse()

    def _stop_pulse(self) -> None:
        self._pulsing = False

    def _pulse(self) -> None:
        if not getattr(self, "_pulsing", False):
            return
        import math
        self._pulse_step = (self._pulse_step + 0.15) % (2 * math.pi)
        alpha = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(self._pulse_step))
        r = int(0x60 * alpha + 0x1a * (1 - alpha))
        g = int(0xa5 * alpha + 0x1a * (1 - alpha))
        b = int(0xfa * alpha + 0x1f * (1 - alpha))
        try:
            self._dot.configure(text_color=f"#{r:02x}{g:02x}{b:02x}")
            self.after(60, self._pulse)
        except Exception:
            self._pulsing = False

    def get_selected_name(self) -> str:
        return self._selected_name

    # ── Попап ─────────────────────────────

    def _toggle_popup(self, event=None) -> None:
        if self._popup and self._popup.winfo_exists():
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self) -> None:
        if not self._items:
            return
        p = self._p

        # Координаты
        self.update_idletasks()
        x = self._btn_frame.winfo_rootx()
        y = self._btn_frame.winfo_rooty() + self._btn_frame.winfo_height() + 2
        w = self._btn_frame.winfo_width()

        popup = ctk.CTkToplevel(self)
        popup.wm_overrideredirect(True)
        popup.geometry(f"{w}x{min(len(self._items)*36, 300)}+{x}+{y}")
        popup.configure(fg_color=p.bg_card)
        popup.lift()
        popup.focus_force()
        popup.bind("<FocusOut>", lambda e: self.after(100, self._close_popup))

        scroll = ctk.CTkScrollableFrame(popup, fg_color=p.bg_card,
                                         scrollbar_button_color=p.border,
                                         scrollbar_button_hover_color=p.border_light,
                                         corner_radius=0)
        scroll.pack(fill="both", expand=True)
        scroll.grid_columnconfigure(0, weight=1)

        for i, (name, status) in enumerate(self._items):
            row = ctk.CTkFrame(scroll, fg_color="transparent", cursor="hand2",
                                corner_radius=0)
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            row.grid_columnconfigure(1, weight=1)

            dot = ctk.CTkLabel(row, text="●", width=22,
                                font=("Segoe UI", 16, "bold"),
                                text_color=_PING_COLOR.get(status, "#4a5568"))
            dot.grid(row=0, column=0, padx=(8, 4), pady=4)
            self._popup_dots[name] = dot

            # Пульсация для проверяемого пресета
            if status == PingStatus.CHECKING:
                import math as _math
                _phase = [0.0]
                stop_flag = [False]
                self._popup_stop[name] = stop_flag
                def _pulse_dot(d=dot, ph=_phase, sf=stop_flag):
                    if sf[0] or not d.winfo_exists():
                        return
                    ph[0] = (ph[0] + 0.15) % (2 * _math.pi)
                    a = 0.4 + 0.6 * (0.5 + 0.5 * _math.sin(ph[0]))
                    r_ = int(0x60 * a + 0x1a * (1 - a))
                    g_ = int(0xa5 * a + 0x1a * (1 - a))
                    b_ = int(0xfa * a + 0x1f * (1 - a))
                    try:
                        d.configure(text_color=f"#{r_:02x}{g_:02x}{b_:02x}")
                        dot.after(60, _pulse_dot)
                    except Exception:
                        pass
                dot.after(60, _pulse_dot)
            else:
                # Сбросить флаг остановки если статус не CHECKING
                self._popup_stop[name] = [True]

            lbl = ctk.CTkLabel(row, text=name, text_color=p.text_primary,
                                anchor="w", font=("Segoe UI", 12))
            lbl.grid(row=0, column=1, sticky="ew", pady=4, padx=(0, 8))

            # Подсветить выбранный
            if name == self._selected_name:
                row.configure(fg_color=p.bg_hover)

            def _pick(n=name, s=status, r=row):
                self._select_item(n, s)

            for w_ in (row, dot, lbl):
                w_.bind("<Button-1>", lambda e, fn=_pick: fn())
                w_.bind("<Enter>", lambda e, r_=row: r_.configure(fg_color=p.bg_hover))
                w_.bind("<Leave>", lambda e, r_=row, n_=name:
                    r_.configure(fg_color=p.bg_hover if n_ == self._selected_name else "transparent"))

        self._popup = popup
        self._arrow.configure(text="▲")

    def _close_popup(self) -> None:
        if self._popup and self._popup.winfo_exists():
            try:
                self._popup.destroy()
            except Exception:
                pass
        self._popup = None
        self._popup_dots.clear()
        try:
            self._arrow.configure(text="▼")
        except Exception:
            pass

    def get_status_for(self, name: str):
        """Вернуть текущий статус пресета из _items."""
        for n, s in self._items:
            if n == name:
                return s
        return PingStatus.UNKNOWN

    def _select_item(self, name: str, status) -> None:
        self.set_selected(name, status)
        self._close_popup()
        if self._on_select:
            self._on_select(name)


class DashboardTab(ctk.CTkFrame):

    def __init__(self, parent, manager: ZapretManager, config: dict = None, save_config_fn=None) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self.manager = manager
        self._config = config or {}
        self._save_config_fn = save_config_fn  # callable() → сохраняет config в toml
        self._presets: list = []
        self._selected_preset: Optional[dict] = None

        import sys as _sys
        _app_dir_str = self._config.get("_app_dir", "")
        if _app_dir_str:
            _app_dir = Path(_app_dir_str)
        elif getattr(_sys, "frozen", False):
            _app_dir = Path(_sys.executable).parent
        else:
            _app_dir = Path(__file__).parent.parent
        zapret_dir = _app_dir / "zapret"
        self._ping_mgr = PresetPingManager(
            zapret_dir=zapret_dir,
            on_update=self._on_ping_update,
            on_tests_done=self._on_tests_done,
        )

        # Инициализируем TgProxyManager до _build() — он используется в UI
        self._tg_proxy = TgProxyManager(
            app_root=_app_dir,
            on_state_change=self._on_tg_proxy_state,
        )
        import logging as _lg
        _lg.getLogger(__name__).debug(f"TgProxy exe path: {self._tg_proxy._exe}, exists={self._tg_proxy.is_available}")

        self._build()

        # Сначала загружаем пресеты, потом кэш — порядок важен!
        self._load_presets()
        # Кэш загружаем через after() чтобы UI успел отрисоваться
        self.after(200, self._load_cache_silent)
        # Автоматически запускаем тесты если кэш старше 24 часов
        self.after(500, self._maybe_run_tests)

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

        preset_label_row = ctk.CTkFrame(pc, fg_color="transparent")
        preset_label_row.grid(row=0, column=0, padx=(m.padding_md, 8),
                              pady=(m.padding_md, 4), sticky="w")
        ctk.CTkLabel(preset_label_row, text="Пресет", font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).pack(side="left")
        add_help_icon(
            preset_label_row,
            "Пресет — набор правил обхода блокировок. Если сайты не открываются "
            "на текущем — попробуйте другой пресет. Точка слева от пресета "
            "показывает статус проверки:",
            legend=[
                (_PING_COLOR[PingStatus.UNKNOWN],  "не проверялось"),
                (_PING_COLOR[PingStatus.CHECKING], "идёт проверка"),
                (_PING_COLOR[PingStatus.OK],       "работает"),
                (_PING_COLOR[PingStatus.WARN],     "нестабильно"),
                (_PING_COLOR[PingStatus.FAIL],     "не работает"),
            ],
        )

        self._preset_menu = PresetDropdown(
            pc,
            on_select=self._on_preset_select,
            on_refresh=self._on_refresh_presets,
            fg_color=p.bg_input,
            text_color=p.text_primary,
            height=m.button_height,
            corner_radius=m.corner_radius_sm,
        )
        self._preset_menu.grid(row=0, column=1, padx=(0, m.padding_md),
                               pady=(m.padding_md, 6), sticky="ew")

        # Строка статуса — фиксированная высота, текст появляется при обновлении
        self._ping_status_lbl = ctk.CTkLabel(
            pc, text="",
            font=(t.family_ui, t.size_xs),
            text_color=p.text_muted,
            anchor="w",
            height=18,
        )
        self._ping_status_lbl.grid(row=1, column=0, columnspan=2,
                                   padx=m.padding_md, pady=(2, 8), sticky="w")

        # ── Game Filter — выбор режима (кнопка вкл/выкл — в верхнем ряду) ──
        gf_col = ctk.CTkFrame(pc, fg_color="transparent")
        gf_col.grid(row=2, column=0, columnspan=2, sticky="w",
                    padx=m.padding_md, pady=(0, m.padding_md))

        gf_mode_row = ctk.CTkFrame(gf_col, fg_color="transparent")
        gf_mode_row.pack(anchor="w")

        ctk.CTkLabel(
            gf_mode_row, text="Game Filter",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_secondary,
        ).pack(side="left", padx=(0, 4))
        add_help_icon(
            gf_mode_row,
            "Отдельная фильтрация для игрового трафика — применяется вместе "
            "с zapret. Включайте, если блокируется подключение к играм; "
            "попробуйте UDP — чаще всего игры используют именно его, но если "
            "не поможет, попробуйте TCP или «Все».",
            padx=(0, 8),
        )

        # Режим помнится даже когда фильтр выключен — чтобы при
        # включении тумблером в верхнем ряду применился последний выбранный.
        self._game_filter_mode = "all"        # tcp | udp | all
        self._game_filter_enabled = False
        self._game_filter_mode_buttons: dict = {}
        for mode, label in (("tcp", "TCP"), ("udp", "UDP"), ("all", "Все")):
            btn = ctk.CTkButton(
                gf_mode_row, text=label,
                width=52, height=26,
                corner_radius=m.corner_radius_sm,
                font=(t.family_ui, t.size_xs),
                fg_color=p.bg_input, hover_color=p.bg_hover,
                text_color=p.text_secondary, border_width=1, border_color=p.border_light,
                command=lambda mo=mode: self._on_game_filter_mode_select(mo),
            )
            btn.pack(side="left", padx=(0, 4))
            self._game_filter_mode_buttons[mode] = btn

        # Авто-рестарт теперь определяется автоматически — если zapret запущен
        self._auto_var = ctk.BooleanVar(value=True)

        # ── Кнопки управления ─────────────────────
        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.grid(row=3, column=0, padx=m.padding_lg, pady=(0, m.padding_md), sticky="w")

        btn_cfg = dict(height=m.button_height + 8, corner_radius=m.corner_radius,
                       font=(t.family_ui, t.size_md, "bold"), width=140)

        # Кнопка Запуск/Стоп — одна кнопка
        self._btn_toggle = ctk.CTkButton(
            bf, text="▶  Запуск",
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, border_width=1, border_color=p.border_light,
            command=self._on_toggle, **btn_cfg)
        self._btn_toggle.pack(side="left", padx=(0, 8))

        # ── Кнопка Game Filter ────────────────
        self._btn_game = ctk.CTkButton(
            bf, text="Game Filter",
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, border_width=1, border_color=p.border_light,
            corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_md, "bold"),
            height=m.button_height + 8, width=130,
            command=self._on_game_filter_toggle,
        )
        self._btn_game.pack(side="left", padx=(8, 0))

        # ── Кнопка DNS ────────────────────────
        self._dns_enabled = False
        self._dns_interface: str = ""  # интерфейс на котором был включён DNS
        self._btn_dns = ctk.CTkButton(
            bf, text="DNS",
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, border_width=1, border_color=p.border_light,
            corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_md, "bold"),
            height=m.button_height + 8, width=100,
            command=self._on_dns_toggle,
        )
        self._btn_dns.pack(side="left", padx=(8, 0))

        # ── Кнопка TG Proxy ───────────────────
        self._btn_tg = ctk.CTkButton(
            bf, text="TG Proxy",
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, border_width=1, border_color=p.border_light,
            corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_md, "bold"),
            height=m.button_height + 8, width=120,
            command=self._on_tg_proxy_toggle,
        )
        self._btn_tg.pack(side="left", padx=(8, 0))

        self._update_buttons()
        self._load_game_filter_state()
        self._update_tg_btn()

    # ──────────────────────────────────────────────
    #  Пресеты
    # ──────────────────────────────────────────────

    def _load_presets(self) -> None:
        _app_dir = Path(self._config.get("_app_dir", "")) or Path(__file__).parent.parent
        base = _app_dir
        self._presets = list_presets(base / "zapret")

        if not self._presets:
            self._preset_menu.set_items([])
            return

        self._refresh_menu_values()
        names = [p['name'] for p in self._presets]
        # Восстанавливаем сохранённый пресет из конфига
        saved = self._config.get("zapret", {}).get("last_preset", "")
        current = self._preset_menu.get_selected_name()
        target = saved if saved in names else (current if current in names else names[0])
        self._preset_menu.set_selected(target, self._ping_mgr.get_status(target))
        self._on_preset_select(target, auto_start=False)

    def _load_cache_silent(self) -> None:
        """Загружает кэш тихо — без сообщений об ошибках если файла нет."""
        self._ping_mgr.load_cached()

    def _maybe_run_tests(self) -> None:
        """Запустить тесты автоматически если кэш устарел (>24 часов) или отсутствует."""
        from core.ping_checker import find_latest_results
        results_dir = self._ping_mgr._results_dir
        latest = find_latest_results(results_dir)
        if latest is None:
            # Нет файла вообще — запускаем
            self._run_auto_tests()
            return
        age_hours = (time.time() - latest.stat().st_mtime) / 3600
        if age_hours > 168:  # раз в неделю
            self._run_auto_tests()

    def _run_auto_tests(self) -> None:
        """Запустить тесты в фоне. Если zapret запущен — сначала останавливаем."""
        if self._ping_mgr.is_testing:
            return
        if self._dns_enabled:
            self._ping_status_lbl.configure(
                text="⚠ Отключите DNS для точной проверки",
                text_color=theme.palette.warning)
            return
        if self.manager.is_running:
            # Останавливаем zapret и ждём выгрузки WinDivert перед тестами
            self._ping_status_lbl.configure(
                text="Останавливаем zapret перед проверкой…",
                text_color=theme.palette.text_muted)
            self.manager.stop()
            # Обновляем кнопку — показываем неактивное состояние
            self._update_buttons()
            # Ждём 4 сек чтобы WinDivert выгрузился, потом запускаем тесты
            self.after(4000, self._start_tests_after_stop)
            return
        self._ping_status_lbl.configure(
            text="обновление…",
            text_color=theme.palette.text_muted)
        self._ping_mgr.run_tests()

    def _start_tests_after_stop(self) -> None:
        """Запустить тесты после остановки zapret."""
        if self._ping_mgr.is_testing:
            return
        self._ping_status_lbl.configure(
            text="обновление…",
            text_color=theme.palette.text_muted)
        self._ping_mgr.run_tests()

    def _refresh_menu_values(self) -> None:
        """Обновить все пункты меню с актуальными статусами пинга."""
        items = [(p['name'], self._ping_mgr.get_status(p['name'])) for p in self._presets]
        self._preset_menu.set_items(items)
        if self._selected_preset:
            name = self._selected_preset['name']
            self._preset_menu.set_selected(name, self._ping_mgr.get_status(name))

    def _on_refresh_presets(self) -> None:
        # Кнопка теперь внутри PresetDropdown._refresh_btn
        try:
            self._preset_menu._refresh_btn.configure(state="disabled", text="…")
        except Exception:
            pass
        self._ping_status_lbl.configure(text="обновление…")
        self.after(50, self._do_refresh)

    def _do_refresh(self) -> None:
        self._load_presets()
        self._load_cache_silent()
        try:
            self._preset_menu._refresh_btn.configure(state="normal", text="↻")
        except Exception:
            pass
        self._run_auto_tests()

    # ──────────────────────────────────────────────
    #  Выбор пресета
    # ──────────────────────────────────────────────

    def _on_preset_select(self, name: str, auto_start: bool = True) -> None:
        preset = next((p for p in self._presets if p['name'] == name), None)
        self._selected_preset = preset

        if preset:
            args_str = ' '.join(preset['args'])
            if len(args_str) > 130:
                args_str = args_str[:127] + '…'
            # _args_label скрыт
            # _preset_desc скрыт
            # Сохраняем выбор в конфиг
            if "zapret" not in self._config:
                self._config["zapret"] = {}
            self._config["zapret"]["last_preset"] = name
            if self._save_config_fn:
                self._save_config_fn()

        self._update_ping_indicator(name)

        # Авто-рестарт только если zapret уже запущен
        if auto_start and preset and self.manager.is_running:
            bat = preset.get('path')
            self.manager.restart(bat_path=bat)

    def _update_ping_indicator(self, preset_name: str) -> None:
        pass

    # ──────────────────────────────────────────────
    #  Тестирование
    # ──────────────────────────────────────────────

    def _on_ping_update(self, preset_name: str, status: PingStatus) -> None:
        self.after(0, self._apply_ping_update, preset_name, status)

    def _apply_ping_update(self, preset_name: str, status: PingStatus) -> None:
        # Обновляем только конкретный элемент в списке — не перестраиваем весь список
        for i, (name, _) in enumerate(self._preset_menu._items):
            if name == preset_name:
                self._preset_menu._items[i] = (name, status)
                break
        # Если это выбранный пресет — обновить точку цвета в заголовке
        if preset_name == self._preset_menu.get_selected_name():
            self._preset_menu.set_selected(preset_name, status)
        # Обновляем точку в открытом попапе в реалтайме
        dot = self._preset_menu._popup_dots.get(preset_name)
        if dot:
            try:
                color = _PING_COLOR.get(status, "#4a5568")
                dot.configure(text_color=color)
                # Если статус CHECKING — запускаем пульсацию
                if status == PingStatus.CHECKING:
                    import math as _math
                    _phase = [0.0]
                    stop_flag = [False]
                    self._preset_menu._popup_stop[preset_name] = stop_flag
                    def _pulse(d=dot, ph=_phase, sf=stop_flag):
                        if sf[0] or not d.winfo_exists():
                            return
                        ph[0] = (ph[0] + 0.15) % (2 * _math.pi)
                        a = 0.4 + 0.6 * (0.5 + 0.5 * _math.sin(ph[0]))
                        r_ = int(0x60 * a + 0x1a * (1 - a))
                        g_ = int(0xa5 * a + 0x1a * (1 - a))
                        b_ = int(0xfa * a + 0x1f * (1 - a))
                        try:
                            d.configure(text_color=f"#{r_:02x}{g_:02x}{b_:02x}")
                            d.after(60, _pulse)
                        except Exception:
                            pass
                    dot.after(60, _pulse)
                else:
                    # Остановить старую пульсацию через флаг
                    old_flag = self._preset_menu._popup_stop.get(preset_name)
                    if old_flag:
                        old_flag[0] = True
                    dot.configure(text_color=color)
            except Exception:
                pass

    def _on_tests_done(self, success: bool, message: str) -> None:
        def _clear():
            if not self._dns_enabled:
                self._ping_status_lbl.configure(text="")
        self.after(0, _clear)

    # ──────────────────────────────────────────────
    #  Управление процессом
    # ──────────────────────────────────────────────

    def _get_current_bat(self):
        """Вернуть Path к bat файлу текущего пресета."""
        if self._selected_preset:
            return self._selected_preset.get('path')
        return None

    def _on_toggle(self) -> None:
        if self.manager.is_running:
            self.manager.stop()
            return
        if not self._selected_preset:
            # Пресетов нет — скорее всего core (zapret) ещё не установлен.
            # Устанавливаем и запускаем сразу, не отправляя на вкладку «Обновления».
            self._install_core_then_start()
            return
        self.manager.start(bat_path=self._get_current_bat())

    def _install_core_then_start(self) -> None:
        from core.updater import download_and_install_core
        from pathlib import Path
        _app_dir = Path(self._config.get("_app_dir", "")) or Path(__file__).parent.parent
        zapret_dir = _app_dir / "zapret"

        self._btn_toggle.configure(state="disabled")
        self._ping_status_lbl.configure(
            text="Устанавливаем zapret, это займёт немного времени…",
            text_color=theme.palette.text_muted)

        download_and_install_core(
            zapret_dir=zapret_dir,
            on_progress=lambda msg: self.after(
                0, lambda m=msg: self._ping_status_lbl.configure(text=m)),
            on_done=lambda ok, msg: self.after(0, self._on_core_install_done, ok, msg),
        )

    def _on_core_install_done(self, success: bool, message: str) -> None:
        self._btn_toggle.configure(state="normal")
        if not success:
            import logging
            logging.getLogger(__name__).error(f"Ошибка установки zapret: {message}")
            self._ping_status_lbl.configure(
                text="Не удалось установить zapret, попробуйте позже",
                text_color=theme.palette.error)
            self.after(4000, lambda: self._ping_status_lbl.configure(text=""))
            return
        self._ping_status_lbl.configure(
            text="✓ zapret установлен, проверяем пресеты…", text_color=theme.palette.success)
        self._load_presets()
        # Небольшая пауза — иначе _run_auto_tests() мгновенно перезатирает
        # это сообщение своим "обновление…", и зелёное подтверждение
        # практически не успевает показаться пользователю.
        self.after(1500, self._run_auto_tests)

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
        self._persist_last_state()

    def _btn_style_on(self) -> dict:
        p = theme.palette
        return dict(
            fg_color=p.btn_on_bg,
            hover_color=p.btn_on_hover,
            text_color=p.btn_on_text,
            border_color=p.btn_on_border,
        )

    def _btn_style_off(self) -> dict:
        p = theme.palette
        return dict(
            fg_color=p.bg_card,
            hover_color=p.bg_hover,
            text_color=p.text_secondary,
            border_color=p.border_light,
        )

    def _update_buttons(self) -> None:
        running = self.manager.is_running
        if running:
            self._btn_toggle.configure(text="■  Стоп", **self._btn_style_on())
        else:
            self._btn_toggle.configure(text="▶  Запуск", **self._btn_style_off())

    # ──────────────────────────────────────────────
    #  Game Filter
    # ──────────────────────────────────────────────

    def _game_filter_flag_path(self):
        from pathlib import Path
        _app_dir = Path(self._config.get("_app_dir", "")) or Path(__file__).parent.parent
        return _app_dir / "zapret" / "utils" / "game_filter.enabled"

    def _load_game_filter_state(self) -> None:
        """Загрузить текущее состояние game filter из файла.
        Файла нет — выключен (режим остаётся последним запомненным,
        по умолчанию "all", для следующего включения тумблером)."""
        flag = self._game_filter_flag_path()
        if flag.exists():
            try:
                mode = flag.read_text(encoding="utf-8", errors="replace").strip().lower()
            except Exception:
                mode = "all"
            self._game_filter_mode = mode if mode in ("tcp", "udp", "all") else "all"
            self._game_filter_enabled = True
        else:
            self._game_filter_enabled = False
        self._apply_game_filter_style()

    def prewarm(self) -> None:
        """Вызывается main_window._prewarm_tabs() один раз при старте.
        Обычный прогрев вкладок ничего не знает про кнопки режима Game
        Filter (TCP/UDP/Все) — они переключаются между bg_input и accent,
        и без явного прогона через оба состояния акцентный цвет рендерится
        только при первом реальном клике, что выглядит как мозаика."""
        p = theme.palette
        for btn in self._game_filter_mode_buttons.values():
            btn.configure(fg_color=p.accent, hover_color=p.accent,
                         text_color=p.bg_root, border_width=0)
            self.update_idletasks()
        self._apply_game_filter_style()
        self.update_idletasks()

    def _apply_game_filter_style(self) -> None:
        p = theme.palette
        for mode, btn in self._game_filter_mode_buttons.items():
            if mode == self._game_filter_mode:
                btn.configure(fg_color=p.accent, hover_color=p.accent,
                             text_color=p.bg_root, border_width=0)
            else:
                btn.configure(fg_color=p.bg_input, hover_color=p.bg_hover,
                             text_color=p.text_secondary, border_width=1,
                             border_color=p.border_light)
        if self._game_filter_enabled:
            self._btn_game.configure(**self._btn_style_on())
        else:
            self._btn_game.configure(**self._btn_style_off())

    def _write_game_filter_flag(self) -> None:
        flag = self._game_filter_flag_path()
        if self._game_filter_enabled:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(self._game_filter_mode, encoding="utf-8")
        elif flag.exists():
            flag.unlink()

    def _apply_game_filter_change(self, prev_enabled: bool, prev_mode: str) -> None:
        import logging
        log = logging.getLogger(__name__)
        try:
            self._write_game_filter_flag()
        except Exception as e:
            log.error(f"Ошибка Game Filter: {e}")
            self._game_filter_enabled = prev_enabled
            self._game_filter_mode = prev_mode
            import tkinter.messagebox as mb
            mb.showerror("FlowZap — Game Filter", "Ошибка, попробуйте позже")
        else:
            if self._game_filter_enabled:
                log.info(f"Game Filter включён, режим: {self._game_filter_mode}")
            else:
                log.info("Game Filter выключен")
        self._apply_game_filter_style()
        # Рестарт если запущен чтобы применить изменения
        if self.manager.is_running:
            self.manager.restart(bat_path=self._get_current_bat())

    def _on_game_filter_mode_select(self, mode: str) -> None:
        """Клик по TCP/UDP/Все — выбирает режим и включает фильтр (если
        уже включён — просто переключает режим без "мигания" тумблера)."""
        if self._game_filter_enabled and mode == self._game_filter_mode:
            return  # уже выбран и включён — ничего не делаем
        prev_enabled, prev_mode = self._game_filter_enabled, self._game_filter_mode
        self._game_filter_mode = mode
        self._game_filter_enabled = True
        self._apply_game_filter_change(prev_enabled, prev_mode)

    def _on_game_filter_toggle(self) -> None:
        """Основной тумблер — вкл/выкл с последним запомненным режимом."""
        prev_enabled, prev_mode = self._game_filter_enabled, self._game_filter_mode
        self._game_filter_enabled = not self._game_filter_enabled
        self._apply_game_filter_change(prev_enabled, prev_mode)

    # ──────────────────────────────────────────────
    #  TG Proxy
    # ──────────────────────────────────────────

    def _update_tg_btn(self) -> None:
        available = self._tg_proxy.is_available
        if not available:
            # Кнопка активна всегда — при нажатии покажем сообщение о скачивании
            self._btn_tg.configure(
                state="normal",
                text="TG Proxy",
                **self._btn_style_off(),
            )
            return
        if self._tg_proxy.is_running:
            self._btn_tg.configure(state="normal", text="TG Proxy", **self._btn_style_on())
        else:
            self._btn_tg.configure(state="normal", text="TG Proxy", **self._btn_style_off())

    def _on_tg_proxy_toggle(self) -> None:
        if not self._tg_proxy.is_available:
            self._install_tg_proxy_then_start()
            return
        # Запускаем в фоновом потоке чтобы не блокировать UI
        import threading
        threading.Thread(
            target=self._tg_proxy.toggle,
            daemon=True,
            name="tg-proxy-toggle"
        ).start()

    def _install_tg_proxy_then_start(self) -> None:
        from core.updater import download_and_install_tg_proxy
        from pathlib import Path
        _app_dir = Path(self._config.get("_app_dir", "")) or Path(__file__).parent.parent
        tgproxy_dir = _app_dir / "tgproxy"

        self._btn_tg.configure(state="disabled")
        self._ping_status_lbl.configure(
            text="Устанавливаем TG Proxy, это займёт немного времени…",
            text_color=theme.palette.text_muted)

        download_and_install_tg_proxy(
            tgproxy_dir=tgproxy_dir,
            on_progress=lambda msg: self.after(
                0, lambda m=msg: self._ping_status_lbl.configure(text=m)),
            on_done=lambda ok, msg: self.after(0, self._on_tg_proxy_install_done, ok, msg),
        )

    def _on_tg_proxy_install_done(self, success: bool, message: str) -> None:
        self._btn_tg.configure(state="normal")
        if not success:
            import logging
            logging.getLogger(__name__).error(f"Ошибка установки TG Proxy: {message}")
            self._ping_status_lbl.configure(
                text="Не удалось установить TG Proxy, попробуйте позже",
                text_color=theme.palette.error)
            self.after(4000, lambda: self._ping_status_lbl.configure(text=""))
            return
        self._ping_status_lbl.configure(
            text="✓ TG Proxy установлен, запускаем…", text_color=theme.palette.success)
        import threading
        threading.Thread(
            target=self._tg_proxy.toggle,
            daemon=True,
            name="tg-proxy-toggle"
        ).start()
        self.after(3000, lambda: self._ping_status_lbl.configure(text=""))

    def _on_tg_proxy_state(self, running: bool) -> None:
        self.after(0, self._update_tg_btn)
        self.after(0, self._persist_last_state)

    #  DNS-кнопка
    # ──────────────────────────────────────────────
    def _get_active_interface(self) -> str:
        """
        Находит имя интерфейса с активным шлюзом через ipconfig.
        Поддерживает русские и английские названия.
        """
        import subprocess, re
        try:
            result = subprocess.run(
                'ipconfig',
                capture_output=True, shell=True, encoding='cp866', errors='replace'
            )
            # Парсим вывод ipconfig — ищем блок с шлюзом
            current_name = ""
            for line in result.stdout.splitlines():
                # Строка с именем адаптера — не начинается с пробела и содержит ":"
                if not line.startswith(" ") and ":" in line:
                    # Убираем все префиксы до последнего слова-имени
                    # "Адаптер Ethernet Ethernet:" -> "Ethernet"
                    # "Адаптер беспроводной локальной сети Беспроводная сеть:" -> "Беспроводная сеть"
                    # "Wireless LAN adapter Беспроводная сеть:" -> "Беспроводная сеть"
                    name = re.sub(
                        r'^.*?(Ethernet|Wireless LAN adapter|беспроводной локальной сети|PPP adapter|Адаптер \w+)\s+',
                        '', line, flags=re.IGNORECASE
                    ).strip().rstrip(':').strip()
                    if not name:
                        # Fallback — берём всё после последнего известного слова
                        name = line.strip().rstrip(':').strip()
                    current_name = name
                # Строка с основным шлюзом
                elif current_name and ('Основной шлюз' in line or 'Default Gateway' in line):
                    gw = line.split(':', 1)[-1].strip()
                    # Убираем IPv6 адреса (содержат %)
                    if gw and '%' not in gw and gw != '' and not gw.startswith('fe80'):
                        # Проверяем что это валидный IPv4
                        parts = gw.split('.')
                        if len(parts) == 4:
                            return current_name
        except Exception:
            pass
        return "Ethernet"

    def _on_dns_toggle(self) -> None:
        """Переключить DNS — запускает netsh в фоновом потоке, UI не зависает."""
        import tkinter.messagebox as mb

        self._dns_enabled = not self._dns_enabled
        p = theme.palette

        dns_cfg = self._config.get("dns", {})
        pairs = dns_cfg.get("pairs", [])
        if pairs and isinstance(pairs[0], dict):
            pair  = pairs[0]
            dns1  = pair.get("ipv4_main",   pair.get("main",   ""))
            dns2  = pair.get("ipv4_backup",  pair.get("backup", ""))
            dns1v6 = pair.get("ipv6_main",  "")
            dns2v6 = pair.get("ipv6_backup", "")
        else:
            servers = dns_cfg.get("servers", [])
            dns1   = servers[0] if len(servers) > 0 else ""
            dns2   = servers[1] if len(servers) > 1 else ""
            dns1v6 = ""
            dns2v6 = ""

        if self._dns_enabled:
            if not dns1 and not dns1v6:
                self._dns_enabled = False
                mb.showwarning(
                    "FlowZap — DNS",
                    "Добавьте DNS адреса во вкладке «Параметры»."
                )
                self._btn_dns.configure(**self._btn_style_off())
                return

        # Показываем промежуточное состояние — кнопка серая и заблокирована
        self._btn_dns.configure(text="DNS…", state="disabled",
                                fg_color=p.bg_hover, text_color=p.text_muted,
                                border_color=p.border)

        import threading
        if self._dns_enabled:
            # При включении определяем интерфейс и запоминаем его
            interface = self._get_active_interface()
            self._dns_interface = interface
            import logging as _lg
            _lg.getLogger(__name__).debug(f"DNS включается на интерфейсе: '{interface}'")
        else:
            # При выключении используем тот же интерфейс что и при включении
            interface = self._dns_interface or self._get_active_interface()
            import logging as _lg
            _lg.getLogger(__name__).debug(f"DNS выключается на интерфейсе: '{interface}' (сохранён: '{self._dns_interface}')")

        threading.Thread(
            target=self._dns_worker,
            args=(self._dns_enabled, dns1, dns2, interface, dns1v6, dns2v6),
            daemon=True,
        ).start()

    def _dns_worker(self, enable: bool, dns1: str, dns2: str, interface: str,
                    dns1v6: str = "", dns2v6: str = "") -> None:
        """Выполняется в фоновом потоке. Применяет DNS через netsh."""
        import subprocess, logging
        log = logging.getLogger(__name__)
        error = ""

        def run(cmd: str):
            return subprocess.run(
                cmd, shell=True, capture_output=True,
                encoding='cp866', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

        try:
            # Получаем все активные интерфейсы через netsh
            r_ifaces = run('netsh interface show interface')
            iface_list = []
            for line in r_ifaces.stdout.splitlines():
                if 'Подключён' in line or 'Connected' in line or 'Подключен' in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        iface_list.append(' '.join(parts[3:]))

            if not iface_list:
                iface_list = [interface] if interface else ['Ethernet']

            if enable:
                for iface in iface_list:
                    # Сначала сбрасываем на DHCP чтобы очистить старые записи (IPv4 и IPv6)
                    run(f'netsh interface ip set dns name="{iface}" source=dhcp')
                    run(f'netsh interface ipv6 set dns name="{iface}" source=dhcp')
                    # IPv4
                    if dns1:
                        run(f'netsh interface ip set dns name="{iface}" source=static addr={dns1} validate=no')
                        if dns2:
                            run(f'netsh interface ip add dns name="{iface}" addr={dns2} index=2 validate=no')
                    # IPv6
                    if dns1v6:
                        run(f'netsh interface ipv6 set dns name="{iface}" source=static addr={dns1v6} validate=no')
                        if dns2v6:
                            run(f'netsh interface ipv6 add dns name="{iface}" addr={dns2v6} index=2 validate=no')
                ifaces_str = ', '.join(iface_list)
                parts_log = [x for x in [dns1, dns2, dns1v6, dns2v6] if x]
                log.info(f"DNS установлен: {', '.join(parts_log)} | Интерфейсы: {ifaces_str}")
            else:
                for iface in iface_list:
                    run(f'netsh interface ip set dns name="{iface}" source=dhcp')
                    run(f'netsh interface ipv6 set dns name="{iface}" source=dhcp')
                ifaces_str = ', '.join(iface_list)
                log.info(f"DNS сброшен на DHCP | Интерфейсы: {ifaces_str}")

        except Exception as e:
            error = str(e)
            log.error(f"Ошибка DNS (интерфейс: {interface}): {e}")

        self.after(0, self._dns_done, enable, interface, error)

    def _persist_last_state(self) -> None:
        """Записывает текущее состояние сервисов (zapret/DNS/TG Proxy) в конфиг
        сразу, а не только при штатном закрытии. Windows при выключении/
        перезагрузке убивает процесс без вызова WM_DELETE_WINDOW — если
        last_state обновлять только в on_close(), после такого завершения
        в конфиге останется устаревшее состояние с прошлого явного выхода."""
        last_state = {
            "zapret_running": bool(self.manager.is_running),
            "zapret_preset":  self._selected_preset.get("name", "") if self._selected_preset else "",
            "dns_enabled":    bool(self._dns_enabled),
            "tg_proxy_running": bool(self._tg_proxy.is_running),
        }
        self._config["last_state"] = last_state
        if self._save_config_fn:
            try:
                self._save_config_fn()
            except Exception:
                pass

    def on_close(self) -> None:
        """Вызывается при закрытии приложения — сохраняем состояние
        и сбрасываем DNS если включён."""
        self._persist_last_state()

        if self._dns_enabled:
            import subprocess
            def run(cmd):
                subprocess.run(cmd, shell=True, capture_output=True,
                               encoding='cp866', errors='replace',
                               creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                r = subprocess.run(
                    'netsh interface show interface',
                    shell=True, capture_output=True,
                    encoding='cp866', errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in r.stdout.splitlines():
                    if 'Подключён' in line or 'Connected' in line or 'Подключен' in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            iface = ' '.join(parts[3:])
                            run(f'netsh interface ip set dns name="{iface}" source=dhcp')
                            run(f'netsh interface ipv6 set dns name="{iface}" source=dhcp')
            except Exception:
                pass

    def _restart_dns(self) -> None:
        """Переподключиться к DNS — выключить и включить с новыми адресами."""
        if not self._dns_enabled:
            return
        # Выключаем текущий DNS
        self._dns_enabled = False
        interface = self._get_active_interface()
        import threading
        threading.Thread(
            target=self._dns_worker,
            args=(False, "", "", interface),
            daemon=True,
        ).start()
        # Включаем новый DNS через секунду
        self.after(1500, self._apply_new_dns)

    def _apply_new_dns(self) -> None:
        """Включить DNS с новыми адресами после переподключения."""
        dns_cfg = self._config.get("dns", {})
        pairs = dns_cfg.get("pairs", [])
        if pairs and isinstance(pairs[0], dict):
            pair   = pairs[0]
            dns1   = pair.get("ipv4_main",   pair.get("main",   ""))
            dns2   = pair.get("ipv4_backup",  pair.get("backup", ""))
            dns1v6 = pair.get("ipv6_main",  "")
            dns2v6 = pair.get("ipv6_backup", "")
        else:
            servers = dns_cfg.get("servers", [])
            dns1   = servers[0] if servers else ""
            dns2   = servers[1] if len(servers) > 1 else ""
            dns1v6 = ""
            dns2v6 = ""

        if not dns1 and not dns1v6:
            return

        self._dns_enabled = True
        interface = self._get_active_interface()
        import threading
        threading.Thread(
            target=self._dns_worker,
            args=(True, dns1, dns2, interface, dns1v6, dns2v6),
            daemon=True,
        ).start()

    def _dns_done(self, enable: bool, interface: str, error: str) -> None:
        """Вызывается в главном потоке после завершения фоновой операции."""
        import tkinter.messagebox as mb
        p = theme.palette
        self._btn_dns.configure(text="DNS", state="normal")

        # Сообщаем ping_mgr об актуальном состоянии DNS
        if hasattr(self, "_ping_mgr"):
            self._ping_mgr.set_dns_active(enable and not bool(error))

        if error:
            self._dns_enabled = not enable  # откатить состояние
            mb.showerror(
                "FlowZap — DNS",
                "Ошибка подключения DNS.\n\n"
                "Убедитесь что приложение запущено от администратора."
            )
            self._btn_dns.configure(**self._btn_style_off())
        elif enable:
            self._btn_dns.configure(**self._btn_style_on())
        else:
            self._btn_dns.configure(**self._btn_style_off())
            # DNS выключен — убираем предупреждение
            self._ping_status_lbl.configure(text="")

        self._persist_last_state()

    # ──────────────────────────────────────────────
    #  Восстановление состояния при запуске
    # ──────────────────────────────────────────────

    def restore_state(self) -> None:
        """Поднять сервисы по сохранённому состоянию из прошлой сессии.
        Вызывается из main.py после полной инициализации UI."""
        if not self._config.get("ui", {}).get("restore_state", True):
            return
        state = self._config.get("last_state", {})
        if not state:
            return

        # zapret — выбрать сохранённый пресет и запустить
        if state.get("zapret_running") and not self.manager.is_running:
            preset_name = state.get("zapret_preset", "")
            preset = next((p for p in self._presets if p["name"] == preset_name), None)
            if preset:
                self._selected_preset = preset
                self.manager.start(bat_path=preset.get("path"))

        # DNS
        if state.get("dns_enabled") and not self._dns_enabled:
            self._on_dns_toggle()

        # TG Proxy
        if state.get("tg_proxy_running") and not self._tg_proxy.is_running:
            self._on_tg_proxy_toggle()
