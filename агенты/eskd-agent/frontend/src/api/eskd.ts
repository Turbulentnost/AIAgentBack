import axios from "axios";
import { getDevRoles, getDevUser } from "@/api/client";
import type { EskdCheckResponse, HealthResponse } from "@/types/eskd";

export const eskdApiBase = import.meta.env.VITE_ESKD_API_URL ?? "/eskd-api";
const baseURL = eskdApiBase;

export const api = axios.create({
  baseURL,
  timeout: 600_000
});

api.interceptors.request.use((config) => {
  config.headers["X-Dev-User"] = getDevUser();
  config.headers["X-Dev-Roles"] = getDevRoles();
  return config;
});

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>("/health");
  return data;
}

export interface CheckCacheLookup {
  found: boolean;
  from_marking: boolean;
  from_check_run: boolean;
  checked_in_kb: boolean;
  display_name: string | null;
  marked_pages_count: number;
  has_ai_check: boolean;
  message: string | null;
}

export async function lookupCheckCache(filename: string): Promise<CheckCacheLookup> {
  const { data } = await api.get<CheckCacheLookup>("/api/v1/eskd/check/lookup", {
    params: { filename }
  });
  return data;
}

export async function fetchPdfInfo(file: File): Promise<{ filename: string; pages: number }> {
  const fd = new FormData();
  fd.append("file", file);
  const { data } = await api.post("/api/v1/eskd/pdf/info", fd);
  return data;
}

export async function runEskdCheckSync(
  files: File[],
  opts: {
    designation?: string;
    allPages: boolean;
    page?: number;
    pageFrom?: number;
    pageTo?: number;
    pages?: string;
  }
): Promise<EskdCheckResponse> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("all_pages", opts.allPages ? "true" : "false");
  if (opts.designation?.trim()) fd.append("designation", opts.designation.trim());
  if (opts.page) fd.append("page", String(opts.page));
  if (opts.pageFrom) fd.append("page_from", String(opts.pageFrom));
  if (opts.pageTo) fd.append("page_to", String(opts.pageTo));
  if (opts.pages) fd.append("pages", opts.pages);
  const { data } = await api.post<EskdCheckResponse>("/api/v1/eskd/check", fd);
  return data;
}

export async function cancelEskdJob(jobId: string): Promise<void> {
  const fd = new FormData();
  fd.append("job_id", jobId);
  await api.post("/api/v1/eskd/check/cancel", fd);
}

export async function* streamEskdCheck(
  files: File[],
  opts: {
    designation?: string;
    allPages: boolean;
    page?: number;
    pageFrom?: number;
    pageTo?: number;
    pages?: string;
  },
  signal?: AbortSignal
): AsyncGenerator<{ event: string; data: unknown }> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("all_pages", opts.allPages ? "true" : "false");
  if (opts.designation?.trim()) fd.append("designation", opts.designation.trim());
  if (opts.page) fd.append("page", String(opts.page));
  if (opts.pageFrom) fd.append("page_from", String(opts.pageFrom));
  if (opts.pageTo) fd.append("page_to", String(opts.pageTo));
  if (opts.pages) fd.append("pages", opts.pages);

  const resp = await fetch(`${baseURL}/api/v1/eskd/check/stream`, {
    method: "POST",
    body: fd,
    signal,
    headers: {
      "X-Dev-User": getDevUser(),
      "X-Dev-Roles": getDevRoles()
    }
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `HTTP ${resp.status}`);
  }
  if (!resp.body) throw new Error("Пустой stream");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      if (!part.trim()) continue;
      let event = "message";
      let dataStr = "";
      for (const line of part.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataStr += line.slice(5).trim();
      }
      if (!dataStr) continue;
      try {
        const payload = JSON.parse(dataStr) as { type?: string };
        const eventName =
          event === "message" && payload?.type ? payload.type : event;
        yield { event: eventName, data: payload };
      } catch {
        yield { event, data: dataStr };
      }
    }
  }
}
