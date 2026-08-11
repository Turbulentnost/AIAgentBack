"""Upload a PDF to ESKD check/stream and print SSE progress."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

PDF = Path(
    r"\\192.168.1.198\Files\10.СКТБ\Общие Документы\_КОНСТРУКТОРСКАЯ ДОКУМЕНТАЦИЯ"
    r"\Архив КД\GFG\GBP.02.24.002-01 Табличка паспортная.pdf"
)
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
    if not PDF.is_file():
        print(f"File not found: {PDF}", file=sys.stderr)
        return 1

    print(f"Uploading {PDF.name} ({PDF.stat().st_size} bytes)...", flush=True)
    t0 = time.time()

    with PDF.open("rb") as handle:
        files = {"files": (PDF.name, handle, "application/pdf")}
        timeout = httpx.Timeout(connect=30.0, read=None, write=120.0, pool=120.0)
        with httpx.stream("POST", URL, data=DATA, files=files, headers=HEADERS, timeout=timeout) as resp:
            print(f"HTTP {resp.status_code}", flush=True)
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
                        print(f"[{elapsed:6.0f}s] {event}: {payload[:120]}", flush=True)
                        continue

                    if event == "gost_start":
                        print(
                            f"[{elapsed:6.0f}s] {event}: GOST {obj.get('gost_key')} "
                            f"({obj.get('index')}/{obj.get('total')})",
                            flush=True,
                        )
                    elif event == "gost_complete":
                        print(
                            f"[{elapsed:6.0f}s] {event}: GOST {obj.get('gost_key')} "
                            f"percent={obj.get('percent')}",
                            flush=True,
                        )
                    elif event == "complete":
                        print(
                            f"[{elapsed:6.0f}s] DONE: errors={obj.get('total_errors')} "
                            f"warnings={obj.get('total_warnings')} items={obj.get('total_items')}",
                            flush=True,
                        )
                    else:
                        label = (
                            obj.get("message")
                            or obj.get("gost_key")
                            or obj.get("summary")
                            or obj.get("status")
                            or obj.get("type")
                            or event
                        )
                        print(f"[{elapsed:6.0f}s] {event}: {label}", flush=True)

    print("Stream finished", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
