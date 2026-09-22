const $=id=>document.getElementById(id);
const MOTORS=[10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
let statusBusy=false, targetBusy=false, targetPending=false, active=false;
const targets=Array(15).fill(2047);

async function post(path, body={}){
  const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(x.detail||'request failed');
  return x;
}
function showError(e){$('message').textContent=typeof e==='string'?e:(e.message||String(e));}

const list=$('joints');
MOTORS.forEach((id,i)=>{
  list.insertAdjacentHTML('beforeend',`<div class="cal-row" id="row${i}">
    <div class="cal-meta"><b>ID ${id}</b><span id="raw${i}">—</span><span id="zero${i}">zero —</span></div>
    <input id="sl${i}" type="range" min="0" max="4095" value="2047">
    <div class="cal-actions"><span id="val${i}">2047</span><button data-finish="${i}">Finish</button></div>
  </div>`);
  $(`sl${i}`).oninput=()=>{
    targets[i]=+$(`sl${i}`).value;
    $(`val${i}`).textContent=targets[i];
    queueTargets();
  };
});

function queueTargets(){
  if(!active) return;
  if(targetBusy){targetPending=true;return;}
  targetBusy=true;targetPending=false;
  post('cal/targets',{targets:[...targets]}).catch(showError).finally(()=>{
    targetBusy=false;
    if(targetPending) queueTargets();
  });
}

document.querySelectorAll('[data-finish]').forEach(btn=>{
  btn.onclick=async()=>{
    try{
      const i=+btn.dataset.finish;
      const r=await post('cal/finish',{index:i});
      $(`row${i}`).classList.add('done');
      $(`zero${i}`).textContent='zero '+r.zero_pos;
      $('message').textContent=`Joint ${MOTORS[i]} zero saved as ${r.zero_pos}`;
    }catch(e){showError(e);}
  };
});

async function enter(){
  try{
    await post('cal/enter',{});
    active=true;
    targets.fill(2047);
    MOTORS.forEach((_,i)=>{$(`sl${i}`).value=2047;$(`val${i}`).textContent='2047';});
    $('message').textContent='Calibration active: temporary KP = 3, KD = 0, target 2047';
  }catch(e){showError(e);}
}
async function exit(){
  try{
    await post('cal/exit',{});
    active=false;
    $('message').textContent='Calibration closed. Position modes are available from the control page.';
  }catch(e){showError(e);}
}
$('start').onclick=enter;
$('exit').onclick=exit;
$('exit2').onclick=exit;
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/cal/exit',new Blob([JSON.stringify({})],{type:'application/json'})));
document.addEventListener('visibilitychange',()=>{if(document.hidden&&active)exit();});

setInterval(async()=>{
  if(statusBusy||document.hidden)return;
  statusBusy=true;
  try{
    const r=await fetch('/api/status');
    if(!r.ok)throw new Error('status read failed');
    const s=await r.json();
    active=s.mode==='calibrate';
    $('mode').textContent=s.mode+(s.mcu_calibrating?' · MCU':'');
    if(s.last_error)$('message').textContent=s.last_error;
    if(s.cal_done)s.cal_done.forEach((d,i)=>$(`row${i}`).classList.toggle('done',!!d));
    if(s.raw_pos)s.raw_pos.forEach((v,i)=>$(`raw${i}`).textContent='encoder '+v);
    if(s.zero_pos)s.zero_pos.forEach((v,i)=>$(`zero${i}`).textContent='zero '+v);
    if(active){
      // Keep the calibration session alive. Status reads do not refresh its heartbeat.
      if(!targetBusy) post('cal/targets',{targets:[...targets]}).catch(()=>{});
    }
  }catch(e){showError(e);}
  finally{statusBusy=false;}
},250);
