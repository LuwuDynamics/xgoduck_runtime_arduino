// Execute the actual browser script with a minimal DOM and transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map(), win = {}, doc = {}, requests = [], beacons = [];
const element = id => {
  if (!nodes.has(id)) nodes.set(id, {value: '0', textContent: '', firstElementChild: {style: {}},
    insertAdjacentHTML() {}, hasPointerCapture: () => false});
  return nodes.get(id);
};
const context = {document: {hidden: false, getElementById: element, querySelectorAll: () => [],
  addEventListener: (name, fn) => doc[name] = fn},
  window: {addEventListener: (name, fn) => win[name] = fn},
  fetch: async (path, options) => {requests.push({path, body: JSON.parse(options.body)}); return {ok:true, json:async()=>({})};},
  navigator: {sendBeacon: (path, body) => beacons.push({path, body})}, Blob,
  setInterval() {}, console};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../assets/app.js'), 'utf8'), context);
(async () => {
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
  console.log('PASS: hide/close zero motion without disarming; Torque off still works.');
})().catch(e => {console.error(e); process.exitCode=1;});
