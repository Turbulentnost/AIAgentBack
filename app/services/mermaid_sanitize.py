from __future__ import annotations

import re

_ARROW_FIX_RE = re.compile(r"(?<![->])-{3,}(?!>)")
_EDGE_LABEL_RE = re.compile(r"(\s*-->\s+)([^|>\-\n][^>\n]*?)(\s+-->)")
_SUBGRAPH_UNQUOTED_RE = re.compile(
    r"^\s*subgraph\s+([A-Za-z_][\w]*)\s*\[(?!\")([^\]\n]+)\]",
    re.MULTILINE,
)
_NODE_BRACKET_RE = re.compile(
    r"(\b[A-Za-z_][\w]*)\s*\[(?!\"|/)([^\]\n]+)\]"
)
_NODE_PARENS_RE = re.compile(
    r"(\b[A-Za-z_][\w]*)\s*\((\[[^\]]+\])\)"
)
_SUBGRAPH_TITLE_RE = re.compile(
    r"^\s*subgraph\s+([^\"\[\n]+)$",
    re.MULTILINE,
)


def _escape_label(text: str) -> str:
    return text.replace('"', "'").strip()


def sanitize_mermaid_code(code: str) -> str:
    """Исправить типичные синтаксические ошибки LLM в Mermaid flowchart."""
    text = (code or "").strip()
    if not text:
        return text

    lines: list[str] = []
    subgraph_counter = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            lines.append(line)
            continue

        fixed = line
        fixed = _ARROW_FIX_RE.sub("-->", fixed)
        fixed = _EDGE_LABEL_RE.sub(
            lambda m: f' -->|{_escape_label(m.group(2).strip())}|--> ',
            fixed,
        )

        def _quote_node_label(match: re.Match[str]) -> str:
            node_id, label = match.group(1), match.group(2).strip()
            if '"' in label:
                return match.group(0)
            if any(ch in label for ch in '()/:&'):
                return f'{node_id}["{_escape_label(label)}"]'
            return match.group(0)

        fixed = _NODE_BRACKET_RE.sub(_quote_node_label, fixed)
        fixed = _NODE_PARENS_RE.sub(lambda m: f'{m.group(1)}({m.group(2)})', fixed)
        fixed = _SUBGRAPH_UNQUOTED_RE.sub(
            lambda m: f'subgraph {m.group(1)}["{_escape_label(m.group(2))}"]',
            fixed,
        )

        if re.match(r"^\s*subgraph\s+", fixed) and "[" not in fixed:
            subgraph_counter += 1
            title = re.sub(r"^\s*subgraph\s+", "", fixed).strip()
            fixed = f'  subgraph sg_{subgraph_counter}["{_escape_label(title)}"]'

        lines.append(fixed)

    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result
