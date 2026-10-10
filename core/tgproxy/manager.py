"""
core/tgproxy/manager.py
-----------------------
TG WS Proxy — локальный MTProto-прокси для Telegram (Flowseal/tg-ws-proxy).

Сервер работает в скрытом процессе FlowZap (core/tgproxy/host.py) из кода в
<app_root>/tgproxy/src, настройки — config["tgproxy"] (core/tgproxy/settings.py).
Своего окна, значка в трее и самообновления у него нет: всё управляется
отсюда, обновляется — вкладкой «Обновления» (core/updates/tgproxy.py).

Подключён ли Telegram — по таблице TCP-соединений: к порту сервера есть
живые соединения, и видно, какая программа их открыла.

До 1.1 FlowZap запускал готовый exe автора (TgWsProxy_windows.exe в
tgproxy/) — migrate_legacy() его останавливает и удаляет.
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from core.system import tcp, winproc
from core.tgproxy import settings as tg_settings
from core.tgproxy.host import CREATE_NO_WINDOW, probe, serve_command

logger = logging.getLogger(__name__)

PROXY_DIR_NAME = "tgproxy"
LEGACY_EXE = "TgWsProxy_windows.exe"
_LEGACY_AUTOSTART = "TgWsProxy"         # значение в HKCU\...\Run у exe автора

# Сколько ждать, пока сервер откроет порт: импорт библиотек в новом
# процессе на медленном диске — пара секунд
_START_TIMEOUT_SEC = 15

# Клиенты Telegram для компьютера: если такой запущен, а к прокси никто не
# подключён — Telegram ходит мимо прокси
TELEGRAM_APPS = frozenset(name.lower() for name in (
    "Telegram.exe", "AyuGram.exe", "Kotatogram.exe", "64Gram.exe",
    "Unigram.exe", "materialgram.exe", "Forkgram.exe",
))


def src_dir(tgproxy_dir: Path) -> Path:
    return tgproxy_dir / "src"


def is_installed(tgproxy_dir: Path) -> bool:
    return (src_dir(tgproxy_dir) / "proxy" / "tg_ws_proxy.py").is_file()


def client_title(exe: str) -> str:
    """«Telegram.exe» → «Telegram»; пусто (соединение с другого устройства) — «другое устройство»."""
    if not exe:
        return "другое устройство"
    return exe[:-4] if exe.lower().endswith(".exe") else exe


def _last_log_line(log_file: Path) -> str:
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    for line in reversed(lines[-40:]):
        if "ERROR" in line or "Error" in line or "error" in line:
            return line.strip()
    return lines[-1].strip() if lines else ""


_CLIENT_LINE = re.compile(r"\[(?P<host>[0-9a-fA-F.:]+):\d+\]\s+(?P<msg>.*)")
REJECT_WINDOW_SEC = 20     # отказ «свежий», пока с него прошло меньше


class _RejectWatch:
    """Кому прокси отказывает. Таблица TCP показывает только, что клиент
    открыл соединение, а не что его пустили: Telegram со старым секретом
    подключается каждые несколько секунд, сервер пишет в лог «bad handshake»,
    а FlowZap считал Telegram подключённым (2026-10-09 — часами).
    Дочитывает новые строки tgproxy.log; для каждого адреса помнит время
    последнего отказа и последнего нормального соединения."""

    def __init__(self, log_file: Path) -> None:
        self._log_file = log_file
        self._offset = 0
        self._tail = b""
        self._bad: dict[str, float] = {}
        self._good: dict[str, float] = {}

    def reset(self) -> None:
        """Перед запуском сервера: прошлые запуски не считаем."""
        try:
            self._offset = self._log_file.stat().st_size
        except OSError:
            self._offset = 0
        self._tail = b""
        self._bad.clear()
        self._good.clear()

    def _read(self) -> None:
        try:
            size = self._log_file.stat().st_size
            if size < self._offset:          # лог начали заново
                self._offset, self._tail = 0, b""
            if size == self._offset:
                return
            with open(self._log_file, "rb") as f:
                f.seek(self._offset)
                data = f.read(size - self._offset)
        except OSError:
            return
        self._offset += len(data)
        *lines, self._tail = (self._tail + data).split(b"\n")
        now = time.monotonic()
        for raw in lines:
            m = _CLIENT_LINE.search(raw.decode("utf-8", errors="replace"))
            if not m:
                continue
            msg = m["msg"]
            if "bad handshake" in msg:
                self._bad[m["host"]] = now
            # «handshake ok» (подробный лог) и строки уже пущенного соединения —
            # пустили; «timeout during handshake» — ни то ни другое
            elif "handshake ok" in msg or "handshake" not in msg:
                self._good[m["host"]] = now

    def rejected(self) -> set[str]:
        """Адреса, которым прокси недавно отказал и с тех пор ни разу не пустил."""
        self._read()
        now = time.monotonic()
        return {host for host, t in self._bad.items()
                if now - t < REJECT_WINDOW_SEC and self._good.get(host, 0) < t}


def _same_path(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(str(b)))


def migrate_legacy(tgproxy_dir: Path) -> None:
    """Убрать exe автора, который ставил FlowZap до 1.1: процесс (только
    запущенный из нашей папки — отдельно установленный TgWsProxy не трогаем),
    файл и его автозапуск, если тот указывает на нашу папку. Синхронно."""
    exe = tgproxy_dir / LEGACY_EXE
    if exe.exists():
        for pid, name in winproc.process_names().items():
            if name.lower() == LEGACY_EXE.lower():
                path = winproc.image_path(pid)
                if path and _same_path(path, exe):
                    logger.info(f"Останавливаю старый TgWsProxy из папки FlowZap (PID {pid})")
                    winproc.kill_pid(pid)
        for _ in range(10):
            try:
                exe.unlink()
                logger.info("Старый TgWsProxy_windows.exe удалён — прокси теперь встроен в FlowZap")
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.3)       # процесс ещё не отпустил файл
        else:
            logger.warning("Не удалось удалить старый TgWsProxy_windows.exe — попробую при следующем запуске")
        # Версия в version.txt была версией exe; кода сервера ещё нет — не установлен
        if not is_installed(tgproxy_dir):
            try:
                (tgproxy_dir / "version.txt").unlink(missing_ok=True)
            except OSError:
                pass
    _remove_legacy_autostart(exe)


def _remove_legacy_autostart(exe: Path) -> None:
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, _LEGACY_AUTOSTART)
            if os.path.normcase(str(exe)) in os.path.normcase(str(value)):
                winreg.DeleteValue(key, _LEGACY_AUTOSTART)
                logger.info("Удалён автозапуск старого TgWsProxy из папки FlowZap")
    except OSError:
        pass        # записи нет — и хорошо


class TgProxyManager:
    """Процесс сервера TG WS Proxy. Синхронные методы — для фоновых потоков
    и выхода из приложения; UI зовёт *_async."""

    def __init__(self, app_root: Path, config: dict) -> None:
        self._dir = app_root / PROXY_DIR_NAME
        self._log_file = app_root / "logs" / "tgproxy.log"
        self._rejects = _RejectWatch(self._log_file)
        self._config = config
        tg_settings.ensure_config(config)
        self._proc: Optional[subprocess.Popen] = None
        self._server_pid: Optional[int] = None     # кто слушает порт (обычно = _proc.pid)
        self._port: Optional[int] = None
        self._lock = threading.Lock()

    # ── Состояние ────────────────────────────────────────────────────────

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def log_file(self) -> Path:
        return self._log_file

    @property
    def settings(self) -> dict:
        return self._config["tgproxy"]

    @property
    def is_available(self) -> bool:
        return is_installed(self._dir)

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Запуск и остановка ───────────────────────────────────────────────

    def _options(self) -> Optional[set[str]]:
        """Параметры, которые знает установленная версия сервера (options.json
        пишет установка; нет файла — спрашиваем сервер сейчас)."""
        src = src_dir(self._dir)
        path = src / "options.json"
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = probe(src)
            if info.get("error"):
                logger.error(f"TG WS Proxy не запускается: {info['error']}")
                return None
            try:
                path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        options = info.get("options") if isinstance(info, dict) else None
        return set(options) if isinstance(options, list) else None

    def start(self) -> str:
        """Запустить сервер и дождаться, пока он откроет порт. "" — работает,
        иначе текст ошибки для пользователя. Синхронный (до ~15 с)."""
        with self._lock:
            if self.is_running:
                return ""
            if not self.is_available:
                return "TG WS Proxy не установлен"
            tg = self.settings
            port = tg["port"]

            options = self._options()
            if options is None:
                return ("Установленная версия TG WS Proxy не запускается — переустановите "
                        "её во вкладке «Обновления»")
            args, skipped = tg_settings.build_args(tg, options)
            missing = [o for o in tg_settings.REQUIRED_OPTIONS if o in skipped]
            if missing:
                return (f"Эта версия TG WS Proxy не понимает {', '.join(missing)} — "
                        "нужна новая версия FlowZap")
            if skipped:
                logger.warning(f"TG WS Proxy этой версии не знает {', '.join(skipped)} — "
                               "эти настройки не применены")

            try:
                busy = tcp.listener_pid(port)
            except OSError as e:
                logger.warning(f"Таблица соединений недоступна: {e}")
                busy = None
            if busy is not None:
                name = winproc.process_names().get(busy, "")
                who = client_title(name) if name else "другая программа"
                return (f"Порт {port} занят ({who}). Закройте её или смените порт "
                        "в «Параметрах»")

            self._rejects.reset()
            try:
                self._proc = subprocess.Popen(
                    serve_command(src_dir(self._dir), self._log_file, args),
                    cwd=str(self._dir),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
            except OSError as e:
                logger.error(f"Ошибка запуска TG WS Proxy: {e}")
                self._proc = None
                return "Не удалось запустить TG WS Proxy — подробности в логе"

            deadline = time.monotonic() + _START_TIMEOUT_SEC
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    reason = _last_log_line(self._log_file)
                    logger.error(f"TG WS Proxy завершился при запуске (код {self._proc.returncode}): {reason}")
                    self._proc = None
                    return "TG WS Proxy не запустился — подробности в logs/tgproxy.log"
                try:
                    pid = tcp.listener_pid(port)
                except OSError:
                    pid = None
                if pid is not None:
                    # Порт до запуска был свободен — слушает наш сервер (из
                    # исходников python.exe может оказаться посредником, и
                    # слушает тогда его дочерний процесс)
                    self._server_pid, self._port = pid, port
                    logger.info(f"TG WS Proxy работает на порту {port} (PID {pid})")
                    return ""
                time.sleep(0.15)

            logger.error(f"TG WS Proxy не открыл порт {port} за {_START_TIMEOUT_SEC} с")
            self._stop_locked()
            return f"TG WS Proxy не открыл порт {port} — подробности в logs/tgproxy.log"

    def _stop_locked(self) -> None:
        proc, server_pid = self._proc, self._server_pid
        self._proc = self._server_pid = self._port = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
        if server_pid is not None and proc is not None and server_pid != proc.pid:
            winproc.kill_pid(server_pid)

    def stop(self) -> None:
        with self._lock:
            was_running = self.is_running
            self._stop_locked()
            if was_running:
                logger.info("TG WS Proxy остановлен")

    def stop_all(self) -> str:
        """Синхронно остановить сервер (выход из приложения). "" — остановлен."""
        self.stop()
        return ""

    def set_running_async(self, enable: bool, on_done: Callable[[bool, str], None]) -> None:
        """Включить/выключить. on_done(running, error) из фонового потока:
        running — ФАКТИЧЕСКОЕ состояние после операции."""

        def _worker() -> None:
            if enable:
                error = self.start()
                on_done(not error, error)
            else:
                self.stop()
                on_done(False, "")

        threading.Thread(target=_worker, daemon=True, name="tgproxy-toggle").start()

    def restart_async(self, on_done: Callable[[bool, str], None]) -> None:
        """Перезапустить с новыми настройками. on_done(running, error)."""

        def _worker() -> None:
            self.stop()
            error = self.start()
            on_done(not error, error)

        threading.Thread(target=_worker, daemon=True, name="tgproxy-restart").start()

    def migrate_legacy_async(self, on_done: Callable[[], None]) -> None:
        def _worker() -> None:
            try:
                migrate_legacy(self._dir)
            except Exception:
                logger.exception("Ошибка перехода со старого TgWsProxy")
            on_done()

        threading.Thread(target=_worker, daemon=True, name="tgproxy-migrate").start()

    # ── Кто подключён ────────────────────────────────────────────────────

    def _telegram_process(self, names: dict[int, str]) -> Optional[int]:
        """PID запущенного клиента Telegram (известного или того, что уже
        подключался к прокси) или None."""
        known = TELEGRAM_APPS | ({self.settings.get("client", "").lower()} - {""})
        return next((pid for pid, name in names.items() if name.lower() in known), None)

    def poll_clients(self) -> tuple[bool, list[str], bool, str, set[str]]:
        """(жив ли сервер; exe клиентов, подключённых к нему — "" для
        соединений с других устройств; запущен ли на компьютере Telegram;
        путь к его exe или ""; адреса, которым сервер отказывает — неверный
        секрет, см. _RejectWatch). Telegram смотрим и при выключенном прокси —
        точка на плитке показывает, идёт ли он мимо прокси. Синхронный,
        миллисекунды."""
        names = winproc.process_names()
        app_pid = self._telegram_process(names)
        app_path = (winproc.image_path(app_pid) or "") if app_pid is not None else ""
        app_running = app_pid is not None

        server_pid, port = self._server_pid, self._port
        if server_pid is None or port is None or not self.is_running:
            return False, [], app_running, app_path, set()
        rejected = self._rejects.rejected()
        try:
            conns = tcp.connections()
        except OSError as e:
            logger.debug(f"Таблица соединений недоступна: {e}")
            return True, [], app_running, app_path, rejected
        # Соединение видно дважды: со стороны сервера (наш порт) и со стороны
        # клиента — по его адресу находим, чей это процесс
        owners = {c.local: c.pid for c in conns if c.state == tcp.ESTABLISHED}
        clients = []
        for c in conns:
            if c.state == tcp.ESTABLISHED and c.pid == server_pid and c.local[1] == port:
                client_pid = owners.get(c.remote)
                clients.append(names.get(client_pid, "") if client_pid is not None else "")
        return True, clients, app_running, app_path, rejected

    def poll_async(self, on_done: Callable[[bool, list[str], bool, str, set[str]], None]) -> None:
        threading.Thread(target=lambda: on_done(*self.poll_clients()),
                         daemon=True, name="tgproxy-poll").start()

    def launch_telegram(self) -> None:
        """Запустить Telegram, если он ещё не запущен: по пути, запомненному,
        когда он работал (так находятся и AyuGram, и нестандартная папка),
        иначе из обычного места установки. Не нашли (например, версия из
        Microsoft Store) — открываем tg://, Windows сама откроет Telegram."""
        if self._telegram_process(winproc.process_names()) is not None:
            return
        candidates = [self.settings.get("client_path", ""),
                      os.path.join(os.environ.get("APPDATA", ""), "Telegram Desktop", "Telegram.exe")]
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    subprocess.Popen([path], cwd=os.path.dirname(path), close_fds=True,
                                     creationflags=subprocess.DETACHED_PROCESS)
                    logger.info(f"Запущен Telegram: {path}")
                    return
                except OSError as e:
                    logger.warning(f"Не удалось запустить {path}: {e}")
        logger.info("Telegram не найден на диске — открываем tg://")
        webbrowser.open("tg://")

    def launch_telegram_async(self) -> None:
        threading.Thread(target=self.launch_telegram, daemon=True, name="tgproxy-launch-tg").start()

    # ── Подключение Telegram ─────────────────────────────────────────────

    def open_in_telegram(self) -> None:
        """Открыть tg://proxy — Telegram спросит «Подключить». Для Telegram на
        этом компьютере адрес всегда 127.0.0.1: адрес в сети может смениться."""
        logger.info("Открываем Telegram: tg://proxy (127.0.0.1)")
        webbrowser.open(tg_settings.tg_link(self.settings))

    def share_link(self) -> str:
        """Ссылка для копирования: с доступом из сети — с адресом компьютера в
        сети (для телефона), иначе 127.0.0.1."""
        tg = self.settings
        host = (tg_settings.lan_address() if tg.get("lan") else None) or tg_settings.LOCAL_HOST
        return tg_settings.tg_link(tg, host)

    def phone_link(self) -> Optional[str]:
        """Ссылка для QR-кода: tg:// с адресом компьютера в сети. Не t.me —
        камера открывает её через браузер, а t.me часто заблокирован (страница
        висит); tg:// камера Android передаёт прямо в Telegram (проверено).
        None — компьютер не в сети (адрес не определить)."""
        host = tg_settings.lan_address()
        return tg_settings.tg_link(self.settings, host) if host else None
