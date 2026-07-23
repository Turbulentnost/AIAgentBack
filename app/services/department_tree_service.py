from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Department, User
from app.schemas.department import DepartmentMemberRead, DepartmentTreeNode, DepartmentTreeResponse
from app.utils.department_utils import is_liquidated_department_name


class DepartmentTreeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_tree(
        self,
        *,
        active_departments_only: bool = True,
        active_users_only: bool = True,
    ) -> DepartmentTreeResponse:
        departments = await self._load_departments(active_departments_only)
        members = await self._load_members(active_users_only)

        members_by_department: dict[uuid.UUID, list[DepartmentMemberRead]] = defaultdict(list)
        unassigned: list[DepartmentMemberRead] = []
        for member in members:
            if member.department_id is None:
                unassigned.append(member)
            else:
                members_by_department[member.department_id].append(member)

        for dept_members in members_by_department.values():
            dept_members.sort(key=_member_sort_key)

        unassigned.sort(key=_member_sort_key)

        department_ids = {department.id for department in departments}
        children_map: dict[uuid.UUID | None, list[Department]] = defaultdict(list)
        for department in departments:
            parent_id = department.parent_id if department.parent_id in department_ids else None
            children_map[parent_id].append(department)

        for child_list in children_map.values():
            child_list.sort(key=lambda item: item.name.casefold())

        roots = [
            self._build_node(department, children_map, members_by_department)
            for department in children_map[None]
        ]

        return DepartmentTreeResponse(
            roots=roots,
            members=members,
            unassigned_members=unassigned,
            total_departments=len(departments),
            total_members=len(members),
        )

    async def _load_departments(self, active_only: bool) -> list[Department]:
        stmt = select(Department).order_by(Department.name)
        if active_only:
            stmt = stmt.where(Department.is_active.is_(True))
        result = await self.db.execute(stmt)
        departments = list(result.scalars().all())
        if active_only:
            departments = [
                department
                for department in departments
                if not is_liquidated_department_name(department.name)
            ]
        return departments

    async def _load_members(self, active_only: bool) -> list[DepartmentMemberRead]:
        stmt = select(User).where(User.deleted_at.is_(None)).order_by(User.full_name, User.email)
        if active_only:
            stmt = stmt.where(User.is_active.is_(True))
        result = await self.db.execute(stmt)
        users = list(result.scalars().all())
        members = [DepartmentMemberRead.model_validate(user) for user in users]
        members.sort(key=_member_sort_key)
        return members

    def _build_node(
        self,
        department: Department,
        children_map: dict[uuid.UUID | None, list[Department]],
        members_by_department: dict[uuid.UUID, list[DepartmentMemberRead]],
    ) -> DepartmentTreeNode:
        direct_members = members_by_department.get(department.id, [])
        child_nodes = [
            self._build_node(child, children_map, members_by_department)
            for child in children_map.get(department.id, [])
        ]
        total_member_count = len(direct_members) + sum(node.total_member_count for node in child_nodes)
        return DepartmentTreeNode(
            id=department.id,
            name=department.name,
            slug=department.slug,
            description=department.description,
            parent_id=department.parent_id,
            is_active=department.is_active,
            source_system=department.source_system,
            external_id=department.external_id,
            members=direct_members,
            member_count=len(direct_members),
            total_member_count=total_member_count,
            children=child_nodes,
        )


def _member_sort_key(member: DepartmentMemberRead) -> tuple[str, str]:
    label = (member.full_name or member.email or "").casefold()
    return (label, member.email.casefold())
