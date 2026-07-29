# Программа интеграционных испытаний (п. 13 / 4.8.14)

## Этап 1 — базовая интеграция

| № | Сценарий | Шаги | Ожидаемый результат |
|---|---|---|---|
| 1.1 | REST create check | `POST /api/v1/checks` multipart + metadata | 202, `check_id`, counts |
| 1.2 | Idempotency | повтор с тем же `Idempotency-Key` | тот же `check_id`, без дубля в БД |
| 1.3 | PDF report | `GET /api/v1/checks/{id}/report` | `application/pdf` |
| 1.4 | JSON report | `GET .../report?format=json` | schema v1 |
| 1.5 | Findings | `GET .../findings` | structured list |
| 1.6 | Cancel | `POST .../cancel` | status `cancelled` |
| 1.7 | Rulesets | `GET /api/v1/rulesets` | current ruleset |
| 1.8 | Exchange log | операция create | запись в `integration_exchange_log` |
| 1.9 | Offline model | model down + есть marking/cache | result `from_marking`/`from_cache` |
| 1.10 | UI journal | вкладка «Интеграции» | список log entries |

## Этап 2 — PDM generic

| № | Сценарий | Ожидаемый результат |
|---|---|---|
| 2.1 | File package in `/incoming` | job created, package → `/completed` |
| 2.2 | metadata.json sidecar | поля mapped в unified card |
| 2.3 | Webhook registration | delivery queued on complete |
| 2.4 | PDM retry | idempotent by request_id |
| 2.5 | New revision checksum | old jobs stale |

## Этап 3 — 1С ERP

| № | Сценарий | Ожидаемый результат |
|---|---|---|
| 3.1 | ERP context update | metadata_extra saved |
| 3.2 | Readiness approved | `production_ready=true` |
| 3.3 | Critical errors | `production_ready=false`, `readiness_status=blocked` |

## Этап 4 — СЭД, очередь, CAD POC

| № | Сценарий | Ожидаемый результат |
|---|---|---|
| 4.1 | SED archive | files in `/data/integration/sed/{ref}/` |
| 4.2 | Worker poll | incoming scanned, webhooks retried |
| 4.3 | CAD POC | экспорт PDF из КОМПАС → REST check |

## Критерии pass/fail

- Pass: HTTP код, поля контракта и запись в exchange log соответствуют таблице.
- Fail: duplicate job при retry, отсутствие протокола, silent error без log entry.
