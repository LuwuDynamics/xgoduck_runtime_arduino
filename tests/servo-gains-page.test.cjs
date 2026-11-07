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

async function setup(t,viewport={width:1100,height:900},locale='en-US'){
  const state={mode:'shadow',calls:[],failSaved:false,failRuntime:false,invalidRuntime:false,failWrite:false,
    servos:{0:{saved:[5,10],runtime:[7,19]},1:{saved:[9,23],runtime:[9,23]}}};
  const context=await browser.newContext({viewport,locale});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  t.after(async()=>{await context.close();assert.deepEqual(errors,[]);});
  await context.route('http://duck-gains.test/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!url.pathname.startsWith('/api/')){
      const file=path.join(__dirname,'../assets',url.pathname);
      const contentType={'.html':'text/html','.css':'text/css','.js':'text/javascript'}[path.extname(file)];
      return route.fulfill({status:200,body:await readFile(file),contentType});
    }
    const body=request.postDataJSON()||{},action=body.action,id=body.target_id;
    state.calls.push({...body,path:url.pathname});
    const reply=(json,status=200)=>route.fulfill({status,json});
    if(url.pathname==='/api/status')return reply({mode:state.mode,enabled:false,mcu_servo_debug:state.mode==='servo_debug',feedback_age_ms:1,servo_scan_version:2});
    if(url.pathname==='/api/servo/enter'){state.mode='servo_debug';return reply({ok:true});}
    if(url.pathname==='/api/servo/exit'){state.mode='shadow';return reply({ok:true});}
    if(action==='torque_off')return reply({ok:true});
    if(action==='ping')return reply({op:7,target_id:id,ok:id in state.servos});
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
  await page.goto('http://duck-gains.test/servo.html');
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
  assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
  assert.deepEqual(await page.locator('.nav a').allTextContents(),['控制','标定','舵机设置']);
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
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
});
