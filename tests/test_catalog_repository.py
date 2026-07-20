"""Тесты staging-репозитория справочников 1С."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent_pochta.db.catalog_repository import CatalogRepository
from agent_pochta.db.models import Base, ErpContractorRow
from agent_pochta.schemas import Contractor, Department


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
def catalog_repo(session):
    return CatalogRepository(session)


def test_upsert_contractor_and_department(catalog_repo, session):
    run_id = catalog_repo.begin_sync_run("json", notes="test")
    catalog_repo.upsert_contractors(
        [
            Contractor(
                contractor_id="C-TEST",
                name="Тест",
                emails=["test@example.com"],
                department_codes=["SALES"],
                contractor_type="клиент",
            )
        ],
        source="json",
        sync_run_id=run_id,
    )
    catalog_repo.upsert_departments(
        [
            Department(
                department_id="SALES",
                department_name="Продажи",
                head_name="Иванов",
                responsibility="Заказы",
                keywords=["заказ"],
            )
        ],
        source="json",
        sync_run_id=run_id,
    )
    catalog_repo.finish_sync_run(run_id, status="done", contractors_count=1, departments_count=1)
    session.commit()

    contractors = catalog_repo.load_active_contractors(source="json")
    departments = catalog_repo.load_active_departments(source="json")
    assert len(contractors) == 1
    assert contractors[0].emails == ["test@example.com"]
    assert len(departments) == 1
    assert departments[0].keywords == ["заказ"]

    row = session.query(ErpContractorRow).filter_by(contractor_id="C-TEST").one()
    assert json.loads(row.emails_json) == ["test@example.com"]


def test_upsert_manual_contractor_creates_hitl_draft(catalog_repo, session):
    row = catalog_repo.upsert_manual_contractor(
        contractor_id="email:vendor@example.com",
        name="ООО «Ромашка»",
        email="vendor@example.com",
        department_code="00-000002",
    )
    session.commit()

    assert row.source == "hitl"
    assert row.name == "ООО «Ромашка»"
    assert row.needs_review is True
    assert json.loads(row.emails_json) == ["vendor@example.com"]
    assert json.loads(row.department_codes_json) == ["00-000002"]
