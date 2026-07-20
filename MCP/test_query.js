const http = require('http');
const BASE = 'http://192.168.2.229:81/erp_pm/odata/standard.odata/';
const AUTH = Buffer.from('odata.user:npo852456').toString('base64');

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, { headers: { Authorization: `Basic ${AUTH}`, Accept: 'application/json' }, timeout: 120000 }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        console.error('URL', url.slice(0, 120));
        console.error('STATUS', res.statusCode, 'LEN', data.length);
        if (res.statusCode >= 400) reject(new Error(data.slice(0, 500)));
        else resolve(JSON.parse(data));
      });
    }).on('error', reject);
  });
}

fetchJson(`${BASE}Task_%D0%97%D0%B0%D0%B4%D0%B0%D1%87%D0%B0%D0%98%D1%81%D0%BF%D0%BE%D0%BB%D0%BD%D0%B8%D1%82%D0%B5%D0%BB%D1%8F?$format=json&$top=2&$filter=DeletionMark%20eq%20false`)
  .then((d) => console.log(JSON.stringify(d.value?.length)))
  .catch((e) => console.error(e.message));
