export interface LiveCheck {
  element?: string;
  zone?: string;
  status?: string;
  value?: string;
  gost_reference?: string;
  message?: string;
}

export interface LiveEskdPayload {
  summary?: string;
  checks?: LiveCheck[];
  positions?: Array<{
    pos?: string | number;
    designation?: string;
    title?: string;
    quantity?: string | number;
    zone?: string;
    order_index?: number;
    order_ok?: boolean;
    readable?: boolean;
  }>;
  positions_order_ok?: boolean;
  errors?: Array<{ code?: string; message?: string; kind?: string }>;
  warnings?: Array<{ code?: string; message?: string; kind?: string }>;
  overlays?: unknown[];
}

/** Извлекает JSON из потока токенов model (```json … ``` или сырой объект). */
export function parseLiveJson(text: string): LiveEskdPayload | null {
  const raw = text.trim();
  if (!raw) return null;

  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  let candidate = (fenced?.[1] ?? raw).trim();

  const start = candidate.indexOf("{");
  if (start < 0) return null;
  candidate = candidate.slice(start);

  for (let end = candidate.length; end > 2; end--) {
    const chunk = candidate.slice(0, end).trimEnd();
    if (!chunk.endsWith("}")) continue;
    try {
      const parsed = JSON.parse(chunk) as LiveEskdPayload;
      if (parsed && typeof parsed === "object") return parsed;
    } catch {
      /* partial JSON while streaming */
    }
  }
  return null;
}
