import { api } from "@/api/eskd";
import type { KnowledgeBaseFilter, KnowledgeBaseItem, KnowledgeBaseListResponse } from "@/types/knowledgeBase";

export async function fetchKnowledgeBase(params?: {
  q?: string;
  filter?: KnowledgeBaseFilter;
  page?: number;
  size?: number;
}): Promise<KnowledgeBaseListResponse> {
  const checked =
    params?.filter === "checked" ? true : params?.filter === "unchecked" ? false : undefined;
  const { data } = await api.get<KnowledgeBaseListResponse>("/api/v1/eskd/knowledge-base", {
    params: {
      q: params?.q?.trim() || undefined,
      checked,
      page: params?.page ?? 1,
      size: params?.size ?? 24
    }
  });
  return data;
}

export async function verifyKnowledgeBaseEntry(params: {
  checkRunId?: string | null;
  markingDocumentId?: string | null;
}): Promise<KnowledgeBaseItem> {
  const { data } = await api.post<{ item: KnowledgeBaseItem }>("/api/v1/eskd/knowledge-base/verify", {
    check_run_id: params.checkRunId ?? undefined,
    marking_document_id: params.markingDocumentId ?? undefined
  });
  return data.item;
}

export async function deleteKnowledgeBaseEntry(key: string): Promise<void> {
  await api.delete(`/api/v1/eskd/knowledge-base/${encodeURIComponent(key)}`);
}
