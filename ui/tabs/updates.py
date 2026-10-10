"""
ui/tabs/updates.py
-----------------
Вкладка «Обновления»: три блока — FlowZap (GUI), Core (zapret), TG WS Proxy.
У каждого версия, статус и кнопки «Проверить» / «Обновить».

Все сетевые операции — в core/updates/: он сам создаёт потоки и зовёт
on_progress / on_done из них. Вкладка только оборачивает эти колбэки в Signal.

Автоматическая проверка по таймеру и точка на вкладке — в MainWindow;
здесь только переключатель «Проверять автоматически» (updater.check_on_start).
"""

import logging
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.base import Glyph, button, label, restyle, set_tone
from ui.widgets.controls import CircleBadge, Switch
from ui.widgets.layout import Page
from core.updates.app import find_exe_asset, download_and_install_exe
from core.updates.releases import FLOWZAP_REPO, check_release_async
from core.tgproxy.manager import is_installed
from core.updates.tgproxy import TG_PROXY_REPO, download_and_install_tg_proxy, get_installed_tg_proxy_version
from core.updates.zapret import CORE_REPO, download_and_install_core, get_installed_core_version
from core.version import GUI_VERSION

log = logging.getLogger(__name__)


# ── Режим сравнения версий GUI ───────────────────────────────────────────
# False — ТЕСТОВЫЙ режим (сейчас): сборка с «beta» НЕ считается новее релиза,
#         проверка всегда находит последний релиз — чтобы можно было
#         протестировать сам процесс обновления.
# True  — БОЕВОЙ режим (включить после всех тестов): beta 0.5.2 считается новее
#         релиза 0.5.1 (откат не предлагается), но финал 0.5.2 новее беты 0.5.2.
BETA_IS_NEWER = False

_QUIT_DELAY_MS = 2000   # пауза перед выходом после успешной загрузки обновления GUI


_CORE_MISSING = "Не установлен — скачается при установке или первом запуске обхода"
_TG_MISSING = "Не установлен — скачается при установке или первом включении"


def _parse_version(v: str) -> tuple[tuple[int, ...], bool]:
    """'0.5.2 beta 11 win' -> ((0, 5, 2), True); 'v0.5.1' -> ((0, 5, 1), False)."""
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", v)
    nums = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    return nums, "beta" in v.lower()


def gui_update_available(current: str, tag: str) -> bool:
    """Есть ли для GUI релиз, который стоит предложить вместо current."""
    cur, cur_beta = _parse_version(current)
    latest, _ = _parse_version(tag)
    if BETA_IS_NEWER:
        return latest > cur or (latest == cur and cur_beta)
    return cur_beta or latest > cur


def _same_version(installed: str, tag: str) -> bool:
    return installed.lstrip("vV") == tag.lstrip("vV")


def _release_notes(body: str, max_lines: int = 6) -> str:
    """Текст релиза с GitHub (markdown) → несколько коротких строк без разметки."""
    lines = []
    for raw in (body or "").splitlines():
        line = re.sub(r"[*_`#>]+", "", raw).strip()
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        if not line:
            continue
        if line[0] in "-•":
            line = "•  " + line.lstrip("-• ").strip()
        lines.append(line)
    shown = lines[:max_lines]
    if len(lines) > max_lines:
        shown.append("…")
    return "\n".join(shown)


class _UpdateCard(QFrame):
    """Строка-карточка компонента. У всех трёх один скелет: значок, название и
    версия, статус (текстом и цветом), одна кнопка справа — «Проверить», а когда
    найдено обновление — «Обновить». Ниже по необходимости: полоса загрузки и
    «Что нового». Логики обновления не содержит."""

    _TONES = {"muted": "muted", "secondary": None, "ok": "success", "warn": "warning", "error": "error"}

    def __init__(self, title: str, description: str, glyph: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tile")
        self.setProperty("active", False)
        self._description = description
        self._action = "Обновить"
        self.updating = False

        self.badge = CircleBadge(glyph, 46)
        self.version_label = label("", role="pill")
        self.status_label = label(description, role="muted", wrap=True)

        self.btn_check = button("Проверить")
        self.btn_update = button("Обновить", variant="primary")
        for b in (self.btn_check, self.btn_update):
            b.setMinimumWidth(130)
            b.setMinimumHeight(40)
        self.btn_update.setVisible(False)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(label(title, role="section"))
        title_row.addWidget(self.version_label)
        title_row.addStretch(1)

        text = QVBoxLayout()
        text.setSpacing(4)
        text.addLayout(title_row)
        text.addWidget(self.status_label)

        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(self.badge, alignment=Qt.AlignVCenter)
        head.addLayout(text, stretch=1)
        head.addWidget(self.btn_check, alignment=Qt.AlignVCenter)
        head.addWidget(self.btn_update, alignment=Qt.AlignVCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)          # стадии без процентов — «бегущая» полоса
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)

        self.notes_box = QFrame()
        self.notes_box.setObjectName("notes")
        notes = QVBoxLayout(self.notes_box)
        notes.setContentsMargins(16, 12, 16, 12)
        notes.setSpacing(6)
        notes_head = QHBoxLayout()
        notes_head.addWidget(label("Что нового", role="strong"))
        notes_head.addStretch(1)
        self.notes_link = label("", role="hint")
        self.notes_link.setCursor(Qt.PointingHandCursor)
        self.notes_link.mouseReleaseEvent = lambda _e: self._notes_url and QDesktopServices.openUrl(QUrl(self._notes_url))
        notes_head.addWidget(self.notes_link)
        notes.addLayout(notes_head)
        self.notes_text = label("", role="muted", wrap=True)
        notes.addWidget(self.notes_text)
        self.notes_box.setVisible(False)
        self._notes_url = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addLayout(head)
        layout.addWidget(self.progress)
        layout.addWidget(self.notes_box)

    def set_version(self, text: str) -> None:
        self.version_label.setText(text)

    def set_action_text(self, text: str) -> None:
        """«Обновить» или «Установить» (компонент ещё не установлен)."""
        self._action = text
        self.btn_update.setText(text)

    def set_status(self, text: str, kind: str = "secondary") -> None:
        self.status_label.setText(text)
        set_tone(self.status_label, self._TONES.get(kind))

    def set_notes(self, body: str, url: str = "") -> None:
        text = _release_notes(body)
        self._notes_url = url
        self.notes_text.setText(text)
        self.notes_link.setText("Полностью на GitHub ↗" if url else "")
        self.notes_box.setVisible(bool(text))

    def set_checking(self, checking: bool) -> None:
        self.btn_check.setEnabled(not checking)
        self.btn_check.setText("Проверка…" if checking else "Проверить")
        if checking:
            self.set_update_enabled(False)

    def set_updating(self, updating: bool) -> None:
        self.updating = updating
        self.btn_check.setEnabled(not updating)
        self.btn_update.setEnabled(not updating)
        busy = "Устанавливаю…" if self._action == "Установить" else "Обновляю…"
        self.btn_update.setText(busy if updating else self._action)
        self.progress.setVisible(updating)

    def set_update_enabled(self, enabled: bool) -> None:
        """Найдено обновление: кнопка «Проверить» уступает место «Обновить»,
        карточка подсвечивается — видно, где есть что делать."""
        self.btn_update.setEnabled(enabled)
        self.btn_update.setVisible(enabled)
        self.btn_check.setVisible(not enabled)
        if not enabled:
            self.notes_box.setVisible(False)
        self._set_active(enabled)

    def set_not_installed(self, hint: str) -> None:
        """Компонент не установлен: сразу «Установить». Это не обновление —
        карточка не подсвечивается и «Что нового» не показывается."""
        self.set_action_text("Установить")
        self.set_status(hint, "secondary")
        self.btn_update.setEnabled(True)
        self.btn_update.setVisible(True)
        self.btn_check.setVisible(False)
        self.notes_box.setVisible(False)
        self._set_active(False)

    def _set_active(self, active: bool) -> None:
        if self.property("active") != active:
            self.setProperty("active", active)
            restyle(self)
            self.badge.set_active(active)


class UpdatesTab(QWidget):
    _releaseChecked = Signal(str, object)     # (блок, release | None) — из check_release_async
    _progress       = Signal(str, str)        # (блок, сообщение)      — из download_and_install_*
    _finished       = Signal(str, bool, str)  # (блок, ok, сообщение)  — из download_and_install_*

    def __init__(self, parent=None, config: dict = None, manager=None, dashboard=None,
                 save_config_fn=None, on_autocheck_changed=None) -> None:
        super().__init__(parent)
        self._config = config or {}
        self._save_config_fn = save_config_fn
        self._on_autocheck_changed = on_autocheck_changed
        self._manager = manager           # ZapretManager (или None)
        self._dashboard = dashboard       # DashboardTab: zapret_dir, TG Proxy, перечитывание пресетов

        app_dir = Path(self._config.get("_app_dir") or Path(__file__).resolve().parents[2])
        self._app_dir = app_dir
        self._tg_dir = app_dir / "tgproxy"

        self._core_resume_bat = None      # пресет, который вернуть после обновления Core
        self._tg_resume = False           # вернуть ли TG Proxy после обновления

        self._gui = _UpdateCard("FlowZap", "Это приложение", Glyph.MONITOR)
        self._core = _UpdateCard("zapret Core", "Движок обхода и пресеты от Flowseal", Glyph.SHIELD)
        self._tg = _UpdateCard("TG WS Proxy", "Прокси для Telegram", Glyph.SEND)
        self._cards = {"gui": self._gui, "core": self._core, "tg": self._tg}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        page = Page("Обновления", "Версии FlowZap, zapret и TG Proxy")
        root.addWidget(page)
        self._sw_auto = Switch("Проверять автоматически")
        self._sw_auto.setToolTip("При запуске и раз в 3 часа; новое отмечается точкой на вкладке")
        self._sw_auto.setChecked(self._config.get("updater", {}).get("check_on_start", True))
        self._sw_auto.clicked.connect(self._on_autocheck_toggled)
        page.header_actions.addWidget(self._sw_auto, alignment=Qt.AlignVCenter)
        page.header_actions.addSpacing(12)
        self._btn_check_all = button("Проверить все")
        self._btn_check_all.setMinimumHeight(40)
        self._btn_check_all.clicked.connect(self._check_all)
        page.header_actions.addWidget(self._btn_check_all, alignment=Qt.AlignVCenter)
        for card in self._cards.values():
            page.body.addWidget(card)

        self._gui.btn_check.clicked.connect(self._check_gui)
        self._gui.btn_update.clicked.connect(self._update_gui)
        self._core.btn_check.clicked.connect(self._check_core)
        self._core.btn_update.clicked.connect(self._update_core)
        self._tg.btn_check.clicked.connect(self._check_tg)
        self._tg.btn_update.clicked.connect(self._update_tg)

        # Слоты — методы, не лямбды: тогда доставка из потоков updater'а идёт через очередь GUI-потока.
        self._releaseChecked.connect(self._on_release_checked)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)

        self._gui.set_version(f"v{GUI_VERSION}")
        self._refresh_installed()

    def _on_autocheck_toggled(self) -> None:
        enabled = self._sw_auto.isChecked()
        self._config.setdefault("updater", {})["check_on_start"] = enabled
        if self._save_config_fn:
            self._save_config_fn()
        if self._on_autocheck_changed:
            self._on_autocheck_changed(enabled)

    # ── общее ────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        # Core мог быть установлен с Dashboard — подтягиваем актуальные версии при показе вкладки.
        self._refresh_installed()
        super().showEvent(event)

    def _refresh_installed(self) -> None:
        """Версии в карточках и состояние «не установлен» (Core и TG Proxy
        могли поставиться с главной — при первом запуске обхода/включении)."""
        for card, installed, hint in (
            (self._core, self._core_installed_version(), _CORE_MISSING),
            (self._tg, self._tg_installed_version(), _TG_MISSING),
        ):
            card.set_version(installed or "не установлен")
            if card.updating:
                continue
            if not installed:
                card.set_not_installed(hint)
            elif card.btn_update.isVisible() and card.btn_update.text() == "Установить":
                # Был «не установлен», а теперь стоит — обычная карточка с «Проверить»
                card.set_action_text("Обновить")
                card.set_update_enabled(False)
                card.set_status(card._description, "muted")

    def _core_installed_version(self) -> str:
        core = self._dashboard.zapret_dir if self._dashboard is not None else self._app_dir / "zapret"
        return get_installed_core_version(core) or ""

    def _tg_installed_version(self) -> str:
        if not is_installed(self._tg_dir):
            return ""
        return get_installed_tg_proxy_version(self._tg_dir) or "установлен"

    def apply_background_check(self, releases: dict) -> None:
        """Результаты фоновой проверки из MainWindow — в карточки. Пропускаем
        не полученные (нет сети) и карточки, где сейчас идёт проверка или загрузка."""
        for block, release in releases.items():
            card = self._cards[block]
            if not release or not card.btn_check.isEnabled() or not card.btn_update.isEnabled() and card.btn_update.isVisible():
                continue
            if block == "core" and not self._core_installed_version() or block == "tg" and not self._tg_installed_version():
                continue
            self._on_release_checked(block, release)

    def _on_release_checked(self, block: str, release) -> None:
        {"gui": self._apply_gui_check,
         "core": self._apply_core_check,
         "tg": self._apply_tg_check}[block](release)

    def _on_progress(self, block: str, message: str) -> None:
        self._cards[block].set_status(message, "muted")

    def _on_finished(self, block: str, ok: bool, message: str) -> None:
        {"gui": self._on_gui_done,
         "core": self._on_core_done,
         "tg": self._on_tg_done}[block](ok, message)

    def _start_check(self, block: str, repo: str) -> None:
        card = self._cards[block]
        card.set_checking(True)
        card.set_status("Проверка обновлений…", "secondary")
        check_release_async(repo, lambda release: self._releaseChecked.emit(block, release))

    def _callbacks(self, block: str) -> dict:
        return {
            "on_progress": lambda msg: self._progress.emit(block, msg),
            "on_done": lambda ok, msg: self._finished.emit(block, ok, msg),
        }

    def _check_all(self) -> None:
        """Проверить все три блока сразу (занятые обновлением — пропускаем)."""
        for check, card in ((self._check_gui, self._gui), (self._check_core, self._core), (self._check_tg, self._tg)):
            if card.btn_check.isEnabled() and card.btn_check.isVisible():
                check()

    # ── FlowZap (GUI) ────────────────────────────────────────────────────

    def _check_gui(self) -> None:
        self._start_check("gui", FLOWZAP_REPO)

    def _apply_gui_check(self, release) -> None:
        c = self._gui
        c.set_checking(False)
        if not release:
            c.set_status("Ошибка соединения. Попробуйте позже.", "error")
            return
        tag = release.get("tag_name", "?")
        if not gui_update_available(GUI_VERSION, tag):
            c.set_status("✓ Актуальная версия", "ok")
            return
        if find_exe_asset(release) is None:
            c.set_status(f"Версия {tag} есть, но файл релиза ещё не добавлен.", "error")
            return
        c.set_status(f"Доступна версия {tag}", "warn")
        c.set_update_enabled(True)
        c.set_notes(release.get("body", ""), release.get("html_url", ""))

    def _update_gui(self) -> None:
        self._gui.set_updating(True)
        self._gui.set_status("Начинаем загрузку…", "muted")
        # Бета: обновление через фоновую службу — только по строке
        # via_service = true в [updater] config.toml (в интерфейсе её нет)
        via_service = bool(self._config.get("updater", {}).get("via_service", False))
        download_and_install_exe(install_dir=self._app_dir, via_service=via_service, **self._callbacks("gui"))

    def _on_gui_done(self, ok: bool, message: str) -> None:
        c = self._gui
        c.set_updating(False)
        if ok:
            c.set_status(f"✓ {message}. Приложение перезапустится…", "ok")
            c.set_update_enabled(False)
            # Выход через QApplication.quit() → aboutToQuit → shutdown() остановит DNS/zapret/TG Proxy.
            QTimer.singleShot(_QUIT_DELAY_MS, QApplication.quit)
        else:
            c.set_status("Ошибка, попробуйте позже", "error")
            c.set_update_enabled(True)

    # ── Core (zapret) ────────────────────────────────────────────────────

    def _check_core(self) -> None:
        self._start_check("core", CORE_REPO)

    def _apply_core_check(self, release) -> None:
        c = self._core
        c.set_checking(False)
        installed = self._core_installed_version()
        if not installed:
            c.set_not_installed(_CORE_MISSING)
            return
        if not release:
            c.set_status("Ошибка соединения. Попробуйте позже.", "error")
            return
        tag = release.get("tag_name", "?")
        if _same_version(installed, tag):
            c.set_status("✓ Актуальная версия", "ok")
            return
        c.set_status(f"Доступна версия {tag}", "warn")
        c.set_action_text("Обновить")
        c.set_update_enabled(True)
        c.set_notes(release.get("body", ""), release.get("html_url", ""))

    def _update_core(self) -> None:
        # Пинг-тест держит свой собственный winws.exe в обход ZapretManager —
        # обновление убило бы его через taskkill в середине прогона, и
        # тестируемый в этот момент пресет получил бы ложный FAIL не из-за
        # себя, а из-за самого обновления (см. DashboardTab.is_testing_presets).
        if self._dashboard is not None and self._dashboard.is_testing_presets:
            self._core.set_status(
                "Сейчас идёт проверка пресетов — дождитесь её окончания и повторите.", "warn"
            )
            return
        # winws.exe держит файлы Core и драйвер WinDivert — запущенный zapret останавливаем
        # принудительно и после обновления возвращаем тот же пресет.
        self._core_resume_bat = None
        if self._manager is not None and self._manager.is_running:
            self._core_resume_bat = self._manager.bat_path
            self._manager.stop()
        self._core.set_updating(True)
        self._core.set_status("Начинаем загрузку…", "muted")
        download_and_install_core(
            zapret_dir=self._dashboard.zapret_dir, repo=CORE_REPO, **self._callbacks("core")
        )

    def _on_core_done(self, ok: bool, message: str) -> None:
        c = self._core
        c.set_updating(False)
        if ok:
            c.set_version(self._core_installed_version() or "не установлен")
            c.set_action_text("Обновить")
            c.set_update_enabled(False)
            self._dashboard.on_core_updated()     # перечитать пресеты ДО возврата пресета
        elif not self._core_installed_version():
            c.set_not_installed(_CORE_MISSING)
        else:
            c.set_update_enabled(True)
        # Пресет возвращаем и при ошибке: zapret работал до нажатия, пользователь не должен остаться без него.
        extra = self._resume_zapret()
        if ok:
            c.set_status(f"✓ {message}{extra}", "ok")
        else:
            c.set_status(f"Ошибка, попробуйте позже{extra}", "error")

    def _resume_zapret(self) -> str:
        bat, self._core_resume_bat = self._core_resume_bat, None
        if bat is None or self._manager is None or self._manager.is_running:
            return ""     # не работал до обновления (или пользователь уже запустил сам)
        bat = Path(bat)
        if not bat.exists():
            return f" Пресет «{bat.stem}» в новой версии Core не найден — запустите zapret вручную."
        # Результат запуска покажет плитка обхода на главной (ошибка — красным)
        self._manager.start_async(bat_path=bat)
        return f" Пресет «{bat.stem}» запускается снова."

    # ── TG WS Proxy ──────────────────────────────────────────────────────

    def _check_tg(self) -> None:
        self._start_check("tg", TG_PROXY_REPO)

    def _apply_tg_check(self, release) -> None:
        c = self._tg
        c.set_checking(False)
        installed = self._tg_installed_version()
        if not installed:
            c.set_not_installed(_TG_MISSING)
            return
        if not release:
            c.set_status("Ошибка соединения. Попробуйте позже.", "error")
            return
        tag = release.get("tag_name", "?")
        if _same_version(installed, tag):
            c.set_status("✓ Актуальная версия", "ok")
            return
        c.set_status(f"Доступна версия {tag}", "warn")
        c.set_action_text("Обновить")
        c.set_update_enabled(True)
        c.set_notes(release.get("body", ""), release.get("html_url", ""))

    def _update_tg(self) -> None:
        self._tg.set_updating(True)
        self._tg_resume = self._dashboard.tg_proxy_running
        if not self._tg_resume:
            self._download_tg()
            return
        # Новая версия заработает только после перезапуска, а папку с кодом
        # работающего сервера лучше не подменять: останавливаем через Dashboard
        # (тумблер «TG Proxy» там остаётся синхронным), качаем после остановки
        # и включаем обратно.
        self._tg.set_status("Останавливаю TG Proxy…", "muted")
        self._dashboard.set_tg_proxy(False, on_done=self._on_tg_stopped)

    def _on_tg_stopped(self, running: bool, error: str) -> None:
        if running:      # не остановился — файл занят, обновлять нельзя; работает как работал
            self._tg_resume = False
            self._tg.set_updating(False)
            self._tg.set_update_enabled(True)
            self._tg.set_status(error or "Не удалось остановить TG Proxy.", "error")
            return
        self._download_tg()

    def _download_tg(self) -> None:
        self._tg.set_status("Начинаем загрузку…", "muted")
        download_and_install_tg_proxy(self._tg_dir, **self._callbacks("tg"))

    def _on_tg_done(self, ok: bool, message: str) -> None:
        c = self._tg
        c.set_updating(False)
        if ok:
            c.set_version(self._tg_installed_version() or "установлен")
            c.set_status(f"✓ {message}", "ok")
            c.set_action_text("Обновить")
            c.set_update_enabled(False)
        elif not self._tg_installed_version():
            c.set_not_installed(_TG_MISSING)
            c.set_status("Ошибка, попробуйте позже", "error")
        else:
            c.set_status("Ошибка, попробуйте позже", "error")
            c.set_update_enabled(True)
        # Возвращаем прокси и при ошибке: прежняя версия на месте, а работала она до нажатия.
        if self._tg_resume:
            self._tg_resume = False
            self._dashboard.set_tg_proxy(True)
