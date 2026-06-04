from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class BaseAgentConfig:
    agent_id: str
    name: str
    version: str = "1.0.0"
    default_model: str = "gpt-4.1"
    confidence_threshold: float = 0.75
