"""
core/updates/zapret.py
----------------------
Установка и обновление zapret Core (релиз Flowseal/zapret-discord-youtube):
архив с GitHub (SourceForge — зеркало), выгрузка WinDivert, распаковка с
сохранением пользовательских списков. Если установлена фоновая служба —
она обновляет и свою копию winws.
"""
import logging
import io
import shutil
import threading
import time
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Callable, Optional

from core.system import winproc
from core.updates.releases import download_asset, latest_asset, retry_without_dns, run_install, sourceforge_url
from core.zapret.winws import WINWS_EXE, USER_LIST_FILES, ensure_user_lists, warmup_windivert
from core.updates import component_transaction as transaction

logger = logging.getLogger(__name__)

CORE_REPO = "Flowseal/zapret-discord-youtube"

# Зеркало на SourceForge — точная копия Flowseal/zapret-discord-youtube,
# используется как молчаливый fallback, если GitHub недоступен из сети.
SOURCEFORGE_ZAPRET_PROJECT = "flowseal.mirror"

# Какие пресеты (.bat) поставил релиз Flowseal — чтобы при обновлении
# удалять только их, а не пресеты, которые человек сделал сам
PRESETS_MANIFEST = "flowzap-presets.txt"

# Установку могут начать и главная (первый запуск), и «Обновления» — две сразу
# писали бы в одну папку zapret
_install_lock = threading.Lock()
_commit_lock = threading.Lock()
_exit_requested = threading.Event()


def is_installing() -> bool:
    return _install_lock.locked()


def finish_before_exit() -> None:
    """Запретить новую замену, дождаться уже начатой замены либо отката."""
    request_exit()
    with _commit_lock:
        pass


def request_exit() -> None:
    """Отменить подготовку, не ожидая критической секции."""
    _exit_requested.set()


def _check_cancelled() -> None:
    if _exit_requested.is_set():
        raise ValueError("Обновление zapret прервано при выходе")


def recover_interrupted_update(zapret_dir: Path) -> None:
    """Вызывать при запуске до чтения пресетов и до фоновых операций."""
    with _commit_lock:
        transaction.recover(zapret_dir)


def get_installed_core_version(zapret_dir: Path) -> Optional[str]:
    ver_file = zapret_dir / "version.txt"
    if ver_file.exists():
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def find_zip_asset(release: dict) -> Optional[dict]:
    for asset in release.get("assets", []):
        name = asset.get("name", "").lower()
        if name.startswith("zapret") and name.endswith(".zip"):
            return asset
    return None



def _unload_windivert() -> None:
    """
    Выгрузить драйвер WinDivert из ядра перед обновлением.
    Без этого Windows блокирует перезапись WinDivert64.sys с WinError 5.
    """
    import subprocess
    flags = winproc.CREATE_NO_WINDOW

    # Убиваем все winws.exe — они держат хэндл на драйвер
    winproc.kill(WINWS_EXE)
    time.sleep(1)

    # Останавливаем и удаляем службу WinDivert (возможны оба имени)
    for svc in ("WinDivert", "WinDivert14"):
        try:
            subprocess.run([winproc.system_exe("sc.exe"), "stop", svc],
                           capture_output=True, creationflags=flags, timeout=5)
        except Exception:
            pass
        try:
            subprocess.run([winproc.system_exe("sc.exe"), "delete", svc],
                           capture_output=True, creationflags=flags, timeout=5)
        except Exception:
            pass

    time.sleep(1)  # Ждём выгрузки драйвера из ядра
    logger.info("WinDivert выгружен перед обновлением")


def _merge_copy(src_dir: Path, dst_dir: Path) -> None:
    """Рекурсивно копирует файлы с заменой, не трогая лишние файлы в dst.
    Пользовательские списки (list-general-user.txt и т.п.) не перезаписываются."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        dst_item = dst_dir / item.name
        if item.is_dir():
            _merge_copy(item, dst_item)
        elif (item.name.lower().endswith("-user.txt")
              or (item.parent.name.lower() == "lists" and item.name.lower() in USER_LIST_FILES)
              or (item.parent.name.lower() == "utils" and item.name.lower() == "game_filter.enabled")) and dst_item.exists():
            logger.debug(f"Пропущен пользовательский файл: {item.name}")
        else:
            shutil.copy2(item, dst_item)


def _remove_stale_presets(src: Path, zapret_dir: Path) -> None:
    """Пресеты прошлого релиза Flowseal, которых нет в новом (переименовали
    или убрали), — иначе навсегда оставались бы в списке. Только из списка
    PRESETS_MANIFEST: свой пресет (скопированный general.bat под другим
    именем) обновление не трогает. Списка нет (zapret ставился FlowZap до
    1.1.2) — в этот раз ничего не удаляем: какие пресеты чьи, неизвестно."""
    new_bats = {b.name.lower() for b in src.glob("*.bat")}
    if not new_bats:
        return
    try:
        manifest = (zapret_dir / PRESETS_MANIFEST).read_text(encoding="utf-8")
    except OSError:
        logger.info("Список пресетов прошлой версии zapret неизвестен — устаревшие не удаляю")
        return
    previous = {line.strip().lower() for line in manifest.splitlines() if line.strip()}
    for old_bat in zapret_dir.glob("*.bat"):
        name = old_bat.name.lower()
        if name in previous and name not in new_bats:
            try:
                old_bat.unlink()
                logger.info(f"Удалён устаревший пресет: {old_bat.name}")
            except Exception as e:
                logger.warning(f"Не удалось удалить {old_bat.name}: {e}")


def _write_presets_manifest(src: Path, zapret_dir: Path) -> None:
    names = sorted(b.name for b in src.glob("*.bat"))
    (zapret_dir / PRESETS_MANIFEST).write_text("\n".join(names) + "\n", encoding="utf-8")


def _validate_payload(source: Path) -> None:
    """Проверяем сам релиз до объединения со старой установкой."""
    required = ("bin/winws.exe", "bin/WinDivert.dll", "bin/WinDivert64.sys",
                "bin/cygwin1.dll", "lists/list-general.txt", "lists/ipset-all.txt")
    for name in required:
        path = source / name
        if not path.is_file() or (not name.endswith(".txt") and path.stat().st_size == 0):
            raise ValueError("В архиве zapret нет необходимых файлов — обновление не установлено")
    if not any(p.is_file() and p.stat().st_size for p in source.glob("general*.bat")):
        raise ValueError("В архиве zapret нет пресетов — обновление не установлено")


def _install_archive(data: bytes, zapret_dir: Path, tag: str,
                     log: Callable[[str], None], before_install=None) -> None:
    try:
        _prepare_and_install(data, zapret_dir, tag, log, before_install)
    except Exception:
        # Ошибка распаковки/копирования не должна оставлять большой архив.
        # Журнал и резервную копию незавершённого отката не удаляем.
        try:
            with _commit_lock:
                transaction.discard_preparation(zapret_dir)
            transaction.cleanup_retired(zapret_dir)
        except Exception:
            logger.warning("Временные файлы zapret сохранены для следующего запуска", exc_info=True)
        raise


def _prepare_and_install(data: bytes, zapret_dir: Path, tag: str,
                         log: Callable[[str], None], before_install=None) -> None:
    """До commit меняется только соседняя папка на том же диске."""
    _check_cancelled()
    recover_interrupted_update(zapret_dir)
    transaction.cleanup_retired(zapret_dir)
    work = transaction.transaction_dir(zapret_dir)
    work.mkdir(parents=True)
    raw = work / "archive"
    raw.mkdir()
    log("Распаковываем...")
    # Отклоняем опасные пути, а не исправляем их при распаковке.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            path = PureWindowsPath(entry.filename)
            if path.drive or path.root or any(p == ".." or ":" in p for p in path.parts):
                raise ValueError("Архив zapret содержит некорректные пути")
        archive.extractall(raw)
    entries = list(raw.iterdir())
    source = entries[0] if len(entries) == 1 and entries[0].is_dir() else raw
    _validate_payload(source)
    _check_cancelled()
    # Копирование старой установки и замена защищены от обычного выхода.
    # Внутри нет загрузки из сети: ожидание ограничено локальными операциями.
    with _commit_lock:
        _check_cancelled()
        if before_install:
            before_install()
        from core.service import client as service_client
        if not service_client.service_installed():
            _unload_windivert()
        candidate = work / "new"
        if zapret_dir.exists():
            shutil.copytree(zapret_dir, candidate)
        else:
            candidate.mkdir()
        _remove_stale_presets(source, candidate)
        _merge_copy(source, candidate)
        _write_presets_manifest(source, candidate)
        ensure_user_lists(candidate / "lists")
        if any(not (candidate / "lists" / name).is_file() for name in USER_LIST_FILES):
            raise ValueError("Не удалось подготовить списки zapret — обновление не установлено")
        (candidate / "version.txt").write_text(tag, encoding="utf-8")
        _validate_payload(candidate)
        _check_cancelled()
        log("Устанавливаем zapret…")
        transaction.commit(zapret_dir)
    transaction.cleanup_retired(zapret_dir)


def download_and_install_core(
    zapret_dir: Path,
    repo: str = CORE_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
    before_install: Optional[Callable[[], None]] = None,
) -> None:
    """before_install — в фоновом потоке, когда архив уже скачан (например,
    остановить обход: он работает, пока идёт загрузка)."""
    def body(log: Callable[[str], None]) -> str:
        if not _install_lock.acquire(blocking=False):
            raise ValueError("zapret уже устанавливается")
        try:
            return _install(log)
        finally:
            _install_lock.release()

    def _install(log: Callable[[str], None]) -> str:
        from core.service import client as service_client

        _check_cancelled()
        tag, asset = latest_asset(repo, find_zip_asset, "Архив zapret", log)
        done_word = "обновлён до" if get_installed_core_version(zapret_dir) else "установлен:"
        data = download_asset(
            asset,
            lambda: (sourceforge_url(SOURCEFORGE_ZAPRET_PROJECT, tag, asset["name"]), True),
            "зеркало SourceForge",
        )

        _install_archive(data, zapret_dir, tag, log, before_install)
        log(f"✓ zapret {done_word} {tag}.")

        if _exit_requested.is_set():
            return f"zapret {done_word} {tag}"
        if service_client.service_installed():
            # winws работает из папки службы — она сама скачивает ту же версию
            # и сверяет sha256 (без UAC). Не вышло — повторит перед запуском.
            try:
                current = service_client.session.status().get("engine_version") or ""
            except service_client.ServiceError:
                current = ""
            if current == tag:
                # Уже та же (подтянула другая копия FlowZap) — второй раз не качаем
                logger.info(f"winws в фоновой службе уже {tag}")
            else:
                log("Обновляю winws в фоновой службе…")
                try:
                    retry_without_dns(lambda: service_client.session.update_engine(tag),
                                      lambda e: isinstance(e, service_client.ServiceError))
                except service_client.ServiceError as e:
                    logger.warning(f"Служба не обновила winws: {e}")
        else:
            # Первый запуск после установки грузит драйвер в ядро — иначе первый тест пресетов упадёт
            warmup_windivert(zapret_dir / "bin" / WINWS_EXE)
        return f"zapret {done_word} {tag}"

    run_install("core-updater", "Ошибка обновления Core", body, on_progress, on_done)
