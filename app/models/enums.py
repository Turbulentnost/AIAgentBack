from __future__ import annotations

import enum

class AgentStatus(str, enum.Enum):
    DRAFT = "draft"
    TESTING = "testing"
    OPE = "ope"
    REFINEMENT = "refinement"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"

class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TaskStepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class ConfidenceLevel(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class FindingSeverity(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class SourceReliability(str, enum.Enum):
    OFFICIAL = "official"
    VERIFIED = "verified"
    UNOFFICIAL = "unofficial"
    NEEDS_CHECK = "needs_check"
    OUTDATED = "outdated"
    REPLACED = "replaced"
    REVOKED = "revoked"

class DocumentType(str, enum.Enum):
    REGULATION = "regulation"
    TZ = "tz"
    PMI = "pmi"
    KD = "kd"
    TD = "td"
    CONTRACT = "contract"
    SPECIFICATION = "specification"
    ACT = "act"
    CHECKLIST = "checklist"
    PROTOCOL = "protocol"
    ORDER = "order"
    MEMO = "memo"
    OTHER = "other"

class DocumentProcessingStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    TEXT_EXTRACTION_PENDING = "text_extraction_pending"
    TEXT_EXTRACTED = "text_extracted"
    INDEXING_PENDING = "indexing_pending"
    INDEXED = "indexed"
    FAILED = "failed"

class TextExtractStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    FAILED = "failed"

class OpeDecision(str, enum.Enum):
    IMPLEMENT = "implement"
    REFINE = "refine"
    EXTEND = "extend"
    TERMINATE = "terminate"
