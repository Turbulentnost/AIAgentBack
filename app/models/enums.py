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
    TASK_INPUT = "task_input"
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

class KnowledgeBaseStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    UPDATING = "updating"
    ERROR = "error"
    ARCHIVED = "archived"

class KnowledgeBaseSourceStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    UPDATING = "updating"
    ERROR = "error"
    ARCHIVED = "archived"

class KnowledgeBaseRuleStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    ARCHIVED = "archived"

class KnowledgeBaseGrantType(str, enum.Enum):
    USER = "user"
    DEPARTMENT = "department"
    AGENT = "agent"
    ADMIN_ONLY = "admin_only"

class KnowledgeBaseAccessType(str, enum.Enum):
    READ = "read"
    SEARCH = "search"
    USE_VIA_AGENT = "use_via_agent"
    MANAGE_SOURCES = "manage_sources"
    REINDEX = "reindex"
    MANAGE_ACCESS = "manage_access"
    ADMIN = "admin"

class KnowledgeBaseAgentAccessMode(str, enum.Enum):
    SEARCH_ONLY = "search_only"
    SEARCH_AND_CITE = "search_and_cite"
    DECISION = "decision"
    AUTO_ACTION = "auto_action"

class KnowledgeBaseIndexJobType(str, enum.Enum):
    FULL = "full"
    SOURCE = "source"
    CHUNK = "chunk"
    EMBEDDINGS = "embeddings"
    ACCESS_REINDEX = "access_reindex"

class KnowledgeBaseIndexJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"

class KnowledgeBaseIndexErrorType(str, enum.Enum):
    TEXT_EXTRACT_FAILED = "text_extract_failed"
    OCR_FAILED = "ocr_failed"
    TABLE_READ_FAILED = "table_read_failed"
    UNSUPPORTED_FORMAT = "unsupported_format"
    DAMAGED_FILE = "damaged_file"
    DOCUMENT_ACCESS_DENIED = "document_access_denied"
    EMBEDDING_FAILED = "embedding_failed"
    QDRANT_WRITE_FAILED = "qdrant_write_failed"
    VERSION_CONFLICT = "version_conflict"
    DOCUMENT_EXPIRED = "document_expired"

class BrowserRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

class OpeDecision(str, enum.Enum):
    IMPLEMENT = "implement"
    REFINE = "refine"
    EXTEND = "extend"
    TERMINATE = "terminate"
