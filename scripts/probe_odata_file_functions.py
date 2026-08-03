"""Sample FunctionImport names from OData metadata."""
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
text = httpx.get(
    s.odata_base_url.rstrip("/") + "/$metadata",
    auth=(s.odata_username, s.odata_password),
    timeout=120,
).text
names = re.findall('FunctionImport Name="([^"]+)"', text)
print("total", len(names))
# print ones containing latin file-ish or cyrillic fragments via unicode escape check
for n in names:
    low = n.casefold()
    if "file" in low or "attach" in low or "binary" in low or "хран" in low or "файл" in low:
        print(n)
print("--- first 30 ---")
for n in names[:30]:
    print(n)
