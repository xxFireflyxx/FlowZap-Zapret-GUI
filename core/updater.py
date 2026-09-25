"""
core/updater.py
---------------
Проверка и загрузка обновлений FlowZap.
"""
import logging
import threading
import shutil
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

GUI_VERSION = "0.5.2 beta 11 win"
FLOWZAP_REPO      = "xxFireflyxx/FlowZap-Zapret-GUI"
FLOWZAP_GITLAB_ID = "xx_firefly_xx%2Fflowzap"


_ssl_ctx = None


def _ssl_context():
    """SSL-контекст с сертификатами certifi для urlopen.

    На части систем (особенно Windows 11 в изолированном окружении
    PyInstaller) отсутствует нужное системное хранилище CA-сертификатов,
    и urlopen падает с CERTIFICATE_VERIFY_FAILED. certifi — обязательная
    зависимость (requirements.txt), поэтому fallback ниже — просто на
    случай, если её всё же не окажется в окружении.
    """
    global _ssl_ctx
    if _ssl_ctx is None:
        import ssl
        try:
            import certifi
            _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception as e:
            logger.warning(f"certifi недоступен ({e}), используем системный SSL-контекст")
            _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


_github_token: Optional[str] = None


def set_github_token(token: str) -> None:
    """Установить GitHub токен для API запросов."""
    global _github_token
    _github_token = token.strip() if token else None


def _github_headers() -> dict:
    """Заголовки для GitHub API с токеном если задан."""
    headers = {"User-Agent": "FlowZap/1.0"}
    if _github_token:
        headers["Authorization"] = f"token {_github_token}"
    return headers


# Специальное исключение для rate limit
class RateLimitError(Exception):
    pass


def _download_with_grace(
    url: str,
    headers: dict,
    connect_timeout: float = 15,
    transfer_timeout: float = 120,
) -> bytes:
    """
    Качает файл по url с раздельными таймаутами:
    - connect_timeout — сколько ждём ОТВЕТА сервера целиком (не одну попытку
      соединения — если DNS вернул несколько адресов или сервер сначала
      редиректит на другой хост, urlopen(timeout=X) ограничивает только
      КАЖДУЮ такую попытку по отдельности, и они суммируются). Поэтому
      соединение выполняется в отдельном демон-потоке: не уложились в
      connect_timeout секунд — сразу TimeoutError, не дожидаясь, пока сокет
      переберёт все адреса. Поток-неудачник просто тихо доживает и
      завершается сам, ничего не блокируя.
    - transfer_timeout — если ответ получен и данные пошли, таймаут на
      чтение шире: обрывает только реальное зависание передачи (нет новых
      байт дольше transfer_timeout секунд), а не общий лимит на весь файл.
    """
    import urllib.request, threading

    result: dict = {}

    def _connect() -> None:
        try:
            req = urllib.request.Request(url, headers=headers)
            result["response"] = urllib.request.urlopen(req, timeout=transfer_timeout, context=_ssl_context())
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(connect_timeout)
    if t.is_alive():
        raise TimeoutError(f"Нет ответа от сервера за {connect_timeout} сек")
    if "error" in result:
        raise result["error"]

    r = result["response"]
    try:
        r.fp.raw._sock.settimeout(transfer_timeout)
    except Exception:
        pass  # не удалось достать сокет — читаем с тем же (широким) таймаутом
    return r.read()


def _get_from_gitlab() -> Optional[dict]:
    """GitLab fallback - возвращает данные в GitHub-совместимом формате."""
    try:
        import urllib.request, json
        url = f"https://gitlab.com/api/v4/projects/{FLOWZAP_GITLAB_ID}/releases"
        req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
            releases = json.load(r)
        if not releases:
            return None
        rel = releases[0]
        assets = []
        for link in rel.get("assets", {}).get("links", []):
            assets.append({
                "name": link.get("name", ""),
                "browser_download_url": link.get("url", ""),
                "size": 0,
            })
        logger.info(f"GitLab fallback: {rel.get('tag_name')}")
        return {
            "tag_name": rel.get("tag_name", ""),
            "name":     rel.get("name", ""),
            "assets":   assets,
            "_source":  "gitlab",
        }
    except Exception as e:
        logger.error(f"GitLab fallback error: {e}")
        return None


# Кэш релизов: repo -> (timestamp, release_dict)
_release_cache: dict = {}
_CACHE_TTL = 3 * 3600  # 3 часа


def get_latest_release(repo: str = FLOWZAP_REPO, force: bool = False) -> Optional[dict]:
    import time, urllib.request, json

    # Проверяем in-memory кэш
    if not force and repo in _release_cache:
        ts, cached = _release_cache[repo]
        if time.time() - ts < _CACHE_TTL:
            logger.debug(f"Релиз из кэша: {repo}")
            return cached

    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
            result = json.load(r)
        _release_cache[repo] = (time.time(), result)
        return result
    except Exception as e:
        err_str = str(e).lower()
        if "403" in err_str or "rate limit" in err_str or "404" in err_str:
            if repo == FLOWZAP_REPO:
                reason = "rate limit" if ("403" in err_str or "rate limit" in err_str) else "404"
                logger.warning(f"GitHub {reason} - switching to GitLab fallback")
                result = _get_from_gitlab()
                if result:
                    _release_cache[repo] = (time.time(), result)
                return result
        logger.error(f"Ошибка проверки обновлений: {e}")
        if repo == FLOWZAP_REPO:
            logger.info("Trying GitLab fallback after error...")
            result = _get_from_gitlab()
            if result:
                _release_cache[repo] = (time.time(), result)
            return result
        return None


def find_exe_asset(release: dict) -> Optional[dict]:
    """
    Найти asset для обновления FlowZap GUI.
    Приоритеты:
      1. flowzap-vX.X.X.zip
      2. flowzap-*.zip
      3. release-*.zip (старый формат)
      4. release.zip
      5. любой zip
      6. прямой exe
    """
    assets = release.get("assets", [])

    real_assets = [
        a for a in assets
        if "/archive/" not in a.get("browser_download_url", "")
        and a.get("browser_download_url", "")
    ]

    # Приоритет 1: flowzap-vX.X.X.zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("flowzap-v") and name.endswith(".zip"):
            return asset

    # Приоритет 2: flowzap-*.zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("flowzap-") and name.endswith(".zip"):
            return asset

    # Приоритет 3: release-*.zip (старый формат)
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("release-") and name.endswith(".zip"):
            return asset

    # Приоритет 4: release.zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name == "release.zip":
            return asset

    # Приоритет 5: любой zip
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.endswith(".zip"):
            return asset

    # Fallback: прямой exe
    for asset in real_assets:
        if asset.get("name", "").lower().endswith(".exe"):
            return asset

    return None


def _extract_update_payload(data: bytes) -> Optional[Path]:
    """Распаковать архив обновления во временную папку.

    Белый список — только FlowZap.exe и, если он есть в архиве, папка
    _internal/, где бы они ни лежали внутри архива (обычно под префиксом
    FlowZap/). Всё остальное содержимое архива (config.toml-шаблон,
    случайные файлы) игнорируется и никогда не попадает на диск — так
    обновление не может затереть zapret/, tgproxy/, logs/ или config.toml
    пользователя.

    Возвращает временную папку с FlowZap.exe (и, если был в архиве,
    подпапкой _internal/) или None, если exe в архиве не нашёлся.
    """
    import zipfile, io, tempfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            exe_member = next(
                (m for m in names if Path(m).name.lower() == "flowzap.exe"),
                None,
            )
            if exe_member is None:
                logger.error("FlowZap.exe не найден в архиве обновления")
                return None

            root = Path(exe_member).parent.as_posix()
            internal_prefix = f"{root}/_internal/" if root not in ("", ".") else "_internal/"

            temp_dir = Path(tempfile.mkdtemp(prefix="flowzap_update_"))
            (temp_dir / "FlowZap.exe").write_bytes(zf.read(exe_member))

            internal_members = [
                m for m in names
                if m.replace("\\", "/").startswith(internal_prefix) and not m.endswith("/")
            ]
            for member in internal_members:
                rel = member.replace("\\", "/")[len(internal_prefix):]
                # Защита от zip-slip: ни один элемент не должен выходить за
                # пределы temp_dir/_internal через "..".
                if not rel or ".." in Path(rel).parts:
                    logger.warning(f"Пропущен подозрительный путь в архиве: {member}")
                    continue
                dest = temp_dir / "_internal" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))

            if not internal_members:
                logger.warning("В архиве обновления нет _internal/ — будет заменён только exe")

            return temp_dir
    except Exception as e:
        logger.error(f"Ошибка распаковки обновления: {e}")
    return None


def _check_disk_space(paths: list, required_bytes: int) -> Optional[str]:
    """Проверить, что на дисках всех путей есть required_bytes байт свободно.

    Возвращает текст ошибки, если места где-то не хватает, иначе None.
    Если для какого-то пути место проверить не удалось (сетевой диск,
    ошибка ОС), проверка для него пропускается — это не повод прерывать
    обновление из-за неполадки самой проверки.
    """
    checked_drives = set()
    for p in paths:
        drive = p.anchor or str(p)
        if drive in checked_drives:
            continue
        checked_drives.add(drive)
        existing = p
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        try:
            free = shutil.disk_usage(existing).free
        except Exception as e:
            logger.warning(f"Не удалось проверить свободное место для {existing}: {e}")
            continue
        if free < required_bytes:
            return (
                f"Недостаточно места на диске {drive}: свободно "
                f"{free / 1024**2:.0f} МБ, нужно ~{required_bytes / 1024**2:.0f} МБ"
            )
    return None


def _build_swap_script(
    current_exe: Path,
    current_internal: Path,
    new_exe: Path,
    new_internal: Optional[Path],
    payload_dir: Path,
    update_log: Path,
    max_wait_attempts: int = 20,
) -> str:
    """Собрать .bat, который меняет FlowZap.exe (+ _internal/) после
    закрытия приложения, с опросом вместо слепого ожидания и полным
    откатом при любой ошибке.

    Схема:
      1. Опрашивать (move поверх самого себя как переименование) CUR_EXE,
         пока файл не освободится — это и есть признак, что приложение
         закрылось. До max_wait_attempts попыток по 1 сек.
      2. Если внутри архива был _internal — убрать старый в сторону
         (CUR_INTERNAL -> .old), поставить новый на его место.
      3. Поставить новый exe на место старого.
      4. Если что-то на шаге 2 или 3 не удалось — откатить сделанное
         (вернуть .old-копии на место) и перезапустить старую версию.
      5. При успехе удалить .old-копии, временную папку и сам bat,
         запустить новую версию.

    Все пути передаются через переменные окружения bat и везде
    используются в кавычках, поэтому пробелы, кириллица и спецсимволы
    вида ^ или & в пути безопасны.
    """
    has_internal = new_internal is not None

    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        "",
        f'set "CUR_EXE={current_exe}"',
        f'set "CUR_INTERNAL={current_internal}"',
        f'set "NEW_EXE={new_exe}"',
        f'set "NEW_INTERNAL={new_internal if has_internal else ""}"',
        f'set "PAYLOAD_DIR={payload_dir}"',
        f'set "UPDATE_LOG={update_log}"',
        "",
        "set /a attempts=0",
        ":wait_loop",
        'move /y "%CUR_EXE%" "%CUR_EXE%.old" >nul 2>&1',
        "if not errorlevel 1 goto exe_parked",
        "set /a attempts+=1",
        f"if %attempts% geq {max_wait_attempts} goto timeout_abort",
        "timeout /t 1 /nobreak >nul",
        "goto wait_loop",
        "",
        ":exe_parked",
    ]

    if has_internal:
        lines += [
            'if exist "%CUR_INTERNAL%.old" rmdir /s /q "%CUR_INTERNAL%.old" >nul 2>&1',
            'if not exist "%CUR_INTERNAL%" goto move_new_internal',
            'move /y "%CUR_INTERNAL%" "%CUR_INTERNAL%.old" >nul 2>&1',
            "if errorlevel 1 goto rollback_exe_only",
            "",
            ":move_new_internal",
            'move /y "%NEW_INTERNAL%" "%CUR_INTERNAL%" >nul 2>&1',
            "if errorlevel 1 goto rollback_internal_and_exe",
            "",
        ]

    lines += [
        ":place_exe",
        'move /y "%NEW_EXE%" "%CUR_EXE%" >nul 2>&1',
        "if errorlevel 1 goto rollback_full",
        "",
        'if exist "%CUR_INTERNAL%.old" rmdir /s /q "%CUR_INTERNAL%.old" >nul 2>&1',
        'del "%CUR_EXE%.old" >nul 2>&1',
        'echo %date% %time% Update applied: %CUR_EXE% >> "%UPDATE_LOG%" 2>nul',
        'rmdir /s /q "%PAYLOAD_DIR%" >nul 2>&1',
        'start "" "%CUR_EXE%"',
        'del "%~f0"',
        "exit /b 0",
        "",
    ]

    if has_internal:
        lines += [
            ":rollback_internal_and_exe",
            'if exist "%CUR_INTERNAL%" rmdir /s /q "%CUR_INTERNAL%" >nul 2>&1',
            'if exist "%CUR_INTERNAL%.old" move /y "%CUR_INTERNAL%.old" "%CUR_INTERNAL%" >nul 2>&1',
            "",
            ":rollback_exe_only",
            'move /y "%CUR_EXE%.old" "%CUR_EXE%" >nul 2>&1',
            'echo %date% %time% Update rollback: _internal swap failed >> "%UPDATE_LOG%" 2>nul',
            "goto abort_relaunch",
            "",
        ]

    lines += [
        ":rollback_full",
    ]
    if has_internal:
        lines += [
            'if exist "%CUR_INTERNAL%.old" (',
            '  if exist "%CUR_INTERNAL%" rmdir /s /q "%CUR_INTERNAL%" >nul 2>&1',
            '  move /y "%CUR_INTERNAL%.old" "%CUR_INTERNAL%" >nul 2>&1',
            ")",
        ]
    lines += [
        'move /y "%CUR_EXE%.old" "%CUR_EXE%" >nul 2>&1',
        'echo %date% %time% Update rollback: exe swap failed >> "%UPDATE_LOG%" 2>nul',
        "goto abort_relaunch",
        "",
        ":timeout_abort",
        f'echo %date% %time% Update cancelled: app did not close after {max_wait_attempts} tries >> "%UPDATE_LOG%" 2>nul',
        "",
        ":abort_relaunch",
        'rmdir /s /q "%PAYLOAD_DIR%" >nul 2>&1',
        'if exist "%CUR_EXE%" start "" "%CUR_EXE%"',
        'del "%~f0"',
        "exit /b 1",
    ]

    return "\r\n".join(lines) + "\r\n"


def cleanup_old_update_leftovers(root: Path) -> None:
    """Удалить FlowZap.exe.old и _internal.old, оставшиеся после
    обновления. Вызывается один раз при следующем успешном старте
    приложения — если .old-файлы ещё существуют, значит предыдущий
    запуск дошёл до конца благополучно и откат больше не понадобится.
    """
    exe_old = root / "FlowZap.exe.old"
    internal_old = root / "_internal.old"
    for path in (exe_old, internal_old):
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            logger.info(f"Удалён хвост предыдущего обновления: {path.name}")
        except Exception as e:
            logger.warning(f"Не удалось удалить {path.name}: {e}")


def download_and_install_exe(
    install_dir: Path,
    repo: str = FLOWZAP_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request, sys

            _log("Проверяем обновления...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_exe_asset(release)
            if not asset:
                raise ValueError(f"Файл для обновления не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            asset_name = asset["name"]
            _log("Скачивается...")

            data = None
            github_error: Optional[Exception] = None

            # Попытка 1: GitHub напрямую (15 сек ждём ответа, дальше таймаут
            # шире — обрывает только зависание уже начавшейся закачки)
            try:
                data = _download_with_grace(dl_url, _github_headers())
            except Exception as e:
                github_error = e
                logger.warning(f"Скачивание с GitHub не удалось: {e}")

            # Попытка 2: зеркало GitLab — молча, без вывода ошибки пользователю
            if data is None:
                logger.info("Пробуем зеркало GitLab...")
                try:
                    gitlab_release = _get_from_gitlab()
                    if not gitlab_release:
                        raise ValueError("GitLab недоступен")
                    gitlab_asset = find_exe_asset(gitlab_release)
                    if not gitlab_asset:
                        raise ValueError("Файл для обновления не найден в релизе GitLab")
                    mirror_req = urllib.request.Request(
                        gitlab_asset["browser_download_url"],
                        headers={"User-Agent": "FlowZap/1.0"},
                    )
                    with urllib.request.urlopen(mirror_req, timeout=120, context=_ssl_context()) as r:
                        data = r.read()
                    asset_name = gitlab_asset["name"]
                    tag = gitlab_release.get("tag_name", tag)
                    logger.info(f"Зеркало GitLab: скачано {len(data)} байт")
                except Exception as mirror_error:
                    # Оба источника недоступны — вот теперь показываем ошибку пользователю
                    logger.error(f"GitHub: {github_error}. GitLab: {mirror_error}")
                    raise ValueError(
                        "Не удалось скачать обновление с GitHub. Резервный "
                        "источник тоже не дал результата. Попробуйте позже."
                    )

            current_exe = (
                Path(sys.executable)
                if getattr(sys, "frozen", False)
                else install_dir / "FlowZap.exe"
            )
            target_dir = current_exe.parent
            current_internal = target_dir / "_internal"
            asset_lower = asset_name.lower()

            # Извлекаем во временную папку. Белый список — только
            # FlowZap.exe и _internal/, что бы ещё ни лежало в архиве
            # (см. _extract_update_payload).
            import tempfile as _tempfile, zipfile as _zipfile, io as _io

            if asset_lower.endswith(".zip"):
                total_size = 0
                try:
                    with _zipfile.ZipFile(_io.BytesIO(data)) as _zf:
                        total_size = sum(i.file_size for i in _zf.infolist())
                except Exception:
                    total_size = len(data) * 3  # архив не читается — грубая оценка

                space_error = _check_disk_space(
                    [Path(_tempfile.gettempdir()), target_dir],
                    required_bytes=total_size * 2,
                )
                if space_error:
                    raise ValueError(space_error)

                _log("Распаковываем...")
                payload_dir = _extract_update_payload(data)
                if not payload_dir:
                    raise ValueError("FlowZap.exe не найден внутри zip архива")
            else:
                payload_dir = Path(_tempfile.mkdtemp(prefix="flowzap_update_"))
                (payload_dir / "FlowZap.exe").write_bytes(data)

            new_exe = payload_dir / "FlowZap.exe"
            new_internal = payload_dir / "_internal"
            if not new_internal.is_dir():
                new_internal = None

            # Bat с опросом вместо слепого ожидания и полным откатом при
            # любой ошибке (см. _build_swap_script).
            update_log = target_dir / "logs" / "update.log"
            update_log.parent.mkdir(parents=True, exist_ok=True)
            bat_path = payload_dir / "_flowzap_update.bat"
            bat_path.write_text(
                _build_swap_script(
                    current_exe=current_exe,
                    current_internal=current_internal,
                    new_exe=new_exe,
                    new_internal=new_internal,
                    payload_dir=payload_dir,
                    update_log=update_log,
                ),
                encoding="utf-8",
            )

            # Запускаем bat с правами администратора через ShellExecute runas
            import subprocess as _sp, ctypes as _ct
            try:
                # ShellExecute с runas — покажет UAC если нужно
                _ct.windll.shell32.ShellExecuteW(
                    None, "runas", "cmd.exe",
                    f'/c "{bat_path}"',
                    None, 0  # SW_HIDE
                )
            except Exception:
                # Fallback без UAC
                _sp.Popen(
                    ["cmd.exe", "/c", str(bat_path)],
                    creationflags=0x08000000,
                    close_fds=True,
                )

            _log(f"✓ Обновление до {tag} готово. Приложение перезапустится...")
            if on_done:
                on_done(True, f"Обновлено до {tag}. Перезапускаем...")

        except Exception as exc:
            logger.error(f"Ошибка обновления: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="flowzap-updater").start()


# ── Core (zapret) ──────────────────────────────────────────────────────

# Зеркало на SourceForge — точная копия Flowseal/zapret-discord-youtube,
# используется как молчаливый fallback, если GitHub недоступен из сети.
SOURCEFORGE_ZAPRET_PROJECT = "flowseal.mirror"


def _sourceforge_mirror_url(project: str, tag: str, filename: str) -> str:
    """
    Точная ссылка на конкретный файл конкретного релиза на SourceForge.
    /files/latest/download НЕ годится — SourceForge отдаёт по ней первый
    файл в списке релиза (часто это "Source code.tar.gz", а не нужный
    exe/zip), независимо от того, какая версия реально нужна.
    """
    return f"https://sourceforge.net/projects/{project}/files/{tag}/{filename}/download"


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
    import subprocess, os, time
    if os.name != "nt":
        return
    flags = 0x08000000  # CREATE_NO_WINDOW

    # Убиваем все winws.exe — они держат хэндл на драйвер
    try:
        subprocess.run(["taskkill", "/F", "/IM", "winws.exe"],
                       capture_output=True, creationflags=flags)
    except Exception:
        pass

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


def download_and_install_core(
    zapret_dir: Path,
    repo: str = "Flowseal/zapret-discord-youtube",
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request, json, zipfile, io, tempfile, os

            _log("Проверяем обновления...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_zip_asset(release)
            if not asset:
                raise ValueError(f"Архив zapret не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            _log("Скачивается...")

            data = None
            github_error: Optional[Exception] = None

            # Попытка 1: GitHub напрямую (15 сек ждём ответа, дальше таймаут
            # шире — обрывает только зависание уже начавшейся закачки)
            try:
                data = _download_with_grace(dl_url, _github_headers())
            except Exception as e:
                github_error = e
                logger.warning(f"Скачивание с GitHub не удалось: {e}")

            # Попытка 2: зеркало SourceForge — молча, без вывода ошибки пользователю.
            # URL строится из tag и имени файла с GitHub, поэтому если скачивание
            # успешно — это гарантированно тот же файл той же версии, отдельная
            # сверка версии не нужна.
            if data is None:
                logger.info("Пробуем зеркало SourceForge...")
                try:
                    mirror_url = _sourceforge_mirror_url(
                        SOURCEFORGE_ZAPRET_PROJECT, tag, asset["name"]
                    )
                    mirror_req = urllib.request.Request(
                        mirror_url,
                        headers={"User-Agent": "FlowZap/1.0"},
                    )
                    with urllib.request.urlopen(mirror_req, timeout=120, context=_ssl_context()) as r:
                        data = r.read()
                    logger.info(f"Зеркало SourceForge: скачано {len(data)} байт")
                except Exception as mirror_error:
                    # Оба источника недоступны — вот теперь показываем ошибку пользователю
                    logger.error(f"GitHub: {github_error}. SourceForge: {mirror_error}")
                    raise ValueError(
                        "Не удалось скачать обновление с GitHub. Резервный "
                        "источник тоже не дал результата. Попробуйте позже."
                    )

            logger.info("Останавливаем WinDivert...")
            _unload_windivert()
            _log("Распаковываем...")
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(tmp)

                entries = os.listdir(tmp)
                if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
                    src = Path(tmp) / entries[0]
                else:
                    src = Path(tmp)

                zapret_dir.mkdir(parents=True, exist_ok=True)

                def _merge_copy(src_dir: Path, dst_dir: Path) -> None:
                    """Рекурсивно копирует файлы с заменой, не трогая лишние файлы в dst."""
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    for item in src_dir.iterdir():
                        dst_item = dst_dir / item.name
                        if item.is_dir():
                            _merge_copy(item, dst_item)
                        else:
                            # Пропускаем *users.txt — пользовательские списки
                            if item.name.endswith("users.txt"):
                                logger.debug(f"Пропущен пользовательский файл: {item.name}")
                                continue
                            shutil.copy2(item, dst_item)

                _merge_copy(src, zapret_dir)

                (zapret_dir / "version.txt").write_text(tag, encoding="utf-8")

            # Создаём пустые пользовательские файлы —
            # winws.exe падает если файл указан в аргументах но не существует
            _lists_dir = zapret_dir / "lists"
            _lists_dir.mkdir(parents=True, exist_ok=True)
            for _uf in [
                "list-general-user.txt",
                "list-exclude-user.txt",
                "list-exclude.txt",
                "ipset-exclude-user.txt",
                "ipset-exclude.txt",
            ]:
                _fp = _lists_dir / _uf
                if not _fp.exists():
                    try:
                        _fp.touch()
                        logger.info(f"Создан пустой файл: {_uf}")
                    except Exception as _e:
                        logger.warning(f"Не удалось создать {_uf}: {_e}")

            _log(f"✓ zapret обновлён до {tag}.")

            # Прогрев WinDivert — первый запуск после установки требует
            # загрузки драйвера в ядро, иначе первый тест пресетов упадёт
            try:
                import subprocess, time
                winws = zapret_dir / "bin" / "winws.exe"
                if winws.exists():
                    logger.info("Инициализация WinDivert...")
                    proc = subprocess.Popen(
                        [str(winws), "--wf-tcp=80"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=0x08000000,
                    )
                    time.sleep(3)
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
                    logger.info("WinDivert инициализирован")
            except Exception as e:
                logger.debug(f"Прогрев WinDivert: {e}")

            if on_done:
                on_done(True, f"zapret обновлён до {tag}")

        except Exception as exc:
            logger.error(f"Ошибка обновления Core: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="core-updater").start()


# ── TG WS Proxy ────────────────────────────────────────────────────────

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


def _get_exe_version(exe_path: Path) -> Optional[str]:
    """
    Читает версию, зашитую в PE-ресурсы exe (FileVersion). Не зависит
    от того, откуда скачан файл — с GitHub или с зеркала — поэтому
    надёжнее сверки размера с API релиза.
    """
    import ctypes

    class _FixedFileInfo(ctypes.Structure):
        _fields_ = [
            ("dwSignature",        ctypes.c_uint32),
            ("dwStrucVersion",     ctypes.c_uint32),
            ("dwFileVersionMS",    ctypes.c_uint32),
            ("dwFileVersionLS",    ctypes.c_uint32),
            ("dwProductVersionMS", ctypes.c_uint32),
            ("dwProductVersionLS", ctypes.c_uint32),
        ]

    try:
        path = str(exe_path)
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        res = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, res):
            return None
        r = ctypes.c_void_p()
        l = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(res, "\\", ctypes.byref(r), ctypes.byref(l)):
            return None
        ffi = _FixedFileInfo.from_address(r.value)
        parts = [
            ffi.dwFileVersionMS >> 16, ffi.dwFileVersionMS & 0xFFFF,
            ffi.dwFileVersionLS >> 16, ffi.dwFileVersionLS & 0xFFFF,
        ]
        if parts[-1] == 0:
            parts = parts[:3]  # убираем нулевую 4-ю часть — как в теге на GitHub
        return ".".join(str(p) for p in parts)
    except Exception as e:
        logger.debug(f"Не удалось прочитать версию из exe: {e}")
        return None


def download_and_install_tg_proxy(
    tgproxy_dir: Path,
    repo: str = TG_PROXY_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    def _worker() -> None:
        try:
            import urllib.request

            _log("Проверяем обновления...")
            release = get_latest_release(repo)
            if not release:
                raise ValueError("Не удалось получить информацию о релизе")

            tag = release.get("tag_name", "unknown")
            asset = find_tg_proxy_asset(release)
            if not asset:
                raise ValueError(f"Файл TgWsProxy_windows.exe не найден в релизе {tag}")

            dl_url = asset["browser_download_url"]
            expected_size = asset.get("size", 0)
            _log("Скачивается...")

            data = None
            github_error: Optional[Exception] = None

            # Попытка 1: GitHub напрямую (15 сек ждём ответа, дальше таймаут
            # шире — обрывает только зависание уже начавшейся закачки)
            try:
                data = _download_with_grace(dl_url, _github_headers())
            except Exception as e:
                github_error = e
                logger.warning(f"Скачивание с GitHub не удалось: {e}")

            # Попытка 2: зеркало SourceForge — молча, без вывода ошибки пользователю.
            # URL строится из tag и имени файла с GitHub, поэтому если скачивание
            # успешно — это гарантированно тот же файл той же версии.
            if data is None:
                logger.info("Пробуем зеркало SourceForge...")
                try:
                    mirror_url = _sourceforge_mirror_url(
                        SOURCEFORGE_TGPROXY_PROJECT, tag, asset["name"]
                    )
                    mirror_req = urllib.request.Request(
                        mirror_url,
                        headers={"User-Agent": "FlowZap/1.0"},
                    )
                    with urllib.request.urlopen(mirror_req, timeout=120, context=_ssl_context()) as r:
                        data = r.read()
                    logger.info(f"Зеркало SourceForge: скачано {len(data)} байт")
                except Exception as mirror_error:
                    # Оба источника недоступны — вот теперь показываем ошибку пользователю
                    logger.error(f"GitHub: {github_error}. SourceForge: {mirror_error}")
                    raise ValueError(
                        "Не удалось скачать обновление с GitHub. Резервный "
                        "источник тоже не дал результата. Попробуйте позже."
                    )

            # Сверяем размер скачанного файла с ожидаемым из GitHub API — это
            # проверка на оборванную/повреждённую загрузку (с любого источника).
            # На версию не влияет: URL зеркала уже пинует нужный tag, поэтому
            # раз скачивание удалось — версия верна независимо от размера.
            if expected_size and len(data) != expected_size:
                logger.warning(
                    f"Размер скачанного файла ({len(data)}) не совпадает с "
                    f"ожидаемым из GitHub API ({expected_size}) — файл может "
                    f"быть повреждён"
                )

            tgproxy_dir.mkdir(parents=True, exist_ok=True)
            exe_path = tgproxy_dir / "TgWsProxy_windows.exe"
            exe_path.write_bytes(data)

            # Тег с GitHub — основной источник версии: URL зеркала уже
            # пинуется к конкретному tag+имени файла, так что любая успешная
            # загрузка (с GitHub или с зеркала) гарантированно соответствует
            # этому тегу. Версию из ресурсов exe используем только для
            # диагностики в логах — она иногда отстаёт от тега, если автор
            # релиза забыл обновить её внутри файла, и НЕ должна перебивать
            # тег (иначе апдейтер вечно считает, что доступно обновление,
            # даже после успешной установки).
            actual_version = _get_exe_version(exe_path)
            if actual_version and actual_version != tag.lstrip("v"):
                logger.info(
                    f"Тег релиза {tag}, но версия в ресурсах exe: "
                    f"{actual_version} (используем тег)"
                )
            installed_version = tag
            (tgproxy_dir / "version.txt").write_text(installed_version, encoding="utf-8")

            _log(f"✓ TG WS Proxy установлен ({installed_version})")
            if on_done:
                on_done(True, f"TG WS Proxy установлен ({installed_version})")

        except Exception as exc:
            logger.error(f"Ошибка установки TG Proxy: {exc}")
            _log(f"✗ Ошибка: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="tgproxy-updater").start()


# ── Gaming lists (medvedeff-true/ru-gaming-blocklist) ──────────────────

GAMING_LISTS_REPO_RAW = "https://raw.githubusercontent.com/medvedeff-true/ru-gaming-blocklist/main"
GAMING_LIST_DOMAINS        = "flowzap-game-list-all.txt"
GAMING_LIST_IPSET          = "flowzap-game-ipset.txt"
_GAMING_REMOTE_DOMAINS     = "medvedeff-game-list-all.txt"
_GAMING_REMOTE_IPSET       = "medvedeff-game-ipset.txt"
GAMING_UPDATE_INTERVAL = 6 * 3600  # 6 часов в секундах
_GAMING_STAMP_FILE     = "gaming_lists_updated.txt"


def update_gaming_lists(
    lists_dir: Path,
    force: bool = False,
    on_done: Optional[Callable[[bool, str], None]] = None,
) -> None:
    """
    Скачать игровые списки доменов и IP в фоновом потоке.
    Пропускает загрузку если с последнего обновления прошло менее 6 часов.
    force=True — игнорировать кэш.
    """
    def _worker() -> None:
        import urllib.request, time

        stamp_file = lists_dir / _GAMING_STAMP_FILE

        # Проверяем кэш
        if not force and stamp_file.exists():
            try:
                last = float(stamp_file.read_text(encoding="utf-8").strip())
                if time.time() - last < GAMING_UPDATE_INTERVAL:
                    logger.debug("Игровые списки актуальны, пропускаем загрузку")
                    if on_done:
                        on_done(True, "актуальны")
                    return
            except Exception:
                pass

        try:
            lists_dir.mkdir(parents=True, exist_ok=True)
            for remote, local in (
                (_GAMING_REMOTE_DOMAINS, GAMING_LIST_DOMAINS),
                (_GAMING_REMOTE_IPSET,   GAMING_LIST_IPSET),
            ):
                url = f"{GAMING_LISTS_REPO_RAW}/{remote}"
                logger.info(f"Обновление игрового списка: {remote} -> {local}")
                req = urllib.request.Request(url, headers={"User-Agent": "FlowZap/1.0"})
                with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as r:
                    data = r.read()
                (lists_dir / local).write_bytes(data)
                logger.info(f"Сохранён: {local} ({len(data)} байт)")

            stamp_file.write_text(str(time.time()), encoding="utf-8")
            logger.info("Игровые списки обновлены")
            if on_done:
                on_done(True, "обновлены")

        except Exception as exc:
            logger.warning(f"Ошибка обновления игровых списков: {exc}")
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_worker, daemon=True, name="gaming-lists-updater").start()


# ── Xbox DNS — автообновление адреса с GitHub ──

XBOX_DNS_URL = (
    "https://raw.githubusercontent.com/xxFireflyxx/FlowZap-Zapret-GUI/"
    "main/core/xbox-dns.toml"
)

# Адрес, который был зашит дефолтом до появления автообновления. Нужен
# как база для сравнения на самом первом запуске приложения, у которого
# в config.toml ещё нет "_last_official" (т.е. обновление ни разу не
# применялось) — не пустое значение, а именно то, что реально стоит у
# всех, кто ни разу не трогал этот пресет руками.
XBOX_DNS_ORIGINAL_DEFAULT = {
    "ipv4_main":   "111.88.96.50",
    "ipv4_backup": "111.88.96.51",
    "ipv6_main":   "2a00:ab00:1233:26::50",
    "ipv6_backup": "2a00:ab00:1233:26::51",
}
_XBOX_DNS_FIELDS = ("ipv4_main", "ipv4_backup", "ipv6_main", "ipv6_backup")

_xbox_dns_cache: Optional[dict] = None
_xbox_dns_cache_ts: float = 0.0
_XBOX_DNS_CACHE_TTL = 6 * 3600  # 6 часов


def get_xbox_dns(force: bool = False) -> Optional[dict]:
    """Скачать актуальный адрес xbox-dns с GitHub (raw-файл, без лимитов
    GitHub API), с in-memory кэшем на 6 часов. None — если сеть недоступна
    или файл не читается: тогда обновление в этот раз просто не происходит,
    это не ошибка приложения."""
    import time, urllib.request, tomllib

    global _xbox_dns_cache, _xbox_dns_cache_ts

    if not force and _xbox_dns_cache is not None:
        if time.time() - _xbox_dns_cache_ts < _XBOX_DNS_CACHE_TTL:
            logger.debug("xbox-dns из кэша")
            return _xbox_dns_cache

    try:
        req = urllib.request.Request(XBOX_DNS_URL, headers={"User-Agent": "FlowZap/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
            raw = r.read()
        data = tomllib.loads(raw.decode("utf-8"))
        if not all(data.get(k) for k in _XBOX_DNS_FIELDS):
            logger.warning("xbox-dns.toml на GitHub неполный, пропускаем")
            return None
        result = {k: data[k] for k in _XBOX_DNS_FIELDS}
        _xbox_dns_cache = result
        _xbox_dns_cache_ts = time.time()
        return result
    except Exception as e:
        logger.warning(f"Не удалось скачать xbox-dns.toml: {e}")
        return None


def sync_xbox_dns(config: dict, force: bool = False) -> bool:
    """Подставить в config адрес xbox-dns из GitHub, если пользователь
    не менял его руками с прошлого автообновления. Возвращает True, если
    config был изменён и его нужно сохранить на диск (config.save_config()
    у вызывающего кода) — сама эта функция ничего не пишет на диск.

    Правило: сравниваем текущий адрес в пресете "xbox-dns" с тем, что
    приложение само подставило в прошлый раз (config["dns"]["_last_official"],
    а до первого автообновления — с исходным дефолтом). Если он изменился —
    значит пользователь вписал что-то своё, и мы его не трогаем.
    """
    new = get_xbox_dns(force=force)
    if new is None:
        return False

    dns = config.setdefault("dns", {})
    pairs = dns.get("pairs") or []
    pair = next(
        (p for p in pairs if isinstance(p, dict) and p.get("name") == "xbox-dns"),
        None,
    )
    if pair is None:
        return False

    baseline = dns.get("_last_official") or XBOX_DNS_ORIGINAL_DEFAULT
    current = {k: pair.get(k, "") for k in _XBOX_DNS_FIELDS}

    if current != baseline:
        logger.debug("xbox-dns изменён пользователем вручную, автообновление пропущено")
        return False

    if current == new:
        return False  # уже актуально

    pair["ipv4_main"] = new["ipv4_main"]
    pair["ipv4_backup"] = new["ipv4_backup"]
    pair["ipv6_main"] = new["ipv6_main"]
    pair["ipv6_backup"] = new["ipv6_backup"]
    # legacy-поля, которые читает dashboard как fallback (см. parameters.py)
    pair["main"] = new["ipv4_main"]
    pair["backup"] = new["ipv4_backup"]

    if pairs and pairs[0] is pair:
        # это активная пара — пересобираем плоский список servers так же,
        # как это делает parameters.py::_build_flat_servers()
        dns["servers"] = [new["ipv4_main"], new["ipv4_backup"]]

    dns["_last_official"] = dict(new)
    logger.info("Адрес xbox-dns обновлён автоматически")
    return True
