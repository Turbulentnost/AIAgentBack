const http = require('http');
const https = require('https');

const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const USER_KEY = '9f5cc704-002c-11f1-9792-6cb31113810c';
const USER_NAME = 'Уставицкий Андрей Алексеевич';
const DOC_TYPE = 'Document_ТД_СлужебнаяЗаписка';
const userCache = new Map();

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    const req = lib.get(url, {
      headers: {
        Authorization: `Basic ${AUTH}`,
        Accept: 'application/json; charset=utf-8',
      },
      timeout: 120000,
    }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 500)}`));
          return;
        }
        resolve(JSON.parse(data));
      });
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
  });
}

async function paginate(entity, filter, select) {
  const rows = [];
  let skip = 0;
  while (true) {
    const params = new URLSearchParams({
      $format: 'json',
      $filter: filter,
      $select: select,
      $top: '500',
      $skip: String(skip),
      $orderby: 'Date desc',
    });
    const data = await fetchJson(BASE + entity + '?' + params.toString());
    const batch = data.value || [];
    rows.push(...batch);
    if (batch.length < 500) break;
    skip += 500;
  }
  return rows;
}

async function resolveUser(refKey, userType) {
  if (!refKey || refKey === '00000000-0000-0000-0000-000000000000') return '';
  const cacheKey = `${refKey}|${userType || ''}`;
  if (userCache.has(cacheKey)) return userCache.get(cacheKey);

  let entity = null;
  if ((userType || '').includes('Catalog_Пользователи')) entity = 'Catalog_Пользователи';
  else if ((userType || '').includes('Catalog_ФизическиеЛица')) entity = 'Catalog_ФизическиеЛица';
  else if ((userType || '').includes('Catalog_Сотрудники')) entity = 'Catalog_Сотрудники';

  let name = refKey;
  if (entity) {
    try {
      const params = new URLSearchParams({ $format: 'json', $select: 'Description' });
      const data = await fetchJson(`${BASE}${entity}(guid'${refKey}')?${params}`);
      name = data.Description || refKey;
    } catch (_) {}
  }
  userCache.set(cacheKey, name);
  return name;
}

function fmtDate(value) {
  if (!value || value.startsWith('0001-01-01')) return '';
  return value.slice(0, 10).split('-').reverse().join('.');
}

async function main() {
  const select = [
    'Ref_Key', 'Description', 'Number', 'Date', 'Executed', 'ДатаИсполнения',
    'Предмет', 'Предмет_Type', 'Исполнитель', 'Исполнитель_Type',
    'Автор', 'Автор_Type', 'RoutePoint', 'RoutePoint_Type', 'РезультатВыполнения', 'ПредметСтрокой',
  ].join(',');

  const filters = [
    "substringof('ознаком',Description) eq true and DeletionMark eq false",
    "substringof('Ознаком',Description) eq true and DeletionMark eq false",
  ];

  const tasks = new Map();
  for (const filter of filters) {
    const rows = await paginate('Task_ЗадачаИсполнителя', filter, select);
    for (const row of rows) {
      if (row.Исполнитель !== USER_KEY) continue;
      if (!(row.Предмет_Type || '').includes(DOC_TYPE)) continue;
      tasks.set(row.Ref_Key, row);
    }
  }

  const results = [];
  const sorted = [...tasks.values()].sort((a, b) => (b.Date || '').localeCompare(a.Date || ''));
  for (const task of sorted) {
    const docKey = task.Предмет;
    let doc = {};
    if (docKey) {
      try {
        const params = new URLSearchParams({
          $format: 'json',
          $select: 'Ref_Key,Number,Date,ТемаСлужебнойЗаписки,ТемаСлужебнойЗаписки_Type,Ответственный_Key',
        });
        doc = await fetchJson(`${BASE}Document_ТД_СлужебнаяЗаписка(guid'${docKey}')?${params}`);
      } catch (err) {
        doc = { error: String(err.message || err) };
      }
    }

    const theme = doc.ТемаСлужебнойЗаписки || task.ПредметСтрокой || task.Description || '';
    const author = await resolveUser(task.Автор, task.Автор_Type);
    const addressee = (await resolveUser(task.Исполнитель, task.Исполнитель_Type)) || USER_NAME;
    const executed = !!task.Executed;

    results.push({
      date: fmtDate(doc.Date || task.Date),
      number: doc.Number || task.Number || '',
      theme,
      author,
      addressee,
      status: executed ? 'Ознакомлен' : 'Не ознакомлен',
      oznakom_date: executed ? fmtDate(task.ДатаИсполнения) : '',
      ref_key: doc.Ref_Key || docKey,
      task_ref: task.Ref_Key,
      task_description: task.Description || '',
    });
  }

  console.log(JSON.stringify(results, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
