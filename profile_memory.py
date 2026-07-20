"""Memory profile for Contour4 CfoHeadAgent hot path (no uvicorn / no LLM network).

Usage (from AIAgentBack):
  uv run python profile_memory.py

Optional line-by-line report (memory_profiler):
  uv run python -m memory_profiler profile_memory.py
"""
from __future__ import annotations

import asyncio
import gc
import sys
import tracemalloc
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Ensure package root on sys.path when run as a script
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CYCLES = 10

_MOCK_LLM = {
    "recommendation": "Сумма в лимите ДС, предложить утвердить.",
    "rationale": "amount<=ds_limit",
    "suggested_action": "approve",
    "needs_hitl": True,
    "norm_refs": ["СТО-28-020 §6.2"],
}


def _base_payload() -> dict:
    return {
        "task_id": "task-mem-1",
        "case_id": "case-mem-1",
        "correlation_id": "contour4:mem:case-1",
        "idempotency_key": "cfo:case-mem-1:v1",
        "case_context": {
            "payment_request_id": "PR-MEM-1",
            "cfo_code": "CFO-01",
            "amount": "1000.00",
            "ds_limit": "5000.00",
            "payment_mode": "prepay",
        },
    }


def _print_tracemalloc_diff(snapshot_before: tracemalloc.Snapshot) -> None:
    snapshot_after = tracemalloc.take_snapshot()
    stats = snapshot_after.compare_to(snapshot_before, "lineno")
    print("\n=== tracemalloc TOP-10 (compare_to lineno) ===")
    for i, stat in enumerate(stats[:10], start=1):
        print(f"{i:2d}. {stat}")


def _print_objgraph_growth() -> None:
    print("\n=== objgraph.show_growth() ===")
    try:
        import objgraph
    except ImportError:
        print("objgraph not installed — skip (uv add --dev objgraph)")
        return
    # Baseline after imports / first growth call primes internal counters
    objgraph.show_growth(limit=20)
    print("(second call after workload — deltas vs previous show_growth)")


async def _run_cycles() -> None:
    from app.agents.cfo_head_agent.service import CfoHeadAgent

    agent = CfoHeadAgent()
    payload = _base_payload()
    with patch(
        "app.agents.cfo_head_agent.service.recommend_with_llm",
        new_callable=AsyncMock,
        return_value=_MOCK_LLM,
    ):
        for i in range(CYCLES):
            result = await agent.run(payload)
            print(
                f"  cycle {i + 1}/{CYCLES}: status={result.role_status} "
                f"action={result.suggested_action}"
            )


def _workload_sync() -> None:
    """Sync entry for optional memory_profiler @profile."""
    asyncio.run(_run_cycles())


try:
    from memory_profiler import profile as _mp_profile

    @_mp_profile
    def profiled_workload() -> None:
        _workload_sync()

except ImportError:

    def profiled_workload() -> None:
        print("memory_profiler not installed — running without @profile")
        _workload_sync()


def main() -> int:
    print(f"Contour4 memory profile: CfoHeadAgent.run x {CYCLES} (LLM mocked)")
    print(f"Python {sys.version}")

    # Warm import / registry outside measured window
    from app.agents.cfo_head_agent.service import CfoHeadAgent  # noqa: F401

    try:
        import objgraph

        print("\n=== objgraph baseline (prime) ===")
        objgraph.show_growth(limit=5)
    except ImportError:
        objgraph = None  # type: ignore[assignment]

    gc.collect()
    tracemalloc.start(25)
    snap_before = tracemalloc.take_snapshot()

    profiled_workload()

    gc.collect()
    _print_tracemalloc_diff(snap_before)

    if objgraph is not None:
        _print_objgraph_growth()
    else:
        print("\n=== objgraph.show_growth() ===\nobjgraph not installed — skip")

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        f"\n=== tracemalloc totals ===\n"
        f"current={current / 1024:.1f} KiB  peak={peak / 1024:.1f} KiB"
    )
    print(
        "\nNote: prompts use @lru_cache(maxsize=1) — bounded, not a leak.\n"
        "Look for unbounded caches, open httpx/AsyncSession, growing globals."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
