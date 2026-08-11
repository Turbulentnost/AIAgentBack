"""Run per_gost v2 ESKD check on a PDF path from argv."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

URL = "http://127.0.0.1:8080/api/v1/eskd/check/stream"
HEADERS = {"X-Dev-User": "otk.ivanov", "X-Dev-Roles": "ESKD_OTK"}


def default_data(pipeline_mode: str) -> dict[str, str]:
    return {
        "all_pages": "true",
        "pipeline_mode": pipeline_mode,
        "force_refresh": "true",
    }


def parse_sse_block(block: str) -> tuple[str, str | None]:
    event = "message"
    payload: str | None = None
    for line in block.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
    return event, payload


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: run_v2_check_file.py <pdf>", file=sys.stderr)
        return 1

    pdf = Path(sys.argv[1]).expanduser().resolve()
    pipeline_mode = sys.argv[2] if len(sys.argv) > 2 else "per_gost"
    data = default_data(pipeline_mode)
    if not pdf.is_file():
        print(f"File not found: {pdf}", file=sys.stderr)
        return 1

    out = pdf.with_suffix(".v2_result.json")

    print("=== per_gost v2 check ===")
    print(f"PDF: {pdf} ({pdf.stat().st_size} bytes)")
    t0 = time.time()
    complete: dict | None = None

    with pdf.open("rb") as handle:
        files = {"files": (pdf.name, handle, "application/pdf")}
        timeout = httpx.Timeout(connect=30.0, read=None, write=300.0, pool=300.0)
        with httpx.stream(
            "POST", URL, data=data, files=files, headers=HEADERS, timeout=timeout
        ) as resp:
            print("HTTP", resp.status_code)
            resp.raise_for_status()
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    event, payload = parse_sse_block(block)
                    if not payload:
                        continue
                    elapsed = time.time() - t0
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    if event == "page_start":
                        print(
                            f"[{elapsed:6.0f}s] page {obj.get('index')}/{obj.get('total')} "
                            f"{obj.get('source', '')}"
                        )
                    elif event == "gost_start":
                        print(
                            f"[{elapsed:6.0f}s] GOST {obj.get('gost_key')} "
                            f"({obj.get('index')}/{obj.get('total')}) page={obj.get('page')}"
                        )
                    elif event == "gost_complete":
                        print(
                            f"[{elapsed:6.0f}s] GOST {obj.get('gost_key')} done "
                            f"pct={obj.get('percent')}"
                        )
                    elif event in ("model_retry", "gost_queued", "queue_round"):
                        print(f"[{elapsed:6.0f}s] {event}: {obj}")
                    elif event == "complete":
                        complete = obj
                        print(
                            f"[{elapsed:6.0f}s] COMPLETE "
                            f"errors={obj.get('total_errors')} "
                            f"warnings={obj.get('total_warnings')} "
                            f"items={obj.get('total_items')}"
                        )
                    elif event == "error":
                        print(f"[{elapsed:6.0f}s] ERROR", obj)

    elapsed = time.time() - t0
    print(f"\nTotal elapsed: {elapsed:.1f}s")

    if complete:
        out.write_text(json.dumps(complete, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Result saved: {out}")
        print("\nSummary:", complete.get("summary", ""))
        gost_summary = complete.get("gost_summary") or {}
        if gost_summary:
            print("GOST summary:", json.dumps(gost_summary, ensure_ascii=False))
        for item in complete.get("items") or []:
            print(
                f"\n--- {item.get('source')} --- "
                f"errors={item.get('errors_count')} warnings={item.get('warnings_count')}"
            )
            for err in item.get("errors") or []:
                print(f"  [E] {err.get('gost_reference','')} {err.get('message','')[:120]}")
            for warn in item.get("warnings") or []:
                print(f"  [W] {warn.get('gost_reference','')} {warn.get('message','')[:120]}")

    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
