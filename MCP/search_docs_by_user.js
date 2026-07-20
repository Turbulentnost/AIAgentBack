const http = require('http');

const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const USER_KEYS = [
  '9f5cc704-002c-11f1-9792-6cb31113810c',
  '9cc55a11-fff7-11f0-9792-6cb31113810c',
  'cca67597-fff8-11f0-9792-6cb31113810c',
];

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
  const results = [];
  for (const key of USER_KEYS) {
    for (const field of ['Ответственный_Key', 'МенеджерКому_Key', 'ИсполнительУД_Key']) {
      try {
        const filter = `${field} eq guid'${key}' and DeletionMark eq false`;
        const params = new URLSearchParams({
          $format: 'json',
          $filter: filter,
          $select: 'Ref_Key,Number,Date,ТемаСлужебнойЗаписки,Статус,Ответственный_Key,МенеджерКому_Key,ИсполнительУД_Key',
          $top: '100',
          $orderby: 'Date desc',
        });
        const data = await fetchJson(`${BASE}Document_ТД_СлужебнаяЗаписка?${params.toString()}`);
        for (const row of data.value || []) {
          results.push({ field, userKey: key, ...row });
        }
      } catch (err) {
        results.push({ field, userKey: key, error: String(err.message) });
      }
    }
  }

  // Tabular section participants
  for (const key of USER_KEYS) {
    try {
      const filter = `Участник_Key eq guid'${key}'`;
      const params = new URLSearchParams({
        $format: 'json',
        $filter: filter,
        $select: 'Ref_Key,LineNumber,Участник_Key,РольНаСовещании',
        $top: '100',
      });
      const data = await fetchJson(`${BASE}Document_ТД_СлужебнаяЗаписка_СписокУчастников?${params.toString()}`);
      for (const row of data.value || []) {
        results.push({ source: 'СписокУчастников', userKey: key, ...row });
      }
    } catch (err) {
      results.push({ source: 'СписокУчастников', userKey: key, error: String(err.message) });
    }
  }

  console.log(JSON.stringify(results, null, 2));
}

main().catch((err) => { console.error(err); process.exit(1); });
