"""Seed fixed ND control templates."""
from __future__ import annotations

import asyncio
import sys

from app.db.session import AsyncSessionLocal
from app.models.enums import NdTemplateType
from app.services.nd_control_template_service import NdControlTemplateService
from app.utils.nd_template_classification import ND_TEMPLATE_TYPE_LABELS


async def seed_nd_control_templates() -> int:
    created_or_updated = 0
    async with AsyncSessionLocal() as db:
        service = NdControlTemplateService(db)
        for index, template_type in enumerate(ND_TEMPLATE_TYPE_LABELS, start=1):
            await service.create_template(
                template_type=template_type,
                name=ND_TEMPLATE_TYPE_LABELS[template_type],
                sort_order=index * 10,
            )
            created_or_updated += 1
        await db.commit()
    return created_or_updated


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    count = asyncio.run(seed_nd_control_templates())
    print(f"Seeded ND control templates: {count}")
    if count != len(NdTemplateType):
        print(
            f"Warning: seeded {count} templates, enum has {len(NdTemplateType)} values",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
