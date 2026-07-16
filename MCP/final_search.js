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
    http.get(url, { headers: { Authorization: `Basic ${AUTH}`, Accept: 'application/json; charset=utf-8' }, timeout: 120000 }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        if (res.statusCode >= 400) reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 400)}`));
        else resolve(JSON.parse(data));
      });
    }).on('error', reject);
  });
}

function fmtDate(v) {
  if (!v || v.startsWith('0001-01-01')) return '';
  const [y, m, d] = v.slice(0, 10).split('-');
  return `${d}.${m}.${y}`;
}

async function resolveUser(refKey, userType) {
  if (!refKey) return '';
  let entity = null;
  if ((userType || '').includes('Catalog_Пользователи')) entity = 'Catalog_Пользователи';
  else if ((userType || '').includes('Catalog_ФизическиеЛица')) entity = 'Catalog_ФизическиеЛица';
  else if ((userType || '').includes('Catalog_Сотрудники')) entity = 'Catalog_Сотрудники';
  if (!entity) return refKey;
  try {
    const data = await fetchJson(`${BASE}${entity}(guid'${refKey}')?$format=json&$select=Description`);
    return data.Description || refKey;
  } catch {
    return refKey;
  }
}

async function main() {
  const matched = [];
  let skip = 0;
  while (true) {
    const params = new URLSearchParams({
      $format: 'json',
      $filter: "Date ge datetime'2020-01-01T00:00:00' and DeletionMark eq false",
      $select: 'Ref_Key,Description,Number,Date,Executed,ДатаИсполнения,Предмет,Предмет_Type,Исполнитель,Исполнитель_Type,Автор,Автор_Type,ПредметСтрокой',
      $top: '500',
      $skip: String(skip),
      $orderby: 'Date desc',
    });
    const data = await fetchJson(`${BASE}Task_ЗадачаИсполнителя?${params.toString()}`);
    const rows = data.value || [];
    for (const row of rows) {
      if (!USER_KEYS.has(row.Исполнитель)) continue;
      const isSvc = (row.Предмет_Type || '').includes(DOC_TYPE);
      const isOzn = /ознаком/i.test(row.Description || '');
      if (!isSvc || !isOzn) continue;
      matched.push(row);
    }
    process.stderr.write(`skip=${skip} batch=${rows.length} matched=${matched.length}\n`);
    if (rows.length < 500) break;
    skip += 500;
  }

  const results = [];
  for (const task of matched) {
    let doc = {};
    const docKey = task.Предмет;
    if (docKey) {
      try {
        const params = new URLSearchParams({
          $format: 'json',
          $select: 'Ref_Key,Number,Date,ТемаСлужебнойЗаписки,Статус',
        });
        doc = await fetchJson(`${BASE}Document_ТД_СлужебнаяЗаписка(guid'${docKey}')?${params}`);
      } catch (err) {
        doc = { accessError: String(err.message) };
      }
    }
    results.push({
      date: fmtDate(doc.Date || task.Date),
      number: doc.Number || task.Number || '',
      theme: doc.ТемаСлужебнойЗаписки || task.ПредметСтрокой || task.Description || '',
      author: await resolveUser(task.Автор, task.Автор_Type),
      addressee: await resolveUser(task.Исполнитель, task.Исполнитель_Type) || 'Уставицкий Андрей Алексеевич',
      status: task.Executed ? 'Ознакомлен' : 'Не ознакомлен',
      oznakom_date: task.Executed ? fmtDate(task.ДатаИсполнения) : '',
      ref_key: doc.Ref_Key || docKey,
      task_ref: task.Ref_Key,
      doc_status: doc.Статус || '',
      doc_access: doc.accessError || 'ok',
    });
  }

  console.log(JSON.stringify(results, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
