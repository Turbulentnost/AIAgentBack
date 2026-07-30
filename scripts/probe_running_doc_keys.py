"""List all fields on a doc with BP started=true."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_REF = "5185c652-9aeb-11f0-9710-6cb3111380bc"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    doc = httpx.get(
        f"{base}{quote('Document_ТД_ВходящаяКорреспонденция')}(guid'{DOC_REF}')?$format=json",
        auth=auth,
        timeout=120,
    ).raise_for_status().json()
    keys = sorted(doc)
    print(json.dumps({"keys": keys, "non_empty": {k: doc[k] for k in keys if doc.get(k) not in (None, "", [], "00000000-0000-0000-0000-000000000000")}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
