const http = require('http');
const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const DOC_TYPE = 'Document_ТД_СлужебнаяЗаписка';

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
  const descCounts = {};
  const routeCounts = {};
  const samples = [];
  const filters = [
    "substringof('ознаком',Description) eq true and DeletionMark eq false",
    "substringof('Ознаком',Description) eq true and DeletionMark eq false",
  ];

  for (const filter of filters) {
    let skip = 0;
    while (true) {
      const params = new URLSearchParams({
        $format: 'json', $filter: filter,
        $select: 'Ref_Key,Description,RoutePoint,RoutePoint_Type,Предмет,Предмет_Type,Date,Executed,Исполнитель',
        $top: '500', $skip: String(skip),
      });
      const data = await fetchJson(`${BASE}Task_ЗадачаИсполнителя?${params.toString()}`);
      const rows = data.value || [];
      for (const row of rows) {
        if (!(row.Предмет_Type || '').includes(DOC_TYPE)) continue;
        descCounts[row.Description || ''] = (descCounts[row.Description || ''] || 0) + 1;
        const rk = `${row.RoutePoint || ''}|${row.RoutePoint_Type || ''}`;
        routeCounts[rk] = (routeCounts[rk] || 0) + 1;
        if (samples.length < 5) samples.push(row);
      }
      if (rows.length < 500) break;
      skip += 500;
    }
  }

  const topDesc = Object.entries(descCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const topRoute = Object.entries(routeCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
  console.log(JSON.stringify({ topDesc, topRoute, samples }, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
