"""
main.py
-------
Точка входа FlowZap.
"""

import sys
import io

# sys.stdout/stderr отсутствуют в noconsole-сборке PyInstaller — многие
# библиотеки не переживают print() в None, поэтому подменяем их пустым
# буфером. _HAS_CONSOLE запоминает, был ли поток настоящим, ДО подмены:
# используется ниже в setup_logging(), чтобы не вешать на этот буфер
# StreamHandler — иначе он копил бы в памяти каждую строку лога на всё
# время работы приложения в трее без предела.
_HAS_CONSOLE = sys.stdout is not None
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# Скрытый процесс сервера TG WS Proxy (core/tgproxy/host.py) — тот же exe,
# но без интерфейса: PySide6 и всё остальное ниже ему не нужны.
if len(sys.argv) > 1 and sys.argv[1] in ("--tgproxy", "--tgproxy-probe"):
    from core.tgproxy.host import run as _run_tgproxy
    sys.exit(_run_tgproxy(sys.argv[1:]))

import faulthandler
import logging
import threading
import time
import tomllib
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent

CONFIG_PATH = ROOT / "config.toml"

# Порт-«замок» single-instance (см. _acquire_single_instance) — только
# 127.0.0.1, наружу не смотрит.
SINGLE_INSTANCE_PORT = 58392

_crash_log_file = None  # держим открытым на всё время работы — нужен faulthandler.enable(file=...)
_error_dialog_shown = False  # не более одного окна с ошибкой за сессию


class _ErrorDialog(QObject):
    """Окно «непредвиденная ошибка» — только из GUI-потока: виджет, созданный
    в фоновом потоке (DNS, TG Proxy, загрузки…), мог уронить весь FlowZap
    вместо сообщения. Объект живёт в главном потоке, сигнал из любого потока
    доставляется туда очередью."""
    requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.requested.connect(self._show)

    @Slot(str)
    def _show(self, crash_log_path: str) -> None:
        QMessageBox.critical(
            None, "FlowZap — ошибка",
            "Произошла непредвиденная ошибка. Подробности сохранены "
            f"в {crash_log_path}.\n\n"
            "Приложение может продолжить работу, но лучше его "
            "перезапустить.",
        )


_error_dialog: "_ErrorDialog | None" = None


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "flowzap.log"
    level = logging.INFO if getattr(sys, "frozen", False) else logging.DEBUG
    # С ограничением: без него flowzap.log рос без конца (~300 КБ в день).
    # Прежние части — в logs/archive/, в logs/ только текущие файлы
    from core.system.logfiles import ArchivedLogHandler, tidy_old_backups
    tidy_old_backups(log_dir)
    handlers = [ArchivedLogHandler(log_file, max_bytes=2 * 1024 * 1024, backups=2)]
    if _HAS_CONSOLE:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def setup_crash_handling(log_dir: Path) -> None:
    """faulthandler — на случай падения самого интерпретатора (сегфолт и
    т.п.), которое sys.excepthook не ловит. sys.excepthook и
    threading.excepthook — на обычные необработанные исключения в
    главном потоке и в фоновых threading.Thread. Диалог с ошибкой
    показывается не более одного раза за сессию — иначе повторяющееся
    исключение (например, в таймере) засыпало бы окнами."""
    global _crash_log_file, _error_dialog
    _error_dialog = _ErrorDialog()
    log_dir.mkdir(parents=True, exist_ok=True)
    crash_log_path = log_dir / "crash.log"
    _crash_log_file = open(crash_log_path, "a", encoding="utf-8")
    faulthandler.enable(file=_crash_log_file)

    def _report(exc_type, exc_value, exc_tb) -> None:
        global _error_dialog_shown
        logging.getLogger("flowzap.crash").error(
            "Необработанное исключение", exc_info=(exc_type, exc_value, exc_tb)
        )
        import traceback
        traceback.print_exception(exc_type, exc_value, exc_tb, file=_crash_log_file)
        _crash_log_file.flush()

        if not _error_dialog_shown and QApplication.instance() is not None:
            _error_dialog_shown = True
            _error_dialog.requested.emit(str(crash_log_path))

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        _report(exc_type, exc_value, exc_tb)

    def _thread_excepthook(args) -> None:
        _report(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def _try_wake_existing_instance() -> bool:
    """Стучится в уже запущенный экземпляр по SINGLE_INSTANCE_PORT.
    True — тот ответил и поднял своё окно, этот экземпляр может выходить."""
    sock = QTcpSocket()
    sock.connectToHost(QHostAddress.LocalHost, SINGLE_INSTANCE_PORT)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"WAKEUP")
    sock.waitForBytesWritten(500)
    if not sock.waitForReadyRead(1000):
        sock.close()
        return False
    reply = bytes(sock.readAll())
    sock.close()
    return reply == b"OK"


def _just_updated_from_old_version() -> bool:
    """Нас только что поставил скрипт обновления FlowZap до 1.0 (.bat): он
    запускает новую версию сразу, не дожидаясь, пока старая закроется (она
    выходит через ~2 с после «готово»). Будить старую нельзя — она вот-вот
    закроется, и не останется ни одной. Признак — свежая запись «Update
    applied» в logs/update.log: .bat пишет её прямо перед запуском."""
    log = ROOT / "logs" / "update.log"
    try:
        if time.time() - log.stat().st_mtime > 60:
            return False
        lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        return bool(lines) and "Update applied" in lines[-1]
    except OSError:
        return False


def _acquire_single_instance() -> "QTcpServer | None":
    """None — этот процесс должен завершиться (либо разбудил уже
    запущенный экземпляр, либо порт занят кем-то ещё и достучаться не
    удалось). Иначе — сервер, который слушает WAKEUP от следующих
    запусков; ссылку на него нужно держать живой, пока работает app.

    --relaunched — FlowZap перезапустил сам себя от администратора: старый
    экземпляр как раз закрывается (сбрасывает DNS, останавливает обход) —
    ждём его, а не будим."""
    server = QTcpServer()
    if server.listen(QHostAddress.LocalHost, SINGLE_INSTANCE_PORT):
        return server
    if "--relaunched" in sys.argv or _just_updated_from_old_version():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if server.listen(QHostAddress.LocalHost, SINGLE_INSTANCE_PORT):
                return server

    if _try_wake_existing_instance():
        return None

    QMessageBox.warning(
        None, "FlowZap",
        "FlowZap уже запущен, но не отвечает (или порт "
        f"{SINGLE_INSTANCE_PORT} занят другой программой).\n\n"
        "Закройте существующий процесс FlowZap в Диспетчере задач "
        "и запустите программу снова.",
    )
    return None


def load_config(config_path: Path) -> dict:
    from core.dns.builtin import apply_builtin_dns
    from ui.theme import DEFAULT_THEME     # без Qt — модуль только с цветами
    defaults = {
        "zapret": {
            "exe_path": str(ROOT / "zapret" / "bin" / "winws.exe"),
            "presets_dir": str(ROOT / "zapret"),
            "last_preset": "general",
            "args": [],
            "autostart": False,
        },
        "ui":      {"theme": DEFAULT_THEME, "remember_tab": True},
        "updater": {"repo": "Flowseal/zapret-discord-youtube", "check_on_start": True},
        "dns": {"pairs": []},
    }
    try:
        user_cfg = {}
        if config_path.exists():
            with open(config_path, "rb") as f:
                user_cfg = tomllib.load(f)
        for section, values in user_cfg.items():
            if section in defaults and isinstance(values, dict):
                defaults[section].update(values)
            else:
                defaults[section] = values
        # dns.servers — формат до списка пар; адреса из него были адресами
        # xbox-dns, а он теперь встроенный — ключ просто убираем
        defaults.get("dns", {}).pop("servers", None)
    except Exception as exc:
        # Дальше FlowZap работает с настройками по умолчанию и при первом
        # сохранении перезапишет файл — испорченный (обрезан, опечатка после
        # ручной правки) откладываем в сторону, иначе пропали бы секрет TG,
        # DNS-серверы и списки
        backup = config_path.with_name(f"config.broken-{time.strftime('%Y%m%d-%H%M%S')}.toml")
        try:
            config_path.replace(backup)
            note = f" — файл сохранён как {backup.name}, FlowZap начал с настроек по умолчанию"
            defaults["_config_broken"] = backup.name
        except OSError:
            note = ""
        logging.getLogger(__name__).error(f"Ошибка чтения config.toml: {exc}{note}")
    # Встроенные DNS прописаны в приложении (core/dns/builtin.py): пропавшие
    # возвращаются, новые из этой версии добавляются, удалённые — убираются
    apply_builtin_dns(defaults)
    # TG Proxy: недостающие настройки; новый секрет — сохранить сразу (main())
    from core.tgproxy.settings import ensure_config
    if ensure_config(defaults):
        defaults["_tgproxy_new_secret"] = True
    return defaults


def _migrate_win_autostart_async() -> None:
    """Задача Планировщика от FlowZap до 1.0 → запись в реестре (если сейчас
    хватает прав её удалить; иначе это сделает установка фоновой службы).
    В фоне: schtasks может думать несколько секунд."""

    def _worker() -> None:
        try:
            from core.system.autostart import migrate_legacy_autostart, repair_autostart_path
            migrate_legacy_autostart()
            repair_autostart_path()
        except Exception:
            logging.getLogger("flowzap").exception("Ошибка миграции автозапуска Windows")

    threading.Thread(target=_worker, daemon=True, name="autostart-migrate").start()


def main() -> None:
    setup_logging(ROOT / "logs")
    setup_crash_handling(ROOT / "logs")
    log = logging.getLogger("flowzap")
    log.info("─── FlowZap запускается ───")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # закрытие окна сворачивает в трей, не завершает процесс

    # Single-instance — до создания manager/MainWindow: если это второй
    # запуск и первый экземпляр отозвался, второму дальше делать нечего.
    instance_server = _acquire_single_instance()
    if instance_server is None:
        sys.exit(0)

    # Если прошлое обновление прошло успешно (раз мы вообще дошли сюда
    # единственным экземпляром), .old-хвосты больше не нужны — откатывать
    # уже нечего.
    from core.updates.app import cleanup_old_update_leftovers
    cleanup_old_update_leftovers(ROOT)

    config = load_config(CONFIG_PATH)
    config["_app_dir"] = str(ROOT)

    from ui.theme import DEFAULT_THEME, theme
    theme.set_theme(config.get("ui", {}).get("theme", DEFAULT_THEME))

    from core.updates.releases import set_github_token
    from core.zapret.game_lists import update_gaming_lists
    gh_token = config.get("github", {}).get("token", "")
    if gh_token:
        set_github_token(gh_token)
        log.info("GitHub токен установлен")

    from core.zapret.manager import ZapretManager
    _exe_raw = Path(config["zapret"]["exe_path"])
    zapret_exe = _exe_raw if _exe_raw.is_absolute() else ROOT / _exe_raw
    manager = ZapretManager(zapret_exe=zapret_exe)

    from ui.main_window import MainWindow
    window = MainWindow(root=ROOT, config=config, config_path=CONFIG_PATH, manager=manager)
    broken = config.pop("_config_broken", None)
    if broken:
        QMessageBox.warning(
            None, "FlowZap",
            "Файл настроек config.toml повреждён или в нём ошибка — FlowZap запущен "
            "с настройками по умолчанию.\n\n"
            f"Прежний файл сохранён рядом как {broken}: "
            "из него можно вернуть свои DNS-серверы и секрет TG Proxy.",
        )
    if config.pop("_tgproxy_new_secret", False):
        # Иначе при следующем запуске секрет был бы другим, и прокси,
        # добавленный в Telegram, перестал бы подключаться
        window.save_config()

    _migrate_win_autostart_async()
    window.start_builtin_dns_sync()
    window.start_announcements_fetch()

    def _on_wake_request() -> None:
        """Второй экземпляр достучался через single-instance порт —
        поднимаем окно и отвечаем ему, чтобы он мог спокойно выйти."""
        while instance_server.hasPendingConnections():
            conn = instance_server.nextPendingConnection()
            if conn.waitForReadyRead(500) and bytes(conn.readAll()) == b"WAKEUP":
                conn.write(b"OK")
                conn.waitForBytesWritten(500)
                window.wake()
            conn.disconnectFromHost()
            conn.deleteLater()

    instance_server.newConnection.connect(_on_wake_request)

    window.show()

    # Игровые списки (Game Filter) — только если Core уже установлен;
    # внутри своя проверка кэша на 6 часов, не качает при каждом старте.
    zapret_dir = Path(config["_app_dir"]) / config.get("zapret", {}).get("presets_dir", "zapret")
    if (zapret_dir / "bin" / "winws.exe").exists():
        update_gaming_lists(zapret_dir / "lists")

    log.info("UI готов, запуск event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


