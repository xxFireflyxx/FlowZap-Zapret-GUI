"""utils/helpers.py — вспомогательные функции."""
import shlex
from pathlib import Path

def args_to_list(raw: str) -> list:
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()

def ensure_dirs(*paths) -> None:
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)
