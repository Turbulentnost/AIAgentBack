"""Тесты справочника departments в PostgreSQL."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent_pochta.db.department_repository import DepartmentRepository
from agent_pochta.db.models import Base, DepartmentRow
from agent_pochta.schemas import DepartmentRecord
from agent_pochta.services.routing_departments import (
    build_department_records_for_db,
    directions_by_code_from_rules,
    load_routing_rules,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def department_repo(session):
    return DepartmentRepository(session)


def test_directions_by_code_from_rules():
    rules = load_routing_rules()
    directions = directions_by_code_from_rules(rules)
    assert directions["00-000002"] == "ПР"
    assert directions["00-000076"] == "ПР"
    assert directions["00-000065"] == "ПР"


def test_build_department_records_for_db():
    records = build_department_records_for_db()
    codes = {record.code for record in records}
    assert len(records) == 131
    assert "00-999999" not in codes
    assert "00-000163" in codes

    buh = next(record for record in records if record.code == "00-000002")
    assert buh.name == "Бухгалтерия"
    assert buh.direction == "ПР"
    assert buh.email == "almaz_glavbuh@turbo-don.ru"
    assert "almaz_glavbuh@turbo-don.ru" in buh.metadata.get("emails", [])
    assert buh.is_active is True


def test_department_repository_upsert_and_list(department_repo, session):
    department_repo.upsert_many(
        [
            DepartmentRecord(
                code="00-000002",
                name="Бухгалтерия",
                direction="ПР",
                email="npo_buh@turbo-don.ru",
                metadata={"head_name": "Иванов"},
            ),
            DepartmentRecord(
                code="00-000076",
                name="ОРКК",
                direction="КС",
                is_active=True,
            ),
        ]
    )
    session.commit()

    assert department_repo.count_active() == 2
    record = department_repo.get_by_code("00-000002")
    assert record is not None
    assert record.direction == "ПР"
    assert record.metadata["head_name"] == "Иванов"

    ui = department_repo.list_for_ui()
    assert ui == [
        {"id": "00-000002", "name": "Бухгалтерия"},
        {"id": "00-000076", "name": "ОРКК"},
    ]

    row = session.query(DepartmentRow).filter_by(code="00-000002").one()
    assert json.loads(row.metadata_json)["head_name"] == "Иванов"

    assert department_repo.deactivate("00-000076") is True
    session.commit()
    assert department_repo.count_active() == 1
