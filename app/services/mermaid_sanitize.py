from __future__ import annotations

import hashlib
import re

_ARROW_FIX_RE = re.compile(r"(?<![->])-{3,}(?!>)")
_EDGE_LABEL_RE = re.compile(r"(\s*-->\s+)([^|>\-\n][^>\n]*?)(\s+-->)")
_NODE_HOP_EDGE_RE = re.compile(r"-->\|([A-Za-z_][\w]*)\|\s*-->")
_TRAILING_NODE_HOP_RE = re.compile(r"-->\|([A-Za-z_][\w]*)\|\s*$")
_SUBGRAPH_UNQUOTED_RE = re.compile(
    r"^\s*subgraph\s+([A-Za-z_][\w]*)\s*\[(?!\")([^\]\n]+)\]",
    re.MULTILINE,
)
_SUBGRAPH_SPACED_ID_RE = re.compile(
    r"^(\s*subgraph\s+)([^\[\n\"]+?)(\s*\[)",
    re.MULTILINE,
)
_NODE_BRACKET_RE = re.compile(
    r"(\b[A-Za-z_][\w]*)\s*\[(?!\"|/)([^\]\n]+)\]"
)
_NODE_DOUBLE_BRACKET_RE = re.compile(
    r"(\b[A-Za-z_][\w]*)\s*\[\[(?!\"|/)([^\]\n]+)\]\]"
)
_DECISION_NODE_RE = re.compile(
    r"(\b[A-Za-z_][\w]*)\{(?!\"|/)([^}\n]+)\}"
)
_ROUND_NODE_RE = re.compile(
    r"(\b[A-Za-z_][\w]*)\(\[(?!\")([^\]\n]+)\]\)"
)
_NODE_PARENS_RE = re.compile(
    r"(\b[A-Za-z_][\w]*)\s*\((\[[^\]]+\])\)"
)
_SUBGRAPH_DECL_RE = re.compile(
    r"^(\s*subgraph\s+)([^\[\n\"]+?)(\s*\[)",
    re.MULTILINE,
)
_SUBGRAPH_END_PLACEHOLDER = "__MERMAID_SUBGRAPH_END__"
_FLOWCHART_HEADER_RE = re.compile(r"^\s*(graph|flowchart)\s+(TD|LR|BT|RL)\b", re.IGNORECASE | re.MULTILINE)


def _escape_label(text: str) -> str:
    return text.replace('"', "'").strip()


def _label_needs_quotes(label: str) -> bool:
    text = label.strip()
    if not text or text.startswith('"'):
        return False
    if any(ch in text for ch in ":;&?/\\"):
        return True
    if "(" in text or ")" in text:
        return True
    if re.search(r"[а-яА-ЯёЁ]", text):
        return True
    if " " in text:
        return True
    return False


def _normalize_subgraph_id(raw: str) -> str:
    text = raw.strip()
    ascii_slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    if ascii_slug and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ascii_slug):
        if not re.search(r"[^\x00-\x7F]", text):
            return ascii_slug[:48]
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    prefix = ascii_slug[:12].rstrip("_") if ascii_slug else "sg"
    if not re.match(r"[A-Za-z_]", prefix):
        prefix = "sg"
    return f"{prefix}_{digest}"


def _fix_node_hop_edge_labels(line: str) -> str:
    fixed = line
    while True:
        updated = _NODE_HOP_EDGE_RE.sub(r" --> \1 -->", fixed)
        if updated == fixed:
            break
        fixed = updated
    return _TRAILING_NODE_HOP_RE.sub(r" --> \1", fixed)


def _ensure_flowchart_header(text: str) -> str:
    if _FLOWCHART_HEADER_RE.search(text):
        return re.sub(r"^\s*graph\s+", "flowchart ", text, count=1, flags=re.IGNORECASE | re.MULTILINE)
    return f"flowchart TD\n{text}"


def _sanitize_line(line: str, *, subgraph_counter: list[int]) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("%%"):
        return line
    if stripped.lower() in {"end", "subgraph"}:
        return line

    fixed = _fix_node_hop_edge_labels(line)
    fixed = _ARROW_FIX_RE.sub("-->", fixed)
    fixed = re.sub(r"(?<![->])--(?![->])", "-->", fixed)
    fixed = _EDGE_LABEL_RE.sub(
        lambda m: (
            m.group(0)
            if re.fullmatch(r"[A-Za-z_][\w]*", m.group(2).strip())
            else f' -->|{_escape_label(m.group(2).strip())}|--> '
        ),
        fixed,
    )
    fixed = _fix_node_hop_edge_labels(fixed)

    def _quote_rect(match: re.Match[str]) -> str:
        node_id, label = match.group(1), match.group(2).strip()
        if _label_needs_quotes(label):
            return f'{node_id}["{_escape_label(label)}"]'
        return match.group(0)

    def _quote_double_rect(match: re.Match[str]) -> str:
        node_id, label = match.group(1), match.group(2).strip()
        if _label_needs_quotes(label):
            return f'{node_id}[["{_escape_label(label)}"]]'
        return match.group(0)

    def _quote_decision(match: re.Match[str]) -> str:
        node_id, label = match.group(1), match.group(2).strip()
        if _label_needs_quotes(label):
            return f'{node_id}{{"{_escape_label(label)}"}}'
        return match.group(0)

    def _quote_round(match: re.Match[str]) -> str:
        node_id, label = match.group(1), match.group(2).strip()
        escaped = _escape_label(label)
        if '"' in escaped or "]" in escaped or "(" in escaped or ")" in escaped:
            return f'{node_id}["{escaped}"]'
        return f"{node_id}([{escaped}])"

    fixed = _NODE_BRACKET_RE.sub(_quote_rect, fixed)
    fixed = _NODE_DOUBLE_BRACKET_RE.sub(_quote_double_rect, fixed)
    fixed = _DECISION_NODE_RE.sub(_quote_decision, fixed)
    fixed = _ROUND_NODE_RE.sub(_quote_round, fixed)
    fixed = _NODE_PARENS_RE.sub(lambda m: f"{m.group(1)}({m.group(2)})", fixed)
    fixed = _SUBGRAPH_UNQUOTED_RE.sub(
        lambda m: f'subgraph {m.group(1)}["{_escape_label(m.group(2))}"]',
        fixed,
    )
    fixed = _SUBGRAPH_SPACED_ID_RE.sub(
        lambda m: f'{m.group(1)}{_normalize_subgraph_id(m.group(2))}{m.group(3)}',
        fixed,
    )

    fixed = _SUBGRAPH_DECL_RE.sub(
        lambda m: (
            m.group(0)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", m.group(2).strip())
            and not re.search(r"[^\x00-\x7F]", m.group(2))
            else f"{m.group(1)}{_normalize_subgraph_id(m.group(2).strip())}{m.group(3)}"
        ),
        fixed,
    )

    if re.match(r"^\s*subgraph\s+", fixed) and "[" not in fixed:
        subgraph_counter[0] += 1
        title = re.sub(r"^\s*subgraph\s+", "", fixed).strip()
        fixed = f'  subgraph sg_{subgraph_counter[0]}["{_escape_label(title)}"]'

    return fixed


def _fix_reserved_node_ids(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if line.strip() == "end":
            lines.append(_SUBGRAPH_END_PLACEHOLDER)
            continue
        fixed = re.sub(r"\bstart\s*\(\[", "start_node([", line)
        fixed = re.sub(r"\bend\s*\(\[", "end_node([", fixed)
        lines.append(fixed)

    merged = "\n".join(lines)
    merged = re.sub(r"\bstart\b", "start_node", merged)
    merged = re.sub(r"\bend\b", "end_node", merged)
    return merged.replace(_SUBGRAPH_END_PLACEHOLDER, "end")


def sanitize_mermaid_code(code: str) -> str:
    """Исправить типичные синтаксические ошибки LLM в Mermaid flowchart."""
    return repair_mermaid_code(code, aggressive=False)


def repair_mermaid_code(code: str, *, aggressive: bool = False) -> str:
    text = (code or "").strip()
    if not text:
        return text

    text = re.sub(r"```(?:mermaid)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.replace("```", "").strip()
    text = _ensure_flowchart_header(text)

    subgraph_counter = [0]
    lines = [_sanitize_line(line, subgraph_counter=subgraph_counter) for line in text.splitlines()]

    if aggressive:
        cleaned: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                cleaned.append(line)
                continue
            if stripped.startswith("style ") or stripped.startswith("classDef ") or stripped.startswith("linkStyle "):
                continue
            cleaned.append(line)
        lines = cleaned

    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = _fix_reserved_node_ids(result.strip())
    return result
