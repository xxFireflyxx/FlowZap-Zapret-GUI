"""
core/updates/app.py
-------------------
Самообновление FlowZap: скачать архив релиза, проверить подпись автора
(core/updates/signing.py), распаковать FlowZap.exe и _internal/ рядом с
приложением и запустить PowerShell-скрипт замены, который дождётся выхода
приложения, заменит файлы (с откатом) и перезапустит.
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from core.updates import signing
from core.updates.releases import (
    FLOWZAP_REPO, _fetch, download_asset, get_from_gitlab, get_latest_release, latest_asset,
    run_install,
)

logger = logging.getLogger(__name__)

_UNSIGNED = ("Обновление {tag} не прошло проверку подписи ({reason}) — не устанавливаю. "
             "Если это не ошибка сети, скачайте FlowZap вручную со страницы проекта")


def _fetch_signature(repo: str, name: str, tag: str) -> bytes:
    """Файл подписи <архив>.sig того же релиза: GitHub, потом GitLab.
    SignatureError — нигде нет."""
    sig_name = name + signing.SIGNATURE_SUFFIX
    problems = []
    for source in (lambda: get_latest_release(repo), get_from_gitlab):
        try:
            release = source()
            if not release or release.get("tag_name") != tag:
                continue
            where = release.get("_source", "github")
            asset = next((a for a in release.get("assets", []) if a.get("name") == sig_name), None)
            if asset is None:
                problems.append(f"{where}: нет {sig_name}")
                continue
            if where == "gitlab":
                return _fetch(asset["browser_download_url"], timeout=30)
            return download_asset(asset)    # с проверкой размера и sha256 из GitHub
        except Exception as e:
            problems.append(str(e))
    logger.error(f"Подпись {sig_name} не получена: {'; '.join(problems) or 'релиз не найден'}")
    raise signing.SignatureError("нет файла подписи")


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

    # Приоритет 0: flowzap-full-vX.X.X.zip — запас на случай, если в релизе
    # появятся два архива (полный и служебный); обычно его нет
    for asset in real_assets:
        name = asset.get("name", "").lower()
        if name.startswith("flowzap-full-") and name.endswith(".zip"):
            return asset

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


def _extract_update_payload(data: bytes, parent: Path) -> Optional[Path]:
    """Распаковать архив обновления во временную папку внутри parent (папки
    приложения): перенос _internal на место — это переименование, а оно
    работает только в пределах одного диска. Из %TEMP% на C: в D:\\FlowZap
    перенести папку нельзя — обновление откатывалось.

    Белый список — только FlowZap.exe и, если он есть в архиве, папка
    _internal/, где бы они ни лежали внутри архива (обычно под префиксом
    FlowZap/). Всё остальное содержимое архива (config.toml-шаблон,
    случайные файлы) игнорируется и никогда не попадает на диск — так
    обновление не может затереть zapret/, tgproxy/, logs/ или config.toml
    пользователя.

    Возвращает временную папку с FlowZap.exe (и, если был в архиве,
    подпапкой _internal/) или None, если exe в архиве не нашёлся.
    """
    import zipfile, io

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

            temp_dir = _new_update_dir(parent)
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


def _ps_str(value) -> str:
    """Строка в одинарных кавычках PowerShell ('  удваивается)."""
    return "'" + str(value).replace("'", "''") + "'"


def _build_swap_script(
    app_pid: int,
    current_exe: Path,
    current_internal: Path,
    new_exe: Path,
    new_internal: Optional[Path],
    payload_dir: Path,
    update_log: Path,
    wait_seconds: int = 90,
) -> str:
    """Собрать PowerShell-скрипт, который меняет FlowZap.exe (+ _internal/)
    после закрытия приложения и откатывает всё при любой ошибке.

    Почему так, а не .bat, как раньше:
      * закрытие ждём по PID. Раньше признаком был удавшийся move exe-файла,
        но Windows разрешает переименовать запущенный exe — скрипт шёл дальше
        сразу, пока приложение ещё работало, и перенос _internal с
        загруженными DLL срывался → откат;
      * .bat читается в кодировке OEM (cp866), а писался в UTF-8 —
        кириллица в пути к приложению ломала все пути;
      * .bat лежал в удаляемой папке и удалял её до строки с перезапуском —
        cmd после этого дальше не выполняет, приложение не поднималось.
    Скрипт пишется в UTF-8 с BOM (иначе PowerShell 5.1 читает его в ANSI)."""
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$log = {_ps_str(update_log)}",
        f"$curExe = {_ps_str(current_exe)}",
        f"$curInt = {_ps_str(current_internal)}",
        f"$newExe = {_ps_str(new_exe)}",
        f"$newInt = {_ps_str(new_internal) if new_internal else chr(39) * 2}",
        f"$payload = {_ps_str(payload_dir)}",
        f"$appPid = {int(app_pid)}",
        "$exeOld = $curExe + '.old'",
        "$intOld = $curInt + '.old'",
        "",
        "function Log($m) {",
        "    try { Add-Content -LiteralPath $log -Encoding UTF8 -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) } catch {}",
        "}",
        "# Несколько попыток: антивирус или индексатор могут ненадолго держать файл",
        "function Try-Move($src, $dst) {",
        "    for ($i = 0; $i -lt 20; $i++) {",
        "        try { Move-Item -LiteralPath $src -Destination $dst -Force; return $true }",
        "        catch { Start-Sleep -Milliseconds 500 }",
        "    }",
        "    Log ('move failed: ' + $src + ' -> ' + $dst + ': ' + $Error[0].Exception.Message)",
        "    return $false",
        "}",
        "function Finish($ok) {",
        "    Remove-Item -LiteralPath $payload -Recurse -Force -ErrorAction SilentlyContinue",
        "    if (Test-Path -LiteralPath $curExe) {",
        "        Start-Process -FilePath $curExe -WorkingDirectory (Split-Path -Parent $curExe)",
        "    }",
        "    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
        "    if ($ok) { exit 0 } else { exit 1 }",
        "}",
        "function Restore-Exe { if (Test-Path -LiteralPath $exeOld) { Try-Move $exeOld $curExe | Out-Null } }",
        "function Restore-Int {",
        "    if (Test-Path -LiteralPath $intOld) {",
        "        if (Test-Path -LiteralPath $curInt) { Remove-Item -LiteralPath $curInt -Recurse -Force -ErrorAction SilentlyContinue }",
        "        Try-Move $intOld $curInt | Out-Null",
        "    }",
        "}",
        "",
        "# 1. Ждём, пока приложение завершится (shutdown сбрасывает DNS и останавливает zapret)",
        "$proc = Get-Process -Id $appPid -ErrorAction SilentlyContinue",
        f"if ($proc -and -not $proc.WaitForExit({int(wait_seconds) * 1000})) {{",
        f"    Log 'Update cancelled: app did not close in {int(wait_seconds)} s'",
        "    Remove-Item -LiteralPath $payload -Recurse -Force -ErrorAction SilentlyContinue",
        "    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
        "    exit 1",
        "}",
        "",
        "# 2. Старые копии в сторону, новые на место",
        "Remove-Item -LiteralPath $exeOld -Force -ErrorAction SilentlyContinue",
        "if (-not (Try-Move $curExe $exeOld)) { Log 'Update cancelled: exe is locked'; Finish $false }",
        "if ($newInt) {",
        "    if (Test-Path -LiteralPath $intOld) { Remove-Item -LiteralPath $intOld -Recurse -Force -ErrorAction SilentlyContinue }",
        "    if ((Test-Path -LiteralPath $curInt) -and -not (Try-Move $curInt $intOld)) {",
        "        Restore-Exe; Log 'Update rollback: _internal is locked'; Finish $false",
        "    }",
        "    if (-not (Try-Move $newInt $curInt)) {",
        "        Restore-Int; Restore-Exe; Log 'Update rollback: new _internal not placed'; Finish $false",
        "    }",
        "}",
        "if (-not (Try-Move $newExe $curExe)) {",
        "    Restore-Int; Restore-Exe; Log 'Update rollback: new exe not placed'; Finish $false",
        "}",
        "",
        "# 3. Успех: убрать старые копии, запустить новую версию",
        "Remove-Item -LiteralPath $exeOld -Force -ErrorAction SilentlyContinue",
        "Remove-Item -LiteralPath $intOld -Recurse -Force -ErrorAction SilentlyContinue",
        "Log ('Update applied: ' + $curExe)",
        "Finish $true",
    ]
    return "\r\n".join(lines) + "\r\n"


def _launch_swap_script(script_path: Path) -> None:
    """Запустить скрипт замены скрыто и так, чтобы он пережил выход
    приложения. ShellExecute выводит процесс из job-объекта родителя. Без
    «runas»: папка приложения доступна на запись (обновление уже распаковано
    в неё), поэтому прав администратора не нужно и окна UAC не будет. Ошибка
    запуска — исключение (раньше результат ShellExecute не проверялся и
    пользователь видел «обновлено», хотя скрипт не стартовал)."""
    import ctypes
    import subprocess
    params = f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script_path}"'
    ret = 0
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(None, "open", "powershell.exe", params, None, 0)
    except Exception as e:
        logger.warning(f"ShellExecute: {e}")
    if ret > 32:
        return
    logger.warning(f"ShellExecute вернул {ret} — запускаю напрямую")
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-WindowStyle", "Hidden", "-File", str(script_path)]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # CREATE_BREAKAWAY_FROM_JOB — чтобы скрипт не умер вместе с приложением
        subprocess.Popen(cmd, creationflags=flags | 0x01000000, close_fds=True)
    except OSError:
        # job-объект не разрешает выход — запускаем как есть
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)


def _new_update_dir(parent: Path) -> Path:
    """Папка для распаковки обновления рядом с FlowZap. Не tempfile.mkdtemp:
    с Python 3.13 она получает права «только владелец», и они переезжают
    вместе с файлами в папку FlowZap. Если FlowZap обновлялся от
    администратора, владелец — группа администраторов, и новый FlowZap.exe
    потом не запустить ни щелчком, ни автозапуском (так сломалось
    самообновление 0.5.2). Обычная папка наследует права папки FlowZap."""
    import uuid
    path = parent / f"_flowzap_update_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


def cleanup_old_update_leftovers(root: Path) -> None:
    """Удалить FlowZap.exe.old и _internal.old, оставшиеся после
    обновления. Вызывается один раз при следующем успешном старте
    приложения — если .old-файлы ещё существуют, значит предыдущий
    запуск дошёл до конца благополучно и откат больше не понадобится.
    """
    exe_old = root / "FlowZap.exe.old"
    internal_old = root / "_internal.old"
    leftovers = [exe_old, internal_old, *root.glob("_flowzap_update_*")]
    for path in leftovers:
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


def _install_via_service(current_exe: Path, payload_dir: Path) -> bool:
    """Отдать замену файлов фоновой службе (FlowZapService.cs, AppUpdate):
    она дождётся выхода FlowZap, заменит FlowZap.exe и _internal/ с откатом и
    запустит новую версию — от имени пользователя, его правами. Бета:
    включается строкой via_service = true в [updater] config.toml.
    False — служба задание не приняла (нет её, старая версия, ошибка):
    обновляем по-старому, скриптом PowerShell."""
    from core.service import client as service_client
    try:
        if not service_client.service_installed():
            logger.info("Обновление через службу: служба не установлена — ставлю скриптом")
            return False
        service_client.session.update_app(current_exe, payload_dir)
    except Exception as e:
        logger.warning(f"Обновление через службу не вышло ({e}) — ставлю скриптом")
        return False
    logger.info("Обновление ставит фоновая служба (бета): после закрытия FlowZap")
    return True


def download_and_install_exe(
    install_dir: Path,
    repo: str = FLOWZAP_REPO,
    on_progress: Optional[Callable[[str], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
    via_service: bool = False,
) -> None:
    def body(log: Callable[[str], None]) -> str:
        import sys, zipfile, io

        tag, asset = latest_asset(repo, find_exe_asset, "Файл для обновления", log)

        # Зеркало — релиз на GitLab. Если там тот же тег и файл — сверяем с GitHub.
        from_gitlab: dict = {}

        def gitlab_mirror() -> tuple[str, bool]:
            release = get_from_gitlab()
            if not release:
                raise ValueError("GitLab недоступен")
            # Только та же версия: иначе (пре-релиз с GitHub, а на GitLab —
            # прежний релиз) FlowZap «обновился» бы не на то, что предложил
            if release.get("tag_name") != tag:
                raise ValueError(f"на GitLab версия {release.get('tag_name')}, а нужна {tag}")
            gl_asset = find_exe_asset(release)
            if not gl_asset:
                raise ValueError("Файл для обновления не найден в релизе GitLab")
            from_gitlab.update(name=gl_asset["name"], tag=tag)
            return gl_asset["browser_download_url"], gl_asset["name"] == asset["name"]

        data = download_asset(asset, gitlab_mirror, "зеркало GitLab")
        asset_name = from_gitlab.get("name", asset["name"])
        tag = from_gitlab.get("tag", tag)

        log("Проверяем подпись...")
        try:
            key_id = signing.verify(data, asset_name, tag, _fetch_signature(repo, asset_name, tag))
        except signing.SignatureError as e:
            logger.error(f"Обновление {tag} ({asset_name}): подпись не прошла проверку — {e}")
            raise ValueError(_UNSIGNED.format(tag=tag, reason=e))
        logger.info(f"Обновление {tag}: подпись верна (ключ {key_id})")

        current_exe = (
            Path(sys.executable) if getattr(sys, "frozen", False) else install_dir / "FlowZap.exe"
        )
        target_dir = current_exe.parent

        # Во временную папку рядом с приложением. Белый список — только
        # FlowZap.exe и _internal/ (см. _extract_update_payload).
        if asset_name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    total_size = sum(i.file_size for i in zf.infolist())
            except Exception:
                total_size = len(data) * 3  # архив не читается — грубая оценка
            space_error = _check_disk_space([target_dir], required_bytes=total_size * 2)
            if space_error:
                raise ValueError(space_error)
            log("Распаковываем...")
            payload_dir = _extract_update_payload(data, target_dir)
            if not payload_dir:
                raise ValueError("FlowZap.exe не найден внутри zip архива")
        else:
            payload_dir = _new_update_dir(target_dir)
            (payload_dir / "FlowZap.exe").write_bytes(data)

        if via_service and _install_via_service(current_exe, payload_dir):
            log(f"✓ Обновление до {tag} готово. Приложение перезапустится...")
            return f"Обновлено до {tag}. Перезапускаем..."

        new_internal = payload_dir / "_internal"
        # Скрипт замены ждёт выхода этого процесса по PID и откатывает всё
        # при ошибке (см. _build_swap_script). Лежит вне payload_dir,
        # которую сам же удаляет.
        update_log = target_dir / "logs" / "update.log"
        update_log.parent.mkdir(parents=True, exist_ok=True)
        script_path = target_dir / f"_flowzap_update_{os.getpid()}.ps1"
        script_path.write_text(
            _build_swap_script(
                app_pid=os.getpid(),
                current_exe=current_exe,
                current_internal=target_dir / "_internal",
                new_exe=payload_dir / "FlowZap.exe",
                new_internal=new_internal if new_internal.is_dir() else None,
                payload_dir=payload_dir,
                update_log=update_log,
            ),
            encoding="utf-8-sig",
        )
        try:
            _launch_swap_script(script_path)
        except Exception as e:
            shutil.rmtree(payload_dir, ignore_errors=True)
            script_path.unlink(missing_ok=True)
            raise ValueError(f"Не удалось запустить установку обновления: {e}")

        log(f"✓ Обновление до {tag} готово. Приложение перезапустится...")
        return f"Обновлено до {tag}. Перезапускаем..."

    run_install("flowzap-updater", "Ошибка обновления", body, on_progress, on_done)
