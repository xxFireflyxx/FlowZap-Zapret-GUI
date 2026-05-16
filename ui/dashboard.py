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

        # Статус пинга — мелко внутри строки, между текстом и стрелкой
        self._status_lbl = ctk.CTkLabel(
            self._btn_frame, text="",
            font=("Segoe UI", 9),
            text_color=p.text_muted,
            anchor="e",
        )
        self._status_lbl.grid(row=0, column=2, padx=(4, 2), pady=8, sticky="e")
        self._status_lbl.bind("<Button-1>", self._toggle_popup)

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

    def set_status_text(self, text: str) -> None:
        """Показать мелкий статус внутри строки (например 'обновление…')."""
        try:
            self._status_lbl.configure(text=text)
        except Exception:
            pass

    def clear_status_text(self) -> None:
        """Убрать статусный текст."""
        try:
            self._status_lbl.configure(text=" ")
        except Exception:
            pass

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

            # Пульсация для проверяемого пресета
            if status == PingStatus.CHECKING:
                import math as _math
                _phase = [0.0]
                def _pulse_dot(d=dot, ph=_phase):
                    if not d.winfo_exists():
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
        try:
            self._arrow.configure(text="▼")
        except Exception:
            pass

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

        _app_dir = Path(self._config.get("_app_dir", "")) or Path(__file__).parent.parent
        zapret_dir = _app_dir / "zapret"
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

        ctk.CTkLabel(pc, text="Пресет", font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).grid(
            row=0, column=0, padx=(m.padding_md, 8), pady=(m.padding_md, 4), sticky="w")

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
        self._btn_dns.pack(side="left", padx=(8, 0))

        # ── Кнопка Game Filter ────────────────
        self._game_filter_enabled = False
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

        self._update_buttons()
        self._load_game_filter_state()

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
        """Запустить тесты в фоне, показать статус в дропдауне."""
        if self._ping_mgr.is_testing:
            return
        if self._dns_enabled:
            self._ping_status_lbl.configure(
                text="⚠ Отключите DNS для точной проверки",
                text_color=theme.palette.error)
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
        else:
            self.manager.start(bat_path=self._get_current_bat())

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
        return Path(__file__).parent.parent / "zapret" / "utils" / "game_filter.enabled"

    def _load_game_filter_state(self) -> None:
        """Загрузить текущее состояние game filter из файла."""
        self._game_filter_enabled = self._game_filter_flag_path().exists()
        self._apply_game_filter_style()

    def _apply_game_filter_style(self) -> None:
        if self._game_filter_enabled:
            self._btn_game.configure(**self._btn_style_on())
        else:
            self._btn_game.configure(**self._btn_style_off())

    def _on_game_filter_toggle(self) -> None:
        flag = self._game_filter_flag_path()
        self._game_filter_enabled = not self._game_filter_enabled
        try:
            if self._game_filter_enabled:
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.write_text("all", encoding="utf-8")
            else:
                if flag.exists():
                    flag.unlink()
        except Exception as e:
            import tkinter.messagebox as mb
            self._game_filter_enabled = not self._game_filter_enabled
            mb.showerror("FlowZap — Game Filter", f"Ошибка: {e}")
        self._apply_game_filter_style()
        # Рестарт если запущен чтобы применить изменения
        if self.manager.is_running:
            self.manager.restart(bat_path=self._get_current_bat())

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
                self._btn_dns.configure(**self._btn_style_off())
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
                # Сначала сбрасываем ВСЕ DNS на DHCP — очищаем старые записи
                subprocess.run(
                    f'netsh interface ip set dns name="{interface}" source=dhcp',
                    shell=True, capture_output=True,
                    text=True, encoding='cp866', errors='replace'
                )
                # Устанавливаем основной DNS
                cmd1 = f'netsh interface ip set dns name="{interface}" source=static addr={dns1} validate=no'
                log.debug(f"DNS cmd: {cmd1}")
                r1 = subprocess.run(cmd1, shell=True, capture_output=True,
                                    text=True, encoding='cp866', errors='replace')
                if r1.returncode != 0:
                    raise RuntimeError(r1.stdout.strip() or r1.stderr.strip() or f"код {r1.returncode}")
                # Добавляем запасной DNS если есть
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
            dns1 = pairs[0].get("main", "")
            dns2 = pairs[0].get("backup", "")
        else:
            servers = dns_cfg.get("servers", [])
            dns1 = servers[0] if servers else ""
            dns2 = servers[1] if len(servers) > 1 else ""

        if not dns1:
            return

        self._dns_enabled = True
        interface = self._get_active_interface()
        import threading
        threading.Thread(
            target=self._dns_worker,
            args=(True, dns1, dns2, interface),
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
                f"Не удалось {'установить' if enable else 'сбросить'} DNS.\n\n"
                f"Интерфейс: {interface}\n"
                f"Ошибка: {error}\n\n"
                f"Убедитесь что приложение запущено от администратора."
            )
            self._btn_dns.configure(**self._btn_style_off())
        elif enable:
            self._btn_dns.configure(**self._btn_style_on())
        else:
            self._btn_dns.configure(**self._btn_style_off())
            # DNS выключен — убираем предупреждение
            self._ping_status_lbl.configure(text="")
