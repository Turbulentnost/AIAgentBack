import { api } from "@/api/eskd";
import type {
  CheckRunChange,
  CheckRunDetail,
  CheckRunListResponse,
  CheckRunVersion,
  GostCatalogItem
} from "@/types/history";

export async function fetchCheckHistory(params?: {
  page?: number;
  size?: number;
  filename?: string;
  designation?: string;
}): Promise<CheckRunListResponse> {
  const { data } = await api.get<CheckRunListResponse>("/api/v1/eskd/history", { params });
  return data;
}

export async function fetchCheckRunDetail(id: string): Promise<CheckRunDetail> {
  const { data } = await api.get<CheckRunDetail>(`/api/v1/eskd/history/${id}`);
  return data;
}

export async function fetchCheckRunVersions(id: string): Promise<CheckRunVersion[]> {
  const { data } = await api.get<CheckRunVersion[]>(`/api/v1/eskd/history/${id}/versions`);
  return data;
}

export async function fetchCheckRunChanges(id: string): Promise<CheckRunChange[]> {
  const { data } = await api.get<CheckRunChange[]>(`/api/v1/eskd/history/${id}/changes`);
  return data;
}

export async function fetchGostCatalog(): Promise<GostCatalogItem[]> {
  const { data } = await api.get<{ items: GostCatalogItem[] }>("/api/v1/eskd/gost-catalog");
  return data.items;
}
