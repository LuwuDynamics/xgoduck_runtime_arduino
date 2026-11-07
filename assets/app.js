const $=id=>document.getElementById(id);
const chinese=/^zh(?:[-_]|$)/i.test(navigator.languages?.[0]||navigator.language||'en');
const t=(en,zh)=>chinese?zh:en;
document.documentElement.lang=t('en','zh-CN');
if(chinese){
  for(const element of document.querySelectorAll('[data-zh]'))element.textContent=element.dataset.zh;
  for(const element of document.querySelectorAll('[data-zh-alt]'))element.alt=element.dataset.zhAlt;
}
const toggleLabel=enabled=>enabled?t('On','开'):t('Off','关');
const actionLabels={
  pick:t('Pick','叼东西'),roulade:t('Roulade','翻滚'),sit:t('Sit','坐下'),stand:t('Stand','站起'),
  left_kick:t('Left kick','左脚踢球'),right_kick:t('Right kick','右脚踢球')
};
const modeLabels={
  shadow:t('inference only','仅推理'),off:t('torque off','舵机卸力'),hold:t('default pose','默认姿态'),
  policy:t('walk','行走'),calibrate:t('calibration','标定'),servo_debug:t('servo setup','舵机设置'),
  sit:t('sit','坐姿'),stand:t('stand','正在站起'),left_kick:t('left kick','左脚踢球'),right_kick:t('right kick','右脚踢球')
};
const recoveryLabels={walk:t('Walking','行走中'),default_pose:t('Preparing to get up','准备起身'),getup:t('Getting up','正在起身')};
const serverMessages=new Map([
  ['Not calibrated. Hold the board upright and still.','尚未标定，请将主板竖直放稳。'],
  ['Saved IMU calibration loaded.','已加载保存的 IMU 标定。'],
  ['IMU calibrated and saved.','IMU 标定已保存。'],
  ['IMU calibration reset. Previous calibration backed up.','IMU 标定已重置，原标定结果已备份。'],
  ['Calibration cancelled. Previous calibration kept.','标定已取消，已保留上一次结果。'],
  ['Calibration could not be loaded. Calibrate again or reset it.','无法加载 IMU 标定，请重新标定或重置。'],
  ['IMU calibration is in progress. Wait for it to finish or exit calibration.','IMU 正在标定，请等待完成或退出标定。'],
  ['Hold the board upright with the battery connector pointing up.','请让主板保持竖直，电池接口朝上。'],
  ['Stop motion and hold the board upright and still.','请停止运动，将主板竖直放稳。'],
  ['Unload all joints before calibrating the IMU.','请先卸力所有舵机，再标定 IMU。'],
  ['Wait for the current action to finish before calibrating.','请等当前动作完成后再标定。'],
  ['IMU readings are stale. Check the connection and try again.','IMU 读数已过期，请检查连接后重试。'],
  ['IMU readings were interrupted. Try again.','IMU 读数中断，请重试。'],
  ['The IMU restarted. Try again.','IMU 已重启，请重试。'],
  ['Not enough IMU readings. Check the connection and try again.','IMU 读数不足，请检查连接后重试。'],
  ['Not enough IMU readings. Keep the board still and try again.','IMU 读数不足，请保持主板静止后重试。'],
  ['Unexpected acceleration. Keep the board still and try again.','加速度异常，请保持主板静止后重试。'],
  ['Movement detected. Hold the board still and try again.','检测到晃动，请保持主板静止后重试。'],
  ['Calibration cancelled because the control mode changed.','控制模式已切换，标定已取消。'],
  ['Calibration cancelled because the app stopped.','应用已停止，标定已取消。'],
  ['App stopped. Calibration cancelled.','应用已停止，标定已取消。'],
  ['Stop motion before resetting IMU calibration.','请先停止运动，再重置 IMU 标定。'],
  ['Could not reset calibration. Previous calibration kept.','无法重置 IMU 标定，已保留上一次结果。'],
  ['MCU feedback is stale','STM32 反馈已过期。'],
  ['IMU feedback is stale','IMU 反馈已过期。'],
  ['required servo feedback / IMU missing','缺少必要的舵机或 IMU 反馈。'],
  ['STM32 feedback timed out; motion paused, holding last targets','STM32 反馈超时，运动已暂停，舵机保持上一次目标位置。'],
  ['sensor feedback lost; disarmed','传感器反馈中断，舵机已卸力。'],
  ['MCU released torque during the feedback pause','反馈中断期间，STM32 已卸力。'],
  ['calibrate/servo_debug heartbeat lost; exited','标定或舵机设置页面失去连接，已退出当前模式。'],
  ['exit calibrate/servo_debug first','请先退出标定或舵机设置。'],
  ['exit calibrate/servo_debug before sit/stand','请先退出标定或舵机设置，再执行坐下或站起。'],
  ['motion commands disabled in calibrate/servo_debug','标定或舵机设置期间无法发送运动指令。'],
  ['Action preparation timed out; action cancelled.','动作准备超时，已取消。'],
  ['Roulade start timed out; action cancelled.','翻滚启动超时，已取消。'],
  ['stand up before switching to walk','请先站起，再切换到行走。'],
  ['stand up before switching roller mode','请先站起，再切换滑行模式。'],
  ['stand up before starting pick','请先站起，再叼东西。'],
  ['stand up before starting roulade','请先站起，再翻滚。'],
  ['wait for the current action to finish preparing','请等待当前动作准备完成。'],
  ['wait for kick to finish before switching to walk','请等踢球完成后再切换到行走。'],
  ['wait for kick to finish before switching roller mode','请等踢球完成后再切换滑行模式。'],
  ['wait for roulade to finish before switching roller mode','请等翻滚完成后再切换滑行模式。'],
  ['wait for kick to finish before sit/stand','请等踢球完成后再坐下或站起。'],
  ['wait for kick to finish before pick','请等踢球完成后再叼东西。'],
  ['wait for kick to finish before roulade','请等踢球完成后再翻滚。'],
  ['wait for pick/roulade to finish before sitting','请等叼取或翻滚完成后再坐下。'],
  ['wait for get-up recovery before sit/stand','请等待起身完成，再坐下或站起。'],
  ['sit/stand is already active','坐下或站起动作正在进行。'],
  ['stand is only available after sit','小鸭坐下后才能执行站起动作。'],
  ['kick requires shadow/policy mode','请先切换到仅推理或行走模式，再踢球。'],
  ['pick requires shadow/policy mode','请先切换到仅推理或行走模式，再叼东西。'],
  ['roulade requires shadow/policy mode','请先切换到仅推理或行走模式，再翻滚。'],
  ['kick only while walking upright with no other action','踢球需要小鸭在行走模式下站稳，且没有其他动作。'],
  ['pick only while walking upright','叼东西需要小鸭在行走模式下站稳。'],
  ['roulade only while walking','翻滚需要小鸭处于行走模式。'],
  ['wait for roulade to finish','请等待翻滚结束。'],
  ['wait for IMU feedback before roulade','请等待 IMU 反馈后再翻滚。'],
  ['command exceeds bounds','指令参数超出范围。'],
  ['walking vx must be between -0.2 and 0.2','行走前后速度 vx 必须在 −0.2 到 0.2 之间。'],
  ['action scale must be within [0,2]','动作幅度必须在 0 到 2 之间。'],
  ['gyro alpha must be between 0 and 0.95','陀螺仪平滑参数必须在 0 到 0.95 之间。'],
  ['gravity tau must be between 0.01 and 2 seconds','重力平滑时间必须在 0.01 到 2 秒之间。'],
  ['action alpha must be within [0,1]','动作平滑参数必须在 0 到 1 之间。'],
  ['低电压保护中，请充电后重新使能。 Low battery protection is active.','低电压保护中，请充电后重新使能。','Low battery protection is active. Recharge before enabling torque.'],
  ['IMU 标定文件无效或为旧版。请在 9527 的 Control 页重新标定 IMU。','IMU 标定文件无效或为旧版，请重新标定 IMU。','The IMU calibration file is invalid or outdated. Calibrate the IMU again.'],
  ['STM32 未确认舵机使能，请重新点击「站起来」。','STM32 未确认舵机使能，请重新发送动作指令。','STM32 did not confirm torque enable. Send the command again.'],
  ['鸭子正在准备动作，请稍等。','小鸭正在准备动作，请稍等。','Duck is preparing an action. Please wait.']
].map(([original,zh,en=original])=>[original,t(en,zh)]));
function localizeMessage(message){
  if(!message)return '';
  for(const [prefix,zh] of [['Previous calibration kept. ','已保留上一次标定结果。'],['Motion command failed: ','运动指令失败。']]){
    if(message.startsWith(prefix))return t(prefix,zh)+localizeMessage(message.slice(prefix.length));
  }
  const remaining=message.match(/^旧版软件零位.*尚未完成的 ID：([\d, ]+)。/);
  if(remaining)return t(`Set the mechanical centers on the Calibration page. Remaining servo IDs ${remaining[1]}.`,`请先在标定页设置舵机机械中位。未完成的舵机 ID ${remaining[1]}。`);
  const rollerRange=message.match(/^roller wz must be between (-?[\d.]+) and (-?[\d.]+)$/);
  if(chinese&&rollerRange)return `滑行转向速度 ωz 必须在 ${rollerRange[1]} 到 ${rollerRange[2]} 之间。`;
  return serverMessages.get(message)||message;
}
function requestErrorText(error){
  if(error instanceof TypeError||error instanceof SyntaxError)return t('Could not contact Duck. Check the connection and try again.','暂时无法连接小鸭，请检查连接后重试。');
  return localizeMessage(error.message);
}
let statusBusy=false, commandBusy=false, commandPending=false, rollerBusy=false;
let rollerEnabled=false;
let sitstandPhase='idle', sitstandBusy=false, sitstandReady=false;
let kickActive=false, kickBusy=false;
let imuCalibrationActive=false, imuCalibrationBusy=false, imuCalibrationError='', imuReady=false;
let imuState=null, controlModeAvailable=true;
let actionPreparing=false;
let headingBusy=false;
const motionRanges={
  walk:{forward:.2,backward:.2,strafe:.3,yaw:1,keyboard:[.2,.15,.5]},
  roller:{forward:.4,backward:.4,strafe:0,yaw:.45,keyboard:[.4,0,.45]}
};
for(let i=0;i<4;i++) $('head-controls').insertAdjacentHTML('beforeend',`<label>${t('Head','头部')} ${i+1}<input id="head${i}" type="range" min="-1" max="1" value="0" step="0.02"></label>`);
$('head-controls').insertAdjacentHTML('beforeend',`<label>${t('Mouth','嘴部')}<input id="mouth" type="range" min="0" max="30" value="0" step="1"></label>`);
function showPreparation(active){
  actionPreparing=!!active;
  if(actionPreparing)for(const id of ['pick','roulade','sitstand','left_kick','right_kick','roller-mode'])$(id).disabled=true;
}
async function post(path, body){const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw new Error(typeof x.detail==='string'&&x.detail?x.detail:t('Request failed.','请求失败。'));if(x.preparing)showPreparation(true);return x;}
function showError(e){$('message').textContent=requestErrorText(e);}
function showHeading(h){
  if(!h)return;
  $('heading-hold').checked=h.enabled;$('heading-state').textContent=toggleLabel(h.enabled);
}
$('heading-hold').onchange=async()=>{
  if(headingBusy)return;
  const enabled=$('heading-hold').checked;
  headingBusy=true;$('heading-hold').disabled=true;
  try{showHeading(await post('heading-hold',{enabled}));}
  catch(e){$('heading-hold').checked=!enabled;showError(e);}
  finally{headingBusy=false;$('heading-hold').disabled=false;}
};
async function setMode(mode){try{await post('mode',{mode});$('message').textContent=t('Mode updated','模式已更新');}catch(e){showError(e);}}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
function showRoller(enabled){
  const changed=rollerEnabled!==enabled;
  rollerEnabled=enabled;
  $('roller-mode').checked=enabled;$('roller-state').textContent=toggleLabel(enabled);
  $('roller-warning').hidden=!enabled;
  $('action-profile').textContent=enabled?t('Skating','滑行'):t('Walking / other models','行走 / 其他模型');
  const limits=motionRanges[enabled?'roller':'walk'];
  const strafe=limits.strafe?'±'+limits.strafe:'0';
  $('motion-limits').textContent=t(`Limits: vx -${limits.backward} to +${limits.forward}, vy ${strafe}, ωz ±${limits.yaw}.`,`速度范围 · vx −${limits.backward} 至 +${limits.forward}，vy ${strafe}，ωz ±${limits.yaw}。`);
  $('left-label').textContent=enabled?t('Forward / backward','前进 / 后退'):t('Forward / strafe','前后 / 侧移');
  if(changed)zero(false);
}
$('roller-mode').onchange=async()=>{
  if(rollerBusy)return;
  const enabled=$('roller-mode').checked;
  showRoller(enabled);
  rollerBusy=true;$('roller-mode').disabled=true;
  try{const r=await post('roller',{enabled});showRoller(r.enabled);showActionSettings(r);$('message').textContent=r.enabled?t('Skating mode on','滑行模式已开启'):t('Skating mode off','滑行模式已关闭');}
  catch(e){showRoller(!enabled);showError(e);}
  finally{rollerBusy=false;$('roller-mode').disabled=false;}
};
$('pick').onclick=async()=>{try{await post('pick',{});$('message').textContent=actionLabels.pick;}catch(e){showError(e);}};
$('roulade').onclick=async()=>{try{await post('roulade',{});$('message').textContent=actionLabels.roulade;}catch(e){showError(e);}};
function showKick(state){
  const active=!!state?.active;
  if(active!==kickActive)zero(false);
  kickActive=active;
  for(const side of ['left','right'])$(side+'_kick').disabled=kickBusy||active||actionPreparing;
  document.querySelectorAll('#head-controls input').forEach(input=>{
    input.disabled=active;
    if(active)input.value=0;
  });
  if(active){
    document.querySelector('[data-mode="policy"]').disabled=true;
    $('pick').disabled=$('roulade').disabled=$('sitstand').disabled=true;
    $('roller-mode').disabled=true;
  }
}
for(const side of ['left','right'])$(side+'_kick').onclick=async()=>{
  if(kickBusy||kickActive||actionPreparing)return;
  kickBusy=true;
  for(const foot of ['left','right'])$(foot+'_kick').disabled=true;
  try{
    showKick(await post(side+'_kick',{}));
    $('message').textContent=actionLabels[side+'_kick'];
  }catch(e){showError(e);}
  finally{
    kickBusy=false;
    for(const foot of ['left','right'])$(foot+'_kick').disabled=kickActive||actionPreparing;
  }
};
function showSitstand(state){
  const phase=state?.phase||'idle';
  if(phase!==sitstandPhase)zero(false);
  sitstandPhase=phase;
  $('sitstand').textContent=phase==='idle'?actionLabels.sit:actionLabels.stand;
  $('sitstand').disabled=sitstandBusy||phase==='stand'||!sitstandReady||kickActive||actionPreparing;
  if(phase!=='idle'){
    document.querySelector('[data-mode="policy"]').disabled=true;
    $('pick').disabled=$('roulade').disabled=true;
    $('roller-mode').disabled=true;
  }
}
$('sitstand').onclick=async()=>{
  if(sitstandBusy||sitstandPhase==='stand'||!sitstandReady||actionPreparing)return;
  const action=sitstandPhase==='sit'?'stand':'sit';
  sitstandBusy=true;showSitstand({phase:sitstandPhase});
  try{
    const r=await post('sitstand',{action});showSitstand(r);
    $('message').textContent=action==='sit'?t('Sitting','正在坐下'):t('Standing up','正在站起');
  }catch(e){showError(e);}
  finally{sitstandBusy=false;showSitstand({phase:sitstandPhase});}
};
$('stop').onclick=()=>{zero();setMode('off')};
const keys=new Set(), sticks={left:{x:0,y:0},right:{x:0,y:0}}, resetSticks=[];
let motion=[0,0,0], targetMotion=[0,0,0];
const deadzone=v=>Math.abs(v)<=.15?0:v;
function updateMotion(){
  if(kickActive||imuCalibrationActive||imuCalibrationBusy){targetMotion=[0,0,0];return;}
  const key=(a,b)=>keys.has(a)-keys.has(b);
  const limits=motionRanges[rollerEnabled?'roller':'walk'];
  const forward=deadzone(sticks.left.y);
  targetMotion=[keys.has('w')||keys.has('s')?key('w','s')*limits.keyboard[0]:forward*(forward<0?limits.backward:limits.forward),
    keys.has('a')||keys.has('d')?key('a','d')*limits.keyboard[1]:deadzone(-sticks.left.x)*limits.strafe,
    keys.has('q')||keys.has('e')?key('q','e')*limits.keyboard[2]:deadzone(-sticks.right.x)*limits.yaw];
  targetMotion[0]=Math.max(-limits.backward,Math.min(limits.forward,targetMotion[0]));
  targetMotion[2]=Math.max(-limits.yaw,Math.min(limits.yaw,targetMotion[2]));
}
function showMotion(){
  ['vx','vy','wz'].forEach((id,i)=>$(id+'Value').textContent=motion[i].toFixed(2));
}
async function sendCommand(){
  if(imuCalibrationActive||imuCalibrationBusy||!controlModeAvailable)return;
  if(commandBusy){commandPending=true;return;}
  commandBusy=true;commandPending=false;
  motion=motion.map((old,i)=>{const value=.75*old+.25*targetMotion[i];return targetMotion[i]===0&&Math.abs(value)<.001?0:value;});
  showMotion();
  try{await post('command',{twist:imuCalibrationActive?[0,0,0]:[...motion],head:[0,1,2,3].map(i=>imuCalibrationActive?0:+$('head'+i).value),mouth:imuCalibrationActive?0:+$('mouth').value});}
  catch(e){showError(e);}
  finally{commandBusy=false;if(commandPending)sendCommand();}
}
function joystick(id,side){
  const zone=$(id),knob=zone.firstElementChild;
  let pointer=null;
  function reset(){
    const old=pointer;pointer=null;
    sticks[side]={x:0,y:0};knob.style.transform='translate(0px,0px)';
    if(old!==null&&zone.hasPointerCapture(old))zone.releasePointerCapture(old);
  }
  resetSticks.push(reset);
  function move(e){
    const b=zone.getBoundingClientRect(),radius=(zone.clientWidth-knob.offsetWidth)/2;
    let dx=e.clientX-b.left-b.width/2,dy=e.clientY-b.top-b.height/2;
    const distance=Math.hypot(dx,dy);
    if(distance>radius){dx*=radius/distance;dy*=radius/distance;}
    knob.style.transform=`translate(${dx}px,${dy}px)`;
    sticks[side]={x:dx/radius,y:-dy/radius};updateMotion();
  }
  zone.onpointerdown=e=>{if(pointer!==null||e.button!==0)return;e.preventDefault();pointer=e.pointerId;zone.setPointerCapture(pointer);move(e);sendCommand();};
  zone.onpointermove=e=>{if(e.pointerId===pointer)move(e);};
  const release=e=>{if(e.pointerId!==pointer)return;reset();updateMotion();sendCommand();};
  zone.onpointerup=zone.onpointercancel=zone.onlostpointercapture=release;
}
joystick('joy-left','left');joystick('joy-right','right');
function zero(send=true){keys.clear();resetSticks.forEach(reset=>reset());motion=[0,0,0];updateMotion();showMotion();if(send)sendCommand();}
$('zero').onclick=()=>zero();
['leg-alpha','head-alpha'].forEach(id=>$(id).onchange=()=>post('filter',{leg:+$('leg-alpha').value,head:+$('head-alpha').value}).catch(showError));
const actionInputs=[['action-scale','action_scale'],['leg-alpha','action_alpha'],['head-alpha','action_alpha_head']];
actionInputs.forEach(([id])=>$(id).oninput=()=>$(id+'-value').textContent=(+$(id).value).toFixed(2));
function showActionSettings(settings){
  actionInputs.forEach(([id,key])=>{if(document.activeElement!==$(id)){$(id).value=settings[key];$(id+'-value').textContent=(+$(id).value).toFixed(2);}});
}
$('action-scale').onchange=()=>post('action-scale',{scale:+$('action-scale').value}).catch(showError);
const imuFilters=[['gyro-alpha','gyro_alpha'],['gravity-tau','gravity_tau_s']];
imuFilters.forEach(([id,key])=>{
  const input=$(id),output=$(id+'-value');
  input.oninput=()=>output.textContent=(+input.value).toFixed(2);
  input.onchange=()=>post('imu-filter',{[key]:+input.value}).catch(showError);
});
function showImuCalibration(){
  imuCalibrationActive=!!imuState?.active;
  const busy=imuCalibrationBusy||imuCalibrationActive;
  $('imu-calibrate').disabled=busy||!imuReady||!controlModeAvailable||!imuState;
  $('imu-reset').disabled=busy||!imuReady||!controlModeAvailable||!imuState||(!imuState.calibrated&&imuState.state!=='error');
  const percent=Math.round((imuState?.progress||0)*100);
  $('imu-calibrate').textContent=imuCalibrationActive?t(`Calibrating… ${percent}%`,`正在标定… ${percent}%`):t('Calibrate IMU','标定 IMU');
  $('imu-calibration-progress').hidden=!imuCalibrationActive;
  $('imu-calibration-progress').value=imuState?.progress||0;
  const failed=!!imuCalibrationError||imuState?.state==='error';
  $('imu-calibration-status').classList.toggle('error',failed);
  const seconds=imuState?.seconds??1.5;
  const description=imuCalibrationActive
    ?t(`Keep the board still. Calibrating for ${seconds} seconds…`,`请保持主板静止，正在采样 ${seconds} 秒…`)
    :imuCalibrationError||localizeMessage(imuState?.message);
  $('imu-calibration-status').textContent=description||(failed
    ?t('IMU calibration failed. Try again.','IMU 标定失败，请重试。')
    :t('Reading calibration status…','正在读取标定状态…'));
  document.querySelector('[data-mode="shadow"]').disabled=busy;
  if(busy){
    document.querySelectorAll('[data-mode]').forEach(b=>b.disabled=true);
    for(const id of ['pick','roulade','sitstand','left_kick','right_kick','roller-mode'])$(id).disabled=true;
    document.querySelectorAll('#head-controls input').forEach(input=>input.disabled=true);
  }
}
for(const [id,action]of [['imu-calibrate','start'],['imu-reset','reset']])$(id).onclick=async()=>{
  if(imuCalibrationBusy||imuCalibrationActive||!imuReady||!controlModeAvailable||!imuState)return;
  imuCalibrationBusy=true;imuCalibrationError='';zero(false);showImuCalibration();
  try{
    await post('mode',{mode:'off'});
    imuState=await post('imu-calibration',{action});
  }catch(e){imuCalibrationError=requestErrorText(e);}
  finally{imuCalibrationBusy=false;showImuCalibration();}
};
function keyCommand(){updateMotion();sendCommand();}
window.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();$('stop').click();return;}const k=e.key.toLowerCase();if('wasdqe'.includes(k)&&k.length===1){keys.add(k);keyCommand();}});
window.addEventListener('keyup',e=>{const k=e.key.toLowerCase();if(keys.delete(k))keyCommand();});
window.addEventListener('blur',()=>zero());
document.addEventListener('visibilitychange',()=>{if(document.hidden)zero();});
window.addEventListener('pagehide',()=>{
  zero(false);
  navigator.sendBeacon('/api/command',new Blob([JSON.stringify({twist:[0,0,0]})],{type:'application/json'}));
});
setInterval(()=>{if(!document.hidden&&!commandBusy)sendCommand();},50);
setInterval(async()=>{if(statusBusy||document.hidden)return;statusBusy=true;try{
const r=await fetch('/api/status');
if(!r.ok)throw new Error(t('Could not read robot status.','暂时无法读取小鸭状态。'));
const s=await r.json();
$('rx').textContent=s.feedback_hz.toFixed(1)+' Hz';
$('rl').textContent=s.inference_hz.toFixed(1)+' Hz';
$('servos').textContent=(s.servo_ids?.length||0)+'/15';
$('imu').textContent=s.imu_ok?t('online','在线'):t('offline','离线');
const mode=s.left_kick?.active?'left_kick':s.right_kick?.active?'right_kick':s.sitstand?.active?s.sitstand.phase:s.mode;
const torque=s.enabled?t(' · enabled',' · 舵机已使能'):t(' · torque off',' · 舵机卸力');
$('mode').textContent=(modeLabels[mode]||mode)+(mode==='off'&&!s.enabled?'':torque);
$('message').textContent=localizeMessage(s.last_error)||(s.control_ready?t('Robot ready','小鸭已就绪'):t('Waiting for robot…','等待小鸭就绪…'));
controlModeAvailable=!['calibrate','servo_debug'].includes(s.mode);
if(!headingBusy)showHeading(s.heading_hold);
$('voltage').textContent=s.voltage_v==null?'—':s.voltage_v.toFixed(1)+' V';
const imuActive=!!s.imu_calibration?.active;
if(imuActive!==imuCalibrationActive)zero(false);
imuCalibrationActive=imuActive;
imuState=s.imu_calibration||null;
imuReady=!!s.imu_ok&&s.feedback_age_ms!=null&&s.feedback_age_ms<150;
document.querySelectorAll('[data-mode="hold"],[data-mode="policy"]').forEach(b=>b.disabled=!s.control_ready);
if(s.roller&&!rollerBusy)showRoller(s.roller.enabled);
if(!rollerBusy)showActionSettings(s.deployment);
imuFilters.forEach(([id,key])=>{if(document.activeElement!==$(id)){$(id).value=s.deployment[key];$(id+'-value').textContent=(+$(id).value).toFixed(2);}});
const recovery=s.recovery, pick=s.pick, roulade=s.roulade, sitstand=s.sitstand, rightKick=s.right_kick;
showPreparation(s.action_preparation?.active);
const kick=s.left_kick?.active?s.left_kick:rightKick;
const sitActive=!!sitstand?.active, kickRunning=!!kick?.active;
sitstandReady=!!sitstand&&!!s.control_ready&&recovery?.phase==='walk'&&!pick?.active&&!roulade?.active&&!kickRunning&&!actionPreparing;
showSitstand(sitstand);
showKick(kick);
document.querySelector('[data-mode="policy"]').disabled=!s.control_ready||sitActive||kickRunning;
$('roller-mode').disabled=rollerBusy||sitActive||kickRunning||actionPreparing;
const upright=(s.mode==='shadow'||s.mode==='policy')&&recovery&&recovery.active&&recovery.phase==='walk';
$('pick').disabled=actionPreparing||sitActive||kickRunning||!upright||(pick&&pick.active)||(roulade&&roulade.active);
$('roulade').disabled=$('pick').disabled;
for(const side of ['left','right'])$(side+'_kick').disabled=kickBusy||!s[side+'_kick']||!s.control_ready||$('pick').disabled;
if(recovery){
  const preparingLabel=actionLabels[s.action_preparation?.action]||t('action','动作');
  const phaseLabel=actionPreparing?t(`Preparing ${preparingLabel}`,`准备${preparingLabel}`)
    :kickRunning?actionLabels[kick.side==='left'?'left_kick':'right_kick']
    :sitActive?(sitstand.phase==='sit'?t('Sitting','坐姿'):t('Standing up','正在站起'))
    :roulade?.active?actionLabels.roulade
    :pick?.active?actionLabels.pick
    :recoveryLabels[recovery.phase]||recovery.phase;
  $('recovery-phase').textContent=recovery.active?phaseLabel:t('idle','待机');
  $('active-model').textContent=recovery.active?phaseLabel:'—';
  $('tilt-angle').textContent=recovery.angle_deg==null?'—':recovery.angle_deg.toFixed(1)+'°';
  const walking=recovery.phase==='walk'&&!actionPreparing&&!kickRunning&&!sitActive&&!roulade?.active&&!pick?.active;
  $('recovery-timer').textContent=!recovery.active?t('Stopped','已停止'):walking?t('Ready','就绪'):phaseLabel;
  if(pick?.active)$('mouth').value=Math.round(s.mouth||0);
}
showImuCalibration();
for(const [id,values]of [['acc-values',s.acc_corrected||s.acc],['gyro-values',s.gyro_corrected||s.gyro],['gravity-values',s.gravity],['acc-raw-values',s.acc],['gyro-raw-values',s.gyro]])$(id).textContent=values?values.map(x=>x.toFixed(3)).join(' / '):'—';
$('command-health').textContent=s.control_ready?t('Controls ready.','控制已就绪。'):t('Waiting for feedback.','等待反馈。');
if(s.angles)$('joints').innerHTML=s.motors.map((id,i)=>`<tr class="${s.servo_ids.includes(id)?'':'missing'}"><td>${id}</td><td>${s.angles[i].toFixed(1)}</td><td>${s.targets[i].toFixed(1)}</td></tr>`).join('');
}catch(e){showError(e);}finally{statusBusy=false;}},250);
