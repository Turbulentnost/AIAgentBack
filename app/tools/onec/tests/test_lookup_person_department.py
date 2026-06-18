from app.tools.onec.lookup_person_department import person_key_for_responsible


def test_person_key_for_responsible_from_person_catalog() -> None:
    key = person_key_for_responsible(
        "person-1",
        users={},
        persons={"person-1": {"Ref_Key": "person-1"}},
    )
    assert key == "person-1"


def test_person_key_for_responsible_from_user_catalog() -> None:
    key = person_key_for_responsible(
        "user-1",
        users={"user-1": {"Ref_Key": "user-1", "ФизическоеЛицо_Key": "person-2"}},
        persons={},
    )
    assert key == "person-2"


def test_person_key_for_responsible_missing_when_user_has_no_person_link() -> None:
    key = person_key_for_responsible(
        "user-1",
        users={"user-1": {"Ref_Key": "user-1", "Description": "Иванов Иван Иванович"}},
        persons={},
    )
    assert key is None
