"""
release.py — Сборщик релиза FlowZap
=====================================
Запускать из корневой папки проекта: python release.py

Что делает:
  1. Спрашивает номер версии
  2. Прописывает версию в core/updater.py
  3. Собирает FlowZap.exe через PyInstaller
  4. Упаковывает exe + библиотеки в release-vX.X.X.zip
  5. Сохраняет на Рабочий стол
"""

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ── Настройки ─────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
UPDATER_FILE = PROJECT_ROOT / "core" / "updater.py"

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
# ──────────────────────────────────────────────────────────────────────


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


def build_exe() -> bool:
    """Запустить PyInstaller."""
    print("\n  Сборка exe (PyInstaller)...")
    spec_file = PROJECT_ROOT / "FlowZap.spec"

    if not spec_file.exists():
        print("  [WARN] FlowZap.spec не найден, запускаем через main.py")
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--onefile", "--windowed", "--name", "FlowZap",
            "--uac-admin",
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
    """Найти папку с собранным приложением."""
    candidates = [
        PROJECT_ROOT / "dist" / "FlowZap",  # onedir
        PROJECT_ROOT / "dist",               # onefile
    ]
    for c in candidates:
        if (c / "FlowZap.exe").exists():
            return c
    return None


def pack_release(version: str, dist_dir: Path, out_dir: Path) -> Path:
    """
    Упаковать содержимое dist_dir в release-vX.X.X.zip.
    Добавляет config.toml шаблон.
    Возвращает путь к zip файлу.
    """
    tag = f"v{version}"
    zip_name = f"release-{tag}.zip"
    zip_path = out_dir / zip_name

    print(f"\n  Упаковка в {zip_name}...")

    # Добавляем шаблон config.toml в dist папку перед упаковкой
    config_path = dist_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in dist_dir.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(dist_dir)
                zf.write(file, arcname)
                print(f"    + {arcname}")

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"  [OK] Архив создан: {zip_path} ({size_mb:.1f} МБ)")
    return zip_path


def clean_build() -> None:
    """Удалить папки build/ и dist/ после сборки."""
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

    # Удаляем старую папку если есть
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # 1. Обновляем версию в коде
    set_version(version)

    # 2. Собираем exe
    exe_ok = build_exe()
    if not exe_ok:
        print("\n  [!] Сборка прервана — PyInstaller вернул ошибку")
        input("  Нажмите Enter для выхода...")
        return

    # 3. Находим папку dist
    dist_dir = find_dist_dir()
    if not dist_dir:
        print("\n  [ERROR] Папка с exe не найдена после сборки")
        input("  Нажмите Enter для выхода...")
        return

    # 4. Упаковываем в zip
    zip_path = pack_release(version, dist_dir, out_dir)

    # 5. Чистим временные папки PyInstaller
    clean_build()

    # Итог
    print(f"\n{'='*50}")
    print(f"  Готово! FlowZap {tag}")
    print(f"{'='*50}")
    print(f"  Архив для релиза: {zip_path}")
    print(f"  Загрузите {zip_path.name} в GitHub Releases")
    print(f"{'='*50}\n")

    input("  Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
