"""Extract full BSL from agent transcript."""
from __future__ import annotations

import json
from pathlib import Path

TRANSCRIPT = Path(
    r"C:\Users\mdj\.cursor\projects\c-Users-mdj-Desktop-2-agent-pochta"
    r"\agent-transcripts\0024d0d2-e2e1-469b-8d75-18367df07e7a"
    r"\0024d0d2-e2e1-469b-8d75-18367df07e7a.jsonl"
)
OUT = Path(__file__).resolve().parents[1] / "data" / "temp" / "user_bsl_full.txt"
NEEDLE = "ЗаписатьДвоичныеДанныеВХранилищеБСП"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(TRANSCRIPT.read_text(encoding="utf-8").splitlines(), 1):
        if NEEDLE not in line or '"role":"user"' not in line:
            continue
        text = json.loads(line)["message"]["content"][0]["text"]
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT} chars={len(text)} line={i}")
        return
    raise SystemExit("not found")


if __name__ == "__main__":
    main()
