const $=id=>document.getElementById(id);
const MOTORS=[10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
let statusBusy=false, active=false, centering=null, presentMask=0;
let held=Array(15).fill(false);

async function post(path, body={}){
  const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(x.detail||'request failed');
  return x;
}
function showError(e){$('message').textContent=e.message||String(e);}

MOTORS.forEach((id,i)=>{
  $('joints').insertAdjacentHTML('beforeend',`<div class="cal-row" id="row${i}">
    <div class="cal-meta"><b>ID ${id}</b><span id="raw${i}">Waiting for feedback</span></div>
    <div class="cal-actions"><span id="state${i}">Unloaded</span><button data-center="${i}" disabled>Set center &amp; hold</button></div>
  </div>`);
});

function refreshRows(){
  document.querySelectorAll('[data-center]').forEach(btn=>{
    const i=+btn.dataset.center;
    btn.disabled=!active||centering!==null||held[i]||!(presentMask&(1<<i));
    $(`row${i}`).classList.toggle('done',held[i]);
    $(`state${i}`).textContent=centering===i?'Centering…':held[i]?'Holding 2048':'Unloaded';
  });
}

document.querySelectorAll('[data-center]').forEach(btn=>{
  btn.onclick=async()=>{
    const i=+btn.dataset.center;
    centering=i;
    refreshRows();
    $('message').textContent=`Setting the center of ID ${MOTORS[i]}…`;
    try{
      await post('cal/center',{index:i});
      $('message').textContent=`Center command sent to ID ${MOTORS[i]}. It is holding position 2048.`;
    }catch(e){showError(e);}
    finally{centering=null;refreshRows();}
  };
});

async function enter(){
  try{
    await post('cal/enter');
    active=true;
    held.fill(false);
    $('message').textContent='All servos are unloaded. Turn one joint by hand, then set its center.';
  }catch(e){showError(e);}
  refreshRows();
}
async function exit(){
  active=false;
  refreshRows();
  try{
    await post('cal/exit');
    held.fill(false);
    $('message').textContent='Calibration closed. All servos are unloaded.';
  }catch(e){showError(e);}
  refreshRows();
}
$('start').onclick=enter;
$('exit').onclick=exit;
$('exit2').onclick=exit;
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/cal/exit',new Blob(['{}'],{type:'application/json'})));
document.addEventListener('visibilitychange',()=>{if(document.hidden&&active)exit();});

setInterval(async()=>{
  if(statusBusy||document.hidden)return;
  statusBusy=true;
  try{
    if(active)await post('cal/heartbeat').catch(()=>{});
    const r=await fetch('/api/status');
    if(!r.ok)throw new Error('status read failed');
    const s=await r.json();
    active=s.mode==='calibrate';
    presentMask=s.raw_mask||0;
    held=s.cal_done||Array(15).fill(false);
    $('mode').textContent=active?'Calibration active':'Calibration closed';
    if(s.last_error)$('message').textContent=s.last_error;
    if(s.raw_pos)s.raw_pos.forEach((v,i)=>{
      $(`raw${i}`).textContent=presentMask&(1<<i)?'Encoder '+v:'No servo feedback';
    });
    refreshRows();
  }catch(e){showError(e);}
  finally{statusBusy=false;}
},250);

enter();
