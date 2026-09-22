const $=id=>document.getElementById(id);
const MOTOR_IDS=[10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
let active=false;

async function post(path, body={}){
  const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(x.detail||'request failed');
  return x;
}
function show(msg){$('message').textContent=msg;}
function showError(e){show(e.message||String(e));}
function targetId(){return +$('target-id').value;}

const readSelect=$('read-id');
MOTOR_IDS.forEach(id=>{
  const opt=document.createElement('option');
  opt.value=id;
  opt.textContent='ID '+id;
  readSelect.appendChild(opt);
});

async function enter(){
  try{await post('servo/enter',{});active=true;show('Servo setup active');}
  catch(e){showError(e);}
}
async function exit(){
  try{await post('servo/exit',{});active=false;show('Servo setup closed');}
  catch(e){showError(e);}
}

async function send(action, extra={}, id=targetId()){
  try{
    if(!active) await enter();
    const r=await post('servo',{action,target_id:id,...extra});
    show(`${action} ok`+(r.raw_pos!=null?` · raw=${r.raw_pos}`:''));
    if(action==='read') $('read-out').textContent=`raw ${r.raw_pos}`;
    return r;
  }catch(e){showError(e);}
}

$('start').onclick=enter;
$('exit').onclick=exit;
$('exit2').onclick=exit;
$('goto').onclick=()=>send('goto',{raw_pos:+$('goto-pos').value});
$('set-gains').onclick=()=>send('set_gains',{kp:+$('kp').value,kd:+$('kd').value});
$('set-id').onclick=()=>send('set_id',{new_id:+$('new-id').value});
$('read').onclick=()=>send('read',{},+$('read-id').value);
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/servo/exit',new Blob([JSON.stringify({})],{type:'application/json'})));
document.addEventListener('visibilitychange',()=>{if(document.hidden&&active)exit();});

setInterval(async()=>{
  if(document.hidden)return;
  try{
    const r=await fetch('/api/status');
    if(!r.ok)return;
    const s=await r.json();
    active=s.mode==='servo_debug';
    $('mode').textContent=s.mode+(s.mcu_servo_debug?' · MCU':'');
    if(active){
      await post('servo/enter',{}).catch(()=>{});
    }
  }catch(_){}
},1000);
