"""
ui/tabs/settings.py
------------------
Вкладка «Настройки»: две колонки — запуск и окно, оформление | фоновая
служба, прочее (ярлык, логи), о программе.
Всё сохраняется сразу — кнопки «Сохранить» нет.

Логика автозапуска Windows (реестр пользователя) — core/system/autostart.py, ярлыка — core/system/shortcut.py: они сами
создают потоки и зовут on_done из них, вкладка оборачивает колбэки в Signal.

Тема применяется сразу (on_theme_changed → MainWindow.apply_theme) и
сохраняется в ui.theme; ui.tray_enabled применяется сразу через on_tray_changed.
ui.restore_state читает DashboardTab: при запуске включает DNS и TG Proxy,
если они были включены (config["state"], пишется при каждом переключении).

TODO: карточка «Подсветка» (ui.bar_style: default / rainbow / candy / none) —
      вернуть, когда в Dashboard появится анимированная полоса статуса.
"""

import logging
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.theme import theme, THEMES, THEME_NAMES
from ui.widgets.base import button, divider, label, set_tone
from ui.widgets.controls import Switch
from ui.widgets.layout import Page, SettingRow
from core.updates.releases import FLOWZAP_REPO
from core.version import GUI_VERSION
from core.system.autostart import autostart_enabled_async, set_autostart_async
from core.system.shortcut import create_desktop_shortcut_async
from core.service import client as service_client

log = logging.getLogger(__name__)

_STATUS_MS = 4000     # сколько висит статус автозапуска Windows
_SHORTCUT_MS = 3000   # сколько на кнопке висит результат создания ярлыка
_REPO_URL = f"https://github.com/{FLOWZAP_REPO}"
_LICENSE_URL = f"{_REPO_URL}/blob/main/LICENSE"


def _group(caption: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    """Карточка-группа настроек: подпись капсом (+ пояснение) и тело."""
    card = QFrame()
    card.setObjectName("card")
    outer = QVBoxLayout(card)
    outer.setContentsMargins(22, 18, 22, 18)
    outer.setSpacing(4)
    outer.addWidget(label(caption, role="caption"))
    if subtitle:
        outer.addWidget(label(subtitle, role="muted"))
    outer.addSpacing(8)
    body = QVBoxLayout()
    body.setSpacing(8)
    outer.addLayout(body)
    return card, body


class _ThemeSwatch(QAbstractButton):
    """Карточка выбора темы: мини-превью окна в цветах темы + название.
    Рисует превью цветами СВОЕЙ темы, рамку выбора — цветами текущей."""

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(THEME_NAMES[key])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(100, 112)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        cur, own = theme.palette, THEMES[self.key]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Превью окна: вкладки сверху, три плитки, блок пресетов — как главная.
        preview = QRectF(2, 2, w - 4, 74)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(own.bg_root))
        painter.drawRoundedRect(preview, 10, 10)
        tabs = QRectF(preview.center().x() - 27, preview.y() + 7, 54, 10)
        painter.setBrush(QColor(own.border))
        painter.drawRoundedRect(tabs, 5, 5)
        painter.setBrush(QColor(own.bg_card))
        painter.drawRoundedRect(QRectF(tabs.x() + 2, tabs.y() + 2, 16, 6), 3, 3)

        gap, top = 5, preview.y() + 23
        tile_w = (preview.width() - 16 - gap * 2) / 3
        for i in range(3):
            tile = QRectF(preview.x() + 8 + i * (tile_w + gap), top, tile_w, 24)
            painter.setPen(QPen(QColor(own.border), 1))
            painter.setBrush(QColor(own.bg_card))
            painter.drawRoundedRect(tile, 5, 5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(own.text_primary))
            painter.drawRoundedRect(QRectF(tile.x() + 5, tile.y() + 6, tile_w * 0.4, 4), 2, 2)
            painter.setBrush(QColor(own.accent if i == 0 else own.border_strong))
            painter.drawRoundedRect(QRectF(tile.right() - 15, tile.y() + 5, 10, 6), 3, 3)
        block = QRectF(preview.x() + 8, top + 29, preview.width() - 16, 15)
        painter.setPen(QPen(QColor(own.border), 1))
        painter.setBrush(QColor(own.bg_card))
        painter.drawRoundedRect(block, 5, 5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(own.accent))
        painter.drawRoundedRect(QRectF(block.x() + 5, block.y() + 5, 14, 5), 2.5, 2.5)
        painter.setBrush(QColor(own.text_muted))
        painter.drawRoundedRect(QRectF(block.x() + 23, block.y() + 5, block.width() * 0.3, 5), 2.5, 2.5)

        # Рамка выбора / наведения
        if self.isChecked() or self.underMouse():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(cur.accent if self.isChecked() else cur.border_strong), 2))
            painter.drawRoundedRect(preview.adjusted(-1, -1, 1, 1), 11, 11)

        # Подпись
        font = QFont(theme.typography.family_ui)
        font.setPixelSize(theme.typography.size_sm)
        font.setWeight(QFont.DemiBold if self.isChecked() else QFont.Normal)
        painter.setFont(font)
        painter.setPen(QColor(cur.text_primary if self.isChecked() else cur.text_secondary))
        text_rect = QRectF(0, 82, w, h - 82)
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignVCenter, THEME_NAMES[self.key])


class SettingsTab(QWidget):
    _winKnown    = Signal(bool)        # (включён)     — из autostart_enabled_async
    _winDone     = Signal(bool, str)   # (ok, error)   — из set_autostart_async
    _shortcutDone = Signal(bool, str)  # (ok, error)   — из create_desktop_shortcut_async
    _serviceDone  = Signal(bool, str)  # (ok, error)   — установка/удаление фоновой службы

    def __init__(self, parent=None, config: dict = None, save_config_fn=None,
                 on_tray_changed=None, on_theme_changed=None, on_effects_changed=None,
                 manager=None, on_relaunch_admin=None, on_service_outdated=None) -> None:
        super().__init__(parent)
        # (bool) — есть новая версия службы: MainWindow зажигает точку на вкладке
        self._on_service_outdated = on_service_outdated
        self._manager = manager
        self._on_relaunch_admin = on_relaunch_admin
        self._service_busy = False
        self._config = config if config is not None else {}
        self._save_config_fn = save_config_fn
        self._on_tray_changed = on_tray_changed
        self._on_theme_changed = on_theme_changed
        self._on_effects_changed = on_effects_changed
        self._app_dir = Path(self._config.get("_app_dir") or Path(__file__).parent.parent)
        self._win_target = False       # что просили при последнем щелчке по «запуск с Windows»

        # Слоты — методы, не лямбды: тогда доставка из потоков идёт через очередь GUI-потока.
        self._winKnown.connect(self._on_win_known)
        self._winDone.connect(self._on_win_done)
        self._shortcutDone.connect(self._on_shortcut_done)
        self._serviceDone.connect(self._on_service_done)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_win_status)
        self._shortcut_timer = QTimer(self)
        self._shortcut_timer.setSingleShot(True)
        self._shortcut_timer.timeout.connect(self._reset_shortcut_button)

        self._build()

        # Задачу в планировщике проверяем в фоне; пока не ответила — переключатель заблокирован.
        self._sw_win.setEnabled(False)
        autostart_enabled_async(lambda enabled: self._winKnown.emit(enabled))

    # ── построение ───────────────────────────────────────────────────────

    def _build(self) -> None:
        zapret_cfg = self._config.get("zapret", {})
        ui_cfg = self._config.get("ui", {})

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        page = Page("Настройки", "Всё сохраняется сразу")
        # Каскад: группы разной высоты в двух колонках — вся страница всплывает целиком
        self.setProperty("cascade", "together")
        root.addWidget(page)

        columns = QHBoxLayout()
        columns.setSpacing(theme.metrics.padding_md)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(theme.metrics.padding_md)
        right.setSpacing(theme.metrics.padding_md)
        columns.addLayout(left, stretch=1)
        columns.addLayout(right, stretch=1)
        page.body.addLayout(columns)

        # ── Запуск и окно ──
        launch, body = _group("ЗАПУСК И ОКНО")
        self._sw_win = Switch()
        self._sw_win.clicked.connect(self._on_win_clicked)
        self._row_win = SettingRow(
            "Запускать вместе с Windows", "Без запроса прав администратора при входе", self._sw_win)

        self._sw_zapret = Switch()
        self._sw_zapret.setChecked(zapret_cfg.get("autostart", False))
        self._sw_zapret.clicked.connect(self._on_zapret_autostart_clicked)

        self._sw_restore = Switch()
        self._sw_restore.setChecked(ui_cfg.get("restore_state", True))
        self._sw_restore.clicked.connect(self._on_restore_state_clicked)

        self._sw_tray = Switch()
        self._sw_tray.setChecked(ui_cfg.get("tray_enabled", True))
        self._sw_tray.clicked.connect(self._on_tray_clicked)

        rows = [
            self._row_win,
            SettingRow("Включать обход при запуске", "С последним выбранным пресетом", self._sw_zapret),
            SettingRow("Восстанавливать DNS и TG Proxy", "Если они были включены при выходе", self._sw_restore),
            SettingRow("Сворачивать в трей при закрытии",
                       "Крестик прячет окно, обход продолжает работать", self._sw_tray),
        ]
        for i, row in enumerate(rows):
            if i:
                body.addWidget(divider())
            body.addWidget(row)
        left.addWidget(launch)

        # ── Оформление ──
        look, body = _group("ОФОРМЛЕНИЕ", "Тема применяется сразу")
        swatches = QHBoxLayout()
        swatches.setSpacing(12)
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        current = ui_cfg.get("theme") if ui_cfg.get("theme") in THEMES else theme.current
        for key in THEME_NAMES:
            sw = _ThemeSwatch(key)
            sw.setChecked(key == current)
            sw.clicked.connect(lambda _=False, k=key: self._on_theme_selected(k))
            self._theme_group.addButton(sw)
            swatches.addWidget(sw)
        body.addLayout(swatches)
        body.addSpacing(6)

        self._sw_aurora = Switch()
        self._sw_aurora.setChecked(ui_cfg.get("aurora", True))
        self._sw_aurora.clicked.connect(self._on_effects_clicked)
        self._sw_anim = Switch()
        self._sw_anim.setChecked(ui_cfg.get("animations", True))
        self._sw_anim.clicked.connect(self._on_effects_clicked)
        body.addWidget(divider())
        body.addWidget(SettingRow(
            "Северное сияние на фоне",
            "Цветные пятна медленно плавают за карточками. В трее не работает", self._sw_aurora))
        body.addWidget(divider())
        body.addWidget(SettingRow(
            "Анимации интерфейса",
            "Волна от переключателей, плавные вкладки и появление, живая полоса проверки", self._sw_anim))
        left.addWidget(look)
        left.addStretch(1)

        # ── Фоновая служба ──
        service, body = _group("ФОНОВАЯ СЛУЖБА")
        self._btn_service = button("Установить")
        self._btn_service.setMinimumWidth(140)
        self._btn_service.clicked.connect(self._on_service_clicked)
        self._row_service = SettingRow("FlowZap Service", "Проверяю…", self._btn_service)
        body.addWidget(self._row_service)
        body.addWidget(label(
            "Запускает обход без прав администратора у самого FlowZap. Ставится один раз — "
            "Windows спросит разрешение", role="hint", wrap=True))
        self._btn_admin = button("Запустить FlowZap от администратора", variant="link")
        self._btn_admin.setToolTip("Запасной вариант, если служба не устанавливается: обход запускается напрямую")
        self._btn_admin.clicked.connect(self._on_relaunch_admin_clicked)
        body.addWidget(self._btn_admin, alignment=Qt.AlignLeft)
        right.addWidget(service)

        # ── Прочее ──
        misc, body = _group("ПРОЧЕЕ")
        self._btn_shortcut = button("Создать")
        self._btn_shortcut.setMinimumWidth(140)
        self._btn_shortcut.clicked.connect(self._on_shortcut_clicked)
        self._row_shortcut = SettingRow("Ярлык на рабочем столе", "Быстрый запуск FlowZap", self._btn_shortcut)
        body.addWidget(self._row_shortcut)
        body.addWidget(divider())
        self._btn_logs = button("Открыть папку")
        self._btn_logs.setMinimumWidth(140)
        self._btn_logs.setToolTip("Откроется папка logs с выделенным flowzap.log")
        self._btn_logs.clicked.connect(self._open_logs_folder)
        body.addWidget(SettingRow(
            "Логи", "Если что-то не работает — приложите flowzap.log к обращению", self._btn_logs))
        right.addWidget(misc)

        # ── О программе ──
        about, body = _group("О ПРОГРАММЕ")
        head = QHBoxLayout()
        head.setSpacing(14)
        if self._icon_path().exists():
            logo = QLabel()
            logo.setPixmap(QIcon(str(self._icon_path())).pixmap(46, 46))
            head.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(label(f"FlowZap {GUI_VERSION}", role="strong"))
        names.addWidget(label("© 2026 xxFireflyxx. Все права защищены", role="muted"))
        head.addLayout(names, stretch=1)
        btn_license = button("Условия использования  ↗", variant="link")
        btn_license.setToolTip(_LICENSE_URL)
        btn_license.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(_LICENSE_URL)))
        head.addWidget(btn_license, alignment=Qt.AlignVCenter)
        body.addLayout(head)
        links = QHBoxLayout()
        links.setSpacing(8)
        for text, url in (("Страница проекта  ↗", _REPO_URL), ("Проблемы и вопросы  ↗", _REPO_URL + "/issues")):
            btn = button(text)
            btn.setMinimumHeight(40)
            btn.setToolTip(url)
            btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            links.addWidget(btn, stretch=1)
        body.addLayout(links)
        body.addWidget(label("Ссылки открываются в браузере, на GitHub. В «Проблемах и вопросах» можно "
                             "найти похожий случай или описать свой", role="hint", wrap=True))
        right.addWidget(about)
        right.addStretch(1)

    def _icon_path(self) -> Path:
        bundled = Path(getattr(sys, "_MEIPASS", self._app_dir)) / "assets" / "icon.ico"
        return bundled if bundled.exists() else self._app_dir / "assets" / "icon.ico"

    # ── конфиг ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        if self._save_config_fn:
            self._save_config_fn()

    def _section(self, name: str) -> dict:
        return self._config.setdefault(name, {})

    # ── переключатели ────────────────────────────────────────────────────

    def _on_zapret_autostart_clicked(self) -> None:
        self._section("zapret")["autostart"] = self._sw_zapret.isChecked()
        self._save()

    def _on_restore_state_clicked(self) -> None:
        self._section("ui")["restore_state"] = self._sw_restore.isChecked()
        self._save()

    def _on_tray_clicked(self) -> None:
        enabled = self._sw_tray.isChecked()
        self._section("ui")["tray_enabled"] = enabled
        if self._on_tray_changed:
            self._on_tray_changed(enabled)
        self._save()

    # ── запуск с Windows ────────────────────────────────────────────────

    def _on_win_known(self, exists: bool) -> None:
        self._sw_win.setChecked(exists)
        self._sw_win.setEnabled(True)

    def _on_win_clicked(self) -> None:
        self._win_target = self._sw_win.isChecked()
        self._sw_win.setEnabled(False)       # пока PowerShell/schtasks работает — повторные щелчки не нужны
        set_autostart_async(self._win_target, lambda ok, err: self._winDone.emit(ok, err))

    def _on_win_done(self, ok: bool, error: str) -> None:
        enable = self._win_target
        self._sw_win.setEnabled(True)
        if not ok:
            self._sw_win.setChecked(not enable)      # вернуть как было
            self._show_win_status(error or "Не удалось изменить автозапуск", "error")
            return
        if enable:
            self._show_win_status("✓ FlowZap добавлен в автозапуск", "success")
        else:
            self._show_win_status("Автозапуск отключён", None)

    def _show_win_status(self, text: str, tone: str | None) -> None:
        """Результат — временно вместо пояснения под названием строки."""
        desc = self._row_win.description
        if not self._status_timer.isActive():
            self._win_desc_text = desc.text()
        desc.setText(text)
        set_tone(desc, tone)
        self._status_timer.start(_STATUS_MS)         # перезапуск: старый таймер не сотрёт новый текст

    def _clear_win_status(self) -> None:
        desc = self._row_win.description
        desc.setText(getattr(self, "_win_desc_text", desc.text()))
        set_tone(desc, None)

    # ── тема ─────────────────────────────────────────────────────────────

    def _on_effects_clicked(self) -> None:
        ui = self._section("ui")
        ui["aurora"] = self._sw_aurora.isChecked()
        ui["animations"] = self._sw_anim.isChecked()
        self._save()
        if self._on_effects_changed:
            self._on_effects_changed(ui["aurora"], ui["animations"])

    def _on_theme_selected(self, key: str) -> None:
        # ui.theme — ключ, который читают main.py и MainWindow.
        self._section("ui")["theme"] = key
        self._save()
        if self._on_theme_changed:
            self._on_theme_changed(key)

    # ── ярлык ────────────────────────────────────────────────────────────

    def _on_shortcut_clicked(self) -> None:
        self._btn_shortcut.setEnabled(False)
        self._btn_shortcut.setText("Создаю…")
        create_desktop_shortcut_async(self._app_dir, lambda ok, err: self._shortcutDone.emit(ok, err))

    def _on_shortcut_done(self, ok: bool, error: str) -> None:
        self._btn_shortcut.setEnabled(True)
        self._btn_shortcut.setText("✓ Создан" if ok else "Ошибка")
        desc = self._row_shortcut.description
        if not ok:
            desc.setText(error or "Не удалось создать ярлык")
            set_tone(desc, "error")
        self._shortcut_timer.start(_SHORTCUT_MS)

    def _reset_shortcut_button(self) -> None:
        self._btn_shortcut.setText("Создать")
        desc = self._row_shortcut.description
        desc.setText("Быстрый запуск FlowZap")
        set_tone(desc, None)

    # ── фоновая служба ───────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_service_status()

    def refresh_service_status(self) -> None:
        """Состояние службы — из диспетчера служб Windows (быстро, без канала)."""
        if self._service_busy:
            return
        try:
            state, _pid = service_client.query_service()
        except OSError:
            state = "unknown"
        admin = service_client.is_admin()
        installed = state != "missing"
        outdated = installed and service_client.update_available()
        texts = {
            "running": ("Работает", "success"),
            "stopped": ("Установлена, остановлена — запустится сама при включении обхода", None),
            "missing": (("Не установлена — сейчас обход работает, потому что FlowZap запущен "
                         "от администратора") if admin else
                        "Не установлена — без неё обход не запустится", None if admin else "warning"),
        }
        text, tone = texts.get(state, ("Состояние неизвестно", "warning"))
        if outdated:
            text = (f"Есть новая версия службы ({service_client.version_text(service_client.installed_version())} → "
                    f"{service_client.version_text(service_client.bundled_version())}) — обновите, Windows спросит разрешение")
            tone = "warning"
        desc = self._row_service.description
        desc.setText(text)
        set_tone(desc, tone)
        self._service_action = "update" if outdated else ("remove" if installed else "install")
        self._btn_service.setText({"update": "Обновить", "remove": "Удалить", "install": "Установить"}[self._service_action])
        self._btn_service.setEnabled(True)
        if self._on_service_outdated:
            self._on_service_outdated(outdated)
        self._btn_admin.setVisible(not installed and not admin and self._on_relaunch_admin is not None)

    def _engine_dir(self) -> Path:
        exe = Path(self._config.get("zapret", {}).get("exe_path", "zapret/bin/winws.exe"))
        return (exe if exe.is_absolute() else self._app_dir / exe).parent

    def _on_service_clicked(self) -> None:
        action = getattr(self, "_service_action", "install")
        if action == "remove" and QMessageBox.question(
                self, "FlowZap", "Удалить фоновую службу?\n\nОбход перестанет запускаться, пока служба "
                "не будет установлена снова (или FlowZap не запущен от администратора).") != QMessageBox.Yes:
            return
        self._service_busy = True
        self._btn_service.setEnabled(False)
        self._btn_service.setText({"update": "Обновляю…", "remove": "Удаляю…", "install": "Устанавливаю…"}[action])
        desc = self._row_service.description
        desc.setText("Подтвердите в окне Windows")
        set_tone(desc, None)

        def _worker() -> None:
            try:
                if action == "remove":
                    if self._manager is not None and self._manager.is_running:
                        self._manager.stop()
                    service_client.uninstall()
                else:
                    # Обновление = переустановка: служба на это время остановится
                    if action == "update" and self._manager is not None and self._manager.is_running:
                        self._manager.stop()
                    engine = self._engine_dir()
                    version_file = engine.parent / "version.txt"
                    version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
                    service_client.install(engine, version)
            except service_client.ServiceError as e:
                self._serviceDone.emit(False, str(e))
                return
            except Exception as e:
                log.error(f"Фоновая служба: {e}", exc_info=True)
                self._serviceDone.emit(False, str(e))
                return
            self._serviceDone.emit(True, "")

        threading.Thread(target=_worker, daemon=True, name="service-install").start()

    def _on_service_done(self, ok: bool, error: str) -> None:
        self._service_busy = False
        self.refresh_service_status()
        if not ok:
            desc = self._row_service.description
            desc.setText(error or "Не получилось")
            set_tone(desc, "error")

    def _on_relaunch_admin_clicked(self) -> None:
        if self._on_relaunch_admin:
            self._on_relaunch_admin()

    # ── логи ─────────────────────────────────────────────────────────────

    def _open_logs_folder(self) -> None:
        """Папка logs с уже выделенным flowzap.log — его удобно сразу перетащить в обращение.
        Рядом лежит crash.log, если программа падала."""
        logs_dir = self._app_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "flowzap.log"
        if log_file.exists():
            subprocess.Popen(f'explorer /select,"{log_file}"')
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir)))
