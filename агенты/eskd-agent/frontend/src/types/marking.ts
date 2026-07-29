export type GostSeverity = "ok" | "warning" | "error";

export interface GostFinding {
  gost_key: string;
  severity: GostSeverity;
  pages: number[];
  note: string;
}

export interface PageLevelFinding {
  page: number;
  gost_findings: GostFinding[];
  note: string;
}

export interface MarkingDocumentPage {
  page: number;
  preview_url: string;
  width?: number | null;
  height?: number | null;
}

export interface MarkingDocument {
  id: string;
  designation: string | null;
  source_filename: string;
  pages: MarkingDocumentPage[];
  created_at: string;
  reused_existing?: boolean;
  has_saved_label?: boolean;
}

export interface MarkingDocumentLookup {
  found: boolean;
  document: MarkingDocument | null;
  marked_pages_count: number;
  label_updated_at: string | null;
}

export interface MarkingLabel {
  id: string;
  document_id: string;
  check_run_id: string | null;
  is_rework: boolean;
  document_level: GostFinding[];
  page_level: PageLevelFinding[];
  problem_report: string | null;
  created_at: string;
}

export interface MarkingLabelCreate {
  document_id: string;
  check_run_id?: string | null;
  is_rework?: boolean;
  document_level: GostFinding[];
  page_level: PageLevelFinding[];
  problem_report?: string;
}

export interface MarkingLabelUpdate {
  document_level: GostFinding[];
  page_level: PageLevelFinding[];
  problem_report?: string;
}

export interface MarkingLabelSuggested {
  found: boolean;
  source: "saved" | "check_run" | "none";
  label_id: string | null;
  check_run_id: string | null;
  page_level: PageLevelFinding[];
  problem_report: string;
}

export interface MarkingDocumentListItem {
  id: string;
  designation: string | null;
  source_filename: string;
  pages_count: number;
  created_at: string;
  latest_label_id: string | null;
  marked_pages_count: number;
  label_updated_at: string | null;
}

export interface GostStatItem {
  gost_key: string;
  title: string;
  error_count: number;
  warning_count: number;
  total: number;
  after_ai_error_count: number;
  after_ai_warning_count: number;
  after_ai_total: number;
}

export interface GostStatsResponse {
  items: GostStatItem[];
}
