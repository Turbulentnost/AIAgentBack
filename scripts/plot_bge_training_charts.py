"""Графики итеративного обучения BGE — чёрный фон, светофор, grid по 5%."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "data" / "stats"

# Данные из отчётов (до / после итеративного обучения)
OPERATOR_IN_SAMPLE = {
    "title": "Обучение BGE — размеченные оператором (in-sample)",
    "total": 192,
    "target": 90,
    "stages": [
        ("Старт", 53.6, 103),
        ("Итерация 1", 60.9, 117),
        ("Итерация 2", 87.0, 167),
        ("Итерация 3", 91.7, 176),
    ],
}

OPERATOR_HOLDOUT = {
    "title": "Holdout 80/20 — метки оператора (без утечки)",
    "total": 28,
    "target": 90,
    "stages": [
        ("Старт", 53.6, 15),
        ("После обучения", 96.4, 27),
    ],
}

HOLDOUT_ERP = {
    "title": "Holdout ERP — новые письма (не в обучении)",
    "total": 100,
    "target": 90,
    "stages": [
        ("Старт", 13.0, 13),
        ("После обучения", 21.0, 21),
    ],
}

YELLOW = (1.0, 0.84, 0.0)
GREEN = (0.0, 0.91, 0.46)
PCT_MIN = 53.0
PCT_MAX = 91.0


def traffic_light_rgb(pct: float) -> tuple[float, float, float]:
    """53% → жёлтый, 91% → ярко-зелёный."""
    t = (pct - PCT_MIN) / (PCT_MAX - PCT_MIN)
    t = max(0.0, min(1.0, t))
    # ease: медленнее в жёлтой зоне, быстрее зеленеет к концу
    t = t**0.75
    return (
        YELLOW[0] + (GREEN[0] - YELLOW[0]) * t,
        YELLOW[1] + (GREEN[1] - YELLOW[1]) * t,
        YELLOW[2] + (GREEN[2] - YELLOW[2]) * t,
    )


def plot_dataset(dataset: dict, out_path: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator

    stages = dataset["stages"]
    labels = [s[0] for s in stages]
    pcts = [s[1] for s in stages]
    correct = [s[2] for s in stages]
    total = dataset["total"]
    target = dataset["target"]

    y_min = max(0, int(min(pcts) - 8))
    y_max = min(100, int(max(pcts) + 8))
    if y_max < target + 2:
        y_max = target + 5

    fig, ax = plt.subplots(figsize=(12, 7), facecolor="#0a0a0a")
    ax.set_facecolor("#0a0a0a")

    # Grid каждые 5%
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.grid(which="major", axis="y", color="#333333", linewidth=0.8, alpha=0.9)
    ax.grid(which="major", axis="x", color="#2a2a2a", linewidth=0.6, alpha=0.7)

    x = list(range(len(stages)))
    colors = [traffic_light_rgb(p) for p in pcts]

    ax.plot(x, pcts, color="#555555", linewidth=2, zorder=2, alpha=0.5)
    ax.scatter(x, pcts, s=220, c=colors, edgecolors="white", linewidths=2, zorder=5)

    # Цель 90%
    ax.axhline(target, color="#FF5252", linestyle="--", linewidth=1.5, alpha=0.85, zorder=1)
    ax.text(
        len(stages) - 0.05,
        target + 0.35,
        f"Цель {target}%",
        color="#FF8A80",
        fontsize=11,
        ha="right",
        fontweight="bold",
    )

    for i, (pct, corr, col) in enumerate(zip(pcts, correct, colors)):
        pct_str = f"{pct:.1f}%".replace(".0%", "%")
        ax.annotate(
            pct_str,
            (x[i], pct),
            textcoords="offset points",
            xytext=(0, 18),
            ha="center",
            fontsize=14,
            fontweight="bold",
            color=col,
        )
        ax.annotate(
            f"✓ {corr}",
            (x[i], pct),
            textcoords="offset points",
            xytext=(0, -22),
            ha="center",
            fontsize=12,
            color="#CCCCCC",
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#E0E0E0", fontsize=12, fontweight="medium")
    ax.set_ylabel("Точность, %", color="#B0B0B0", fontsize=12)
    ax.set_ylim(y_min, y_max)
    ax.set_xlim(-0.4, len(stages) - 0.6)

    ax.tick_params(axis="y", colors="#888888", labelsize=10)
    ax.tick_params(axis="x", colors="#E0E0E0")
    for spine in ax.spines.values():
        spine.set_color("#444444")

    ax.set_title(dataset["title"], color="#FFFFFF", fontsize=16, fontweight="bold", pad=16)

    # Угол: всего писем
    box_text = f"Всего писем\n{total}"
    ax.text(
        0.02,
        0.98,
        box_text,
        transform=ax.transAxes,
        fontsize=13,
        fontweight="bold",
        color="#FFFFFF",
        va="top",
        ha="left",
        bbox=dict(
            boxstyle="round,pad=0.5",
            facecolor="#1a1a1a",
            edgecolor="#555555",
            linewidth=1.2,
        ),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def plot_combined(out_path: Path) -> None:
    """Три выборки на одном холсте."""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator

    datasets = [OPERATOR_IN_SAMPLE, OPERATOR_HOLDOUT, HOLDOUT_ERP]
    fig, axes = plt.subplots(1, 3, figsize=(18, 6.5), facecolor="#0a0a0a")

    for ax, dataset in zip(axes, datasets):
        ax.set_facecolor("#0a0a0a")
        stages = dataset["stages"]
        labels = [s[0] for s in stages]
        pcts = [s[1] for s in stages]
        correct = [s[2] for s in stages]
        total = dataset["total"]
        target = dataset["target"]

        y_min = max(0, int(min(pcts) - 8))
        y_max = min(100, int(max(pcts) + 8))
        if y_max < target + 2:
            y_max = target + 5

        ax.yaxis.set_major_locator(MultipleLocator(5))
        ax.grid(which="major", axis="y", color="#333333", linewidth=0.7)
        ax.grid(which="major", axis="x", color="#2a2a2a", linewidth=0.5)

        x = list(range(len(stages)))
        colors = [traffic_light_rgb(p) for p in pcts]
        ax.plot(x, pcts, color="#555", linewidth=1.5, alpha=0.4)
        ax.scatter(x, pcts, s=140, c=colors, edgecolors="white", linewidths=1.5, zorder=5)
        ax.axhline(target, color="#FF5252", linestyle="--", linewidth=1, alpha=0.7)

        for i, (pct, corr, col) in enumerate(zip(pcts, correct, colors)):
            ax.annotate(
                f"{pct:.1f}%",
                (x[i], pct),
                textcoords="offset points",
                xytext=(0, 12),
                ha="center",
                fontsize=11,
                fontweight="bold",
                color=col,
            )
            ax.annotate(
                f"✓ {corr}",
                (x[i], pct),
                textcoords="offset points",
                xytext=(0, -16),
                ha="center",
                fontsize=9,
                color="#BBBBBB",
            )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, color="#DDD", fontsize=9, rotation=12, ha="right")
        ax.set_ylim(y_min, y_max)
        ax.set_title(dataset["title"], color="#FFF", fontsize=11, fontweight="bold", pad=10)
        ax.tick_params(colors="#888", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#444")

        ax.text(
            0.03,
            0.97,
            f"n={total}",
            transform=ax.transAxes,
            fontsize=10,
            fontweight="bold",
            color="#FFF",
            va="top",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#1a1a1a", edgecolor="#555"),
        )

    fig.suptitle(
        "BGE: обучение на выборках оператора и holdout ERP",
        color="#FFFFFF",
        fontsize=15,
        fontweight="bold",
        y=1.02,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


def main() -> None:
    out_dir = STATS / "charts"
    plot_dataset(OPERATOR_IN_SAMPLE, out_dir / "bge_train_operator_insample.png")
    plot_dataset(OPERATOR_HOLDOUT, out_dir / "bge_train_operator_holdout.png")
    plot_dataset(HOLDOUT_ERP, out_dir / "bge_train_holdout_erp.png")
    plot_combined(out_dir / "bge_train_all_datasets.png")

    meta = {
        "datasets": {
            "operator_insample": OPERATOR_IN_SAMPLE,
            "operator_holdout": OPERATOR_HOLDOUT,
            "holdout_erp": HOLDOUT_ERP,
        },
        "color_scale": {"min_pct_yellow": PCT_MIN, "max_pct_green": PCT_MAX},
    }
    (out_dir / "bge_train_charts_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
