from __future__ import annotations

import uuid

from app.schemas.department import DepartmentMemberRead
from app.services.department_tree_service import DepartmentTreeService, _member_sort_key


def test_member_sort_key_uses_full_name() -> None:
    first = DepartmentMemberRead(
        id=uuid.uuid4(),
        email="b@example.com",
        full_name="Борисов",
    )
    second = DepartmentMemberRead(
        id=uuid.uuid4(),
        email="a@example.com",
        full_name="Антонов",
    )
    assert _member_sort_key(first) > _member_sort_key(second)


def test_build_node_counts_members_in_subtree() -> None:
    dept_id = uuid.uuid4()
    child_id = uuid.uuid4()
    user_id = uuid.uuid4()
    child_user_id = uuid.uuid4()

    members_by_department = {
        dept_id: [
            DepartmentMemberRead(id=user_id, email="root@example.com", full_name="Root User", department_id=dept_id),
        ],
        child_id: [
            DepartmentMemberRead(
                id=child_user_id,
                email="child@example.com",
                full_name="Child User",
                department_id=child_id,
            ),
        ],
    }

    class FakeDepartment:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    root = FakeDepartment(
        id=dept_id,
        name="Root",
        slug="root",
        description=None,
        parent_id=None,
        is_active=True,
        source_system=None,
        external_id=None,
    )
    child = FakeDepartment(
        id=child_id,
        name="Child",
        slug="child",
        description=None,
        parent_id=dept_id,
        is_active=True,
        source_system=None,
        external_id=None,
    )
    children_map = {None: [root], dept_id: [child]}

    service = DepartmentTreeService(db=None)  # type: ignore[arg-type]
    node = service._build_node(root, children_map, members_by_department)

    assert node.member_count == 1
    assert node.total_member_count == 2
    assert len(node.children) == 1
    assert node.children[0].member_count == 1
