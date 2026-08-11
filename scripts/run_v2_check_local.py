"""Health check LM Studio + run per_gost v2 on local test PDF."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

PDF = Path(__file__).resolve().parents[1] / "data" / "temp" / "test_drawing.pdf"
URL = "http://127.0.0.1:8080/api/v1/eskd/check/stream"
HEADERS = {"X-Dev-User": "otk.ivanov", "X-Dev-Roles": "ESKD_OTK"}
DATA = {
    "all_pages": "true",
    "pipeline_mode": "per_gost",
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
    try:
        r = httpx.get("http://192.168.1.157:1234/v1/models", timeout=10)
        print("LM Studio:", r.status_code)
        if r.status_code == 200:
            models = [m.get("id") for m in r.json().get("data", [])]
            print("  models:", models[:10])
    except Exception as exc:
        print("LM Studio FAIL:", exc)
        return 1

    for health_url in ("http://127.0.0.1:8765/health", "http://127.0.0.1:8080/health"):
        try:
            r = httpx.get(health_url, timeout=5)
            print(health_url, r.status_code, r.text[:300])
        except Exception as exc:
            print(health_url, "FAIL", exc)
            return 1

    if not PDF.is_file():
        print(f"PDF not found: {PDF}", file=sys.stderr)
        return 1

    print("\n=== per_gost v2 check ===")
    print(f"PDF: {PDF.name} ({PDF.stat().st_size} bytes)")
    t0 = time.time()
    complete: dict | None = None

    with PDF.open("rb") as handle:
        files = {"files": (PDF.name, handle, "application/pdf")}
        timeout = httpx.Timeout(connect=30.0, read=None, write=120.0, pool=120.0)
        with httpx.stream(
            "POST", URL, data=DATA, files=files, headers=HEADERS, timeout=timeout
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

                    if event == "gost_start":
                        print(
                            f"[{elapsed:6.0f}s] GOST {obj.get('gost_key')} "
                            f"start ({obj.get('index')}/{obj.get('total')})"
                        )
                    elif event == "gost_complete":
                        print(
                            f"[{elapsed:6.0f}s] GOST {obj.get('gost_key')} "
                            f"done pct={obj.get('percent')}"
                        )
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

    print(f"\nTotal elapsed: {time.time() - t0:.1f}s")
    if complete:
        out = Path(__file__).resolve().parents[1] / "data" / "temp" / "v2_check_result.json"
        out.write_text(json.dumps(complete, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Result saved: {out}")
        gost_report = complete.get("gost_report") or {}
        if gost_report:
            print("\nPer-GOST summary:")
            for key, info in gost_report.items():
                print(
                    f"  {key}: errors={info.get('errors', 0)} "
                    f"warnings={info.get('warnings', 0)}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
