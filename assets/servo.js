const $=id=>document.getElementById(id);
const chinese=/^zh(?:[-_]|$)/i.test(navigator.languages?.[0]||navigator.language||'en');
const t=(en,zh)=>chinese?zh:en;
document.documentElement.lang=t('en','zh-CN');
if(chinese){
  for(const element of document.querySelectorAll('[data-zh]'))element.textContent=element.dataset.zh;
  for(const attr of ['aria-label','title']){
    const translated='data-zh-'+attr;
    for(const element of document.querySelectorAll(`[${translated}]`))element.setAttribute(attr,element.getAttribute(translated));
  }
}
const serverErrors=new Map([
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
  ['IMU calibration is in progress. Wait for it to finish or exit calibration.','IMU 正在标定，请等待完成或退出标定。'],
  ['低电压保护中，请充电后重新使能。 Low battery protection is active.','低电压保护中，请充电后重试。','Low battery protection is active. Recharge and try again.'],
  ['舵机中点指令已执行，但校准记录保存失败；请检查板端存储后重新校准。','中位指令已执行，但标定记录保存失败。请检查板上存储后重新标定。','The midpoint command ran, but the calibration record could not be saved. Check the board storage and calibrate again.']
].map(([original,zh,en=original])=>[original,t(en,zh)]));
function errorText(error){
  if(error.name==='TimeoutError')return t('The request timed out. Try again.','请求超时，请重试。');
  if(error instanceof TypeError||error instanceof SyntaxError)return t('Could not contact Duck. Check the connection and try again.','暂时无法连接小鸭，请检查连接后重试。');
  const message=error.message;
  if(chinese){
    const noReply=message.match(/^No servo replied at ID (\d+); ID was not changed$/);
    if(noReply)return `ID ${noReply[1]} 没有应答，未修改 ID。`;
    const inUse=message.match(/^ID (\d+) is already in use; choose another ID$/);
    if(inUse)return `ID ${inUse[1]} 已被占用，请选择其他 ID。`;
    const writePending=message.match(/^ID write sent, but ID (\d+) did not reply\. Scan again$/);
    if(writePending)return `已发送改号指令，但 ID ${writePending[1]} 没有应答，请重新扫描。`;
  }
  return serverErrors.get(message)||message;
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let active=false, busy=false, selectedId=null, scanTask=null, scanController=null, closing=false;
const editIds=['kp','kd','new-id','reset-mid','set-gains','set-id','read'];
const protectionIds=['protection-current','enable-protection','disable-protection','set-protection-current'];

async function api(path,body,signal){
  const timeout=AbortSignal.timeout(4000);
  const options={signal:signal?AbortSignal.any([signal,timeout]):timeout};
  if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const r=await fetch('/api/'+path,options);
  const x=await r.json();
  if(!r.ok)throw new Error(typeof x.detail==='string'&&x.detail?x.detail:t('Request failed','请求失败。'));
  return x;
}
function show(msg){$('message').textContent=msg;}
function clearReadout(){
  $('read-out').textContent='—';
  ['saved-kp','saved-kd','runtime-kp','runtime-kd'].forEach(id=>$(id).textContent='—');
  $('gains-status').textContent=t('Select a servo to read both values.','请选择舵机，读取保存值和运行值。');
  delete $('gains-status').dataset.state;
}
async function readParameters(id,written=false){
  clearReadout();
  $('gains-status').textContent=t('Reading saved and runtime values…','正在读取保存值和运行值…');
  const values={},errors=[];
  for(const [action,source,op] of [['read','saved',6],['read_runtime_gains','runtime',12]]){
    try{
      const r=await api('servo',{action,target_id:id});
      if(r.ok!==true||r.op!==op||r.target_id!==id||
        ![r.kp,r.kd].every(v=>Number.isInteger(v)&&v>=0&&v<=255)||
        (source==='saved'&&!Number.isInteger(r.raw_pos)))throw new Error(t('Invalid gain reply','收到无效的 KP/KD 回包。'));
      values[source]=r;
      $(source+'-kp').textContent=r.kp;
      $(source+'-kd').textContent=r.kd;
      if(source==='saved')$('read-out').textContent=`ID ${id} · ${t('Pos','编码器')} ${r.raw_pos}`;
    }catch(e){
      const label=source==='saved'?t('Saved read failed: ','读取保存值失败。'):t('Runtime read failed: ','读取运行值失败。');
      errors.push(label+errorText(e));
    }
  }
  const status=$('gains-status');
  if(errors.length){
    status.dataset.state='incomplete';
    status.textContent=t('Read incomplete · unavailable values shown as —','部分参数未读到，以 — 显示。');
    show(`ID ${id} · ${written?t('KP/KD written. ','KP/KD 已写入。'):''}${errors.join(' · ')}`);
  }else{
    const match=values.saved.kp===values.runtime.kp&&values.saved.kd===values.runtime.kd;
    status.dataset.state=match?'match':'different';
    status.textContent=match?t('Saved and runtime values match.','保存值与运行值一致。')
      :t('Saved and runtime values differ. Runtime values are used now.','保存值与运行值不同，当前使用运行值。');
    show(`ID ${id} · ${written?t('KP/KD written and read back','KP/KD 已写入并读回'):t('parameters read','已读取参数')}`);
  }
}
function lock(value){
  busy=value;
  editIds.forEach(id=>$(id).disabled=value||selectedId===null);
  protectionIds.forEach(id=>$(id).disabled=value);
  $('scan').disabled=value;
  $('scan-results').querySelectorAll('button').forEach(b=>b.disabled=value);
  $('exit').disabled=value&&!scanController;
}
function selectId(id){
  selectedId=validId(id);
  clearReadout();
  $('scan-results').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.id===selectedId)));
  lock(false);
  show(t(`Selected ID ${selectedId}`,`已选择 ID ${selectedId}`));
  send('read');
}
function validId(value){
  const id=Number(value);
  if(String(value).trim()===''||!Number.isInteger(id)||id<0||id>253)throw new Error(t('ID must be between 0 and 253','舵机 ID 必须在 0 到 253 之间。'));
  return id;
}
async function enter(signal){
  await api('servo/enter',{},signal);
  const deadline=Date.now()+1500;
  while(Date.now()<deadline){
    const s=await api('status',undefined,signal);
    if(s.mode==='servo_debug'&&s.mcu_servo_debug&&s.enabled===false&&s.feedback_age_ms!=null&&s.feedback_age_ms<500){
      active=true;
      return;
    }
    await sleep(100);
  }
  throw new Error(t('No MCU feedback. Servo setup is not ready.','未收到 STM32 反馈，舵机设置尚未就绪。'));
}
function stopScan(){
  if(scanController){
    $('scan-status').textContent=t('Stopping scan…','正在停止扫描…');
    scanController.abort();
  }
}
async function exit(){
  if(closing)return;
  closing=true;
  stopScan();
  if(scanTask)await scanTask;
  lock(true);
  try{await api('servo/exit',{});active=false;show(t('Servo setup closed','已退出舵机设置'));}
  catch(e){show(errorText(e));}
  finally{lock(false);closing=false;}
}
async function send(action,extra={}){
  if(busy||closing||selectedId===null)return;
  lock(true);
  try{
    const id=selectedId;
    if(action==='set_id')extra.new_id=validId(extra.new_id);
    clearReadout();
    await enter();
    const label=action==='reset_mid'?t('Reset Mid','重置中位'):action==='set_gains'?t('Set KpKd','写入 KP/KD'):t('Read','读取参数');
    show(action==='set_id'?t(`Changing ID ${id} to ${extra.new_id}…`,`正在将 ID ${id} 改为 ${extra.new_id}…`):`ID ${id} · ${label}…`);
    if(action==='read'){
      await readParameters(id);
      return;
    }
    await api('servo',{action,target_id:id,...extra});
    if(action==='reset_mid'){
      show(`ID ${id} · ${t('Mid reset sent. Servo remains unloaded.','已发送中位重置指令，舵机保持卸力。')}`);
    }else if(action==='set_id'){
      selectedId=null;
      $('scan-results').replaceChildren();
      $('scan-progress').hidden=true;
      $('scan-status').textContent=t('ID changed. Scan again to select the updated servo.','ID 已修改，请重新扫描并选择新 ID。');
      show(`ID ${id} → ${extra.new_id} · ${t('verified','已验证')}`);
    }else{
      await readParameters(id,true);
    }
  }catch(e){show(errorText(e));}
  finally{lock(false);}
}
async function scan(){
  scanController=new AbortController();
  const signal=scanController.signal;
  selectedId=null;
  clearReadout();
  lock(true);
  const found=[];
  let checked=0;
  $('scan-results').replaceChildren();
  $('scan-progress').value=0;
  $('scan-progress').hidden=false;
  $('scan-stop').hidden=false;
  $('scan-status').textContent=t('Preparing scan…','正在准备扫描…');
  try{
    const s=await api('status',undefined,signal);
    if(s.servo_scan_version!==2)throw new Error(t('Deploy the updated project to enable servo setup.','请部署更新后的应用，再使用舵机设置。'));
    await enter(signal);
    await api('servo',{action:'torque_off',target_id:10},signal);
    show(t('Scanning · torque off','正在扫描 · 舵机已卸力'));
    for(let id=0;id<=253;id++){
      signal.throwIfAborted();
      $('scan-status').textContent=t(`Scanning ID ${id} · ${checked}/254 checked · ${found.length} found`,`正在扫描 ID ${id} · 已检查 ${checked}/254 · 找到 ${found.length} 个`);
      let online=false;
      for(let attempt=0;attempt<2;attempt++){
        await api('servo/enter',{},signal);
        signal.throwIfAborted();
        const r=await api('servo',{action:'ping',target_id:id},signal);
        if(r.op!==7||r.target_id!==id||typeof r.ok!=='boolean')throw new Error(t('Invalid scan reply','收到无效的扫描回包。'));
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
    $('scan-status').textContent=t(`Scan complete · 254 IDs checked · ${found.length} found`,`扫描完成 · 已检查 254 个 ID · 找到 ${found.length} 个`);
    show(found.length?t('Select a found ID to read or change its settings.','请选择找到的 ID，读取或修改设置。')
      :t('No servos replied. Check servo power, bus connection and baud rate.','没有舵机应答，请检查舵机供电、总线连接和波特率。'));
  }catch(e){
    const summary=signal.aborted?t('Stopped','已停止'):t('Scan failed','扫描失败');
    $('scan-status').textContent=`${summary} · ${t(`${checked}/254 checked · ${found.length} found (partial results)`,`已检查 ${checked}/254 · 找到 ${found.length} 个（部分结果）`)}`;
    show(signal.aborted?t('Scan stopped. Select a found ID to continue.','扫描已停止，可选择已找到的 ID。')
      :e.message==='servo reply timeout'?t('No MCU reply. Scan stopped.','未收到 STM32 回包，扫描已停止。'):errorText(e));
  }finally{
    $('scan-stop').hidden=true;
    scanController=null;
    lock(false);
  }
}
async function sendProtection(action,enabled){
  if(busy||closing)return;
  lock(true);
  try{
    const settingCurrent=action==='set_protection_current',body={action};
    if(settingCurrent){
      const input=$('protection-current').value.trim(),current=Number(input);
      if(!input||!Number.isFinite(current)||current<0||current>65535*.0065)throw new Error(t('Enter a protection current between 0 and 425.9775 A.','请输入 0 到 425.9775 A 之间的保护电流。'));
      body.current_a=current;
    }else{
      body.protection_enabled=enabled;
    }
    await enter();
    show(t('Broadcasting protection settings to all servos…','正在向所有舵机广播保护设置…'));
    const r=await api('servo',body);
    if(!r.ok||r.broadcast!==true||r.target_id!==254||r.op!==(settingCurrent?10:9))throw new Error(t('Invalid broadcast reply','收到无效的广播回包。'));
    if(settingCurrent){
      $('protection-current-out').textContent=`${t('Broadcast sent','已发送广播')} · ${r.current_a.toFixed(4)} A`;
    }else{
      $('protection-out').textContent=t(`Broadcast sent · voltage / overcurrent ${r.protection_enabled?'on':'off'}`,`已发送广播 · 电压与过流保护${r.protection_enabled?'开启':'关闭'}`);
    }
    show(t('Protection broadcast sent. Individual servo writes are not verified.','保护设置广播已发送，各舵机的写入结果尚未单独验证。'));
  }catch(e){show(errorText(e));}
  finally{lock(false);}
}
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
$('enable-protection').onclick=()=>sendProtection('set_protection',true);
$('disable-protection').onclick=()=>sendProtection('set_protection',false);
$('set-protection-current').onclick=()=>sendProtection('set_protection_current');
window.addEventListener('pagehide',()=>{
  stopScan();
  navigator.sendBeacon('/api/servo/exit',new Blob(['{}'],{type:'application/json'}));
});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(active||scanTask))exit();});

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
  show(t('Entering servo setup and unloading all servos…','正在进入舵机设置，所有舵机将卸力…'));
  try{
    await enter();
    show(t('All servos are unloaded. Scan for servos, then select an ID.','所有舵机已卸力。请先扫描，再选择舵机 ID。'));
  }catch(e){show(errorText(e));}
  finally{lock(false);}
}
initialize();
