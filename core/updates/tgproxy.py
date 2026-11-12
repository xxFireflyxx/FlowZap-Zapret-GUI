"""
core/updates/tgproxy.py
-----------------------
Установка и обновление TG WS Proxy (Flowseal/tg-ws-proxy, лицензия MIT).

Ставится не exe автора (это приложение со своим значком в трее, окнами и
самообновлением), а только код самого сервера — папки proxy/ и utils/ из
исходников релиза — в tgproxy/src. Запускает его FlowZap в скрытом
процессе (core/tgproxy/host.py), библиотеки сервера (cryptography, httpx,
h2) входят в сборку FlowZap.

Перед заменой новая версия проходит пробный запуск: если ей нужно то, чего
в этой сборке FlowZap нет, она не ставится, а установленная продолжает
работать. Архив — с GitHub, зеркало — SourceForge (тот же архив).
"""
import io
import json
import logging
import shutil
import threading
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Optional

from core.tgproxy.host import probe
from core.tgproxy.manager import is_installed, migrate_legacy, src_dir
from core.updates.releases import download_asset, latest_release_for_user, run_install, sourceforge_url
from core.updates import component_transaction as transaction

logger = logging.getLogger(__name__)

TG_PROXY_REPO = "Flowseal/tg-ws-proxy"

# Зеркало на SourceForge: там лежит и архив исходников каждого релиза
SOURCEFORGE_TGPROXY_PROJECT = "tg-ws-proxy.mirror"

# Что берём из исходников: сервер, его вспомогательный пакет и лицензию
_KEEP_DIRS = ("proxy", "utils")
_KEEP_FILES = ("LICENSE",)

# Установку могут начать и главная (первое включение), и «Обновления»
_install_lock = threading.Lock()
_commit_lock = threading.Lock()
_exit_requested = threading.Event()
_COMPONENT = "Telegram Proxy"


def is_installing() -> bool:
    return _install_lock.locked()


def request_exit() -> None:
    """Отменить подготовку, не ожидая критической секции."""
    _exit_requested.set()


def finish_before_exit() -> None:
    """Не начинать новую замену и дождаться уже начатой замены/отката."""
    request_exit()
    with _commit_lock:
        pass


def _check_cancelled() -> None:
    if _exit_requested.is_set():
        raise ValueError("Обновление Telegram Proxy прервано при выходе")


def _check_source_path(path: Path) -> None:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        logger.error("Папка Telegram Proxy является ссылкой: %s", path)
        raise ValueError("Не удалось безопасно обновить Telegram Proxy — подробности в логе")


def recover_interrupted_update(tgproxy_dir: Path) -> None:
    """До запуска прокси: вернуть целую установку, включая её номер версии."""
    with _commit_lock:
        transaction.recover(tgproxy_dir, _COMPONENT)
        # Совместимость с прежним обновлением, которое заменяло только src.
        old = tgproxy_dir / "src.old"
        source = src_dir(tgproxy_dir)
        if old.exists() and not source.exists():
            _check_source_path(tgproxy_dir)
            _check_source_path(old)
            if not (old / "proxy" / "tg_ws_proxy.py").is_file():
                raise ValueError("Не удалось восстановить Telegram Proxy — резервная копия сохранена")
            old.rename(source)
            logger.warning("Восстановлен Telegram Proxy после прерывания прежнего установщика")


def get_installed_tg_proxy_version(tgproxy_dir: Path) -> Optional[str]:
    """Тег установленной версии или None, если сервер не установлен
    (version.txt без кода — остаток старой схемы, не считается)."""
    if not is_installed(tgproxy_dir):
        return None
    try:
        return (tgproxy_dir / "version.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _extract(data: bytes, dest: Path) -> None:
    """Из архива исходников — только _KEEP_DIRS и _KEEP_FILES, без верхней
    папки архива (у GitHub и SourceForge она называется по-разному)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            windows_path = PureWindowsPath(info.filename)
            if windows_path.drive or windows_path.root or any(p == ".." or ":" in p for p in windows_path.parts):
                raise ValueError("Архив Telegram Proxy содержит некорректные пути")
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts[1:]
            if not parts or info.is_dir():
                continue
            if any(p in ("", ".", "..") or ":" in p for p in parts) or "__pycache__" in parts:
                continue
            if not (parts[0] in _KEEP_DIRS or (len(parts) == 1 and parts[0] in _KEEP_FILES)):
                continue
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    if not (dest / "proxy" / "tg_ws_proxy.py").is_file():
        raise ValueError("В архиве релиза нет кода сервера — возможно, автор изменил структуру проекта")


def _install_archive(data: bytes, tgproxy_dir: Path, tag: str, log: Callable[[str], None]) -> None:
    try:
        _prepare_and_install(data, tgproxy_dir, tag, log)
    except Exception:
        try:
            with _commit_lock:
                transaction.discard_preparation(tgproxy_dir, _COMPONENT)
            transaction.cleanup_retired(tgproxy_dir, _COMPONENT)
        except Exception:
            logger.warning("Временные файлы Telegram Proxy сохранены для следующего запуска", exc_info=True)
        raise


def _prepare_and_install(data: bytes, tgproxy_dir: Path, tag: str, log: Callable[[str], None]) -> None:
    _check_cancelled()
    recover_interrupted_update(tgproxy_dir)
    _check_source_path(tgproxy_dir)
    transaction.cleanup_retired(tgproxy_dir, _COMPONENT)
    tgproxy_dir.mkdir(parents=True, exist_ok=True)
    work = transaction.transaction_dir(tgproxy_dir)
    work.mkdir()
    # Пробный запуск разрешён host.py только из собственных src/src.new.
    # Сохраняем этот белый список, не разрешаем выполнение из произвольных папок.
    source = tgproxy_dir / "src.new"
    _check_source_path(source)
    if source.exists():
        shutil.rmtree(source)
    try:
        _extract(data, source)
        _check_cancelled()
        log("Проверяю, что новая версия запускается...")
        info = probe(source)
        if info.get("error"):
            logger.error("TG WS Proxy %s: пробный запуск не прошёл: %s", tag, info["error"])
            keep = " Установленная версия продолжит работать." if is_installed(tgproxy_dir) else ""
            raise ValueError(f"Версия {tag} не запускается в этой сборке FlowZap — нужна новая версия FlowZap.{keep}")
        if info.get("version") and info["version"] != tag.lstrip("vV"):
            logger.info("Тег релиза %s, версия в коде: %s", tag, info["version"])
        (source / "options.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        _check_cancelled()
        with _commit_lock:
            _check_cancelled()
            candidate = work / "new"
            # Настройки прокси — в config.toml приложения. Остальные локальные
            # файлы в tgproxy сохраняем, код src заменяем целиком после probe.
            def keep_local_files(directory, names):
                return [n for n in names if n in ("src", "src.new", "src.old")] if Path(directory) == tgproxy_dir else []
            shutil.copytree(tgproxy_dir, candidate, ignore=keep_local_files)
            source.rename(candidate / "src")
            (candidate / "version.txt").write_text(tag, encoding="utf-8")
            _check_cancelled()
            log("Устанавливаем Telegram Proxy…")
            transaction.commit(tgproxy_dir, _COMPONENT)
        transaction.cleanup_retired(tgproxy_dir, _COMPONENT)
    finally:
        # Эта папка только для подготовки; резервная копия хранится отдельно.
        if source.exists() and not source.is_symlink() and not getattr(source, "is_junction", lambda: False)():
            try:
                shutil.rmtree(source)
            except OSError:
                logger.warning("Не удалось убрать подготовленные файлы Telegram Proxy", exc_info=True)


def download_and_install_tg_proxy(
    tgproxy_dir: Path,
    repo: str = TG_PROXY_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def body(log: Callable[[str], None]) -> str:
        if not _install_lock.acquire(blocking=False):
            raise ValueError("Telegram Proxy уже устанавливается")
        try:
            return _install(tgproxy_dir, repo, log)
        finally:
            _install_lock.release()

    run_install("tgproxy-updater", "Ошибка установки TG Proxy", body, on_progress, on_done)


def _install(tgproxy_dir: Path, repo: str, log: Callable[[str], None]) -> str:
    _check_cancelled()
    log("Проверяем обновления...")
    release = latest_release_for_user(repo)      # при лимите — повтор без своего DNS
    tag = release.get("tag_name", "")
    if not tag:
        raise ValueError("В релизе нет номера версии")

    log("Скачивается...")
    archive = f"{release.get('name') or 'TG WS Proxy ' + tag} source code.zip"
    data = download_asset(
        {"name": archive,
         "browser_download_url": release.get("zipball_url")
         or f"https://api.github.com/repos/{repo}/zipball/{tag}"},
        lambda: (sourceforge_url(SOURCEFORGE_TGPROXY_PROJECT, tag, archive), False),
        "зеркало SourceForge",
    )

    _install_archive(data, tgproxy_dir, tag, log)
    # exe автора, который ставил FlowZap до 1.1, больше не нужен
    if not _exit_requested.is_set():
        migrate_legacy(tgproxy_dir)
    log(f"✓ Telegram Proxy установлен ({tag})")
    return f"Telegram Proxy установлен ({tag})"
