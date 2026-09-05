"""
ui/help_tooltip.py
-------------------
Маленький значок «?» рядом с заголовком блока — по клику показывает
всплывающую подсказку с пояснением. Используется на Dashboard,
Параметрах и Настройках.

Подсказка рисуется как обычный CTkFrame поверх содержимого главного окна
(.place() + .lift()), а не отдельным окном ОС (CTkToplevel). Так проще
и надёжнее: сворачивание в трей, Alt+Tab, потеря фокуса — всё работает
само собой, раз подсказка физически является частью того же самого окна
приложения, а не отдельной сущностью, которую нужно вручную прятать/
поднимать/синхронизировать с видимостью главного окна.
"""

import customtkinter as ctk
from ui.theme import theme


class HelpIcon(ctk.CTkLabel):
    """Значок '?' — клик открывает/закрывает всплывающую подсказку с текстом.
    Опционально можно добавить цветную легенду (список пар цвет/подпись) —
    например расшифровку цветных точек статуса."""

    _open_instances: list = []  # все сейчас открытые подсказки — чтобы можно
                                 # было закрыть их разом (переключение вкладки)

    def __init__(self, parent, text: str, popup_width: int = 240,
                 legend: list = None, **kwargs) -> None:
        p = theme.palette
        t = theme.typography
        super().__init__(
            parent, text="?",
            width=16, height=16,
            corner_radius=8,
            fg_color=p.bg_hover,
            text_color=p.text_secondary,
            font=(t.family_ui, t.size_xs, "bold"),
            cursor="hand2",
            **kwargs,
        )
        self._help_text = text
        self._popup_width = popup_width
        self._legend = legend or []
        self._popup_frame = None
        self._just_opened = False
        self.bind("<Button-1>", self._toggle_popup)
        root = self.winfo_toplevel()
        root.bind_all("<Button-1>", self._on_global_click, add="+")

    def _toggle_popup(self, event=None) -> None:
        if self._popup_frame is not None and self._popup_frame.winfo_exists():
            self._close_popup()
        else:
            try:
                self._open_popup()
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(
                    f"Ошибка открытия подсказки HelpIcon: {e}"
                )

    def _on_global_click(self, event) -> None:
        if self._popup_frame is None or not self._popup_frame.winfo_exists():
            return
        if self._just_opened:
            return
        w = event.widget
        if w is self:
            return
        node = w
        while node is not None:
            if node is self._popup_frame:
                return
            node = getattr(node, "master", None)
        self._close_popup()

    def _open_popup(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        root = self.winfo_toplevel()
        root.update_idletasks()

        frame = ctk.CTkFrame(
            root, fg_color=p.bg_card, corner_radius=m.corner_radius_sm,
            border_width=1, border_color=p.border, width=self._popup_width,
        )

        ctk.CTkLabel(
            frame, text=self._help_text,
            font=(t.family_ui, t.size_sm),
            text_color=p.text_secondary,
            wraplength=self._popup_width - 20,
            justify="left",
        ).pack(padx=10, pady=(8, 4 if self._legend else 8), anchor="w")

        for color, label in self._legend:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(padx=10, pady=1, anchor="w", fill="x")
            ctk.CTkLabel(
                row, text="●",
                font=(t.family_ui, t.size_sm),
                text_color=color, width=14,
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=label,
                font=(t.family_ui, t.size_sm),
                text_color=p.text_secondary,
            ).pack(side="left")
        if self._legend:
            ctk.CTkFrame(frame, fg_color="transparent", height=4).pack()

        # Меряем размер ДО первого показа — geometry() у пакованных детей
        # (текст + легенда) считается независимо от того, размещён ли уже
        # сам frame через .place(). Если мерить после первого place(),
        # размер ещё не пересчитан, и виден "прыжок" от большого кадра
        # к подогнанному под текст (особенно заметно у длинной легенды).
        frame.update_idletasks()

        icon_x = self.winfo_rootx() - root.winfo_rootx()
        icon_y = self.winfo_rooty() - root.winfo_rooty() + self.winfo_height() + 4

        root_w = root.winfo_width()
        root_h = root.winfo_height()
        fw = frame.winfo_reqwidth()
        fh = frame.winfo_reqheight()
        final_x = min(icon_x, max(0, root_w - fw - 8))
        final_y = icon_y
        if final_y + fh > root_h:
            final_y = max(0, self.winfo_rooty() - root.winfo_rooty() - fh - 4)

        frame.place(x=final_x, y=final_y)
        frame.lift()

        self._popup_frame = frame
        HelpIcon._open_instances.append(self)
        self._just_opened = True
        self.after(150, self._clear_just_opened)

    def _clear_just_opened(self) -> None:
        self._just_opened = False

    def _close_popup(self) -> None:
        if self._popup_frame is not None and self._popup_frame.winfo_exists():
            try:
                self._popup_frame.destroy()
            except Exception:
                pass
        self._popup_frame = None
        if self in HelpIcon._open_instances:
            HelpIcon._open_instances.remove(self)

    @classmethod
    def close_all(cls) -> None:
        """Закрыть все сейчас открытые подсказки — вызывается при
        переключении вкладки, чтобы подсказка не осталась висеть поверх
        контента другой вкладки."""
        for icon in list(cls._open_instances):
            icon._close_popup()


def add_help_icon(parent, text: str, side: str = "left", padx=(6, 0),
                   popup_width: int = 240, legend: list = None,
                   **pack_kwargs) -> HelpIcon:
    """Создать значок подсказки и сразу разместить его (.pack) рядом с
    предыдущим виджетом в той же строке."""
    icon = HelpIcon(parent, text, popup_width=popup_width, legend=legend)
    icon.pack(side=side, padx=padx, **pack_kwargs)
    return icon
