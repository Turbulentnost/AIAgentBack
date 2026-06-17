from __future__ import annotations

ND_DOCUMENT_EXTRACTION_SYSTEM_PROMPT = """
Ты — эксперт по анализу нормативной документации организации.

Твоя задача — извлечь структурированные данные из фрагмента или полного текста нормативного документа
и вернуть результат строго в формате JSON по схеме DocumentExtractionResult.

Правила:
1. Возвращай только JSON без пояснений, markdown и комментариев.
2. Не выдумывай данные. Если поле не найдено в тексте — используй null или пустой массив.
3. Не считай разработчика документа владельцем процесса.
4. Не считай согласующего владельцем процесса без явного основания в тексте.
5. Владельца процесса извлекай только при явных формулировках:
   «ответственный за процесс», «ответственный за выполнение», «контроль возлагается»,
   «выполняет», «осуществляет», «обеспечивает».
6. Если владелец процесса не найден явно — добавь owner_candidates с reason и confidence.
7. Все спорные, неоднозначные или неполные данные добавляй в unknowns.
8. Обязательно указывай evidence (page, section, quote) там, где это возможно.
9. Если анализируешь только фрагмент документа — извлекай только то, что явно присутствует во фрагменте.
10. Не дублируй одни и те же процессы, формы и обязанности, если они уже описаны.

Структура JSON:
{
  "document": {
    "document_code", "title", "document_type", "version", "status",
    "approval_date", "effective_date", "purpose",
    "scope": { "text", "departments", "positions", "applies_to_all_company" }
  },
  "participants": {
    "developed_by", "checked_by", "approved_by", "agreed_by"
  },
  "processes": [{
    "name", "description", "goal", "inputs", "outputs", "actions",
    "roles", "forms", "systems", "resources", "related_departments", "owner_candidates"
  }],
  "responsibilities": [{ "subject", "responsibility", "role_type", "confidence", "evidence" }],
  "forms": [{ "name", "code", "purpose", "related_process", "evidence" }],
  "related_departments", "related_documents", "related_systems",
  "unknowns": [{ "field", "reason", "description" }]
}

Допустимые значения:
- confidence: high, medium, low
- role_type: process_owner, performer, controller, approver, document_owner, unknown
- unknown reason: not_found, ambiguous, requires_human_confirmation
""".strip()


def build_full_text_extraction_user_prompt(
    *,
    document_code: str | None,
    file_name: str | None,
    document_text: str,
) -> str:
    header = "Проанализируй полный текст нормативного документа и верни JSON DocumentExtractionResult."
    meta = f"Код документа: {document_code or 'не указан'}\nИмя файла: {file_name or 'не указано'}"
    return f"{header}\n\n{meta}\n\nТекст документа:\n{document_text}"


def build_chunk_extraction_user_prompt(
    *,
    document_code: str | None,
    file_name: str | None,
    chunk_index: int,
    total_chunks: int,
    page_number: int | None,
    section: str | None,
    chunk_text: str,
) -> str:
    location = []
    if page_number is not None:
        location.append(f"страница {page_number}")
    if section:
        location.append(f"раздел {section}")
    location_text = ", ".join(location) if location else "местоположение не указано"

    header = (
        f"Проанализируй фрагмент {chunk_index + 1} из {total_chunks} нормативного документа "
        f"и верни JSON DocumentExtractionResult только по данным этого фрагмента."
    )
    meta = (
        f"Код документа: {document_code or 'не указан'}\n"
        f"Имя файла: {file_name or 'не указано'}\n"
        f"Фрагмент: {location_text}"
    )
    return f"{header}\n\n{meta}\n\nТекст фрагмента:\n{chunk_text}"
