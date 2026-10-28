const $=id=>document.getElementById(id);
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
  if(!r.ok)throw new Error(x.detail||'Request failed');
  return x;
}
function show(msg){$('message').textContent=msg;}
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
  $('read-out').textContent='—';
  $('scan-results').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.id===selectedId)));
  lock(false);
  show('Selected ID '+selectedId);
  send('read');
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
async function send(action,extra={}){
  if(busy||closing||selectedId===null)return;
  lock(true);
  try{
    const id=selectedId;
    if(action==='set_id')extra.new_id=validId(extra.new_id);
    await enter();
    show(action==='set_id'?`Changing ID ${id} to ${extra.new_id}…`:`ID ${id} · ${action==='reset_mid'?'Reset Mid':action==='set_gains'?'Set KpKd':'Read'}…`);
    const r=await api('servo',{action,target_id:id,...extra});
    if(action==='read'){
      $('read-out').textContent=`ID ${id} · Pos ${r.raw_pos} · KP ${r.kp} · KD ${r.kd}`;
      show(`ID ${id} · parameters read`);
    }else if(action==='reset_mid'){
      $('read-out').textContent='—';
      show(`ID ${id} · Mid reset sent. Servo remains unloaded.`);
    }else if(action==='set_id'){
      selectedId=null;
      $('read-out').textContent='—';
      $('scan-results').replaceChildren();
      $('scan-progress').hidden=true;
      $('scan-status').textContent='ID changed. Scan again to select the updated servo.';
      show(`ID ${id} → ${extra.new_id} · verified`);
    }else{
      show(`ID ${id} · KP/KD written`);
    }
  }catch(e){show(e.message);}
  finally{lock(false);}
}
async function scan(){
  scanController=new AbortController();
  const signal=scanController.signal;
  selectedId=null;
  $('read-out').textContent='—';
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
    if(s.servo_scan_version!==2)throw new Error('Deploy the updated project to enable servo setup.');
    await enter(signal);
    await api('servo',{action:'torque_off',target_id:10},signal);
    show('Scanning · torque off');
    for(let id=0;id<=253;id++){
      signal.throwIfAborted();
      $('scan-status').textContent=`Scanning ID ${id} · ${checked}/254 checked · ${found.length} found`;
      let online=false;
      for(let attempt=0;attempt<2;attempt++){
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
        b.setAttribute('aria-pressed','false');
        b.onclick=()=>selectId(id);
        $('scan-results').appendChild(b);
      }
      checked++;
      $('scan-progress').value=checked;
    }
    $('scan-status').textContent=`Scan complete · 254 IDs checked · ${found.length} found`;
    show(found.length?'Select a found ID to read or change its settings.':'No servos replied. Check servo power, bus connection and baud rate.');
  }catch(e){
    $('scan-status').textContent=`${signal.aborted?'Stopped':'Scan failed'} · ${checked}/254 checked · ${found.length} found (partial results)`;
    show(signal.aborted?'Scan stopped. Select a found ID to continue.':e.message==='servo reply timeout'?'No MCU reply. Scan stopped.':e.message);
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
      if(!input||!Number.isFinite(current)||current<0||current>65535*.0065)throw new Error('Enter a protection current between 0 and 425.9775 A.');
      body.current_a=current;
    }else{
      body.protection_enabled=enabled;
    }
    await enter();
    show('Broadcasting protection settings to all servos…');
    const r=await api('servo',body);
    if(!r.ok||r.broadcast!==true||r.target_id!==254||r.op!==(settingCurrent?10:9))throw new Error('Invalid broadcast reply');
    if(settingCurrent){
      $('protection-current-out').textContent=`Broadcast sent · ${r.current_a.toFixed(4)} A`;
    }else{
      $('protection-out').textContent=`Broadcast sent · voltage / overcurrent ${r.protection_enabled?'on':'off'}`;
    }
    show('Protection broadcast sent. Individual servo writes are not verified.');
  }catch(e){show(e.message);}
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
lock(false);
