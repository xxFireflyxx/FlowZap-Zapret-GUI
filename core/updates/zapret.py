"""
core/updates/zapret.py
----------------------
Установка и обновление zapret Core (релиз Flowseal/zapret-discord-youtube):
архив с GitHub (SourceForge — зеркало), выгрузка WinDivert, распаковка с
сохранением пользовательских списков. Если установлена фоновая служба —
она обновляет и свою копию winws.
"""
import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from core.system import winproc
from core.updates.releases import download_asset, latest_asset, retry_without_dns, run_install, sourceforge_url
from core.zapret.winws import WINWS_EXE, ensure_user_lists, warmup_windivert

logger = logging.getLogger(__name__)

CORE_REPO = "Flowseal/zapret-discord-youtube"

# Зеркало на SourceForge — точная копия Flowseal/zapret-discord-youtube,
# используется как молчаливый fallback, если GitHub недоступен из сети.
SOURCEFORGE_ZAPRET_PROJECT = "flowseal.mirror"


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
            subprocess.run(["sc", "stop", svc],
                           capture_output=True, creationflags=flags, timeout=5)
        except Exception:
            pass
        try:
            subprocess.run(["sc", "delete", svc],
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
        elif item.name.lower().endswith("-user.txt") and dst_item.exists():
            logger.debug(f"Пропущен пользовательский файл: {item.name}")
        else:
            shutil.copy2(item, dst_item)


def _remove_stale_presets(src: Path, zapret_dir: Path) -> None:
    """Пресеты, которых нет в новом релизе (переименовали или убрали в
    Flowseal), иначе навсегда оставались бы в списке."""
    new_bats = {b.name.lower() for b in src.glob("*.bat")}
    if not new_bats:
        return
    for old_bat in zapret_dir.glob("*.bat"):
        if old_bat.name.lower() not in new_bats:
            try:
                old_bat.unlink()
                logger.info(f"Удалён устаревший пресет: {old_bat.name}")
            except Exception as e:
                logger.warning(f"Не удалось удалить {old_bat.name}: {e}")


def download_and_install_core(
    zapret_dir: Path,
    repo: str = CORE_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def body(log: Callable[[str], None]) -> str:
        import zipfile, io, tempfile

        tag, asset = latest_asset(repo, find_zip_asset, "Архив zapret", log)
        data = download_asset(
            asset,
            lambda: (sourceforge_url(SOURCEFORGE_ZAPRET_PROJECT, tag, asset["name"]), True),
            "зеркало SourceForge",
        )

        logger.info("Останавливаем WinDivert...")
        _unload_windivert()
        log("Распаковываем...")
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(tmp)
            entries = list(Path(tmp).iterdir())
            src = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(tmp)
            zapret_dir.mkdir(parents=True, exist_ok=True)
            _remove_stale_presets(src, zapret_dir)
            _merge_copy(src, zapret_dir)
            (zapret_dir / "version.txt").write_text(tag, encoding="utf-8")

        ensure_user_lists(zapret_dir / "lists")
        log(f"✓ zapret обновлён до {tag}.")

        from core.service import client as service_client
        if service_client.service_installed():
            # winws работает из папки службы — она сама скачивает ту же версию
            # и сверяет sha256 (без UAC). Не вышло — повторит перед запуском.
            log("Обновляю winws в фоновой службе…")
            try:
                retry_without_dns(lambda: service_client.session.update_engine(tag),
                                  lambda e: isinstance(e, service_client.ServiceError))
            except service_client.ServiceError as e:
                logger.warning(f"Служба не обновила winws: {e}")
        else:
            # Первый запуск после установки грузит драйвер в ядро — иначе первый тест пресетов упадёт
            warmup_windivert(zapret_dir / "bin" / WINWS_EXE)
        return f"zapret обновлён до {tag}"

    run_install("core-updater", "Ошибка обновления Core", body, on_progress, on_done)
