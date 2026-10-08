"""
core/updates/tgproxy.py
-----------------------
Установка и обновление TG WS Proxy (релиз Flowseal/tg-ws-proxy): exe с
GitHub, SourceForge — зеркало.
"""
import logging
from pathlib import Path
from typing import Callable, Optional

from core.system.fileinfo import get_exe_version
from core.updates.releases import download_asset, latest_asset, run_install, sourceforge_url

logger = logging.getLogger(__name__)

TG_PROXY_REPO = "Flowseal/tg-ws-proxy"
TG_PROXY_EXE  = "TgWsProxy_windows.exe"

# Зеркало на SourceForge — точная копия exe из релиза Flowseal/tg-ws-proxy,
# используется как молчаливый fallback, если GitHub недоступен из сети.
SOURCEFORGE_TGPROXY_PROJECT = "tg-ws-proxy.mirror"


def get_installed_tg_proxy_version(tgproxy_dir: Path) -> Optional[str]:
    ver_file = tgproxy_dir / "version.txt"
    if ver_file.exists():
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def find_tg_proxy_asset(release: dict) -> Optional[dict]:
    """Найти exe для Windows в релизе tg-ws-proxy."""
    assets = release.get("assets", [])
    # Приоритет: TgWsProxy_windows.exe (Windows 10+)
    for asset in assets:
        name = asset.get("name", "").lower()
        if name == "tgwsproxy_windows.exe":
            return asset
    # Любой windows exe
    for asset in assets:
        name = asset.get("name", "").lower()
        if "windows" in name and name.endswith(".exe") and "7" not in name:
            return asset
    return None


def download_and_install_tg_proxy(
    tgproxy_dir: Path,
    repo: str = TG_PROXY_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def body(log: Callable[[str], None]) -> str:
        tag, asset = latest_asset(repo, find_tg_proxy_asset, f"Файл {TG_PROXY_EXE}", log)
        data = download_asset(
            asset,
            lambda: (sourceforge_url(SOURCEFORGE_TGPROXY_PROJECT, tag, asset["name"]), True),
            "зеркало SourceForge",
        )
        tgproxy_dir.mkdir(parents=True, exist_ok=True)
        exe_path = tgproxy_dir / TG_PROXY_EXE
        exe_path.write_bytes(data)

        # Версия — тег релиза: файл (с GitHub или зеркала) сверен с ним по
        # sha256. Версия из ресурсов exe — только для лога: она иногда
        # отстаёт от тега, и если бы перебивала его, апдейтер вечно считал
        # бы, что доступно обновление.
        actual_version = get_exe_version(exe_path)
        if actual_version and actual_version != tag.lstrip("v"):
            logger.info(f"Тег релиза {tag}, но версия в ресурсах exe: {actual_version} (используем тег)")
        (tgproxy_dir / "version.txt").write_text(tag, encoding="utf-8")

        log(f"✓ TG WS Proxy установлен ({tag})")
        return f"TG WS Proxy установлен ({tag})"

    run_install("tgproxy-updater", "Ошибка установки TG Proxy", body, on_progress, on_done)
