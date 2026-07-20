from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.enums import NdStructuralDocumentType
from app.schemas.diagram_block import DiagramBlockType
from app.schemas.nd_process_graph import ProcessGraphDTO
from app.services.process_uml_document_profile import has_operational_actions

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_FLOWCHART_RE = re.compile(r"^\s*flowchart\s+(TD|LR|BT|RL)\b", re.IGNORECASE | re.MULTILINE)
_MARKDOWN_FENCE_RE = re.compile(r"```")
_START_NODE_RE = re.compile(
    r"\(\[\s*\"?(?:Начало|Старт|Start|начало|старт|start)[^\"\]]*\"?\]\)"
    r"|\[\(\s*\"?(?:Начало|Старт|Start|начало|старт|start)[^\"\]]*\"?\]\)",
    re.IGNORECASE,
)
_END_NODE_RE = re.compile(
    r"\(\[\s*\"?(?:Конец|Окончание|End|конец|окончание|end|заверш)[^\"\]]*\"?\]\)"
    r"|\[\(\s*\"?(?:Конец|Окончание|End|конец|окончание|end|заверш)[^\"\]]*\"?\]\)",
    re.IGNORECASE,
)
_DECISION_NODE_RE = re.compile(r"\w+\{[^}]+\}")
_SUBGRAPH_RE = re.compile(r"^\s*subgraph\b", re.IGNORECASE | re.MULTILINE)
_ENUM_RE = re.compile(
    r"\b(PROCESS_[A-Z_]+|ROLE_[A-Z_]+|DOCUMENT_[A-Z_]+|NdRelationType\.[A-Z_]+)\b"
)
_YES_BRANCH_RE = re.compile(r"\|\s*(?:Да|Yes|да|yes)\s*\|", re.IGNORECASE)
_NO_BRANCH_RE = re.compile(r"\|\s*(?:Нет|No|нет|no)\s*\|", re.IGNORECASE)
_DOCUMENT_MARKER_RE = re.compile(r"Документ\s*:", re.IGNORECASE)
_OPERATION_NODE_RE = re.compile(r'\["[^"]+"\]')
_POLICY_STRUCTURE_RE = re.compile(
    r"(намерен|област|обязатель|ответствен|политик)",
    re.IGNORECASE,
)
_STO_SECTION_RE = re.compile(
    r"(критер|ресурс|риск|архив|контрол|результатив)",
    re.IGNORECASE,
)
_NODE_ID_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)")
_NODE_DEFINITION_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\[\[|\[|\(\[|\(\(|\(|\{)"
)
_NODE_LABEL_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[\[|\[|\(\[|\(\(|\(|\{)\s*\"?([^\"\]\)}]+)"
)
_EDGE_SPLIT_RE = re.compile(r"\s*(?:-->\|[^|]*\||-->|-\.\->|---)\s*")
_NON_NODE_LINE_RE = re.compile(
    r"^\s*(?:flowchart|graph|subgraph|end\b|classDef\b|class\b|style\b|linkStyle\b|%%)",
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    orphan_nodes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "invalid"
        if self.warnings:
            return "warning"
        return "valid"


def _resolve_document_type(process_graph: ProcessGraphDTO) -> NdStructuralDocumentType | None:
    raw = process_graph.primary_document_type or process_graph.source_document_type
    if not raw:
        return None
    try:
        return NdStructuralDocumentType(raw)
    except ValueError:
        return None


def _count_operation_nodes(code: str) -> int:
    return len(_OPERATION_NODE_RE.findall(code))


def _node_id(segment: str) -> str | None:
    match = _NODE_ID_RE.match(segment.strip())
    if not match:
        return None
    node_id = match.group(1)
    if node_id.lower() in {"flowchart", "graph", "subgraph", "end"}:
        return None
    return node_id


def _node_label(segment: str) -> tuple[str, str] | None:
    match = _NODE_LABEL_RE.match(segment.strip())
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _collect_mermaid_graph(code: str) -> tuple[set[str], set[tuple[str, str]], dict[str, str]]:
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    labels: dict[str, str] = {}

    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or _NON_NODE_LINE_RE.match(line):
            continue

        definition = _NODE_DEFINITION_RE.match(line)
        if definition:
            nodes.add(definition.group(1))
            label = _node_label(line)
            if label:
                labels[label[0]] = label[1]

        parts = _EDGE_SPLIT_RE.split(line)
        if len(parts) < 2:
            continue

        path: list[str] = []
        for part in parts:
            node_id = _node_id(part)
            if not node_id:
                continue
            path.append(node_id)
            nodes.add(node_id)
            label = _node_label(part)
            if label:
                labels[label[0]] = label[1]

        for source, target in zip(path, path[1:]):
            edges.add((source, target))

    return nodes, edges, labels


def _looks_like_start(label: str) -> bool:
    return bool(re.search(r"^(?:Начало|Старт|Start)\b", label.strip(), re.IGNORECASE))


def _looks_like_end(label: str) -> bool:
    return bool(re.search(r"^(?:Конец|Окончание|End|Заверш)", label.strip(), re.IGNORECASE))


def _format_orphan_node(node_id: str, labels: dict[str, str]) -> str:
    label = labels.get(node_id)
    return f"{node_id} ({label})" if label else node_id


def _validate_by_document_type(
    code: str,
    process_graph: ProcessGraphDTO,
    result: ValidationResult,
) -> None:
    document_type = _resolve_document_type(process_graph)
    if document_type is None:
        return

    has_actions = has_operational_actions(process_graph)
    decision_nodes = _DECISION_NODE_RE.findall(code)

    if document_type == NdStructuralDocumentType.POLICY:
        if not has_actions:
            if not _POLICY_STRUCTURE_RE.search(code):
                result.warnings.append(
                    "Для Политики без actions ожидается логическая структура: "
                    "намерение → область → обязательства → ответственность"
                )
            return
        return

    if document_type == NdStructuralDocumentType.REGULATION:
        if process_graph.roles and not _SUBGRAPH_RE.search(code):
            result.warnings.append("Для Положения ожидается схема ответственности с subgraph/swimlane")
        return

    if document_type == NdStructuralDocumentType.PROCESS_REGULATION:
        if has_actions and _count_operation_nodes(code) < 1:
            result.errors.append("Для Регламента должна быть последовательность операций")
            result.is_valid = False
        if process_graph.roles and not _SUBGRAPH_RE.search(code):
            result.errors.append("Для Регламента должны быть отражены роли участников")
            result.is_valid = False
        if process_graph.conditions and not decision_nodes:
            result.errors.append("Для Регламента при наличии условий нужны decision-узлы {}")
            result.is_valid = False
        return

    if document_type == NdStructuralDocumentType.STO:
        if process_graph.effectiveness_criteria and not _STO_SECTION_RE.search(code):
            result.warnings.append("Для СТО рекомендуется отразить критерии результативности")
        if process_graph.resources and "ресурс" not in code.lower():
            result.warnings.append("Для СТО рекомендуется отразить ресурсы процесса")
        if process_graph.risks and "риск" not in code.lower():
            result.warnings.append("Для СТО рекомендуется отразить риски и меры контроля")
        if process_graph.documentation_and_archive and "архив" not in code.lower():
            result.warnings.append("Для СТО рекомендуется отразить документирование и архивирование")
        return

    if document_type == NdStructuralDocumentType.INSTRUCTION:
        if len(process_graph.roles) > 3:
            result.warnings.append(
                "Для Инструкции схема должна быть операционной: слишком много организационных ролей"
            )
        if has_actions and _count_operation_nodes(code) < 1:
            result.warnings.append("Для Инструкции ожидается линейная операционная последовательность действий")


def validate_process_mermaid(mermaid_code: str, process_graph: ProcessGraphDTO) -> ValidationResult:
    """Пост-валидация Mermaid-кода блок-схемы процесса СМК."""
    result = ValidationResult()
    code = (mermaid_code or "").strip()
    if not code:
        result.errors.append("Пустой Mermaid-код")
        result.is_valid = False
        return result

    if _MARKDOWN_FENCE_RE.search(code):
        result.errors.append("Mermaid-код содержит markdown-обёртку ```")
        result.is_valid = False

    if not _FLOWCHART_RE.search(code):
        result.errors.append("Отсутствует flowchart TD или flowchart LR")
        result.is_valid = False

    nodes, edges, labels = _collect_mermaid_graph(code)
    connected_nodes = {node for edge in edges for node in edge}
    orphan_nodes = sorted(nodes - connected_nodes)
    if orphan_nodes:
        result.orphan_nodes = [_format_orphan_node(node, labels) for node in orphan_nodes]
        result.errors.append("В Mermaid есть несвязанные узлы: " + ", ".join(result.orphan_nodes))
        result.is_valid = False

    document_type = _resolve_document_type(process_graph)
    policy_without_actions = (
        document_type == NdStructuralDocumentType.POLICY and not has_operational_actions(process_graph)
    )

    has_start_in_graph = any(item.block_type == DiagramBlockType.START for item in process_graph.actions)
    has_end_in_graph = any(item.block_type == DiagramBlockType.END for item in process_graph.actions)

    if not policy_without_actions:
        if has_start_in_graph and not _START_NODE_RE.search(code):
            result.errors.append("В схеме отсутствует начальный узел (закруглённый «Начало»)")
            result.is_valid = False
        elif not _START_NODE_RE.search(code):
            result.warnings.append("Начальный узел не найден явно — проверьте схему")
        else:
            start_nodes = [
                node_id
                for node_id, label in labels.items()
                if _looks_like_start(label)
            ]
            if start_nodes and not any(source in start_nodes for source, _ in edges):
                result.errors.append("Начальный узел не имеет исходящей связи с первым действием")
                result.is_valid = False

        if has_end_in_graph and not _END_NODE_RE.search(code):
            result.errors.append("В схеме отсутствует конечный узел (закруглённый «Конец»)")
            result.is_valid = False
        elif not _END_NODE_RE.search(code):
            result.warnings.append("Конечный узел не найден явно — проверьте схему")
        else:
            end_nodes = [
                node_id
                for node_id, label in labels.items()
                if _looks_like_end(label)
            ]
            if end_nodes and not any(target in end_nodes for _, target in edges):
                result.errors.append("Конечный узел не имеет входящей связи из последнего действия")
                result.is_valid = False

    decision_nodes = _DECISION_NODE_RE.findall(code)
    if process_graph.conditions and not decision_nodes:
        result.errors.append("В process_graph есть условия, но в Mermaid нет decision-узлов {}")
        result.is_valid = False

    for node in decision_nodes:
        node_id = node.split("{", 1)[0]
        branches = re.findall(rf"{re.escape(node_id)}\s*-->", code)
        if len(branches) < 2:
            result.errors.append(f"Decision-узел {node_id} имеет менее двух исходящих веток")
            result.is_valid = False
        if not (_YES_BRANCH_RE.search(code) and _NO_BRANCH_RE.search(code)):
            result.warnings.append("Decision-ветки должны быть подписаны «Да» и «Нет»")

    regulation_type = document_type == NdStructuralDocumentType.PROCESS_REGULATION
    if process_graph.roles and not _SUBGRAPH_RE.search(code) and not policy_without_actions:
        if regulation_type or document_type != NdStructuralDocumentType.INSTRUCTION:
            result.errors.append("В process_graph есть роли, но в Mermaid нет subgraph/swimlane")
            result.is_valid = False

    document_markers = list(process_graph.documents) + list(process_graph.forms)
    document_actions = [
        item for item in process_graph.actions if item.block_type == DiagramBlockType.DOCUMENT_OUTPUT
    ]
    if (document_markers or document_actions or process_graph.outputs) and not (
        _DOCUMENT_MARKER_RE.search(code)
        or any(doc.lower() in code.lower() for doc in document_markers if doc)
        or any(out.lower() in code.lower() for out in process_graph.outputs if out)
    ):
        result.warnings.append("Документы/формы/выходы процесса не отражены явно в схеме")

    if _UUID_RE.search(code):
        result.errors.append("Mermaid-код содержит сырые UUID")
        result.is_valid = False

    if _ENUM_RE.search(code):
        result.errors.append("Mermaid-код содержит английские enum-значения")
        result.is_valid = False

    if re.search(r"-->\|[A-Za-z_][\w]*\|\s*-->\|", code):
        result.errors.append(
            "Невалидная цепочка подписей на стрелках (-->|id|-->|id|). "
            "Используй step1 --> step2 или A -->|подпись| B"
        )
        result.is_valid = False

    _validate_by_document_type(code, process_graph, result)

    return result


def build_process_uml_retry_prompt(
    validation_errors: list[str],
    *,
    orphan_nodes: list[str] | None = None,
) -> str:
    errors_text = "\n".join(f"- {item}" for item in validation_errors)
    orphan_text = ", ".join(orphan_nodes or [])
    orphan_instruction = (
        "\nВ диаграмме найдены несвязанные узлы: "
        f"{orphan_text}.\n"
        "Исправь Mermaid так, чтобы каждый узел был связан с основным процессом.\n"
        "Если узел является справочным, помести его в subgraph 'Справочная информация' "
        "и свяжи пунктирной линией с основным процессом.\n"
        if orphan_nodes
        else ""
    )
    return (
        "Предыдущая диаграмма не соответствует требованиям СТО-34-003 / ГОСТ 19.701-90.\n"
        f"Ошибки:\n{errors_text}\n\n"
        f"{orphan_instruction}"
        "Исправь Mermaid-код.\n"
        "Не меняй смысл процесса.\n"
        "Не добавляй новых действий.\n"
        "Не удаляй важные действия.\n"
        "Верни только валидный Mermaid code."
    )
