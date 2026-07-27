export interface EskdRemark {
  kind: string;
  code: string;
  message: string;
  severity: string;
  gost_reference?: string | null;
  element?: string | null;
  text: string;
}

export interface EskdPosition {
  pos: string;
  designation?: string;
  title?: string;
  quantity?: string;
  zone?: string;
  order_index?: number;
  order_ok?: boolean;
  readable?: boolean;
}

export interface EskdElement {
  order: number;
  name: string;
  value?: string;
  zone?: string;
  gost_reference?: string | null;
  ok?: boolean | null;
  note?: string;
}

export interface EskdItemReport {
  index: number;
  total: number;
  source: string;
  filename?: string | null;
  page: number;
  status: string;
  summary: string;
  errors_count: number;
  warnings_count: number;
  errors: EskdRemark[];
  warnings: EskdRemark[];
  positions: EskdPosition[];
  elements?: EskdElement[];
  positions_order_ok?: boolean;
  report_text?: string;
  infer_seconds?: number;
  error?: string | null;
}

export interface EskdPackageError {
  kind: string;
  code: string;
  message: string;
  severity: string;
  gost_reference?: string | null;
  element?: string | null;
  pages?: number[];
  text: string;
}

export interface EskdDocumentIndexSummary {
  source: string;
  designation?: string | null;
  pages_count: number;
  sheets_total?: number | null;
  positions_in_spec: string[];
  positions_on_drawing: string[];
}

export interface EskdCheckResponse {
  job_id: string;
  designation?: string | null;
  model: string;
  adapter: string;
  total_items: number;
  processed: number;
  failed: number;
  total_errors: number;
  total_warnings: number;
  total_infer_seconds: number;
  load_seconds: number;
  progress_percent: number;
  status: string;
  global_warnings: string[];
  items: EskdItemReport[];
  report_text: string;
  extracted_texts?: Array<{ source: string; name: string; format: string; chars: number; text: string }>;
  preprocess_warnings?: string[];
  summary?: string;
  pipeline_mode?: string;
  document_index?: EskdDocumentIndexSummary | null;
  package_errors?: EskdPackageError[];
  evaluator?: string | null;
  gost_summary?: Record<string, unknown> | null;
}

export interface HealthResponse {
  status: string;
  gateway: string;
  model: {
    reachable?: boolean;
    model_loaded?: boolean;
    model_path?: string;
    adapter_path?: string;
    load_seconds?: number;
    error?: string;
  };
}

export type PageMode = "all" | "single" | "range" | "list";

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}
