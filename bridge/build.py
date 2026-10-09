"""
bridge/build.py — сборка «моста» FlowZap.exe для обновления с 0.5.x
(см. FlowZapBridge.cs) встроенным в Windows компилятором C#.

    python bridge/build.py <полная сборка .zip> <куда положить FlowZap.exe> <версия>

Полная сборка (архив с папкой FlowZap/: FlowZap.exe, _internal/, …) вшивается
в мост ресурсом payload.zip. Вызывает release/release.py.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "FlowZapBridge.cs"
ICON = HERE.parent / "assets" / "icon.ico"
FRAMEWORK = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319"
REFERENCES = ["System.dll", "System.Core.dll", "System.IO.Compression.dll"]


def build(payload: Path, output: Path, version: str) -> Path:
    csc = FRAMEWORK / "csc.exe"
    if not csc.exists():
        raise SystemExit(f"Не найден компилятор C#: {csc}")
    nums = [int(x) for x in version.split(".") if x.isdigit()][:4]
    nums += [0] * (4 - len(nums))
    with tempfile.TemporaryDirectory() as tmp:
        version_cs = Path(tmp) / "BridgeVersion.cs"
        version_cs.write_text(
            "namespace FlowZap.Bridge { static partial class Info {\n"
            f'    public const string Version = "{version}";\n'
            f'    public const string AssemblyVersion = "{".".join(map(str, nums))}";\n'
            "} }\n", encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(csc), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
            "/codepage:65001", f"/out:{output}", f"/win32icon:{ICON}",
            f"/resource:{payload},payload.zip",
            *(f"/reference:{FRAMEWORK / r}" for r in REFERENCES),
            str(SOURCE), str(version_cs),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="cp866", errors="replace")
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n")
        raise SystemExit("Сборка моста FlowZap.exe не удалась")
    print(f"Собран мост {output} ({output.stat().st_size // 1024 // 1024} МБ)")
    return output


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    build(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
