"""Find OData FunctionImport / Action related to files."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from agent_pochta.config import get_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

s = get_settings()
base = s.odata_base_url.rstrip("/") + "/"
text = httpx.get(base + "$metadata", auth=(s.odata_username, s.odata_password), timeout=120).text
for kind in ("FunctionImport", "Action", "Function"):
    for m in re.finditer(rf'{kind} Name="([^"]+)"', text):
        name = m.group(1)
        if any(x.lower() in name.lower() for x in ("file", "файл", "хран", "attach", "odata")):
            print(kind, name)
print("--- sample FunctionImport count", len(re.findall(r'FunctionImport Name="', text)))
print("--- sample Action count", len(re.findall(r'Action Name="', text)))
