"""
ui/settings_tab.py
------------------
Вкладка «Настройки».
"""

import customtkinter as ctk
from pathlib import Path
from ui.theme import theme
from core.manager import ZapretManager
from core.updater import (
    GUI_VERSION, FLOWZAP_REPO as FLOWSEAL_REPO,
    get_latest_release, get_installed_core_version,
    download_and_install_core,
)
import threading


class SettingsTab(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame, manager: ZapretManager, config: dict = None, on_core_updated=None) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self.manager = manager
        self._config = config or {}
        self._repo = self._config.get("updater", {}).get("repo", "xxFireflyxx/Flowzap-gui-zapret-dns-tgwsproxy")
        self._app_dir = Path(__file__).parent.parent
        self._on_core_updated = on_core_updated  # коллбэк -> запуск тестов пинга
        self._build()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Скроллируемый контейнер
        scroll = ctk.CTkScrollableFrame(
            self, fg_color=p.bg_root, corner_radius=0,
            scrollbar_button_color=p.border,
            scrollbar_button_hover_color=p.border_light,
        )
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            scroll,
            text="Настройки",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg,
               pady=(m.padding_lg, m.padding_md))

        # ── Пути ─────────────────────────────
        paths_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        paths_card.grid(row=1, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))
        paths_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            paths_card, text="Путь к winws.exe",
            font=(t.family_ui, t.size_sm), text_color=p.text_secondary,
        ).grid(row=0, column=0, padx=m.padding_md, pady=(m.padding_md, 4), sticky="w")

        self._exe_entry = ctk.CTkEntry(
            paths_card,
            placeholder_text="zapret/bin/winws.exe",
            fg_color=p.bg_input, text_color=p.text_primary,
            border_color=p.border, corner_radius=m.corner_radius_sm,
        )
        self._exe_entry.grid(row=0, column=1, padx=(0, m.padding_md),
                             pady=(m.padding_md, m.padding_md), sticky="ew")

        # ── Поведение ─────────────────────────
        beh_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        beh_card.grid(row=2, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))

        self._autostart_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            beh_card, text="Автозапуск zapret при старте FlowZap",
            variable=self._autostart_var,
            progress_color=p.accent, button_color=p.text_primary,
            font=(t.family_ui, t.size_md), text_color=p.text_primary,
        ).pack(anchor="w", padx=m.padding_md, pady=m.padding_md)

        # ── Стиль градиентной полоски ─────────
        bar_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        bar_card.grid(row=3, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))

        ctk.CTkLabel(
            bar_card, text="Стиль полоски",
            font=(t.family_ui, t.size_md, "bold"), text_color=p.text_primary,
        ).pack(anchor="w", padx=m.padding_md, pady=(m.padding_md, 4))

        ctk.CTkLabel(
            bar_card, text="Анимированная полоска вверху окна",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, 8))

        self._bar_style_var = ctk.StringVar(
            value=self._config.get("ui", {}).get("bar_style", "default")
        )
        ctk.CTkSegmentedButton(
            bar_card,
            values=["default", "candy", "rainbow", "none"],
            variable=self._bar_style_var,
            selected_color=p.accent,
            selected_hover_color=p.accent_dim,
            fg_color=p.bg_input,
            unselected_color=p.bg_input,
            text_color=p.text_secondary,
            command=self._on_bar_style_change,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, m.padding_md))

        # ── Выбор темы ────────────────────────
        theme_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        theme_card.grid(row=4, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))

        ctk.CTkLabel(
            theme_card, text="Тема интерфейса",
            font=(t.family_ui, t.size_md, "bold"), text_color=p.text_primary,
        ).pack(anchor="w", padx=m.padding_md, pady=(m.padding_md, 4))

        ctk.CTkLabel(
            theme_card, text="Применится после перезапуска приложения",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, 8))

        from ui.theme import THEME_NAMES
        self._theme_var = ctk.StringVar(
            value=self._config.get("ui", {}).get("theme_name", "default")
        )
        ctk.CTkSegmentedButton(
            theme_card,
            values=list(THEME_NAMES.keys()),
            variable=self._theme_var,
            selected_color=p.accent,
            selected_hover_color=p.accent_dim,
            fg_color=p.bg_input,
            unselected_color=p.bg_input,
            text_color=p.text_secondary,
            command=self._on_theme_change,
        ).pack(anchor="w", padx=m.padding_md, pady=(0, m.padding_md))

        # ── Обновления Core ───────────────────
        update_card = ctk.CTkFrame(scroll, fg_color=p.bg_card, corner_radius=m.corner_radius)
        update_card.grid(row=5, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))
        update_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            update_card, text="Версии и обновления",
            font=(t.family_ui, t.size_md, "bold"), text_color=p.text_primary,
        ).grid(row=0, column=0, columnspan=3, padx=m.padding_md,
               pady=(m.padding_md, 8), sticky="w")

        ctk.CTkLabel(
            update_card, text="GUI:",
            font=(t.family_ui, t.size_sm), text_color=p.text_secondary,
        ).grid(row=1, column=0, padx=(m.padding_md, 4), pady=2, sticky="w")

        ctk.CTkLabel(
            update_card, text=f"v{GUI_VERSION}",
            font=(t.family_ui, t.size_sm, "bold"), text_color=p.text_primary,
        ).grid(row=1, column=1, padx=0, pady=2, sticky="w")

        ctk.CTkLabel(
            update_card, text="Core:",
            font=(t.family_ui, t.size_sm), text_color=p.text_secondary,
        ).grid(row=2, column=0, padx=(m.padding_md, 4), pady=2, sticky="w")

        installed = get_installed_core_version(self._app_dir / "zapret")
        self._core_ver_label = ctk.CTkLabel(
            update_card,
            text=installed if installed else "неизвестно",
            font=(t.family_ui, t.size_sm, "bold"), text_color=p.text_primary,
        )
        self._core_ver_label.grid(row=2, column=1, padx=0, pady=2, sticky="w")

        ctk.CTkLabel(
            update_card, text="Доступно:",
            font=(t.family_ui, t.size_sm), text_color=p.text_secondary,
        ).grid(row=3, column=0, padx=(m.padding_md, 4), pady=2, sticky="w")

        self._latest_ver_label = ctk.CTkLabel(
            update_card, text="—",
            font=(t.family_ui, t.size_sm, "bold"), text_color=p.text_muted,
        )
        self._latest_ver_label.grid(row=3, column=1, padx=0, pady=2, sticky="w")

        btn_row = ctk.CTkFrame(update_card, fg_color="transparent")
        btn_row.grid(row=4, column=0, columnspan=3,
                     padx=m.padding_md, pady=(8, m.padding_md), sticky="w")

        self._btn_check = ctk.CTkButton(
            btn_row, text="Проверить обновления",
            height=m.button_height,
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_secondary,
            corner_radius=m.corner_radius_sm,
            font=(t.family_ui, t.size_sm),
            command=self._check_updates,
        )
        self._btn_check.pack(side="left", padx=(0, 8))

        self._btn_update = ctk.CTkButton(
            btn_row, text="Обновить Core",
            height=m.button_height,
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color="#000000",
            corner_radius=m.corner_radius_sm,
            font=(t.family_ui, t.size_sm, "bold"),
            command=self._update_core,
            state="disabled",
        )
        self._btn_update.pack(side="left")

        self._update_status = ctk.CTkLabel(
            update_card, text="",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
            anchor="w", wraplength=400,
        )
        self._update_status.grid(row=5, column=0, columnspan=3,
                                 padx=m.padding_md, pady=(0, m.padding_md), sticky="ew")

        # ── Кнопка сохранить ─────────────────
        ctk.CTkButton(
            scroll, text="Сохранить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color="#000000", height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._save,
        ).grid(row=6, column=0, sticky="w", padx=m.padding_lg, pady=(0, m.padding_lg))

    # ─────────────────────────────────────────
    #  Тема
    # ─────────────────────────────────────────

    def _on_theme_change(self, value: str) -> None:
        if "ui" not in self._config:
            self._config["ui"] = {}
        self._config["ui"]["theme_name"] = value
        root = self.winfo_toplevel()
        if hasattr(root, "save_config"):
            root.save_config()

    # ─────────────────────────────────────────
    #  Полоска
    # ─────────────────────────────────────────

    def _on_bar_style_change(self, value: str) -> None:
        root = self.winfo_toplevel()
        if hasattr(root, "set_bar_style"):
            root.set_bar_style(value)

    # ─────────────────────────────────────────
    #  Обновления
    # ─────────────────────────────────────────

    def _check_updates(self) -> None:
        self._btn_check.configure(state="disabled", text="Проверяю…")
        self._update_status.configure(text="Запрос к GitHub...", text_color=theme.palette.text_muted)
        self._btn_update.configure(state="disabled")

        def _worker() -> None:
            latest = get_latest_release(self._repo)
            self.after(0, self._on_check_done, latest)

        threading.Thread(target=_worker, daemon=True, name="update-check").start()

    def _on_check_done(self, latest) -> None:
        p = theme.palette
        self._btn_check.configure(state="normal", text="Проверить обновления")

        if isinstance(latest, dict):
            latest = latest.get("tag_name")

        if latest is None:
            self._update_status.configure(
                text="Не удалось получить информацию. Проверьте интернет-соединение.",
                text_color=p.error)
            self._latest_ver_label.configure(text="ошибка", text_color=p.error)
            return

        self._latest_ver_label.configure(text=latest, text_color=p.text_primary)

        installed = get_installed_core_version(self._app_dir / "zapret")
        if installed and installed == latest:
            self._update_status.configure(
                text=f"✓ Core актуален ({latest})", text_color=p.success)
        else:
            self._update_status.configure(
                text=f"Доступно обновление: {latest}" + (f" (установлено: {installed})" if installed else ""),
                text_color=p.warning,
            )
            self._btn_update.configure(state="normal")

    def _update_core(self) -> None:
        if self.manager.is_running:
            self._update_status.configure(
                text="⚠ Остановите zapret перед обновлением Core",
                text_color=theme.palette.warning)
            return

        self._btn_update.configure(state="disabled", text="Обновляю…")
        self._btn_check.configure(state="disabled")
        self._update_status.configure(text="Начинаем загрузку...",
                                      text_color=theme.palette.text_muted)

        zapret_dir = self._app_dir / "zapret"
        download_and_install_core(
            zapret_dir=zapret_dir,
            repo=self._repo,
            on_progress=lambda msg: self.after(0, lambda m=msg: self._update_status.configure(text=m)),
            on_done=self._on_update_done,
        )

    def _on_update_done(self, success: bool, message: str) -> None:
        self.after(0, self._apply_update_done, success, message)

    def _apply_update_done(self, success: bool, message: str) -> None:
        p = theme.palette
        self._btn_check.configure(state="normal")
        self._btn_update.configure(text="Обновить Core")

        if success:
            self._update_status.configure(text=f"✓ {message}", text_color=p.success)
            installed = get_installed_core_version(self._app_dir / "zapret")
            if installed:
                self._core_ver_label.configure(text=installed)
            # Запустить тесты пинга после успешного обновления Core
            if self._on_core_updated:
                self._update_status.configure(
                    text=f"✓ {message} — запускаем тесты пресетов…",
                    text_color=p.success,
                )
                self._on_core_updated()
        else:
            self._update_status.configure(text=f"✗ {message}", text_color=p.error)
            self._btn_update.configure(state="normal")

    # ─────────────────────────────────────────

    def on_activate(self) -> None:
        installed = get_installed_core_version(self._app_dir / "zapret")
        self._core_ver_label.configure(text=installed if installed else "неизвестно")

    def _save(self) -> None:
        root = self.winfo_toplevel()
        if hasattr(root, "save_config"):
            root.save_config()
