export interface KnowledgeBaseItem {

  key: string;

  display_name: string;

  designation: string | null;

  checked: boolean;

  check_count: number;

  last_checked_at: string | null;

  last_check_run_id: string | null;

  total_errors: number | null;

  total_warnings: number | null;

  has_ai_check: boolean;

  has_marking: boolean;

  marking_document_id: string | null;

  marked_pages_count: number;

  marking_errors_count: number;

  marking_warnings_count: number;

  marking_updated_at: string | null;

  human_verified_at: string | null;

  pages_count: number | null;

  verifiers: string[];

  verifiers_count: number;

}



export interface KnowledgeBaseListResponse {

  items: KnowledgeBaseItem[];

  total: number;

  page: number;

  size: number;

  checked_count: number;

  unchecked_count: number;

}



export type KnowledgeBaseFilter = "all" | "checked" | "unchecked";


