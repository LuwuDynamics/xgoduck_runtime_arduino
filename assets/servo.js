const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let active=false, busy=false, selectedId=null, scanTask=null, scanController=null, closing=false, lastRead=null;
const editIds=['kp','kd','new-id','set-gains','set-id','read'];
const protectionIds=['protection-current','enable-protection','disable-protection','set-protection-current'];

async function api(path,body,signal){
  const timeout=AbortSignal.timeout(4000);
  const options={signal:signal?AbortSignal.any([signal,timeout]):timeout};
  if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const r=await fetch('/api/'+path,options);
  const x=await r.json();
  if(!r.ok)throw new Error(x.detail||t('srv.request_failed'));
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
  lastRead=null;
  $('read-out').textContent='—';
  $('scan-results').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.id===selectedId)));
  lock(false);
  show(t('srv.selected',{id:selectedId}));
  send('read');
}
function validId(value){
  const id=Number(value);
  if(String(value).trim()===''||!Number.isInteger(id)||id<0||id>253)throw new Error(t('srv.id_range'));
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
  throw new Error(t('srv.no_mcu'));
}
function stopScan(){
  if(scanController){
    $('scan-status').textContent=t('srv.stopping_scan');
    scanController.abort();
  }
}
async function exit(){
  if(closing)return;
  closing=true;
  stopScan();
  if(scanTask)await scanTask;
  lock(true);
  try{await api('servo/exit',{});active=false;show(t('srv.closed'));}
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
    show(action==='set_id'
      ?t('srv.changing_id',{id,new_id:extra.new_id})
      :`ID ${id} · ${action==='set_gains'?t('srv.action_gains'):t('srv.action_read')}…`);
    const r=await api('servo',{action,target_id:id,...extra});
    if(action==='read'){
      lastRead={id,pos:r.raw_pos,kp:r.kp,kd:r.kd};
      $('read-out').textContent=t('srv.readout',lastRead);
      show(t('srv.params_read',{id}));
    }else if(action==='set_id'){
      selectedId=null;
      lastRead=null;
      $('read-out').textContent='—';
      $('scan-results').replaceChildren();
      $('scan-progress').hidden=true;
      $('scan-status').textContent=t('srv.id_changed_scan');
      show(t('srv.verified',{id,new_id:extra.new_id}));
    }else{
      show(t('srv.kp_written',{id}));
    }
  }catch(e){show(e.message);}
  finally{lock(false);}
}
async function scan(){
  scanController=new AbortController();
  const signal=scanController.signal;
  selectedId=null;
  lastRead=null;
  $('read-out').textContent='—';
  lock(true);
  const found=[];
  let checked=0;
  $('scan-results').replaceChildren();
  $('scan-progress').value=0;
  $('scan-progress').hidden=false;
  $('scan-stop').hidden=false;
  $('scan-status').textContent=t('srv.preparing_scan');
  try{
    const s=await api('status',undefined,signal);
    if(s.servo_scan_version!==2)throw new Error(t('srv.deploy'));
    await enter(signal);
    await api('servo',{action:'torque_off',target_id:10},signal);
    show(t('srv.scanning_torque_off'));
    for(let id=0;id<=253;id++){
      signal.throwIfAborted();
      $('scan-status').textContent=t('srv.scanning',{id,checked,found:found.length});
      let online=false;
      for(let attempt=0;attempt<2;attempt++){
        await api('servo/enter',{},signal);
        signal.throwIfAborted();
        const r=await api('servo',{action:'ping',target_id:id},signal);
        if(r.op!==7||r.target_id!==id||typeof r.ok!=='boolean')throw new Error(t('srv.invalid_scan_reply'));
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
    $('scan-status').textContent=t('srv.scan_complete',{found:found.length});
    show(found.length?t('srv.select_found'):t('srv.no_servos'));
  }catch(e){
    $('scan-status').textContent=`${signal.aborted?t('srv.stopped'):t('srv.scan_failed')} · ${t('srv.partial',{checked,found:found.length})}`;
    show(signal.aborted?t('srv.scan_stopped_continue'):e.message==='servo reply timeout'?t('srv.no_mcu_scan_stopped'):e.message);
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
      if(!input||!Number.isFinite(current)||current<0||current>65535*.0065)throw new Error(t('srv.protection_current_range'));
      body.current_a=current;
    }else{
      body.protection_enabled=enabled;
    }
    await enter();
    show(t('srv.broadcasting'));
    const r=await api('servo',body);
    if(!r.ok||r.broadcast!==true||r.target_id!==254||r.op!==(settingCurrent?10:9))throw new Error(t('srv.invalid_broadcast'));
    if(settingCurrent){
      $('protection-current-out').textContent=t('srv.current_sent',{current:r.current_a.toFixed(4)});
    }else{
      $('protection-out').textContent=t('srv.protection_sent',{state:r.protection_enabled?t('idx.mode.on'):t('idx.mode.off')});
    }
    show(t('srv.protection_broadcast'));
  }catch(e){show(e.message);}
  finally{lock(false);}
}
$('scan').onclick=()=>{
  if(busy||scanTask||closing)return;
  scanTask=scan().finally(()=>{scanTask=null;});
};
$('scan-stop').onclick=stopScan;
$('exit').onclick=exit;
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

window.addEventListener('xgoduck:langchange',()=>{
  if(lastRead)$('read-out').textContent=t('srv.readout',lastRead);
  if(scanController){
    $('scan-status').textContent=t('srv.scanning_torque_off');
  }else{
    $('scan-status').textContent=t('srv.scan_status_init');
    if(!busy&&!closing)show(t('srv.initial'));
  }
});
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
