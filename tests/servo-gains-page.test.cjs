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
      const contentType={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.jpg':'image/jpeg',
        '.svg':'image/svg+xml','.json':'application/json'}[path.extname(file)];
      return route.fulfill({status:200,body:await readFile(file),contentType});
    }
    const body=request.postDataJSON()||{},action=body.action,id=body.target_id;
    state.calls.push({...body,path:url.pathname});
    const reply=(json,status=200)=>route.fulfill({status,json});
    if(url.pathname==='/api/status')return reply({mode:state.mode,enabled:state.enabled,mcu_servo_debug:state.mode==='servo_debug',feedback_age_ms:1,servo_scan_version:2,
      servo_ids:state.servoIds,acc:state.acc,imu_ok:true,imu_calibration:state.imuJob||{calibrated:state.imuCalibrated,state:'idle'},
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
        return reply({ok:true,target_id:id,session:body.session,torque_off:state.testStopConfirmed!==false});
      }else{
        if(!same)return reply({detail:'servo operation failed'},400);
        if(state.testIdleExpired){
          state.testSession=null;
          return reply({detail:'Servo unloaded after 10 seconds without an angle change.'},400);
        }
        if(body.offset_deg===30&&state.holdTestMove)await state.holdTestMove();
      }
      return reply({ok:true,target_id:id,session:body.session,raw_pos:2048});
    }
    if(action==='torque_off')return reply({ok:true});
    if(action==='ping'){
      if(id===state.timeoutId&&state.pingTimeouts>0){
        state.pingTimeouts--;
        return reply({detail:'servo reply timeout'},400);
      }
      return reply({op:7,target_id:id,ok:id in state.servos});
    }
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
      return reply({op:5,target_id:id,ok:true});
    }
    if(action==='set_id'){
      if(state.failIdProtection)return reply({detail:`ID ${body.new_id} is set and the midpoint command was sent. Protection settings were not confirmed. Keep the servo at its midpoint and retry using the same ID.`},400);
      if(state.failIdGains)return reply({detail:`ID ${body.new_id} is set. Protection verified, but KP/KD were not confirmed. Scan for the new ID and check its gains.`},400);
      return reply({op:3,target_id:id,new_id:body.new_id,ok:true,gains:{op:state.wrongGainOp?4:5,target_id:body.new_id,ok:true,kp:5,kd:10},protection:{
        target_id:body.new_id,voltage_protection:true,overcurrent_protection:true,current_raw:308}});
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
  await page.locator('#hardware-check [data-confirm]').click();
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

test('An empty full scan shows the wiring photo and an early stop shows it again',async t=>{
  const {page,state}=await setup(t,{width:390,height:844},'zh-CN');
  const dialog=page.getByRole('dialog',{name:'本次扫描未找到舵机 ID'});
  assert.equal(await dialog.isVisible(),false); // Setup stopped after finding two IDs.
  const help=page.getByRole('button',{name:'扫描帮助'});
  const results=await page.locator('#scan-results').innerText();
  await help.click();
  const manual=page.getByRole('dialog',{name:'扫描帮助'});
  await manual.getByRole('button',{name:'放大接线图'}).waitFor();
  await manual.getByRole('button',{name:'关闭'}).click();
  assert.equal(await page.locator('#scan-results').innerText(),results);
  assert.equal(state.calls.some(call=>call.action==='ping'),false);
  state.servos={};
  state.servoIds=[];
  await page.click('#scan');
  await dialog.waitFor({timeout:60000});
  assert.match(await page.locator('#scan-status').textContent(),/已检查 254 个 ID · 找到 0 个/);
  assert.deepEqual(state.calls.filter(c=>c.action==='ping').map(c=>c.target_id),
    Array.from({length:254},(_,i)=>i));
  const steps=dialog.getByRole('listitem');
  assert.equal(await steps.count(),4);
  assert.match(await steps.nth(0).innerText(),/^请先把电池充满.*1 小时以上/);
  assert.match(await steps.nth(0).innerText(),/8\.4 V 18650 电池组，容量大于 3500 mAh，放电倍率大于 3C/);
  assert.match(await steps.nth(1).innerText(),/^对照下图检查舵机连线/);
  assert.match(await steps.nth(3).innerText(),/联系售后.*您这台设备的实拍照片/);
  const image=dialog.getByRole('img',{name:/电池和右侧舵机/});
  await image.evaluate(img=>img.decode());
  assert.equal(await image.evaluate(img=>img.naturalWidth),1080);
  assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth));
  const enlarge=dialog.getByRole('button',{name:'放大接线图'});
  assert.equal(await enlarge.innerText(),'放大');
  const thumbnail=await image.boundingBox();
  await image.click();
  const viewer=page.getByRole('dialog',{name:'接线大图'});
  const fullPhoto=viewer.getByRole('img',{name:/电池和右侧舵机/});
  await fullPhoto.evaluate(img=>img.decode());
  const enlarged=await fullPhoto.boundingBox();
  assert.ok(enlarged.width>thumbnail.width && enlarged.height>thumbnail.height);
  assert.ok(enlarged.x>=0 && enlarged.x+enlarged.width<=390 && enlarged.y+enlarged.height<=844);
  await viewer.getByRole('button',{name:'关闭大图'}).click();
  assert.equal(await enlarge.evaluate(el=>el===document.activeElement),true);
  await page.setViewportSize({width:1200,height:800});
  assert.match(await steps.nth(1).innerText(),/^对照右图检查舵机连线/);
  await enlarge.getByText('放大',{exact:true}).click();
  await viewer.waitFor();
  await page.keyboard.press('Escape');
  await viewer.waitFor({state:'hidden'});
  assert.equal(await dialog.isVisible(),true);
  await dialog.getByRole('button',{name:'关闭'}).click();
  await page.locator('#scan:enabled').waitFor();
  await page.click('#language');
  state.timeoutId=0;
  state.pingTimeouts=Infinity;
  await page.click('#scan');
  await page.locator('#message').filter({hasText:'retry ID 0'}).waitFor();
  await page.click('#scan-stop');
  const stopped=page.getByRole('dialog',{name:'No servo IDs found in this scan'});
  await stopped.waitFor();
  assert.match(await stopped.getByRole('listitem').nth(0).innerText(),/8\.4 V 18650 battery pack.*greater than 3500 mAh.*greater than 3C/);
  assert.match(await stopped.getByRole('listitem').nth(1).innerText(),/^Use the photo on the right to check/);
  assert.match(await stopped.getByRole('listitem').nth(3).innerText(),/send a photo of your own device/);
  assert.match(await page.locator('#scan-status').textContent(),/^Stopped · 0\/254 checked/);
  await page.keyboard.press('Escape');
  assert.equal(await stopped.isVisible(),false);
});

test('Leaving servo setup with no scan results does not open scan help',async t=>{
  const {page,state}=await setup(t);
  state.servos={};
  state.timeoutId=0;
  state.pingTimeouts=Infinity;
  await page.click('#scan');
  await page.locator('#message').filter({hasText:'retry ID 0'}).waitFor();
  await page.click('#exit');
  await page.locator('#message').filter({hasText:'Servo setup closed'}).waitFor();
  assert.equal(await page.locator('#scan-empty').isVisible(),false);
});

test('Scan resumes on the same ID after a short timeout and completes without duplicates',async t=>{
  const {page,state}=await setup(t);
  state.timeoutId=1;
  state.pingTimeouts=1;
  await page.click('#scan');
  await page.locator('#message').filter({hasText:'retry ID 1'}).waitFor();
  await page.locator('#scan-status').filter({hasText:'Scan complete'}).waitFor({timeout:60000});
  assert.equal(await page.locator('#scan-status').innerText(),'Scan complete · 254 IDs checked · 2 found');
  assert.deepEqual(await page.locator('#scan-results button').allTextContents(),['ID 0','ID 1']);
  assert.equal(await page.locator('#scan-empty').isVisible(),false);
  const probes=state.calls.filter(c=>c.action==='ping').map(c=>c.target_id);
  assert.deepEqual(probes,[0,1,1,...Array.from({length:252},(_,i)=>i+2)]);
});

test('Persistent scan timeout keeps partial results and stops at the unconfirmed ID',async t=>{
  const {page,state}=await setup(t);
  state.timeoutId=1;
  state.pingTimeouts=Infinity;
  await page.click('#scan');
  await page.locator('#message').filter({hasText:'Communication did not recover'}).waitFor();
  assert.match(await page.locator('#scan-status').innerText(),/1\/254 checked · 1 found/);
  assert.deepEqual(await page.locator('#scan-results button:enabled').allTextContents(),['ID 0']);
  assert.deepEqual(state.calls.filter(c=>c.action==='ping').map(c=>c.target_id),[0,1,1,1,1]);
});

test('Scan waits for a reconnect during setup before probing the first address',async t=>{
  const {page,state}=await setup(t);
  await page.route('**/api/servo',route=>{
    if(route.request().postDataJSON()?.action!=='torque_off')return route.fallback();
    return route.fulfill({status:400,json:{detail:'STM32 Bridge is reconnecting. 运动通信恢复中，请稍后重试。'}});
  },{times:1});
  await page.click('#scan');
  await page.locator('#message').filter({hasText:'Waiting for communication to start the scan'}).waitFor();
  assert.equal(state.calls.some(c=>c.action==='ping'),false);
  await page.locator('#scan-results button[data-id="1"]').waitFor();
  await page.locator('#scan:enabled').waitFor();
  assert.deepEqual(state.calls.filter(c=>c.action==='ping').slice(0,2).map(c=>c.target_id),[0,1]);
});

test('Stop during scan recovery cancels the pending retry',async t=>{
  const {page,state}=await setup(t);
  state.timeoutId=1;
  state.pingTimeouts=Infinity;
  await page.click('#scan');
  await page.locator('#message').filter({hasText:'retry ID 1'}).waitFor();
  await page.click('#scan-stop');
  await page.locator('#scan:enabled').waitFor();
  assert.match(await page.locator('#scan-status').innerText(),/^Stopped · 1\/254 checked/);
  assert.deepEqual(state.calls.filter(c=>c.action==='ping').map(c=>c.target_id),[0,1]);
});

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

test('Voltage help opens from the keyboard without sending motion shortcuts',async t=>{
  const {page,state}=await setup(t,{width:390,height:844},'zh-CN',true);
  const help=page.getByRole('button',{name:'电压说明'});
  for(const width of [390,1180]){
    await page.setViewportSize({width,height:844});
    const label=await page.locator('.voltage-heading small').boundingBox();
    const button=await help.boundingBox();
    const gap=button.x-(label.x+label.width);
    assert.ok(gap>=0 && gap<=8 && button.y<label.y+label.height && label.y<button.y+button.height);
  }
  await help.focus();
  await page.keyboard.press('Space');
  const dialog=page.getByRole('dialog',{name:'电压说明'});
  await dialog.waitFor();
  assert.match(await dialog.innerText(),/7\.7、7\.8 和 7\.9 V，页面就显示中间的 7\.8 V/);
  assert.match(await dialog.innerText(),/暂时没有读到舵机电压/);
  await page.keyboard.press('w');
  await page.keyboard.press('Escape');
  await dialog.waitFor({state:'hidden'});
  assert.equal(await help.evaluate(el=>el===document.activeElement),true);
  assert.ok(state.calls.every(call=>call.mode!=='off'&&!call.twist?.some(Boolean)));
  await page.click('#language');
  await page.getByRole('button',{name:'About the voltage reading'}).click();
  await page.getByRole('dialog',{name:'About the voltage reading'}).getByRole('button',{name:'Close'}).click();
});

test('Control shows live IMU data and locks motion during calibration on the dedicated page',async t=>{
  const {page,state}=await setup(t,{width:1180,height:850},'en-US',true);
  const mode=page.locator('[data-mode="shadow"]');
  assert.equal(await page.locator('.imu-calibration, #imu-calibrate, #imu-reset').count(),0);
  state.imuJob={active:true,state:'sampling'};
  state.acc=[0,0,-9.8];
  await page.locator('#acc-values').filter({hasText:'0.000 / 0.000 / -9.800'}).waitFor();
  assert.equal(await mode.isDisabled(),true);
  assert.equal(await page.locator('#head-controls, #head0, #mouth').count(),0);
  state.imuJob={active:false,state:'idle',calibrated:true};
  await page.locator('[data-mode="shadow"]:enabled').waitFor();
  assert.equal(await page.locator('#head-alpha').isVisible(),true);
  assert.ok(state.calls.every(call=>call.path!=='/api/imu-calibration'));
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

test('Single-servo motion control sits below joint selection and only jogs the enabled ID',async t=>{
  const {page,state}=await setup(t);
  const motion=page.locator('#joint-groups + .servo-test');
  assert.equal(await motion.locator('h3').innerText(),'Single-servo motion control');
  const area=await motion.boundingBox();
  const settings=await page.locator('.servo-controls').boundingBox();
  assert.ok(area.x+area.width<=settings.x);
  assert.equal(await page.locator('#servo-test-start').isDisabled(),true);
  await page.locator('#joint-groups button[data-id="12"]').click();
  await page.locator('#servo-model[data-selected="12"]').waitFor();
  await page.locator('#joint-groups button[data-id="22"]').click();
  await page.locator('#servo-model[data-selected="22"]').waitFor();
  assert.equal(await page.locator('#joint-groups button').count(),15);
  assert.equal(await page.locator('#current-joint').innerText(),'ID 22 · Right leg · Hip pitch');
  assert.equal(await page.locator('#set-id').isDisabled(),true);
  assert.equal(await page.locator('#servo-test-offset').isDisabled(),true);
  assert.equal(state.calls.some(call=>call.path==='/api/servo/test'),false);
  await page.click('#servo-test-start');
  await page.locator('#servo-test-offset:enabled').waitFor();
  assert.equal(await page.locator('#scan').isDisabled(),true);
  assert.equal(await page.locator('#joint-groups button:enabled, #scan-results button:enabled').count(),0);
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

test('Scan and model share one target, including factory IDs with no assigned joint',async t=>{
  const {page,state}=await setup(t);
  state.servos[11]={saved:[5,10],runtime:[5,10]};
  await page.click('#scan');
  await page.locator('#scan-results button[data-id="11"]').waitFor();
  await page.click('#scan-stop');
  await select(page,11);
  await page.locator('#servo-model[data-selected="11"]').waitFor();
  assert.equal(await page.locator('#joint-groups [data-id="11"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#servo-test-start').isEnabled(),true);
  await page.locator('#joint-groups [data-id="34"]').click();
  await page.locator('#servo-model[data-selected="34"]').waitFor();
  assert.equal(await page.locator('#scan-results [aria-pressed=true]').count(),0);
  await page.locator('#joint-groups [data-id="11"]').click();
  await page.locator('#set-id:enabled').waitFor();
  assert.equal(await page.locator('#scan-results [data-id="11"]').getAttribute('aria-pressed'),'true');
  await select(page,1);
  await page.locator('#servo-model[data-selected=""]').waitFor();
  assert.equal(await page.locator('#current-joint').innerText(),'ID 1');
  assert.equal(await page.locator('#servo-test-start').isDisabled(),true);
  assert.equal(await page.locator('#set-id').isEnabled(),true);
  assert.equal(state.calls.some(call=>call.path==='/api/servo/test'),false);
});

test('Assembly IDs point inside one physical servo case each, with feet and beak unassigned',async()=>{
  const model=JSON.parse(await readFile(path.join(__dirname,'../assets/servo-model.json'),'utf8'));
  // Case centres in mm in the zero-pose assembly. Rows match the numbered photo
  // (inner hip pair, outer upper pair, lower leg; lower/upper neck and three inside the head).
  const centers={10:[12.5,17.5,-11.5],11:[20,17.5,-48],12:[10.5,54.5,-40.5],13:[-17.7,54.5,-55],
    14:[-17.7,50,-97],30:[41,0,17],31:[41,0,52],32:[41,-7.5,93.2],33:[8.6,-7.5,93.2],34:[48.5,26,96.1]};
  for (let id=10;id<=14;id++) {
    const [x,y,z]=centers[id];
    centers[id+10]=[x,-y,z];
  }
  const cases=model.parts.filter(part=>part.id!==null);
  assert.equal(cases.length,15);
  assert.deepEqual(Object.keys(model.joints).sort(),Object.keys(centers).sort());
  for(const [key,coords] of Object.entries(centers)){
    const id=Number(key);
    const [part]=cases.filter(p=>p.id===id);
    assert.equal(part.surface,'servo',key);
    for(let axis=0;axis<3;axis++){
      const v=part.vertices.filter((_,i)=>i%3===axis).map(n=>n/10);
      const pos=model.joints[key].center[axis]/10;
      assert.ok(Math.abs(pos-coords[axis])<0.25,`ID ${id}, axis ${axis}`);
      assert.ok(Math.min(...v)<pos && pos<Math.max(...v),`ID ${id} marker outside housing`);
    }
  }
  for(const p of model.parts.filter(p=>p.surface!=='servo')) assert.equal(p.id,null);
});

test('Installation diagram follows the selected housing and clears for an unassigned ID',async t=>{
  const {page}=await setup(t);
  const guide=page.locator('#joint-pose-guide'), image=guide.locator('img');
  assert.equal(await guide.isVisible(),false);
  await page.locator('#joint-groups [data-id="14"]').click();
  await guide.locator('summary').click();
  for(const id of [14,24,34]){
    await page.locator('#joint-groups [data-id="'+id+'"]').click();
    await image.evaluate(img=>img.decode());
    assert.equal(await image.getAttribute('src'),`cal-${id}-pose.svg`);
    assert.match(await image.getAttribute('alt'),new RegExp('ID '+id+' '));
  }
  await select(page,1);
  assert.equal(await guide.isVisible(),false);
  assert.equal(await guide.evaluate(el=>el.open),false);
});

test('Picking and rotating the CAD mesh does not start a test or change the target during a test',async t=>{
  const {page,state}=await setup(t);
  const canvas=page.locator('#servo-model');
  await page.waitForFunction(()=>document.querySelector('#servo-model').dataset.selected==='');
  async function clickJoint(id) {
    const position=await page.evaluate(id=>{
      const {point:[x,y]}=jointModel.markers.find(marker=>marker.id===id);
      return {x,y};
    },id);
    await canvas.click({position});
  }
  await clickJoint(34);
  await page.locator('#servo-model[data-selected="34"]').waitFor();
  const before=await canvas.evaluate(el=>el.toDataURL());
  const box=await canvas.boundingBox();
  await page.mouse.move(box.x+40,box.y+60);
  await page.mouse.down();
  await page.mouse.move(box.x+110,box.y+70,{steps:8});
  await page.mouse.up();
  await page.waitForFunction(before=>document.querySelector('#servo-model').toDataURL()!==before,before);
  assert.match(await page.locator('#current-joint').innerText(),/ID 34/);
  assert.equal(state.calls.some(c=>c.path==='/api/servo/test'),false);
  await page.click('#servo-test-start');
  await page.locator('#servo-test-offset:enabled').waitFor();
  await clickJoint(14);
  assert.match(await page.locator('#current-joint').innerText(),/ID 34/);
  await page.click('#servo-test-stop');
  await page.locator('#servo-test-offset:disabled').waitFor();
  assert.ok(state.calls.filter(c=>c.path==='/api/servo/test').every(c=>c.target_id===34));
});

test('Installation check survives a visible window losing focus and unloads when hidden',async t=>{
  const {page,state}=await setup(t);
  await page.locator('#joint-groups button[data-id="30"]').click();
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

test('Stop can interrupt a pending angle update and needs torque readback before reporting success',async t=>{
  const {page,state}=await setup(t);
  await page.locator('#joint-groups button[data-id="30"]').click();
  const slider=page.locator('#servo-test-offset');
  const stop=page.locator('#servo-test-stop');
  const status=page.locator('#servo-test-status');
  let releaseMove;
  const moveStarted=new Promise(started=>{
    state.holdTestMove=()=>new Promise(resolve=>{
      releaseMove=resolve;
      started();
    });
  });
  state.testStopConfirmed=false;
  await page.click('#servo-test-start');
  await page.locator('#servo-test-offset:enabled').waitFor();
  try{
    await slider.press('End');
    await moveStarted;
    await stop.click();
    await status.filter({hasText:'STM32 did not confirm that torque is off.'}).waitFor();
    assert.equal(await slider.isDisabled(),true);
    assert.equal(await page.locator('#servo-test-start').isDisabled(),true);
    assert.equal(await stop.isEnabled(),true);
    assert.equal(state.testSession,null);

    const lateMove=page.waitForResponse(response=>response.url().endsWith('/api/servo/test')&&response.request().postDataJSON()?.offset_deg===30);
    releaseMove();
    await lateMove;
    state.testStopConfirmed=true;
    await stop.click();
    await status.filter({hasText:'Test stopped. Servo unloaded.'}).waitFor();
    assert.deepEqual(state.calls.filter(call=>call.path==='/api/servo/test').slice(-3).map(call=>call.action),['move','stop','stop']);
  }finally{
    releaseMove?.();
  }
});

test('Installation check shows automatic unload and waits for an explicit restart',async t=>{
  const {page,state}=await setup(t);
  await page.locator('#joint-groups button[data-id="30"]').click();
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

for(const [failure,expected] of [
  [null,'已读回保存值和运行值，均为 KP=5、KD=10'],
  ['failIdProtection','保护设置尚未确认'],
  ['failIdGains','点击「重新读取」查看保存值和运行值'],
  ['wrongGainOp','未能核实 KP=5、KD=10'],
]){
  test('Change ID clears the old address and reports '+(failure||'verified gains'),async t=>{
    const {page,state}=await setup(t,{width:1000,height:800},'zh-CN');
    await select(page);
    assert.equal(await page.locator('#kp').isVisible(),true);
    await page.fill('#kp','9');
    await page.fill('#new-id','42');
    assert.match(await page.locator('#set-id-hint').innerText(),/当前位置设为中位.*2 A.*KP=5、KD=10/);
    if(failure)state[failure]=true;
    await page.click('#set-id');
    await page.locator('#message').filter({hasText:expected}).waitFor();
    assert.equal(await page.locator('#scan-results button').count(),0);
    assert.equal(await page.locator('#set-id').isDisabled(),true);
    assert.deepEqual(await page.locator('.gains-readback dd').allTextContents(),['—','—']);
    assert.equal(await page.locator('#joint-groups [aria-pressed=true]').count(),0);
    assert.deepEqual(state.calls.filter(c=>c.action==='set_id').map(c=>[c.target_id,c.new_id]),[[0,42]]);
    assert.equal(state.calls.some(c=>c.action==='set_gains'||c.action==='set_protection_current'),false);
    if(!failure)assert.equal(await page.inputValue('#kp'),'5');
  });
}

test('Applying defaults at the existing ID reports the same ID and verified gains',async t=>{
  const {page}=await setup(t,{width:1000,height:800},'zh-CN');
  await select(page,1);
  await page.fill('#new-id','1');
  await page.click('#set-id');
  await page.locator('#message').filter({hasText:'舵机 ID 保持为 1。'}).waitFor();
  assert.match(await page.locator('#message').innerText(),/已读回保存值和运行值，均为 KP=5、KD=10/);
  assert.equal(await page.locator('#scan-status').innerText(),'请重新扫描并选择舵机。');
});

test('Read displays both register banks, distinguishes a mismatch and never writes gains',async t=>{
  const {page,state}=await setup(t);
  await select(page);
  assert.deepEqual(await values(page),['5','10','7','19']);
  assert.equal(await page.locator('#gains-status').getAttribute('data-state'),'different');
  assert.deepEqual(await page.locator('.gains-readback dd').allTextContents(),['0','2047']);
  assert.equal(await page.locator('.gains-readback dl + button').innerText(),'Read again');
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
  assert.deepEqual(await page.locator('.gains-readback dd').allTextContents(),['0','—']);
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
  const motion=await page.locator('.servo-test').boundingBox();
  const settings=await page.locator('.servo-controls').boundingBox();
  assert.ok(motion.y+motion.height<=settings.y);
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
  assert.deepEqual(await page.locator('.gains-readback dt').allTextContents(),['当前舵机 ID','当前编码器值']);
  assert.equal(await page.locator('#read').innerText(),'重新读取');
  assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
  assert.deepEqual(await page.locator('.nav a').allTextContents(),['控制','标定','舵机设置','语音交互驾驶舱','GitHub','Wiki','常见问题']);
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
