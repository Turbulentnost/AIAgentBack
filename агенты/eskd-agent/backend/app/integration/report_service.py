from __future__ import annotations

import json
from typing import Any

from app.integration.check_executor import classify_counts
from app.models.integration import IntegrationJob
from app.schemas.integration import FindingItem, FindingsResponse


class ReportService:
    @staticmethod
    def build_findings(job: IntegrationJob) -> FindingsResponse:
        payload = job.result_payload or {}
        items: list[FindingItem] = []
        for page_item in payload.get("items") or []:
            if not isinstance(page_item, dict):
                continue
            page_no = int(page_item.get("page") or page_item.get("index") or 0)
            for bucket, severity in (("errors", "critical"), ("warnings", "major")):
                for finding in page_item.get(bucket) or []:
                    if not isinstance(finding, dict):
                        continue
                    items.append(
                        FindingItem(
                            page=page_no,
                            severity=severity if bucket == "errors" else "minor",
                            code=str(finding.get("code") or finding.get("kind") or "issue"),
                            message=str(finding.get("message") or finding.get("text") or ""),
                            gost_reference=finding.get("gost_reference"),
                        )
                    )
        return FindingsResponse(check_id=job.id, items=items, total=len(items))

    @staticmethod
    def build_json_report(job: IntegrationJob, summary: dict[str, Any]) -> dict[str, Any]:
        payload = job.result_payload or {}
        critical, major, minor = classify_counts(payload)
        return {
            "schema_version": "1.0",
            "check_id": str(job.id),
            "request_id": job.request_id,
            "status": job.status,
            "result_status": job.result_status,
            "critical_count": critical,
            "major_count": major,
            "minor_count": minor,
            "blocks_workflow": job.blocks_workflow,
            "ruleset_version": job.ruleset_version,
            "is_stale": job.is_stale,
            "checked_at": job.completed_at.isoformat() if job.completed_at else None,
            "summary": summary,
            "result": payload,
        }

    @staticmethod
    def build_pdf_bytes(job: IntegrationJob, summary: dict[str, Any]) -> bytes:
        payload = job.result_payload or {}
        findings = ReportService.build_findings(job)
        lines = [
            "ПРОТОКОЛ ПРОВЕРКИ ЕСКD",
            "=" * 40,
            f"Check ID: {job.id}",
            f"Request ID: {job.request_id}",
            f"Статус: {job.status}",
            f"Результат: {job.result_status or '-'}",
            f"Ruleset: {job.ruleset_version or '-'}",
            f"Critical/Major/Minor: {job.critical_count}/{job.major_count}/{job.minor_count}",
            f"Blocks workflow: {'да' if job.blocks_workflow else 'нет'}",
            f"Stale: {'да' if job.is_stale else 'нет'}",
            "",
            "Сводка:",
            json.dumps(summary, ensure_ascii=False, indent=2),
            "",
            "Замечания:",
        ]
        for item in findings.items[:500]:
            lines.append(
                f"- [{item.severity}] лист {item.page} {item.code}: {item.message}"
            )
        report_text = str(payload.get("report_text") or "").strip()
        if report_text:
            lines.extend(["", "Текст отчёта:", report_text])
        body = "\n".join(lines)
        return _minimal_pdf(body)


def _minimal_pdf(text: str) -> bytes:
    """Generate a minimal valid PDF with plain text (no external deps)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = safe.split("\n")[:200]
    content_lines = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
    for idx, line in enumerate(lines):
        prefix = " (" if idx == 0 else "T* ("
        content_lines.append(f"{prefix}{line[:120]}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode()
        + stream
        + b"\nendstream\nendobj\n"
    )
    objects.append(
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
    )

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(pdf)
