"""
ui/updates_tab.py
-----------------
Вкладка «Обновления».
Два блока: обновление FlowZap (GUI) и обновление Core (zapret).
"""

import threading
import customtkinter as ctk
from pathlib import Path
from ui.theme import theme
from core.updater import (
    GUI_VERSION, FLOWZAP_REPO,
    get_latest_release, find_exe_asset,
    get_installed_core_version, download_and_install_core,
    download_and_install_exe,
)


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0,)


CORE_REPO = "Flowseal/zapret-discord-youtube"


class UpdatesTab(ctk.CTkFrame):
    def __init__(
        self,
        parent: ctk.CTkFrame,
        config: dict = None,
        manager=None,
        on_core_updated=None,
    ) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self._config = config or {}
        self._manager = manager
        self._on_core_updated = on_core_updated
        self._app_dir = Path(__file__).parent.parent
        self._latest_gui_release = None
        self._build()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="Обновления",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg,
               pady=(m.padding_lg, m.padding_md))

        # ── Блок 1: FlowZap GUI ───────────────────
        self._build_gui_block(row=1)

        # ── Блок 2: Core (zapret) ─────────────────
        self._build_core_block(row=2)

    # ──────────────────────────────────────────────
    #  Блок FlowZap GUI
    # ──────────────────────────────────────────────

    def _build_gui_block(self, row: int) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=row, column=0, sticky="ew", padx=m.padding_lg,
                  pady=(0, m.padding_md))
        card.grid_columnconfigure(0, weight=1)

        # Заголовок
        ctk.CTkLabel(card, text="FlowZap",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, sticky="w", padx=m.padding_md,
            pady=(m.padding_md, 4))

        # Версия приложения
        ver_row = ctk.CTkFrame(card, fg_color="transparent")
        ver_row.grid(row=1, column=0, sticky="w", padx=m.padding_md, pady=(0, 4))

        ctk.CTkLabel(ver_row, text="Версия приложения:",
                     font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(ver_row, text=f"v{GUI_VERSION}",
                     font=(t.family_ui, t.size_sm, "bold"),
                     text_color=p.text_primary).pack(side="left")

        self._gui_status = ctk.CTkLabel(
            card, text="",
            font=(t.family_ui, t.size_sm), text_color=p.text_secondary)
        self._gui_status.grid(row=2, column=0, sticky="w",
                              padx=m.padding_md, pady=(0, 8))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="w",
                     padx=m.padding_md, pady=(0, m.padding_md))

        self._btn_gui_check = ctk.CTkButton(
            btn_row, text="Проверить",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color="#ffffff", height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._check_gui,
        )
        self._btn_gui_check.pack(side="left", padx=(0, 8))

        self._btn_gui_update = ctk.CTkButton(
            btn_row, text="Обновить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color="#000000", height=m.button_height,
            corner_radius=m.corner_radius,
            state="disabled",
            command=self._update_gui,
        )
        self._btn_gui_update.pack(side="left")

    # ──────────────────────────────────────────────
    #  Блок Core (zapret)
    # ──────────────────────────────────────────────

    def _build_core_block(self, row: int) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=row, column=0, sticky="ew", padx=m.padding_lg,
                  pady=(0, m.padding_md))
        card.grid_columnconfigure(1, weight=1)

        # Заголовок
        ctk.CTkLabel(card, text="Core (zapret)",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, columnspan=2, sticky="w",
            padx=m.padding_md, pady=(m.padding_md, 4))

        # Версия установленная
        inst_row = ctk.CTkFrame(card, fg_color="transparent")
        inst_row.grid(row=1, column=0, columnspan=2, sticky="w",
                      padx=m.padding_md, pady=(0, 2))
        ctk.CTkLabel(inst_row, text="Версия Core:",
                     font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).pack(side="left", padx=(0, 6))
        installed = get_installed_core_version(self._app_dir / "zapret")
        self._core_installed_lbl = ctk.CTkLabel(
            inst_row,
            text=installed if installed else "не установлен",
            font=(t.family_ui, t.size_sm, "bold"),
            text_color=p.text_primary)
        self._core_installed_lbl.pack(side="left")

        self._core_latest_lbl = ctk.CTkLabel(card, text="")  # скрыт

        self._core_status = ctk.CTkLabel(
            card, text="",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
            anchor="w", wraplength=450)
        self._core_status.grid(row=2, column=0, columnspan=2,
                               padx=m.padding_md, pady=(0, 8), sticky="ew")

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0, columnspan=2,
                     padx=m.padding_md, pady=(0, m.padding_md), sticky="w")

        self._btn_core_check = ctk.CTkButton(
            btn_row, text="Проверить",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color="#ffffff", height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._check_core,
        )
        self._btn_core_check.pack(side="left", padx=(0, 8))

        self._btn_core_update = ctk.CTkButton(
            btn_row, text="Обновить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color="#000000", height=m.button_height,
            corner_radius=m.corner_radius,
            state="disabled",
            command=self._update_core,
        )
        self._btn_core_update.pack(side="left")

    # ──────────────────────────────────────────────
    #  GUI обновление
    # ──────────────────────────────────────────────

    def _check_gui(self) -> None:
        self._btn_gui_check.configure(state="disabled", text="Проверка…")
        self._gui_status.configure(text="Проверка обновлений…",
                                   text_color=theme.palette.text_secondary)
        threading.Thread(target=lambda: self.after(
            0, self._apply_gui_check, get_latest_release()), daemon=True).start()

    def _apply_gui_check(self, release) -> None:
        p = theme.palette
        self._btn_gui_check.configure(state="normal", text="Проверить")
        if not release:
            self._gui_status.configure(
                text="Не удалось подключиться к GitHub.",
                text_color=p.error)
            return

        self._latest_gui_release = release
        tag = release.get("tag_name", "?")
        has_asset = find_exe_asset(release) is not None
        current = _version_tuple(GUI_VERSION)
        latest = _version_tuple(tag)

        if latest > current:
            if has_asset:
                self._gui_status.configure(
                    text=f"Доступна новая версия: {tag}",
                    text_color=p.success)
                self._btn_gui_update.configure(state="normal")
            else:
                self._gui_status.configure(
                    text=f"Версия {tag} есть, но файл релиза ещё не добавлен.",
                    text_color=p.warning)
        else:
            self._gui_status.configure(
                text=f"У вас актуальная версия ({tag})",
                text_color=p.success)

    def _update_gui(self) -> None:
        self._btn_gui_update.configure(state="disabled")
        self._btn_gui_check.configure(state="disabled")
        download_and_install_exe(
            install_dir=self._app_dir,
            on_progress=lambda msg: self.after(
                0, lambda m=msg: self._gui_status.configure(text=m)),
            on_done=lambda ok, msg: self.after(0, self._on_gui_done, ok, msg),
        )

    def _on_gui_done(self, success: bool, msg: str) -> None:
        p = theme.palette
        self._btn_gui_check.configure(state="normal")
        self._gui_status.configure(
            text=msg, text_color=p.success if success else p.error)
        if not success:
            self._btn_gui_update.configure(state="normal")

    # ──────────────────────────────────────────────
    #  Core обновление
    # ──────────────────────────────────────────────

    def _check_core(self) -> None:
        self._btn_core_check.configure(state="disabled", text="Проверяю…")
        self._core_status.configure(text="Запрос к GitHub…",
                                    text_color=theme.palette.text_muted)
        self._btn_core_update.configure(state="disabled")

        def _worker():
            release = get_latest_release(CORE_REPO)
            self.after(0, self._apply_core_check, release)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_core_check(self, release) -> None:
        p = theme.palette
        self._btn_core_check.configure(state="normal", text="Проверить")

        if not release:
            self._core_status.configure(
                text="Не удалось получить информацию. Проверьте интернет.",
                text_color=p.error)
            # core_latest_lbl скрыт
            return

        tag = release.get("tag_name", "?")
        # core_latest_lbl скрыт

        installed = get_installed_core_version(self._app_dir / "zapret")
        if installed and installed == tag:
            self._core_status.configure(
                text=f"✓ Core актуален ({tag})", text_color=p.success)
        else:
            self._core_status.configure(
                text=f"Доступно обновление: {tag}" +
                     (f" (установлено: {installed})" if installed else ""),
                text_color=p.warning)
            self._btn_core_update.configure(state="normal")

    def _update_core(self) -> None:
        if self._manager and self._manager.is_running:
            self._core_status.configure(
                text="⚠ Остановите zapret перед обновлением Core",
                text_color=theme.palette.warning)
            return

        self._btn_core_update.configure(state="disabled", text="Обновляю…")
        self._btn_core_check.configure(state="disabled")
        self._core_status.configure(text="Начинаем загрузку…",
                                    text_color=theme.palette.text_muted)

        download_and_install_core(
            zapret_dir=self._app_dir / "zapret",
            repo=CORE_REPO,
            on_progress=lambda msg: self.after(
                0, lambda m=msg: self._core_status.configure(text=m)),
            on_done=lambda ok, msg: self.after(0, self._on_core_done, ok, msg),
        )

    def _on_core_done(self, success: bool, message: str) -> None:
        p = theme.palette
        self._btn_core_check.configure(state="normal")
        self._btn_core_update.configure(text="Обновить")

        if success:
            self._core_status.configure(text=f"✓ {message}", text_color=p.success)
            installed = get_installed_core_version(self._app_dir / "zapret")
            if installed:
                self._core_installed_lbl.configure(text=installed)
            if self._on_core_updated:
                self._on_core_updated()
        else:
            self._core_status.configure(text=f"✗ {message}", text_color=p.error)
            self._btn_core_update.configure(state="normal")

    def on_activate(self) -> None:
        """Обновить отображение версии Core при переходе на вкладку."""
        installed = get_installed_core_version(self._app_dir / "zapret")
        self._core_installed_lbl.configure(
            text=installed if installed else "не установлен")
