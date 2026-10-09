from pathlib import Path
import logging
import sys
import threading

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QStackedWidget,
    QLabel,
    QSystemTrayIcon,
    QMenu,
    QApplication,
)

from ui.theme import theme
from ui.widgets.animation import IntroSplash, cascade_hide, cascade_in, page_steps
from ui.widgets.base import Glyph, label
from ui.widgets.navigation import TabBar
from ui.widgets.aurora import AuroraBackground
from core.updates.app import find_exe_asset
from core.updates.releases import FLOWZAP_REPO, get_latest_release
from core.updates.tgproxy import TG_PROXY_REPO, get_installed_tg_proxy_version
from core.updates.zapret import CORE_REPO, get_installed_core_version
from core.version import GUI_VERSION
from ui.tabs.dashboard import DashboardTab
from ui.tabs.parameters import ParametersTab
from ui.tabs.updates import UpdatesTab, gui_update_available
from ui.tabs.settings import SettingsTab

log = logging.getLogger(__name__)

TABS = ["Главная", "Параметры", "Обновления", "Настройки"]
_UPDATES_TAB = 2
_SETTINGS_TAB = 3

# Автопроверка обновлений — раз в 3 часа: совпадает с TTL кэша в
# core.updates.releases.get_latest_release (_CACHE_TTL), чаще всё равно ходило бы
# в тот же кэш и не находило ничего нового.
UPDATE_CHECK_INTERVAL_MS = 3 * 60 * 60 * 1000
UPDATE_CHECK_STARTUP_DELAY_MS = 3000


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _asset_path(root: Path, name: str) -> Path:
    """assets/<name>: в сборке PyInstaller (onedir) лежит в sys._MEIPASS (папка _internal),
    рядом с exe его нет; в dev-режиме — <root>/assets."""
    bundled = Path(getattr(sys, "_MEIPASS", root)) / "assets" / name
    return bundled if bundled.exists() else root / "assets" / name


class MainWindow(QMainWindow):
    _updatesFound = Signal(bool)   # (есть ли обновление GUI/Core/TG Proxy) — из _do_check_updates
    _releasesFetched = Signal(object)  # {"gui"/"core"/"tg": release} — из _do_check_updates
    _builtinDnsFetched = Signal(object)  # ((version, entries) | None) — из fetch_builtin_dns_async

    def __init__(self, root: Path, config: dict = None, config_path: Path = None, manager=None):
        super().__init__()
        self.root = root
        self.config = config or {}
        self.config_path = config_path
        self.manager = manager

        self.setWindowTitle("FlowZap")
        self.setMinimumSize(900, 640)
        self.resize(1120, 760)

        self.minimize_to_tray = self.config.get("ui", {}).get("tray_enabled", True)

        icon_path = _asset_path(self.root, "icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        ui_cfg = self.config.get("ui", {})
        theme.aurora = ui_cfg.get("aurora", True)
        theme.animations = ui_cfg.get("animations", True)
        self._build_ui()
        self.apply_theme(ui_cfg.get("theme", "earthy"))
        self.aurora.set_enabled(theme.aurora)
        self._build_tray()

        # Окно появляется прозрачным и проявляется после первого кадра Qt
        # (_reveal): иначе Windows ~0,25 с показывает белое окно, пока Qt
        # готовит первую отрисовку.
        self.setWindowOpacity(0.0)
        self.aurora.installEventFilter(self)

        # Реальный выход (tray «Выход», закрытие без трея, QApplication.quit()) —
        # сбросить DNS, остановить zapret и TG Proxy. Сворачивание в трей сюда не попадает.
        QApplication.instance().aboutToQuit.connect(self.dashboard.shutdown)

        self._updatesFound.connect(self._set_updates_dot)
        # Результат фоновой проверки — сразу в карточки вкладки «Обновления»,
        # чтобы при входе на неё было видно, что именно обновлять.
        self._releasesFetched.connect(self.updates.apply_background_check)
        self._builtinDnsFetched.connect(self._on_builtin_dns_fetched)
        self._start_update_autocheck()

    def save_config(self) -> None:
        if not self.config_path:
            return
        try:
            import tomli_w
            data = {k: v for k, v in self.config.items() if not k.startswith("_")}
            with open(self.config_path, "wb") as f:
                tomli_w.dump(data, f)
        except Exception:
            logging.getLogger(__name__).exception("Ошибка сохранения конфига")

    def start_builtin_dns_sync(self) -> None:
        """Скачать список встроенных DNS в фоне (вызывается при запуске)."""
        from core.dns.builtin import fetch_builtin_dns_async
        fetch_builtin_dns_async(lambda result: self._builtinDnsFetched.emit(result))

    def _on_builtin_dns_fetched(self, result) -> None:
        """Конфиг меняем здесь, в GUI-потоке. Если поменялась активная пара
        (её адреса или она сама — удалённую заменяет следующая), а DNS
        включён, новые адреса сразу применяются в системе."""
        from core.dns.builtin import apply_builtin_dns
        from core.dns.manager import get_active_pair
        if not result:
            return
        active_before = get_active_pair(self.config)
        if not apply_builtin_dns(self.config, result[1], result[0]):
            return
        self.save_config()
        # Параметры держат свою копию пар и при сохранении перезаписали бы новые адреса.
        self.parameters.reload_dns()
        if get_active_pair(self.config) != active_before:
            self.dashboard.on_dns_changed()

    def set_tray_enabled(self, enabled: bool) -> None:
        """Вызывается вкладкой «Настройки»: закрытие окна сворачивает в трей (True) или завершает приложение (False).
        Иконка в трее остаётся в любом случае, как и раньше."""
        self.minimize_to_tray = enabled

    def apply_theme(self, name: str) -> None:
        """Применить тему ко всему приложению сразу, без перезапуска
        (вызывается при старте и из вкладки «Настройки»)."""
        theme.set_theme(name)
        theme.apply(QApplication.instance())
        self._apply_title_bar()

    def set_effects(self, aurora: bool, animations: bool) -> None:
        """Переключатели «Северное сияние» и «Анимации интерфейса» во вкладке «Настройки».
        Сияние меняет и QSS (полупрозрачные карточки), поэтому тему переприменяем."""
        theme.animations = animations
        if theme.aurora != aurora:
            theme.aurora = aurora
            theme.apply(QApplication.instance())
            self.aurora.set_enabled(aurora)

    def changeEvent(self, event) -> None:
        # Свёрнутое окно — сияние на паузе (в трее окно скрыто — это hideEvent самого фона).
        if event.type() == event.Type.WindowStateChange:
            self.aurora.pause(self.isMinimized())
        super().changeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            if theme.animations:
                # Шапку и блоки прячем сразу, до первого кадра. Приветствие:
                # заставка с логотипом по центру окна (только сияние вокруг),
                # а пока она тает — проявляется шапка и всплывают блоки
                # (медленнее и мягче, чем при смене вкладок). Ступени каскада
                # считаем тогда же: к этому времени раскладка окончательная.
                cascade_hide([[self._topbar]] + page_steps(self.stack.currentWidget()))
                self._intro = IntroSplash(
                    self.aurora, QIcon(str(_asset_path(self.root, "icon.ico"))),
                    on_fade_out=self._intro_fade_out)
                # Стартует в _reveal — когда окно станет видно

    def _intro_fade_out(self) -> None:
        """Заставка тает — собирается интерфейс: шапка, затем блоки страницы."""
        cascade_in([[self._topbar]], first_delay_ms=0, duration_ms=1400, lift=10)
        cascade_in(page_steps(self.stack.currentWidget()),
                   first_delay_ms=280, step_ms=330, duration_ms=2300, lift=42)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.aurora and event.type() == QEvent.Paint and self.windowOpacity() < 1.0                 and not getattr(self, "_revealing", False):
            self._revealing = True
            self.aurora.removeEventFilter(self)
            QTimer.singleShot(0, self._reveal)      # после того, как этот кадр дорисуется
        return super().eventFilter(obj, event)

    def _reveal(self) -> None:
        """Первый кадр готов — проявить окно и начать приветствие."""
        intro = getattr(self, "_intro", None)
        if intro is not None:
            intro.start()
        if not theme.animations:
            self.setWindowOpacity(1.0)
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(160)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        self._reveal_anim = anim
        anim.start()

    def _apply_title_bar(self) -> None:
        """Тёмная системная рамка окна в тёмных темах (Windows 10 1809+ / 11).
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20; на старых сборках вызов просто не сработает."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            value = ctypes.c_int(1 if theme.palette.is_dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            log.debug("Не удалось переключить цвет рамки окна", exc_info=True)

    def _build_ui(self):
        central = AuroraBackground()
        self.aurora = central
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._topbar = self._build_topbar()
        layout.addWidget(self._topbar)

        self.stack = QStackedWidget()

        self.dashboard = DashboardTab(manager=self.manager, config=self.config, save_config_fn=self.save_config)
        self.stack.addWidget(self.dashboard)

        self.parameters = ParametersTab(
            manager=self.manager,
            config=self.config,
            save_config_fn=self.save_config,
            on_dns_changed=self.dashboard.on_dns_changed,
            tg_controller=self.dashboard,
        )
        self.stack.addWidget(self.parameters)
        # Выбор активной пары на главной — Параметры перечитывают список.
        self.dashboard.on_pairs_changed = self.parameters.reload_dns
        self.dashboard.on_open_parameters = lambda: self.show_tab(1)

        self.updates = UpdatesTab(
            config=self.config, manager=self.manager, dashboard=self.dashboard,
            save_config_fn=self.save_config, on_autocheck_changed=self.set_update_autocheck,
        )
        self.stack.addWidget(self.updates)

        self.settings = SettingsTab(
            config=self.config,
            save_config_fn=self.save_config,
            on_tray_changed=self.set_tray_enabled,
            on_theme_changed=self.apply_theme,
            on_effects_changed=self.set_effects,
            manager=self.manager,
            on_relaunch_admin=self.relaunch_as_admin,
            # Новая версия службы — та же мигающая точка, что у «Обновлений»
            on_service_outdated=lambda v: self._tabs.set_badge(_SETTINGS_TAB, v),
        )
        self.stack.addWidget(self.settings)
        layout.addWidget(self.stack, stretch=1)

        self.setCentralWidget(central)

    def _build_topbar(self) -> QWidget:
        """Шапка: логотип слева, вкладки по центру, права администратора справа.
        Боковые колонки одной ширины — вкладки стоят ровно по центру окна."""
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.metrics.padding_lg, 16, theme.metrics.padding_lg, 4)
        layout.setSpacing(16)

        side_w = 190
        brand = QWidget()
        brand.setFixedWidth(side_w)
        brand_l = QHBoxLayout(brand)
        brand_l.setContentsMargins(0, 0, 0, 0)
        brand_l.setSpacing(10)
        icon_path = _asset_path(self.root, "icon.ico")
        if icon_path.exists():
            logo = QLabel()
            logo.setPixmap(QIcon(str(icon_path)).pixmap(28, 28))
            brand_l.addWidget(logo)
        title = QLabel("FlowZap")
        title.setObjectName("brand")
        brand_l.addWidget(title)
        brand_l.addStretch(1)
        layout.addWidget(brand)

        layout.addStretch(1)
        self._tabs = TabBar(TABS)
        self._tabs.currentChanged.connect(self.show_tab)
        layout.addWidget(self._tabs)
        # Точка «есть новая версия службы» на «Настройках» — сразу при старте
        QTimer.singleShot(0, lambda: self.settings.refresh_service_status())
        layout.addStretch(1)

        right = QWidget()
        right.setFixedWidth(side_w)
        right_l = QHBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(6)
        right_l.addStretch(1)
        admin = _is_admin()
        from core.service import client as service_client
        via_service = not admin and service_client.service_installed()
        ok = admin or via_service
        right_l.addWidget(label(Glyph.SHIELD if ok else Glyph.WARNING, role="glyph",
                                tone="muted" if ok else "warning"))
        text = "Администратор" if admin else ("Фоновая служба" if via_service else "Без прав администратора")
        right_l.addWidget(label(text, role="hint", tone=None if ok else "warning"))
        if via_service:
            right.setToolTip("Обход и DNS работают через фоновую службу FlowZap — права администратора не нужны.")
        elif not admin:
            right.setToolTip("Нужна фоновая служба FlowZap: «Настройки» → «Фоновая служба» → «Установить».")
        layout.addWidget(right)
        return bar

    def show_tab(self, index: int):
        changed = index != self.stack.currentIndex()
        self.stack.setCurrentIndex(index)
        # Блоки новой страницы всплывают по очереди (каскад, как в макете)
        if changed and self.isVisible():
            cascade_in(page_steps(self.stack.currentWidget()))
        # Сначала отрисовать новую страницу (~10 мс), потом запускать «таблетку»
        # вкладок — иначе первый кадр её анимации пропадает и она дёргается.
        if self.isVisible():
            self.centralWidget().repaint()
        self._tabs.set_current(index)

    # ── Автопроверка обновлений ─────────────────────

    def _start_update_autocheck(self) -> None:
        """updater.check_on_start управляет и первой проверкой (через 3 с
        после старта, чтобы не тормозить открытие окна), и периодической
        (раз в 3 часа). Выключено — значит фоновых сетевых запросов к
        GitHub нет вообще, только проверка руками на вкладке
        «Обновления». Сама вкладка «Обновления» и точка на кнопке
        считают обновления независимо друг от друга (см. docstring
        ui/tabs/updates.py) — это разные, дублирующие друг друга проверки
        по репозиториям, а не общий кэш."""
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._check_updates_bg)
        if not self.config.get("updater", {}).get("check_on_start", True):
            return
        QTimer.singleShot(UPDATE_CHECK_STARTUP_DELAY_MS, self._check_updates_bg)
        self._update_timer.start(UPDATE_CHECK_INTERVAL_MS)

    def set_update_autocheck(self, enabled: bool) -> None:
        """Переключатель «Проверять автоматически» на вкладке «Обновления»."""
        if enabled:
            self._update_timer.start(UPDATE_CHECK_INTERVAL_MS)
            self._check_updates_bg()
        else:
            self._update_timer.stop()

    def _check_updates_bg(self) -> None:
        threading.Thread(target=self._do_check_updates, daemon=True, name="update-checker").start()

    def _do_check_updates(self) -> None:
        """Проверяет три репозитория (GUI, Core, TG Proxy) и решает, надо
        ли показывать точку на кнопке «Обновления». Синхронные вызовы
        get_latest_release — сами уже в фоновом потоке (_check_updates_bg),
        свой отдельный поток на каждый репозиторий не нужен. Ошибка по
        любому из трёх не должна прятать обновление по остальным."""
        has_update = False
        releases = {}

        try:
            release = get_latest_release(FLOWZAP_REPO)
            releases["gui"] = release
            if release:
                tag = release.get("tag_name", "")
                if gui_update_available(GUI_VERSION, tag) and find_exe_asset(release) is not None:
                    has_update = True
        except Exception:
            log.exception("Автопроверка обновлений: ошибка при проверке FlowZap")

        try:
            # Точку показываем только для установленного Core, как и для TG
            # Proxy: отсутствие компонента — не обновление (он скачается при
            # первом запуске обхода или кнопкой «Установить»).
            installed = (get_installed_core_version(self.dashboard.zapret_dir) or "").lstrip("vV")
            if installed:
                release = get_latest_release(CORE_REPO)
                releases["core"] = release
                if release and installed != release.get("tag_name", "").lstrip("vV"):
                    has_update = True
        except Exception:
            log.exception("Автопроверка обновлений: ошибка при проверке Core")

        try:
            # Только если TG Proxy установлен — см. Core выше.
            tg_dir = self.root / "tgproxy"
            installed = (get_installed_tg_proxy_version(tg_dir) or "").lstrip("vV")
            if installed:
                release = get_latest_release(TG_PROXY_REPO)
                releases["tg"] = release
                if release and installed != release.get("tag_name", "").lstrip("vV"):
                    has_update = True
        except Exception:
            log.exception("Автопроверка обновлений: ошибка при проверке TG Proxy")

        self._updatesFound.emit(has_update)
        self._releasesFetched.emit(releases)

    def _set_updates_dot(self, visible: bool) -> None:
        self._tabs.set_badge(_UPDATES_TAB, visible)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        icon_path = _asset_path(self.root, "icon.ico")
        if icon_path.exists():
            self.tray.setIcon(QIcon(str(icon_path)))

        menu = QMenu()
        menu.addAction("Открыть").triggered.connect(self._restore_from_tray)
        menu.addAction("Выход").triggered.connect(self._quit)
        self.tray.setContextMenu(menu)

        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def wake(self) -> None:
        """Поднять окно поверх остальных — вызывается из main.py, когда
        второй запущенный экземпляр FlowZap достучался до этого через
        сокет single-instance (порт 58392, см. main.py)."""
        self._restore_from_tray()

    def relaunch_as_admin(self) -> None:
        """Запасной режим: перезапустить FlowZap с правами администратора
        (окно UAC) — обход тогда запускается напрямую, без службы. Новый
        экземпляр (--relaunched) дождётся, пока этот закроется."""
        import ctypes
        import subprocess
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, ["--relaunched"]
        else:
            exe, args = sys.executable, [str(Path(sys.argv[0]).resolve()), "--relaunched"]
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, subprocess.list2cmdline(args), str(Path(exe).parent), 1)
        if ret > 32:
            self._quit()
        else:
            log.warning(f"Перезапуск от администратора не удался ({ret})")

    def _quit(self):
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if self.minimize_to_tray:
            event.ignore()
            self.hide()
        else:
            self.tray.hide()
            event.accept()
            QApplication.quit()
