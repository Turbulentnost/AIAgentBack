export interface GostSummary {
  passed: string[];
  warnings: Record<string, number[]>;
  errors: Record<string, number[]>;
}

export interface CheckRunListItem {
  id: string;
  job_id: string;
  created_at: string;
  original_filename: string | null;
  designation: string | null;
  status: string;
  total_errors: number;
  total_warnings: number;
  pages_count: number | null;
  version_no?: number;
  created_by_login?: string | null;
  created_by_name?: string | null;
  verified_by_login?: string | null;
  verified_by_name?: string | null;
  human_verified_at?: string | null;
  gost_summary?: GostSummary | null;
  progress_percent?: number | null;
  processed_pages?: number | null;
}

export interface CheckRunDetail extends CheckRunListItem {
  content_type: string | null;
  file_size_bytes: number | null;
  file_sha256: string | null;
  check_params: Record<string, unknown> | null;
  model: string | null;
  adapter: string | null;
  gost_summary: GostSummary | null;
  raw_result: Record<string, unknown> | null;
  document_key?: string | null;
  parent_run_id?: string | null;
}

export interface CheckRunChange {
  id: string;
  run_id: string;
  parent_run_id?: string | null;
  version_no: number;
  change_type: string;
  summary: string;
  changed_by_login?: string | null;
  changed_by_name?: string | null;
  created_at: string;
  diff?: Record<string, unknown> | null;
}

export interface CheckRunVersion {
  id: string;
  version_no: number;
  created_at: string;
  created_by_login?: string | null;
  created_by_name?: string | null;
  total_errors: number;
  total_warnings: number;
  status: string;
  human_verified_at?: string | null;
  verified_by_name?: string | null;
}

export interface CheckRunListResponse {
  items: CheckRunListItem[];
  total: number;
  page: number;
  size: number;
}

export interface GostCatalogItem {
  key: string;
  title: string;
}
