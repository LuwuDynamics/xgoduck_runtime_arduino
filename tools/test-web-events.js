// Execute the actual browser script with a minimal DOM and transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map(), win = {}, doc = {}, requests = [], beacons = [];
const element = id => {
  if (!nodes.has(id)) nodes.set(id, {value: '0', textContent: '', firstElementChild: {style: {}},
    insertAdjacentHTML() {}, addEventListener() {},
    setAttribute(name, value) { this[name] = value; }, hasPointerCapture: () => false});
  return nodes.get(id);
};
const context = {document: {hidden: false, documentElement: {}, getElementById: element, querySelectorAll: () => [],
  addEventListener: (name, fn) => doc[name] = fn},
  window: {addEventListener: (name, fn) => win[name] = fn},
  fetch: async (path, options) => {requests.push({path, body: JSON.parse(options.body)}); return {ok:true, json:async()=>({})};},
  navigator: {sendBeacon: (path, body) => beacons.push({path, body})}, Blob, URL,
  location: {href: 'http://192.0.2.10:9527/'},
  setInterval() {}, console};
for(const file of ['panel-language.js','app.js'])
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../assets',file),'utf8'), context);
(async () => {
  assert.equal(element('cockpit-link').href, 'http://192.0.2.10/');
  const language=element('language');
  assert.equal(language.textContent,'中文');
  language.onclick();
  assert.equal(context.document.documentElement.lang,'zh-CN');
  assert.equal(element('head-label-0').textContent,'头部');
  win.keydown({code:'Space',target:language,preventDefault(){assert.fail('language button should receive Space');}});
  assert.deepEqual(requests,[]);
  language.onclick();
  assert.equal(element('head-label-0').textContent,'Head');
  context.document.hidden = true;
  doc.visibilitychange();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/command');
  assert.deepEqual(requests[0].body.twist, [0,0,0]);
  win.pagehide();
  assert.equal(beacons[0].path, '/api/command');
  assert.deepEqual(JSON.parse(await beacons[0].body.text()), {twist:[0,0,0]});
  element('stop').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(requests.some(r => r.path === '/api/mode' && r.body.mode === 'off'));
  console.log('PASS: language switch sends no command; hide/close zero motion; Torque off still works.');
})().catch(e => {console.error(e); process.exitCode=1;});
