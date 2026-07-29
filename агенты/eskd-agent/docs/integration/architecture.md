# Integration Layer Architecture (п. 4.8.2)

## Компоненты

- `integration_documents` — unified document card
- `integration_jobs` — lifecycle accepted → queued → running → completed/rejected/error/cancelled
- `integration_exchange_log` — audit M2M operations
- `integration_webhooks` + deliveries — outbound PDM events
- `integration_api_keys` — service accounts

## Public API

Prefix `/api/v1/checks` отделён от UI proxy `/api/v1/eskd/*`.

## File exchange (generic PDM)

```
/data/integration/incoming → processing → completed | error → archive
```

Каждый пакет содержит `metadata.json` или `metadata.xml` и файлы КД.

## Security

- RBAC через роли AD / API keys
- TLS termination — на reverse proxy (nginx); см. `docs/integration/tls-nginx.md`
- `closed_contour=true` — без внешних cloud вызовов (конфиг)

## Worker

`IntegrationQueueWorker` — polling incoming packages и webhook retry.
