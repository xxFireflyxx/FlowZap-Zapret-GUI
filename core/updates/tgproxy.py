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
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from core.tgproxy.host import probe
from core.tgproxy.manager import is_installed, migrate_legacy, src_dir
from core.updates.releases import download_asset, latest_release_for_user, run_install, sourceforge_url

logger = logging.getLogger(__name__)

TG_PROXY_REPO = "Flowseal/tg-ws-proxy"

# Зеркало на SourceForge: там лежит и архив исходников каждого релиза
SOURCEFORGE_TGPROXY_PROJECT = "tg-ws-proxy.mirror"

# Что берём из исходников: сервер, его вспомогательный пакет и лицензию
_KEEP_DIRS = ("proxy", "utils")
_KEEP_FILES = ("LICENSE",)

# Установку могут начать и главная (первое включение), и «Обновления»
_install_lock = threading.Lock()


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
            parts = PurePosixPath(info.filename).parts[1:]
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


def _swap(new: Path, target: Path) -> None:
    """new → target; прежняя версия возвращается на место, если замена не удалась."""
    old = target.with_name(target.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        try:
            target.rename(old)
        except OSError as e:
            raise ValueError(f"Не удалось заменить файлы прокси ({e}) — выключите TG Proxy и повторите") from None
    try:
        new.rename(target)
    except OSError as e:
        if old.exists():
            old.rename(target)
        raise ValueError(f"Не удалось заменить файлы прокси ({e})") from None
    shutil.rmtree(old, ignore_errors=True)


def download_and_install_tg_proxy(
    tgproxy_dir: Path,
    repo: str = TG_PROXY_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def body(log: Callable[[str], None]) -> str:
        if not _install_lock.acquire(blocking=False):
            raise ValueError("TG WS Proxy уже устанавливается")
        try:
            return _install(tgproxy_dir, repo, log)
        finally:
            _install_lock.release()

    run_install("tgproxy-updater", "Ошибка установки TG Proxy", body, on_progress, on_done)


def _install(tgproxy_dir: Path, repo: str, log: Callable[[str], None]) -> str:
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

    tgproxy_dir.mkdir(parents=True, exist_ok=True)
    new = tgproxy_dir / "src.new"
    shutil.rmtree(new, ignore_errors=True)
    try:
        _extract(data, new)

        log("Проверяю, что новая версия запускается...")
        info = probe(new)
        if info.get("error"):
            logger.error(f"TG WS Proxy {tag}: пробный запуск не прошёл: {info['error']}")
            keep = " Установленная версия продолжит работать." if is_installed(tgproxy_dir) else ""
            raise ValueError(f"Версия {tag} не запускается в этой сборке FlowZap — "
                             f"нужна новая версия FlowZap.{keep}")
        if info.get("version") and info["version"] != tag.lstrip("vV"):
            logger.info(f"Тег релиза {tag}, версия в коде: {info['version']}")
        (new / "options.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

        _swap(new, src_dir(tgproxy_dir))
    finally:
        shutil.rmtree(new, ignore_errors=True)

    (tgproxy_dir / "version.txt").write_text(tag, encoding="utf-8")
    # exe автора, который ставил FlowZap до 1.1, больше не нужен
    migrate_legacy(tgproxy_dir)
    log(f"✓ TG WS Proxy установлен ({tag})")
    return f"TG WS Proxy установлен ({tag})"
