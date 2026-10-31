const $=id=>document.getElementById(id);
let statusBusy=false, commandBusy=false, commandPending=false, rollerBusy=false;
let rollerEnabled=false;
let sitstandPhase='idle', sitstandBusy=false, sitstandReady=false;
let kickActive=false, kickBusy=false;
const motionRanges={
  walk:{forward:.2,backward:.18,strafe:.3,yaw:1,keyboard:[.2,.15,.5]},
  roller:{forward:.4,backward:.4,strafe:0,yaw:.25,keyboard:[.4,0,.25]}
};
for(let i=0;i<4;i++) $('head-controls').insertAdjacentHTML('beforeend',`<label>head ${i+1}<input id="head${i}" type="range" min="-1" max="1" value="0" step="0.02"></label>`);
$('head-controls').insertAdjacentHTML('beforeend','<label>Mouth 34 (°)<input id="mouth" type="range" min="0" max="30" value="0" step="1"></label>');
async function post(path, body){const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw new Error(x.detail||'request failed');return x;}
function showError(e){$('message').textContent=e.message;}
async function setMode(mode){try{await post('mode',{mode});$('message').textContent='Mode updated';}catch(e){showError(e);}}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
function showRoller(enabled){
  const changed=rollerEnabled!==enabled;
  rollerEnabled=enabled;
  $('roller-mode').checked=enabled;$('roller-state').textContent=enabled?'On':'Off';
  $('roller-warning').hidden=!enabled;
  $('action-profile').textContent=enabled?'Roller walking':'Walking / other models';
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
  try{const r=await post('roller',{enabled});showRoller(r.enabled);showActionSettings(r);$('message').textContent=`Roller mode ${r.enabled?'on':'off'} · ${r.model}`;}
  catch(e){showRoller(!enabled);showError(e);}
  finally{rollerBusy=false;$('roller-mode').disabled=false;}
};
$('pick').onclick=async()=>{try{const r=await post('pick',{});$('message').textContent=`Pick started · ${r.model}`;}catch(e){showError(e);}};
$('roulade').onclick=async()=>{try{const r=await post('roulade',{});$('message').textContent=`Roulade · ${r.model}`;}catch(e){showError(e);}};
function showKick(state){
  const active=!!state?.active;
  if(active!==kickActive)zero(false);
  kickActive=active;
  for(const side of ['left','right'])$(side+'_kick').disabled=kickBusy||active;
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
  if(kickBusy||kickActive)return;
  kickBusy=true;
  for(const foot of ['left','right'])$(foot+'_kick').disabled=true;
  try{
    showKick(await post(side+'_kick',{}));
    $('message').textContent=`${side}_kick · returning to walk after 0.4 s`;
  }catch(e){showError(e);}
  finally{
    kickBusy=false;
    for(const foot of ['left','right'])$(foot+'_kick').disabled=kickActive;
  }
};
function showSitstand(state){
  const phase=state?.phase||'idle';
  if(phase!==sitstandPhase)zero(false);
  sitstandPhase=phase;
  $('sitstand').textContent=phase==='idle'?'Sit':'Stand';
  $('sitstand').disabled=sitstandBusy||phase==='stand'||!sitstandReady||kickActive;
  if(phase!=='idle'){
    document.querySelector('[data-mode="policy"]').disabled=true;
    $('pick').disabled=$('roulade').disabled=true;
    $('roller-mode').disabled=true;
  }
}
$('sitstand').onclick=async()=>{
  if(sitstandBusy||sitstandPhase==='stand'||!sitstandReady)return;
  const action=sitstandPhase==='sit'?'stand':'sit';
  sitstandBusy=true;showSitstand({phase:sitstandPhase});
  try{
    const r=await post('sitstand',{action});showSitstand(r);
    $('message').textContent=action==='sit'?'Sit · holding':`Stand · returning to walk after ${r.stand_seconds} s`;
  }catch(e){showError(e);}
  finally{sitstandBusy=false;showSitstand({phase:sitstandPhase});}
};
$('stop').onclick=()=>{zero();setMode('off')};
const keys=new Set(), sticks={left:{x:0,y:0},right:{x:0,y:0}}, resetSticks=[];
let motion=[0,0,0], targetMotion=[0,0,0];
const deadzone=v=>Math.abs(v)<=.15?0:v;
function updateMotion(){
  if(kickActive){targetMotion=[0,0,0];return;}
  const key=(a,b)=>keys.has(a)-keys.has(b);
  const limits=motionRanges[rollerEnabled?'roller':'walk'];
  const forward=deadzone(sticks.left.y);
  targetMotion=[keys.has('w')||keys.has('s')?key('w','s')*limits.keyboard[0]:forward*(forward<0?limits.backward:limits.forward),
    keys.has('a')||keys.has('d')?key('a','d')*limits.keyboard[1]:deadzone(-sticks.left.x)*limits.strafe,
    keys.has('q')||keys.has('e')?key('q','e')*limits.keyboard[2]:deadzone(-sticks.right.x)*limits.yaw];
  targetMotion[0]=Math.max(-limits.backward,Math.min(limits.forward,targetMotion[0]));
}
function showMotion(){
  ['vx','vy','wz'].forEach((id,i)=>$(id+'Value').textContent=motion[i].toFixed(2));
}
async function sendCommand(){
  if(commandBusy){commandPending=true;return;}
  commandBusy=true;commandPending=false;
  motion=motion.map((old,i)=>.75*old+.25*targetMotion[i]);
  showMotion();
  try{await post('command',{twist:[...motion],head:[0,1,2,3].map(i=>+$('head'+i).value),mouth:+$('mouth').value});}
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
setInterval(async()=>{if(statusBusy||document.hidden)return;statusBusy=true;try{const r=await fetch('/api/status');if(!r.ok)throw new Error('status read failed');const s=await r.json();const modeLabel={shadow:'inference only',off:'torque off',hold:'default pose',policy:'walk',calibrate:'calibrate',servo_debug:'servo setup'};$('rx').textContent=s.feedback_hz.toFixed(1)+' Hz';$('rl').textContent=s.inference_hz.toFixed(1)+' Hz';$('servos').textContent=(s.servo_ids?.length||0)+'/15';$('imu').textContent=s.imu_ok?'online':'offline';$('mode').textContent=(s.left_kick?.active?'left_kick':s.right_kick?.active?'right_kick':s.sitstand?.active?s.sitstand.phase:(modeLabel[s.mode]||s.mode))+(s.enabled?' · enabled':' · torque off');$('message').textContent=s.last_error||`Bridge ${(s.bridge_baud/1e6).toFixed(0)} Mbps · state age ${(s.feedback_age_ms||0).toFixed(1)} ms`;
document.querySelectorAll('[data-mode="hold"],[data-mode="policy"]').forEach(b=>b.disabled=!s.control_ready);
if(s.roller&&!rollerBusy)showRoller(s.roller.enabled);
if(!rollerBusy)showActionSettings(s.deployment);
imuFilters.forEach(([id,key])=>{if(document.activeElement!==$(id)){$(id).value=s.deployment[key];$(id+'-value').textContent=(+$(id).value).toFixed(2);}});
const recovery=s.recovery, pick=s.pick, roulade=s.roulade, sitstand=s.sitstand, rightKick=s.right_kick;
const kick=s.left_kick?.active?s.left_kick:rightKick;
const sitActive=!!sitstand?.active, kickRunning=!!kick?.active;
sitstandReady=!!sitstand&&!!s.control_ready&&recovery?.phase==='walk'&&!pick?.active&&!roulade?.active&&!kickRunning;
showSitstand(sitstand);
showKick(kick);
document.querySelector('[data-mode="policy"]').disabled=!s.control_ready||sitActive||kickRunning;
$('roller-mode').disabled=rollerBusy||sitActive||kickRunning;
const upright=(s.mode==='shadow'||s.mode==='policy')&&recovery&&recovery.active&&recovery.phase==='walk';
$('pick').disabled=sitActive||kickRunning||!upright||(pick&&pick.active)||(roulade&&roulade.active);
$('roulade').disabled=$('pick').disabled;
for(const side of ['left','right'])$(side+'_kick').disabled=kickBusy||!s[side+'_kick']||!s.control_ready||$('pick').disabled;
if(recovery){const phaseLabel=kickRunning?`${kick.side}_kick`:sitActive?sitstand.phase:roulade&&roulade.active?'roulade':pick&&pick.active?'pick':({walk:'walk',default_pose:'get-up · action = 0',getup:'get-up'}[recovery.phase]||recovery.phase);
$('recovery-phase').textContent=recovery.active?phaseLabel:'idle';$('active-model').textContent=s.active_model||'—';$('tilt-angle').textContent=recovery.angle_deg==null?'—':recovery.angle_deg.toFixed(1)+'°';
$('recovery-timer').textContent=!recovery.active?'—':kickRunning?`${kick.side}_kick ${kick.remaining_s.toFixed(2)} s left`:sitActive?(sitstand.phase==='sit'?'sit · holding':`stand ${sitstand.remaining_s.toFixed(2)} s left`):roulade&&roulade.active?`roulade ${roulade.remaining_s.toFixed(2)} / ${roulade.seconds.toFixed(2)} s`:pick&&pick.active?`pick φ ${pick.phi.toFixed(2)} / 1.00`:recovery.phase==='default_pose'?`hold ${recovery.hold_remaining_s.toFixed(2)} s left`:recovery.phase==='getup'?`upright ${recovery.stand_elapsed_s.toFixed(2)} / 1.00 s`:`fallen ${recovery.fall_elapsed_s.toFixed(2)} / 0.15 s`;
if(pick&&pick.active)$('mouth').value=Math.round(s.mouth||0);}
for(const [id,values]of [['acc-values',s.acc],['gyro-values',s.gyro],['gravity-values',s.gravity]])$(id).textContent=values?values.map(x=>x.toFixed(3)).join(' / '):'—';
$('command-health').textContent=`sent ${s.sent} · MCU echo ${s.mcu_command_seq??'—'} · command age ${s.mcu_command_age_us<1e9?(s.mcu_command_age_us/1000).toFixed(1)+' ms':'—'} · gaps ${s.sequence_gaps} · rejected ${s.mcu_invalid_commands??0}`;
if(s.angles)$('joints').innerHTML=s.motors.map((id,i)=>`<tr class="${s.servo_ids.includes(id)?'':'missing'}"><td>${id}</td><td>${s.angles[i].toFixed(1)}</td><td>${s.targets[i].toFixed(1)}</td></tr>`).join('');
}catch(e){showError(e);}finally{statusBusy=false;}},250);
