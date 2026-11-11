const $=id=>document.getElementById(id);
const {t,setText}=window.DuckPanelLanguage;
const serverErrors=new Map([
  ['Request failed','请求失败。'],
  ['Invalid gain reply','收到无效的 KP/KD 回包。'],
  ['No MCU feedback. Servo setup is not ready.','未收到 STM32 反馈，舵机设置尚未就绪。'],
  ['Deploy the updated project to enable servo setup.','请部署更新后的应用，再使用舵机设置。'],
  ['Invalid scan reply','收到无效的扫描回包。'],
  ['Enter a protection current between 0 and 425.9775 A.','请输入 0 到 425.9775 A 之间的保护电流。'],
  ['Invalid broadcast reply','收到无效的广播回包。'],
  ['ID must be between 0 and 253','舵机 ID 必须在 0 到 253 之间。'],
  ['Servo ID must be between 0 and 253','舵机 ID 必须在 0 到 253 之间。'],
  ['invalid new id','新 ID 必须在 0 到 253 之间。'],
  ['kp/kd out of range','KP 和 KD 必须在 0 到 255 之间。'],
  ['servo reply timeout','等待舵机指令回包超时。'],
  ['unexpected servo reply','舵机回包与指令不匹配。'],
  ['unexpected servo reply ID','舵机回包的 ID 与目标不一致。'],
  ['servo operation failed','舵机操作失败。'],
  ['not in servo_debug mode','尚未进入舵机设置模式。'],
  ['MCU feedback is stale','STM32 反馈已过期。'],
  ['Both old and new IDs replied. Connect only one servo and scan again','新旧 ID 均有应答。请只连接一个舵机，再重新扫描。'],
  ['protection current out of range','保护电流超出范围。'],
  ['protection current must be amperes','保护电流请以安培为单位输入。'],
  ['protection applies to all servos; target must be broadcast','保护设置必须广播给所有舵机。'],
  ['Runtime gain reads wait until calibration or servo setup transitions finish','请等待标定或舵机设置模式切换完成，再读取运行值。'],
  ['Runtime gain reads wait until calibration/servo setup closes','请退出标定或舵机设置后再读取运行值。'],
  ['Servo setup is not ready for the position test.','请等待舵机设置就绪后再开始测试。'],
  ['Invalid servo test reply','收到无效的舵机测试回包。'],
  ['Unexpected servo test reply.','舵机测试回包与当前测试不匹配，请重试。'],
  ['IMU calibration is in progress. Wait for it to finish or exit calibration.','IMU 正在标定，请等待完成或退出标定。'],
  ['低电压保护中，请充电后重新使能。 Low battery protection is active.','低电压保护中，请充电后重试。','Low battery protection is active. Recharge and try again.'],
  ['舵机中点指令已执行，但校准记录保存失败；请检查板端存储后重新校准。','中位指令已执行，但标定记录保存失败。请检查板上存储后重新标定。','The midpoint command ran, but the calibration record could not be saved. Check the board storage and calibrate again.']
].map(([original,zh,en=original])=>[original,[en,zh]]));
function errorText(error){
  if(error.name==='TimeoutError')return t('The request timed out. Try again.','请求超时，请重试。');
  if(error instanceof TypeError||error instanceof SyntaxError)return t('Could not contact Duck. Check the connection and try again.','暂时无法连接小鸭，请检查连接后重试。');
  const message=error.message;
  if(window.DuckPanelLanguage.chinese){
    const noReply=message.match(/^No servo replied at ID (\d+); ID was not changed$/);
    if(noReply)return `ID ${noReply[1]} 没有应答，未修改 ID。`;
    const offline=message.match(/^No servo replied at ID (\d+)\.$/);
    if(offline)return `ID ${offline[1]} 没有应答，请检查接线和编号。`;
    const lostFeedback=message.match(/^Servo ID (\d+) missed more than (\d+) consecutive feedback frames\.$/);
    if(lostFeedback)return `ID ${lostFeedback[1]} 连续超过 ${lostFeedback[2]} 帧没有位置反馈。`;
    const inUse=message.match(/^ID (\d+) is already in use; choose another ID$/);
    if(inUse)return `ID ${inUse[1]} 已被占用，请选择其他 ID。`;
    const writePending=message.match(/^ID write sent, but ID (\d+) did not reply\. Scan again$/);
    if(writePending)return `已发送改号指令，但 ID ${writePending[1]} 没有应答，请重新扫描。`;
  }
  return t(...(serverErrors.get(message)||[message,message]));
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let active=false, busy=false, selectedId=null, scanTask=null, scanController=null, closing=false;
const testJoints = new Map();
let testSession = null, testBusy = false, testRevision = 0;
const editIds=['kp','kd','new-id','reset-mid','set-gains','set-id','read'];
const protectionIds=['protection-current','enable-voltage-protection','disable-voltage-protection',
  'enable-overcurrent-protection','disable-overcurrent-protection','set-protection-current'];

async function api(path,body,signal){
  const timeout=AbortSignal.timeout(4000);
  const options={signal:signal?AbortSignal.any([signal,timeout]):timeout};
  if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const r=await fetch('/api/'+path,options);
  const x=await r.json();
  if(!r.ok)throw new Error(typeof x.detail==='string'&&x.detail?x.detail:'Request failed');
  return x;
}
function show(label){setText('message',label);}
function clearReadout(){
  setText('read-out',()=>'—');
  ['saved-kp','saved-kd','runtime-kp','runtime-kd'].forEach(id=>$(id).textContent='—');
  setText('gains-status',()=>t('Select a servo to read both values.','请选择舵机，读取保存值和运行值。'));
  delete $('gains-status').dataset.state;
}
async function readParameters(id,written=false){
  clearReadout();
  setText('gains-status',()=>t('Reading saved and runtime values…','正在读取保存值和运行值…'));
  const values={},errors=[];
  for(const [action,source,op] of [['read','saved',6],['read_runtime_gains','runtime',12]]){
    try{
      const r=await api('servo',{action,target_id:id});
      if(r.ok!==true||r.op!==op||r.target_id!==id||
        ![r.kp,r.kd].every(v=>Number.isInteger(v)&&v>=0&&v<=255)||
        (source==='saved'&&!Number.isInteger(r.raw_pos)))throw new Error('Invalid gain reply');
      values[source]=r;
      $(source+'-kp').textContent=r.kp;
      $(source+'-kd').textContent=r.kd;
      if(source==='saved')setText('read-out',()=>`ID ${id} · ${t('Pos','编码器')} ${r.raw_pos}`);
    }catch(e){
      errors.push(()=>{
        const label=source==='saved'?t('Saved read failed: ','读取保存值失败。'):t('Runtime read failed: ','读取运行值失败。');
        return label+errorText(e);
      });
    }
  }
  const status=$('gains-status');
  if(errors.length){
    status.dataset.state='incomplete';
    setText('gains-status',()=>t('Read incomplete · unavailable values shown as —','部分参数未读到，以 — 显示。'));
    show(()=>`ID ${id} · ${written?t('KP/KD written. ','KP/KD 已写入。'):''}${errors.map(label=>label()).join(' · ')}`);
  }else{
    const match=values.saved.kp===values.runtime.kp&&values.saved.kd===values.runtime.kd;
    status.dataset.state=match?'match':'different';
    setText('gains-status',()=>match?t('Saved and runtime values match.','保存值与运行值一致。')
      :t('Saved and runtime values differ. Runtime values are used now.','保存值与运行值不同，当前使用运行值。'));
    show(()=>`ID ${id} · ${written?t('KP/KD written and read back','KP/KD 已写入并读回'):t('parameters read','已读取参数')}`);
  }
}
function lock(value){
  busy=value;
  const settingsLocked = value || testBusy || !!testSession;
  editIds.forEach(id=>$(id).disabled=settingsLocked||selectedId===null);
  protectionIds.forEach(id=>$(id).disabled=settingsLocked);
  $('scan').disabled=settingsLocked;
  $('scan-results').querySelectorAll('button').forEach(b=>b.disabled=settingsLocked);
  $('exit').disabled=value&&!scanController;
  $('servo-test-id').disabled=settingsLocked||!testJoints.size;
  $('servo-test-start').disabled=settingsLocked||!testJoints.size;
  $('servo-test-stop').disabled=!testSession||closing;
  $('servo-test-offset').disabled=!testSession||closing;
}
function selectId(id){
  selectedId=validId(id);
  if (testJoints.has(selectedId)) {
    $('servo-test-id').value=String(selectedId);
    showTestJoint();
  }
  clearReadout();
  $('scan-results').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.id===selectedId)));
  lock(false);
  show(()=>t(`Selected ID ${id}`,`已选择 ID ${id}`));
  send('read');
}
function validId(value){
  const id=Number(value);
  if(String(value).trim()===''||!Number.isInteger(id)||id<0||id>253)throw new Error('ID must be between 0 and 253');
  return id;
}
async function enter(signal){
  await api('servo/enter',{},signal);
  const deadline=Date.now()+1500;
  while(Date.now()<deadline){
    const s=await api('status',undefined,signal);
    if(s.mode==='servo_debug'&&s.mcu_servo_debug&&s.enabled===false&&s.feedback_age_ms!=null&&s.feedback_age_ms<500){
      active=true;
      return s;
    }
    await sleep(100);
  }
  throw new Error('No MCU feedback. Servo setup is not ready.');
}
function stopScan(){
  if(scanController){
    setText('scan-status',()=>t('Stopping scan…','正在停止扫描…'));
    scanController.abort();
  }
}
async function exit(){
  if(closing)return;
  closing=true;
  const wasTesting = testSession || testBusy;
  const hidden = document.hidden;
  testRevision++;
  testSession=null;
  setTestAngle(0);
  if (wasTesting) {
    setText('servo-test-status',()=>hidden
      ? t('Test ended because the tab was hidden.','网页切到后台，测试已结束。')
      : t('Test ended when Servo setup closed.','已退出舵机设置，测试结束。'));
  }
  stopScan();
  if(scanTask)await scanTask;
  lock(true);
  try{await api('servo/exit',{});active=false;show(()=>t('Servo setup closed','已退出舵机设置'));}
  catch(e){show(()=>errorText(e));}
  finally{closing=false;lock(false);}
}
async function send(action,extra={}){
  if(busy||closing||selectedId===null)return;
  lock(true);
  try{
    const id=selectedId;
    if(action==='set_id')extra.new_id=validId(extra.new_id);
    clearReadout();
    await enter();
    show(()=>{
      const label=action==='reset_mid'?t('Reset Mid','重置中位'):action==='set_gains'?t('Set KpKd','写入 KP/KD'):t('Read','读取参数');
      return action==='set_id'?t(`Changing ID ${id} to ${extra.new_id}…`,`正在将 ID ${id} 改为 ${extra.new_id}…`):`ID ${id} · ${label}…`;
    });
    if(action==='read'){
      await readParameters(id);
      return;
    }
    await api('servo',{action,target_id:id,...extra});
    if(action==='reset_mid'){
      show(()=>`ID ${id} · ${t('Mid reset sent. Servo remains unloaded.','已发送中位重置指令，舵机保持卸力。')}`);
    }else if(action==='set_id'){
      selectedId=null;
      $('scan-results').replaceChildren();
      $('scan-progress').hidden=true;
      setText('scan-status',()=>t('ID changed. Scan again to select the updated servo.','ID 已修改，请重新扫描并选择新 ID。'));
      show(()=>`ID ${id} → ${extra.new_id} · ${t('verified','已验证')}`);
    }else{
      await readParameters(id,true);
    }
  }catch(e){show(()=>errorText(e));}
  finally{lock(false);}
}
async function scan(){
  scanController=new AbortController();
  const signal=scanController.signal;
  selectedId=null;
  clearReadout();
  lock(true);
  const found=[];
  let servoIds=[];
  let checked=0;
  $('scan-results').replaceChildren();
  $('scan-progress').value=0;
  $('scan-progress').hidden=false;
  $('scan-stop').hidden=false;
  setText('scan-status',()=>t('Preparing scan…','正在准备扫描…'));
  try{
    const s=await api('status',undefined,signal);
    if(s.servo_scan_version!==2)throw new Error('Deploy the updated project to enable servo setup.');
    servoIds=(await enter(signal)).servo_ids;
    await api('servo',{action:'torque_off',target_id:10},signal);
    show(()=>t('Scanning · torque off','正在扫描 · 舵机已卸力'));
    for(let id=0;id<=253;id++){
      signal.throwIfAborted();
      setText('scan-status',()=>t(`Scanning ID ${id} · ${checked}/254 checked · ${found.length} found`,`正在扫描 ID ${id} · 已检查 ${checked}/254 · 找到 ${found.length} 个`));
      let online=false;
      for(let attempt=0;attempt<2;attempt++){
        await api('servo/enter',{},signal);
        signal.throwIfAborted();
        const r=await api('servo',{action:'ping',target_id:id},signal);
        if(r.op!==7||r.target_id!==id||typeof r.ok!=='boolean')throw new Error('Invalid scan reply');
        if(r.ok){online=true;break;}
      }
      if(online){
        found.push(id);
        const b=document.createElement('button');
        b.textContent=`ID ${id}`;b.dataset.id=id;b.disabled=true;
        b.setAttribute('aria-pressed','false');
        b.onclick=()=>selectId(id);
        $('scan-results').appendChild(b);
      }
      checked++;
      $('scan-progress').value=checked;
    }
    setText('scan-status',()=>t(`Scan complete · 254 IDs checked · ${found.length} found`,`扫描完成 · 已检查 254 个 ID · 找到 ${found.length} 个`));
    show(()=>found.length?t('Select a found ID to read or change its settings.','请选择找到的 ID，读取或修改设置。')
      :t('No servos replied. Check servo power, bus connection and baud rate.','没有舵机应答，请检查舵机供电、总线连接和波特率。'));
  }catch(e){
    setText('scan-status',()=>{
      const summary=signal.aborted?t('Stopped','已停止'):t('Scan failed','扫描失败');
      return `${summary} · ${t(`${checked}/254 checked · ${found.length} found (partial results)`,`已检查 ${checked}/254 · 找到 ${found.length} 个（部分结果）`)}`;
    });
    show(()=>signal.aborted?t('Scan stopped. Select a found ID to continue.','扫描已停止，可选择已找到的 ID。')
      :e.message==='servo reply timeout'?t('No MCU reply. Scan stopped.','未收到 STM32 回包，扫描已停止。'):errorText(e));
  }finally{
    $('scan-stop').hidden=true;
    scanController=null;
    try{
      await readProtection(detectedServoIds(servoIds));
    }finally{lock(false);}
  }
}

function protectionSummary(readings,field){
  let enabled=0,disabled=0,unread=0;
  for(const reply of readings.values()){
    if(!reply)unread++;
    else if(reply[field])enabled++;
    else disabled++;
  }
  return [
    enabled&&t(`${enabled} enabled`,`${enabled} 台启用`),
    disabled&&t(`${disabled} disabled`,`${disabled} 台禁用`),
    unread&&t(`${unread} unread`,`${unread} 台未读到`),
  ].filter(Boolean).join(' · ')||'—';
}

function translateProtectionReadings(){
  const labels={enabled:t('Enabled','已启用'),disabled:t('Disabled','已禁用'),unread:t('Read failed','未读到')};
  for(const cell of $('protection-readings').querySelectorAll('[data-protection-state]')){
    cell.textContent=labels[cell.dataset.protectionState];
  }
}

function renderProtectionReadings(readings){
  const table=$('protection-readings');
  const rows=document.createDocumentFragment();
  for(const [id,reply] of readings){
    const row=document.createElement('tr');
    const idCell=document.createElement('th');
    idCell.scope='row';
    idCell.textContent=id;
    row.append(idCell);
    if(reply){
      for(const field of ['voltage_protection','overcurrent_protection']){
        const badge=document.createElement('span');
        badge.className='protection-badge';
        badge.dataset.protectionState=reply[field]?'enabled':'disabled';
        row.insertCell().append(badge);
      }
      const threshold=row.insertCell();
      threshold.className='protection-threshold';
      threshold.textContent=Number(reply.current_a.toFixed(4));
    }else{
      const unread=row.insertCell();
      unread.colSpan=3;
      unread.dataset.protectionState='unread';
    }
    rows.append(row);
  }
  table.tBodies[0].replaceChildren(rows);
  table.hidden=!readings.size;
  $('protection-readings-empty').hidden=!!readings.size;
  if(!readings.size)setText('protection-readings-empty',()=>t('No servos detected.','未检测到舵机。'));
  setText('voltage-protection-out',()=>protectionSummary(readings,'voltage_protection'));
  setText('overcurrent-protection-out',()=>protectionSummary(readings,'overcurrent_protection'));
  translateProtectionReadings();
}

function detectedServoIds(servoIds=[]){
  const scannedIds=Array.from($('scan-results').querySelectorAll('button'),button=>Number(button.dataset.id));
  return [...new Set([...servoIds,...scannedIds])].sort((a,b)=>a-b);
}

async function readProtection(ids){
  const readings=new Map();
  for(const id of ids){
    if(closing||document.hidden)return;
    readings.set(id,null);
    try{
      const r=await api('servo',{action:'read_protection',target_id:id});
      if(r.ok===true&&r.op===16&&r.target_id===id&&typeof r.voltage_protection==='boolean'&&
        typeof r.overcurrent_protection==='boolean'&&Number.isFinite(r.current_a)){
        readings.set(id,r);
      }
    }catch(_){}
  }
  if(!closing&&!document.hidden)renderProtectionReadings(readings);
}

async function setProtectionSwitch(field,enabled){
  if(busy||closing)return;
  lock(true);
  try{
    const ids=detectedServoIds((await enter()).servo_ids);
    if(!ids.length){
      setText('protection-message',()=>t('No servos detected. Scan for servo IDs first.','未检测到舵机，请先扫描 ID。'));
      return;
    }
    setText('protection-message',()=>t('Setting and verifying protection on detected servos…','正在逐台设置并核对保护开关…'));
    const failed=[];
    for(const id of ids){
      if(closing||document.hidden)return;
      try{
        const r=await api('servo',{action:`set_${field}`,target_id:id,protection_enabled:enabled});
        if(r.ok!==true||r.op!==17||r.target_id!==id||r[field]!==enabled)failed.push(id);
      }catch(_){failed.push(id);}
    }
    await readProtection(ids);
    if(closing||document.hidden)return;
    setText('protection-message',()=>failed.length
      ? t(`Setting could not be verified for ID ${failed.join(', ')}.`,`以下舵机的设置未确认。ID ${failed.join('、')}。`)
      : t('Protection setting verified.','保护开关已核对。'));
  }catch(e){setText('protection-message',()=>errorText(e));}
  finally{lock(false);}
}

async function setProtectionCurrent(){
  if(busy||closing)return;
  lock(true);
  try{
    const input=$('protection-current').value.trim(),current=Number(input);
    if(!input||!Number.isFinite(current)||current<0||current>65535*.0065)throw new Error('Enter a protection current between 0 and 425.9775 A.');
    const ids=detectedServoIds((await enter()).servo_ids);
    setText('protection-message',()=>t('Broadcasting the current threshold…','正在广播写入电流阈值…'));
    const r=await api('servo',{action:'set_protection_current',current_a:current});
    if(!r.ok||r.broadcast!==true||r.target_id!==254||r.op!==10)throw new Error('Invalid broadcast reply');
    await sleep(20);
    await readProtection(ids);
    if(!closing&&!document.hidden)setText('protection-message',()=>t(
      'Threshold broadcast sent. Check the readings below.','电流阈值已广播，请核对下方各舵机的读数。'));
  }catch(e){setText('protection-message',()=>errorText(e));}
  finally{lock(false);}
}

function testJointName(id, joint) {
  return `${t(joint.zone.en,joint.zone.zh)} · ID ${id} · ${t(joint.name.en,joint.name.zh)}`;
}

function showTestJoint() {
  const id = Number($('servo-test-id').value);
  const joint = testJoints.get(id);
  if (!joint) return;
  $('servo-test-image').src = joint.image;
  $('servo-test-image').hidden = false;
  setText('servo-test-caption',()=>testJointName(id,joint));
}

async function loadTestJoints() {
  try {
    const response = await fetch('calibrate-joints.json?v=20261107-2');
    if (!response.ok) throw new Error('Could not load the joint guide');
    const guide = await response.json();
    for (const zone of guide.zones) {
      for (const id of zone.ids) {
        const joint = {...guide.joints[id],zone:zone.title};
        if (!joint.image || !joint.name) throw new Error('Missing joint diagram');
        const option = document.createElement('option');
        option.id = `servo-test-joint-${id}`;
        option.value = id;
        $('servo-test-id').append(option);
        setText(option.id,()=>testJointName(id,joint));
        testJoints.set(id,joint);
      }
    }
    showTestJoint();
  } catch (_) {
    testJoints.clear();
    $('servo-test-id').replaceChildren();
    setText('servo-test-status',()=>t('Joint guide could not load. Reload to retry.','无法加载关节图示，请刷新重试。'));
  }
  lock(busy);
}

function setTestAngle(degrees) {
  $('servo-test-offset').value = degrees;
  $('servo-test-angle').value = `${degrees > 0 ? '+' : ''}${degrees}°`;
}

function releaseServoTest(test) {
  return api('servo/test',{action:'stop',target_id:test.id,session:test.session});
}

async function startServoTest() {
  const id = Number($('servo-test-id').value);
  if (busy || closing || testBusy || testSession || !testJoints.has(id)) return;
  const revision = ++testRevision;
  testBusy = true;
  lock(busy);
  try {
    await enter();
    if (revision !== testRevision || document.hidden) return;
    const reply = await api('servo/test',{action:'start',target_id:id},AbortSignal.timeout(700));
    if (!reply.ok || reply.target_id !== id || !Number.isInteger(reply.session)) throw new Error('Invalid servo test reply');
    const test = {id,session:reply.session,pending:false};
    if (revision !== testRevision || document.hidden) {
      await releaseServoTest(test);
      return;
    }
    const limit = Number.isInteger(reply.offset_limit_deg) && reply.offset_limit_deg > 0 ? reply.offset_limit_deg : 10;
    $('servo-test-offset').min = -limit;
    $('servo-test-offset').max = limit;
    setTestAngle(0);
    testSession = test;
    const idleSeconds = reply.idle_timeout_s;
    setText('servo-test-status',()=>{
      const guide = t(`Testing ID ${id}. Move the slider to check the joint.`,`正在测试 ID ${id}，拖动滑条核对关节。`);
      if (!Number.isInteger(idleSeconds) || idleSeconds <= 0) return guide;
      return guide + t(` The servo unloads after ${idleSeconds} seconds without an angle change.`, `${idleSeconds} 秒不调整角度会自动卸力。`);
    });
  } catch (error) {
    if (revision === testRevision) setText('servo-test-status',()=>errorText(error));
  } finally {
    testBusy = false;
    lock(busy);
  }
}

async function stopServoTest(error) {
  const test = testSession;
  if (!test) return;
  const revision = testRevision;
  testSession = null;
  testBusy = true;
  setTestAngle(0);
  lock(busy);
  let failure = error;
  try {
    await releaseServoTest(test);
  } catch (stopError) {
    failure ??= stopError;
  } finally {
    if (revision === testRevision) {
      const idle = failure?.message.match(/^Servo unloaded after (\d+) seconds without an angle change\.$/);
      setText('servo-test-status',()=>{
        if (idle) return t(failure.message,`${idle[1]} 秒未调整角度，已自动卸力。`);
        if (failure) return t('Test interrupted. ','测试中断。')+errorText(failure);
        return t('Test stopped. Servo unloaded.','已停止测试并卸力。');
      });
    }
    testBusy = false;
    lock(busy);
  }
}

async function refreshServoTest() {
  const test = testSession;
  if (!test || test.pending || document.hidden || closing) return;
  test.pending = true;
  try {
    await api('servo/test',{action:'move',target_id:test.id,session:test.session,
      offset_deg:Number($('servo-test-offset').value)},AbortSignal.timeout(650));
  } catch (error) {
    if (testSession === test) await stopServoTest(error);
  } finally {
    test.pending = false;
  }
}

$('servo-test-id').onchange=showTestJoint;
$('servo-test-start').onclick=startServoTest;
$('servo-test-stop').onclick=()=>stopServoTest();
$('servo-test-offset').oninput=e=>setTestAngle(Number(e.target.value));
setInterval(refreshServoTest,150);
$('servo-test-image').loading='lazy';
void loadTestJoints();

$('scan').onclick=()=>{
  if(busy||scanTask||closing)return;
  scanTask=scan().finally(()=>{scanTask=null;});
};
$('scan-stop').onclick=stopScan;
$('exit').onclick=exit;
$('reset-mid').onclick=()=>send('reset_mid');
$('set-gains').onclick=()=>send('set_gains',{kp:+$('kp').value,kd:+$('kd').value});
$('set-id').onclick=()=>send('set_id',{new_id:$('new-id').value});
$('read').onclick=()=>send('read');
$('enable-voltage-protection').onclick=()=>setProtectionSwitch('voltage_protection',true);
$('disable-voltage-protection').onclick=()=>setProtectionSwitch('voltage_protection',false);
$('enable-overcurrent-protection').onclick=()=>setProtectionSwitch('overcurrent_protection',true);
$('disable-overcurrent-protection').onclick=()=>setProtectionSwitch('overcurrent_protection',false);
$('set-protection-current').onclick=setProtectionCurrent;
$('language').addEventListener('click',translateProtectionReadings);
window.addEventListener('languagechange',translateProtectionReadings);
window.addEventListener('pagehide',()=>{
  testRevision++;
  testSession=null;
  stopScan();
  navigator.sendBeacon('/api/servo/exit',new Blob(['{}'],{type:'application/json'}));
});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(active||scanTask||testBusy||testSession))exit();});

let statusBusy=false;
setInterval(async()=>{
  if(document.hidden||busy||closing||statusBusy)return;
  statusBusy=true;
  try{
    const s=await api('status');
    if(busy||closing)return;
    active=s.mode==='servo_debug';
    if(active)await api('servo/enter',{});
  }catch(_){}
  finally{statusBusy=false;}
},1000);
async function initialize(){
  lock(true);
  show(()=>t('Entering servo setup and unloading all servos…','正在进入舵机设置，所有舵机将卸力…'));
  try{
    const s=await enter();
    show(()=>t('All servos are unloaded. Scan for servos, then select an ID.','所有舵机已卸力。请先扫描，再选择舵机 ID。'));
    await readProtection(detectedServoIds(s.servo_ids));
  }catch(e){
    show(()=>errorText(e));
    for(const id of ['voltage-protection-out','overcurrent-protection-out']){
      setText(id,()=>'—');
    }
    setText('protection-readings-empty',()=>t('Could not read servo settings.','暂时无法读取舵机设置。'));
  }
  finally{lock(false);}
}
initialize();
