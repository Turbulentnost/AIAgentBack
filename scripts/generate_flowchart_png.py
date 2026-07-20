"""PNG блок-схема Агент-Почта — читаемые шрифты и подписи переходов.

Запуск: python scripts/generate_flowchart_png.py
Выход: docs/AGENT-FLOWCHART.png
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "AGENT-FLOWCHART.png"

# (face, edge, text)
STYLE = {
    "start": ("#d4edda", "#28a745", "#155724"),
    "agent": ("#cce5ff", "#007bff", "#004085"),
    "ud": ("#f8d7da", "#dc3545", "#721c24"),
    "decision": ("#fff3cd", "#ffc107", "#856404"),
    "infra": ("#e9ecef", "#6c757d", "#343a40"),
    "end_ok": ("#d4edda", "#28a745", "#155724"),
    "end_spam": ("#f5f5f5", "#999999", "#333333"),
    "end_err": ("#ffe0e0", "#dc3545", "#721c24"),
}

FONT = "DejaVu Sans"
TITLE_SIZE = 20
BOX_SIZE = 11
SMALL_SIZE = 10
EDGE_LABEL = 10
LEGEND_SIZE = 11


@dataclass
class Node:
    id: str
    x: float
    y: float
    w: float
    h: float
    text: str
    kind: str
    shape: str = "box"  # box | diamond | round


@dataclass
class Edge:
    src: str
    dst: str
    label: str = ""
    style: str = "-"
    rad: float = 0.0


def _node_map(nodes: list[Node]) -> dict[str, Node]:
    return {n.id: n for n in nodes}


def _box_center(n: Node) -> tuple[float, float]:
    return n.x, n.y


def _connect_point(n: Node, side: str) -> tuple[float, float]:
    x, y, w, h = n.x, n.y, n.w, n.h
    if side == "top":
        return x, y + h / 2
    if side == "bottom":
        return x, y - h / 2
    if side == "left":
        return x - w / 2, y
    return x + w / 2, y


def draw_node(ax, n: Node) -> None:
    face, edge, txt = STYLE[n.kind]
    if n.shape == "diamond":
        s = min(n.w, n.h) * 0.55
        pts = [(n.x, n.y + s), (n.x + s * 1.15, n.y), (n.x, n.y - s), (n.x - s * 1.15, n.y)]
        patch = Polygon(pts, closed=True, facecolor=face, edgecolor=edge, linewidth=1.8, zorder=2)
        ax.add_patch(patch)
    elif n.shape == "round":
        patch = FancyBboxPatch(
            (n.x - n.w / 2, n.y - n.h / 2),
            n.w,
            n.h,
            boxstyle="round,pad=0.02,rounding_size=0.015",
            facecolor=face,
            edgecolor=edge,
            linewidth=1.8,
            zorder=2,
        )
        ax.add_patch(patch)
    else:
        patch = FancyBboxPatch(
            (n.x - n.w / 2, n.y - n.h / 2),
            n.w,
            n.h,
            boxstyle="square,pad=0.02",
            facecolor=face,
            edgecolor=edge,
            linewidth=1.8,
            zorder=2,
        )
        ax.add_patch(patch)
    ax.text(
        n.x,
        n.y,
        n.text,
        ha="center",
        va="center",
        fontsize=BOX_SIZE,
        color=txt,
        fontfamily=FONT,
        linespacing=1.35,
        zorder=3,
        wrap=True,
    )


def draw_edge(ax, nodes: dict[str, Node], e: Edge) -> None:
    src, dst = nodes[e.src], nodes[e.dst]
    # auto pick sides by relative position
    if dst.y > src.y + 0.01:
        p1 = _connect_point(src, "top")
        p2 = _connect_point(dst, "bottom")
    elif dst.y < src.y - 0.01:
        p1 = _connect_point(src, "bottom")
        p2 = _connect_point(dst, "top")
    elif dst.x > src.x:
        p1 = _connect_point(src, "right")
        p2 = _connect_point(dst, "left")
    else:
        p1 = _connect_point(src, "left")
        p2 = _connect_point(dst, "right")

    arrow = FancyArrowPatch(
        p1,
        p2,
        arrowstyle="-|>",
        mutation_scale=14,
        linewidth=1.5,
        color="#444444",
        linestyle=e.style,
        connectionstyle=f"arc3,rad={e.rad}",
        zorder=1,
    )
    ax.add_patch(arrow)
    if e.label:
        mx = (p1[0] + p2[0]) / 2
        my = (p1[1] + p2[1]) / 2
        ax.text(
            mx,
            my + 0.012,
            e.label,
            ha="center",
            va="bottom",
            fontsize=EDGE_LABEL,
            color="#222222",
            fontfamily=FONT,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#cccccc", alpha=0.95),
            zorder=4,
        )


def build_diagram() -> tuple[list[Node], list[Edge]]:
    cx = 0.50
    bw, bh = 0.34, 0.055
    nodes: list[Node] = [
        Node("i0", cx, 0.96, bw, bh, "0. Celery Beat (10 с)\npoll_imap", "infra"),
        Node("i1", cx, 0.895, bw, bh, "0.1 IMAP UNSEEN → process_email", "infra"),
        Node("s1", cx, 0.83, bw, bh, "1. Старт — новое письмо", "start", "round"),
        Node("n2", cx, 0.765, bw, bh, "2. imap_listener", "agent"),
        Node("n3", cx, 0.70, bw, bh, "3. spam_filter — правила, Прил. А", "agent"),
        Node("d4", cx, 0.635, 0.22, 0.09, "4. Спам\nпо правилам?", "decision", "diamond"),
        Node("spam", 0.17, 0.635, 0.24, bh, "5A. Конец\nstatus=spam", "end_spam", "round"),
        Node("n68", cx, 0.555, bw, 0.075,
             "6. identify_sender (RAG Qdrant)\n7. process_content (PDF/DOCX/XLSX)\n8. route_department (1× LLM)", "agent"),
        Node("d9", cx, 0.475, 0.24, 0.09, "9. is_spam AND\nconf ≥ 0.85?", "decision", "diamond"),
        Node("d10", cx, 0.395, 0.24, 0.09, "10. Серая зона?\n0.70 ≤ conf < 0.85", "decision", "diamond"),
        Node("d11", cx, 0.315, 0.22, 0.09, "11. dept_conf\n< 0.70?", "decision", "diamond"),
        Node("n13", cx, 0.235, bw, bh, "13. create_erp_task → 1С", "agent"),
        Node("d14", cx, 0.165, 0.18, 0.08, "14. 1С OK?", "decision", "diamond"),
        Node("done", cx, 0.085, bw, bh, "15. Конец — status=done", "end_ok", "round"),
        Node("err", 0.17, 0.085, 0.26, bh, "15E. error → эскалация УД", "end_err", "round"),
        Node("retry", 0.17, 0.165, 0.26, bh, "14.1 retry_erp\n600 с × 5", "agent"),
        # HITL column
        Node("hitl", 0.82, 0.395, 0.30, 0.045, "12. Петля HITL — Сотрудник УД", "ud"),
        Node("h1", 0.82, 0.345, 0.32, 0.05, "12.1 GET …/email-messages\n?status=awaiting_human", "ud"),
        Node("h2", 0.82, 0.285, 0.32, 0.055, "12.2 POST …/resolve-human\nmark_spam | mark_not_spam\napprove_routing", "ud"),
        Node("h3", 0.82, 0.225, 0.32, 0.05, "12.3 continue_after_human\nили reprocess_message", "agent"),
    ]
    edges: list[Edge] = [
        Edge("i0", "i1"),
        Edge("i1", "s1"),
        Edge("s1", "n2"),
        Edge("n2", "n3"),
        Edge("n3", "d4"),
        Edge("d4", "spam", "Да", rad=-0.1),
        Edge("d4", "n68", "Нет"),
        Edge("n68", "d9"),
        Edge("d9", "spam", "Да", rad=0.15),
        Edge("d9", "d10", "Нет"),
        Edge("d10", "hitl", "Да", rad=0.05),
        Edge("d10", "d11", "Нет"),
        Edge("d11", "hitl", "Да", rad=0.0),
        Edge("d11", "n13", "Нет"),
        Edge("hitl", "h1"),
        Edge("h1", "h2"),
        Edge("h2", "h3"),
        Edge("h3", "n13", "подтверждено", rad=-0.25),
        Edge("n13", "d14"),
        Edge("d14", "done", "Да"),
        Edge("d14", "retry", "Нет", rad=-0.1),
        Edge("retry", "d14", "повтор", rad=-0.25),
        Edge("retry", "err", "5× fail"),
    ]
    return nodes, edges


def draw_legend(ax) -> None:
    items = [
        ("start", "Старт / успешный конец"),
        ("agent", "ИИ-агент"),
        ("decision", "Решение"),
        ("ud", "Сотрудник УД"),
        ("infra", "Инфраструктура"),
        ("end_spam", "Спам — не регистрируется"),
        ("end_err", "Ошибка 1С"),
    ]
    y = 0.02
    ax.text(0.02, y + 0.04, "Легенда:", fontsize=LEGEND_SIZE, fontweight="bold", fontfamily=FONT)
    for i, (kind, label) in enumerate(items):
        face, edge, _ = STYLE[kind]
        rect = FancyBboxPatch(
            (0.02 + (i % 4) * 0.24, y - 0.015 - (i // 4) * 0.035),
            0.03,
            0.022,
            boxstyle="square,pad=0",
            facecolor=face,
            edgecolor=edge,
            linewidth=1.2,
        )
        ax.add_patch(rect)
        ax.text(
            0.055 + (i % 4) * 0.24,
            y - 0.004 - (i // 4) * 0.035,
            label,
            fontsize=SMALL_SIZE,
            va="center",
            fontfamily=FONT,
        )


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    nodes, edges = build_diagram()
    nmap = _node_map(nodes)

    fig, ax = plt.subplots(figsize=(14, 20), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.5,
        0.995,
        "Агент-Почта — обработка входящей корреспонденции",
        ha="center",
        va="top",
        fontsize=TITLE_SIZE,
        fontweight="bold",
        fontfamily=FONT,
    )
    ax.text(
        0.5,
        0.978,
        "UI: /agents/incoming-mail  ·  API: :8080  ·  Пороги: spam 0.85 / gray 0.70 / dept 0.70",
        ha="center",
        va="top",
        fontsize=SMALL_SIZE,
        color="#555555",
        fontfamily=FONT,
    )

    api_box = FancyBboxPatch(
        (0.68, 0.48),
        0.30,
        0.14,
        boxstyle="round,pad=0.01",
        facecolor="#fafafa",
        edgecolor="#bbbbbb",
        linewidth=1.2,
        zorder=0,
    )
    ax.add_patch(api_box)
    api_text = (
        "REST API\n"
        "GET  /api/v1/email-messages\n"
        "POST …/resolve-human\n"
        "POST …/retry-erp\n"
        "POST …/restore-from-spam"
    )
    ax.text(0.83, 0.55, api_text, ha="center", va="center", fontsize=SMALL_SIZE, fontfamily=FONT, linespacing=1.4)

    for n in nodes:
        draw_node(ax, n)
    for e in edges:
        draw_edge(ax, nmap, e)
    draw_legend(ax)

    plt.tight_layout(pad=0.5)
    fig.savefig(OUTPUT, dpi=150, bbox_inches="tight", facecolor="white", edgecolor="none")
    plt.close(fig)
    print(f"Saved: {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
