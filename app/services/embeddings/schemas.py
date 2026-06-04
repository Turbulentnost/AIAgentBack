from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EmbeddingStatus = Literal["completed", "failed"]


@dataclass(frozen=True)
class EmbeddingResult:
    text_hash: str
    vector: list[float]
    provider: str
    model: str
    vector_size: int
    status: EmbeddingStatus
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingBatchResult:
    items: list[EmbeddingResult]
    provider: str
    model: str
    vector_size: int
    total: int
    failed_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
