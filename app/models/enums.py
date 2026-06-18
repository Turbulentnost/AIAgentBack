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
    EXCLUDED = "excluded"
    NEEDS_OCR = "needs_ocr"
    READY_TO_INDEX = "ready_to_index"


class KnowledgeBaseSourcePrecheckStatus(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class KnowledgeBaseChunkQualityStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    GOOD = "good"
    MEDIUM = "medium"
    LOW = "low"
    FAILED = "failed"

class KnowledgeBaseRuleStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    ARCHIVED = "archived"

class KnowledgeBaseGrantType(str, enum.Enum):
    USER = "user"
    DEPARTMENT = "department"
    ROLE = "role"
    ORGANIZATION = "organization"
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
    CANCELLED = "CANCELLED"

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

class NdChangeRequestStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    DETECTING_DOCUMENT = "detecting_document"
    REQUIRES_MANUAL_DOCUMENT_SELECTION = "requires_manual_document_selection"
    DOCUMENT_SELECTED = "document_selected"
    LOCATING_CHANGE_PLACE = "locating_change_place"
    REQUIRES_MANUAL_LOCATION_SELECTION = "requires_manual_location_selection"
    APPLYING_CHANGES = "applying_changes"
    READY_FOR_USER_REVIEW = "ready_for_user_review"
    SENT_TO_APPROVAL = "sent_to_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"

class NdChangeLocationType(str, enum.Enum):
    TEXT_SECTION = "text_section"
    PARAGRAPH = "paragraph"
    SUBPARAGRAPH = "subparagraph"
    TABLE = "table"
    TABLE_ROW = "table_row"
    APPENDIX = "appendix"
    CHANGE_REGISTRATION_SHEET = "change_registration_sheet"
    NORMATIVE_REFERENCE = "normative_reference"
    ABBREVIATION = "abbreviation"
    TERM_DEFINITION = "term_definition"
    BLOCK_TEXT = "block_text"

class NdChangeLocationStatus(str, enum.Enum):
    FOUND = "found"
    CANDIDATE = "candidate"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    CONFIRMED = "confirmed"

class NdChangeOperationType(str, enum.Enum):
    REPLACE_SECTION = "replace_section"
    REPLACE_PARAGRAPH = "replace_paragraph"
    INSERT_AFTER = "insert_after"
    INSERT_BEFORE = "insert_before"
    DELETE_SECTION = "delete_section"
    UPDATE_TABLE = "update_table"
    ADD_TABLE_ROW = "add_table_row"
    REPLACE_APPENDIX = "replace_appendix"
    UPDATE_REFERENCE = "update_reference"
    ANNUL_DOCUMENT = "annul_document"
    REPLACE_DOCUMENT = "replace_document"
    MANUAL_REVIEW = "manual_review"

class NdChangeOperationStatus(str, enum.Enum):
    DRAFT = "draft"
    APPLIED_TO_DRAFT = "applied_to_draft"
    REQUIRES_MANUAL_REVIEW = "requires_manual_review"
    FAILED = "failed"

class NdChangeDraftFileStatus(str, enum.Enum):
    GENERATED = "generated"
    WARNING_SOURCE_NOT_EDITABLE = "warning_source_not_editable"
    FAILED = "failed"

class NdChangeApprovalStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"

class NdChangeResultStatus(str, enum.Enum):
    DRAFT = "draft"
    READY_FOR_USER_REVIEW = "ready_for_user_review"
    SENT_TO_APPROVAL = "sent_to_approval"
    FAILED = "failed"

class AgentType(str, enum.Enum):
    CONSULTANT = "consultant"
    ACTION = "action"


class AgentBuilderSessionStatus(str, enum.Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    EXECUTING = "executing"
    NEEDS_CLARIFICATION = "needs_clarification"
    GENERATED = "generated"
    NEEDS_USER_REVIEW = "needs_user_review"
    APPROVED = "approved"
    FAILED = "failed"
    ARCHIVED = "archived"


class AgentBuilderPlanStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentBuilderPlanStepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentBlueprintStatus(str, enum.Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    GENERATED = "generated"
    NEEDS_USER_REVIEW = "needs_user_review"
    APPROVED = "approved"
    IN_DEVELOPMENT = "in_development"
    IMPLEMENTED = "implemented"
    ARCHIVED = "archived"


class OpeDecision(str, enum.Enum):
    IMPLEMENT = "implement"
    REFINE = "refine"
    EXTEND = "extend"
    TERMINATE = "terminate"


class NdDocumentType(str, enum.Enum):
    POLICY = "policy"
    REGULATION = "regulation"
    PROCEDURE = "procedure"
    STO = "sto"
    INSTRUCTION = "instruction"


class NdQmsLevel(str, enum.Enum):
    STRATEGIC = "strategic"
    ORGANIZATIONAL = "organizational"
    PROCESS = "process"
    TECHNICAL = "technical"
    OPERATIONAL = "operational"


class NdDocumentCardStatus(str, enum.Enum):
    ACTIVE = "active"
    DRAFT = "draft"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class NdConfidentialityLevel(str, enum.Enum):
    PUBLIC = "public"
    RESTRICTED = "restricted"
    CONFIDENTIAL = "confidential"


class NdExtractionStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class NdBuildStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class NdStructuralDocumentStatus(str, enum.Enum):
    ACTIVE = "active"
    PROJECT = "project"
    DRAFT = "draft"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class NdStructuralDocumentType(str, enum.Enum):
    """Тип нормативного документа СМК (классификация DocumentType)."""

    POLICY = "POLICY"
    REGULATION = "REGULATION"
    PROCESS_REGULATION = "PROCESS_REGULATION"
    STO = "STO"
    INSTRUCTION = "INSTRUCTION"


class NdDocumentLevel(str, enum.Enum):
    """Уровень документа в иерархии СМК (классификация DocumentLevel)."""

    STRATEGIC = "strategic"
    ORGANIZATIONAL = "organizational"
    PROCESS = "process"
    TECHNICAL = "technical"
    OPERATIONAL = "operational"


class NdGraphEntityType(str, enum.Enum):
    DEPARTMENT = "Department"
    PROCESS = "Process"
    DOCUMENT = "Document"
    ROLE = "Role"
    FORM = "Form"
    SYSTEM = "System"
    RESOURCE = "Resource"


class NdRelationType(str, enum.Enum):
    DEPARTMENT_OWNS_PROCESS = "DEPARTMENT_OWNS_PROCESS"
    DEPARTMENT_PARTICIPATES_IN_PROCESS = "DEPARTMENT_PARTICIPATES_IN_PROCESS"
    PROCESS_USES_FORM = "PROCESS_USES_FORM"
    PROCESS_USES_SYSTEM = "PROCESS_USES_SYSTEM"
    PROCESS_HAS_ROLE = "PROCESS_HAS_ROLE"
    PROCESS_PRODUCES_OUTPUT = "PROCESS_PRODUCES_OUTPUT"
    PROCESS_CONSUMES_INPUT = "PROCESS_CONSUMES_INPUT"
    PROCESS_RELATED_TO_PROCESS = "PROCESS_RELATED_TO_PROCESS"
    DOCUMENT_REGULATES_PROCESS = "DOCUMENT_REGULATES_PROCESS"
    DOCUMENT_MENTIONS_DEPARTMENT = "DOCUMENT_MENTIONS_DEPARTMENT"
    ROLE_RESPONSIBLE_FOR_ACTION = "ROLE_RESPONSIBLE_FOR_ACTION"


class NdRelationExtractionType(str, enum.Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNCERTAIN = "uncertain"


class NdResponsibilityRoleType(str, enum.Enum):
    PROCESS_OWNER = "process_owner"
    PERFORMER = "performer"
    CONTROLLER = "controller"
    APPROVER = "approver"
    DOCUMENT_OWNER = "document_owner"
    UNKNOWN = "unknown"


class NdUnknownReason(str, enum.Enum):
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    REQUIRES_HUMAN_CONFIRMATION = "requires_human_confirmation"


class DepartmentAnalysisRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DepartmentAnalysisStep(str, enum.Enum):
    INITIALIZING = "initializing"
    LOADING_KNOWLEDGE_BASES = "loading_knowledge_bases"
    EXTRACTING_DOCUMENT_CARDS = "extracting_document_cards"
    BUILDING_DEPARTMENT_PROFILE = "building_department_profile"
    BUILDING_RELATIONS = "building_relations"
    COMPLETED = "completed"
    FAILED = "failed"
