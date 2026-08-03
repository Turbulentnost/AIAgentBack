const LLM_META_PHRASES = [
  "на основе предоставленного индекса",
  "отсутствуют позиции",
  "не могут быть выявлены",
  "отсутствия детализированных данных",
  "детализированных данных в индексе",
  "проверка комплекта кд выполнена",
  "для анализа перекрёстных ссылок",
  "в данных отсутствуют",
  "positions_in_spec",
  "positions_on_drawing",
  "document_index",
  "pipeline:",
  "extract → rules",
  "extract -> rules",
  "агрегированного json",
  "rules_only",
  "stage 2b",
  "stage 2",
  "без изображений",
  "содержимое листов (sheets)"
];

const LLM_STATUS_ONLY_PHRASES = [
  "не выявлено ошибок",
  "нарушений не выявлено",
  "нарушений не обнаружено",
  "замечаний нет",
  "ошибок не найдено",
  "нарушений не найдено"
];

const META_STATUS_RE =
  /^\s*(?:проверка комплекта|анализ выполнен|оценка выполнена|итог проверки)/i;

export function isLlmMetaText(text?: string | null): boolean {
  const normalized = String(text ?? "")
    .replace(/\s+/g, " ")
    .trim();
  if (!normalized) return false;

  const lower = normalized.toLowerCase();
  if (LLM_META_PHRASES.some((phrase) => lower.includes(phrase))) return true;
  if (META_STATUS_RE.test(normalized)) return true;
  if (/нарушени[яий].*не могут быть/i.test(lower)) return true;
  if (/не выявлен[ыо].*из-за/i.test(lower)) return true;
  if (
    normalized.length <= 120 &&
    LLM_STATUS_ONLY_PHRASES.some((phrase) => lower.includes(phrase)) &&
    !/гост/i.test(lower) &&
    !/позици[яи]\s+\d/i.test(lower)
  ) {
    return true;
  }
  return false;
}

/** Оставляет только текст с конкретными нарушениями ГОСТ. */
export function filterLlmMetaText(text?: string | null): string {
  const raw = String(text ?? "").trim();
  if (!raw) return "";

  const parts = raw.split(/\n\s*\n/);
  if (parts.length > 1) {
    return parts
      .map((part) => part.trim())
      .filter((part) => part && !isLlmMetaText(part))
      .join("\n\n")
      .trim();
  }

  if (isLlmMetaText(raw)) return "";
  return raw;
}
