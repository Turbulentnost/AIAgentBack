"""Сводная статистика обработки писем и human-in-the-loop за период.

Источники:
  - PostgreSQL email_messages (received_at / processed_at — naive UTC)
  - PostgreSQL change_events (журнал изменений оператора и связанных правок)

Входные даты --from / --to интерпретируются в Europe/Moscow (UTC+3).

Пример:
  python scripts/generate_statistics.py \\
    --from "2026-07-08 08:35:00" \\
    --to "2026-07-08 16:30:00" \\
    --output data/stats/статистика.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import PROJECT_ROOT, get_settings  # noqa: E402
from agent_pochta.db.message_filters import MSK  # noqa: E402
from agent_pochta.stats.export import build_statistics_report, export_statistics_files  # noqa: E402


def _parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    raw = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(raw, fmt)
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Статистика писем и human-in-the-loop за период")
    parser.add_argument(
        "--from",
        dest="from_dt",
        default=settings.stats_start_time,
        help='Начало (MSK), напр. "2026-07-08 08:35:00"',
    )
    parser.add_argument(
        "--to",
        dest="to_dt",
        default=None,
        help='Конец (MSK), по умолчанию — сейчас',
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Путь к JSON (по умолчанию: STATS_EXPORT_DIR/статистика.json)",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Путь к Markdown (по умолчанию: рядом с JSON)",
    )
    parser.add_argument(
        "--timezone",
        default=settings.stats_timezone,
        help="Часовой пояс для --from/--to (по умолчанию Europe/Moscow)",
    )
    args = parser.parse_args()

    tz = ZoneInfo(args.timezone)
    from_local = _parse_local_datetime(args.from_dt, tz)
    to_local = datetime.now(tz) if args.to_dt is None else _parse_local_datetime(args.to_dt, tz)

    if args.output is None and args.markdown is None:
        result = export_statistics_files(from_local=from_local, to_local=to_local, tz=tz)
        print(f"JSON: {result['written']['json']}")
        print(f"Markdown: {result['written']['markdown']}")
        if "repo_json" in result["written"]:
            print(f"Repo JSON: {result['written']['repo_json']}")
            print(f"Repo Markdown: {result['written']['repo_markdown']}")
        print(
            f"Писем (received): {result['total_received']}, "
            f"изменений: {result['total_changes']}"
        )
        return

    report = build_statistics_report(from_local=from_local, to_local=to_local, tz=tz)
    export_dir = Path(settings.stats_export_dir)
    if not export_dir.is_absolute():
        export_dir = (PROJECT_ROOT / export_dir).resolve()

    output_path = args.output or (export_dir / "статистика.json")
    if not output_path.is_absolute():
        output_path = (PROJECT_ROOT / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    md_path = args.markdown or output_path.with_suffix(".md")
    if not md_path.is_absolute():
        md_path = (PROJECT_ROOT / md_path).resolve()

    from agent_pochta.stats.export import _build_markdown

    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_build_markdown(report), encoding="utf-8")

    print(f"JSON: {output_path}")
    print(f"Markdown: {md_path}")
    print(
        f"Писем (received): {report['emails']['total_received_in_window']}, "
        f"изменений: {report['human_changes']['counts']['total_field_changes']}"
    )


if __name__ == "__main__":
    main()
