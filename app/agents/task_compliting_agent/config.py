from __future__ import annotations

AGENT_ID = "task_compliting_agent"
AGENT_NAME = "Агент контроля исполнения задач"
AGENT_VERSION = "1.0.0"
DEFAULT_MODEL = "gpt-4.1"
CONFIDENCE_THRESHOLD = 0.75

COMMENT_PRESENCE_EMPTY = "empty"
COMMENT_PRESENCE_PRESENT = "present"

STATUS_NO_ANSWER = "no_answer"
STATUS_FILE_REQUIRED = "file_required"
STATUS_RELEVANT = "relevant"
STATUS_PARTIALLY_RELEVANT = "partially_relevant"
STATUS_IRRELEVANT = "irrelevant"
STATUS_UNCLEAR = "unclear"

VALID_STATUSES = (
    STATUS_NO_ANSWER,
    STATUS_FILE_REQUIRED,
    STATUS_RELEVANT,
    STATUS_PARTIALLY_RELEVANT,
    STATUS_IRRELEVANT,
    STATUS_UNCLEAR,
)
