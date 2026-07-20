const http = require('http');
const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const USER = '9f5cc704-002c-11f1-9792-6cb31113810c';

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
  const keys = new Set([
    '9f5cc704-002c-11f1-9792-6cb31113810c',
    '9cc55a11-fff7-11f0-9792-6cb31113810c',
    'cca67597-fff8-11f0-9792-6cb31113810c',
  ]);
  const filters = [
    "substringof('ознаком',Description) eq true and DeletionMark eq false",
    "substringof('Ознаком',Description) eq true and DeletionMark eq false",
  ];
  const samples = [];
  let userAny = 0;
  for (const filter of filters) {
    let skip = 0;
    while (true) {
      const params = new URLSearchParams({
        $format: 'json', $filter: filter,
        $select: 'Ref_Key,Description,Date,Исполнитель,Предмет_Type,Executed',
        $top: '500', $skip: String(skip),
      });
      const data = await fetchJson(`${BASE}Task_ЗадачаИсполнителя?${params.toString()}`);
      const rows = data.value || [];
      for (const row of rows) {
        if (!keys.has(row.Исполнитель)) continue;
        userAny++;
        if (samples.length < 15) samples.push(row);
      }
      if (rows.length < 500) break;
      skip += 500;
    }
  }
  console.log(JSON.stringify({ userAny, samples }, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
