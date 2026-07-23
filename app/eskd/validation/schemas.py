from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EskdCheckSeverity = Literal["error", "warning", "info"]


@dataclass
class EskdCheckResult:
    code: str
    title: str
    passed: bool
    severity: EskdCheckSeverity
    message: str
    gost_reference: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EskdValidationReport:
    passed: bool
    score: float
    summary: str
    checks: list[EskdCheckResult]
    document_id: str | None = None
    registration_id: str | None = None
    designation: str | None = None
    document_kind: str | None = None
    text_available: bool = False
    validated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        errors = sum(1 for item in self.checks if not item.passed and item.severity == "error")
        warnings = sum(1 for item in self.checks if not item.passed and item.severity == "warning")
        return {
            "passed": self.passed,
            "score": round(self.score, 4),
            "summary": self.summary,
            "errors_count": errors,
            "warnings_count": warnings,
            "checks": [
                {
                    "code": item.code,
                    "title": item.title,
                    "passed": item.passed,
                    "severity": item.severity,
                    "message": item.message,
                    "gost_reference": item.gost_reference,
                    "details": item.details,
                }
                for item in self.checks
            ],
            "document_id": self.document_id,
            "registration_id": self.registration_id,
            "designation": self.designation,
            "document_kind": self.document_kind,
            "text_available": self.text_available,
            "validated_at": self.validated_at,
        }
