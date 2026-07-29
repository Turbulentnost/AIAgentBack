import type { GostSummaryData } from "@/components/GostSummaryForm";
import type { GostCatalogItem } from "@/types/history";
import type { CheckRunDetail } from "@/types/history";
import type { KnowledgeBaseItem } from "@/types/knowledgeBase";
import type { GostFinding, MarkingDocument, MarkingLabel, PageLevelFinding } from "@/types/marking";

export function formatReportDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU");
}

export function gostTitle(catalog: GostCatalogItem[], key: string): string {
  return catalog.find((c) => c.key === key)?.title ?? key;
}

export function buildMarkingGostSummary(
  catalog: GostCatalogItem[],
  pageFindings: PageLevelFinding[]
): GostSummaryData {
  const errors: Record<string, number[]> = {};
  const warnings: Record<string, number[]> = {};
  const violated = new Set<string>();

  for (const entry of pageFindings) {
    for (const f of entry.gost_findings) {
      if (f.severity === "ok") continue;
      violated.add(f.gost_key);
      const pages = f.pages.length ? f.pages : [entry.page];
      const bucket = f.severity === "error" ? errors : warnings;
      const prev = bucket[f.gost_key] ?? [];
      bucket[f.gost_key] = [...new Set([...prev, ...pages])].sort((a, b) => a - b);
    }
  }

  const passed = catalog.map((c) => c.key).filter((key) => !violated.has(key));
  return { passed, warnings, errors };
}

export interface MarkingViolationRow {
  gost_key: string;
  title: string;
  severity: "error" | "warning";
  pages: number[];
  note: string;
}

export function collectMarkingViolations(
  catalog: GostCatalogItem[],
  pageFindings: PageLevelFinding[]
): MarkingViolationRow[] {
  const map = new Map<string, MarkingViolationRow>();

  for (const entry of pageFindings) {
    for (const f of entry.gost_findings) {
      if (f.severity === "ok") continue;
      const pages = f.pages.length ? f.pages : [entry.page];
      const prev = map.get(f.gost_key);
      const noteParts = [f.note?.trim(), entry.note?.trim()].filter(Boolean);
      if (!prev) {
        map.set(f.gost_key, {
          gost_key: f.gost_key,
          title: gostTitle(catalog, f.gost_key),
          severity: f.severity === "error" ? "error" : "warning",
          pages: [...pages],
          note: noteParts.join("; ")
        });
        continue;
      }
      prev.pages = [...new Set([...prev.pages, ...pages])].sort((a, b) => a - b);
      if (f.severity === "error") prev.severity = "error";
      const mergedNote = [prev.note, ...noteParts].filter(Boolean).join("; ");
      prev.note = mergedNote;
    }
  }

  return [...map.values()].sort((a, b) => a.gost_key.localeCompare(b.gost_key));
}

export interface MarkingReportData {
  filename: string;
  designation: string | null;
  uploadedAt: string;
  uploadedBy: string | null;
  pagesCount: number;
  markingStatus: "saved" | "draft_ai" | "new";
  markedPagesCount: number;
  problemReport: string;
  aiCheck: {
    runId: string;
    date: string;
    executor: string | null;
    status: string;
    totalErrors: number;
    totalWarnings: number;
    gostSummary: GostSummaryData | null;
  } | null;
  kb: {
    checked: boolean;
    verifiedAt: string | null;
    verifiers: string[];
    lastCheckedAt: string | null;
    checkCount: number;
  } | null;
  labelSavedAt: string | null;
  humanViolations: MarkingViolationRow[];
  humanPassedCount: number;
  humanGostSummary: GostSummaryData;
}

export function buildMarkingReportData(params: {
  document: MarkingDocument;
  catalog: GostCatalogItem[];
  pageFindings: PageLevelFinding[];
  problemReport: string;
  labelId: string | null;
  draftCheckRunId: string | null;
  savedLabel: MarkingLabel | null | undefined;
  checkRun: CheckRunDetail | null | undefined;
  kbEntry: KnowledgeBaseItem | null | undefined;
}): MarkingReportData {
  const {
    document,
    catalog,
    pageFindings,
    problemReport,
    labelId,
    draftCheckRunId,
    savedLabel,
    checkRun,
    kbEntry
  } = params;

  const humanGostSummary = buildMarkingGostSummary(catalog, pageFindings);
  const humanViolations = collectMarkingViolations(catalog, pageFindings);
  const markedPagesCount = pageFindings.filter(
    (p) => p.gost_findings.some((f) => f.severity !== "ok") || p.note.trim()
  ).length;

  const markingStatus: MarkingReportData["markingStatus"] = labelId
    ? "saved"
    : draftCheckRunId
      ? "draft_ai"
      : "new";

  const checkRunId =
    draftCheckRunId ?? savedLabel?.check_run_id ?? kbEntry?.last_check_run_id ?? checkRun?.id ?? null;

  let aiCheck: MarkingReportData["aiCheck"] = null;
  if (checkRun && checkRunId) {
    aiCheck = {
      runId: checkRunId,
      date: formatReportDate(checkRun.created_at),
      executor: checkRun.created_by_name || checkRun.created_by_login || null,
      status: checkRun.status,
      totalErrors: checkRun.total_errors,
      totalWarnings: checkRun.total_warnings,
      gostSummary: checkRun.gost_summary ?? null
    };
  }

  const uploadedBy = checkRun?.created_by_name || checkRun?.created_by_login || null;

  return {
    filename: document.source_filename,
    designation: document.designation,
    uploadedAt: formatReportDate(document.created_at),
    uploadedBy,
    pagesCount: document.pages.length,
    markingStatus,
    markedPagesCount,
    problemReport: problemReport.trim(),
    aiCheck,
    kb: kbEntry
      ? {
          checked: kbEntry.checked,
          verifiedAt: kbEntry.human_verified_at,
          verifiers: kbEntry.verifiers,
          lastCheckedAt: kbEntry.last_checked_at,
          checkCount: kbEntry.check_count
        }
      : null,
    labelSavedAt: savedLabel ? formatReportDate(savedLabel.created_at) : null,
    humanViolations,
    humanPassedCount: humanGostSummary.passed.length,
    humanGostSummary
  };
}

export function severityLabel(severity: GostFinding["severity"] | "error" | "warning"): string {
  if (severity === "error") return "ошибка";
  if (severity === "warning") return "замечание";
  return "соответствует";
}

export function markingStatusLabel(status: MarkingReportData["markingStatus"]): string {
  if (status === "saved") return "сохранённая разметка";
  if (status === "draft_ai") return "черновик из проверки ИИ";
  return "новая разметка";
}

export function checkStatusLabel(status: string): string {
  if (status === "running") return "в процессе";
  if (status === "cancelled") return "отменено";
  if (status === "done" || status === "completed") return "завершена";
  return status;
}
