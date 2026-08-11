"""ESKD v2 check with LM Studio health monitoring and cleanup on failure."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

PDF = Path(
    r"\\192.168.1.198\Files\10.СКТБ\Общие Документы\_КОНСТРУКТОРСКАЯ ДОКУМЕНТАЦИЯ"
    r"\Архив КД\GFG\GBP-020-100J.00.000.pdf"
)
ESKD_URL = "http://127.0.0.1:8080/api/v1/eskd/check/stream"
CANCEL_URL = "http://127.0.0.1:8080/api/v1/eskd/check/cancel"
LM_HOST = "http://192.168.1.157:1234"
VLM_MODEL = "qwen3-vl-8b-thinking"
HEADERS = {"X-Dev-User": "otk.ivanov", "X-Dev-Roles": "ESKD_OTK"}
DATA = {
    "all_pages": "true",
    "pipeline_mode": "per_gost",
    "force_refresh": "true",
}
GOST_STALL_SEC = 900
LM_PROBE_FAIL_LIMIT = 2
LM_PROBE_INTERVAL_SEC = 45


class CheckState:
    def __init__(self) -> None:
        self.job_id: str | None = None
        self.last_event_at = time.time()
        self.last_gost_key: str | None = None
        self.last_gost_index: int | None = None
        self.failed = False
        self.fail_reason = ""
        self.done = False
        self.lock = threading.Lock()


def parse_sse_block(block: str) -> tuple[str, str | None]:
    event = "message"
    payload: str | None = None
    for line in block.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
    return event, payload


def lm_probe(*, inference: bool = False, timeout_sec: float = 20.0) -> tuple[bool, str]:
    """Light probe = /models only. Inference probe only before check start."""
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            r = client.get(f"{LM_HOST}/v1/models")
            if r.status_code >= 400:
                return False, f"GET /models -> {r.status_code}"
            if not inference:
                return True, "models ok"
            t0 = time.time()
            r2 = client.post(
                f"{LM_HOST}/v1/chat/completions",
                json={
                    "model": "qwen/qwen3.5-9b",
                    "messages": [{"role": "user", "content": "ok"}],
                    "max_tokens": 2,
                },
            )
            dt = time.time() - t0
            if r2.status_code >= 400:
                try:
                    body = r2.json()
                    err_obj = body.get("error") if isinstance(body, dict) else None
                    err = err_obj.get("message", r2.text[:200]) if isinstance(err_obj, dict) else r2.text[:200]
                except Exception:
                    err = r2.text[:200]
                return False, f"inference {r2.status_code}: {err}"
            if dt > timeout_sec:
                return False, f"inference slow {dt:.0f}s"
            return True, f"ok {dt:.1f}s"
    except Exception as exc:
        return False, str(exc)


def load_vlm_if_needed() -> None:
    with httpx.Client(timeout=300.0) as client:
        models = client.get(f"{LM_HOST}/api/v1/models").json().get("models", [])
        for m in models:
            if m.get("key") == VLM_MODEL and m.get("loaded_instances"):
                print(f"VLM already loaded: {VLM_MODEL}", flush=True)
                return
        print(f"Loading VLM {VLM_MODEL}...", flush=True)
        r = client.post(
            f"{LM_HOST}/api/v1/models/load",
            json={"model": VLM_MODEL, "context_length": 8192, "flash_attention": True},
        )
        r.raise_for_status()
        print(f"VLM loaded in {r.json().get('load_time_seconds', '?')}s", flush=True)


def unload_lm_models() -> None:
    print("Unloading LM Studio models...", flush=True)
    try:
        with httpx.Client(timeout=60.0) as client:
            models = client.get(f"{LM_HOST}/api/v1/models").json().get("models", [])
            for m in models:
                for inst in m.get("loaded_instances") or []:
                    iid = inst.get("id")
                    if not iid:
                        continue
                    try:
                        client.post(
                            f"{LM_HOST}/api/v1/models/unload",
                            json={"instance_id": iid},
                        )
                        print(f"  unloaded {iid}", flush=True)
                    except Exception as exc:
                        print(f"  unload failed {iid}: {exc}", flush=True)
    except Exception as exc:
        print(f"Unload pass failed: {exc}", flush=True)


def mark_running_failed(filename: str, reason: str) -> None:
    reason_sql = reason.replace("'", "''")
    fname_sql = filename.replace("'", "''")
    sql = (
        "UPDATE eskd_check_runs SET status = 'failed', updated_at = NOW(), "
        "raw_result = COALESCE(raw_result, '{}'::jsonb) || "
        f"jsonb_build_object('status','failed','error','{reason_sql}') "
        f"WHERE status = 'running' AND original_filename = '{fname_sql}';"
    )
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "eskd-postgres",
        "psql",
        "-U",
        "eskd",
        "-d",
        "eskd_agent",
        "-c",
        sql,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        print(proc.stdout.strip() or proc.stderr.strip(), flush=True)
    except Exception as exc:
        print(f"DB cleanup failed: {exc}", flush=True)


def cancel_job(job_id: str | None) -> None:
    if not job_id:
        return
    try:
        httpx.post(CANCEL_URL, data={"job_id": job_id}, headers=HEADERS, timeout=15.0)
        print(f"Cancel sent for job {job_id}", flush=True)
    except Exception as exc:
        print(f"Cancel failed: {exc}", flush=True)


def restart_eskd_model() -> None:
    try:
        subprocess.run(
            ["docker", "compose", "restart", "eskd-model"],
            check=False,
            capture_output=True,
            text=True,
        )
        print("eskd-model restarted", flush=True)
    except Exception as exc:
        print(f"eskd-model restart failed: {exc}", flush=True)


def cleanup(state: CheckState, filename: str, reason: str) -> None:
    with state.lock:
        if state.failed:
            return
        state.failed = True
        state.fail_reason = reason
    print(f"\n!!! CLEANUP: {reason}", flush=True)
    cancel_job(state.job_id)
    mark_running_failed(filename, reason)
    unload_lm_models()
    restart_eskd_model()


def monitor_loop(state: CheckState, stop: threading.Event) -> None:
    lm_failures = 0
    while not stop.wait(LM_PROBE_INTERVAL_SEC):
        with state.lock:
            if state.done or state.failed:
                return
            stall = time.time() - state.last_event_at
            gost = state.last_gost_key
            g_idx = state.last_gost_index
        if stall > GOST_STALL_SEC:
            cleanup(
                state,
                PDF.name,
                f"stall {stall:.0f}s on GOST {gost} ({g_idx}) — LM Studio likely hung",
            )
            return
        ok, detail = lm_probe(inference=False, timeout_sec=10.0)
        if ok:
            lm_failures = 0
            print(f"[monitor] LM Studio OK ({detail})", flush=True)
        else:
            lm_failures += 1
            print(f"[monitor] LM Studio FAIL ({lm_failures}/{LM_PROBE_FAIL_LIMIT}): {detail}", flush=True)
            if lm_failures >= LM_PROBE_FAIL_LIMIT:
                cleanup(state, PDF.name, f"LM Studio down: {detail}")
                return


def stream_worker(state: CheckState, stop: threading.Event) -> None:
    t0 = time.time()
    try:
        with PDF.open("rb") as handle:
            files = {"files": (PDF.name, handle, "application/pdf")}
            timeout = httpx.Timeout(connect=30.0, read=None, write=300.0, pool=300.0)
            with httpx.stream(
                "POST", ESKD_URL, data=DATA, files=files, headers=HEADERS, timeout=timeout
            ) as resp:
                print(f"HTTP {resp.status_code}", flush=True)
                if resp.status_code >= 400:
                    cleanup(state, PDF.name, f"ESKD HTTP {resp.status_code}")
                    return
                buf = ""
                for chunk in resp.iter_text():
                    if state.failed:
                        break
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

                        with state.lock:
                            state.last_event_at = time.time()
                            if event == "start" and obj.get("job_id"):
                                state.job_id = str(obj["job_id"])
                            if event == "gost_start":
                                state.last_gost_key = str(obj.get("gost_key") or "")
                                state.last_gost_index = int(obj.get("index") or 0)
                            if event in {"complete", "error"}:
                                state.done = True

                        if event == "error":
                            cleanup(state, PDF.name, obj.get("message") or "stream error")
                            return
                        if event == "gost_start":
                            print(
                                f"[{elapsed:6.0f}s] GOST {obj.get('gost_key')} "
                                f"({obj.get('index')}/{obj.get('total')}) page={obj.get('page')}",
                                flush=True,
                            )
                        elif event == "gost_complete":
                            print(
                                f"[{elapsed:6.0f}s] done GOST {obj.get('gost_key')} "
                                f"percent={obj.get('percent')}",
                                flush=True,
                            )
                        elif event == "complete":
                            print(
                                f"[{elapsed:6.0f}s] DONE errors={obj.get('total_errors')} "
                                f"warnings={obj.get('total_warnings')} pages={obj.get('total_items')}",
                                flush=True,
                            )
                        elif event in {"start", "page_start", "preprocess", "item"}:
                            print(f"[{elapsed:6.0f}s] {event}: {obj}", flush=True)
    except Exception as exc:
        if not state.failed:
            cleanup(state, PDF.name, f"stream exception: {exc}")
    finally:
        stop.set()
        with state.lock:
            state.done = True


def main() -> int:
    if not PDF.is_file():
        print(f"File not found: {PDF}", file=sys.stderr)
        return 1

    print(f"File: {PDF.name} ({PDF.stat().st_size} bytes)", flush=True)
    ok, detail = lm_probe(inference=False, timeout_sec=10.0)
    print(f"LM Studio pre-check: {'OK' if ok else 'FAIL'} ({detail})", flush=True)
    if not ok:
        print("LM Studio not ready — aborting without starting check", flush=True)
        return 2

    try:
        load_vlm_if_needed()
    except Exception as exc:
        print(f"Failed to load VLM: {exc}", flush=True)
        return 3

    state = CheckState()
    stop = threading.Event()
    monitor = threading.Thread(target=monitor_loop, args=(state, stop), daemon=True)
    worker = threading.Thread(target=stream_worker, args=(state, stop), daemon=True)
    monitor.start()
    worker.start()
    worker.join()
    stop.set()
    monitor.join(timeout=5)

    if state.failed:
        print(f"Check aborted: {state.fail_reason}", flush=True)
        return 4
    print("Check finished successfully", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
