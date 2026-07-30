"""Балльная модель уверенности отдела: жёсткие vs адаптивные сигналы."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.routing.organizations import LEADERSHIP_DEPARTMENT_CODES

# Коды приоритетных отделов (пороги accept).
CHAIRMAN_DEPARTMENT_CODE = "00-000001"
OD_DEPARTMENT_CODE = "00-000152"
VED_DEPARTMENT_CODE = "00-000015"

# Баллы по плану.
POINTS_EXACT_MAILBOX = 55
POINTS_HARD_CASCADE = 40
POINTS_HARD_LEADERSHIP_CASCADE = 85
POINTS_HARD_FOREIGN_CASCADE = 75
POINTS_HARD_PRODUCT_CASCADE = 70
POINTS_LEARNED_HARD = 50
POINTS_CONFIRM_FACT = 15
MAX_CONFIRM_POINTS = 30
POINTS_ADAPTIVE_CLUSTER = 8
MAX_ADAPTIVE_POINTS = 40
MAX_LLM_POINTS = 25
PENALTY_CONFLICT = 30
PENALTY_COMMERCIAL_VS_VED = 30

# Пороги уровней.
LEVEL_CRITICAL = 98
LEVEL_HIGH = 95
LEVEL_MEDIUM = 70

# Accept gates.
GATE_CHAIRMAN = 98
GATE_OD = 95
GATE_VED = 90
GATE_DEFAULT = 70

# Сколько adaptive-кластеров нужно без hard для leadership.
ADAPTIVE_ONLY_CHAIRMAN = 4
ADAPTIVE_ONLY_OD = 3

LEADERSHIP_HARD_SOURCES = frozenset(
    {
        "institution_chairman",
        "institution_operational_director",
        "info_strict",
        "det_chairman",
    }
)
# Отдельные hard-источники руководства/ответов — не leadership floor, но сильный cascade.
STRONG_HARD_SOURCES = frozenset(
    {
        "gazprom_np_reply",
        "ud_transfer",
    }
)
FOREIGN_HARD_SOURCES = frozenset({"det_foreign_domain"})
EXACT_MAILBOX_SOURCES = frozenset({"exact_email"})
LEARNED_HARD_SOURCES = frozenset({"human_correction"})
EMAIL_KEYWORD_SOURCES = frozenset({"email_keyword", "info_strict_unclear"})
PRODUCT_HARD_PREFIXES = ("det_",)
COMMERCIAL_CONFLICT_SOURCES = frozenset(
    {
        "det_sales_gazprom",
        "det_sales_orkk",
        "det_sales_dealer",
        "det_sales_industrial",
        "sales_gazprom",
        "sales_orkk",
        "sales_odp",
    }
)


class SignalClass(StrEnum):
    HARD = "hard"
    ADAPTIVE = "adaptive"
    PENALTY = "penalty"


@dataclass(frozen=True)
class EvidenceSignal:
    kind: SignalClass
    key: str
    points: int
    label: str = ""


@dataclass
class EvidenceResult:
    score: int
    level: ConfidenceLevel
    hard_count: int = 0
    adaptive_count: int = 0
    hard_foreign: bool = False
    signals: list[EvidenceSignal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def confidence_pct(self) -> int:
        return self.score


def score_to_level(score: int) -> ConfidenceLevel:
    if score >= LEVEL_CRITICAL:
        return ConfidenceLevel.CRITICAL
    if score >= LEVEL_HIGH:
        return ConfidenceLevel.HIGH
    if score >= LEVEL_MEDIUM:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _clamp(score: int) -> int:
    return max(0, min(100, score))


def accumulate_confidence(
    signals: list[EvidenceSignal],
    *,
    llm_confidence: float | None = None,
) -> EvidenceResult:
    """Суммирует сигналы с потолками; LLM — только при hard≥1 или adaptive≥3."""
    hard = [s for s in signals if s.kind == SignalClass.HARD]
    adaptive = [s for s in signals if s.kind == SignalClass.ADAPTIVE]
    penalties = [s for s in signals if s.kind == SignalClass.PENALTY]

    score = 0
    notes: list[str] = []
    used: list[EvidenceSignal] = []

    # Exact mailbox / learned — без потолка на число, но обычно один.
    for s in hard:
        if s.key in {"exact_mailbox", "learned_hard"}:
            score += s.points
            used.append(s)

    # Один сильнейший cascade.
    cascades = [s for s in hard if s.key.startswith("cascade_")]
    if cascades:
        best = max(cascades, key=lambda s: s.points)
        score += best.points
        used.append(best)
        notes.append(f"hard_cascade={best.key}:{best.points}")

    # Confirm facts — потолок.
    confirms = [s for s in hard if s.key.startswith("confirm_")]
    confirm_pts = 0
    for s in confirms:
        if confirm_pts >= MAX_CONFIRM_POINTS:
            break
        add = min(s.points, MAX_CONFIRM_POINTS - confirm_pts)
        confirm_pts += add
        score += add
        used.append(EvidenceSignal(SignalClass.HARD, s.key, add, s.label))
    if confirm_pts:
        notes.append(f"confirm={confirm_pts}")

    # Прочие hard (например hard_foreign flag point already in cascade).
    other_hard = [
        s
        for s in hard
        if s.key not in {"exact_mailbox", "learned_hard"}
        and not s.key.startswith("cascade_")
        and not s.key.startswith("confirm_")
    ]
    for s in other_hard:
        score += s.points
        used.append(s)

    adaptive_pts = 0
    adaptive_used = 0
    for s in adaptive:
        if adaptive_pts >= MAX_ADAPTIVE_POINTS:
            break
        add = min(s.points, MAX_ADAPTIVE_POINTS - adaptive_pts)
        adaptive_pts += add
        adaptive_used += 1
        score += add
        used.append(EvidenceSignal(SignalClass.ADAPTIVE, s.key, add, s.label))
    if adaptive_pts:
        notes.append(f"adaptive={adaptive_pts}/{adaptive_used}")

    hard_count = len({s.key for s in hard if s.points > 0 or s.key.startswith("cascade_")})
    # Более честный hard_count: число независимых hard-ключей (mailbox, cascade, learned, confirms grouped).
    hard_units = 0
    if any(s.key == "exact_mailbox" for s in hard):
        hard_units += 1
    if any(s.key == "learned_hard" for s in hard):
        hard_units += 1
    if cascades:
        hard_units += 1
    if confirms:
        hard_units += 1
    hard_units += len(other_hard)
    hard_count = hard_units

    adaptive_count = adaptive_used

    if llm_confidence is not None and llm_confidence > 0:
        if hard_count >= 1 or adaptive_count >= 3:
            llm_pts = min(MAX_LLM_POINTS, max(0, round(float(llm_confidence) * 25)))
            if llm_pts:
                score += llm_pts
                used.append(
                    EvidenceSignal(
                        SignalClass.ADAPTIVE,
                        "llm",
                        llm_pts,
                        f"llm={llm_confidence:.2f}",
                    )
                )
                notes.append(f"llm=+{llm_pts}")
        else:
            notes.append("llm_skipped_no_evidence")

    for s in penalties:
        score -= abs(s.points)
        used.append(s)
        notes.append(f"penalty={s.key}:-{abs(s.points)}")

    hard_foreign = any(
        s.key in {"cascade_foreign", "hard_foreign", "confirm_foreign_domain"} for s in hard
    ) or any(s.key == "cascade_foreign" for s in cascades)

    score = _clamp(score)
    return EvidenceResult(
        score=score,
        level=score_to_level(score),
        hard_count=hard_count,
        adaptive_count=adaptive_count,
        hard_foreign=hard_foreign,
        signals=used,
        notes=notes,
    )


def apply_department_floor(
    result: EvidenceResult,
    *,
    department_code: str,
    has_conflict: bool = False,
) -> EvidenceResult:
    """Если сработал hard для leadership/VED — подтянуть score до порога accept."""
    code = (department_code or "").strip()
    score = result.score
    notes = list(result.notes)

    if has_conflict and code in {CHAIRMAN_DEPARTMENT_CODE, OD_DEPARTMENT_CODE}:
        notes.append("no_floor_due_to_conflict")
        return EvidenceResult(
            score=score,
            level=score_to_level(score),
            hard_count=result.hard_count,
            adaptive_count=result.adaptive_count,
            hard_foreign=result.hard_foreign,
            signals=list(result.signals),
            notes=notes,
        )

    if result.hard_count >= 1:
        if code == CHAIRMAN_DEPARTMENT_CODE and score < GATE_CHAIRMAN:
            score = GATE_CHAIRMAN
            notes.append("floor_chairman_hard")
        elif code == OD_DEPARTMENT_CODE and score < GATE_OD:
            score = GATE_OD
            notes.append("floor_od_hard")
        elif code == VED_DEPARTMENT_CODE and result.hard_foreign and score < GATE_VED:
            score = GATE_VED
            notes.append("floor_ved_hard_foreign")

    score = _clamp(score)
    return EvidenceResult(
        score=score,
        level=score_to_level(score),
        hard_count=result.hard_count,
        adaptive_count=result.adaptive_count,
        hard_foreign=result.hard_foreign,
        signals=list(result.signals),
        notes=notes,
    )


def department_gate_min(department_code: str, *, default_min: float = 0.70) -> int:
    code = (department_code or "").strip()
    if code == CHAIRMAN_DEPARTMENT_CODE:
        return GATE_CHAIRMAN
    if code == OD_DEPARTMENT_CODE:
        return GATE_OD
    if code == VED_DEPARTMENT_CODE:
        return GATE_VED
    return max(0, min(100, round(default_min * 100)))


def department_confidence_accepted(
    *,
    department_code: str,
    score: int,
    hard_count: int,
    adaptive_count: int,
    hard_foreign: bool,
    has_conflict: bool = False,
    default_min: float = 0.70,
) -> tuple[bool, str]:
    """Проверка accept-gate по отделу."""
    code = (department_code or "").strip()
    min_pct = department_gate_min(code, default_min=default_min)

    if code in LEADERSHIP_DEPARTMENT_CODES and code in {
        CHAIRMAN_DEPARTMENT_CODE,
        OD_DEPARTMENT_CODE,
    }:
        if has_conflict:
            return False, "leadership_conflict"
        need_adaptive = (
            ADAPTIVE_ONLY_CHAIRMAN
            if code == CHAIRMAN_DEPARTMENT_CODE
            else ADAPTIVE_ONLY_OD
        )
        if hard_count < 1 and adaptive_count < need_adaptive:
            return False, f"leadership_need_hard_or_{need_adaptive}_adaptive"
        if score < min_pct:
            return False, f"leadership_score_below_{min_pct}"
        return True, "ok"

    if code == VED_DEPARTMENT_CODE:
        if not hard_foreign:
            return False, "ved_requires_hard_foreign"
        if score < min_pct:
            return False, f"ved_score_below_{min_pct}"
        return True, "ok"

    if score < min_pct:
        return False, f"score_below_{min_pct}"
    return True, "ok"


def build_signals_for_route(
    *,
    match_source: str,
    department_code: str,
    topic_hits: int = 0,
    content_hits: int = 0,
    matched_keywords: list[str] | None = None,
    org_confirmed: bool = False,
    has_conflict: bool = False,
    info_mailbox_no_topic: bool = False,
    unknown_route: bool = False,
    foreign_confirm_markers: list[str] | None = None,
    sender_domain_confirm: bool = False,
    commercial_ru_vs_ved: bool = False,
    leadership_rejected: bool = False,
) -> list[EvidenceSignal]:
    """Собирает сигналы из результата RuleRouter."""
    source = (match_source or "").strip()
    code = (department_code or "").strip()
    keywords = list(matched_keywords or [])
    signals: list[EvidenceSignal] = []

    if leadership_rejected and code in LEADERSHIP_DEPARTMENT_CODES:
        signals.append(
            EvidenceSignal(
                SignalClass.PENALTY,
                "leadership_mailbox_reject",
                100,
                "leadership outside allowed mailbox",
            )
        )
        return signals

    if source in EXACT_MAILBOX_SOURCES:
        signals.append(
            EvidenceSignal(SignalClass.HARD, "exact_mailbox", POINTS_EXACT_MAILBOX, source)
        )
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                "cascade_exact",
                POINTS_HARD_CASCADE,
                source,
            )
        )

    if source in LEARNED_HARD_SOURCES:
        signals.append(
            EvidenceSignal(SignalClass.HARD, "learned_hard", POINTS_LEARNED_HARD, source)
        )

    if source in LEADERSHIP_HARD_SOURCES:
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                "cascade_leadership",
                POINTS_HARD_LEADERSHIP_CASCADE,
                source,
            )
        )

    if source in STRONG_HARD_SOURCES:
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                "cascade_strong",
                POINTS_HARD_PRODUCT_CASCADE,
                source,
            )
        )

    if source in FOREIGN_HARD_SOURCES or (
        code == VED_DEPARTMENT_CODE and source.startswith("det_foreign")
    ):
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                "cascade_foreign",
                POINTS_HARD_FOREIGN_CASCADE,
                source,
            )
        )
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                "confirm_foreign_domain",
                POINTS_CONFIRM_FACT,
                "foreign_domain",
            )
        )

    if source.startswith(PRODUCT_HARD_PREFIXES) and source not in LEADERSHIP_HARD_SOURCES | FOREIGN_HARD_SOURCES:
        if source != "det_foreign_domain":
            signals.append(
                EvidenceSignal(
                    SignalClass.HARD,
                    "cascade_product",
                    POINTS_HARD_PRODUCT_CASCADE,
                    source,
                )
            )

    if source in EMAIL_KEYWORD_SOURCES:
        # Мягкий hard-ish: как email_keyword в старой формуле (+45) → cascade ≥ MEDIUM с adaptive.
        signals.append(
            EvidenceSignal(SignalClass.HARD, "cascade_email_keyword", 50, source)
        )

    if sender_domain_confirm:
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                "confirm_sender_domain",
                POINTS_CONFIRM_FACT,
                "sender_domain",
            )
        )
    if org_confirmed:
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                "confirm_org",
                POINTS_CONFIRM_FACT,
                "org_confirmed",
            )
        )
    for marker in foreign_confirm_markers or []:
        signals.append(
            EvidenceSignal(
                SignalClass.HARD,
                f"confirm_foreign_marker:{marker}",
                POINTS_CONFIRM_FACT,
                marker,
            )
        )

    # Adaptive clusters from keywords / topic / content hits.
    clusters = 0
    if topic_hits:
        clusters += min(2, max(1, topic_hits))
    if content_hits:
        clusters += min(3, max(1, content_hits // 2 or 1))
    # Уникальные keyword-кластеры (не более 5).
    for kw in keywords[:5]:
        if kw and kw not in {m for m in (foreign_confirm_markers or [])}:
            clusters += 1
    # Для hard product/leadership keywords уже учтены в cascade — adaptive только для soft sources.
    soft_adaptive = source in {
        "content",
        "email_keyword",
        "info_strict_unclear",
        "reserve",
        "sales_gazprom",
        "sales_orkk",
        "sales_odp",
    } or source.startswith("sales")
    if soft_adaptive:
        for i in range(min(clusters, 5)):
            signals.append(
                EvidenceSignal(
                    SignalClass.ADAPTIVE,
                    f"adaptive_cluster_{i}",
                    POINTS_ADAPTIVE_CLUSTER,
                    "keyword_cluster",
                )
            )
    elif keywords and source.startswith("det_"):
        # Небольшое adaptive усиление от числа маркеров.
        for i in range(min(2, len(keywords))):
            signals.append(
                EvidenceSignal(
                    SignalClass.ADAPTIVE,
                    f"adaptive_det_{i}",
                    POINTS_ADAPTIVE_CLUSTER,
                    keywords[i] if i < len(keywords) else "",
                )
            )

    if has_conflict and source != "human_correction":
        signals.append(
            EvidenceSignal(SignalClass.PENALTY, "conflict", PENALTY_CONFLICT, "rule_conflict")
        )
    if info_mailbox_no_topic:
        signals.append(
            EvidenceSignal(SignalClass.PENALTY, "info_no_topic", 20, "info@ without topic")
        )
    if unknown_route:
        signals.append(
            EvidenceSignal(SignalClass.PENALTY, "unknown_route", 40, "reserve")
        )
    if commercial_ru_vs_ved and code == VED_DEPARTMENT_CODE:
        signals.append(
            EvidenceSignal(
                SignalClass.PENALTY,
                "commercial_ru_vs_ved",
                PENALTY_COMMERCIAL_VS_VED,
                "commercial markers on RU domain",
            )
        )

    return signals


def evaluate_route_confidence(
    *,
    match_source: str,
    department_code: str,
    topic_hits: int = 0,
    content_hits: int = 0,
    matched_keywords: list[str] | None = None,
    org_confirmed: bool = False,
    has_conflict: bool = False,
    info_mailbox_no_topic: bool = False,
    unknown_route: bool = False,
    foreign_confirm_markers: list[str] | None = None,
    sender_domain_confirm: bool = False,
    commercial_ru_vs_ved: bool = False,
    leadership_rejected: bool = False,
    llm_confidence: float | None = None,
    apply_floor: bool = True,
) -> EvidenceResult:
    signals = build_signals_for_route(
        match_source=match_source,
        department_code=department_code,
        topic_hits=topic_hits,
        content_hits=content_hits,
        matched_keywords=matched_keywords,
        org_confirmed=org_confirmed,
        has_conflict=has_conflict,
        info_mailbox_no_topic=info_mailbox_no_topic,
        unknown_route=unknown_route,
        foreign_confirm_markers=foreign_confirm_markers,
        sender_domain_confirm=sender_domain_confirm,
        commercial_ru_vs_ved=commercial_ru_vs_ved,
        leadership_rejected=leadership_rejected,
    )
    result = accumulate_confidence(signals, llm_confidence=llm_confidence)
    if apply_floor:
        result = apply_department_floor(
            result,
            department_code=department_code,
            has_conflict=has_conflict and match_source != "human_correction",
        )
    return result
