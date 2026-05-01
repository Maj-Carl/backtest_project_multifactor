"""API Key 加载：统一管理 key 路径。"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_API_KEY_FILE_PATH = Path(r"C:\投资\STOCK_API_KE.txt")


def load_api_key_from_file(file_path: Path | None = None) -> str | None:
    path = Path(file_path or DEFAULT_API_KEY_FILE_PATH)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig").strip()
    return text or None


def load_api_key(*, env_name: str = "STOCK_API_KEY") -> str | None:
    return load_api_key_from_file() or os.getenv(env_name)
