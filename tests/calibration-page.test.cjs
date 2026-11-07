// Browser + API integration, entirely against the loopback simulator.
// npm install --no-save playwright; then: node --test tests/calibration-page.test.cjs
// Set PLAYWRIGHT_CHANNEL=chromium to use a downloaded Chromium instead of Chrome.
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createMock,MOTORS}=require('./calibration-mock.cjs');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});});
after(async()=>{await browser?.close();});

async function setup(t,options={},configure=()=>{}) {
  const mock=await createMock();configure(mock.state);
  const context=await browser.newContext({locale:'zh-CN',viewport:{width:1365,height:900},...options});
  const page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',route=>new URL(route.request().url()).origin===mock.url?route.continue():route.abort());
  t.after(async()=>{await context.close();await mock.close();assert.deepEqual(errors,[]);});
  await page.goto(mock.url+'/calibrate.html');
  return {...mock,page,context};
}
const enabled=(page,i)=>page.waitForFunction(i=>!document.querySelector(`[data-center="${i}"]`)?.disabled,i);
const holding=(page,i)=>page.waitForFunction(i=>document.getElementById('row'+i).classList.contains('done'),i);
const poll=(page,fn,arg)=>page.waitForFunction(fn,arg);

test('three zones, every real image decodes, bilingual labels, narrow layout and in-page enlargement',async t=>{
  const {page,state}=await setup(t);
  await enabled(page,10);
  await page.locator('.cal-pose').last().waitFor();
  assert.deepEqual(await page.locator('.cal-zone').evaluateAll(z=>z.map(x=>x.id)),['zone-head','zone-left','zone-right']);
  assert.match(await page.locator('#title').textContent(),/舵机/);
  assert.equal(await page.locator('[data-label=navControl]').textContent(),'控制');
  assert.equal(await page.locator('[data-label=navCalibration]').textContent(),'标定');
  assert.doesNotMatch(await page.locator('#intro').innerText(),/^\s*(?:false|undefined|null)\s*$/m);
  await page.evaluate(()=>{for(const i of document.images)i.loading='eager';});
  const sizes=await page.evaluate(()=>Promise.all(Array.from(document.querySelectorAll('.cal-pose,.cal-photo img,#imu-pose')).map(async i=>{await i.decode();return [i.naturalWidth,i.naturalHeight];})));
  assert.equal(sizes.length,19);assert.ok(sizes.every(([w,h])=>w>0&&h>0));
  const exits=state.calls.filter(c=>c.path==='/api/cal/exit').length;
  const beats=state.beats;
  await page.locator('[data-picture="cal-photo-head.webp"]').click();
  assert.equal(await page.locator('dialog').evaluate(d=>d.open),true);
  await page.waitForTimeout(350);
  assert.ok(state.beats>beats,'enlarging a photo must keep the calibration heartbeat alive');
  assert.equal(state.calls.filter(c=>c.path==='/api/cal/exit').length,exits);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('dialog').evaluate(d=>d.open),false);
  await page.locator('#language').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.locator('#title').textContent(),'Calibration');
  assert.equal(await page.locator('#imu-title').textContent(),'IMU calibration');
  assert.match(await page.locator('#row2 .cal-how').textContent(),/right angle/);
  for (const width of [390,320]) {
    await page.setViewportSize({width,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`no horizontal overflow at ${width}px`);
    const chips=await page.locator('.cal-chip').evaluateAll(chips=>chips.map(c=>{const r=c.getBoundingClientRect();return r.right<=innerWidth&&r.left>=0;}));
    assert.ok(chips.every(Boolean),'all three zone chips stay visible');
  }
});

test('top IMU panel uses the existing start endpoint and blocks conflicting servo operations', async t => {
  const {page, state} = await setup(t);
  const ready = () => poll(page, () => document.getElementById('imu-start').disabled === false);
  await ready();
  assert.equal(await page.locator('#imu-panel').evaluate(panel =>
    Boolean(panel.compareDocumentPosition(document.getElementById('intro')) & Node.DOCUMENT_POSITION_FOLLOWING)), true);

  state.imuOk = false;
  await poll(page, () => document.getElementById('imu-status').textContent.includes('检查 IMU 连接'));
  assert.equal(await page.locator('#imu-start').isDisabled(), true);
  state.imuOk = true;
  await ready();

  await page.locator('[data-center="10"]').click();
  await holding(page, 10);
  assert.equal(await page.locator('#imu-start').isDisabled(), true);
  assert.match(await page.locator('#imu-status').textContent(), /全部卸力/);
  await page.locator('[data-action=unload]').first().click();
  await ready();

  state.imuStartError = 'Hold the board upright with the battery connector pointing up.';
  await page.locator('#imu-start').click();
  await poll(page, () => document.getElementById('imu-status').textContent.includes('电池接口朝上'));
  state.imuStartError = null;
  await page.locator('#imu-start').click();
  await page.locator('#imu-progress').waitFor({state:'visible'});
  assert.equal(await page.locator('[data-center="10"]').isDisabled(), true);
  assert.equal(await page.locator('[data-action=unload]').first().isDisabled(), true);
  assert.equal(await page.locator('#exit').isEnabled(), true);
  await poll(page, () => document.getElementById('imu-status').textContent.includes('已保存'));
  assert.equal(state.mode, 'calibrate');
  assert.equal(await page.locator('#imu-progress').isHidden(), true);
  assert.equal(await page.locator('[data-center="10"]').isEnabled(), true);
  assert.deepEqual(state.calls.filter(call => call.path === '/api/imu-calibration').map(call => call.body),
    [{action:'start'}, {action:'start'}]);
});

test('IMU sample errors show the real reason and exit follows a pending start', async t => {
  const {page, state} = await setup(t, {}, state => {
    state.imuResult = {state:'saved', progress:1, calibrated:true};
    state.imuSampleError = 'Movement detected. Hold the board still and try again.';
  });
  await page.locator('#imu-start').click();
  await poll(page, () => document.getElementById('imu-status').textContent.includes('上一次标定结果已保留'));
  assert.match(await page.locator('#imu-status').textContent(), /检测到晃动/);

  state.imuSampleError = null;
  let releaseStart;
  await page.route('**/api/imu-calibration', async route => {
    await new Promise(resolve => { releaseStart = resolve; });
    await route.continue();
  });
  await page.locator('#imu-start').click();
  for (let i = 0; i < 100 && !releaseStart; i++) await page.waitForTimeout(20);
  assert.ok(releaseStart, 'IMU start must be pending');
  try {
    await page.locator('#exit').click();
    assert.equal(state.mode, 'calibrate');
  } finally {
    releaseStart();
  }
  await poll(page, () => document.getElementById('imu-status').textContent.includes('已取消'));
  assert.equal(state.mode, 'shadow');
  assert.equal(state.imuResult.calibrated, true);
  assert.equal(await page.locator('#imu-start').isDisabled(), true);
  assert.equal(await page.locator('[data-center="10"]').isDisabled(), true);
});

test('all 15 buttons map to canonical MCU indices, confirmed centers hold, unloading preserves saved centers',async t=>{
  const {page,state}=await setup(t);await enabled(page,10);
  for (const id of [30,31,32,33,34,10,11,12,13,14,20,21,22,23,24]) {
    const i=MOTORS.indexOf(id);
    await page.locator(`[data-center="${i}"]`).click();
    await holding(page,i);
    await page.locator(`[data-center="${i}"]`).dispatchEvent('click');
  }
  assert.deepEqual(state.calls.filter(c=>c.path==='/api/cal/center').map(c=>c.body.index),[10,11,12,13,14,0,1,2,3,4,5,6,7,8,9]);
  assert.match(await page.locator('#message').textContent(),/15/);
  await page.locator('[data-action=unload]').first().click();await enabled(page,10);
  assert.equal(state.saved.size,15);
  assert.equal(await page.locator('.cal-card.done').count(),0);
  assert.match(await page.locator('#total').textContent(),/0 \/ 15/);
  await page.locator('#exit').click();
  await poll(page,()=>document.querySelector('#message').textContent.includes('已关闭'));
  assert.equal(state.mode,'shadow');assert.equal(state.saved.size,15);
});

test('a missing servo and an IMU calibration block only permitted actions; a failed save is retryable',async t=>{
  const {page,state}=await setup(t,{},s=>{s.mask&=~(1<<3);s.failCenter=11;});await enabled(page,10);
  assert.equal(await page.locator('[data-center="3"]').isDisabled(),true);
  await page.locator('[data-center="11"]').click();
  await poll(page,()=>!document.getElementById('err11').hidden);
  assert.match(await page.locator('#err11').textContent(),/ID 31/);
  assert.equal(await page.locator('#row11').evaluate(x=>x.classList.contains('done')),false);
  assert.equal(state.saved.size,0);
  state.failCenter=null;
  await enabled(page,11);await page.locator('[data-center="11"]').click();await holding(page,11);
  state.imu=true;
  await poll(page,()=>document.querySelector('[data-center="0"]').disabled);
  assert.equal(await page.locator('[data-action=unload]').first().isDisabled(),true);
  assert.equal(await page.locator('#exit').isEnabled(),true);
});

test('status failure disables centering until fresh feedback returns',async t=>{
  const {page,state}=await setup(t);await enabled(page,10);
  state.statusDown=true;
  await poll(page,()=>document.querySelector('#message').textContent.includes('读不到'));
  assert.equal(await page.locator('[data-center="10"]').isDisabled(),true);
  await page.locator('[data-center="10"]').dispatchEvent('click');
  assert.equal(state.saved.size,0);
  state.statusDown=false;await enabled(page,10);
  await page.locator('[data-center="10"]').click();await holding(page,10);
});

test('exit cannot be undone by an older status response or a late center acknowledgement',async t=>{
  const {page,state}=await setup(t);await enabled(page,10);
  state.delayStatus=true;
  for(let i=0;i<100&&!state.releaseStatus;i++)await page.waitForTimeout(20);
  assert.ok(state.releaseStatus,'a delayed status request must be pending');
  await page.locator('#exit').click();
  await poll(page,()=>document.querySelector('#message').textContent.includes('已关闭'));
  state.releaseStatus();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('[data-center="10"]').isDisabled(),true);
  assert.equal(await page.locator('.cal-card.done').count(),0);
  await page.locator('[data-action=unload]').first().click();await enabled(page,10);
  state.delayCenter=true;
  await page.locator('[data-center="10"]').click();
  for(let i=0;i<100&&!state.releaseCenter;i++)await page.waitForTimeout(20);
  assert.ok(state.releaseCenter,'a delayed center request must be pending');
  await page.locator('#exit').click();
  await poll(page,()=>document.querySelector('#message').textContent.includes('已关闭'));
  state.releaseCenter();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('.cal-card.done').count(),0);
  assert.match(await page.locator('#message').textContent(),/已关闭/);
});

test('hidden pages release calibration and do not silently reenter on return',async t=>{
  const {page,state}=await setup(t);await enabled(page,10);
  await page.locator('[data-center="10"]').click();await holding(page,10);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  await poll(page,()=>document.querySelector('#message').textContent.includes('已关闭'));
  assert.equal(state.mode,'shadow');assert.equal(state.saved.size,1);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForTimeout(350);
  assert.equal(state.mode,'shadow');assert.equal(await page.locator('[data-center="10"]').isDisabled(),true);
});

for (const failure of ['missing','invalid','slow']) {
  test(`guide ${failure}: immediate canonical fallback and bounded load`,async t=>{
    const {page,state}=await setup(t,{locale:'en-US'},s=>{s.guideFailure=failure;});
    await enabled(page,10);
    assert.equal(await page.locator('.cal-card').count(),15);
    await poll(page,()=>document.querySelector('#intro').textContent.includes('Picture guide unavailable'));
    assert.equal(await page.locator('.cal-zone').count(),3);
    await page.locator('[data-center="10"]').click();await holding(page,10);
    assert.equal(state.calls.find(c=>c.path==='/api/cal/center').body.index,10);
  });
}
