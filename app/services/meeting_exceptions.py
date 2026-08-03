"""Общие исключения сервисного слоя meeting agent."""


class MeetingServiceError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code
