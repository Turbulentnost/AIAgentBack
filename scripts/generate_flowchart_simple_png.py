"""Упрощённая PNG блок-схема Агент-Почта (русский + HITL).

Запуск: python scripts/generate_flowchart_simple_png.py
Выход: docs/AGENT-FLOWCHART-SIMPLE.png
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_flowchart_png import (  # noqa: E402
    FONT,
    SMALL_SIZE,
    TITLE_SIZE,
    Edge,
    Node,
    _node_map,
    draw_edge,
    draw_node,
)

OUTPUT = ROOT / "docs" / "AGENT-FLOWCHART-SIMPLE.png"


def build_diagram() -> tuple[list[Node], list[Edge]]:
    cx = 0.46
    bw, bh = 0.34, 0.056
    nodes: list[Node] = [
        Node("START", cx, 0.94, 0.20, 0.046, "СТАРТ", "start", "round"),
        Node("N1", cx, 0.855, bw, bh, "1. Приём письма", "agent"),
        Node("N2", cx, 0.775, bw, bh, "2. Фильтр спама", "agent"),
        Node("N3", cx, 0.695, bw, bh, "3. Идентификация отправителя", "agent"),
        Node("N4", cx, 0.615, bw, bh, "4. Обработка вложений", "agent"),
        Node("N5", cx, 0.535, bw, bh, "5. Маршрутизация", "agent"),
        Node("N6", cx, 0.395, bw, bh, "6. Задача в 1С", "agent"),
        Node("F", cx, 0.275, bw, bh, "7. Сохранение", "agent"),
        Node("END", cx, 0.165, 0.20, 0.046, "КОНЕЦ", "end_ok", "round"),
        Node("H", 0.86, 0.48, 0.26, bh, "Ожидание УД", "ud"),
        Node("E", 0.10, 0.395, 0.22, bh, "retry-erp", "ud"),
    ]
    edges: list[Edge] = [
        Edge("START", "N1"),
        Edge("N1", "N2"),
        Edge("N2", "N3", "не спам"),
        Edge("N2", "F", "спам по правилам", rad=-0.4),
        Edge("N3", "N4"),
        Edge("N4", "N5"),
        Edge("N5", "F", "спам ≥ 0,85", rad=-0.3),
        Edge("N5", "H", "серая зона /\nнизкий отдел", rad=0.05),
        Edge("N5", "N6", "OK"),
        Edge("N6", "F"),
        Edge("F", "END"),
        Edge("H", "N6", "approve_routing", rad=-0.25),
        Edge("H", "N1", "mark_not_spam /\nrestore-from-spam", rad=0.45),
        Edge("H", "END", "mark_spam", rad=0.35),
        Edge("N6", "E", "ошибка 1С", rad=-0.15),
        Edge("E", "N6", rad=-0.4),
    ]
    return nodes, edges


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    nodes, edges = build_diagram()
    nmap = _node_map(nodes)

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 16), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.5,
        0.985,
        "Агент-Почта — упрощённая схема обработки",
        ha="center",
        va="top",
        fontsize=TITLE_SIZE,
        fontweight="bold",
        fontfamily=FONT,
    )
    ax.text(
        0.5,
        0.965,
        "Синий — агент  ·  Красный — сотрудник УД  ·  Пороги: spam 0,85 / gray 0,70 / dept 0,70",
        ha="center",
        va="top",
        fontsize=SMALL_SIZE,
        color="#555555",
        fontfamily=FONT,
    )

    for n in nodes:
        draw_node(ax, n)
    for e in edges:
        draw_edge(ax, nmap, e)

    plt.tight_layout(pad=0.4)
    fig.savefig(OUTPUT, dpi=150, bbox_inches="tight", facecolor="white", edgecolor="none")
    plt.close(fig)
    print(f"Saved: {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
