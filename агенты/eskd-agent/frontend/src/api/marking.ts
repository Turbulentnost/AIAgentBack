import { api, eskdApiBase } from "@/api/eskd";
import type {
  GostStatsResponse,
  MarkingDocument,
  MarkingDocumentListItem,
  MarkingLabel,
  MarkingLabelCreate,
  MarkingLabelSuggested,
  MarkingLabelUpdate
} from "@/types/marking";

export async function openMarkingFromCheckRun(checkRunId: string, file?: File): Promise<MarkingDocument> {
  if (file) {
    const fd = new FormData();
    fd.append("file", file);
    const { data } = await api.post<MarkingDocument>(
      `/api/v1/eskd/marking/documents/open-from-check-run/${checkRunId}`,
      fd
    );
    return data;
  }
  const { data } = await api.post<MarkingDocument>(
    `/api/v1/eskd/marking/documents/open-from-check-run/${checkRunId}`
  );
  return data;
}

export async function lookupMarkingDocumentByFilename(filename: string): Promise<MarkingDocumentLookup> {
  const { data } = await api.get<MarkingDocumentLookup>("/api/v1/eskd/marking/documents/lookup", {
    params: { filename }
  });
  return data;
}

export async function uploadMarkingDocument(
  file: File,
  designation?: string,
  opts?: { forceNew?: boolean }
): Promise<MarkingDocument> {
  const fd = new FormData();
  fd.append("file", file);
  if (designation?.trim()) fd.append("designation", designation.trim());
  fd.append("reuse_existing", opts?.forceNew ? "false" : "true");
  const { data } = await api.post<MarkingDocument>("/api/v1/eskd/marking/documents", fd);
  return data;
}

export async function fetchMarkingDocuments(): Promise<MarkingDocumentListItem[]> {
  const { data } = await api.get<{ items: MarkingDocumentListItem[] }>("/api/v1/eskd/marking/documents");
  return data.items;
}

export async function fetchMarkingDocument(id: string): Promise<MarkingDocument> {
  const { data } = await api.get<MarkingDocument>(`/api/v1/eskd/marking/documents/${id}`);
  return data;
}

export async function fetchLatestMarkingLabel(docId: string): Promise<MarkingLabel | null> {
  try {
    const { data } = await api.get<MarkingLabel>(`/api/v1/eskd/marking/documents/${docId}/label/latest`);
    return data;
  } catch {
    return null;
  }
}

export async function fetchSuggestedMarkingLabel(docId: string): Promise<MarkingLabelSuggested> {
  const { data } = await api.get<MarkingLabelSuggested>(
    `/api/v1/eskd/marking/documents/${docId}/label/suggested`
  );
  return data;
}

export function markingPreviewUrl(relativeUrl: string): string {
  if (/^https?:\/\//i.test(relativeUrl)) return relativeUrl;
  if (relativeUrl.startsWith("/eskd-api")) return relativeUrl;
  const base = eskdApiBase.replace(/\/$/, "");
  return `${base}${relativeUrl}`;
}

export async function createMarkingLabel(payload: MarkingLabelCreate): Promise<MarkingLabel> {
  const { data } = await api.post<MarkingLabel>("/api/v1/eskd/marking/labels", payload);
  return data;
}

export async function updateMarkingLabel(id: string, payload: MarkingLabelUpdate): Promise<MarkingLabel> {
  const { data } = await api.put<MarkingLabel>(`/api/v1/eskd/marking/labels/${id}`, payload);
  return data;
}

export async function fetchMarkingStats(): Promise<GostStatsResponse> {
  const { data } = await api.get<GostStatsResponse>("/api/v1/eskd/marking/stats");
  return data;
}
