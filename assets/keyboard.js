'use strict';
const $ = id => document.getElementById(id);
const bindings = {KeyW:'KeyW',ArrowUp:'KeyW',KeyS:'KeyS',ArrowDown:'KeyS',KeyA:'KeyA',KeyD:'KeyD',KeyQ:'KeyQ',ArrowLeft:'KeyQ',KeyE:'KeyE',ArrowRight:'KeyE'};
const held = new Set();
let state = null, connected = false, focused = document.hasFocus(), busy = false, pending = false, actionBusy = false;
let velocity = [0,0,0], context = '';
const message = text => { $('message').textContent = text; };
async function api(path, body) {
  const r = await fetch('/api/'+path, {method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(800)});
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || '请求失败');
  return data;
}
function actionActive() {
  return !!(state?.pick?.active || state?.roulade?.active || state?.sitstand?.active || state?.left_kick?.active || state?.right_kick?.active);
}
function canControl() {
  return connected && !actionBusy && !actionActive() && ['shadow','policy'].includes(state?.mode) && state?.control_ready && state?.recovery?.phase==='walk';
}
function renderKeys() {
  const active = new Set([...held].map(code=>bindings[code]));
  document.querySelectorAll('[data-key]').forEach(el=>el.classList.toggle('active',active.has(el.dataset.key)));
  ['vx','vy','wz'].forEach((id,i)=>$(id).textContent=velocity[i].toFixed(2));
  $('keyboard-state').textContent=!connected?'键盘默认启用 · 等待连接':!focused||document.hidden?'键盘默认启用 · 返回页面后重新按键即可':canControl()?'键盘已就绪 · 按住移动，松开停止':'键盘默认启用 · 等待行走模式就绪';
}
function target() {
  if (!focused || document.hidden || !canControl()) return [0,0,0];
  const keys = new Set([...held].map(code=>bindings[code]));
  const axis = (a,b)=>Number(keys.has(a))-Number(keys.has(b));
  const scale = Number($('speed').value)/100, roller = state?.roller?.enabled;
  const forward = axis('KeyW','KeyS');
  return [forward*(roller?.8:forward<0?.4:.7)*scale,axis('KeyA','KeyD')*(roller?0:.6)*scale,axis('KeyQ','KeyE')*(roller?.5:2)*scale];
}
function clearMotion(send=true) {
  held.clear();velocity=[0,0,0];renderKeys();
  if(send) sendCommand(true);
}
function disconnected(error) {
  connected=false;clearMotion(false);renderStatus();message('连接中断：'+error.message+'。恢复后重新按键即可。');
}
async function sendCommand(force=false) {
  if (busy) {pending=true;return;}
  if (!force && (!focused || document.hidden || !canControl())) return;
  busy=true;pending=false;
  velocity=target();renderKeys();
  try { await api('command',{twist:[...velocity]}); }
  catch(error) { disconnected(error); }
  finally {busy=false;if(pending){pending=false;sendCommand(true);}}
}
async function perform(path,body) {
  if(actionBusy)return;
  clearMotion();actionBusy=true;renderStatus();
  try {await api(path,body);message('已执行');await refresh();}
  catch(error){message(error.message);}
  finally{actionBusy=false;renderStatus();}
}
$('zero').onclick=()=>clearMotion();
$('stop').onclick=()=>{
  clearMotion(); // Off must remain available even while another request is running.
  api('mode',{mode:'off'}).then(()=>refresh()).catch(disconnected);
};
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>perform('mode',{mode:button.dataset.mode}));
document.querySelectorAll('[data-action]').forEach(button=>button.onclick=()=>perform(button.dataset.action,{}));
$('sitstand').onclick=()=>perform('sitstand',{action:state?.sitstand?.phase==='sit'?'stand':'sit'});
$('roller').onchange=()=>perform('roller',{enabled:$('roller').checked});
$('speed').oninput=()=>{clearMotion();$('speed-value').textContent=$('speed').value+'%';renderLimits();};
let rampBusy=false;
function showRamp(factor) {
  $('ramp-value').textContent=Number(factor).toFixed(2)+'×';
  $('ramp-info').textContent=`加减速上限：前后 ${(0.5*factor).toFixed(2)} m/s²，侧移 ${(0.4*factor).toFixed(2)} m/s²，转向 ${(1.5*factor).toFixed(2)} rad/s²。满幅侧移停止约 ${(0.6/(0.4*factor)).toFixed(2)} 秒。`;
}
$('ramp').oninput=()=>showRamp(Number($('ramp').value));
$('ramp').onchange=async()=>{
  clearMotion();rampBusy=true;$('ramp').disabled=true;
  try {const r=await api('motion-ramp',{factor:Number($('ramp').value)});$('ramp').value=r.factor;showRamp(r.factor);}
  catch(error){if(state?.deployment?.motion_ramp_factor){$('ramp').value=state.deployment.motion_ramp_factor;showRamp(Number($('ramp').value));}message(error.message);}
  finally{rampBusy=false;$('ramp').disabled=!connected;}
};
function editable(event) {return event.target instanceof Element && !!event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])');}
window.addEventListener('keydown',event=>{
  if(event.code==='Space'){event.preventDefault();if(!event.repeat)$('stop').click();return;}
  if(event.code==='Escape'){event.preventDefault();clearMotion();return;}
  if(!bindings[event.code])return;
  if(event.ctrlKey||event.altKey||event.metaKey||event.isComposing||editable(event)){if(held.size)clearMotion();return;}
  if(!focused||document.hidden||!canControl())return;
  event.preventDefault();
  if(event.repeat||held.has(event.code))return;
  held.add(event.code);sendCommand();
});
window.addEventListener('keyup',event=>{if(held.delete(event.code)){event.preventDefault();sendCommand();}});
window.addEventListener('blur',()=>{focused=false;clearMotion();});
window.addEventListener('focus',()=>{focused=true;clearMotion(false);});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearMotion();else renderKeys();});
document.addEventListener('focusin',event=>{if(editable(event)&&held.size)clearMotion();});
window.addEventListener('pagehide',()=>{
  clearMotion(false);
  navigator.sendBeacon('/api/command',new Blob([JSON.stringify({twist:[0,0,0]})],{type:'application/json'}));
});
function renderLimits() {
  const roller=state?.roller?.enabled, scale=Number($('speed').value)/100;
  $('limits').textContent=`当前速度上限：前进 ${((roller?.8:.7)*scale).toFixed(2)} / 后退 ${((roller?.8:.4)*scale).toFixed(2)} / 侧移 ${((roller?0:.6)*scale).toFixed(2)} / 转向 ${((roller?.5:2)*scale).toFixed(2)}`;
  for(const key of ['KeyA','KeyD'])document.querySelector(`[data-key="${key}"]`).classList.toggle('unavailable',!!roller);
}
function renderStatus() {
  renderKeys();
  $('ramp').disabled=!connected||rampBusy;
  document.querySelectorAll('[data-mode]').forEach(b=>b.disabled=!connected||actionBusy||(b.dataset.mode!=='shadow'&&!state?.control_ready));
  const active=actionActive(), upright=canControl();
  document.querySelectorAll('[data-action]').forEach(b=>b.disabled=!upright);
  $('sitstand').textContent=state?.sitstand?.phase==='sit'?'站起':'坐下';
  $('sitstand').disabled=!connected||actionBusy||!(upright||(state?.sitstand?.phase==='sit'&&state?.control_ready));
  $('roller').disabled=!connected||actionBusy||active||['calibrate','servo_debug'].includes(state?.mode);
  $('roller').checked=!!state?.roller?.enabled;
  $('roller-warning').hidden=!state?.roller?.enabled;
  renderLimits();
}
let polling=false;
async function refresh() {
  if(polling)return;
  polling=true;
  try {
    const next=await api('status');
    const nextContext=JSON.stringify([next.mode,next.roller?.enabled,next.recovery?.phase,!!next.pick?.active,!!next.roulade?.active,next.sitstand?.phase,!!next.left_kick?.active,!!next.right_kick?.active]);
    state=next;connected=true;
    if(context!==nextContext||!canControl()) {if(held.size)clearMotion();context=nextContext;}
    const labels={off:'卸力',shadow:'仅推理',hold:'默认姿态',policy:'行走',calibrate:'校准',servo_debug:'舵机设置'};
    $('mode').textContent=(labels[state.mode]||state.mode)+(state.mode==='off'?'':state.enabled?' · 已上力':' · 卸力');
    if(!rampBusy&&document.activeElement!==$('ramp')){$('ramp').value=state.deployment?.motion_ramp_factor??1;showRamp(Number($('ramp').value));}
    $('applied-motion').textContent='控制器平滑后速度：'+(state.motion_command||[0,0,0]).map(v=>Number(v).toFixed(3)).join(' / ');
    $('servos').textContent=(state.servo_ids?.length||0)+'/15';
    $('imu').textContent=state.imu_ok?'在线':'离线';
    $('rx').textContent=Number(state.feedback_hz).toFixed(1)+' Hz';
    $('model').textContent='当前模型：'+(state.active_model||'—')+' · 恢复状态：'+(state.recovery?.phase||'—');
    message(state.last_error||'已连接 · '+(state.enabled?'电机已上力':'电机已卸力'));
    renderStatus();
  }catch(error){disconnected(error);}
  finally{polling=false;}
}
renderKeys();renderLimits();showRamp(Number($('ramp').value));refresh();
setInterval(()=>{if(!document.hidden)refresh();},250);
setInterval(()=>{if(held.size&&focused&&!document.hidden)sendCommand();},50);
