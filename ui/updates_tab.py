"""
ui/updates_tab.py
-----------------
Вкладка «Обновления». Проверка и установка обновлений zapret.
"""

import customtkinter as ctk
from ui.theme import theme


class UpdatesTab(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
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

        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=1, column=0, sticky="ew", padx=m.padding_lg)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="zapret core",
            font=(t.family_ui, t.size_md, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_md, pady=(m.padding_md, 4))

        self._status_lbl = ctk.CTkLabel(
            card,
            text="Нажмите «Проверить», чтобы проверить обновления",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_secondary,
        )
        self._status_lbl.grid(row=1, column=0, sticky="w", padx=m.padding_md, pady=(0, m.padding_md))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", padx=m.padding_md, pady=(0, m.padding_md))

        ctk.CTkButton(
            btn_row, text="Проверить",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_primary, height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._check,
        ).pack(side="left", padx=(0, 8))

        self._btn_update = ctk.CTkButton(
            btn_row, text="Обновить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color="#000000", height=m.button_height,
            corner_radius=m.corner_radius,
            state="disabled",
            command=self._update,
        )
        self._btn_update.pack(side="left")

    def _check(self) -> None:
        self._status_lbl.configure(text="Проверяется… (функция в разработке)")

    def _update(self) -> None:
        self._status_lbl.configure(text="Обновление… (функция в разработке)")
