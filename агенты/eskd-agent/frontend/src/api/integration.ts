import { apiGet } from "./client";

export interface ExchangeLogItem {
  id: string;
  occurred_at: string;
  sender: string;
  receiver: string;
  request_id?: string | null;
  operation: string;
  result: string;
  error_message?: string | null;
  designation?: string | null;
  revision?: string | null;
  actor?: string | null;
}

export interface ExchangeLogListResponse {
  items: ExchangeLogItem[];
  total: number;
}

export interface AuthMe {
  subject: string;
  roles: string[];
  auth_type: string;
}

export async function fetchAuthMe(): Promise<AuthMe> {
  return apiGet<AuthMe>("/api/v1/auth/me");
}

export async function fetchExchangeLog(params?: {
  page?: number;
  size?: number;
  request_id?: string;
  source_system?: string;
}): Promise<ExchangeLogListResponse> {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.size) q.set("size", String(params.size));
  if (params?.request_id) q.set("request_id", params.request_id);
  if (params?.source_system) q.set("source_system", params.source_system);
  const suffix = q.toString() ? `?${q}` : "";
  return apiGet<ExchangeLogListResponse>(`/api/v1/integration/exchange-log${suffix}`);
}
