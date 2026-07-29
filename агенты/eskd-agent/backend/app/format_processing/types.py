"""Нормализация загруженных файлов: текст или PNG для vision-модели."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ArtifactKind = Literal["text", "image"]


@dataclass(frozen=True)
class ProcessedArtifact:
    """Результат конвертации одного фрагмента исходного файла."""

    source: str
    name: str
    kind: ArtifactKind
    data: bytes
    mime: str
    format: str
    meta: dict[str, str | int | float | bool] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if self.kind != "text":
            return ""
        return self.data.decode("utf-8", errors="replace")

    def as_model_upload(self) -> tuple[str, bytes, str] | None:
        """Файл для отправки в Gemma (PNG/PDF). Текст не отправляется в vision."""
        if self.kind == "image":
            return self.name, self.data, self.mime
        if self.mime == "application/pdf":
            return self.name, self.data, self.mime
        return None


@dataclass
class PreprocessResult:
    source: str
    artifacts: list[ProcessedArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def texts(self) -> list[ProcessedArtifact]:
        return [a for a in self.artifacts if a.kind == "text"]

    @property
    def images(self) -> list[ProcessedArtifact]:
        return [a for a in self.artifacts if a.kind == "image"]

    def model_files(self) -> list[tuple[str, bytes, str]]:
        out: list[tuple[str, bytes, str]] = []
        for art in self.artifacts:
            item = art.as_model_upload()
            if item:
                out.append(item)
        return out
