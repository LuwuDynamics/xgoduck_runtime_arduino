const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let active=false, busy=false, scanTask=null, scanController=null, closing=false;
const lockedIds=['start','target-id','read-id','goto-pos','kp','kd','new-id','goto','set-gains','set-id','read','scan'];

async function api(path,body,signal){
  const timeout=AbortSignal.timeout(4000);
  const options={signal:signal?AbortSignal.any([signal,timeout]):timeout};
  if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const r=await fetch('/api/'+path,options);
  const x=await r.json();
  if(!r.ok)throw new Error(x.detail||'Request failed');
  return x;
}
function show(msg){$('message').textContent=msg;}
function lock(value){
  busy=value;
  lockedIds.forEach(id=>$(id).disabled=value);
  $('scan-results').querySelectorAll('button').forEach(b=>b.disabled=value);
  // Exit remains available during a scan and cancels it first.
  $('exit').disabled=$('exit2').disabled=value&&!scanController;
}
function selectId(id){
  $('target-id').value=id;
  $('read-id').value=id;
  $('read-out').textContent='—';
  $('scan-results').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.id===+id)));
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
    if(s.mode==='servo_debug'&&s.mcu_servo_debug&&s.feedback_age_ms!=null&&s.feedback_age_ms<500){
      active=true;
      return;
    }
    await sleep(100);
  }
  throw new Error('No MCU feedback. Servo setup is not ready.');
}
function stopScan(){
  if(scanController){
    $('scan-status').textContent='Stopping scan…';
    scanController.abort();
  }
}
async function exit(){
  if(closing)return;
  closing=true;
  stopScan();
  if(scanTask)await scanTask;
  lock(true);
  try{await api('servo/exit',{});active=false;show('Servo setup closed');}
  catch(e){show(e.message);}
  finally{lock(false);closing=false;}
}
async function send(action,extra={},value=$('target-id').value){
  if(busy||closing)return;
  lock(true);
  try{
    const id=validId(value);
    if(action==='set_id')extra.new_id=validId(extra.new_id);
    await enter();
    if(action==='read')$('read-out').textContent='—';
    show(action==='set_id'?`Changing ID ${id} to ${extra.new_id}…`:`${action} · ID ${id}…`);
    const r=await api('servo',{action,target_id:id,...extra});
    show(`${action} ok`+(action==='read'?` · ID ${id} · raw=${r.raw_pos}`:''));
    if(action==='read')$('read-out').textContent=`raw ${r.raw_pos}`;
    if(action==='set_id'){
      selectId(extra.new_id);
      $('scan-results').replaceChildren();
      $('scan-progress').hidden=true;
      $('scan-status').textContent='ID changed. Scan again to refresh the list.';
      show(`ID ${id} → ${extra.new_id} · verified`);
    }
  }catch(e){show(e.message);}
  finally{lock(false);}
}
async function scan(){
  scanController=new AbortController();
  const signal=scanController.signal;
  lock(true);
  const found=[];
  let checked=0;
  $('scan-results').replaceChildren();
  $('scan-progress').value=0;
  $('scan-progress').hidden=false;
  $('scan-stop').hidden=false;
  $('scan-status').textContent='Preparing scan…';
  try{
    const s=await api('status',undefined,signal);
    if(s.servo_scan_version!==1)throw new Error('Deploy the updated project to enable scanning.');
    await enter(signal);
    // Re-enter on the MCU to turn torque off even after a manual Move to.
    await api('servo',{action:'torque_off',target_id:10},signal);
    show('Scanning · torque off');
    for(let id=0;id<=253;id++){
      signal.throwIfAborted();
      $('scan-status').textContent=`Scanning ID ${id} · ${checked}/254 checked · ${found.length} found`;
      let online=false;
      for(let attempt=0;attempt<2;attempt++){
        // Also refresh the setup heartbeat between probes.
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
        b.setAttribute('aria-pressed',String(id===+$('target-id').value));
        b.onclick=()=>selectId(id);
        $('scan-results').appendChild(b);
      }
      checked++;
      $('scan-progress').value=checked;
    }
    $('scan-status').textContent=`Scan complete · 254 IDs checked · ${found.length} found`;
    show(found.length?'Select a found ID or enter the current ID.':'No servos replied. Check servo power, bus connection and baud rate.');
  }catch(e){
    $('scan-status').textContent=`${signal.aborted?'Stopped':'Scan failed'} · ${checked}/254 checked · ${found.length} found (partial results)`;
    show(signal.aborted?'Scan stopped. Found IDs are kept.':e.message==='servo reply timeout'?'No MCU reply. Scan stopped.':e.message);
  }finally{
    $('scan-stop').hidden=true;
    scanController=null;
    lock(false);
  }
}
$('scan').onclick=()=>{
  if(busy||scanTask||closing)return;
  scanTask=scan().finally(()=>{scanTask=null;});
};
$('scan-stop').onclick=stopScan;
$('target-id').onchange=()=>selectId($('target-id').value);
$('read-id').onchange=()=>selectId($('read-id').value);
$('start').onclick=async()=>{
  if(busy||closing)return;
  lock(true);
  try{await enter();show('Servo setup active');}catch(e){show(e.message);}finally{lock(false);}
};
$('exit').onclick=exit;
$('exit2').onclick=exit;
$('goto').onclick=()=>send('goto',{raw_pos:+$('goto-pos').value});
$('set-gains').onclick=()=>send('set_gains',{kp:+$('kp').value,kd:+$('kd').value});
$('set-id').onclick=()=>send('set_id',{new_id:$('new-id').value});
$('read').onclick=()=>send('read',{},$('read-id').value);
window.addEventListener('pagehide',()=>{
  stopScan();
  if(active)navigator.sendBeacon('/api/servo/exit',new Blob([JSON.stringify({})],{type:'application/json'}));
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
