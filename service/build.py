"""
service/build.py — сборка FlowZapService.exe встроенным в Windows
компилятором C# (.NET Framework 4, есть в любой Windows 10/11 — ставить
Visual Studio не нужно).

    python service/build.py

Результат — service/FlowZapService.exe рядом с исходником. FlowZap берёт его
оттуда (в собранном приложении — из _internal/service/).
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "FlowZapService.cs"
OUTPUT = HERE / "FlowZapService.exe"
FRAMEWORK = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319"

REFERENCES = ["System.dll", "System.Core.dll", "System.ServiceProcess.dll", "System.Web.Extensions.dll",
              "System.IO.Compression.dll"]


def build() -> Path:
    csc = FRAMEWORK / "csc.exe"
    if not csc.exists():
        raise SystemExit(f"Не найден компилятор C#: {csc}")
    cmd = [
        str(csc), "/nologo", "/target:exe", "/platform:x64", "/optimize+",
        "/codepage:65001", f"/out:{OUTPUT}",
        *(f"/reference:{FRAMEWORK / r}" for r in REFERENCES),
        str(SOURCE),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="cp866", errors="replace")
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n")
        raise SystemExit("Сборка FlowZapService.exe не удалась")
    if result.stdout.strip():
        print(result.stdout.strip())
    print(f"Собран {OUTPUT} ({OUTPUT.stat().st_size // 1024} КБ)")
    return OUTPUT


if __name__ == "__main__":
    build()
    sys.exit(0)
