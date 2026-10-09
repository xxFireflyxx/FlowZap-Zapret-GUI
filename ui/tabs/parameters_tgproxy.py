"""
ui/tabs/parameters_tgproxy.py
-----------------------------
Карточка «Telegram Proxy» во вкладке «Параметры»: состояние подключения
Telegram, кнопки «Подключить» / «Скопировать ссылку» и настройки сервера.

Слева — то, от чего зависит ссылка в Telegram (порт, секрет, доступ из
сети), справа под «Дополнительно» — всё остальное из настроек TG WS Proxy
(Cloudflare, дата-центры, производительность, лог). Настройки, которые у
exe автора относятся к его окну (язык, тема, автозапуск, проверка
обновлений, размер лога), здесь не нужны: этим занимается FlowZap.

Всё сохраняется сразу (config["tgproxy"]); работающий прокси
перезапускается — через DashboardTab.on_tg_settings_changed(), он же
владелец процесса и источник состояния (tg_connection_status, сигнал
tgStatusChanged).
"""

import logging
import subprocess

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QToolTip,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core.tgproxy import settings as tg_settings
from ui.tabs.dashboard import TG_DOT_COLORS
from ui.widgets.base import AutoHideLabel, button, divider, label, restyle, set_tone
from ui.widgets.controls import StatusDot, Switch
from ui.widgets.layout import SettingRow

log = logging.getLogger(__name__)

# Пауза перед перезапуском прокси: несколько переключений подряд — один перезапуск
_APPLY_DELAY_MS = 600


def _line_edit(width: int, placeholder: str = "", validator=None) -> QLineEdit:
    edit = QLineEdit()
    edit.setFixedWidth(width)
    edit.setMinimumHeight(36)
    edit.setPlaceholderText(placeholder)
    if validator is not None:
        edit.setValidator(validator)
    return edit


def _mask(secret: str) -> str:
    return f"{secret[:4]}••••••••{secret[-4:]}" if len(secret) >= 8 else "—"


class TgProxyCard(QFrame):
    """controller — DashboardTab: состояние, подключение, перезапуск."""

    def __init__(self, config: dict, save_config_fn, controller, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._config = config
        self._save_config_fn = save_config_fn
        self._ctl = controller
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(_APPLY_DELAY_MS)
        self._apply_timer.timeout.connect(self._ctl.on_tg_settings_changed)
        self._build()
        self._load()
        self._ctl.tgStatusChanged.connect(self._refresh_status)
        self._refresh_status()

    @property
    def _tg(self) -> dict:
        return self._config["tgproxy"]

    # ── UI ──────────────────────────────────────────

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(6)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Telegram Proxy", role="section"))
        titles.addWidget(label("Настройки применяются сразу — работающий прокси перезапустится сам",
                               role="muted", wrap=True))
        head.addLayout(titles, stretch=1)
        btn_log = button("Открыть лог", variant="ghost")
        btn_log.setToolTip("logs/tgproxy.log — что делает прокси")
        btn_log.clicked.connect(self._open_log)
        head.addWidget(btn_log, alignment=Qt.AlignTop)
        outer.addLayout(head)

        # Состояние Telegram и действия
        status = QHBoxLayout()
        status.setSpacing(10)
        self._dot = StatusDot(10)
        status.addWidget(self._dot, alignment=Qt.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self._state = label("", role="strong")
        texts.addWidget(self._state)
        self._detail = label("", role="hint", wrap=True)
        texts.addWidget(self._detail)
        status.addLayout(texts, stretch=1)
        self._btn_copy = button("Скопировать ссылку", variant="ghost")
        self._btn_copy.clicked.connect(self._copy_link)
        status.addWidget(self._btn_copy, alignment=Qt.AlignVCenter)
        self._btn_connect = button("Подключить в Telegram", variant="primary")
        self._btn_connect.setToolTip("Telegram спросит «Подключить» — подтвердите")
        self._btn_connect.clicked.connect(self._ctl.connect_telegram)
        status.addWidget(self._btn_connect, alignment=Qt.AlignVCenter)
        outer.addLayout(status)
        outer.addWidget(divider())

        columns = QHBoxLayout()
        columns.setSpacing(36)
        columns.addLayout(self._build_connection(), stretch=1)
        columns.addLayout(self._build_advanced(), stretch=1)
        outer.addLayout(columns)

        self._status = AutoHideLabel()
        self._status.setProperty("role", "hint")
        self._status.setWordWrap(True)
        self._status.setText("")
        outer.addWidget(self._status)

    @staticmethod
    def _column_head(caption: str, action: QWidget | None = None) -> QWidget:
        """Подпись колонки капсом; высота общая — подписи двух колонок на одной линии."""
        head = QWidget()
        head.setFixedHeight(30)
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(label(caption, role="caption"), alignment=Qt.AlignVCenter)
        row.addStretch(1)
        if action is not None:
            row.addWidget(action, alignment=Qt.AlignVCenter)
        return head

    def _build_connection(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addWidget(self._column_head("ПОДКЛЮЧЕНИЕ"))

        self._port = _line_edit(96, validator=QIntValidator(1, 65535, self))
        self._port.editingFinished.connect(self._apply_port)
        col.addWidget(SettingRow("Порт", "Telegram подключается к этому порту", self._port))
        col.addWidget(divider())

        secret_box = QWidget()
        secret_row = QHBoxLayout(secret_box)
        secret_row.setContentsMargins(0, 0, 0, 0)
        secret_row.setSpacing(10)
        self._secret = label("", role="muted")
        secret_row.addWidget(self._secret, alignment=Qt.AlignVCenter)
        btn_secret = button("Новый")
        btn_secret.setToolTip("Сгенерировать новый секрет")
        btn_secret.clicked.connect(self._new_secret)
        secret_row.addWidget(btn_secret, alignment=Qt.AlignVCenter)
        col.addWidget(SettingRow("Секрет", "Ключ доступа к прокси — как пароль", secret_box))
        col.addWidget(divider())

        self._sw_lan = Switch()
        self._sw_lan.clicked.connect(self._apply_lan)
        col.addWidget(SettingRow(
            "Прокси для телефона",
            "Телефон в той же Wi-Fi сети сможет пользоваться прокси этого компьютера: "
            "нажмите «Скопировать ссылку» и откройте её на телефоне. Windows может "
            "спросить разрешение — разрешите для частных сетей",
            self._sw_lan))
        col.addWidget(divider())

        self._sw_launch = Switch()
        self._sw_launch.clicked.connect(self._apply_launch)
        col.addWidget(SettingRow(
            "Запускать Telegram вместе с прокси",
            "Включили прокси на главной, а Telegram не запущен — FlowZap откроет его сам",
            self._sw_launch))
        col.addStretch(1)
        return col

    def _build_advanced(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        self._btn_more = button("Показать", variant="link")
        self._btn_more.clicked.connect(self._toggle_advanced)
        col.addWidget(self._column_head("ДОПОЛНИТЕЛЬНО", self._btn_more))
        self._more_hint = label("Cloudflare, дата-центры, производительность. Менять — только если "
                                "Telegram через прокси работает плохо", role="hint", wrap=True)
        col.addWidget(self._more_hint)

        self._advanced = QWidget()
        body = QVBoxLayout(self._advanced)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        self._sw_cf = Switch()
        self._sw_cf.clicked.connect(lambda: self._apply_flag("cfproxy", self._sw_cf))
        body.addWidget(SettingRow(
            "Запасной путь через Cloudflare",
            "Если дата-центр Telegram недоступен напрямую — подключаться через Cloudflare",
            self._sw_cf))
        body.addWidget(divider())

        self._cf_domains = _line_edit(230, "автоматически")
        self._cf_domains.editingFinished.connect(
            lambda: self._apply_domains("cfproxy_domains", self._cf_domains))
        self._row_cf_domains = SettingRow(
            "Свои домены Cloudflare", "Через запятую. Пусто — выбираются сами", self._cf_domains)
        body.addWidget(self._row_cf_domains)
        body.addWidget(divider())

        self._worker = _line_edit(230, "не используются")
        self._worker.editingFinished.connect(
            lambda: self._apply_domains("worker_domains", self._worker))
        body.addWidget(SettingRow(
            "Cloudflare Worker", "Свои Worker-домены (имя.аккаунт.workers.dev), через запятую",
            self._worker))
        body.addWidget(divider())

        self._sw_h2 = Switch()
        self._sw_h2.clicked.connect(lambda: self._apply_flag("h2", self._sw_h2))
        self._row_h2 = SettingRow(
            "Медиа одним соединением (HTTP/2)",
            "Фото и видео через Cloudflare — общим соединением. Нужны запасной путь и TLS",
            self._sw_h2)
        body.addWidget(self._row_h2)
        body.addWidget(divider())

        self._sw_insecure = Switch()
        self._sw_insecure.clicked.connect(lambda: self._apply_flag("no_secure", self._sw_insecure))
        body.addWidget(SettingRow(
            "Cloudflare без TLS",
            "Порт 80 вместо 443 для Cloudflare и Worker. Только если с TLS не работает",
            self._sw_insecure))
        body.addWidget(divider())

        self._dc = _line_edit(300, "без правил")
        self._dc.editingFinished.connect(self._apply_dc)
        body.addWidget(SettingRow(
            "IP дата-центров",
            "Номер дата-центра и его адрес, через запятую. Если фото и видео не грузятся, "
            "а запасной путь через Cloudflare включён, — попробуйте убрать "
            "2:149.154.167.220 (совет автора прокси)",
            self._dc))
        body.addWidget(divider())

        self._pool = _line_edit(72, validator=QIntValidator(0, 64, self))
        self._pool.editingFinished.connect(lambda: self._apply_int("pool_size", self._pool, 0, 64))
        body.addWidget(SettingRow(
            "Пул соединений", "Готовых соединений на дата-центр (4). 0 — без прямого пути", self._pool))
        body.addWidget(divider())

        self._buf = _line_edit(72, validator=QIntValidator(4, 4096, self))
        self._buf.editingFinished.connect(lambda: self._apply_int("buf_kb", self._buf, 4, 4096))
        body.addWidget(SettingRow("Буфер, КБ", "Буфер на соединение (256)", self._buf))
        body.addWidget(divider())

        self._sw_verbose = Switch()
        self._sw_verbose.clicked.connect(lambda: self._apply_flag("verbose", self._sw_verbose))
        body.addWidget(SettingRow("Подробный лог", "Для поиска неполадок", self._sw_verbose))
        body.addWidget(divider())

        btn_reset = button("Вернуть по умолчанию", variant="ghost")
        btn_reset.setToolTip("Порт, секрет и доступ из сети не меняются")
        btn_reset.clicked.connect(self._reset)
        body.addWidget(btn_reset, alignment=Qt.AlignLeft)

        col.addWidget(self._advanced)
        self._advanced.hide()
        col.addStretch(1)
        return col

    def _toggle_advanced(self) -> None:
        shown = not self._advanced.isVisible()
        self._advanced.setVisible(shown)
        self._more_hint.setVisible(not shown)
        self._btn_more.setText("Скрыть" if shown else "Показать")

    # ── Значения ────────────────────────────────────

    def _load(self) -> None:
        tg = self._tg
        self._port.setText(str(tg["port"]))
        self._secret.setText(_mask(tg["secret"]))
        self._sw_lan.setChecked(tg["lan"])
        self._sw_launch.setChecked(tg["launch_telegram"])
        self._sw_cf.setChecked(tg["cfproxy"])
        self._cf_domains.setText(", ".join(tg["cfproxy_domains"]))
        self._worker.setText(", ".join(tg["worker_domains"]))
        self._sw_h2.setChecked(tg["h2"])
        self._sw_insecure.setChecked(tg["no_secure"])
        self._dc.setText(", ".join(tg["dc_ip"]))
        self._pool.setText(str(tg["pool_size"]))
        self._buf.setText(str(tg["buf_kb"]))
        self._sw_verbose.setChecked(tg["verbose"])
        for edit in (self._cf_domains, self._worker, self._dc):
            edit.setCursorPosition(0)       # длинный список — видно начало, а не хвост
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        """Как у автора: свои домены — только с запасным путём, HTTP/2 — ещё и с TLS."""
        tg = self._tg
        self._row_cf_domains.setEnabled(tg["cfproxy"])
        self._row_h2.setEnabled(tg["cfproxy"] and not tg["no_secure"])

    def _saved(self, message: str = "✓ Сохранено") -> None:
        if self._save_config_fn:
            self._save_config_fn()
        if self._ctl.tg_proxy_running:
            message += " — прокси перезапускается"
        self._set_status(message, "success")
        self._apply_timer.start()

    def _set_status(self, text: str, tone: str | None = None) -> None:
        self._status.setText(text)
        set_tone(self._status, tone)

    def _field_error(self, edit: QLineEdit, text: str) -> None:
        """Ошибка — у самого поля: красная рамка и подсказка под ним. Строка
        статуса внизу карточки остаётся, но до неё часто не долистывают."""
        edit.setProperty("error", True)
        restyle(edit)
        QToolTip.showText(edit.mapToGlobal(QPoint(0, edit.height() + 2)), text, edit)
        self._set_status(text, "error")
        if not edit.property("_error_hooked"):
            edit.setProperty("_error_hooked", True)
            edit.textEdited.connect(lambda _t, e=edit: self._clear_field_error(e))

    @staticmethod
    def _clear_field_error(edit: QLineEdit) -> None:
        if edit.property("error"):
            edit.setProperty("error", False)
            restyle(edit)
            QToolTip.hideText()

    def _apply_port(self) -> None:
        text = self._port.text().strip()
        try:
            port = int(text)
        except ValueError:
            port = 0
        if not tg_settings.valid_port(port):
            self._field_error(self._port, "Порт — число от 1 до 65535")
            self._port.setText(str(self._tg["port"]))
            return
        if port == self._tg["port"]:
            return
        self._tg["port"] = port
        self._saved("✓ Порт изменён — подключите Telegram заново")

    def _new_secret(self) -> None:
        reply = QMessageBox.question(
            self, "FlowZap — TG Proxy",
            "Сгенерировать новый секрет?\n\nПрокси, добавленный в Telegram, перестанет "
            "работать — после смены подключите Telegram заново.")
        if reply != QMessageBox.Yes:
            return
        self._tg["secret"] = tg_settings.new_secret()
        self._secret.setText(_mask(self._tg["secret"]))
        self._saved("✓ Новый секрет — подключите Telegram заново")

    def _apply_lan(self) -> None:
        self._tg["lan"] = self._sw_lan.isChecked()
        self._saved("✓ Прокси для телефона включён — нажмите «Скопировать ссылку» и откройте её на телефоне"
                    if self._tg["lan"] else "✓ Прокси для телефона выключен")

    def _apply_launch(self) -> None:
        # На работу прокси не влияет — сохраняем без перезапуска
        self._tg["launch_telegram"] = self._sw_launch.isChecked()
        if self._save_config_fn:
            self._save_config_fn()
        self._set_status("✓ Сохранено", "success")

    def _apply_flag(self, key: str, switch: Switch) -> None:
        self._tg[key] = switch.isChecked()
        self._sync_enabled()
        self._saved()

    def _apply_domains(self, key: str, edit: QLineEdit) -> None:
        try:
            domains = tg_settings.parse_domains(edit.text())
        except ValueError as e:
            self._field_error(edit, str(e))
            return
        edit.setText(", ".join(domains))
        edit.setCursorPosition(0)
        if domains == self._tg[key]:
            return
        self._tg[key] = domains
        self._saved()

    def _apply_dc(self) -> None:
        try:
            entries = tg_settings.parse_dc_ip(self._dc.text())
        except ValueError as e:
            self._field_error(self._dc, str(e))
            return
        self._dc.setText(", ".join(entries))
        self._dc.setCursorPosition(0)
        if entries == self._tg["dc_ip"]:
            return
        self._tg["dc_ip"] = entries
        self._saved()

    def _apply_int(self, key: str, edit: QLineEdit, low: int, high: int) -> None:
        try:
            value = int(edit.text().strip())
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            self._field_error(edit, f"Нужно число от {low} до {high}")
            edit.setText(str(self._tg[key]))
            return
        if value == self._tg[key]:
            return
        self._tg[key] = value
        self._saved()

    def _reset(self) -> None:
        tg_settings.reset_advanced(self._tg)
        self._load()
        self._saved("✓ Дополнительные настройки — по умолчанию")

    # ── Действия ────────────────────────────────────

    def _refresh_status(self) -> None:
        kind, text, detail, tone = self._ctl.tg_connection_status()
        self._dot.set_color(TG_DOT_COLORS.get(kind, "text_muted"))
        self._state.setText(text)
        self._detail.setText(detail)
        set_tone(self._detail, tone)

    def _copy_link(self) -> None:
        QApplication.clipboard().setText(self._ctl.tg_manager.share_link())
        if self._tg["lan"]:
            self._set_status("✓ Ссылка скопирована — откройте её на телефоне в той же сети", "success")
        else:
            self._set_status("✓ Ссылка скопирована — она для Telegram на этом компьютере", "success")

    def _open_log(self) -> None:
        path = self._ctl.tg_manager.log_file
        try:
            if path.exists():
                subprocess.Popen(f'explorer /select,"{path}"')
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                subprocess.Popen(f'explorer "{path.parent}"')
        except Exception as exc:
            log.error(f"Не удалось открыть лог TG Proxy: {exc}")
