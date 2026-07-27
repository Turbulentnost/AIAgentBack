from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.tools.onec.create_meeting_topic import (
    MEETING_TYPES,
    merge_topic_participant_fios,
    require_topic_participant_fios,
)


class MeetingTopicSimilarityBreakdownRead(BaseModel):
    topic: float | None = None
    participants: float | None = None
    details: float | None = None


class MeetingTopicParticipantRead(BaseModel):
    participant_ref_key: str | None = None
    fio: str | None = None


class MeetingTopicSummaryRead(BaseModel):
    ref_key: str | None = None
    code: str | None = None
    description: str
    details: str | None = None
    meeting_type: str | None = None
    manager: str | None = None
    reviewer: str | None = None
    department: str | None = None
    room: str | None = None
    project: str | None = None
    committee: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    closed_date: str | None = None
    is_active: bool = True
    similarity_score: float | None = None
    similarity_method: str | None = None
    similarity_breakdown: MeetingTopicSimilarityBreakdownRead | None = None
    participants: list[MeetingTopicParticipantRead] = Field(default_factory=list)


class MeetingTopicCheckSimilarRequest(BaseModel):
    description: str = Field(description="Наименование темы совещания")
    manager_fio: str = Field(description="ФИО руководителя")
    meeting_type: str = Field(
        default="Отчетное",
        description=f"Вид совещания: {', '.join(MEETING_TYPES)}",
    )
    topic_details: str | None = Field(
        default=None,
        description="Описание темы (поле Описание в 1С)",
    )
    participant_fios: list[str] = Field(
        default_factory=list,
        description="Участники темы по ФИО (инициатор и руководитель добавляются автоматически)",
    )
    initiator_fio: str | None = Field(
        default=None,
        description="ФИО инициатора СЗ — всегда включается в участников темы",
    )


class MeetingTopicCheckSimilarRead(BaseModel):
    similar_found: bool
    requires_user_decision: bool
    similar_topic: MeetingTopicSummaryRead | None = None
    similarity_score: float | None = None
    similarity_method: str | None = None
    similarity_breakdown: MeetingTopicSimilarityBreakdownRead | None = None
    missing_participants: list[MeetingTopicParticipantRead] = Field(
        default_factory=list,
        description=(
            "Участники из СЗ, которых ещё нет в похожей теме 1С (по ФИО). "
            "При decision=use_existing они будут добавлены в тему, если найдены в 1С."
        ),
    )
    unresolved_participants: list[MeetingTopicParticipantRead] = Field(
        default_factory=list,
        description=(
            "Участники из СЗ, которых нет в теме и которых не удалось найти в 1С — "
            "добавить автоматически нельзя."
        ),
    )
    required_fields: list[str] = Field(default_factory=list)
    message: str


class MeetingTopicResolveRequest(BaseModel):
    decision: Literal["use_existing", "create_new"]
    existing_topic_ref_key: str | None = Field(
        default=None,
        description="Ref_Key существующей темы при decision=use_existing",
    )
    description: str | None = None
    manager_fio: str | None = None
    meeting_type: str | None = Field(default="Отчетное")
    reviewer_fio: str | None = None
    closed_date: str | None = None
    closed_end_of_year: bool = False
    department_key: str | None = None
    room_key: str | None = None
    project_key: str | None = None
    committee_key: str | None = None
    organization_key: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    is_management_circle_topic: bool | None = None
    topic_details: str | None = None
    participant_fios: list[str] = Field(default_factory=list)
    initiator_fio: str | None = None
    dry_run: bool = False

    @model_validator(mode="after")
    def validate_decision_payload(self) -> MeetingTopicResolveRequest:
        if self.decision == "use_existing":
            if not (self.existing_topic_ref_key or "").strip():
                raise ValueError(
                    "Для decision=use_existing нужен existing_topic_ref_key"
                )
            return self

        missing: list[str] = []
        if not (self.description or "").strip():
            missing.append("description")
        if not (self.manager_fio or "").strip():
            missing.append("manager_fio")
        if not (self.meeting_type or "").strip():
            missing.append("meeting_type")
        merged_participants = merge_topic_participant_fios(
            self.participant_fios,
            manager_fio=self.manager_fio,
            initiator_fio=self.initiator_fio,
        )
        if not merged_participants:
            missing.append("participant_fios")
        if missing:
            raise ValueError(
                "Для decision=create_new заполните поля: "
                + ", ".join(missing)
            )
        normalized_type = (self.meeting_type or "").strip()
        if normalized_type not in MEETING_TYPES:
            raise ValueError(
                f"meeting_type должен быть одним из: {', '.join(MEETING_TYPES)}"
            )
        return self


class MeetingTopicResolveRead(BaseModel):
    decision: Literal["use_existing", "create_new"]
    used_existing: bool
    created: bool
    dry_run: bool = False
    topic: MeetingTopicSummaryRead
    participants_count: int = 0
    added_participants: list[MeetingTopicParticipantRead] = Field(
        default_factory=list,
        description="Участники из СЗ, добавленные в существующую тему 1С",
    )
    message: str


class MeetingTopicValidationRead(BaseModel):
    valid: bool
    topic: MeetingTopicSummaryRead | None = None
    reason: str | None = None
