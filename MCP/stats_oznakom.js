const http = require('http');

const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const USER = '9f5cc704-002c-11f1-9792-6cb31113810c';
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
      res.on('end', () => resolve(JSON.parse(data)));
    }).on('error', reject);
  });
}

async function main() {
  const filters = [
    "substringof('ознаком',Description) eq true and DeletionMark eq false",
    "substringof('Ознаком',Description) eq true and DeletionMark eq false",
  ];

  const stats = { total: 0, userTotal: 0, svcTotal: 0, userSvc: 0, userSamples: [] };

  for (const filter of filters) {
    let skip = 0;
    while (true) {
      const params = new URLSearchParams({
        $format: 'json',
        $filter: filter,
        $select: 'Ref_Key,Description,Исполнитель,Предмет_Type,Date',
        $top: '500',
        $skip: String(skip),
      });
      const data = await fetchJson(BASE + 'Task_ЗадачаИсполнителя?' + params.toString());
      const rows = data.value || [];
      for (const row of rows) {
        stats.total++;
        const isUser = row.Исполнитель === USER;
        const isSvc = (row.Предмет_Type || '').includes(DOC_TYPE);
        if (isUser) stats.userTotal++;
        if (isSvc) stats.svcTotal++;
        if (isUser && isSvc) stats.userSvc++;
        if (isUser && stats.userSamples.length < 10) {
          stats.userSamples.push({
            Description: row.Description,
            Предмет_Type: row.Предмет_Type,
            Date: row.Date,
          });
        }
      }
      if (rows.length < 500) break;
      skip += 500;
    }
  }

  console.log(JSON.stringify(stats, null, 2));
}

main().catch((err) => { console.error(err); process.exit(1); });
