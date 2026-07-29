# Матрица интеграционных взаимодействий (п. 4.8.13)

| Контур | Направление | Канал | Основные поля | Периодичность |
|---|---|---|---|---|
| PDM generic | PDM → ESKD | REST `POST /api/v1/checks` | `document_id`, `revision`, `checksum`, files | по событию выпуска версии |
| PDM generic | PDM → ESKD | Файлы `/incoming/{package}/` + `metadata.json` | sidecar JSON/XML, PDF/DXF/DWG | watcher/cron 15 с |
| PDM generic | ESKD → PDM | Webhook `CheckCompleted` / `CheckRejected` | `check_id`, counts, `report_url`, `blocks_workflow` | push + retry |
| PDM generic | ESKD → PDM | Pull `GET /api/v1/checks/{id}` | статус, findings, report | fallback polling |
| 1С ERP | 1С → ESKD | REST `POST /api/v1/erp/context` | nomenclature, order, project, due_date | регламент / событие |
| 1С ERP | ESKD → 1С | REST `GET /api/v1/erp/readiness/{document_id}` | `production_ready`, `critical_count`, report URL | polling 1С |
| СЭД / архив | ESKD → СЭD | REST `POST /api/v1/sed/archive` | protocol PDF/JSON, checksum, ruleset, decision | после нормоконтроля |
| AD / SSO | AD → UI | LDAP (optional) + dev headers | группы AD → RBAC роли | login |
| M2M | интегратор → ESKD | `X-API-Key` / Bearer | service account roles | каждый запрос |
| CAD (POC) | КОМПАС → ESKD | экспорт PDF + REST checks | `designation`, file | команда пользователя |

## RBAC

| Роль | REST |
|---|---|
| ESKD_Administrators | checks, webhooks, logs, api-keys |
| ESKD_NormControl | checks, logs, sed |
| ESKD_Designers | checks |
| ESKD_Managers | checks read, erp readiness |
| ESKD_Auditors | checks read, logs, erp |

## Idempotency / checksum

- Заголовок `Idempotency-Key` или поле `request_id` — повтор не создаёт вторую job.
- Смена `checksum` помечает предыдущие job как `is_stale=true`.
