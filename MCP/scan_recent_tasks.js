const http = require('http');

const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const USER_KEYS = new Set([
  '9f5cc704-002c-11f1-9792-6cb31113810c',
  '9cc55a11-fff7-11f0-9792-6cb31113810c',
  'cca67597-fff8-11f0-9792-6cb31113810c',
]);
const DOC_TYPE = 'Document_ТД_СлужебнаяЗаписка';

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
        if (res.statusCode >= 400) reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 400)}`));
        else resolve(JSON.parse(data));
      });
    }).on('error', reject);
  });
}

async function main() {
  const matched = [];
  let skip = 0;
  while (true) {
    const params = new URLSearchParams({
      $format: 'json',
      $filter: 'DeletionMark eq false',
      $select: 'Ref_Key,Description,Number,Date,Executed,ДатаИсполнения,Предмет,Предмет_Type,Исполнитель,Исполнитель_Type,RoutePoint,RoutePoint_Type',
      $top: '500',
      $skip: String(skip),
      $orderby: 'Date desc',
    });
    const data = await fetchJson(`${BASE}Task_ЗадачаИсполнителя?${params.toString()}`);
    const rows = data.value || [];
    for (const row of rows) {
      if (!(row.Предмет_Type || '').includes(DOC_TYPE)) continue;
      if (!USER_KEYS.has(row.Исполнитель)) continue;
      matched.push(row);
    }
    process.stderr.write(`skip=${skip} batch=${rows.length} matched=${matched.length}\n`);
    if (rows.length < 500) break;
    skip += 500;
    if (skip >= 20000) break; // scan recent 20k tasks first
  }
  console.log(JSON.stringify(matched, null, 2));
}

main().catch((err) => { console.error(err); process.exit(1); });
