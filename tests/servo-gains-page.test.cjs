// Exercise the real Servo setup page against read-only register fixtures.
// Run: node --test tests/servo-gains-page.test.cjs (requires Playwright + Chrome).
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {readFile}=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});});
after(async()=>{await browser?.close();});

async function setup(t,viewport={width:1100,height:900},locale='en-US',control=false){
  const state={mode:'shadow',calls:[],failSaved:false,failRuntime:false,invalidRuntime:false,failWrite:false,
    servos:{0:{saved:[5,10],runtime:[7,19]},1:{saved:[9,23],runtime:[9,23]}},
    protection:{0:{current:1.5015,voltage:true,overcurrent:true},1:{current:1.5015,voltage:true,overcurrent:true},
      10:{current:1.001,voltage:true,overcurrent:true},31:{current:1.5015,voltage:false,overcurrent:false}},
    servoIds:[10,11,12,13,14,20,21,22,23,24,30,31,32,33], enabled:false, imuCalibrated:true,
    lowThresholds:[],testSession:null, testOffsetLimit:30};
  const context=await browser.newContext({viewport,locale});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  t.after(async()=>{await context.close();assert.deepEqual(errors,[]);});
  await context.route('http://duck-gains.test/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!url.pathname.startsWith('/api/')){
      const file=path.join(__dirname,'../assets',url.pathname==='/'?'index.html':url.pathname);
      const contentType={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png',
        '.svg':'image/svg+xml','.json':'application/json'}[path.extname(file)];
      return route.fulfill({status:200,body:await readFile(file),contentType});
    }
    const body=request.postDataJSON()||{},action=body.action,id=body.target_id;
    state.calls.push({...body,path:url.pathname});
    const reply=(json,status=200)=>route.fulfill({status,json});
    if(url.pathname==='/api/status')return reply({mode:state.mode,enabled:state.enabled,mcu_servo_debug:state.mode==='servo_debug',feedback_age_ms:1,servo_scan_version:2,
      servo_ids:state.servoIds,imu_ok:true,imu_calibration:{calibrated:state.imuCalibrated,state:'idle'},
      feedback_hz:100,inference_hz:50,control_ready:true,deployment:{gyro_alpha:.5,gravity_tau_s:.3}});
    if(url.pathname==='/api/servo/gain-check')return reply({state:'checked',mismatches:[{id:22,kp:7,kd:9}]});
    if(url.pathname==='/api/servo/overcurrent-check')return reply({state:'checked',checked_ids:state.servoIds,low_thresholds:state.lowThresholds});
    if(url.pathname==='/api/command')return reply({ok:true});
    if(url.pathname==='/api/version')return reply({version:'0.3.4'});
    if(url.pathname==='/api/servo/enter'){state.mode='servo_debug';return reply({ok:true});}
    if(url.pathname==='/api/servo/exit'){state.mode='shadow';state.testSession=null;return reply({ok:true});}
    if(url.pathname==='/api/servo/test'){
      if(state.mode!=='servo_debug')return reply({detail:'Servo setup is not ready for the position test.'},400);
      if(action==='start'){
        state.testSession={id,session:123};
        return reply({ok:true,target_id:id,session:123,raw_pos:2048,offset_limit_deg:state.testOffsetLimit,
          idle_timeout_s:state.testIdleTimeout});
      }
      const same=state.testSession?.id===id&&state.testSession.session===body.session;
      if(action==='stop'){
        if(same)state.testSession=null;
      }else{
        if(!same)return reply({detail:'servo operation failed'},400);
        if(state.testIdleExpired){
          state.testSession=null;
          return reply({detail:'Servo unloaded after 10 seconds without an angle change.'},400);
        }
      }
      return reply({ok:true,target_id:id,session:body.session,raw_pos:2048});
    }
    if(action==='torque_off')return reply({ok:true});
    if(action==='ping')return reply({op:7,target_id:id,ok:id in state.servos});
    if(['read_protection','set_voltage_protection','set_overcurrent_protection'].includes(action)){
      const settings=state.protection[id];
      if(!settings)return reply({detail:'servo operation failed'},400);
      if(action==='set_voltage_protection')settings.voltage=body.protection_enabled;
      if(action==='set_overcurrent_protection')settings.overcurrent=body.protection_enabled;
      return reply({op:action==='read_protection'?16:17,target_id:id,ok:true,current_a:settings.current,
        voltage_protection:settings.voltage,overcurrent_protection:settings.overcurrent});
    }
    if(action==='set_protection_current'){
      const current=Math.round(body.current_a/.0065)*.0065;
      for(const settings of Object.values(state.protection))settings.current=current;
      return reply({op:10,target_id:254,ok:true,broadcast:true,current_a:current});
    }
    if(action==='set_gains'){
      if(state.failWrite)return reply({detail:'Gain write failed'},400);
      state.servos[id]={saved:[body.kp,body.kd],runtime:[body.kp,body.kd]};
      return reply({op:4,target_id:id,ok:true});
    }
    if(action==='read'||action==='read_runtime_gains'){
      const saved=action==='read';
      if(saved?state.failSaved:state.failRuntime)return reply({detail:'servo reply timeout'},400);
      const [kp,kd]=state.servos[id][saved?'saved':'runtime'];
      return reply({op:saved?6:12,target_id:id,ok:true,raw_pos:2047,kp:!saved&&state.invalidRuntime?null:kp,kd});
    }
    return reply({detail:'Unexpected operation'},400);
  });
  await page.goto('http://duck-gains.test/'+(control?'':'servo.html'));
  if(control)return {page,state};
  await page.waitForFunction(()=>!document.getElementById('scan').disabled);
  await page.click('#scan');
  await page.locator('#scan-results button[data-id="1"]').waitFor();
  await page.click('#scan-stop');
  await page.waitForFunction(()=>!document.getElementById('scan').disabled);
  state.calls=[];
  return {page,state};
}
async function select(page,id=0){
  await page.click(`#scan-results button[data-id="${id}"]`);
  await page.waitForFunction(()=>!document.getElementById('read').disabled);
}
async function read(page){
  await page.click('#read');
  await page.waitForFunction(()=>!document.getElementById('read').disabled);
}
async function values(page){
  return page.locator('.servo-gains tbody td').allTextContents();
}

test('Protection lists each servo and distinguishes mixed settings, missing replies, and saved thresholds',async t=>{
  const {page,state}=await setup(t,{width:390,height:844});
  state.servoIds=[10,31,32];
  await page.reload();
  await page.locator('#scan:enabled').waitFor();
  assert.equal(await page.locator('#protection-current').inputValue(),'2');
  const voltage=page.getByRole('group',{name:'Voltage protection'});
  const current=page.getByRole('group',{name:'Overcurrent protection'});
  const table=page.locator('#protection-readings');
  const rows=()=>table.locator('tbody tr').evaluateAll(trs=>trs.map(tr=>Array.from(tr.cells,cell=>cell.textContent)));
  assert.equal(await voltage.locator('#voltage-protection-out').innerText(),'1 enabled · 1 disabled · 1 unread');
  assert.equal(await current.locator('#overcurrent-protection-out').innerText(),'1 enabled · 1 disabled · 1 unread');
  assert.equal(await table.getByRole('columnheader',{name:'Saved threshold (A)'}).count(),1);
  assert.deepEqual(await rows(),[
    ['10','Enabled','Enabled','1.001'],
    ['31','Disabled','Disabled','1.5015'],
    ['32','Read failed'],
  ]);
  assert.ok(await table.evaluate(el=>el.offsetWidth<=el.parentElement.clientWidth));
  await page.click('#language');
  assert.equal(await page.locator('#voltage-protection-out').innerText(),'1 台启用 · 1 台禁用 · 1 台未读到');
  assert.deepEqual(await rows(),[
    ['10','已启用','已启用','1.001'],
    ['31','已禁用','已禁用','1.5015'],
    ['32','未读到'],
  ]);
  await page.click('#language');
  assert.deepEqual(state.calls.filter(call=>call.action).map(call=>call.action),Array(3).fill('read_protection'));
  await current.getByRole('button',{name:'Enable overcurrent protection'}).click();
  await page.locator('#scan:enabled').waitFor();
  assert.equal(await current.locator('#overcurrent-protection-out').innerText(),'2 enabled · 1 unread');
  assert.equal(await voltage.locator('#voltage-protection-out').innerText(),'1 enabled · 1 disabled · 1 unread');
  assert.match(await page.locator('#protection-message').innerText(),/could not be verified for ID 32/);
  await voltage.getByRole('button',{name:'Disable voltage protection'}).click();
  await page.locator('#scan:enabled').waitFor();
  assert.equal(await voltage.locator('#voltage-protection-out').innerText(),'2 disabled · 1 unread');
  assert.equal(await current.locator('#overcurrent-protection-out').innerText(),'2 enabled · 1 unread');
  assert.deepEqual(state.calls.filter(call=>call.action?.startsWith('set_')).map(call=>call.target_id),[10,31,32,10,31,32]);
  assert.deepEqual(await table.locator('td.protection-threshold').allTextContents(),['1.001','1.5015']);
  await page.click('#set-protection-current');
  await page.locator('#scan:enabled').waitFor();
  assert.deepEqual(await table.locator('td.protection-threshold').allTextContents(),['2.002','2.002']);
  assert.equal(state.calls.filter(call=>call.action==='set_protection_current').length,1);
  state.servoIds=[];
  await page.reload();
  await page.locator('#scan:enabled').waitFor();
  assert.equal(await table.isVisible(),false);
  assert.equal(await page.locator('#protection-readings-empty').innerText(),'No servos detected.');
});

test('Control checks gains after all 15 are online and reports missing IMU calibration',async t=>{
  const {page,state}=await setup(t,{width:1180,height:850},'zh-CN',true);
  await page.waitForFunction(()=>document.getElementById('servos').textContent==='14/15');
  assert.equal(state.calls.filter(call=>call.path==='/api/servo/gain-check').length,0);
  assert.equal(await page.locator('#startup-check').isVisible(),false);
  state.enabled=true;
  state.servoIds.push(34);
  await page.waitForFunction(()=>document.getElementById('servos').textContent==='15/15');
  assert.equal(state.calls.filter(call=>call.path==='/api/servo/gain-check').length,0);
  state.enabled=false;
  state.imuCalibrated=false;
  await page.locator('#gain-check-warning').waitFor();
  assert.match(await page.locator('#gain-check-text').innerText(),/ID 22 \(7 \/ 9\)/);
  assert.equal(await page.locator('#gain-check-warning a').getAttribute('href'),'/servo.html');
  assert.match(await page.locator('#imu-check-text').innerText(),/尚未保存标定/);
  assert.equal(await page.locator('#imu-check-warning a').getAttribute('href'),'/calibrate.html');
  assert.equal(state.calls.filter(call=>call.path==='/api/servo/gain-check').length,1);
  assert.ok(state.calls.every(call=>call.path!=='/api/imu-calibration'&&call.path!=='/api/servo'));
});

test('Control warns about low current thresholds with only some servos online',async t=>{
  const {page,state}=await setup(t,{width:1180,height:850},'zh-CN',true);
  state.servoIds=[10,31,32];
  state.lowThresholds=[{id:10,current_a:1.001},{id:31,current_a:1.5015}];
  state.enabled=true;
  await page.waitForFunction(()=>document.getElementById('servos').textContent==='3/15');
  const requested=()=>state.calls.filter(call=>call.path==='/api/servo/overcurrent-check');
  assert.equal(requested().length,0);
  assert.equal(await page.locator('#startup-check').isVisible(),false);
  state.enabled=false;
  const warning=page.locator('#overcurrent-check-warning');
  await warning.waitFor();
  assert.match(await warning.innerText(),/ID 10 \(1\.001 A\)、ID 31 \(1\.5015 A\)。建议设为 2 A/);
  assert.doesNotMatch(await warning.innerText(),/ID 32/);
  assert.equal(await warning.locator('a').getAttribute('href'),'/servo.html#servo-protection');
  assert.equal(requested().length,1);
  assert.ok(state.calls.every(call=>call.path!=='/api/servo'&&call.path!=='/api/servo/enter'));
  state.lowThresholds=[];
  const recheck=page.waitForResponse(response=>response.url().endsWith('/api/servo/overcurrent-check'));
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
  await recheck;
  await warning.waitFor({state:'hidden'});
  assert.equal(requested().length,2);
});

test('Installation check shows the selected joint and only jogs the enabled ID',async t=>{
  const {page,state}=await setup(t);
  await page.waitForFunction(()=>document.querySelectorAll('#servo-test-id option').length===15);
  await page.selectOption('#servo-test-id','22');
  assert.match(await page.locator('#servo-test-caption').innerText(),/Right leg · ID 22 · Hip pitch/);
  assert.equal(await page.locator('#servo-test-image').getAttribute('src'),'cal-22-pose.svg');
  assert.equal(await page.locator('#servo-test-offset').isDisabled(),true);
  assert.equal(state.calls.some(call=>call.path==='/api/servo/test'),false);
  await page.click('#servo-test-start');
  await page.locator('#servo-test-offset:enabled').waitFor();
  assert.equal(await page.locator('#scan').isDisabled(),true);
  const slider=page.locator('#servo-test-offset');
  for(const [key,offset] of [['End',30],['Home',-30]]){
    const moved=page.waitForResponse(response=>response.url().endsWith('/api/servo/test')&&response.request().postDataJSON()?.offset_deg===offset);
    await slider.press(key);
    await moved;
  }
  assert.ok(state.calls.filter(call=>call.path==='/api/servo/test').every(call=>call.target_id===22));
  const stopped=page.waitForResponse(response=>response.url().endsWith('/api/servo/test')&&response.request().postDataJSON()?.action==='stop');
  await page.click('#servo-test-stop');
  await stopped;
  assert.equal(state.testSession,null);
  assert.equal(await page.locator('#servo-test-offset').isDisabled(),true);

  state.testOffsetLimit=undefined;
  await page.click('#servo-test-start');
  await page.locator('#servo-test-offset:enabled').waitFor();
  assert.equal(await slider.getAttribute('min'),'-10');
  assert.equal(await slider.getAttribute('max'),'10');
});

test('Installation check survives a visible window losing focus and unloads when hidden',async t=>{
  const {page,state}=await setup(t);
  await page.click('#servo-test-start');
  const slider=page.locator('#servo-test-offset');
  await page.locator('#servo-test-offset:enabled').waitFor();
  await slider.scrollIntoViewIfNeeded();
  const box=await slider.boundingBox(), y=box.y+box.height/2;
  const dragTo=async (start,end)=>{
    await page.mouse.move(box.x+box.width*start,y);
    await page.mouse.down();
    await page.mouse.move(box.x+box.width*end,y,{steps:5});
    await page.mouse.up();
  };
  await dragTo(.5,.8);
  await page.evaluate(()=>{
    if(document.hidden)throw new Error('Expected the servo page to be visible');
    window.dispatchEvent(new Event('blur'));
  });
  const moved=page.waitForResponse(response=>response.url().endsWith('/api/servo/test')&&response.request().postDataJSON()?.offset_deg<0,{timeout:3500});
  await dragTo(.8,.2);
  await moved;
  assert.equal(await slider.isEnabled(),true);
  assert.deepEqual(state.calls.filter(call=>call.path==='/api/servo/test'&&call.action!=='move').map(call=>call.action),['start']);

  const exited=page.waitForResponse(response=>response.url().endsWith('/api/servo/exit'));
  await page.evaluate(()=>{
    Object.defineProperty(document,'hidden',{configurable:true,value:true});
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await exited;
  await page.locator('#servo-test-offset:disabled').waitFor();
  assert.match(await page.locator('#servo-test-status').innerText(),/tab was hidden/);
  assert.equal(state.mode,'shadow');
  assert.equal(state.testSession,null);
});

test('Installation check shows automatic unload and waits for an explicit restart',async t=>{
  const {page,state}=await setup(t);
  state.testIdleTimeout=10;
  const status=page.locator('#servo-test-status');
  const start=page.locator('#servo-test-start');
  await start.click();
  await page.locator('#servo-test-offset:enabled').waitFor();
  assert.match(await status.innerText(),/unloads after 10 seconds without an angle change/);

  state.testIdleExpired=true;
  await page.getByText('Servo unloaded after 10 seconds without an angle change.',{exact:true}).waitFor();
  assert.equal(await page.locator('#servo-test-offset').isDisabled(),true);
  assert.equal(await page.locator('#servo-test-stop').isDisabled(),true);
  await page.click('#language');
  assert.equal(await status.innerText(),'10 秒未调整角度，已自动卸力。');
  assert.equal(state.calls.filter(call=>call.path==='/api/servo/test'&&call.action==='start').length,1);

  state.testIdleExpired=false;
  await start.click();
  await page.locator('#servo-test-offset:enabled').waitFor();
  assert.match(await status.innerText(),/10 秒不调整角度会自动卸力/);
  await page.click('#servo-test-stop');
  await page.locator('#servo-test-offset:disabled').waitFor();
});

test('Read displays both register banks, distinguishes a mismatch and never writes gains',async t=>{
  const {page,state}=await setup(t);
  await select(page);
  assert.deepEqual(await values(page),['5','10','7','19']);
  assert.equal(await page.locator('#gains-status').getAttribute('data-state'),'different');
  assert.match(await page.locator('#read-out').textContent(),/ID 0 · Pos 2047/);
  assert.deepEqual(state.calls.filter(call=>call.path==='/api/servo').map(call=>call.action),['read','read_runtime_gains']);
  await page.fill('#kp','11');await page.fill('#kd','21');
  await read(page);
  assert.equal(await page.inputValue('#kp'),'11');assert.equal(await page.inputValue('#kd'),'21');
  await select(page,1);
  assert.deepEqual(await values(page),['9','23','9','23']);
  assert.equal(await page.locator('#gains-status').getAttribute('data-state'),'match');
});

test('Timeouts and malformed replies never reuse old readings or turn them into zeros',async t=>{
  const {page,state}=await setup(t);
  await select(page);
  state.failRuntime=true;await read(page);
  assert.deepEqual(await values(page),['5','10','—','—']);
  assert.equal(await page.locator('#gains-status').getAttribute('data-state'),'incomplete');
  assert.match(await page.locator('#message').textContent(),/Runtime read failed: servo reply timeout/);
  state.failRuntime=false;state.failSaved=true;await read(page);
  assert.deepEqual(await values(page),['—','—','7','19']);
  assert.equal(await page.locator('#read-out').textContent(),'—');
  state.failSaved=false;state.invalidRuntime=true;await read(page);
  assert.deepEqual(await values(page),['5','10','—','—']);
  assert.match(await page.locator('#message').textContent(),/Invalid gain reply/);
});

test('Set KpKd refreshes both banks and distinguishes write success from readback failure',async t=>{
  const {page,state}=await setup(t);
  await select(page);
  await page.fill('#kp','12');await page.fill('#kd','24');
  await page.click('#set-gains');
  await page.waitForFunction(()=>!document.getElementById('read').disabled);
  assert.deepEqual(await values(page),['12','24','12','24']);
  assert.match(await page.locator('#message').textContent(),/KP\/KD written and read back/);
  state.failRuntime=true;await page.click('#set-gains');
  await page.waitForFunction(()=>!document.getElementById('read').disabled);
  assert.deepEqual(await values(page),['12','24','—','—']);
  assert.match(await page.locator('#message').textContent(),/KP\/KD written\. Runtime read failed/);
  state.failWrite=true;await page.click('#set-gains');
  await page.waitForFunction(()=>!document.getElementById('read').disabled);
  assert.deepEqual(await values(page),['—','—','—','—']);
  assert.match(await page.locator('#message').textContent(),/Gain write failed/);
});

test('Both value rows fit a narrow phone viewport',async t=>{
  const {page}=await setup(t,{width:375,height:812},'fr-FR');
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.locator('#scan').textContent(),'Scan all IDs');
  await select(page);
  const box=await page.locator('.servo-gains').boundingBox();
  assert.ok(box.x>=0&&box.x+box.width<=375,JSON.stringify(box));
  assert.deepEqual(await values(page),['5','10','7','19']);
});

test('Chinese locale translates navigation, scan progress and runtime readback feedback',async t=>{
  const {page,state}=await setup(t,{width:320,height:844},'zh-TW');
  const restartHint=page.locator('#gains-restart-hint');
  assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
  assert.deepEqual(await page.locator('.nav a').allTextContents(),['控制','标定','舵机设置']);
  assert.equal(await restartHint.textContent(),'写入后断电重启，舵机会加载保存的 KP/KD。');
  assert.equal(await page.locator('#scan').textContent(),'扫描所有 ID');
  assert.equal(await page.locator('#scan-progress').getAttribute('aria-label'),'舵机扫描进度');
  assert.match(await page.locator('#scan-status').textContent(),/^已停止.*找到 2 个/);
  await select(page);
  assert.deepEqual(await values(page),['5','10','7','19']);
  assert.match(await page.locator('#gains-status').textContent(),/当前使用运行值/);
  state.failRuntime=true;
  await read(page);
  assert.deepEqual(await values(page),['5','10','—','—']);
  assert.match(await page.locator('#message').textContent(),/读取运行值失败。等待舵机指令回包超时/);
  const servoCommands=state.calls.filter(call=>call.path==='/api/servo').length;
  await page.getByRole('button',{name:'Switch to English'}).click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await restartHint.textContent(),'After writing, power-cycle the Duck to reload the saved KP/KD.');
  assert.match(await page.locator('#scan-status').textContent(),/^Stopped.*2 found/);
  assert.match(await page.locator('#message').textContent(),/Runtime read failed: servo reply timeout/);
  assert.equal(await page.locator('#scan-progress').getAttribute('aria-label'),'Servo scan progress');
  assert.deepEqual(await values(page),['5','10','—','—']);
  await page.getByRole('button',{name:'切换为中文'}).click();
  assert.match(await page.locator('#message').textContent(),/读取运行值失败。等待舵机指令回包超时/);
  assert.equal(state.calls.filter(call=>call.path==='/api/servo').length,servoCommands);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
});
