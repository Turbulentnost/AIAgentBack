import type { CheckRunDetail } from "@/types/history";
import type { EskdCheckResponse, EskdItemReport } from "@/types/eskd";

export function detailToCheckResponse(detail: CheckRunDetail): EskdCheckResponse | null {
  const raw = detail.raw_result;
  if (!raw || typeof raw !== "object") return null;

  const items = Array.isArray(raw.items)
    ? raw.items.filter((row): row is EskdItemReport => Boolean(row && typeof row === "object"))
    : [];

  return {
    job_id: String(raw.job_id ?? detail.job_id),
    designation: (raw.designation as string | null | undefined) ?? detail.designation,
    model: String(raw.model ?? detail.model ?? ""),
    adapter: String(raw.adapter ?? detail.adapter ?? ""),
    total_items: Number(raw.total_items ?? items.length ?? detail.pages_count ?? 0),
    processed: Number(raw.processed ?? items.length ?? 0),
    failed: Number(raw.failed ?? 0),
    total_errors: Number(raw.total_errors ?? detail.total_errors ?? 0),
    total_warnings: Number(raw.total_warnings ?? detail.total_warnings ?? 0),
    total_infer_seconds: Number(raw.total_infer_seconds ?? 0),
    load_seconds: Number(raw.load_seconds ?? 0),
    progress_percent: Number(raw.progress_percent ?? 100),
    status: String(raw.status ?? detail.status ?? "done"),
    global_warnings: Array.isArray(raw.global_warnings) ? (raw.global_warnings as string[]) : [],
    items,
    report_text: String(raw.report_text ?? ""),
    extracted_texts: raw.extracted_texts as EskdCheckResponse["extracted_texts"],
    preprocess_warnings: raw.preprocess_warnings as string[] | undefined,
    summary: raw.summary as string | undefined,
    pipeline_mode: raw.pipeline_mode as string | undefined,
    document_index: raw.document_index as EskdCheckResponse["document_index"],
    package_errors: raw.package_errors as EskdCheckResponse["package_errors"],
    evaluator: raw.evaluator as string | null | undefined,
    gost_summary: raw.gost_summary as Record<string, unknown> | null | undefined
  };
}
