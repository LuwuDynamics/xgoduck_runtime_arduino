const $=id=>document.getElementById(id);
const MOTORS=[10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
let statusBusy=false, active=false, centering=null, presentMask=0;
let held=Array(15).fill(false);
let lastRaw=[], messageKey='cal.entering';

async function post(path, body={}){
  const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(x.detail||t('cal.request_failed'));
  return x;
}
function showError(e){$('message').textContent=e.message||String(e);}

MOTORS.forEach((id,i)=>{
  $('joints').insertAdjacentHTML('beforeend',`<div class="cal-row" id="row${i}">
    <div class="cal-meta"><b>ID ${id}</b><span id="raw${i}">${t('cal.waiting')}</span></div>
    <div class="cal-actions"><span id="state${i}">${t('cal.unloaded')}</span><button data-center="${i}" disabled>${t('cal.set')}</button></div>
  </div>`);
});

function refreshRows(){
  document.querySelectorAll('[data-center]').forEach(btn=>{
    const i=+btn.dataset.center;
    btn.disabled=!active||centering!==null||held[i]||!(presentMask&(1<<i));
    $(`row${i}`).classList.toggle('done',held[i]);
    $(`state${i}`).textContent=centering===i?t('cal.centering'):held[i]?t('cal.holding'):t('cal.unloaded');
  });
}

document.querySelectorAll('[data-center]').forEach(btn=>{
  btn.onclick=async()=>{
    const i=+btn.dataset.center;
    centering=i;
    refreshRows();
    $('message').textContent=t('cal.setting',{id:MOTORS[i]});
    messageKey=null;
    try{
      await post('cal/center',{index:i});
      $('message').textContent=t('cal.sent',{id:MOTORS[i]});
    }catch(e){showError(e);}
    finally{centering=null;refreshRows();}
  };
});

async function enter(){
  try{
    await post('cal/enter');
    active=true;
    held.fill(false);
    $('message').textContent=t('cal.ready');
    messageKey='cal.ready';
  }catch(e){showError(e);}
  refreshRows();
}
async function exit(){
  active=false;
  refreshRows();
  try{
    await post('cal/exit');
    held.fill(false);
    $('message').textContent=t('cal.closed');
    messageKey='cal.closed';
  }catch(e){showError(e);}
  refreshRows();
}
$('start').onclick=enter;
$('exit').onclick=exit;
$('exit2').onclick=exit;
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/cal/exit',new Blob(['{}'],{type:'application/json'})));
document.addEventListener('visibilitychange',()=>{if(document.hidden&&active)exit();});

window.addEventListener('xgoduck:langchange',()=>{
  document.querySelectorAll('[data-center]').forEach(btn=>{btn.textContent=t('cal.set');});
  if(messageKey)$('message').textContent=t(messageKey);
  lastRaw.forEach((v,i)=>{
    $(`raw${i}`).textContent=presentMask&(1<<i)?t('cal.encoder',{pos:v}):t('cal.no_feedback');
  });
  refreshRows();
});

setInterval(async()=>{
  if(statusBusy||document.hidden)return;
  statusBusy=true;
  try{
    if(active)await post('cal/heartbeat').catch(()=>{});
    const r=await fetch('/api/status');
    if(!r.ok)throw new Error(t('cal.status_failed'));
    const s=await r.json();
    active=s.mode==='calibrate';
    presentMask=s.raw_mask||0;
    held=s.cal_done||Array(15).fill(false);
    $('mode').textContent=active?t('cal.active'):t('cal.inactive');
    if(s.last_error){$('message').textContent=s.last_error;messageKey=null;}
    if(s.raw_pos){lastRaw=s.raw_pos;s.raw_pos.forEach((v,i)=>{
      $(`raw${i}`).textContent=presentMask&(1<<i)?t('cal.encoder',{pos:v}):t('cal.no_feedback');
    });}
    refreshRows();
  }catch(e){showError(e);}
  finally{statusBusy=false;}
},250);

enter();
