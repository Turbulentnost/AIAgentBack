# CAD plugin POC — КОМПАС-3D (п. 4.8.3)

MVP без встроенного SDK: команда меню запускает экспорт текущего чертежа в PDF и отправляет его в integration REST.

## Поток

1. Пользователь: «Проверить ЕСКД» в КОМПАС.
2. Макрос сохраняет активный документ во временный PDF.
3. HTTP `POST /api/v1/checks` с `metadata.json` и файлом.
4. UI/PDM получает `check_id` и открывает протокол.

## Пример metadata

```json
{
  "document_id": "KOMPAS-ACTIVE",
  "source_system": "kompas",
  "designation": "АБВГ.001.001",
  "revision": "01",
  "author": "ivanov"
}
```

## Пример PowerShell (POC)

```powershell
$metadata = @{
  document_id = "KOMPAS-ACTIVE"
  source_system = "kompas"
  designation = "ABVG.001.001"
  revision = "01"
} | ConvertTo-Json -Compress

curl.exe -X POST "http://192.168.2.102:8000/api/v1/checks" `
  -H "X-Dev-User: kompas-user" `
  -H "X-Dev-Roles: ESKD_Designers" `
  -F "metadata=$metadata" `
  -F "files=@C:\Temp\drawing.pdf"
```

## Дальнейшее развитие

- Highlight замечаний на листе (overlay coordinates из findings).
- Встроенная панель статуса `GET /api/v1/checks/{id}`.
