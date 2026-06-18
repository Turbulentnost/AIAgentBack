from __future__ import annotations

from enum import Enum


class DiagramBlockType(str, Enum):
    START = "start"
    OPERATION = "operation"
    DECISION = "decision"
    SUBPROCESS = "subprocess"
    DOCUMENT_OUTPUT = "document_output"
    CONNECTOR = "connector"
    END = "end"
