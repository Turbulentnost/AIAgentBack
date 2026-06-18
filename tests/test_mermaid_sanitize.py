from __future__ import annotations

from app.services.mermaid_sanitize import sanitize_mermaid_code


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
