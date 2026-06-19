# ESKD/KD PDF fixtures

Тестовые PDF для проверки `EskdValidationEngine` и OCR-пайплайна извлечения текста.

Сгенерировать заново:

```bash
python scripts/generate_eskd_pdf_fixtures.py
```

## Файлы

| Файл | Назначение |
|------|------------|
| `ABVG.123456.001.pdf` | Корректный чертёж КД с основной надписью по ЕСКД |
| `ABVG.123456.001_scan.pdf` | Скан-копия корректного чертежа (только растровое изображение) |
| `WRONG_FILENAME.pdf` | Чертёж с нарушениями реквизитов и несогласованным именем файла |

## 1. `ABVG.123456.001.pdf` — корректный документ

**Содержимое:** чертёж детали с основной надписью (Обозначение, Наименование, Масштаб, Лист, Листов, Разработал, Проверил, Утвердил), обозначение `ABVG.123456.001`, масштаб `1:2`, текстовый слой PDF.

**Рекомендуемый контекст валидации:**

- `document_type`: `KD`
- `designation`: `ABVG.123456.001`
- `document_kind`: `drawing`
- `document_title`: `Корпус`
- `original_filename`: `ABVG.123456.001.pdf`
- `text_extract_status`: `extracted` (после `process_document`)

**Ожидаемый результат `EskdValidationEngine`:** `passed=true`, высокий `score` (≥ 0.8).

| Проверка | Ожидание |
|----------|----------|
| `document_type_kd` | pass |
| `designation_present` | pass |
| `designation_characters` | pass |
| `designation_gost201` | pass |
| `designation_filename` | pass |
| `designation_in_content` | pass |
| `main_inscription` | pass |
| `drawing_scale` | pass |

## 2. `ABVG.123456.001_scan.pdf` — скан (image-based PDF)

**Содержимое:** растровая копия `ABVG.123456.001.pdf` с лёгким поворотом и шумом; **без** выделяемого текстового слоя — имитирует отсканированный документ для OCR.

**Рекомендуемый контекст до OCR:**

- те же метаданные, что у корректного чертежа;
- `document_text`: `""`, `text_extract_status`: `not_started` или `pending`.

**Ожидаемый результат до OCR:** `passed=true` по метаданным (критических error нет), но `score` снижен (~0.76) из‑за предупреждений по тексту.

| Проверка | До OCR | После успешного OCR |
|----------|--------|---------------------|
| `text_extracted` | fail (warning) | pass |
| `designation_in_content` | fail (warning/error) — текст не извлечён | pass |
| `main_inscription` | fail (warning) | pass |
| `drawing_scale` | fail (warning) | pass |
| проверки обозначения по метаданным | pass | pass |

## 3. `WRONG_FILENAME.pdf` — документ с ошибками

**Содержимое:** в основной надписи нет графы «Обозначение» (вместо неё «Код документа»), нет «Масштаб», неформальный код `INVALID DOC` в теле чертежа. Имя файла не совпадает с обозначением ЕСКД.

**Рекомендуемый контекст валидации (типичный сценарий ошибки):**

- `designation`: `INVALID DOC`
- `document_title`: `Деталь без кода`
- `original_filename`: `WRONG_FILENAME.pdf`
- `document_kind`: `drawing`
- `text_extract_status`: `extracted`

**Ожидаемый результат:** `passed=false`, низкий `score`.

| Проверка | Ожидание |
|----------|----------|
| `designation_characters` | fail — пробел в обозначении |
| `designation_gost201` | fail — не формат ГОСТ 2.201 |
| `designation_filename` | fail (warning) — `INVALID DOC` ≠ `WRONG_FILENAME` |
| `main_inscription` | fail — нет ключевого слова «обозначение» |
| `designation_in_content` | fail — обозначение не найдено в извлечённом тексте |
| `drawing_scale` | fail (warning) — «Масштаб» отсутствует |

## Связь с тестами

Моки в `tests/eskd/mocks.py` (`build_valid_drawing_context`, `build_invalid_designation_context`) описывают тот же контекст, что и эти PDF после извлечения текста. PDF-фикстуры дополняют unit-тесты реальными файлами для интеграционных сценариев парсера и OCR.
