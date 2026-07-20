const http = require('http');
const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const USER_KEYS = new Set([
  '9f5cc704-002c-11f1-9792-6cb31113810c',
  '9cc55a11-fff7-11f0-9792-6cb31113810c',
  'cca67597-fff8-11f0-9792-6cb31113810c',
]);

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, { headers: { Authorization: `Basic ${AUTH}`, Accept: 'application/json; charset=utf-8' }, timeout: 120000 }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        if (res.statusCode >= 400) reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 300)}`));
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
      $filter: "Date ge datetime'2026-01-01T00:00:00' and DeletionMark eq false",
      $select: 'Ref_Key,Description,Date,Executed,Исполнитель,Предмет_Type',
      $top: '500',
      $skip: String(skip),
      $orderby: 'Date desc',
    });
    const data = await fetchJson(`${BASE}Task_ЗадачаИсполнителя?${params.toString()}`);
    const rows = data.value || [];
    for (const row of rows) {
      if (USER_KEYS.has(row.Исполнитель)) matched.push(row);
    }
    if (rows.length < 500) break;
    skip += 500;
  }
  console.log(JSON.stringify({ count: matched.length, tasks: matched }, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
