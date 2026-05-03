"""
ui/updates_tab.py
-----------------
Вкладка «Обновления». Проверка и установка обновлений FlowZap.
"""

import threading
import customtkinter as ctk
from pathlib import Path
from ui.theme import theme
from core.updater import GUI_VERSION, get_latest_release, find_exe_asset


def _version_tuple(v: str) -> tuple:
    """Преобразовать строку версии в tuple для сравнения."""
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0,)


class UpdatesTab(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame, config: dict = None) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self._config = config or {}
        self._latest_release = None
        self._build()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Обновления",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg, pady=(m.padding_lg, m.padding_md))

        # ── Карточка FlowZap ──────────────────────
        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=1, column=0, sticky="ew", padx=m.padding_lg)
        card.grid_columnconfigure(0, weight=1)

        # Заголовок + текущая версия
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=m.padding_md, pady=(m.padding_md, 4))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="FlowZap",
            font=(t.family_ui, t.size_md, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text=f"Текущая версия: v{GUI_VERSION}",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_muted,
        ).grid(row=0, column=1, sticky="e")

        # Статус
        self._status_lbl = ctk.CTkLabel(
            card,
            text="Нажмите «Проверить», чтобы проверить обновления",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_secondary,
        )
        self._status_lbl.grid(row=1, column=0, sticky="w", padx=m.padding_md, pady=(0, m.padding_md))

        # Кнопки
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="w", padx=m.padding_md, pady=(0, m.padding_md))

        self._btn_check = ctk.CTkButton(
            btn_row, text="Проверить",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_primary, height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._check,
        )
        self._btn_check.pack(side="left", padx=(0, 8))

        self._btn_update = ctk.CTkButton(
            btn_row, text="Обновить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color="#000000", height=m.button_height,
            corner_radius=m.corner_radius,
            state="disabled",
            command=self._update,
        )
        self._btn_update.pack(side="left")

    # ──────────────────────────────────────────────
    #  Проверка обновлений
    # ──────────────────────────────────────────────

    def _check(self) -> None:
        self._btn_check.configure(state="disabled", text="Проверка…")
        self._status_lbl.configure(text="Проверка обновлений…", text_color=theme.palette.text_secondary)
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        release = get_latest_release()
        self.after(0, self._apply_check_result, release)

    def _apply_check_result(self, release) -> None:
        p = theme.palette
        t = theme.typography
        self._btn_check.configure(state="normal", text="Проверить")

        if not release:
            self._status_lbl.configure(
                text="Не удалось подключиться к GitHub. Проверьте интернет.",
                text_color=p.error,
            )
            return

        self._latest_release = release
        tag = release.get("tag_name", "unknown")
        has_exe = find_exe_asset(release) is not None

        current = _version_tuple(GUI_VERSION)
        latest  = _version_tuple(tag)

        if latest > current:
            if has_exe:
                self._status_lbl.configure(
                    text=f"Доступна новая версия: {tag}",
                    text_color=p.success,
                )
                self._btn_update.configure(state="normal")
            else:
                self._status_lbl.configure(
                    text=f"Новая версия {tag} доступна, но exe-файл ещё не добавлен в релиз.",
                    text_color=p.warning,
                )
                self._btn_update.configure(state="disabled")
        else:
            self._status_lbl.configure(
                text=f"У вас актуальная версия ({tag})",
                text_color=p.success,
            )
            self._btn_update.configure(state="disabled")

    # ──────────────────────────────────────────────
    #  Обновление
    # ──────────────────────────────────────────────

    def _update(self) -> None:
        self._btn_update.configure(state="disabled")
        self._btn_check.configure(state="disabled")
        self._status_lbl.configure(text="Скачиваем обновление…", text_color=theme.palette.text_secondary)

        from core.updater import download_and_install_exe
        install_dir = Path(__file__).parent.parent
        download_and_install_exe(
            install_dir=install_dir,
            on_progress=lambda msg: self.after(0, self._on_progress, msg),
            on_done=lambda ok, msg: self.after(0, self._on_done, ok, msg),
        )

    def _on_progress(self, msg: str) -> None:
        self._status_lbl.configure(text=msg, text_color=theme.palette.text_secondary)

    def _on_done(self, success: bool, msg: str) -> None:
        p = theme.palette
        self._btn_check.configure(state="normal")
        self._status_lbl.configure(
            text=msg,
            text_color=p.success if success else p.error,
        )
        if not success:
            self._btn_update.configure(state="normal")
