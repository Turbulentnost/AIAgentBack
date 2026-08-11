"""Export ESKD v2 per-GOST prompts to docs/ESKD_промпты/*.md and eskd-agent/model/prompts/."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODEL_DIR = Path(r"c:\Users\mdj\Desktop\рабочее\agent_nd\агенты\eskd-agent\model")
POCHTA_ROOT = Path(__file__).resolve().parents[1]
if not MODEL_DIR.is_dir():
    MODEL_DIR = POCHTA_ROOT.parents[2] / "agent_nd" / "агенты" / "eskd-agent" / "model"

sys.path.insert(0, str(MODEL_DIR))
spec = importlib.util.spec_from_file_location("per_gost_pipeline", MODEL_DIR / "per_gost_pipeline.py")
pg = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(pg)

from gost_catalog import GOST_LINE_ORDER  # noqa: E402

DOCS = POCHTA_ROOT / "docs"
OUT_DIR = DOCS / "ESKD_промпты"
MODEL_PROMPTS = MODEL_DIR / "prompts"
INDEX = DOCS / "ESKD_промпты_8_ГОСТ.md"
FENCE = "```"


def render_gost_md(gost_key: str, gost_title: str) -> str:
    system = pg._build_gost_system_prompt(gost_key, gost_title).rstrip()
    user = pg._build_gost_user_prompt(
        gost_key=gost_key,
        gost_title=gost_title,
        page=1,
        filename="{filename}",
        designation="{designation}",
    ).replace("Лист 1,", "Лист {page},").rstrip()
    return (
        f"# {gost_key} — {gost_title}\n\n"
        f"## SYSTEM\n\n{FENCE}text\n{system}\n{FENCE}\n\n"
        f"## USER\n\n{FENCE}text\n{user}\n{FENCE}\n"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_PROMPTS.mkdir(parents=True, exist_ok=True)
    index_lines = [
        "# ESKD — промпты v2 (8 ГОСТ)\n\n"
        "По одному файлу на ГОСТ. Плейсхолдеры: `{page}`, `{filename}`, `{designation}`.\n\n"
        f"Runtime (per_gost v2) читает `eskd-agent/model/prompts/` "
        "(env `ESKD_PROMPTS_DIR` для переопределения).\n\n",
    ]

    for gost_key, gost_title in GOST_LINE_ORDER:
        body = render_gost_md(gost_key, gost_title)
        doc_path = OUT_DIR / f"{gost_key}.md"
        model_path = MODEL_PROMPTS / f"{gost_key}.md"
        doc_path.write_text(body, encoding="utf-8")
        model_path.write_text(body, encoding="utf-8")
        print(f"Wrote {doc_path.name} -> docs + model/prompts")
        index_lines.append(f"- [{gost_key} — {gost_title}](ESKD_промпты/{gost_key}.md)\n")

    INDEX.write_text("".join(index_lines), encoding="utf-8")
    print(f"Wrote {INDEX}")
    print(f"Model prompts dir: {MODEL_PROMPTS}")


if __name__ == "__main__":
    main()
