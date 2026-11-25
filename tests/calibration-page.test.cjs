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
  await page.locator('#hardware-check [data-confirm]').click();
  return {...mock,page,context};
}
const poll=(page,fn,arg)=>page.waitForFunction(fn,arg);
const batchReady=page=>poll(page,()=>!document.getElementById('complete-calibration').disabled);
const imuReady=page=>poll(page,()=>!document.getElementById('imu-start').disabled);

test('IMU progress follows the current sampling window in both languages', async t => {
  const {page,state}=await setup(t);
  await imuReady(page);
  const message=page.locator('#imu-status');
  state.imu=true;
  state.imuResult={state:'sampling',phase:'settling',retry:1,max_retries:10,progress:0};
  await message.filter({hasText:'自动重试（1/10）'}).waitFor();
  assert.match(await message.innerText(),/即将开始采样/);
  assert.equal(await page.locator('#imu-start').isDisabled(),true);
  assert.equal(await page.locator('#imu-progress').isHidden(),true);
  state.imuResult.phase='sampling';
  state.imuResult.progress=.5;
  await message.filter({hasText:'已采样 50%'}).waitFor();
  await page.locator('#language').click();
  assert.match(await message.innerText(),/Automatic retry 1\/10.*Sampling 50%/);
  state.imu=false;
  state.imuResult={state:'error',progress:1,calibrated:true,
    message:'Previous calibration kept. No stable IMU readings after 10 automatic retries. Support the board and try again.'};
  await message.filter({hasText:'10 automatic retries'}).waitFor();
  await page.locator('#language').click();
  assert.match(await message.innerText(),/自动重试 10 次.*上一次标定结果已保留/);
  assert.equal(state.calls.filter(c=>c.path==='/api/imu-calibration').length,0);
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
  await page.getByRole('link',{name:'复查舵机的中位标定'}).click();
  assert.equal(new URL(page.url()).hash,'#main-joints-title');
  assert.ok(await page.getByRole('link',{name:'打开舵机设置'}).isVisible());
  await page.locator('#language').click();
  await page.getByRole('link',{name:'Open Servo setup'}).click();
  await page.waitForURL('**/servo.html#servo-workspace');
  assert.ok(await page.locator('#servo-workspace #servo-model').isVisible());
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

test('control, calibration and servo setup share navigation and content bounds',async t=>{
  const {url,page}=await setup(t);
  const baseline=new Map();
  for(const [file,current] of [['index.html','/'],['calibrate.html','/calibrate.html'],['servo.html','/servo.html']]){
    await page.goto(url+'/'+file);
    assert.equal(await page.locator('.nav [aria-current=page]').getAttribute('href'),current);
    assert.equal(await page.locator('#cockpit-link').getAttribute('href'),'http://127.0.0.1/');
    for(const lang of ['zh-CN','en']){
      if(lang==='en')await page.locator('#language').click();
      assert.equal(await page.locator('html').getAttribute('lang'),lang);
      for(const width of [1280,768,390,320]){
        await page.setViewportSize({width,height:900});
        const layout=await page.evaluate(()=>{
          const bounds=el=>{
            const {x,y,width,height}=el.getBoundingClientRect();
            return {x,y,width,height};
          };
          const buttons=[...document.querySelectorAll('.header-actions a, .header-actions button')];
          const {x,width}=bounds(document.querySelector('main > section'));
          return {x,width,buttons:buttons.map(el=>[el.textContent,el.getAttribute('href'),bounds(el)])};
        });
        assert.equal(layout.buttons.length,9);
        for(const [label,,box] of layout.buttons){
          assert.ok(box.x>=layout.x && box.x+box.width<=layout.x+layout.width,label);
        }
        const key=lang+'-'+width;
        if(baseline.has(key))assert.deepEqual(layout,baseline.get(key),file+' '+key);
        else baseline.set(key,layout);
      }
    }
  }
});

test('four steps, reference pictures, and bilingual narrow layout',async t=>{
  const {page,state}=await setup(t);
  await batchReady(page);
  assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
  assert.equal(await page.locator('.nav [aria-current=page]').textContent(),'标定');
  assert.equal(await page.locator('[data-label=unifiedTitle]').textContent(),'统一标定');
  assert.deepEqual(await page.locator('section .cal-step').allTextContents(),['01','02','03','04']);
  assert.equal(await page.locator('#zones,#zonebar,[data-calibration-mode],[data-center]').count(),0);
  assert.doesNotMatch(await page.locator('main').innerText(),/逐个标定|切换标定方式/);
  await page.evaluate(()=>{for(const image of document.images)image.loading='eager';});
  const images=await page.locator('main img').evaluateAll(items=>Promise.all(items.map(async image=>{
    await image.decode();
    return [image.getAttribute('src'),image.naturalWidth,image.naturalHeight];
  })));
  assert.deepEqual(images.map(([url])=>url),['cal-unified-1.png','cal-unified-2.png','cal-unified-3.png','cal-unified-4.png',
    'cal-30-pose.svg','imu-calibration-pose.png','cal-default-pose.jpg']);
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
  assert.equal(await page.locator('#neck-start').textContent(),'Calibrate ID 30');
  assert.equal(await page.locator('#imu-title').textContent(),'IMU calibration');
  assert.equal(await page.locator('#imu-panel figcaption').textContent(),'IMU placement reference');
  assert.equal(await page.locator('#finish-title').textContent(),'Check the default pose');
  for(const width of [1365,390,320]){
    await page.setViewportSize({width,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`no horizontal overflow at ${width}px`);
  }
});

test('single-servo guide opens on demand with the original references and only centers the selected ID',async t=>{
  const {page,state}=await setup(t,{viewport:{width:320,height:844}},s=>{s.mask=1<<7;});
  const toggle=page.locator('#single-toggle'),panel=page.locator('#single-panel');
  assert.equal(await panel.isHidden(),true);
  assert.equal(await panel.locator('img').count(),0);
  await toggle.click();
  const select=panel.locator('select'),start=panel.locator('#single-start');
  await select.waitFor();
  assert.equal(await select.locator('option').count(),15);
  assert.equal(await start.isDisabled(),true);
  for(const [id,group] of [[30,'head'],[12,'legs'],[22,'legs']]){
    await select.selectOption(String(id));
    const pictures=await panel.locator('img').evaluateAll(async images=>Promise.all(images.map(async img=>{
      await img.decode();return new URL(img.src).pathname;
    })));
    assert.ok(pictures.includes('/cal-photo-'+group+'.webp'));
    assert.ok(pictures.includes('/cal-'+id+'-pose.svg'));
    if(id!==30)assert.ok(pictures.includes('/cal-photo-'+id+'.jpg'));
  }
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await panel.locator('[data-picture="cal-photo-22.jpg"]').click();
  await page.locator('#picture-dialog[open]').waitFor();
  await page.keyboard.press('Escape');
  await page.locator('#language').click();
  assert.equal(await select.inputValue(),'22');
  assert.equal(await start.innerText(),'Set center for ID 22');
  assert.equal(state.calls.some(c=>c.path==='/api/cal/center'),false);
  await start.click();
  await panel.locator('#single-status[data-state="saved"]').waitFor();
  assert.deepEqual(state.calls.filter(c=>c.path==='/api/cal/center').map(c=>c.body),[{index:7}]);
  assert.deepEqual([...state.saved],[7]);
  assert.equal(await start.isDisabled(),true);
  await panel.locator('#single-unload').click();
  await start.waitFor({state:'visible'});
  await toggle.click();
  assert.equal(await panel.isHidden(),true);
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  assert.equal(state.calls.some(c=>c.path==='/api/cal/sitting'),false);
});

test('center 30 then 31 in one calibration session while 30 stays held',async t=>{
  const {page,state}=await setup(t);
  await page.locator('#single-toggle').click();
  const select=page.locator('#single-id'),start=page.locator('#single-start');
  await select.selectOption('30');
  const enters=state.calls.filter(c=>c.path==='/api/cal/enter').length;
  await start.click();
  await page.locator('#single-status[data-state=saved]').waitFor();
  await select.selectOption('31');
  await page.locator('#single-start:enabled').waitFor();
  assert.deepEqual(state.held.filter(Boolean),[true]);
  assert.equal(state.held[10],true);
  assert.equal(await page.locator('#imu-start').isDisabled(),true);
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  assert.equal(await page.locator('#pose-start').isDisabled(),true);
  await start.click();
  await page.locator('#single-status[data-state=saved]').filter({hasText:'31 号'}).waitFor();
  assert.deepEqual(state.held.flatMap((held,i)=>held?[i]:[]),[10,11]);
  assert.deepEqual(state.calls.filter(c=>c.path==='/api/cal/center').map(c=>c.body.index),[10,11]);
  assert.equal(state.calls.filter(c=>c.path==='/api/cal/enter').length,enters);
  await select.selectOption('30');
  assert.equal(await start.isDisabled(),true);
  assert.match(await page.locator('#single-status').innerText(),/30 号舵机已标定/);
  await page.locator('#single-unload').click();
  await page.locator('#single-start:enabled').waitFor();
  assert.equal(state.held.some(Boolean),false);
});

test('opening the single-servo guide keeps the pose active and allows retry after a guide load failure',async t=>{
  const {page,state}=await setup(t);
  await page.locator('#pose-start').click();
  state.enabled=true;
  await page.locator('#pose-status[data-state="saved"]').waitFor();
  state.guideFailure='missing';
  await page.locator('#single-toggle').click();
  await page.locator('#single-loading').filter({hasText:'加载失败'}).waitFor();
  assert.equal(await page.locator('#single-guide').isHidden(),true);
  state.guideFailure=null;
  await page.locator('#single-toggle').click();
  await page.locator('#single-toggle').click();
  await page.locator('#single-id').waitFor();
  assert.equal(state.mode,'hold');
  assert.equal(await page.locator('#single-start').isDisabled(),true);
  await page.locator('#single-unload').click();
  await page.locator('#single-start:enabled').waitFor();
  assert.equal(state.calls.some(c=>c.path==='/api/cal/center'),false);
});

test('batch waits for STM32 and all 15 servos; neck calibration unloads before IMU and pose',async t=>{
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

  await page.locator('#neck-start').click();
  await page.locator('#neck-status[data-state=saved]').waitFor();
  assert.equal(await page.locator('#imu-start').isDisabled(),true);
  assert.deepEqual(state.held.map((v,i)=>v?i:-1).filter(i=>i!==-1),[10]);
  await page.locator('#neck-unload').click();
  await imuReady(page);

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
  assert.deepEqual(saves.map(c=>c.path),['/api/cal/sitting','/api/cal/center','/api/imu-calibration','/api/cal/default-pose']);
  assert.deepEqual(saves[1].body,{index:10});
});

test('neck calibration only needs ID 30, checks its reply and ignores a late write after exit',async t=>{
  const {page,state}=await setup(t,{},s=>{s.mask=1;s.imuOk=false;});
  await page.locator('#neck-status').filter({hasText:'正在等待 30 号舵机反馈'}).waitFor();
  assert.equal(await page.locator('#neck-start').isDisabled(),true);
  state.mask=1<<10;
  await page.locator('#neck-encoder').filter({hasText:'2210'}).waitFor();
  assert.equal(await page.locator('#complete-calibration').isDisabled(),true);
  const start=page.locator('#neck-start');
  await page.route('**/api/cal/center',route=>route.fulfill({json:{ok:true,index:9,id:24}}),{times:1});
  await start.click();
  await page.locator('#neck-status[data-state=error]').filter({hasText:'尚未确认'}).waitFor();
  state.failCenter=10;
  await start.click();
  await page.locator('#neck-status[data-state=error]').filter({hasText:'30 号舵机未应答'}).waitFor();
  state.failCenter=null;
  await start.click();
  await page.locator('#neck-status[data-state=saved]').waitFor();
  assert.deepEqual([...state.saved],[10]);
  assert.equal(await start.isDisabled(),true);
  await page.locator('#neck-unload').click();
  state.delayCenter=true;
  await start.click();
  await page.locator('#neck-status').filter({hasText:'正在设置'}).waitFor();
  await page.locator('#exit').click();
  await page.locator('#message').filter({hasText:'已关闭'}).waitFor();
  state.releaseCenter();
  await page.locator('#neck-status').filter({hasText:'标定已关闭'}).waitFor();
  assert.equal(state.held.some(Boolean),false);
  assert.ok(state.calls.filter(c=>c.path==='/api/cal/center').every(c=>c.body.index===10));
  assert.ok(state.calls.every(c=>c.path!=='/api/cal/sitting'));
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
