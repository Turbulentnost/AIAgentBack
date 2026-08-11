"""Benchmark Polza.ai VLMs on ESKD v2 (8 GOST) prompts.

Usage:
  py scripts/eskd_polza_vlm_bench.py --pilot
  py scripts/eskd_polza_vlm_bench.py --run
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import fitz
import httpx

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "docs" / "ESKD_промпты"
OUT_ROOT = ROOT / "data" / "eskd_polza_bench"
SRC_DIR = ROOT / "data" / "temp" / "eskd_polza_src"
ENV_FILE = ROOT / "eskd.env"
POLZA_BASE = "https://polza.ai/api/v1"

GOST_ORDER = ["2.104", "2.201", "2.105", "2.109", "2.503", "2.316", "2.308", "2.301"]

# Requested → available on Polza (2026-08-10)
MODELS = [
    # requested qwen2.5-vl-32b missing → same family 72B VL instruct
    ("qwen/qwen2.5-vl-72b-instruct", "requested:qwen/qwen2.5-vl-32b-instruct"),
    ("z-ai/glm-4.6v", "requested:z-ai/glm-4.6v"),
    ("qwen/qwen3-vl-235b-a22b-instruct", "requested:qwen/qwen3-vl-235b-a22b-instruct"),
    ("qwen/qwen3-vl-30b-a3b-thinking", "requested:qwen/qwen3-vl-30b-a3b-thinking"),
    # internvl3-78b missing → closest large open VL on Polza
    ("baidu/ernie-4.5-vl-424b-a47b", "requested:opengvlab/internvl3-78b"),
]

# Known critical findings on kronshtein.pdf (manual/ESKD review)
KRON_EXPECTED = [
    {"id": "empty_designation", "needles": ["обозначен", "designation", "пусто"]},
    {"id": "informal_title", "needles": ["вроде как", "неформал", "наименован"]},
    {"id": "liter_question", "needles": ["литер", "?", "лит."]},
    {"id": "empty_sheet", "needles": ["лист", "листов", "sheet"]},
    {"id": "missing_checker", "needles": ["пров", "checker", "проверил"]},
    {"id": "tt_garbage", "needles": ["шалам", "балам", "бессмыслен", "мусор"]},
]


@dataclass
class CallResult:
    model: str
    pdf_stem: str
    page: int
    gost_key: str
    ok: bool
    elapsed_sec: float
    cost_rub: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    remarks_count: int = 0
    errors_count: int = 0
    warnings_count: int = 0
    json_valid: bool = False
    schema_ok: bool = False
    error: str | None = None
    remarks: list = field(default_factory=list)
    raw_preview: str = ""


def load_api_key() -> str:
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("ESKD_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("ESKD_API_KEY not found in eskd.env")


def model_slug(model: str) -> str:
    return model.replace("/", "__").replace(":", "_")


def parse_prompt_md(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)```", text, flags=re.S)
    if len(blocks) < 2:
        raise ValueError(f"Bad prompt file: {path}")
    return blocks[0].strip(), blocks[1].strip()


def load_prompts() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for key in GOST_ORDER:
        out[key] = parse_prompt_md(PROMPTS_DIR / f"{key}.md")
    return out


def render_page(pdf: Path, page_index: int, *, max_side: int = 1600) -> tuple[bytes, str]:
    doc = fitz.open(pdf)
    page = doc[page_index]
    zoom = min(2.0, max_side / max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png"), "image/png"


def parse_json_loose(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def schema_ok(remarks: list) -> bool:
    required = {"code", "severity", "element", "zone", "message", "expected", "found", "action_hint"}
    for r in remarks:
        if not isinstance(r, dict) or not required.issubset(r.keys()):
            return False
    return True


def call_vlm(
    client: httpx.Client,
    *,
    model: str,
    system: str,
    user: str,
    image_png: bytes,
) -> tuple[dict, float, dict]:
    b64 = base64.b64encode(image_png).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            },
        ],
        "temperature": 0.1,
        # Thinking VLMs burn completion budget on reasoning; keep headroom for JSON.
        "max_tokens": 12000,
    }
    t0 = time.perf_counter()
    resp = client.post(f"{POLZA_BASE}/chat/completions", json=payload)
    elapsed = time.perf_counter() - t0
    body = resp.json()
    if resp.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        msg = err.get("message") if isinstance(err, dict) else resp.text[:400]
        raise RuntimeError(f"HTTP {resp.status_code}: {msg}")
    usage = body.get("usage") or {}
    message = (body.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    content = str(content or "").strip()
    if not content:
        for key in ("reasoning_content", "reasoning", "refusal"):
            alt = message.get(key)
            if isinstance(alt, str) and alt.strip():
                content = alt.strip()
                break
    # Prefer JSON object if model wrapped reasoning + JSON.
    if content and ("{" not in content[:20]):
        m = re.search(r"\{[\s\S]*\}\s*$", content)
        if m:
            content = m.group(0)
    return {"raw": content, "usage": usage}, elapsed, body


def score_kron_text(blob: str) -> dict:
    text = (blob or "").lower()
    hits = []
    for item in KRON_EXPECTED:
        hit = any(n.lower() in text for n in item["needles"])
        hits.append({"id": item["id"], "hit": hit})
    return {
        "expected": len(KRON_EXPECTED),
        "hits": sum(1 for h in hits if h["hit"]),
        "recall": round(sum(1 for h in hits if h["hit"]) / len(KRON_EXPECTED), 3),
        "details": hits,
    }


def score_kron(remarks: list) -> dict:
    return score_kron_text(json.dumps(remarks, ensure_ascii=False))


def copy_inputs_for_models(pdfs: list[Path], models: list[tuple[str, str]]) -> None:
    inp = OUT_ROOT / "inputs"
    inp.mkdir(parents=True, exist_ok=True)
    for model, _note in models:
        slug = model_slug(model)
        for pdf in pdfs:
            dst = inp / f"{pdf.stem}__{slug}{pdf.suffix}"
            if not dst.exists():
                shutil.copy2(pdf, dst)


def run_bench(
    *,
    models: list[tuple[str, str]],
    pdfs: list[Path],
    pages_per_pdf: dict[str, list[int]],
    gost_keys: list[str],
    api_key: str,
) -> dict:
    prompts = load_prompts()
    copy_inputs_for_models(pdfs, models)
    results_root = OUT_ROOT / "results"
    results_root.mkdir(parents=True, exist_ok=True)

    all_calls: list[CallResult] = []
    summary_models: dict[str, dict] = {}

    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=600.0, headers=headers) as client:
        bal0 = client.get(f"{POLZA_BASE}/balance").json()
        for model, note in models:
            slug = model_slug(model)
            model_dir = results_root / slug
            model_dir.mkdir(parents=True, exist_ok=True)
            model_calls: list[CallResult] = []
            t_model0 = time.perf_counter()
            cost_model = 0.0

            for pdf in pdfs:
                page_indexes = pages_per_pdf.get(pdf.stem, [0])
                pdf_bundle = {
                    "model": model,
                    "requested_note": note,
                    "pdf": pdf.name,
                    "pages": [],
                }
                for page_i in page_indexes:
                    image_png, _mime = render_page(pdf, page_i)
                    page_pack = {"page": page_i + 1, "gosts": {}}
                    for gost_key in gost_keys:
                        system, user_tmpl = prompts[gost_key]
                        user = (
                            user_tmpl.replace("{page}", str(page_i + 1))
                            .replace("{filename}", pdf.name)
                            .replace("{designation}", "")
                        )
                        print(
                            f"[{model}] {pdf.stem} p{page_i+1} {gost_key} ...",
                            flush=True,
                        )
                        try:
                            parsed_wrap, elapsed, _body = call_vlm(
                                client,
                                model=model,
                                system=system,
                                user=user,
                                image_png=image_png,
                            )
                            usage = parsed_wrap["usage"]
                            raw = parsed_wrap["raw"]
                            try:
                                data = parse_json_loose(raw)
                                remarks = data.get("remarks") if isinstance(data, dict) else None
                                if not isinstance(remarks, list):
                                    remarks = []
                                ok_json = True
                            except Exception as exc:
                                remarks = []
                                ok_json = False
                                raw = f"{raw}\n\nJSON_ERROR: {exc}"
                            errors = sum(
                                1
                                for r in remarks
                                if isinstance(r, dict) and r.get("severity") == "error"
                            )
                            warnings = sum(
                                1
                                for r in remarks
                                if isinstance(r, dict) and r.get("severity") == "warning"
                            )
                            cost = float(usage.get("cost_rub") or usage.get("cost") or 0.0)
                            cr = CallResult(
                                model=model,
                                pdf_stem=pdf.stem,
                                page=page_i + 1,
                                gost_key=gost_key,
                                ok=True,
                                elapsed_sec=round(elapsed, 3),
                                cost_rub=cost,
                                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                                completion_tokens=int(usage.get("completion_tokens") or 0),
                                total_tokens=int(usage.get("total_tokens") or 0),
                                remarks_count=len(remarks),
                                errors_count=errors,
                                warnings_count=warnings,
                                json_valid=ok_json,
                                schema_ok=ok_json and schema_ok(remarks),
                                remarks=remarks,
                                raw_preview=raw[:8000],
                            )
                        except Exception as exc:
                            cr = CallResult(
                                model=model,
                                pdf_stem=pdf.stem,
                                page=page_i + 1,
                                gost_key=gost_key,
                                ok=False,
                                elapsed_sec=0.0,
                                error=str(exc)[:500],
                            )
                        model_calls.append(cr)
                        all_calls.append(cr)
                        cost_model += cr.cost_rub
                        page_pack["gosts"][gost_key] = asdict(cr)
                        print(
                            f"  -> ok={cr.ok} t={cr.elapsed_sec}s cost={cr.cost_rub:.4f} "
                            f"E={cr.errors_count} W={cr.warnings_count} json={cr.json_valid}",
                            flush=True,
                        )
                        time.sleep(0.4)
                    pdf_bundle["pages"].append(page_pack)
                (model_dir / f"{pdf.stem}.json").write_text(
                    json.dumps(pdf_bundle, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            # quality on kronshtein (remarks + raw text — models sometimes fail JSON)
            kron_blob_parts: list[str] = []
            for c in model_calls:
                if "kronshtein" not in c.pdf_stem.lower():
                    continue
                if c.remarks:
                    kron_blob_parts.append(json.dumps(c.remarks, ensure_ascii=False))
                if c.raw_preview:
                    kron_blob_parts.append(c.raw_preview)
            quality = score_kron_text("\n".join(kron_blob_parts))
            model_summary = {
                "model": model,
                "requested_note": note,
                "elapsed_sec": round(time.perf_counter() - t_model0, 2),
                "cost_rub": round(cost_model, 4),
                "calls": len(model_calls),
                "ok_calls": sum(1 for c in model_calls if c.ok),
                "json_valid_rate": round(
                    sum(1 for c in model_calls if c.json_valid) / max(1, len(model_calls)), 3
                ),
                "schema_ok_rate": round(
                    sum(1 for c in model_calls if c.schema_ok) / max(1, len(model_calls)), 3
                ),
                "avg_elapsed_sec": round(
                    sum(c.elapsed_sec for c in model_calls) / max(1, len(model_calls)), 2
                ),
                "total_errors": sum(c.errors_count for c in model_calls),
                "total_warnings": sum(c.warnings_count for c in model_calls),
                "kronshtein_quality": quality,
            }
            summary_models[model] = model_summary
            (model_dir / "summary.json").write_text(
                json.dumps(model_summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        bal1 = client.get(f"{POLZA_BASE}/balance").json()

    comparison = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "balance_before": bal0,
        "balance_after": bal1,
        "total_calls": len(all_calls),
        "total_ok": sum(1 for c in all_calls if c.ok),
        "total_cost_rub": round(sum(c.cost_rub for c in all_calls), 4),
        "total_elapsed_sec": round(sum(c.elapsed_sec for c in all_calls), 2),
        "models": summary_models,
        "notes": {
            "pipeline": "ESKD v2 per_gost prompts from docs/ESKD_промпты",
            "missing_requested": [
                "qwen/qwen2.5-vl-32b-instruct → used qwen/qwen2.5-vl-72b-instruct",
                "opengvlab/internvl3-78b → used baidu/ernie-4.5-vl-424b-a47b",
            ],
            "gbp_pages": "page 1 only by default (--max-pages-gbp); source PDF has 3 pages",
        },
    }
    (OUT_ROOT / "COMPARISON.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_comparison_md(comparison)
    return comparison


def write_comparison_md(comparison: dict) -> None:
    lines = [
        "# Polza VLM bench — ESKD v2 (8 ГОСТ)",
        "",
        f"Создано: {comparison['created_at']}",
        "",
        f"Баланс до: {comparison['balance_before'].get('amount')} ₽",
        f"Баланс после: {comparison['balance_after'].get('amount')} ₽",
        "",
        "## Замены моделей (нет в каталоге Polza)",
        "",
    ]
    for n in comparison["notes"]["missing_requested"]:
        lines.append(f"- {n}")
    lines += [
        "",
        "## Сводка",
        "",
        "| Модель | сек | ₽ | JSON% | schema% | avg сек | E | W | kron recall |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, s in comparison["models"].items():
        q = s.get("kronshtein_quality") or {}
        lines.append(
            f"| `{model}` | {s['elapsed_sec']} | {s['cost_rub']} | "
            f"{s['json_valid_rate']} | {s['schema_ok_rate']} | {s['avg_elapsed_sec']} | "
            f"{s['total_errors']} | {s['total_warnings']} | {q.get('recall', '—')} |"
        )
    lines += [
        "",
        "## Качество на кронштейне",
        "",
        "Ожидаемые критичные находки: пустое обозначение, «вроде как», литера `?`, "
        "пустой Лист, нет Пров., ТТ «Шалам балам».",
        "",
    ]
    for model, s in comparison["models"].items():
        q = s.get("kronshtein_quality") or {}
        lines.append(f"### `{model}` — recall {q.get('recall')}")
        for d in q.get("details") or []:
            mark = "✓" if d.get("hit") else "✗"
            lines.append(f"- {mark} {d.get('id')}")
        lines.append("")
    (OUT_ROOT / "COMPARISON.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true", help="1 model × 1 page × 1 GOST")
    parser.add_argument("--run", action="store_true", help="Full selected matrix")
    parser.add_argument("--max-pages-gbp", type=int, default=1, help="GBP pages to evaluate")
    args = parser.parse_args()
    if not args.pilot and not args.run:
        parser.error("Specify --pilot or --run")

    api_key = load_api_key()
    pdfs = [
        SRC_DIR / "GBP-025-16.00.00.000.pdf",
        SRC_DIR / "kronshtein.pdf",
    ]
    for p in pdfs:
        if not p.is_file():
            raise SystemExit(f"Missing source PDF: {p}")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    if args.pilot:
        models = [MODELS[1]]  # glm-4.6v
        pages = {"GBP-025-16.00.00.000": [0], "kronshtein": [0]}
        gosts = ["2.104"]
        # only kron for cost estimate
        pdfs = [SRC_DIR / "kronshtein.pdf"]
        pages = {"kronshtein": [0]}
    else:
        models = MODELS
        gbp_pages = list(range(max(1, args.max_pages_gbp)))
        pages = {"GBP-025-16.00.00.000": gbp_pages, "kronshtein": [0]}
        gosts = GOST_ORDER

    comparison = run_bench(
        models=models,
        pdfs=pdfs,
        pages_per_pdf=pages,
        gost_keys=gosts,
        api_key=api_key,
    )
    # Avoid Windows cp1251 console crashes on arrows/checkmarks in nested fields.
    summary = {
        "total_calls": comparison.get("total_calls"),
        "total_cost_rub": comparison.get("total_cost_rub"),
        "total_elapsed_sec": comparison.get("total_elapsed_sec"),
        "models": {
            m: {
                "elapsed_sec": s.get("elapsed_sec"),
                "cost_rub": s.get("cost_rub"),
                "json_valid_rate": s.get("json_valid_rate"),
                "schema_ok_rate": s.get("schema_ok_rate"),
                "avg_elapsed_sec": s.get("avg_elapsed_sec"),
                "kron_recall": (s.get("kronshtein_quality") or {}).get("recall"),
                "requested_note": s.get("requested_note"),
            }
            for m, s in (comparison.get("models") or {}).items()
        },
        "out": str(OUT_ROOT),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
