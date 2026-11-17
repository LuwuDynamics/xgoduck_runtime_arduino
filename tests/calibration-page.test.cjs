// Browser + API integration, entirely against the loopback simulator.
// npm install --no-save playwright; then: node --test tests/calibration-page.test.cjs
// Set PLAYWRIGHT_CHANNEL=chromium to use a downloaded Chromium instead of Chrome.
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createMock}=require('./calibration-mock.cjs');
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
const poll=(page,fn,arg)=>page.waitForFunction(fn,arg);
const batchReady=page=>poll(page,()=>!document.getElementById('complete-calibration').disabled);
const imuReady=page=>poll(page,()=>!document.getElementById('imu-start').disabled);

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
  assert.equal(await page.locator('#complete-calibration').isDisabled(), true);
});

test('footer default pose checks feedback, waits for enable and supports comparison and recalibration',async t=>{
  const {page,state}=await setup(t,{},s=>{s.mask&=~(1<<2);});
  const start=page.locator('#pose-start');
  await poll(page,()=>document.getElementById('pose-status').textContent.includes('15 个舵机'));
  assert.equal(await start.isDisabled(),true);
  assert.equal(state.calls.filter(call=>call.path==='/api/cal/default-pose').length,0);
  state.mask=32767;
  await start.click();
  await poll(page,()=>document.getElementById('pose-status').textContent.includes('等待舵机使能'));
  assert.equal(state.mode,'hold');
  assert.equal(await start.isDisabled(),true);
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  state.enabled=true;
  await poll(page,()=>document.getElementById('pose-status').textContent.includes('默认姿势已启用'));
  await page.locator('#finish [data-picture="cal-default-pose.jpg"]').click();
  assert.equal(await page.locator('#picture-image').getAttribute('src'),'cal-default-pose.jpg');
  const beats=state.poseBeats;
  await page.waitForTimeout(350);
  assert.ok(state.poseBeats>beats,'enlarging the comparison photo keeps the pose heartbeat active');
  await page.keyboard.press('Escape');
  await page.locator('#finish [data-action=unload]').click();
  await batchReady(page);
  assert.equal(state.mode,'calibrate');
  assert.equal(state.enabled,false);
  assert.match(await page.locator('#finish .cal-finish-recheck').textContent(),/复查舵机的中位标定/);
});

test('hiding the page while a default pose request is pending releases after the request',async t=>{
  const {page,state}=await setup(t);
  let releaseStart;
  await page.route('**/api/cal/default-pose',async route=>{
    await new Promise(resolve=>{releaseStart=resolve;});
    await route.continue();
  });
  await page.locator('#pose-start').click();
  for(let i=0;i<100&&!releaseStart;i++)await page.waitForTimeout(20);
  assert.ok(releaseStart,'default pose start must be pending');
  try{
    await page.evaluate(()=>{
      Object.defineProperty(document,'hidden',{configurable:true,value:true});
      document.dispatchEvent(new Event('visibilitychange'));
    });
  }finally{releaseStart();}
  await poll(page,()=>document.getElementById('message').textContent.includes('已关闭'));
  assert.deepEqual(state.calls.filter(call=>['/api/cal/default-pose','/api/cal/exit'].includes(call.path)).map(call=>call.path),
    ['/api/cal/default-pose','/api/cal/exit']);
  assert.equal(state.mode,'shadow');
  assert.equal(state.poseBeats,0);
});

test('three steps, usable reference photos, no old calibration entries, and bilingual narrow layout',async t=>{
  const {page,state}=await setup(t);
  await batchReady(page);
  assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
  assert.equal(await page.locator('[data-label=navCalibration]').textContent(),'标定');
  assert.equal(await page.locator('[data-label=unifiedTitle]').textContent(),'统一标定');
  assert.deepEqual(await page.locator('section .cal-step').allTextContents(),['01','02','03']);
  assert.equal(await page.locator('#zones,#zonebar,#neck-start,[data-calibration-mode],[data-center]').count(),0);
  assert.doesNotMatch(await page.locator('main').innerText(),/30 号|逐个标定|切换标定方式/);
  await page.evaluate(()=>{for(const image of document.images)image.loading='eager';});
  const images=await page.locator('main img').evaluateAll(items=>Promise.all(items.map(async image=>{
    await image.decode();
    return [image.getAttribute('src'),image.naturalWidth,image.naturalHeight];
  })));
  assert.deepEqual(images.map(([url])=>url),['cal-unified-1.png','cal-unified-2.png','cal-unified-3.png','cal-unified-4.png',
    'imu-calibration-pose.png','cal-default-pose.jpg']);
  assert.ok(images.every(([,w,h])=>w>0&&h>0));
  const exits=state.calls.filter(c=>c.path==='/api/cal/exit').length;
  const beats=state.beats;
  const imuPicture=page.locator('#imu-panel [data-picture="imu-calibration-pose.png"]');
  assert.equal(await imuPicture.getAttribute('aria-label'),'放大图片 · IMU 摆放参考');
  await imuPicture.click();
  assert.equal(await page.locator('#picture-dialog').evaluate(d=>d.open),true);
  assert.equal(await page.locator('#picture-image').getAttribute('src'),'imu-calibration-pose.png');
  await poll(page,()=>document.querySelector('#picture-image').naturalWidth>0);
  await page.waitForTimeout(350);
  assert.ok(state.beats>beats,'enlarging a photo must keep the calibration heartbeat alive');
  assert.equal(state.calls.filter(c=>c.path==='/api/cal/exit').length,exits);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#picture-dialog').evaluate(d=>d.open),false);
  await page.locator('#language').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.locator('#complete-calibration').textContent(),'Calibrate all 15 servos');
  assert.equal(await page.locator('#imu-title').textContent(),'IMU calibration');
  assert.equal(await page.locator('#imu-panel figcaption').textContent(),'IMU placement reference');
  assert.equal(await page.locator('#finish-title').textContent(),'Check the default pose');
  for(const width of [1365,390,320]){
    await page.setViewportSize({width,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`no horizontal overflow at ${width}px`);
  }
});

test('batch waits for STM32 and all 15 servos; proceeds directly to IMU and pose',async t=>{
  const {page,state}=await setup(t,{},state=>{state.mcuCalibrating=false;state.mask&=~(1<<2);});
  await poll(page,()=>document.getElementById('unified-status').textContent.includes('STM32'));
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  state.mcuCalibrating=true;
  await poll(page,()=>document.getElementById('unified-status').textContent.includes('12 号舵机的实时反馈'));
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  state.mask=32767;
  await batchReady(page);
  assert.equal(state.calls.filter(c=>c.path==='/api/cal/sitting').length,0);
  await page.locator('#complete-calibration').click();
  await poll(page,()=>document.getElementById('unified-status').textContent.includes('中位已保存'));
  assert.equal(state.saved.size,15);
  assert.ok(state.saved.has(10),'batch also covers ID 30');
  assert.equal(state.held.some(Boolean),false);
  assert.equal(state.enabled,false);

  state.imuOk=false;
  await poll(page,()=>document.getElementById('imu-status').textContent.includes('检查 IMU 连接'));
  assert.equal(await page.locator('#imu-start').isDisabled(),true);
  state.imuOk=true;
  await page.locator('#imu-start').click();
  await page.locator('#imu-progress').waitFor({state:'visible'});
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  assert.equal(await page.locator('#pose-start').isDisabled(),true);
  assert.equal(await page.locator('#exit').isEnabled(),true);
  await poll(page,()=>document.getElementById('imu-status').textContent.includes('已保存'));
  const poseStarted=page.waitForResponse(r=>r.url().endsWith('/api/cal/default-pose')&&r.ok());
  await page.locator('#pose-start').click();
  await poseStarted;
  assert.equal(state.mode,'hold');
  const saves=state.calls.filter(c=>['/api/cal/sitting','/api/cal/center','/api/imu-calibration','/api/cal/default-pose'].includes(c.path));
  assert.deepEqual(saves.map(c=>c.path),['/api/cal/sitting','/api/imu-calibration','/api/cal/default-pose']);
});

test('batch shows automatic retry progress and simple bilingual guidance on exhaustion',async t=>{
  const {page,state}=await setup(t,{},s=>{
    s.delayBatch=true;
    s.failBatch='Sitting calibration failed after 3 retries. Sitting calibration failed at target positions.';
  });
  await page.locator('#complete-calibration').click();
  await poll(page,()=>document.getElementById('unified-status').textContent.includes('保持小鸭的标定姿势'));
  state.sittingCalibration={retry:2,max_retries:3};
  await poll(page,()=>document.getElementById('unified-status').textContent.includes('自动重试（2/3）'));
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  assert.equal(await page.locator('#pose-start').isDisabled(),true);
  assert.equal(await page.locator('#imu-start').isDisabled(),true);
  assert.equal(state.calls.filter(c=>c.path==='/api/cal/sitting').length,1);
  assert.ok(state.releaseBatch);
  state.releaseBatch();
  await poll(page,()=>document.getElementById('unified-status').dataset.state==='error');
  const message=page.locator('#unified-status');
  assert.match(await message.textContent(),/重试 3 次.*把电池充满.*检查舵机线/);
  assert.doesNotMatch(await message.textContent(),/参考位置|STM32/);
  await page.locator('#language').click();
  assert.match(await message.textContent(),/3 automatic retries.*Fully charge the battery.*servo cables/);
  assert.equal(state.saved.size,0);
  state.failBatch=null;
  state.sittingCalibration=null;
  await page.locator('#complete-calibration').click();
  await poll(page,()=>document.getElementById('unified-status').dataset.state==='saved');
  assert.equal(state.saved.size,15);
  assert.equal(state.calls.filter(c=>c.path==='/api/cal/sitting').length,2);
});

test('status failure disables unified calibration until fresh feedback returns',async t=>{
  const {page,state}=await setup(t);
  await batchReady(page);
  state.statusDown=true;
  await poll(page,()=>document.getElementById('message').textContent.includes('读不到'));
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  await page.locator('#complete-calibration').dispatchEvent('click');
  assert.equal(state.saved.size,0);
  state.statusDown=false;
  await batchReady(page);
  await page.locator('#complete-calibration').click();
  await poll(page,()=>document.getElementById('unified-status').dataset.state==='saved');
});

test('exit releases a pending batch even if an older status or write reply arrives',async t=>{
  const {page,state}=await setup(t);
  await batchReady(page);
  state.delayStatus=true;
  for(let i=0;i<100&&!state.releaseStatus;i++)await page.waitForTimeout(20);
  assert.ok(state.releaseStatus,'a status request must be pending');
  state.delayBatch=true;
  await page.locator('#complete-calibration').click();
  for(let i=0;i<100&&!state.releaseBatch;i++)await page.waitForTimeout(20);
  assert.ok(state.releaseBatch,'a batch request must be pending');
  await page.locator('#exit').click();
  await poll(page,()=>document.getElementById('message').textContent.includes('已关闭'));
  state.releaseStatus();
  state.releaseBatch();
  await page.waitForTimeout(300);
  assert.equal(state.mode,'shadow');
  assert.equal(state.saved.size,0);
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  assert.notEqual(await page.locator('#unified-status').getAttribute('data-state'),'saved');
});

test('hiding the calibration page unloads without restarting on return',async t=>{
  const {page,state}=await setup(t);
  await page.locator('#complete-calibration').click();
  await poll(page,()=>document.getElementById('unified-status').dataset.state==='saved');
  await page.evaluate(()=>{
    Object.defineProperty(document,'hidden',{configurable:true,value:true});
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await poll(page,()=>document.getElementById('message').textContent.includes('已关闭'));
  assert.equal(state.mode,'shadow');
  assert.equal(state.saved.size,15);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForTimeout(350);
  assert.equal(state.mode,'shadow');
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
});
