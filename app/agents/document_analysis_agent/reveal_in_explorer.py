"""Открытие файла в проводнике Windows (explorer /select)."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"[^\w.\- ()А-Яа-яЁё]+", re.UNICODE)


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "file.xlsx"
    cleaned = _SAFE_NAME_RE.sub("_", name).strip(" ._")
    return cleaned or "file.xlsx"


def reveal_path_in_explorer(path: str) -> str:
    """Открыть проводник с выделением существующего файла. Возвращает abs path."""
    if sys.platform != "win32":
        raise OSError("Открытие в проводнике поддерживается только на Windows")
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Файл не найден: {target}")
    # /select,<path> — один аргумент, без пробела после запятой
    subprocess.Popen(["explorer", f"/select,{target}"], shell=False)
    return str(target)


def reveal_bytes_in_explorer(filename: str, content: bytes) -> str:
    """Сохранить копию во временную папку и выделить её в проводнике."""
    if sys.platform != "win32":
        raise OSError("Открытие в проводнике поддерживается только на Windows")
    folder = Path(tempfile.mkdtemp(prefix="aveon_reveal_"))
    target = folder / _safe_filename(filename)
    target.write_bytes(content)
    return reveal_path_in_explorer(str(target))
