"""Общие константы meeting agent / slot preview."""

DEFAULT_DURATION_MINUTES = 60
SLOT_PREVIEW_MAX_DAYS = 30
SLOT_PREVIEW_TIMEOUT_SECONDS = 180
# Ручная проверка слота: общий календарь + hydrate участников могут занять >3 мин.
SLOT_DETAIL_TIMEOUT_SECONDS = 300
# Кэш занятости для ручного планирования после подбора слота (тот же сеанс, шаги 1–3).
# 1) Пишется при slot-preview. 2) Читается в slot-preview/details. 3) Общий календарь —
#    только для занятых и только на выбранный слот.
SLOT_AVAILABILITY_CACHE_TTL_MINUTES = 10
# Окно дат, в котором переиспользуем снимок; не весь горизонт поиска (30 дней).
SLOT_AVAILABILITY_CACHE_WINDOW_DAYS = 7
# Таймаут одного HTTP-запроса к Exchange (exchangelib BaseProtocol.TIMEOUT).
EWS_REQUEST_TIMEOUT_SECONDS = 180
# Общий календарь calendar@... медленный — отдельный лимит для рекомендаций переноса.
COMPANY_CALENDAR_TIMEOUT_SECONDS = 180
# Ручная проверка: читаем общий календарь только вокруг выбранного слота.
COMPANY_CALENDAR_SLOT_PAD_MINUTES = 15
COMPANY_CALENDAR_SLOT_MAX_ITEMS = 20
# Окно поиска альтернативы для переноса конфликтующей встречи.
RESCHEDULE_HINT_SEARCH_DAYS = 14
QUORUM_MIN_COVERAGE_RATIO = 0.7
# Подбор более раннего слота после удаления участников — только когда свободны все оставшиеся.
REGISTRY_EARLIER_SLOT_MIN_COVERAGE_RATIO = 1.0
# Подбор общего слота после добавления участника — только полностью свободные слоты.
REGISTRY_COMMON_SLOT_MIN_COVERAGE_RATIO = 1.0
QUORUM_MAX_CANDIDATES = 3
QUORUM_VERIFY_TOP_N = 3
MEMO_FETCH_LIMIT = 50
MEMO_FETCH_POOL = 200
