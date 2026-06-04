from __future__ import annotations
import abc
from typing import Any
class BaseConnector(abc.ABC):
    name: str = "base"
    @abc.abstractmethod
    async def fetch(self, resource: str, params: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError
    async def health(self) -> bool:
        return True
