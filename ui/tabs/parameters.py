"""
ui/tabs/parameters.py — Вкладка «Параметры» (PySide6): DNS-серверы и списки zapret.

Две колонки: список DNS-серверов (добавить / изменить / удалить / замерить;
встроенные — только замерить)
и списки сайтов zapret вкладками. Активный сервер — всегда
config["dns"]["pairs"][0]; выбирается он на главной (DashboardTab), здесь
только помечен — одно действие, одно место.
"""

import html
import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core.dns.builtin import builtin_names, is_builtin_dns
from ui.theme import theme
from ui.widgets.base import AutoHideLabel, Glyph, button, label, set_tone
from ui.widgets.controls import IconButton, SegmentedControl
from ui.widgets.layout import Page

log = logging.getLogger(__name__)

# Тот же валидатор, что был в customtkinter-версии для поля IPv4 (домен ИЛИ
# IP) — сохраняем поведение как есть, не сужаем до чистого IP-формата.
_RE_IP_OR_HOST = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$|^(\d{1,3}\.){3}\d{1,3}$"
)

# Сайты для проверки через DNS — заблокированные/ограниченные в РФ
_CHECK_DOMAINS = ["intel.com", "chat.openai.com", "claude.ai"]


def _build_dns_query(domain: str) -> bytes:
    """Собрать DNS A-запрос для домена. Перенесено без изменений."""
    buf = b"\x04\xd2\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for part in domain.encode().split(b"."):
        buf += bytes([len(part)]) + part
    buf += b"\x00\x00\x01\x00\x01"
    return buf


def _dns_query_ping(server: str, timeout: float = 3.0) -> str:
    """Замерить RTT DNS-запроса к серверу. Перенесено без изменений."""
    import socket
    import time

    total_ms = 0
    success = 0
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
            ancount = (data[6] << 8) | data[7] if len(data) > 7 else 0
            if ancount > 0:
                total_ms += ms
                success += 1
        except Exception:
            pass
    if success == 0:
        return "— (не отвечает)"
    return f"{total_ms // success} мс"


# ── Списки zapret (list-general-user.txt и т.д.) ────────────────────────

# (файл, можно ли добавлять, понятное название, пояснение)
LIST_FILES = [
    ("list-general-user.txt",  True,  "Обходить",
     "Сайты, к которым применяется обход — вдобавок к встроенным спискам."),
    ("list-exclude-user.txt",  True,  "Не трогать",
     "Сайты, которые обход не трогает: банки, госуслуги и всё, что ломается."),
    ("ipset-exclude-user.txt", True,  "IP-исключения",
     "IP-адреса и подсети (например 1.2.3.0/24), которые обход не трогает."),
    ("ipset-exclude.txt",      False, "Core",
     "Встроенный список IP-исключений zapret. Можно только удалять — новые добавляйте в «IP-исключения»."),
]

CONFLICT_PAIRS = [
    ("list-general-user.txt", "list-exclude-user.txt"),
]

# Отдельный, более строгий валидатор, чем _RE_IP_OR_HOST у DNS-полей —
# тут допускается CIDR (/24 и т.п.), что для DNS-адреса не имеет смысла.
_RE_LIST_DOMAIN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)
_RE_LIST_IP = re.compile(r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$")


def _valid_list_entry(value: str) -> bool:
    return bool(_RE_LIST_DOMAIN.match(value) or _RE_LIST_IP.match(value))


def _list_meta(filename: str) -> tuple:
    return next((m for m in LIST_FILES if m[0] == filename), (filename, True, filename, ""))


class _AddDnsDialog(QDialog):
    """Модальный диалог добавления DNS-сервера; с pair — изменение существующего."""

    def __init__(self, parent=None, pair: dict | None = None, reserved: set[str] = frozenset()):
        super().__init__(parent)
        self._reserved = reserved      # имена встроенных серверов (нижний регистр)
        self.setWindowTitle("Изменить DNS" if pair else "Добавить DNS")
        self.setMinimumWidth(420)
        self.result_pair: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(label("Изменить DNS-сервер" if pair else "Новый DNS-сервер", role="section"))
        layout.addWidget(label("Достаточно основного IPv4-адреса, остальные поля — по желанию.",
                               role="muted", wrap=True))

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._name = QLineEdit()
        self._name.setPlaceholderText("Например: Мой DNS")
        self._ipv4_main = QLineEdit()
        self._ipv4_main.setPlaceholderText("1.1.1.1")
        self._ipv4_backup = QLineEdit()
        self._ipv4_backup.setPlaceholderText("1.0.0.1")
        self._ipv6_main = QLineEdit()
        self._ipv6_backup = QLineEdit()
        form.addRow(label("Название", role="muted"), self._name)
        form.addRow(label("IPv4 основной", role="muted"), self._ipv4_main)
        form.addRow(label("IPv4 запасной", role="muted"), self._ipv4_backup)
        form.addRow(label("IPv6 основной", role="muted"), self._ipv6_main)
        form.addRow(label("IPv6 запасной", role="muted"), self._ipv6_backup)
        layout.addLayout(form)
        if pair:
            for field, key in ((self._name, "name"), (self._ipv4_main, "ipv4_main"),
                               (self._ipv4_backup, "ipv4_backup"), (self._ipv6_main, "ipv6_main"),
                               (self._ipv6_backup, "ipv6_backup")):
                field.setText(pair.get(key, ""))

        self._error = label("", role="hint", tone="error", wrap=True)
        self._error.setVisible(False)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox()
        save = buttons.addButton("Сохранить", QDialogButtonBox.AcceptRole)
        save.setProperty("variant", "primary")
        cancel = buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        for b in (save, cancel):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(110)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _fail(self, text: str) -> None:
        self._error.setText(text)
        self._error.setVisible(True)

    def _on_save(self) -> None:
        ipv4_m = self._ipv4_main.text().strip()
        ipv4_b = self._ipv4_backup.text().strip()
        ipv6_m = self._ipv6_main.text().strip()
        ipv6_b = self._ipv6_backup.text().strip()

        if not ipv4_m and not ipv6_m:
            self._fail("Укажите хотя бы один IP-адрес.")
            return
        if ipv4_m and not _RE_IP_OR_HOST.match(ipv4_m):
            self._fail(f"Некорректный IPv4: {ipv4_m}")
            return
        if ipv4_b and not _RE_IP_OR_HOST.match(ipv4_b):
            self._fail(f"Некорректный запасной IPv4: {ipv4_b}")
            return

        name = self._name.text().strip() or ipv4_m or ipv6_m
        if name.lower() in self._reserved:
            self._fail(f"Имя «{name}» занято встроенным сервером — выберите другое.")
            return
        self.result_pair = {
            "name": name,
            "ipv4_main": ipv4_m, "ipv4_backup": ipv4_b,
            "ipv6_main": ipv6_m, "ipv6_backup": ipv6_b,
        }
        self.accept()


def _section_card(title: str, subtitle: str) -> tuple[QFrame, QHBoxLayout, QVBoxLayout]:
    """Карточка колонки: заголовок + подзаголовок слева, действия справа, тело.
    Возвращает (карточка, раскладка действий, тело)."""
    card = QFrame()
    card.setObjectName("card")
    outer = QVBoxLayout(card)
    outer.setContentsMargins(22, 20, 22, 18)
    outer.setSpacing(14)
    head = QHBoxLayout()
    head.setSpacing(6)
    titles = QVBoxLayout()
    titles.setSpacing(2)
    titles.addWidget(label(title, role="section"))
    titles.addWidget(label(subtitle, role="muted", wrap=True))
    head.addLayout(titles, stretch=1)
    actions = QHBoxLayout()
    actions.setSpacing(6)
    head.addLayout(actions)
    outer.addLayout(head)
    body = QVBoxLayout()
    body.setSpacing(12)
    outer.addLayout(body, stretch=1)
    return card, actions, body


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


class _DnsPairRow(QFrame):
    """Строка DNS-сервера: имя (+ «активный»), адреса, время ответа, изменить/удалить.
    Встроенный сервер вместо кнопок помечен «встроенный» — он прописан в приложении.
    Выбор активного сервера — только на главной: одно действие, одно место."""

    editRequested = Signal()
    removeRequested = Signal()

    def __init__(self, pair: dict, active: bool, ping: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("listRow")
        self.setMinimumHeight(62)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 8, 0, 8)
        row.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name = pair.get("name") or pair.get("ipv4_main") or pair.get("ipv6_main") or "—"
        name_row.addWidget(label(name, role="strong"))
        if active:
            name_row.addWidget(label("активный", role="pill", tone="accent"))
        name_row.addStretch(1)
        col.addLayout(name_row)
        addrs = [a for a in (pair.get("ipv4_main"), pair.get("ipv4_backup"),
                             pair.get("ipv6_main"), pair.get("ipv6_backup")) if a]
        details = label(" · ".join(addrs) or "—", role="hint")
        details.setToolTip("\n".join(addrs))
        col.addWidget(details)
        row.addLayout(col, stretch=1)

        self.ping_label = label("", role="strong")
        self.ping_label.setFixedWidth(96)
        self.ping_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.ping_label)
        self.set_ping(ping)

        # Колонка действий одной ширины во всех строках — время ответа не скачет.
        holder = QWidget()
        holder.setFixedWidth(92)
        actions = QHBoxLayout(holder)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        actions.addStretch(1)
        if is_builtin_dns(pair):
            builtin = label("встроенный", role="pill")
            builtin.setToolTip("Прописан в FlowZap — удалить или изменить нельзя.\n"
                               "Список встроенных серверов обновляется автоматически.")
            actions.addWidget(builtin, alignment=Qt.AlignVCenter)
        else:
            edit = IconButton(Glyph.EDIT, "Изменить", size=36)
            edit.clicked.connect(self.editRequested.emit)
            actions.addWidget(edit)
            remove = IconButton(Glyph.DELETE, "Удалить", size=36, danger=True)
            remove.clicked.connect(self.removeRequested.emit)
            actions.addWidget(remove)
        row.addWidget(holder)

    def set_ping(self, text: str) -> None:
        """Время ответа цветом и словом: зелёное — быстро, жёлтое — медленнее, красное — нет ответа."""
        self.ping_label.setText(text)
        tone = None
        if "не отвечает" in text:
            tone, text = "error", "нет ответа"
        elif text.endswith("мс"):
            try:
                tone = "success" if int(text.split()[0]) <= 60 else "warning"
            except ValueError:
                pass
        elif text:
            tone = "muted"
        self.ping_label.setText(text)
        set_tone(self.ping_label, tone)


class _EntryRow(QFrame):
    """Строка записи списка: значение (совпадение с поиском выделено) и удалить."""

    removeRequested = Signal(str)

    def __init__(self, value: str, query: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("listRow")
        self.setFixedHeight(44)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(8)
        text = QLabel()
        text.setTextFormat(Qt.RichText)
        if query and query in value:
            i = value.index(query)
            accent = theme.palette.accent
            text.setText(f"{html.escape(value[:i])}<b style='color:{accent}'>{html.escape(query)}</b>"
                         f"{html.escape(value[i + len(query):])}")
        else:
            text.setText(html.escape(value))
        row.addWidget(text, stretch=1)
        remove = IconButton(Glyph.CLOSE, f"Удалить {value}", size=36, danger=True)
        remove.clicked.connect(lambda: self.removeRequested.emit(value))
        row.addWidget(remove)


class ParametersTab(QWidget):
    """Вкладка «Параметры»: список DNS-серверов и пользовательские списки zapret."""

    _pingUpdated = Signal(str, str)  # (ipv4_main, результат) — из фонового потока пинга
    MAX_SHOWN = 200                  # длиннее — показываем начало и просим уточнить поиск

    def __init__(self, parent=None, manager=None, config: dict = None,
                 save_config_fn=None, on_dns_changed=None):
        super().__init__(parent)
        self.manager = manager
        self._config = config or {}
        self._save_config_fn = save_config_fn
        self._on_dns_changed = on_dns_changed
        self._ping_pending = 0
        self._pairs: list[dict] = []
        self._ping_cache: dict[str, str] = {}
        self._rows: list[_DnsPairRow] = []
        self._list_file = LIST_FILES[0][0]

        # Path(x) всегда truthy (даже Path("")), поэтому `Path(...) or Path(...)`
        # никогда не уходил на запасной вариант — or применяем к строке ДО Path().
        _app_dir = Path(self._config.get("_app_dir") or Path(__file__).parent.parent)
        self._lists_dir: Path = _app_dir / "zapret" / "lists"

        self._pingUpdated.connect(self._on_ping_updated)

        self._build()
        self._load_dns_from_config()
        self._on_file_select(self._list_file)

    # ── Конфиг ──────────────────────────────────────

    def _load_dns_from_config(self) -> None:
        pairs_raw = self._config.get("dns", {}).get("pairs", [])
        self._pairs = []
        for entry in pairs_raw:
            if isinstance(entry, dict):
                self._pairs.append({
                    **({"builtin": entry["builtin"]} if entry.get("builtin") else {}),
                    "name":        entry.get("name", entry.get("main", "")),
                    "ipv4_main":   entry.get("ipv4_main", entry.get("main", "")),
                    "ipv4_backup": entry.get("ipv4_backup", entry.get("backup", "")),
                    "ipv6_main":   entry.get("ipv6_main", ""),
                    "ipv6_backup": entry.get("ipv6_backup", ""),
                })
        self._render_pairs()

    def reload_dns(self) -> None:
        """Перечитать пары из конфига — после того как их изменили снаружи
        (выбор на главной, обновление встроенных DNS в MainWindow)."""
        self._load_dns_from_config()

    def _save_dns(self) -> None:
        # "main"/"backup" — legacy-ключи, которые dashboard/core.dns.manager
        # читают как fallback, если ipv4_main/ipv4_backup вдруг нет.
        self._config.setdefault("dns", {})["pairs"] = [
            {**pair, "main": pair["ipv4_main"], "backup": pair["ipv4_backup"]}
            for pair in self._pairs
        ]
        if self._save_config_fn:
            self._save_config_fn()

    # ── UI ──────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        page = Page("Параметры", "DNS-серверы и сайты, которые обходить или не трогать")
        root.addWidget(page)
        columns = QHBoxLayout()
        columns.setSpacing(theme.metrics.padding_md)
        columns.addWidget(self._build_dns_block(), stretch=1)
        columns.addWidget(self._build_lists_block(), stretch=1)
        page.body.addLayout(columns)

    def _build_dns_block(self) -> QFrame:
        card, actions, body = _section_card(
            "DNS-серверы", "Здесь список, а активный сервер выбирается на главной")
        self._btn_ping = button("Замерить", variant="ghost")
        self._btn_ping.setToolTip("Замерить время ответа всех серверов")
        self._btn_ping.clicked.connect(self._on_ping_all)
        actions.addWidget(self._btn_ping, alignment=Qt.AlignTop)
        add_btn = button("Добавить", variant="primary")
        add_btn.clicked.connect(self._on_add_pair)
        actions.addWidget(add_btn, alignment=Qt.AlignTop)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(0)
        body.addLayout(self._rows_layout)

        self._status_lbl = AutoHideLabel()
        self._status_lbl.setProperty("role", "hint")
        self._status_lbl.setText("")
        body.addWidget(self._status_lbl)
        body.addStretch(1)
        body.addWidget(label("Время — средний ответ на запросы к заблокированным сайтам. "
                             "Чем меньше, тем лучше.", role="hint", wrap=True))
        return card

    def _render_pairs(self) -> None:
        _clear(self._rows_layout)
        self._rows = []

        if not self._pairs:
            empty = label("Нет серверов — нажмите «Добавить».", role="muted")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(56)
            self._rows_layout.addWidget(empty)
            return

        for i, pair in enumerate(self._pairs):
            ping = self._ping_cache.get(pair.get("ipv4_main", ""), "")
            row = _DnsPairRow(pair, active=(i == 0), ping=ping)
            row.editRequested.connect(lambda idx=i: self._on_edit_pair(idx))
            row.removeRequested.connect(lambda idx=i: self._on_remove(idx))
            self._rows_layout.addWidget(row)
            self._rows.append(row)

    def _set_status(self, text: str, tone: str | None = None) -> None:
        self._status_lbl.setText(text)
        set_tone(self._status_lbl, tone)

    # ── DNS: действия ───────────────────────────────

    def _on_remove(self, idx: int) -> None:
        if idx >= len(self._pairs) or is_builtin_dns(self._pairs[idx]):
            return
        name = self._pairs[idx].get("name") or self._pairs[idx].get("ipv4_main") or "этот сервер"
        reply = QMessageBox.question(self, "FlowZap — DNS", f"Удалить DNS «{name}»?")
        if reply != QMessageBox.Yes:
            return
        self._pairs.pop(idx)
        self._render_pairs()
        self._save_dns()
        self._set_status("✓ Удалено", "success")
        if idx == 0 and self._on_dns_changed:
            self._on_dns_changed()

    def _on_add_pair(self) -> None:
        dlg = _AddDnsDialog(self, reserved=builtin_names(self._config))
        if dlg.exec() != QDialog.Accepted or not dlg.result_pair:
            return

        new_ipv4 = dlg.result_pair["ipv4_main"]
        if new_ipv4 and any(p.get("ipv4_main") == new_ipv4 for p in self._pairs):
            QMessageBox.warning(self, "FlowZap — DNS", "Такой DNS уже есть.")
            return

        was_empty = not self._pairs
        self._pairs.append(dlg.result_pair)
        self._render_pairs()
        self._save_dns()
        self._set_status("✓ Добавлено", "success")
        if was_empty and self._on_dns_changed:
            self._on_dns_changed()   # первая пара сразу стала активной — обновить главную

    def _on_edit_pair(self, idx: int) -> None:
        if idx >= len(self._pairs) or is_builtin_dns(self._pairs[idx]):
            return
        dlg = _AddDnsDialog(self, pair=self._pairs[idx], reserved=builtin_names(self._config))
        if dlg.exec() != QDialog.Accepted or not dlg.result_pair:
            return
        self._pairs[idx] = dlg.result_pair
        self._render_pairs()
        self._save_dns()
        self._set_status("✓ Сохранено", "success")
        if idx == 0 and self._on_dns_changed:
            self._on_dns_changed()   # изменили активный — переприменить, если DNS включён

    def _on_ping_all(self) -> None:
        addrs = [p["ipv4_main"] for p in self._pairs if p.get("ipv4_main")]
        if not addrs:
            return
        self._set_status("Замеряю…", "accent")
        self._btn_ping.setEnabled(False)
        self._ping_pending = len(addrs)
        for row, pair in zip(self._rows, self._pairs):
            if pair.get("ipv4_main"):
                row.set_ping("…")
        for addr in addrs:
            threading.Thread(target=self._ping_worker, args=(addr,), daemon=True, name="dns-ping").start()

    def _ping_worker(self, addr: str) -> None:
        result = _dns_query_ping(addr)
        # Из фонового потока — только через Signal, как договорено для
        # всех колбэков, которые могут прилететь не из UI-потока.
        self._pingUpdated.emit(addr, result)

    def _on_ping_updated(self, addr: str, text: str) -> None:
        self._ping_cache[addr] = text
        for row, pair in zip(self._rows, self._pairs):
            if pair.get("ipv4_main") == addr:
                row.set_ping(text)
        self._ping_pending = max(0, self._ping_pending - 1)
        if self._ping_pending == 0:
            self._btn_ping.setEnabled(True)
            self._set_status("")

    # ──────────────────────────────────────────
    #  Списки zapret
    # ──────────────────────────────────────────

    def _build_lists_block(self) -> QFrame:
        card, actions, body = _section_card(
            "Списки сайтов", "Изменения применятся при следующем запуске обхода")
        open_folder_btn = button("Открыть папку", variant="ghost")
        open_folder_btn.clicked.connect(self._open_lists_folder)
        actions.addWidget(open_folder_btn, alignment=Qt.AlignTop)

        self._list_tabs = SegmentedControl([(f, t) for f, _, t, _ in LIST_FILES])
        self._list_tabs.changed.connect(self._on_file_select)
        body.addWidget(self._list_tabs)

        self._list_desc = label("", role="muted", wrap=True)
        body.addWidget(self._list_desc)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._list_entry = QLineEdit()
        self._list_entry.setClearButtonEnabled(True)
        self._list_entry.setMinimumHeight(44)
        self._list_entry.returnPressed.connect(self._add_entries)
        self._list_entry.textChanged.connect(self._on_list_text)
        input_row.addWidget(self._list_entry, stretch=1)
        self._btn_list_add = button("Добавить", variant="primary")
        self._btn_list_add.setMinimumHeight(44)
        self._btn_list_add.clicked.connect(self._add_entries)
        input_row.addWidget(self._btn_list_add)
        body.addLayout(input_row)

        status_row = QHBoxLayout()
        self._lists_status = label("", role="hint", wrap=True)
        status_row.addWidget(self._lists_status, stretch=1)
        self._lists_count = label("", role="hint")
        status_row.addWidget(self._lists_count, alignment=Qt.AlignTop)
        body.addLayout(status_row)

        self._entries_layout = QVBoxLayout()
        self._entries_layout.setSpacing(0)
        body.addLayout(self._entries_layout)
        body.addStretch(1)
        body.addWidget(label("Можно вставить ссылку целиком — домен выделится сам. Несколько — через запятую.",
                             role="hint", wrap=True))
        return card

    def _current_list_file(self) -> str:
        return self._list_file

    def _on_file_select(self, filename: str) -> None:
        if not filename:
            return
        self._list_file = filename
        self._list_tabs.set_current(filename)
        _, allow_add, _, desc = _list_meta(filename)
        self._btn_list_add.setVisible(allow_add)
        self._list_entry.setPlaceholderText(
            ("Найти или добавить: ссылка, домен или IP" if allow_add else "Поиск по списку"))
        self._list_desc.setText(desc)
        self._list_entry.clear()
        self._set_list_status("")
        self._render_entries()

    def _query(self) -> str:
        entries = self._parse_list_input()
        return entries[0] if len(entries) == 1 else ""

    def _render_entries(self) -> None:
        """Список записей текущего файла, отфильтрованный по тексту в поле:
        ввод сразу ищет, а «Добавить» добавляет то, чего ещё нет."""
        _clear(self._entries_layout)
        all_lines = self._read_list(self._list_file)
        query = self._query()
        _, allow_add, _, _ = _list_meta(self._list_file)
        found = [v for v in all_lines if query in v] if query else all_lines
        exact = bool(query) and query in all_lines
        self._btn_list_add.setEnabled(not exact)

        if self._list_entry.text().strip():
            if exact:
                self._set_list_status("Уже есть в этом списке")
            elif query and allow_add and not found:
                self._set_list_status(f"«{query}» нет в списке — нажмите «Добавить»")
            else:
                self._set_list_status("")

        self._lists_count.setText(f"Найдено {len(found)} из {len(all_lines)}" if query else f"Всего {len(all_lines)}")
        for value in found[:self.MAX_SHOWN]:
            row = _EntryRow(value, query)
            row.removeRequested.connect(self._remove_value)
            self._entries_layout.addWidget(row)
        if len(found) > self.MAX_SHOWN:
            self._entries_layout.addWidget(label(
                f"Показаны первые {self.MAX_SHOWN} — уточните поиск", role="hint"))
        if not found:
            empty = label("Ничего не найдено" if query else "Список пуст", role="muted")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(56)
            self._entries_layout.addWidget(empty)

    def _on_list_text(self, _text: str) -> None:
        self._set_list_status("")
        self._render_entries()

    def _open_lists_folder(self) -> None:
        self._lists_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(f'explorer "{self._lists_dir}"')
        except Exception as exc:
            log.error(f"Не удалось открыть папку списков: {exc}")
            QMessageBox.critical(self, "FlowZap", "Не удалось открыть папку")

    def _parse_list_input(self) -> list[str]:
        raw = self._list_entry.text().strip()
        parts = re.split(r"[,\n]+", raw)
        result = []
        for part in parts:
            v = part.strip()
            if not v:
                continue
            if "://" in v:
                # Вставили полную ссылку — берём только хост.
                host = urlparse(v).hostname
                if host:
                    v = host
            v = v.lower()
            if v.startswith("www."):
                v = v[4:]
            result.append(v.split("/")[0])
        return result

    def _read_list(self, filename: str) -> list[str]:
        path = self._lists_dir / filename
        if not path.exists():
            return []
        return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _write_list(self, filename: str, lines: list[str]) -> None:
        path = self._lists_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(".bak"))
        sorted_lines = sorted(set(lines), key=str.lower)
        path.write_text("\n".join(sorted_lines) + "\n", encoding="utf-8")

    def _set_list_status(self, text: str, tone: str | None = None) -> None:
        self._lists_status.setText(text)
        set_tone(self._lists_status, tone)

    def _add_entries(self) -> None:
        entries = self._parse_list_input()
        if not entries:
            self._set_list_status("Введите сайт или IP.", "warning")
            return

        filename = self._current_list_file()
        _, allow_add, _, _ = _list_meta(filename)
        if not allow_add:
            return

        invalid = [e for e in entries if not _valid_list_entry(e)]
        if invalid:
            self._set_list_status(f"Некорректный формат: {', '.join(invalid)} — нужен домен или IP.", "error")
            return

        current = self._read_list(filename)
        current_set = set(current)

        warnings = []
        for a, b in CONFLICT_PAIRS:
            other = b if filename == a else (a if filename == b else None)
            if other:
                conflicts = [v for v in entries if v in set(self._read_list(other))]
                if conflicts:
                    warnings.append(f"{', '.join(conflicts)} — уже есть в «{_list_meta(other)[2]}»")
        if warnings:
            reply = QMessageBox.question(
                self, "FlowZap — конфликт списков",
                "\n".join(warnings) + "\n\nВсё равно добавить?",
            )
            if reply != QMessageBox.Yes:
                return

        to_add = [v for v in entries if v not in current_set]
        duplicates = [v for v in entries if v in current_set]
        if to_add:
            self._write_list(filename, current + to_add)
        self._list_entry.blockSignals(True)
        self._list_entry.clear()
        self._list_entry.blockSignals(False)
        if to_add:
            text = f"✓ Добавлено: {', '.join(to_add)}"
            if duplicates:
                text += f" · уже были: {', '.join(duplicates)}"
            self._set_list_status(text, "success")
        else:
            self._set_list_status(f"Уже есть: {', '.join(duplicates)}", "warning")
        self._render_entries()

    def _remove_value(self, value: str) -> None:
        filename = self._current_list_file()
        current = self._read_list(filename)
        if value not in current:
            return
        self._write_list(filename, [l for l in current if l != value])
        self._set_list_status(f"✓ Удалено: {value}", "success")
        self._render_entries()
