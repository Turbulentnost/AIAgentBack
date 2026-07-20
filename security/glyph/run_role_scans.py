"""Run Glyph AnalysisEngine on synthetic role MCP configs with tools attached.

ClaudeDesktopParser (used by `glyph scan` CLI) ignores tools[] in JSON.
This runner loads generated configs, attaches Tool models, and applies all
static Glyph rules — same engine as the CLI.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from glyph.engine.analyzer import AnalysisEngine
from glyph.models.config import (
    ConfigFormat,
    MCPConfig,
    MCPServer,
    Tool,
    TransportConfig,
    TransportType,
)
from glyph.models.finding import Severity
from glyph.reporter.human import HumanReporter
from glyph.reporter.json_report import JsonReporter
from glyph.cli import get_all_rules

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"
REPORTS = ROOT / "reports"

ROLE_PACKAGES = (
    "cfo_head_agent",
    "finance_director_agent",
    "executive_director_agent",
    "chief_accountant_agent",
    "accountant_agent",
    "legal_specialist_agent",
)


def load_config_with_tools(path: Path) -> MCPConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    servers: list[MCPServer] = []
    for name, server_cfg in data.get("mcpServers", {}).items():
        command = [server_cfg["command"]] if "command" in server_cfg else []
        args = list(server_cfg.get("args") or [])
        transport = TransportConfig(
            type=TransportType.STDIO,
            command=command,
            args=args,
        )
        tools = [
            Tool(
                name=t["name"],
                description=t.get("description") or "",
                server_name=name,
                schema=t.get("inputSchema") or {},
            )
            for t in server_cfg.get("tools") or []
        ]
        servers.append(
            MCPServer(
                name=name,
                transport=transport,
                tools=tools,
                env_vars=dict(server_cfg.get("env") or {}),
            )
        )
    return MCPConfig(
        file_path=path,
        format=ConfigFormat.CLAUDE_DESKTOP,
        servers=servers,
        raw_data=data,
    )


def exit_code_for(results) -> int:
    has_critical = any(
        f.severity == Severity.CRITICAL for r in results for f in r.findings
    )
    has_findings = any(r.findings for r in results)
    if has_critical:
        return 2
    if has_findings:
        return 1
    return 0


def scan_one(path: Path) -> tuple[object, int]:
    config = load_config_with_tools(path)
    engine = AnalysisEngine(get_all_rules())
    results = engine.analyze_all([config])
    return results, exit_code_for(results)


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict] = []
    worst_exit = 0

    for package in ROLE_PACKAGES:
        path = GENERATED / f"{package}.mcp.json"
        if not path.is_file():
            print(f"MISSING {path}", file=sys.stderr)
            summary_rows.append(
                {
                    "agent": package,
                    "status": "MISSING_CONFIG",
                    "exit_code": 2,
                    "findings": 0,
                }
            )
            worst_exit = max(worst_exit, 2)
            continue

        results, code = scan_one(path)
        worst_exit = max(worst_exit, code)

        json_path = REPORTS / f"{package}.glyph.json"
        human_path = REPORTS / f"{package}.glyph.txt"
        json_path.write_text(JsonReporter().generate(results), encoding="utf-8")
        human_path.write_text(HumanReporter().generate(results), encoding="utf-8")

        findings = [f for r in results for f in r.findings]
        by_sev: dict[str, int] = {}
        for f in findings:
            by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1

        summary_rows.append(
            {
                "agent": package,
                "status": "FAIL" if code else "PASS",
                "exit_code": code,
                "findings": len(findings),
                "by_severity": by_sev,
                "rules": sorted({f.rule_id for f in findings}),
                "titles": [
                    {"severity": f.severity.value, "rule": f.rule_id, "title": f.title, "location": f.location}
                    for f in findings
                ],
            }
        )
        print(f"{package}: exit={code} findings={len(findings)} -> {json_path.name}")

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "glyph AnalysisEngine (tools attached)",
        "note": "CLI `glyph scan` ignores tools[]; this runner attaches them from generated JSON.",
        "agents": summary_rows,
        "worst_exit_code": worst_exit,
    }
    (REPORTS / "roles_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return worst_exit


if __name__ == "__main__":
    raise SystemExit(main())
