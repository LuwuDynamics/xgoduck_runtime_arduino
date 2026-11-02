/* Read-only ToF view. Colours animate real snapshots; no synthetic distances. */
(() => {
  const $=id=>document.getElementById(id),t=(key,values)=>window.DuckI18n.t(key,values);
  const panel=$('tofPanel'),grid=$('tofGrid'),cells=[];
  const states={starting:'tofStarting',initializing:'tofStarting',ranging:'tofLive',absent:'tofAbsent',offline:'tofOffline',error:'tofError',unavailable:'tofUnavailable',disconnected:'tofDisconnected',stale:'tofStale',empty:'tofEmpty'};
  let expanded=true,data=null,received=0,timer=null,request=null,stopped=false;
  try{expanded=localStorage.getItem('duck-tof-expanded')!=='false';}catch{}
  for(let i=0;i<64;i++){
    const cell=document.createElement('span');cell.className='tof-cell';
    if([27,28,35,36].includes(i))cell.classList.add('tof-middle');
    grid.append(cell);cells.push(cell);
  }
  function distance(value){
    if(!Number.isFinite(value)||value<=0)return ['—',''];
    return value>=1000?[(value/1000).toFixed(2),'m']:[(value/10).toFixed(1),'cm'];
  }
  function measure(id,value){const [number,unit]=distance(value);$(id).textContent=number;$(id+'Unit').textContent=unit;return number==='—'?'—':number+' '+unit;}
  function render(){
    const age=data?.age_ms==null?null:data.age_ms+Math.max(0,performance.now()-received);
    const fresh=!!data?.fresh&&age!==null&&age<=1000;
    const matrix=Array.isArray(data?.grid_mm)&&data.grid_mm.length===8?data.grid_mm.flat():[];
    const values=cells.map((_,i)=>fresh&&Number.isFinite(matrix[i])&&matrix[i]>0?matrix[i]:null);
    const count=values.filter(v=>v!==null).length;
    let state=data?.state||'starting';
    if(state==='ranging')state=!fresh?'stale':!count?'empty':'ranging';
    panel.dataset.state=state;panel.classList.toggle('is-live',state==='ranging');
    $('tofStatus').textContent=t(states[state]||'tofUnavailable');
    $('tofCompact').textContent=measure('tofCenter',fresh?data?.center_mm:null);
    measure('tofNearest',fresh?data?.min_mm:null);
    $('tofValid').textContent=t('tofValid',{count});
    $('tofTiming').textContent=fresh?t('tofTiming',{hz:data.frequency_hz||5,age:Math.round(age)}):'—';
    $('tofFrame').textContent='# '+(data?.seq||'—');
    let reason='';
    if(state==='unavailable')reason=t(/duck_tof.*not available/.test(data?.error_message||'')?'tofRpcMissing':'tofReadFailed');
    else if(state==='error'||state==='offline')reason=t('tofSensorError');
    else if(state==='absent')reason=t('tofNotConnected');
    else if(state==='stale')reason=t('tofStaleHint');
    else if(state==='empty')reason=t('tofNoTarget');
    else if(state==='disconnected')reason=t('tofConnectionLost');
    else if(state==='starting'||state==='initializing')reason=t('tofWaiting');
    $('tofMessage').textContent=reason;$('tofMessage').hidden=!reason;
    $('tofMessage').title=data?.error_message||'';
    grid.setAttribute('aria-label',t('tofGridDescription',{count,distance:distance(fresh?data?.center_mm:null).join(' ').trim()}));
    cells.forEach((cell,i)=>{
      const mm=values[i];cell.classList.toggle('is-valid',mm!==null);
      if(mm===null){cell.style.removeProperty('--depth-color');cell.title=t('tofZoneInvalid',{row:Math.floor(i/8)+1,column:i%8+1});}
      else{
        // Fixed 0–2 m colour scale; numeric values are never capped at 2 m.
        const hue=14+Math.min(1,mm/2000)*173;
        cell.style.setProperty('--depth-color',`hsl(${hue.toFixed(1)} 78% 58%)`);
        cell.title=t('tofZone',{row:Math.floor(i/8)+1,column:i%8+1,distance:mm});
      }
    });
  }
  function layout(){
    panel.classList.toggle('is-collapsed',!expanded);$('tofBody').hidden=!expanded;$('tofSummary').hidden=expanded;
    $('tofToggle').textContent=t(expanded?'tofCollapse':'tofExpand');$('tofToggle').setAttribute('aria-expanded',String(expanded));
    window.dispatchEvent(new CustomEvent('duck-tof-layout'));
  }
  window.DuckToF={get expanded(){return expanded;}};
  $('tofToggle').onclick=()=>{expanded=!expanded;try{localStorage.setItem('duck-tof-expanded',String(expanded));}catch{}layout();};
  function schedule(delay){clearTimeout(timer);if(!document.hidden&&!stopped)timer=setTimeout(poll,delay);}
  async function poll(){
    if(request||document.hidden||stopped)return;
    const controller=new AbortController();request=controller;
    const deadline=setTimeout(()=>controller.abort(),2500),started=performance.now();
    try{
      const response=await fetch('/api/tof/status',{signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw Error('ToF HTTP '+response.status);
      const snapshot=await response.json();
      if(!controller.signal.aborted){
        // Include transit time: a delayed reply cannot make an old frame fresh.
        if(Number.isFinite(snapshot.age_ms))snapshot.age_ms+=performance.now()-started;
        data=snapshot;received=performance.now();
      }
    }catch{
      if(!document.hidden&&!stopped){data={state:'disconnected',fresh:false};received=performance.now();}
    }finally{
      clearTimeout(deadline);request=null;render();
      schedule(Math.max(0,(data?.state==='disconnected'?1500:250)-(performance.now()-started)));
    }
  }
  function suspend(){clearTimeout(timer);request?.abort();data=null;render();}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend();else schedule(0);});
  window.addEventListener('pagehide',()=>{stopped=true;suspend();});
  window.addEventListener('pageshow',()=>{stopped=false;schedule(0);});
  window.addEventListener('duck-languagechange',()=>{layout();render();});
  // Invalidate the display during a stalled HTTP read, before that read times out.
  setInterval(()=>{if(!document.hidden&&!stopped)render();},250);
  layout();render();schedule(0);
})();
