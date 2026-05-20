"""
release.py — Сборщик релиза FlowZap
=====================================
Запускать из корневой папки проекта: python release.py

Что делает:
  1. Спрашивает номер версии
  2. Прописывает версию в core/updater.py
  3. Собирает FlowZap.exe через PyInstaller
  4. Упаковывает в flowzap-vX.X.X.zip и flowzap-vX.X.X.rar
  5. Сохраняет на Рабочий стол
"""

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
UPDATER_FILE = PROJECT_ROOT / "core" / "updater.py"

WINRAR_PATHS = [
    r"D:\WinRAR.exe",
    r"C:\Program Files\WinRAR\Rar.exe",
    r"C:\Program Files (x86)\WinRAR\Rar.exe",
    r"C:\Program Files\WinRAR\WinRAR.exe",
    r"C:\Program Files (x86)\WinRAR\WinRAR.exe",
]

CONFIG_TEMPLATE = """\
[zapret]
exe_path = "zapret/bin/winws.exe"
presets_dir = "zapret"
last_preset = "general"
args = []
autostart = false

[ui]
theme = "dark"
remember_tab = true

[updater]
repo = "xxFireflyxx/Flowzap-gui-zapret-dns-tgwsproxy"
check_on_start = true

[dns]
servers = []
"""


def ask_version() -> str:
    current = get_current_version()
    print(f"\n{'='*50}")
    print("  FlowZap — Сборщик релиза")
    print(f"{'='*50}")
    print(f"  Текущая версия: {current}")
    version = input(f"  Введите новую версию (Enter = оставить {current}): ").strip()
    if not version:
        version = current
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        print(f"  [WARN] Версия '{version}' не соответствует формату X.Y.Z, продолжаем...")
    return version


def get_current_version() -> str:
    content = UPDATER_FILE.read_text(encoding="utf-8")
    m = re.search(r'GUI_VERSION\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else "0.0.0"


def set_version(version: str) -> None:
    content = UPDATER_FILE.read_text(encoding="utf-8")
    new_content = re.sub(
        r'GUI_VERSION\s*=\s*"[^"]+"',
        f'GUI_VERSION = "{version}"',
        content,
    )
    UPDATER_FILE.write_text(new_content, encoding="utf-8")
    print(f"  [OK] Версия обновлена → {version} в core/updater.py")


def get_desktop() -> Path:
    return Path.home() / "Desktop"


def find_winrar() -> Path | None:
    for path in WINRAR_PATHS:
        p = Path(path)
        if p.exists():
            return p
    return None


def build_exe() -> bool:
    print("\n  Сборка exe (PyInstaller)...")
    spec_file = PROJECT_ROOT / "FlowZap.spec"

    if not spec_file.exists():
        print("  [WARN] FlowZap.spec не найден, запускаем через main.py")
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--onefile", "--windowed", "--name", "FlowZap",
            "--uac-admin",
            "--exclude-module", "unittest",
            "--exclude-module", "email",
            "--exclude-module", "html",
            "--exclude-module", "http",
            "--exclude-module", "xml",
            "--exclude-module", "pydoc",
            "--exclude-module", "doctest",
            "--exclude-module", "difflib",
            "--exclude-module", "ftplib",
            "--exclude-module", "getpass",
            "--exclude-module", "imaplib",
            "--exclude-module", "mailbox",
            "--exclude-module", "mimetypes",
            str(PROJECT_ROOT / "main.py"),
        ]
    else:
        cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--noconfirm"]

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print("  [ERROR] PyInstaller завершился с ошибкой")
        return False

    print("  [OK] PyInstaller завершён")
    return True


def find_dist_dir() -> Path | None:
    candidates = [
        PROJECT_ROOT / "dist" / "FlowZap",
        PROJECT_ROOT / "dist",
    ]
    for c in candidates:
        if (c / "FlowZap.exe").exists():
            return c
    return None


def ensure_config(dist_dir: Path) -> None:
    config_path = dist_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")


def pack_zip(version: str, dist_dir: Path, out_dir: Path) -> Path:
    tag = f"v{version}"
    zip_name = f"flowzap-{tag}.zip"
    zip_path = out_dir / zip_name

    print(f"\n  Упаковка в {zip_name}...")
    ensure_config(dist_dir)

    from pathlib import Path as _Path
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in dist_dir.rglob("*"):
            if file.is_file():
                # Кладём файлы в папку FlowZap внутри архива
                arcname = _Path("FlowZap") / file.relative_to(dist_dir)
                zf.write(file, arcname)
                print(f"    + {arcname}")

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"  [OK] ZIP создан: {zip_path} ({size_mb:.1f} МБ)")
    return zip_path


def pack_rar(version: str, dist_dir: Path, out_dir: Path, winrar: Path) -> Path | None:
    tag = f"v{version}"
    rar_name = f"flowzap-{tag}.rar"
    rar_path = out_dir / rar_name

    print(f"\n  Упаковка в {rar_name}...")
    ensure_config(dist_dir)

    exe_name = winrar.name.lower()
    # Создаём временную папку FlowZap чтобы в архиве была нужная структура
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        flowzap_dir = Path(tmp) / "FlowZap"
        shutil.copytree(dist_dir, flowzap_dir)

        if exe_name == "rar.exe":
            cmd = [
                str(winrar), "a",
                "-r", "-m5",
                str(rar_path),
                str(Path(tmp) / "*"),
            ]
        else:
            cmd = [
                str(winrar), "a",
                "-r", "-m5", "-ibck",
                str(rar_path),
                str(Path(tmp) / "*"),
            ]

        try:
            result = subprocess.run(cmd, cwd=tmp, capture_output=True)
            if result.returncode not in (0, 1):
                print(f"  [ERROR] WinRAR завершился с кодом {result.returncode}")
                print(f"  {result.stderr.decode(errors='replace')}")
                return None

            size_mb = rar_path.stat().st_size / 1024 / 1024
            print(f"  [OK] RAR создан: {rar_path} ({size_mb:.1f} МБ)")
            return rar_path

        except Exception as e:
            print(f"  [ERROR] Ошибка создания RAR: {e}")
            return None




def clean_build() -> None:
    for folder in ["build", "dist"]:
        p = PROJECT_ROOT / folder
        if p.exists():
            shutil.rmtree(p)
            print(f"  [OK] Удалена временная папка: {folder}/")


def main() -> None:
    version = ask_version()
    tag = f"v{version}"

    desktop = get_desktop()
    out_dir = desktop / f"FlowZap-{tag}"

    print(f"\n  Папка вывода: {out_dir}")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    winrar = find_winrar()
    if winrar:
        print(f"  [OK] WinRAR найден: {winrar}")
    else:
        print("  [WARN] WinRAR не найден — RAR архив создан не будет")

    set_version(version)

    exe_ok = build_exe()
    if not exe_ok:
        print("\n  [!] Сборка прервана — PyInstaller вернул ошибку")
        input("  Нажмите Enter для выхода...")
        return

    dist_dir = find_dist_dir()
    if not dist_dir:
        print("\n  [ERROR] Папка с exe не найдена после сборки")
        input("  Нажмите Enter для выхода...")
        return

    zip_path = pack_zip(version, dist_dir, out_dir)

    rar_path = None
    if winrar:
        rar_path = pack_rar(version, dist_dir, out_dir, winrar)

    clean_build()

    print(f"\n{'='*50}")
    print(f"  Готово! FlowZap {tag}")
    print(f"{'='*50}")
    print(f"  ZIP: {zip_path}")
    if rar_path:
        print(f"  RAR: {rar_path}")
    else:
        print(f"  RAR: не создан (WinRAR не найден)")
    print(f"\n  Загрузите оба файла в GitHub Releases")
    print(f"{'='*50}\n")

    input("  Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
