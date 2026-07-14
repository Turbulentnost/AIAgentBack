"""Общие константы meeting agent / slot preview."""

DEFAULT_DURATION_MINUTES = 60
SLOT_PREVIEW_MAX_DAYS = 30
SLOT_PREVIEW_TIMEOUT_SECONDS = 180
# Общий календарь calendar@... медленный — отдельный лимит для рекомендаций переноса.
COMPANY_CALENDAR_TIMEOUT_SECONDS = 60
QUORUM_MIN_COVERAGE_RATIO = 0.7
# Подбор более раннего слота после удаления участников — только когда свободны все оставшиеся.
REGISTRY_EARLIER_SLOT_MIN_COVERAGE_RATIO = 1.0
# Подбор общего слота после добавления участника — только полностью свободные слоты.
REGISTRY_COMMON_SLOT_MIN_COVERAGE_RATIO = 1.0
QUORUM_MAX_CANDIDATES = 3
QUORUM_VERIFY_TOP_N = 3
MEMO_FETCH_LIMIT = 50
MEMO_FETCH_POOL = 200
