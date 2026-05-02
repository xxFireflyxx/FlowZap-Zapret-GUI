"""
ui/logs_tab.py
--------------
Вкладка «Логи». Реал-тайм вывод с цветовой подсветкой по тегу.
"""

import re
import customtkinter as ctk
from tkinter import Text, END, INSERT
from ui.theme import theme
from core.manager import ZapretManager


# Регулярка для извлечения тега: [INFO], [ERROR] и т.д.
_TAG_RE = re.compile(r"\[([A-Z]+)\]")


class LogsTab(ctk.CTkFrame):
    """Вкладка реал-тайм логов с подсветкой."""

    MAX_LINES = 2000  # максимум строк в буфере

    def __init__(self, parent: ctk.CTkFrame, manager: ZapretManager) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self.manager = manager
        self._build()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Заголовок + кнопки
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=m.padding_lg, pady=(m.padding_lg, m.padding_md))

        ctk.CTkLabel(
            header,
            text="Логи",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).pack(side="left")

        ctk.CTkButton(
            header, text="Очистить",
            width=90, height=30,
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, corner_radius=m.corner_radius_sm,
            command=self._clear,
        ).pack(side="right")

        ctk.CTkButton(
            header, text="Копировать всё",
            width=120, height=30,
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, corner_radius=m.corner_radius_sm,
            command=self._copy_all,
        ).pack(side="right", padx=(0, 8))

        ctk.CTkButton(
            header, text="Открыть файл лога",
            width=130, height=30,
            fg_color=p.bg_card, hover_color=p.bg_hover,
            text_color=p.text_secondary, corner_radius=m.corner_radius_sm,
            command=self._open_log_file,
        ).pack(side="right", padx=(0, 8))

        # Текстовое поле (обычный tk.Text — нужна цветовая разметка)
        text_frame = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        text_frame.grid(row=1, column=0, sticky="nsew", padx=m.padding_lg, pady=(0, m.padding_lg))
        text_frame.grid_columnconfigure(0, weight=1)
        text_frame.grid_rowconfigure(0, weight=1)

        self._text = Text(
            text_frame,
            bg=p.bg_card,
            fg=p.text_primary,
            font=(t.family_mono, t.size_sm),
            wrap="none",
            state="disabled",
            cursor="arrow",
            bd=0,
            highlightthickness=0,
            selectbackground=p.bg_hover,
            insertbackground=p.accent,
            padx=12,
            pady=8,
        )
        self._text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        # Скроллбар
        scrollbar = ctk.CTkScrollbar(text_frame, command=self._text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._text.configure(yscrollcommand=scrollbar.set)

        # Регистрируем теги подсветки
        self._register_tags()

        # Автоскролл
        self._autoscroll = True

    def _register_tags(self) -> None:
        """Зарегистрировать теги цветов для tk.Text."""
        for tag, color in theme.log_colors.items():
            self._text.tag_configure(tag, foreground=color)
        # Тег для меток времени
        self._text.tag_configure("TIMESTAMP", foreground=theme.palette.text_muted)

    def append_log(self, message: str) -> None:
        """
        Добавить строку лога. Потокобезопасно через after().
        Вызывается из фонового потока менеджера.
        """
        self.after(0, self._insert_line, message)

    def _insert_line(self, message: str) -> None:
        """Вставить строку с тегами цвета (только главный поток)."""
        self._text.configure(state="normal")

        # Лимит строк
        line_count = int(self._text.index("end-1c").split(".")[0])
        if line_count > self.MAX_LINES:
            self._text.delete("1.0", f"{line_count - self.MAX_LINES}.0")

        # Определяем тег
        tag = "DEFAULT"
        match = _TAG_RE.search(message)
        if match:
            tag = match.group(1)

        self._text.insert(END, message + "\n", tag)
        self._text.configure(state="disabled")

        if self._autoscroll:
            self._text.see(END)

    def _clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", END)
        self._text.configure(state="disabled")

    def _copy_all(self) -> None:
        """Скопировать весь текст логов в буфер обмена."""
        text = self._text.get("1.0", END)
        self.clipboard_clear()
        self.clipboard_append(text)

    def _open_log_file(self) -> None:
        """Открыть файл лога в Блокноте."""
        import subprocess
        from pathlib import Path
        log_file = Path(__file__).parent.parent / "logs" / "flowzap.log"
        if log_file.exists():
            subprocess.Popen(["notepad.exe", str(log_file)])
        else:
            import tkinter.messagebox as mb
            mb.showinfo("Лог", f"Файл не найден: {log_file}")
