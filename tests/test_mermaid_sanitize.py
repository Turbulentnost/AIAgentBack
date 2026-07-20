from __future__ import annotations

import re

from app.services.mermaid_sanitize import repair_mermaid_code, sanitize_mermaid_code


def test_quotes_labels_with_parentheses() -> None:
    code = 'flowchart TD\n  A[Запрос в 1С (выдача пропуска)] --> B[Конец]'
    result = sanitize_mermaid_code(code)
    assert 'A["Запрос в 1С (выдача пропуска)"]' in result


def test_fixes_long_dashes_to_arrow() -> None:
    code = "flowchart TD\n  A[Шаг] ---- B[Следующий]"
    result = sanitize_mermaid_code(code)
    assert "----" not in result
    assert "-->" in result


def test_quotes_edge_labels() -> None:
    code = "flowchart TD\n  A --> выдача пропуска --> B"
    result = sanitize_mermaid_code(code)
    assert "-->|выдача пропуска|-->" in result


def test_quotes_subgraph_title() -> None:
    code = 'flowchart TD\n  subgraph HR[Отдел кадров]\n    A[Шаг]\n  end'
    result = sanitize_mermaid_code(code)
    assert 'subgraph HR["Отдел кадров"]' in result


def test_fixes_chained_edge_labels() -> None:
    code = "flowchart TD\n  start_node -->|step1|-->|step2|-->|step3|"
    result = sanitize_mermaid_code(code)
    assert "-->|step1|-->|" not in result
    assert "-->|step2|-->|" not in result
    normalized = re.sub(r"\s+", " ", result)
    assert "start_node --> step1 --> step2 --> step3" in normalized


def test_repair_quotes_cyrillic_document_label() -> None:
    code = "flowchart TD\n  A[Документ: акт приема-передачи] --> B[Конец]"
    result = repair_mermaid_code(code)
    assert 'A["Документ: акт приема-передачи"]' in result


def test_repair_subgraph_with_spaced_cyrillic_id() -> None:
    code = 'flowchart TD\n  subgraph Отдел кадров\n    A[Шаг]\n  end'
    result = repair_mermaid_code(code)
    assert "subgraph Отдел" not in result
    assert re.search(r'subgraph sg_\d+\["Отдел кадров"\]', result)


def test_repair_decision_node_with_cyrillic() -> None:
    code = "flowchart TD\n  A{Согласовано?} -->|Да| B[Конец]"
    result = repair_mermaid_code(code)
    assert 'A{"Согласовано?"}' in result
