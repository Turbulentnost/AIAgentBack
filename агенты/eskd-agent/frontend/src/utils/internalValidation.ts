/** Технические коды rule engine — скрываются в карточках (показываем только LLM-замечания). */
export const INTERNAL_VALIDATION_CODES = new Set([
  "sheet_sequence",
  "designation_mismatch_across_pages"
]);

export function isInternalValidationCode(code?: string | null): boolean {
  return Boolean(code && INTERNAL_VALIDATION_CODES.has(code));
}

export function filterUserFacingRemarks<T extends { code?: string }>(items: T[] | undefined): T[] {
  return (items ?? []).filter((item) => !isInternalValidationCode(item.code));
}
