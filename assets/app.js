const $=id=>document.getElementById(id);
let statusBusy=false, commandBusy=false, commandPending=false, rollerBusy=false;
let rollerEnabled=false;
let sitstandPhase='idle', sitstandBusy=false, sitstandReady=false;
let kickActive=false, kickBusy=false;
let imuCalibrationActive=false, imuCalibrationBusy=false, imuCalibrationError='', imuReady=false;
let imuState=null, controlModeAvailable=true;
let actionPreparing=false;
const motionRanges={
  walk:{forward:.2,backward:.2,strafe:.3,yaw:1,keyboard:[.2,.15,.5]},
  roller:{forward:.4,backward:.4,strafe:0,yaw:.45,keyboard:[.4,0,.45]}
};
for(let i=0;i<4;i++) $('head-controls').insertAdjacentHTML('beforeend',`<label>Head ${i+1}<input id="head${i}" type="range" min="-1" max="1" value="0" step="0.02"></label>`);
$('head-controls').insertAdjacentHTML('beforeend','<label>Mouth<input id="mouth" type="range" min="0" max="30" value="0" step="1"></label>');
function showPreparation(active){
  actionPreparing=!!active;
  if(actionPreparing)for(const id of ['pick','roulade','sitstand','left_kick','right_kick','roller-mode'])$(id).disabled=true;
}
async function post(path, body){const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw new Error(x.detail||'request failed');if(x.preparing)showPreparation(true);return x;}
function showError(e){$('message').textContent=e.message;}
async function setMode(mode){try{await post('mode',{mode});$('message').textContent='Mode updated';}catch(e){showError(e);}}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
function showRoller(enabled){
  const changed=rollerEnabled!==enabled;
  rollerEnabled=enabled;
  $('roller-mode').checked=enabled;$('roller-state').textContent=enabled?'On':'Off';
  $('roller-warning').hidden=!enabled;
  $('action-profile').textContent=enabled?'Skating':'Walking / other models';
  const limits=motionRanges[enabled?'roller':'walk'];
  $('motion-limits').textContent=`Limits: vx -${limits.backward} to +${limits.forward}, vy ${limits.strafe?'±'+limits.strafe:'0'}, ωz ±${limits.yaw}.`;
  $('left-label').textContent=enabled?'Forward / backward':'Forward / strafe';
  if(changed)zero(false);
}
$('roller-mode').onchange=async()=>{
  if(rollerBusy)return;
  const enabled=$('roller-mode').checked;
  showRoller(enabled);
  rollerBusy=true;$('roller-mode').disabled=true;
  try{const r=await post('roller',{enabled});showRoller(r.enabled);showActionSettings(r);$('message').textContent=`Skating mode ${r.enabled?'on':'off'}`;}
  catch(e){showRoller(!enabled);showError(e);}
  finally{rollerBusy=false;$('roller-mode').disabled=false;}
};
$('pick').onclick=async()=>{try{await post('pick',{});$('message').textContent='Pick';}catch(e){showError(e);}};
$('roulade').onclick=async()=>{try{await post('roulade',{});$('message').textContent='Roulade';}catch(e){showError(e);}};
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
    $('message').textContent=side==='left'?'Left kick':'Right kick';
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
  $('sitstand').textContent=phase==='idle'?'Sit':'Stand';
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
    $('message').textContent=action==='sit'?'Sitting':'Standing up';
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
  motion=motion.map((old,i)=>.75*old+.25*targetMotion[i]);
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
  $('imu-calibrate').textContent=imuCalibrationActive?`Calibrating… ${Math.round((imuState.progress||0)*100)}%`:'Calibrate IMU';
  $('imu-calibration-progress').hidden=!imuCalibrationActive;
  $('imu-calibration-progress').value=imuState?.progress||0;
  $('imu-calibration-status').textContent=imuCalibrationError||imuState?.message||'Reading calibration status…';
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
  }catch(e){imuCalibrationError=e.message;}
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
setInterval(async()=>{if(statusBusy||document.hidden)return;statusBusy=true;try{const r=await fetch('/api/status');if(!r.ok)throw new Error('status read failed');const s=await r.json();const modeLabel={shadow:'inference only',off:'torque off',hold:'default pose',policy:'walk',calibrate:'calibration',servo_debug:'servo setup'};$('rx').textContent=s.feedback_hz.toFixed(1)+' Hz';$('rl').textContent=s.inference_hz.toFixed(1)+' Hz';$('servos').textContent=(s.servo_ids?.length||0)+'/15';$('imu').textContent=s.imu_ok?'online':'offline';$('mode').textContent=(s.left_kick?.active?'left kick':s.right_kick?.active?'right kick':s.sitstand?.active?s.sitstand.phase:(modeLabel[s.mode]||s.mode))+(s.enabled?' · enabled':' · torque off');$('message').textContent=s.last_error||(s.control_ready?'Robot ready':'Waiting for robot…');
controlModeAvailable=!['calibrate','servo_debug'].includes(s.mode);
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
if(recovery){const preparingLabel={pick:'Pick',roulade:'Roulade',sit:'Sit',stand:'Stand',left_kick:'Left kick',right_kick:'Right kick'}[s.action_preparation?.action];const phaseLabel=actionPreparing?'Preparing '+preparingLabel:kickRunning?(kick.side==='left'?'Left kick':'Right kick'):sitActive?(sitstand.phase==='sit'?'Sitting':'Standing up'):roulade&&roulade.active?'Roulade':pick&&pick.active?'Pick':({walk:'Walking',default_pose:'Preparing to get up',getup:'Getting up'}[recovery.phase]||recovery.phase);
$('recovery-phase').textContent=recovery.active?phaseLabel:'idle';$('active-model').textContent=recovery.active?phaseLabel:'—';$('tilt-angle').textContent=recovery.angle_deg==null?'—':recovery.angle_deg.toFixed(1)+'°';
$('recovery-timer').textContent=!recovery.active?'Stopped':recovery.phase==='walk'&&!actionPreparing&&!kickRunning&&!sitActive&&!roulade?.active&&!pick?.active?'Ready':phaseLabel;
if(pick&&pick.active)$('mouth').value=Math.round(s.mouth||0);}
showImuCalibration();
for(const [id,values]of [['acc-values',s.acc_corrected||s.acc],['gyro-values',s.gyro_corrected||s.gyro],['gravity-values',s.gravity],['acc-raw-values',s.acc],['gyro-raw-values',s.gyro]])$(id).textContent=values?values.map(x=>x.toFixed(3)).join(' / '):'—';
$('command-health').textContent=s.control_ready?'Controls ready.':'Waiting for feedback.';
if(s.angles)$('joints').innerHTML=s.motors.map((id,i)=>`<tr class="${s.servo_ids.includes(id)?'':'missing'}"><td>${id}</td><td>${s.angles[i].toFixed(1)}</td><td>${s.targets[i].toFixed(1)}</td></tr>`).join('');
}catch(e){showError(e);}finally{statusBusy=false;}},250);
