from app.agents.omto_support_manager_agent.checks import evaluate_mandatory_fields


def test_clean_fields_no_findings():
    findings = evaluate_mandatory_fields(
        {
            "cfo": "ЦФО-01",
            "article": "СТ-100",
            "project": "PRJ-ALPHA",
            "date": "17.07.2026",
            "nomenclature": "NOM-КР-12",
            "quantity": 10,
        },
        "REQ-1",
    )
    assert findings == []


def test_missing_cfo_has_source_and_rule():
    findings = evaluate_mandatory_fields(
        {
            "cfo": "",
            "article": "СТ-100",
            "project": "PRJ-ALPHA",
            "date": "17.07.2026",
            "nomenclature": "NOM-КР-12",
            "quantity": 5,
        },
        "REQ-2",
    )
    assert any(f.field == "cfo" for f in findings)
    for f in findings:
        assert f.rule_id
        assert f.source_ref.startswith("payload.fields.")


def test_bad_quantity():
    findings = evaluate_mandatory_fields(
        {
            "cfo": "ЦФО-02",
            "article": "СТ-210",
            "project": "PRJ-BETA",
            "date": "2026-07-18",
            "nomenclature": "NOM-КАБ-5",
            "quantity": 0,
        },
        "REQ-3",
    )
    assert any(f.field == "quantity" and f.rule_id == "DQ.MANDATORY.QUANTITY" for f in findings)
