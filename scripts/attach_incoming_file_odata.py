"""CLI: прикрепить файл к существующему Document_ТД_ВходящаяКорреспонденция через OData.

Пример:
  python scripts/attach_incoming_file_odata.py \\
    --document-ref aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee \\
    --file C:\\temp\\scan.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileError,
    AttachedFileInput,
    attach_file_to_incoming_document,
    load_attached_file_field_map,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Attach file to incoming correspondence document")
    parser.add_argument("--document-ref", required=True, help="Ref_Key документа-владельца (GUID)")
    parser.add_argument("--file", required=True, help="Путь к файлу на диске")
    parser.add_argument(
        "--field-map",
        default="",
        help="JSON-маппинг полей (по умолчанию data/odata_attached_file_field_map.json)",
    )
    parser.add_argument(
        "--skip-owner-check",
        action="store_true",
        help="Не проверять существование документа GET-запросом",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.odata_base_url:
        print("ODATA_BASE_URL не задан")
        sys.exit(1)

    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"Файл не найден: {file_path}")
        sys.exit(1)

    content = file_path.read_bytes()
    field_map = load_attached_file_field_map(args.field_map or None)
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=settings.odata_timeout_sec,
    )

    try:
        result = attach_file_to_incoming_document(
            client,
            document_ref_key=args.document_ref,
            file_input=AttachedFileInput(filename=file_path.name, content=content),
            field_map=field_map,
            verify_owner_exists=not args.skip_owner_check,
        )
    except AttachedFileError as exc:
        print(f"Ошибка: {exc}")
        sys.exit(2)
    except Exception as exc:
        print(f"Сбой OData: {exc}")
        sys.exit(3)

    print("OK")
    print(f"  entity:   {result.entity}")
    print(f"  ref_key:  {result.ref_key}")
    print(f"  filename: {result.filename}.{result.extension}")
    print(f"  size:     {result.size_bytes} bytes")


if __name__ == "__main__":
    main()
