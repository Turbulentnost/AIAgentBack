from __future__ import annotations

from pathlib import Path

from app.config import settings


class CheckUploadStorage:
    def __init__(self) -> None:
        self._root = Path(settings.uploads_path)
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, *, sha256: str, filename: str, data: bytes) -> Path:
        digest = sha256.strip().lower()
        if not digest or not data:
            raise ValueError("Не удалось сохранить исходный файл проверки")
        safe_name = Path(filename or "upload.bin").name
        target_dir = self._root / digest
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / safe_name
        path.write_bytes(data)
        return path

    def load(self, *, sha256: str, filename: str | None = None) -> tuple[str, bytes] | None:
        digest = sha256.strip().lower()
        if not digest:
            return None
        target_dir = self._root / digest
        if not target_dir.is_dir():
            return None
        if filename:
            path = target_dir / Path(filename).name
            if path.is_file():
                return path.name, path.read_bytes()
        for path in sorted(target_dir.iterdir()):
            if path.is_file():
                return path.name, path.read_bytes()
        return None
