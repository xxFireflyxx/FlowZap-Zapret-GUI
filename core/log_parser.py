"""
core/log_parser.py
------------------
Парсинг строк лога zapret: извлечение тега, уровня, сообщения.
"""
import re
from dataclasses import dataclass
from typing import Optional

_TAG_RE = re.compile(r"\[([A-Z]+)\]")
_TIME_RE = re.compile(r"\[(\d{2}:\d{2}:\d{2})\]")

@dataclass
class LogEntry:
    raw:     str
    tag:     str = "DEFAULT"
    time:    Optional[str] = None
    message: str = ""

def parse_line(line: str) -> LogEntry:
    """Разобрать строку лога на компоненты."""
    entry = LogEntry(raw=line, message=line)
    tag_match = _TAG_RE.search(line)
    if tag_match:
        entry.tag = tag_match.group(1)
    time_match = _TIME_RE.search(line)
    if time_match:
        entry.time = time_match.group(1)
    return entry
