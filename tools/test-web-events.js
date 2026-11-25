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
let browserTime=0;
const context = {document: {hidden: false, documentElement: {}, getElementById: element, querySelectorAll: () => [],
  addEventListener: (name, fn) => doc[name] = fn},
  window: {addEventListener: (name, fn) => win[name] = fn},
  fetch: async (path, options) => {const body=JSON.parse(options.body);requests.push({path, body}); return {ok:true, json:async()=>({roller_gliding:!!body.roller_release})};},
  navigator: {sendBeacon: (path, body) => beacons.push({path, body})}, Blob, URL,
  location: {href: 'http://192.0.2.10:9527/'},
  setInterval() {}, performance:{now:()=>browserTime}, console};
for(const file of ['panel-language.js','app.js'])
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../assets',file),'utf8'), context);
(async () => {
  assert.equal(element('cockpit-link').href, 'http://192.0.2.10/');
  const language=element('language');
  assert.equal(language.textContent,'中文');
  language.onclick();
  assert.equal(context.document.documentElement.lang,'zh-CN');
  context.showServoAlarms({'12': {code: 9, active: true}, '21': {code: 0x24, active: true},
    '30': {code: 2, active: false}, '31': {code: 0x80, active: true}});
  assert.equal(element('servo-alarms').hidden,false);
  assert.match(element('servo-alarms').textContent,/12 · 电压异常、过流 · 0x09 \(当前\)/);
  assert.match(element('servo-alarms').textContent,/21 · 过热、未知报警位 0x20 · 0x24 \(当前\)/);
  assert.match(element('servo-alarms').textContent,/30 · 磁编码器异常 · 0x02 \(最近 2 秒\)/);
  assert.match(element('servo-alarms').textContent,/31 · 未知报警 · 0x80 \(当前\)/);
  win.keydown({code:'Space',target:language,preventDefault(){assert.fail('language button should receive Space');}});
  assert.deepEqual(requests,[]);
  language.onclick();
  assert.match(element('servo-alarms').textContent,/12 · Voltage out of range \+ Overcurrent · 0x09 \(current\)/);
  assert.match(element('servo-alarms').textContent,/30 · Magnetic encoder fault · 0x02 \(last 2 s\)/);
  context.showServoAlarms({'12': {code: 0, active: false}});
  assert.equal(element('servo-alarms').hidden,true);
  context.showServoAlarms({});
  assert.equal(element('servo-alarms').hidden,true);
  context.document.hidden = true;
  doc.visibilitychange();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/command');
  assert.deepEqual(requests[0].body.twist, [0,0,0]);
  assert.equal(requests[0].body.idle_heartbeat,undefined);
  assert.equal('head' in requests[0].body,false);
  assert.equal('mouth' in requests[0].body,false);
  win.pagehide();
  assert.equal(beacons[0].path, '/api/command');
  assert.deepEqual(JSON.parse(await beacons[0].body.text()), {twist:[0,0,0]});
  element('stop').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(requests.some(r => r.path === '/api/mode' && r.body.mode === 'off'));
  context.document.hidden=false;
  vm.runInNewContext('rollerEnabled=true', context);
  await context.sendCommand();
  assert.equal(requests.at(-1).body.idle_heartbeat,true);
  const event={key:'w',code:'KeyW',target:{}};
  win.keydown(event);
  await new Promise(resolve=>setImmediate(resolve));
  win.keyup(event);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(requests.at(-1).body.twist,[0,0,0]);
  assert.equal(requests.at(-1).body.roller_release,true);
  assert.equal(requests.at(-1).body.idle_heartbeat,undefined);
  browserTime+=2000;
  await context.sendCommand();
  assert.equal(requests.at(-1).body.roller_release,true);
  // Blur arrives while a release renewal is in flight. The queued command
  // must remain an explicit stop when the pending request is sent.
  const renewal=context.sendCommand();
  win.blur();
  await renewal;
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.at(-1).body.roller_release,undefined);
  assert.equal(requests.at(-1).body.idle_heartbeat,undefined);
  assert.deepEqual(requests.at(-1).body.twist,[0,0,0]);
  await context.sendCommand();
  assert.equal(requests.at(-1).body.idle_heartbeat,true);
  element('zero').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.at(-1).body.idle_heartbeat,undefined);
  console.log('PASS: language switch sends no command; hide/close zero motion; Torque off still works.');
})().catch(e => {console.error(e); process.exitCode=1;});
