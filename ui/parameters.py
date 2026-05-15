"""
ui/parameters.py
----------------
Вкладка «Параметры». Редактор аргументов CLI для zapret.
Поддерживает режимы: nfqws, tpws, winws.
Также управление DNS-серверами (основной, запасные и т.д.).
"""

import re
import shutil
import subprocess
from pathlib import Path

import customtkinter as ctk
import tkinter.messagebox as mb

from ui.theme import theme
from core.manager import ZapretManager

CUSTOM_DNS_1 = ""
CUSTOM_DNS_2 = ""

LIST_FILES = [
    ("list-general-user.txt",  True),
    ("list-exclude-user.txt",  True),
    ("ipset-exclude-user.txt", True),
    ("ipset-exclude.txt",      False),
]

CONFLICT_PAIRS = [
    ("list-general-user.txt", "list-exclude-user.txt"),
]

_RE_DOMAIN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)
_RE_IP = re.compile(
    r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$"
)


def _valid_entry(value: str) -> bool:
    return bool(_RE_DOMAIN.match(value) or _RE_IP.match(value))



# Сайты для проверки через DNS — заблокированные или ограниченные в РФ
_CHECK_DOMAINS = [
    "intel.com",
    "chat.openai.com",
    "claude.ai",
]


def _build_dns_query(domain: str) -> bytes:
    """Собрать DNS A-запрос для домена."""
    import struct
    buf = b"\x04\xd2\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for part in domain.encode().split(b"."):
        buf += bytes([len(part)]) + part
    buf += b"\x00\x00\x01\x00\x01"
    return buf


def _dns_query_ping(server: str, timeout: float = 3.0) -> str:
    """
    Проверить DNS сервер двумя способами:
    1. Замерить RTT запроса (скорость сервера)
    2. Проверить резолвится ли заблокированный сайт через этот сервер

    Возвращает строку вида "15 мс ✓ youtube" или "— (нет ответа)".
    """
    import socket, time

    results = []
    total_ms = 0
    success_count = 0

    for domain in _CHECK_DOMAINS:
        query = _build_dns_query(domain)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            t0 = time.perf_counter()
            sock.sendto(query, (server, 53))
            data, _ = sock.recvfrom(512)
            ms = int((time.perf_counter() - t0) * 1000)
            sock.close()

            # Проверяем что ответ содержит IP (ANCOUNT > 0)
            # Bytes 6-7 в DNS ответе = ANCOUNT
            ancount = (data[6] << 8) | data[7] if len(data) > 7 else 0
            if ancount > 0:
                total_ms += ms
                success_count += 1
                results.append(domain.replace("www.", "").split(".")[0])
        except Exception:
            pass

    if success_count == 0:
        return "— (не отвечает)"

    avg_ms = total_ms // success_count
    return f"{avg_ms} мс"


class ParametersTab(ctk.CTkFrame):

    def __init__(self, parent: ctk.CTkFrame, manager: ZapretManager, config: dict = None, on_dns_changed=None) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self.manager = manager
        self._config = config or {}
        self._dns_entries: list = []
        self._lists_dir: Path = Path(__file__).parent.parent / "zapret" / "lists"
        self._live_search_after = None
        self._on_dns_changed = on_dns_changed  # сохраняем ДО _build()
        self._build()
        self._load_dns_from_config()

    def _load_dns_from_config(self) -> None:
        """Загрузить DNS пары из конфига в _dns_pairs и обновить UI."""
        dns_cfg = self._config.get("dns", {})
        pairs_raw = dns_cfg.get("pairs", [])
        self._dns_pairs = []

        if pairs_raw:
            # Новый формат: [{main="...", backup="..."}, ...]
            for entry in pairs_raw:
                if isinstance(entry, dict):
                    self._dns_pairs.append((
                        entry.get("main", ""),
                        entry.get("backup", ""),
                    ))
        else:
            # Fallback: старый плоский список — каждый адрес отдельной парой
            for addr in dns_cfg.get("servers", []):
                if addr:
                    self._dns_pairs.append((addr, ""))

        if not self._dns_pairs:
            self._dns_pairs = [("", "")]
        self._active_dns_idx = 0
        self._refresh_dns_ui()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Параметры",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg, pady=(m.padding_lg, m.padding_md))

        # ── DNS-серверы ───────────────────────
        self._dns_pairs = []       # list of (main, backup)
        self._active_dns_idx = 0
        self._dns_dropdown_open = False

        dns_card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        dns_card.grid(row=1, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))
        dns_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            dns_card,
            text="DNS-серверы",
            font=(t.family_ui, t.size_md, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_md, pady=(m.padding_md, 6))

        # ── row=1: строка с активным DNS (кликабельная) ───────────────────────
        self._dns_active_row = ctk.CTkFrame(
            dns_card, fg_color=p.bg_input,
            corner_radius=m.corner_radius_sm,
            cursor="hand2",
        )
        self._dns_active_row.grid(row=1, column=0, sticky="ew", padx=m.padding_md, pady=(0, 2))
        self._dns_active_row.grid_columnconfigure(1, weight=1)
        self._dns_active_row.bind("<Button-1>", lambda e: self._toggle_dns_dropdown())

        self._dns_active_label = ctk.CTkLabel(
            self._dns_active_row,
            text="—",
            font=(t.family_ui, t.size_sm),
            text_color=p.accent,
            anchor="w",
            cursor="hand2",
        )
        self._dns_active_label.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        self._dns_active_label.bind("<Button-1>", lambda e: self._toggle_dns_dropdown())

        self._dns_backup_label = ctk.CTkLabel(
            self._dns_active_row,
            text="",
            font=(t.family_ui, t.size_xs),
            text_color=p.text_muted,
            anchor="w",
            cursor="hand2",
        )
        self._dns_backup_label.grid(row=0, column=1, padx=0, pady=6, sticky="w")
        self._dns_backup_label.bind("<Button-1>", lambda e: self._toggle_dns_dropdown())

        # Стрелка ▼/▲ — сначала (как в пресетах)
        self._dns_arrow_lbl = ctk.CTkLabel(
            self._dns_active_row,
            text="▼",
            font=("Segoe UI", 10),
            text_color=p.accent,
            cursor="hand2",
            width=20,
        )
        self._dns_arrow_lbl.grid(row=0, column=2, padx=(0, 4))
        self._dns_arrow_lbl.bind("<Button-1>", lambda e: self._toggle_dns_dropdown())

        # Кнопка ↻ — после стрелки
        self._dns_refresh_btn = ctk.CTkButton(
            self._dns_active_row,
            text="↻",
            width=28, height=28,
            fg_color="transparent",
            hover_color=p.bg_hover,
            text_color=p.text_secondary,
            corner_radius=m.corner_radius_sm,
            font=(t.family_ui, t.size_md),
            command=self._ping_active_dns,
        )
        self._dns_refresh_btn.grid(row=0, column=3, padx=(0, 6))

        # _dns_popup — CTkToplevel, создаётся в _open_dns_popup()
        self._dns_popup = None

        # ── row=2: строки ввода ───────────────────────────────────────────────
        input_frame = ctk.CTkFrame(dns_card, fg_color="transparent")
        input_frame.grid(row=2, column=0, sticky="ew", padx=m.padding_md, pady=(8, 8))
        input_frame.grid_columnconfigure(0, weight=1)
        input_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            input_frame, text="Основной",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))

        ctk.CTkLabel(
            input_frame, text="Запасной",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 2))

        self._dns_main_entry = ctk.CTkEntry(
            input_frame,
            placeholder_text="1.1.1.1",
            fg_color=p.bg_input, text_color=p.text_primary,
            placeholder_text_color=p.text_muted,
            border_color=p.border, corner_radius=m.corner_radius_sm, height=32,
            font=(t.family_ui, t.size_sm),
        )
        self._dns_main_entry.grid(row=1, column=0, sticky="ew")

        self._dns_backup_entry = ctk.CTkEntry(
            input_frame,
            placeholder_text="1.0.0.1  (необязательно)",
            fg_color=p.bg_input, text_color=p.text_primary,
            placeholder_text_color=p.text_muted,
            border_color=p.border, corner_radius=m.corner_radius_sm, height=32,
            font=(t.family_ui, t.size_sm),
        )
        self._dns_backup_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0))

        # ── row=4: кнопка добавить ────────────────────────────────────────────
        ctk.CTkButton(
            dns_card,
            text="＋  Добавить",
            height=m.button_height,
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color=p.bg_root,
            corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_sm),
            command=self._add_dns_pair,
        ).grid(row=3, column=0, sticky="w", padx=m.padding_md, pady=(0, m.padding_md))

        self._dns_status = ctk.CTkLabel(
            dns_card, text="",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
            anchor="w",
        )
        self._dns_status.grid(row=4, column=0, sticky="ew", padx=m.padding_md, pady=(0, m.padding_md))

        self._mode_var = ctk.StringVar(value="winws")

        # ── Списки zapret ─────────────────────
        self._build_lists_block()

        # Кнопка "Применить" убрана — сохранение происходит автоматически:
        # DNS — при добавлении, удалении и смене активного
        # Списки — при нажатии "Добавить" и "Удалить"

    # ──────────────────────────────────────────
    #  Блок управления списками
    # ──────────────────────────────────────────

    def _build_lists_block(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=2, column=0, sticky="ew", padx=m.padding_lg, pady=(0, m.padding_md))
        card.grid_columnconfigure(0, weight=1)

        # ── Строка: заголовок + выбор файла + кнопка папки ──
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.grid(row=0, column=0, sticky="ew", padx=m.padding_md, pady=(m.padding_md, 8))
        top_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top_row, text="Списки zapret",
            font=(t.family_ui, t.size_md, "bold"), text_color=p.text_primary,
            width=100,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._list_file_var = ctk.StringVar(value=LIST_FILES[0][0])
        self._list_file_popup = None

        # Кастомный дропдаун — как DNS и пресеты
        self._list_file_btn_frame = ctk.CTkFrame(
            top_row, fg_color=p.bg_input,
            corner_radius=m.corner_radius_sm, cursor="hand2",
        )
        self._list_file_btn_frame.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._list_file_btn_frame.grid_columnconfigure(0, weight=1)
        self._list_file_btn_frame.bind("<Button-1>", lambda e: self._toggle_list_file_popup())

        self._list_file_lbl = ctk.CTkLabel(
            self._list_file_btn_frame,
            text=LIST_FILES[0][0],
            font=(t.family_ui, t.size_sm),
            text_color=p.text_primary,
            anchor="w", cursor="hand2",
        )
        self._list_file_lbl.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        self._list_file_lbl.bind("<Button-1>", lambda e: self._toggle_list_file_popup())

        self._list_file_arrow = ctk.CTkLabel(
            self._list_file_btn_frame,
            text="▼", width=20,
            font=("Segoe UI", 10),
            text_color=p.accent, cursor="hand2",
        )
        self._list_file_arrow.grid(row=0, column=1, padx=(0, 8))
        self._list_file_arrow.bind("<Button-1>", lambda e: self._toggle_list_file_popup())

        ctk.CTkButton(
            top_row, text="📂  Списки",
            height=m.button_height,
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_secondary,
            corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_sm),
            command=self._open_lists_folder,
        ).grid(row=0, column=2, sticky="e")

        self._readonly_label = ctk.CTkLabel(
            top_row, text="",
            font=(t.family_ui, t.size_xs), text_color=p.warning,
        )
        self._readonly_label.grid(row=0, column=3, padx=(8, 0))

        # ── Поле ввода ────────────────────────
        input_row = ctk.CTkFrame(card, fg_color="transparent")
        input_row.grid(row=1, column=0, sticky="ew", padx=m.padding_md, pady=(0, 6))
        input_row.grid_columnconfigure(0, weight=1)

        self._list_entry = ctk.CTkEntry(
            input_row,
            placeholder_text="youtube.com, 1.2.3.4 — через запятую",
            fg_color=p.bg_input, text_color=p.text_primary,
            placeholder_text_color=p.text_muted,
            border_color=p.border, corner_radius=m.corner_radius_sm,
            height=34,
        )
        self._list_entry.grid(row=0, column=0, sticky="ew")
        self._list_entry.bind("<Return>", lambda e: self._add_entries())
        self._list_entry.bind("<KeyRelease>", self._on_key_release)

        # ── Кнопки действий ───────────────────
        actions_row = ctk.CTkFrame(card, fg_color="transparent")
        actions_row.grid(row=2, column=0, sticky="w", padx=m.padding_md, pady=(0, 0))

        self._btn_add = ctk.CTkButton(
            actions_row, text="＋  Добавить",
            height=m.button_height,
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color=p.bg_root, corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_sm),
            command=self._add_entries,
        )
        self._btn_add.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            actions_row, text="－  Удалить",
            height=m.button_height,
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.error,
            corner_radius=m.corner_radius,
            font=(t.family_ui, t.size_sm),
            command=self._remove_entries,
        ).pack(side="left")

        # ── Статус (живой поиск + результаты) ─
        self._lists_status = ctk.CTkLabel(
            card, text="",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
            anchor="w", wraplength=560,
        )
        self._lists_status.grid(row=3, column=0, sticky="ew",
                                padx=m.padding_md, pady=(4, m.padding_md))

        self._on_file_select(self._list_file_var.get())

    # ──────────────────────────────────────────
    #  Живой поиск
    # ──────────────────────────────────────────

    def _on_key_release(self, event=None) -> None:
        # Отменить предыдущий отложенный вызов
        if self._live_search_after:
            self.after_cancel(self._live_search_after)
        # Запустить поиск через 300мс после последнего нажатия
        self._live_search_after = self.after(300, self._live_search)

    def _live_search(self) -> None:
        p = theme.palette
        raw = self._list_entry.get().strip()
        if not raw:
            self._lists_status.configure(text="", text_color=p.text_muted)
            return

        parts = re.split(r"[,\n]+", raw)
        entries = [p.strip().lower() for p in parts if p.strip()]
        if not entries:
            return

        results = []
        for filename, _ in LIST_FILES:
            lines = set(self._read_list(filename))
            found = [v for v in entries if v in lines]
            if found:
                results.append(f"«{filename}»")

        if results:
            self._set_status(
                f"Уже есть в: {', '.join(results)}", p.warning
            )
        else:
            self._set_status("Не найдено ни в одном файле", p.text_muted)

    # ──────────────────────────────────────────
    #  Логика списков
    # ──────────────────────────────────────────

    def _toggle_list_file_popup(self) -> None:
        if self._list_file_popup and self._list_file_popup.winfo_exists():
            self._close_list_file_popup()
        else:
            self._open_list_file_popup()

    def _open_list_file_popup(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        files = [f for f, _ in LIST_FILES]

        self._list_file_btn_frame.update_idletasks()
        x = self._list_file_btn_frame.winfo_rootx()
        y = self._list_file_btn_frame.winfo_rooty() + self._list_file_btn_frame.winfo_height() + 2
        w = self._list_file_btn_frame.winfo_width()

        row_h = 36
        popup_h = len(files) * row_h + 12

        popup = ctk.CTkToplevel(self)
        popup.wm_overrideredirect(True)
        popup.geometry(f"{w}x{popup_h}+{x}+{y}")
        popup.configure(fg_color=p.bg_card)
        popup.lift()
        popup.focus_force()
        popup.bind("<FocusOut>", lambda e: self.after(100, self._close_list_file_popup))

        frame = ctk.CTkFrame(popup, fg_color=p.bg_card, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=2, pady=4)
        frame.grid_columnconfigure(0, weight=1)

        current = self._list_file_var.get()
        for i, filename in enumerate(files):
            is_sel = filename == current
            row = ctk.CTkFrame(frame,
                fg_color=p.bg_hover if is_sel else "transparent",
                corner_radius=m.corner_radius_sm, cursor="hand2")
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            row.grid_columnconfigure(0, weight=1)

            lbl = ctk.CTkLabel(row, text=filename,
                font=(t.family_ui, t.size_sm),
                text_color=p.accent if is_sel else p.text_primary,
                anchor="w", cursor="hand2")
            lbl.grid(row=0, column=0, sticky="ew", padx=10, pady=6)

            def _pick(fn=filename):
                self._list_file_var.set(fn)
                self._list_file_lbl.configure(text=fn)
                self._on_file_select(fn)
                self._close_list_file_popup()

            for w_ in (row, lbl):
                w_.bind("<Button-1>", lambda e, fn=_pick: fn())
                w_.bind("<Enter>", lambda e, r_=row: r_.configure(fg_color=p.bg_hover))
                w_.bind("<Leave>", lambda e, r_=row, fn=filename:
                    r_.configure(fg_color=p.bg_hover if fn == self._list_file_var.get() else "transparent"))

        self._list_file_popup = popup
        self._list_file_arrow.configure(text="▲")

    def _close_list_file_popup(self) -> None:
        if self._list_file_popup and self._list_file_popup.winfo_exists():
            try:
                self._list_file_popup.destroy()
            except Exception:
                pass
        self._list_file_popup = None
        try:
            self._list_file_arrow.configure(text="▼")
        except Exception:
            pass

    def _on_file_select(self, filename: str) -> None:
        # Файлы из LIST_FILES имеют явный флаг редактируемости
        # Для всех остальных — разрешаем редактирование
        allow_add = next((a for f, a in LIST_FILES if f == filename), True)
        if not allow_add:
            self._btn_add.configure(state="disabled")
            self._readonly_label.configure(text="⚠ только удаление")
        else:
            self._btn_add.configure(state="normal")
            self._readonly_label.configure(text="")
        self._lists_status.configure(text="")

    def _open_lists_folder(self) -> None:
        self._lists_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(f'explorer "{self._lists_dir}"')
        except Exception as e:
            mb.showerror("FlowZap", f"Не удалось открыть папку:\n{e}")

    def _parse_input(self) -> list:
        raw = self._list_entry.get().strip()
        parts = re.split(r"[,\n]+", raw)
        return [p.strip().lower() for p in parts if p.strip()]

    def _validate_entries(self, entries: list) -> tuple:
        valid, invalid = [], []
        for e in entries:
            (valid if _valid_entry(e) else invalid).append(e)
        return valid, invalid

    def _read_list(self, filename: str) -> list:
        path = self._lists_dir / filename
        if not path.exists():
            return []
        return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _write_list(self, filename: str, lines: list) -> None:
        path = self._lists_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(".bak"))
        sorted_lines = sorted(set(lines), key=str.lower)
        path.write_text("\n".join(sorted_lines) + "\n", encoding="utf-8")

    def _set_status(self, text: str, color: str = None) -> None:
        self._lists_status.configure(
            text=text,
            text_color=color or theme.palette.text_muted,
        )

    def _add_entries(self) -> None:
        p = theme.palette
        entries = self._parse_input()
        if not entries:
            self._set_status("Введите хотя бы одно значение.", p.warning)
            return

        valid, invalid = self._validate_entries(entries)
        if invalid:
            self._set_status(
                f"Некорректный формат: {', '.join(invalid)}  —  ожидается домен или IP.",
                p.error,
            )
            return

        filename = self._list_file_var.get()
        allow_add = next((a for f, a in LIST_FILES if f == filename), True)
        if not allow_add:
            self._set_status("Этот файл доступен только для удаления.", p.warning)
            return

        current = self._read_list(filename)
        current_set = set(current)

        warnings = []
        for a, b in CONFLICT_PAIRS:
            other = b if filename == a else (a if filename == b else None)
            if other:
                conflicts = [v for v in valid if v in set(self._read_list(other))]
                if conflicts:
                    warnings.append(f"⚠ {', '.join(conflicts)} — уже есть в «{other}»")

        if warnings:
            if not mb.askyesno("FlowZap — конфликт списков",
                               "\n".join(warnings) + "\n\nВсё равно добавить?"):
                return

        duplicates = [v for v in valid if v in current_set]
        to_add = [v for v in valid if v not in current_set]

        if to_add:
            self._write_list(filename, current + to_add)

        parts = []
        if to_add:
            parts.append(f"✓ Добавлено: {', '.join(to_add)}")
        if duplicates:
            parts.append(f"уже есть: {', '.join(duplicates)}")

        self._set_status("  |  ".join(parts), p.success if to_add else p.warning)
        self._list_entry.delete(0, "end")

    def _remove_entries(self) -> None:
        p = theme.palette
        entries = self._parse_input()
        if not entries:
            self._set_status("Введите значения для удаления.", p.warning)
            return

        filename = self._list_file_var.get()
        current = self._read_list(filename)
        current_set = set(current)

        found = [v for v in entries if v in current_set]
        not_found = [v for v in entries if v not in current_set]

        if not found:
            self._set_status(
                f"Не найдено в «{filename}»: {', '.join(not_found)}", p.warning)
            return

        self._write_list(filename, [l for l in current if l not in set(found)])

        parts = [f"✓ Удалено: {', '.join(found)}"]
        if not_found:
            parts.append(f"не найдено: {', '.join(not_found)}")
        self._set_status("  |  ".join(parts), p.success)
        self._list_entry.delete(0, "end")

    # ──────────────────────────────────────────
    #  DNS: новый блок
    # ──────────────────────────────────────────

    def _toggle_dns_dropdown(self) -> None:
        if self._dns_popup and self._dns_popup.winfo_exists():
            self._close_dns_popup()
        else:
            self._open_dns_popup()

    def _open_dns_popup(self) -> None:
        if not self._dns_pairs:
            return
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self._dns_active_row.update_idletasks()
        x = self._dns_active_row.winfo_rootx()
        y = self._dns_active_row.winfo_rooty() + self._dns_active_row.winfo_height() + 2
        w = self._dns_active_row.winfo_width()
        row_h = 36
        popup_h = min(len(self._dns_pairs) * row_h + 8, 260)

        popup = ctk.CTkToplevel(self)
        popup.wm_overrideredirect(True)
        popup.geometry(f"{w}x{popup_h}+{x}+{y}")
        popup.configure(fg_color=p.bg_card)
        popup.lift()
        popup.focus_force()
        popup.bind("<FocusOut>", lambda e: self.after(100, self._close_dns_popup))

        old_pings = getattr(self, "_dns_ping_cache", {})
        self._dns_ping_labels = {}

        scroll = ctk.CTkScrollableFrame(
            popup, fg_color=p.bg_card,
            scrollbar_button_color=p.border,
            corner_radius=0,
        )
        scroll.pack(fill="both", expand=True)
        scroll.grid_columnconfigure(0, weight=1)

        for i, (main, backup) in enumerate(self._dns_pairs):
            is_active = (i == 0)

            row = ctk.CTkFrame(scroll, fg_color=p.bg_hover if is_active else "transparent",
                               corner_radius=m.corner_radius_sm, cursor="hand2")
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=(4 if i == 0 else 2))
            row.grid_columnconfigure(0, weight=1)

            # Адрес
            addr_text = main + (f"  +  {backup}" if backup else "")
            addr_lbl = ctk.CTkLabel(
                row, text=addr_text,
                font=(t.family_ui, t.size_sm),
                text_color=p.accent if is_active else p.text_primary,
                anchor="w", cursor="hand2",
            )
            addr_lbl.grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=4)

            # Пинг + крестик
            right = ctk.CTkFrame(row, fg_color="transparent")
            right.grid(row=0, column=1, padx=(4, 4))

            ping_val = old_pings.get(main, "—")
            ping_lbl = ctk.CTkLabel(
                right, text=ping_val,
                font=(t.family_ui, t.size_xs),
                text_color=p.text_muted,
                width=44, anchor="e",
            )
            ping_lbl.pack(side="left", padx=(0, 4))
            self._dns_ping_labels[main] = ping_lbl

            ctk.CTkButton(
                right, text="✕",
                width=26, height=26,
                fg_color="transparent",
                hover_color=p.bg_hover,
                text_color=p.error,
                corner_radius=m.corner_radius_sm,
                font=(t.family_ui, t.size_sm),
                command=lambda idx=i: self._remove_dns_pair(idx),
            ).pack(side="left")

            # Hover-эффект и клик для выбора
            def _bind(r=row, n=main, b=backup, idx=i, a=is_active):
                def on_enter(e): r.configure(fg_color=p.bg_hover)
                def on_leave(e): r.configure(fg_color=p.bg_hover if a else "transparent")
                def on_click(e):
                    if not a:
                        self._set_active_dns(idx)
                        self._close_dns_popup()
                for w_ in r.winfo_children() + [r]:
                    try:
                        w_.bind("<Enter>", on_enter)
                        w_.bind("<Leave>", on_leave)
                        if not a:
                            w_.bind("<Button-1>", on_click)
                    except Exception:
                        pass
            _bind()

        self._dns_popup = popup
        self._dns_arrow_lbl.configure(text="▲")

    def _close_dns_popup(self) -> None:
        if self._dns_popup and self._dns_popup.winfo_exists():
            try:
                self._dns_popup.destroy()
            except Exception:
                pass
        self._dns_popup = None
        try:
            self._dns_arrow_lbl.configure(text="▼")
        except Exception:
            pass

    def _refresh_dns_ui(self) -> None:
        """Обновить строку активного DNS и закрыть попап если открыт."""
        # Закрыть попап — он устарел после изменения списка
        self._close_dns_popup()

        if self._dns_pairs:
            main, backup = self._dns_pairs[0]
            self._dns_active_label.configure(text=main or "—")
            self._dns_backup_label.configure(text=f"+ {backup}" if backup else "")
        else:
            self._dns_active_label.configure(text="—")
            self._dns_backup_label.configure(text="")

    def _set_active_dns(self, idx: int) -> None:
        """Переместить выбранный DNS на первое место."""
        if idx <= 0 or idx >= len(self._dns_pairs):
            return
        self._dns_pairs.insert(0, self._dns_pairs.pop(idx))
        self._active_dns_idx = 0
        self._refresh_dns_ui()
        self._save_dns()
        self._dns_status.configure(text="✓ Активный DNS изменён", text_color=theme.palette.success)
        self.after(2000, lambda: self._dns_status.configure(text=""))
        # Уведомляем dashboard о смене DNS
        if self._on_dns_changed:
            self._on_dns_changed()

    def _remove_dns_pair(self, idx: int) -> None:
        p = theme.palette
        if len(self._dns_pairs) <= 1:
            self._dns_status.configure(text="Нельзя удалить единственный DNS", text_color=p.warning)
            self.after(2000, lambda: self._dns_status.configure(text=""))
            return
        self._dns_pairs.pop(idx)
        self._refresh_dns_ui()
        self._save_dns()
        self._dns_status.configure(text="✓ Удалено", text_color=p.success)
        self.after(2000, lambda: self._dns_status.configure(text=""))

    def _add_dns_pair(self) -> None:
        p = theme.palette
        main = self._dns_main_entry.get().strip()
        backup = self._dns_backup_entry.get().strip()

        if not main:
            self._dns_status.configure(text="Введите основной адрес", text_color=p.warning)
            return

        if not _valid_entry(main):
            self._dns_status.configure(text=f"Некорректный адрес: {main}", text_color=p.error)
            return

        if backup and not _valid_entry(backup):
            self._dns_status.configure(text=f"Некорректный запасной адрес: {backup}", text_color=p.error)
            return

        if any(d[0] == main for d in self._dns_pairs):
            self._dns_status.configure(text="Такой DNS уже есть в списке", text_color=p.warning)
            return

        self._dns_pairs.append((main, backup))
        self._refresh_dns_ui()
        self._save_dns()
        self._dns_main_entry.delete(0, "end")
        self._dns_backup_entry.delete(0, "end")
        self._dns_status.configure(text="✓ Добавлено, пингуем...", text_color=p.success)
        self._ping_dns_pair(len(self._dns_pairs) - 1)

    def _ping_active_dns(self) -> None:
        """Пинговать все DNS пары."""
        self._ping_all_dns()

    def _ping_all_dns(self) -> None:
        """Запустить DNS-проверку для всех пар параллельно."""
        import threading
        if not hasattr(self, "_dns_ping_cache"):
            self._dns_ping_cache = {}

        pairs_to_ping = [
            (i, main) for i, (main, _) in enumerate(self._dns_pairs) if main
        ]
        if not pairs_to_ping:
            return

        # Показываем "…" у всех
        for _, main in pairs_to_ping:
            self._dns_ping_cache[main] = "…"
            lbl = getattr(self, "_dns_ping_labels", {}).get(main)
            if lbl:
                self.after(0, lambda l=lbl: l.configure(text="…"))

        total = len(pairs_to_ping)
        results_done = [0]

        def _worker(main: str) -> None:
            display = _dns_query_ping(main)

            # Обновляем метку и кеш
            self._dns_ping_cache[main] = display
            lbl2 = getattr(self, "_dns_ping_labels", {}).get(main)
            if lbl2:
                self.after(0, lambda d=display, l=lbl2: l.configure(text=d))

            results_done[0] += 1
            # Когда все готовы — показываем итоговый статус
            if results_done[0] >= total:
                ok = [(m, v) for m, v in self._dns_ping_cache.items()
                      if v not in ("—", "…", "?") and "мс" in v]
                if ok:
                    summary = ", ".join(f"{m}: {v}" for m, v in ok[:2])
                    self.after(0, lambda s=summary: self._dns_status.configure(
                        text=f"✓ {s}", text_color=theme.palette.success))
                else:
                    self.after(0, lambda: self._dns_status.configure(
                        text="✗ DNS серверы не отвечают", text_color=theme.palette.error))
                self.after(3000, lambda: self._dns_status.configure(text=""))

        for _, main in pairs_to_ping:
            threading.Thread(target=_worker, args=(main,), daemon=True).start()

    def _ping_dns_pair(self, idx: int) -> None:
        """Пинговать одну DNS пару по индексу."""
        import threading
        if idx >= len(self._dns_pairs):
            return
        main, _ = self._dns_pairs[idx]
        if not main:
            return

        if not hasattr(self, "_dns_ping_cache"):
            self._dns_ping_cache = {}
        self._dns_ping_cache[main] = "…"
        lbl = getattr(self, "_dns_ping_labels", {}).get(main)
        if lbl:
            self.after(0, lambda: lbl.configure(text="…"))

        def _worker():
            display = _dns_query_ping(main)
            self._dns_ping_cache[main] = display
            lbl2 = getattr(self, "_dns_ping_labels", {}).get(main)
            if lbl2:
                self.after(0, lambda d=display: lbl2.configure(text=d))
            color = theme.palette.success if "мс" in display else theme.palette.error
            self.after(0, lambda d=display: self._dns_status.configure(
                text=f"Пинг {main}: {d}", text_color=color))
            self.after(3000, lambda: self._dns_status.configure(text=""))

        threading.Thread(target=_worker, daemon=True).start()

    def get_dns_servers(self) -> list:
        """Вернуть плоский список основных DNS адресов (для dashboard/netsh)."""
        return [main for main, backup in self._dns_pairs if main]

    def _get_dns_pairs_data(self) -> list:
        """Вернуть пары в формате для config.toml: [{main=..., backup=...}, ...]"""
        result = []
        for main, backup in self._dns_pairs:
            if main:
                entry = {"main": main}
                if backup:
                    entry["backup"] = backup
                result.append(entry)
        return result

    def _build_flat_servers(self) -> list:
        """Плоский список — ТОЛЬКО активная пара (первая в списке).
        dashboard берёт [0] как основной и [1] как запасной.
        """
        flat = []
        if self._dns_pairs:
            main, backup = self._dns_pairs[0]
            if main:
                flat.append(main)
            if backup:
                flat.append(backup)
        return flat

    def _save_dns(self) -> None:
        """Сохранить DNS в конфиг и на диск без уведомления."""
        if "dns" not in self._config:
            self._config["dns"] = {}
        self._config["dns"]["pairs"] = self._get_dns_pairs_data()
        # servers — только активная пара, dashboard берёт [0] и [1]
        self._config["dns"]["servers"] = self._build_flat_servers()
        root_win = self.winfo_toplevel()
        if hasattr(root_win, "save_config"):
            root_win.save_config()

    def _apply(self) -> None:
        if "dns" not in self._config:
            self._config["dns"] = {}
        self._config["dns"]["pairs"] = self._get_dns_pairs_data()
        self._config["dns"]["servers"] = self._build_flat_servers()

        root_win = self.winfo_toplevel()
        if hasattr(root_win, "save_config"):
            root_win.save_config()

        self._dns_status.configure(text="✓ Сохранено", text_color=theme.palette.success)
        self.after(2000, lambda: self._dns_status.configure(text=""))
