#!/usr/bin/env node
/**
 * Builds docs/1c_odata_entities_catalog.md from 1C OData $metadata + sample reads.
 * Read-only. Single output file.
 */
import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, '1c_odata_entities_catalog.md');
const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');
const BATCH = 50;
const CONCURRENCY = 8;

const PREFIX_MAP = [
  ['Catalog_', 'Catalog', 'Справочники', 'Справочник'],
  ['Document_', 'Document', 'Документы', 'Документ'],
  ['InformationRegister_', 'InformationRegister', 'Регистры сведений', 'Регистр сведений'],
  ['AccumulationRegister_', 'AccumulationRegister', 'Регистры накопления', 'Регистр накопления'],
  ['AccountingRegister_', 'AccountingRegister', 'Регистры бухгалтерии', 'Регистр бухгалтерии'],
  ['Task_', 'Task', 'Задачи и бизнес-процессы', 'Задача'],
  ['BusinessProcess_', 'BusinessProcess', 'Задачи и бизнес-процессы', 'Бизнес-процесс'],
  ['Enum_', 'Enum', 'Остальные объекты', 'Перечисление'],
  ['ExchangePlan_', 'ExchangePlan', 'Остальные объекты', 'План обмена'],
  ['ChartOfCharacteristicTypes_', 'ChartOfCharacteristicTypes', 'Остальные объекты', 'План видов характеристик'],
  ['ChartOfAccounts_', 'ChartOfAccounts', 'Остальные объекты', 'План счетов'],
  ['ChartOfCalculationTypes_', 'ChartOfCalculationTypes', 'Остальные объекты', 'План видов расчёта'],
];

function fetchBuffer(url, accept = 'application/xml') {
  return new Promise((resolve, reject) => {
    const req = http.get(url, {
      headers: { Authorization: `Basic ${AUTH}`, Accept: accept },
      timeout: 180000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        if (res.statusCode >= 400) reject(new Error(`HTTP ${res.statusCode}: ${buf.toString('utf8').slice(0, 300)}`));
        else resolve({ status: res.statusCode, body: buf, contentType: res.headers['content-type'] });
      });
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
  });
}

function fetchJson(entitySet, top = 1) {
  const url = `${BASE}${encodeURIComponent(entitySet)}?$format=json&$top=${top}`;
  return fetchBuffer(url, 'application/json').then((r) => JSON.parse(r.body.toString('utf8')));
}

function classify(entitySet) {
  for (const [prefix, type, section, purposePrefix] of PREFIX_MAP) {
    if (entitySet.startsWith(prefix)) {
      const ru = entitySet.slice(prefix.length);
      return { type, section, purposePrefix, ruName: ru };
    }
  }
  const idx = entitySet.indexOf('_');
  const type = idx > 0 ? entitySet.slice(0, idx) : entitySet;
  return { type, section: 'Остальные объекты', purposePrefix: type, ruName: entitySet };
}

function parseMetadata(xml) {
  const entityTypes = new Map();
  const entityTypeRe = /<EntityType Name="([^"]+)"[^>]*>([\s\S]*?)<\/EntityType>/g;
  let m;
  while ((m = entityTypeRe.exec(xml)) !== null) {
    const name = m[1];
    const block = m[2];
    const keys = [...block.matchAll(/<PropertyRef Name="([^"]+)"/g)].map((x) => x[1]);
    const props = [];
    const propRe = /<Property Name="([^"]+)"\s+Type="([^"]+)"[^/]*\/?>/g;
    let pm;
    while ((pm = propRe.exec(block)) !== null) {
      props.push({ name: pm[1], type: pm[2] });
    }
    const navs = [...block.matchAll(/<NavigationProperty Name="([^"]+)"\s+Relationship="[^"]+\.([^"_]+)/g)].map((x) => x[1]);
    entityTypes.set(name, { keys, props, navs });
  }

  const entitySets = [];
  const setRe = /<EntitySet Name="([^"]+)"[^>]*EntityType="StandardODATA\.([^"]+)"/g;
  while ((m = setRe.exec(xml)) !== null) {
    entitySets.push({ entitySet: m[1], entityType: m[2] });
  }
  return { entityTypes, entitySets };
}

function escCell(s) {
  return String(s ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ').slice(0, 500);
}

function mainFields(props) {
  const scalar = props.filter((p) => !p.type.startsWith('Collection(') && !p.name.endsWith('_Type') && !p.name.endsWith('_Base64Data'));
  const main = scalar.filter((p) => !['_Key', 'DataVersion', 'Predefined', 'PredefinedDataName'].includes(p.name) && !p.name.endsWith('_Key'));
  const keys = scalar.filter((p) => p.name.endsWith('_Key') || ['Ref_Key', 'Number', 'Date', 'Description', 'Posted', 'DeletionMark', 'Code'].includes(p.name));
  const pick = [...new Set([...keys.slice(0, 5), ...main.slice(0, 8)].map((p) => p.name))].slice(0, 12);
  return pick.join(', ') || scalar.slice(0, 8).map((p) => p.name).join(', ');
}

function tabularParts(props) {
  return props.filter((p) => p.type.startsWith('Collection(')).map((p) => p.name).join(', ');
}

function isTabularSection(entitySet) {
  return /_(RecordType|_[A-Za-zА-Яа-я0-9])/.test(entitySet.replace(/^(Catalog|Document|InformationRegister|AccumulationRegister|AccountingRegister|Task|BusinessProcess)_/, ''))
    && entitySet.includes('_')
    && !entitySet.endsWith('_RecordType') === false;
}

function shouldSample(entitySet) {
  if (entitySet.endsWith('_RecordType')) return false;
  if (entitySet.includes('_Удалить')) return false;
  // tabular row types: Document_X_Y with multiple underscores after prefix
  const parts = entitySet.split('_');
  if (parts[0] === 'Document' && parts.length > 2) return false;
  if (parts[0] === 'Catalog' && parts.length > 2 && !entitySet.includes('ПрисоединенныеФайлы')) return false;
  if (parts[0] === 'Task' && parts.length > 2) return false;
  if (parts[0] === 'BusinessProcess' && parts.length > 2) return false;
  return true;
}

async function testAccess(entitySet) {
  try {
    await fetchJson(entitySet, 1);
    return { ok: true, error: '' };
  } catch (e) {
    return { ok: false, error: String(e.message || e).slice(0, 200) };
  }
}

async function getSamples(entitySet) {
  try {
    const data = await fetchJson(entitySet, 3);
    const rows = data.value || [];
    if (!rows.length) return '(пусто)';
    return rows.map((r, i) => {
      const keys = ['Ref_Key', 'Number', 'Date', 'Description', 'Code', 'Posted'];
      const parts = keys.filter((k) => r[k] !== undefined).map((k) => `${k}=${JSON.stringify(r[k])?.slice(0, 80)}`);
      return `${i + 1}. ${parts.join('; ') || JSON.stringify(r).slice(0, 120)}`;
    }).join(' \\| ');
  } catch (e) {
    return `(ошибка: ${String(e.message).slice(0, 80)})`;
  }
}

async function poolMap(items, fn, limit) {
  const results = new Array(items.length);
  let i = 0;
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      results[idx] = await fn(items[idx], idx);
    }
  }
  await Promise.all(Array.from({ length: limit }, worker));
  return results;
}

async function main() {
  console.error('Health check...');
  const health = await fetchBuffer(BASE);
  console.error(`Health: HTTP ${health.status}`);

  console.error('Downloading $metadata...');
  const metaResp = await fetchBuffer(`${BASE}$metadata`);
  const xml = metaResp.body.toString('utf8');
  console.error(`Metadata size: ${xml.length}`);

  const { entityTypes, entitySets } = parseMetadata(xml);
  console.error(`EntitySets: ${entitySets.length}, EntityTypes: ${entityTypes.size}`);

  const entities = entitySets.map(({ entitySet, entityType }) => {
    const cls = classify(entitySet);
    const schema = entityTypes.get(entityType) || { keys: [], props: [], navs: [] };
    const purpose = `${cls.purposePrefix} «${cls.ruName}»`;
    return {
      entitySet,
      entityType,
      ...cls,
      purpose,
      keys: schema.keys.join(', ') || '—',
      mainFields: mainFields(schema.props),
      tabular: tabularParts(schema.props) || '—',
      relations: schema.navs.slice(0, 15).join(', ') || '—',
      access: 'проверка…',
      samples: '—',
      accessError: '',
    };
  });

  // Access tests in batches
  for (let b = 0; b < entities.length; b += BATCH) {
    const batch = entities.slice(b, b + BATCH);
    console.error(`Access batch ${b / BATCH + 1}/${Math.ceil(entities.length / BATCH)}`);
    await poolMap(batch, async (ent) => {
      const { ok, error } = await testAccess(ent.entitySet);
      ent.access = ok ? 'да' : 'нет';
      ent.accessError = error;
      if (ok && shouldSample(ent.entitySet)) {
        ent.samples = await getSamples(ent.entitySet);
      } else if (ok) {
        ent.samples = '(табличная часть / без выборки)';
      } else {
        ent.samples = '—';
      }
    }, CONCURRENCY);
  }

  const counts = {};
  for (const e of entities) counts[e.type] = (counts[e.type] || 0) + 1;

  const sections = {
    'Справочники': entities.filter((e) => e.section === 'Справочники'),
    'Документы': entities.filter((e) => e.section === 'Документы'),
    'Регистры сведений': entities.filter((e) => e.section === 'Регистры сведений'),
    'Регистры накопления': entities.filter((e) => e.section === 'Регистры накопления'),
    'Регистры бухгалтерии': entities.filter((e) => e.section === 'Регистры бухгалтерии'),
    'Задачи и бизнес-процессы': entities.filter((e) => e.section === 'Задачи и бизнес-процессы'),
    'Остальные объекты': entities.filter((e) => e.section === 'Остальные объекты'),
  };

  const accessErrors = entities.filter((e) => e.access === 'нет');

  let md = `# Каталог объектов 1С, доступных через OData

> Сформировано: ${new Date().toISOString().slice(0, 10)}. Источник: OData \`standard.odata\` базы \`erp_pm\`.
> Health check: HTTP ${health.status}. Опубликовано сущностей (EntitySet): **${entities.length}**.

## Сводка

| Тип объекта | Количество |
|---|---|
`;

  const typeOrder = ['Catalog', 'Document', 'InformationRegister', 'AccumulationRegister', 'AccountingRegister', 'Task', 'BusinessProcess', 'Enum', 'ExchangePlan', 'ChartOfCharacteristicTypes', 'ChartOfAccounts', 'ChartOfCalculationTypes'];
  const sortedTypes = [...new Set([...typeOrder, ...Object.keys(counts)])];
  for (const t of sortedTypes) {
    if (counts[t]) md += `| ${t} | ${counts[t]} |\n`;
  }
  md += `| **Итого EntitySet** | **${entities.length}** |\n`;
  md += `| Доступно для чтения | ${entities.filter((e) => e.access === 'да').length} |\n`;
  md += `| Ошибка доступа | ${accessErrors.length} |\n\n`;

  function renderSection(title, list) {
    let s = `## ${title}\n\n`;
    s += `| № | Техническое имя | Русское название | Назначение | Ключевые поля | Основные поля | Табличные части | Связи | Доступ | Примеры (до 3) |\n`;
    s += `|---:|---|---|---|---|---|---|---|---|---|\n`;
    list.forEach((e, i) => {
      s += `| ${i + 1} | \`${escCell(e.entitySet)}\` | ${escCell(e.ruName)} | ${escCell(e.purpose)} | ${escCell(e.keys)} | ${escCell(e.mainFields)} | ${escCell(e.tabular)} | ${escCell(e.relations)} | ${e.access} | ${escCell(e.samples)} |\n`;
    });
    s += '\n';
    return s;
  }

  for (const [title, list] of Object.entries(sections)) {
    if (list.length) md += renderSection(title, list);
  }

  md += `## Объекты с ошибкой доступа\n\n`;
  md += `| Техническое имя | Текст ошибки |\n|---|---|\n`;
  for (const e of accessErrors.slice(0, 500)) {
    md += `| \`${escCell(e.entitySet)}\` | ${escCell(e.accessError)} |\n`;
  }
  if (accessErrors.length > 500) {
    md += `\n*… и ещё ${accessErrors.length - 500} объектов с ошибкой доступа.*\n`;
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, md, 'utf8');
  console.error(`Written: ${OUT} (${md.length} chars)`);
}

main().catch((e) => { console.error(e); process.exit(1); });
