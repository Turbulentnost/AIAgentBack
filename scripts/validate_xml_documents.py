"""Проверка XML document в raw_payload_json писем (ТЗ §12)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_pochta.routing.xml_builder import validate_xml_document
from agent_pochta.routing.xml_parser import parse_document_xml


def _load_xml_samples(path: Path) -> list[tuple[str, str]]:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict) and "entries" in data:
            rows = data["entries"]
        else:
            rows = [data]
        result: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            xml = row.get("xml_document") or row.get("xml")
            if isinstance(xml, str) and xml.strip():
                label = str(row.get("message_id") or row.get("id") or len(result))
                result.append((label, xml))
        return result

    return [(path.name, path.read_text(encoding="utf-8"))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate XML documents against TZ §12")
    parser.add_argument(
        "paths",
        nargs="*",
        default=["data"],
        help="Files (.xml/.json) or directories with JSON payloads",
    )
    args = parser.parse_args(argv)

    samples: list[tuple[str, str]] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            for file in sorted(path.rglob("*.json")):
                samples.extend(_load_xml_samples(file))
        elif path.is_file():
            samples.extend(_load_xml_samples(path))

    if not samples:
        print("No XML samples found.")
        return 1

    failed = 0
    for label, xml in samples:
        ok = validate_xml_document(xml)
        parsed = parse_document_xml(xml)
        service_code = parsed["services"][0]["name"] if parsed and parsed.get("services") else ""
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {label}: service={service_code}")
        if not ok:
            failed += 1
            if parsed is None:
                print("  parse error")
            elif parsed.get("services"):
                for svc in parsed["services"]:
                    code = svc.get("name", "")
                    if not code.startswith("00-"):
                        print(f"  invalid service name: {code!r}")

    print(f"Checked {len(samples)} document(s), failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
