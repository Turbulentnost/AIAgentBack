const http = require('http');

const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const DOC_TYPE = 'Document_ТД_СлужебнаяЗаписка';
const USER_KEYS = new Set([
  '9f5cc704-002c-11f1-9792-6cb31113810c', // Catalog_Пользователи
  '9cc55a11-fff7-11f0-9792-6cb31113810c', // Catalog_ФизическиеЛица
  'cca67597-fff8-11f0-9792-6cb31113810c', // Catalog_Сотрудники
]);

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, {
      headers: { Authorization: `Basic ${AUTH}`, Accept: 'application/json; charset=utf-8' },
      timeout: 120000,
    }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode >= 400) reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 300)}`));
        else resolve(JSON.parse(data));
      });
    }).on('error', reject);
  });
}

async function main() {
  // Add employee keys dynamically
  try {
    const params = new URLSearchParams({
      $format: 'json',
      $filter: "substringof('Уставицкий',Description) eq true and DeletionMark eq false",
      $select: 'Ref_Key,Description',
    });
    const data = await fetchJson(`${BASE}Catalog_Сотрудники?${params}`);
    for (const row of data.value || []) USER_KEYS.add(row.Ref_Key);
  } catch (_) {}

  const filters = [
    "substringof('ознаком',Description) eq true and DeletionMark eq false",
    "substringof('Ознаком',Description) eq true and DeletionMark eq false",
    'DeletionMark eq false',
  ];

  const matched = [];
  const seen = new Set();

  for (const filter of filters) {
    let skip = 0;
    while (true) {
      const params = new URLSearchParams({
        $format: 'json',
        $filter: filter,
        $select: 'Ref_Key,Description,Number,Date,Executed,ДатаИсполнения,Предмет,Предмет_Type,Исполнитель,Исполнитель_Type,Автор,Автор_Type,RoutePoint,ПредметСтрокой',
        $top: '500',
        $skip: String(skip),
        $orderby: 'Date desc',
      });
      const data = await fetchJson(`${BASE}Task_ЗадачаИсполнителя?${params.toString()}`);
      const rows = data.value || [];
      for (const row of rows) {
        if (!(row.Предмет_Type || '').includes(DOC_TYPE)) continue;
        if (!USER_KEYS.has(row.Исполнитель)) continue;
        if (seen.has(row.Ref_Key)) continue;
        seen.add(row.Ref_Key);
        matched.push(row);
      }
      if (rows.length < 500) break;
      skip += 500;
      if (filter === 'DeletionMark eq false' && skip >= 5000) break; // safety for broad filter
    }
    if (matched.length > 0 && filter !== 'DeletionMark eq false') break;
  }

  console.log(JSON.stringify({ userKeys: [...USER_KEYS], count: matched.length, tasks: matched.slice(0, 20) }, null, 2));
}

main().catch((err) => { console.error(err); process.exit(1); });
