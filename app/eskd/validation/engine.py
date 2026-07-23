from __future__ import annotations

from app.eskd.validation.rules import EskdValidationContext, run_all_checks
from app.eskd.validation.schemas import EskdCheckResult, EskdValidationReport


class EskdValidationEngine:
    ERROR_PENALTY = 0.18
    WARNING_PENALTY = 0.06

    def validate(self, context: EskdValidationContext) -> EskdValidationReport:
        checks = run_all_checks(context)
        score = self._compute_score(checks)
        errors = [item for item in checks if not item.passed and item.severity == "error"]
        passed = len(errors) == 0
        summary = self._build_summary(checks, passed=passed)
        return EskdValidationReport(
            passed=passed,
            score=score,
            summary=summary,
            checks=checks,
            designation=context.designation,
            document_kind=context.document_kind.value,
            text_available=bool(context.document_text.strip()),
        )

    def _compute_score(self, checks: list[EskdCheckResult]) -> float:
        score = 1.0
        for item in checks:
            if item.passed:
                continue
            if item.severity == "error":
                score -= self.ERROR_PENALTY
            elif item.severity == "warning":
                score -= self.WARNING_PENALTY
        return max(0.0, min(1.0, score))

    def _build_summary(self, checks: list[EskdCheckResult], *, passed: bool) -> str:
        failed_errors = [item for item in checks if not item.passed and item.severity == "error"]
        failed_warnings = [item for item in checks if not item.passed and item.severity == "warning"]
        if passed and not failed_warnings:
            return "Документ соответствует базовым требованиям ЕСКД."
        if passed:
            return f"Критических нарушений нет. Предупреждений: {len(failed_warnings)}."
        titles = ", ".join(item.title for item in failed_errors[:4])
        suffix = "…" if len(failed_errors) > 4 else ""
        return f"Выявлены нарушения ЕСКД: {titles}{suffix}."
