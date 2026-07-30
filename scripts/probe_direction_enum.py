"""Извлечь значения enum ТД_НаправленияСлужебныхЗаписок из $metadata OData."""

from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from agent_pochta.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    auth = (settings.odata_username, settings.odata_password)
    url = settings.odata_base_url.rstrip("/") + "/$metadata"
    response = httpx.get(url, auth=auth, timeout=120)
    response.raise_for_status()
    text = response.text
    for enum in ("ТД_НаправленияСлужебныхЗаписок", "ТД_ПлательщикНаправление"):
        match = re.search(
            rf'EnumType Name="{enum}"[^>]*>(.*?)</EnumType>',
            text,
            re.DOTALL,
        )
        if match:
            members = re.findall(r'Member Name="([^"]+)"', match.group(1))
            print(f"Enum {enum} ({len(members)}):")
            for member in members:
                print(f"  {member}")
        else:
            print(f"Enum {enum}: NOT FOUND")

    for prop_name in ("Партнер", "ТемаСлужебнойЗаписки", "Направление", "ПлательщикНаправление"):
        block = re.search(
            rf'Property Name="{prop_name}"[\s\S]{{0,250}}',
            text,
        )
        print(f"{prop_name}:", (block.group(0) if block else "not found").replace("\n", " ")[:240])


if __name__ == "__main__":
    main()
