const http = require('http');

const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const USER_KEY = '9f5cc704-002c-11f1-9792-6cb31113810c';
const PERSON_KEY = '9cc55a11-fff7-11f0-9792-6cb31113810c';

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

async function searchCatalog(entity, filter) {
  const params = new URLSearchParams({ $format: 'json', $filter: filter, $select: 'Ref_Key,Description' });
  const data = await fetchJson(`${BASE}${entity}?${params.toString()}`);
  return data.value || [];
}

async function main() {
  const surnameFilter = "substringof('Уставицкий',Description) eq true and DeletionMark eq false";
  const users = await searchCatalog('Catalog_Пользователи', surnameFilter);
  const persons = await searchCatalog('Catalog_ФизическиеЛица', surnameFilter);
  let employees = [];
  try {
    employees = await searchCatalog('Catalog_Сотрудники', surnameFilter);
  } catch (err) {
    employees = [{ error: String(err.message) }];
  }

  console.log(JSON.stringify({ users, persons, employees, USER_KEY, PERSON_KEY }, null, 2));
}

main().catch((err) => { console.error(err); process.exit(1); });
