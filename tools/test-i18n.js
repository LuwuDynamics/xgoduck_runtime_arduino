#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const assets = process.env.XGODUCK_ASSETS
  ? path.resolve(process.env.XGODUCK_ASSETS)
  : path.join(root, 'assets');
const failures = [];

function fail(msg) { failures.push(msg); }

// Load i18n.js with the smallest DOM mock needed to read the dictionaries.
const store = {};
const ctx = {
  window: { addEventListener() {}, dispatchEvent() {} },
  document: {
    addEventListener() {},
    documentElement: {},
    querySelectorAll() { return []; },
    getElementById() { return null; }
  },
  navigator: { language: 'zh-CN' },
  location: { search: '' },
  localStorage: {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = v; }
  },
  URLSearchParams,
  console
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(assets, 'i18n.js'), 'utf8'), ctx);

const dict = ctx.window.xgoduckDict;
if (!dict || !dict.zh || !dict.en) {
  console.error('FAIL: i18n dictionaries are not exposed');
  process.exit(1);
}

const zh = new Set(Object.keys(dict.zh));
const en = new Set(Object.keys(dict.en));
for (const key of zh) if (!en.has(key)) fail(`missing en key: ${key}`);
for (const key of en) if (!zh.has(key)) fail(`missing zh key: ${key}`);

const used = new Set();
const files = ['index.html', 'calibrate.html', 'servo.html', 'app.js', 'calibrate.js', 'servo.js'];
for (const name of files) {
  const text = fs.readFileSync(path.join(assets, name), 'utf8');
  const attrRe = /data-i18n(?:-html|-aria|-title)?="([^"]+)"/g;
  let m;
  while ((m = attrRe.exec(text))) used.add(m[1]);
  const callRe = /\bt\(\s*'([^']+)'/g;
  while ((m = callRe.exec(text))) used.add(m[1]);
}

for (const key of used) {
  if (!zh.has(key)) fail(`key used but not defined: ${key}`);
}

const unused = [...zh].filter(k => !used.has(k));
if (unused.length) console.log('note: unused keys:', unused.join(', '));

if (failures.length) {
  console.error('FAIL');
  for (const f of failures) console.error(' - ' + f);
  process.exit(1);
}
console.log(`ok: ${zh.size} keys, ${used.size} used, zh/en aligned`);
