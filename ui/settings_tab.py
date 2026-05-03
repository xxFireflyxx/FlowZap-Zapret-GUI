"""
ui/settings_tab.py
------------------
Вкладка «Настройки».
Содержит: автозапуск, стиль статус-бара, тема интерфейса.
Всё сохраняется автоматически — кнопка «Сохранить» не нужна.
"""

import customtkinter as ctk
from pathlib import Path
from ui.theme import theme, THEME_NAMES
from core.manager import ZapretManager


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

        self._autostart_var = ctk.BooleanVar(
            value=self._config.get("zapret", {}).get("autostart", False))
        ctk.CTkSwitch(
            auto_card,
            text="Автозапуск zapret при старте FlowZap",
            variable=self._autostart_var,
            progress_color=p.accent, button_color=p.text_primary,
            font=(t.family_ui, t.size_md), text_color=p.text_primary,
            command=self._on_autostart_change,
        ).pack(anchor="w", padx=m.padding_md, pady=m.padding_md)

        # ── Стиль статус-бара ─────────────────────
        style_card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        style_card.grid(row=2, column=0, sticky="ew", padx=m.padding_lg,
                        pady=(0, m.padding_md))
        style_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(style_card, text="Стиль статус-бара",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, columnspan=2, sticky="w",
            padx=m.padding_md, pady=(m.padding_md, 8))

        self._bar_style_var = ctk.StringVar(
            value=self._config.get("ui", {}).get("bar_style", "default"))
        ctk.CTkOptionMenu(
            style_card,
            variable=self._bar_style_var,
            values=["default", "gradient", "pulse", "minimal"],
            fg_color=p.bg_input,
            button_color=p.accent, button_hover_color=p.accent_dim,
            text_color=p.text_primary,
            dropdown_fg_color=p.bg_card,
            dropdown_text_color=p.text_primary,
            dropdown_hover_color=p.bg_hover,
            corner_radius=m.corner_radius_sm,
            command=self._on_bar_style_change,
            height=m.button_height,
        ).grid(row=1, column=0, columnspan=2, sticky="ew",
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

        self._theme_var = ctk.StringVar(
            value=self._config.get("ui", {}).get("theme_name", "default"))
        ctk.CTkOptionMenu(
            theme_card,
            variable=self._theme_var,
            values=list(THEME_NAMES.values()),
            fg_color=p.bg_input,
            button_color=p.accent, button_hover_color=p.accent_dim,
            text_color=p.text_primary,
            dropdown_fg_color=p.bg_card,
            dropdown_text_color=p.text_primary,
            dropdown_hover_color=p.bg_hover,
            corner_radius=m.corner_radius_sm,
            command=self._on_theme_change,
            height=m.button_height,
        ).grid(row=2, column=0, columnspan=2, sticky="ew",
               padx=m.padding_md, pady=(0, m.padding_md))

    # ──────────────────────────────────────────────
    #  Обработчики
    # ──────────────────────────────────────────────

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
        if "ui" not in self._config:
            self._config["ui"] = {}
        self._config["ui"]["bar_style"] = value
        root = self.winfo_toplevel()
        if hasattr(root, "set_bar_style"):
            root.set_bar_style(value)
        self._save()

    def _save(self) -> None:
        root = self.winfo_toplevel()
        if hasattr(root, "save_config"):
            root.save_config()

    def on_activate(self) -> None:
        pass
