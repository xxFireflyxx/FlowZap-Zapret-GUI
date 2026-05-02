"""utils/github.py — работа с GitHub API."""
import urllib.request, json
from typing import Optional

def fetch_release_info(repo: str) -> Optional[dict]:
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r)
    except Exception:
        return None
