"""Build synthetic Claude-style MCP configs from Contour4 role agent prompts.

Glyph static rules that inspect tool descriptions need Tool objects; the generated
JSON keeps tools[] for audit and for run_role_scans.py (CLI glyph scan alone
does not attach tools from JSON — ClaudeDesktopParser ignores that key).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

AGENTS_ROOT = Path(__file__).resolve().parents[2] / "app" / "agents"
OUT_DIR = Path(__file__).resolve().parent / "generated"

ROLE_PACKAGES = (
    "cfo_head_agent",
    "finance_director_agent",
    "executive_director_agent",
    "chief_accountant_agent",
    "accountant_agent",
    "legal_specialist_agent",
)


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_purpose(config_text: str) -> str:
    match = re.search(
        r"_AGENT_PURPOSE\s*=\s*\(\s*((?:\"[^\"]*\"\s*)+)\)",
        config_text,
        re.DOTALL,
    )
    if match:
        parts = re.findall(r"\"([^\"]*)\"", match.group(1))
        return "".join(parts).strip()
    match = re.search(r"_AGENT_PURPOSE\s*=\s*\"([^\"]*)\"", config_text)
    if match:
        return match.group(1).strip()
    return ""


def _system_prompt_body(system_md: str) -> str:
    """Prefer SYSTEM_PROMPT section; fall back to full file."""
    marker = "## SYSTEM_PROMPT"
    idx = system_md.find(marker)
    if idx < 0:
        return system_md.strip()
    body = system_md[idx + len(marker) :].lstrip()
    if body.startswith("(в LLM)"):
        body = body[len("(в LLM)") :].lstrip()
    # Trim trailing editor notes after examples if present
    return body.strip()


def build_config(package: str) -> dict:
    root = AGENTS_ROOT / package
    system_md = _read(root / "prompts" / "system.md")
    user_md = _read(root / "prompts" / "user.md")
    config_py = _read(root / "config.py")
    purpose = _extract_purpose(config_py)
    system_body = _system_prompt_body(system_md)

    return {
        "mcpServers": {
            package: {
                "command": "echo",
                "args": ["noop"],
                "env": {
                    "GLYPH_ROLE_BRIDGE": "1",
                    "AGENT_ID": package,
                },
                "tools": [
                    {
                        "name": "system_prompt",
                        "description": system_body or "(empty system.md)",
                    },
                    {
                        "name": "user_prompt_template",
                        "description": user_md.strip() or "(empty user.md)",
                    },
                    {
                        "name": "agent_purpose",
                        "description": purpose or f"Role agent package {package}",
                    },
                ],
            }
        }
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for package in ROLE_PACKAGES:
        cfg = build_config(package)
        out = OUT_DIR / f"{package}.mcp.json"
        out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tools = cfg["mcpServers"][package]["tools"]
        print(f"wrote {out.name} tools={len(tools)} sys_chars={len(tools[0]['description'])}")


if __name__ == "__main__":
    main()
