/* Display the saved service region and allow the user to choose another. */
(() => {
  const ids=['openBinding','bindingDialog','closeBinding','bindingRegion','bindingStatus','regionCn','regionGlobal','regionCnCard','regionGlobalCard','regionMessage','bindingRegionChip','startupWake'];
  const el=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)]));
  if(Object.values(el).some(value=>!value))return;
  const t=(key,values)=>window.DuckI18n.t(key,values);
  let snapshot=null,config=null,failed=false,request=null,generation=0,timer=null;
  let selected=null,saving=false,message='';
  let hasOpenedBinding=false,cameraAvailable=false;
  const changed=value=>!!config&&['cn','global'].includes(value)&&(config.custom_endpoint||config.cloud_region!==value);
  function render(){
    el.bindingRegion.textContent=!config?t('bindingRegionUnknown'):config.custom_endpoint?t('bindingCustom'):config.cloud_region==='cn'?t('bindingCn'):config.cloud_region==='global'?t('bindingGlobal'):t('bindingNotConfigured');
    el.bindingRegionChip.textContent=t(!config?'bindingChipNone':config.custom_endpoint?'bindingChipCustom':config.cloud_region==='cn'?'bindingChipCn':config.cloud_region==='global'?'bindingChipGlobal':'bindingChipNone');
    el.openBinding.setAttribute('aria-label',t('bindingEntryCurrent',{region:el.bindingRegion.textContent}));
    el.openBinding.title=t('bindingEntryCurrent',{region:el.bindingRegion.textContent});
    const setup=snapshot?.onboarding||{},actual=snapshot?.session_cloud_region??setup.cloud_region;
    const matches=!!config&&(config.custom_endpoint||actual===config.cloud_region);
    const bound=setup.cloud_region===config?.cloud_region?(setup.bound===undefined?snapshot?.bound:setup.bound):null;
    let state='pending',key='bindingChecking';
    if(failed){state='unavailable';key='bindingUnavailable';}
    else if(setup.state==='awaiting_network'){key='bindingWaitingNetwork';}
    else if(config?.configured&&snapshot?.cloud_connected===true&&matches){state='online';key='bindingOnline';}
    else if(bound===true){state='bound';key=setup.cached?'bindingCached':'bindingBound';}
    else if(bound===false){state='unbound';key='bindingUnbound';}
    else if(!config?.configured&&setup.cloud_error){key='bindingRetrying';}
    el.bindingStatus.textContent=t(key);el.bindingStatus.dataset.state=state;
    for(const [value,input,card] of [['cn',el.regionCn,el.regionCnCard],['global',el.regionGlobal,el.regionGlobalCard]]){
      input.checked=selected===value;input.disabled=saving||!config;
      card.dataset.selected=String(selected===value);card.dataset.current=String(config?.cloud_region===value&&!config.custom_endpoint);
    }
    el.regionMessage.textContent=t(message||(!config?'bindingChecking':!config.configured||config.custom_endpoint?'bindingSelect':'bindingSame'));
    const phrase=config?.wake_phrase;
    el.startupWake.textContent=phrase?t('startupWakeSelected',{phrase}):t('startupWake');
  }
  async function jsonResponse(response){
    if(!response.ok)throw Error('Region service unavailable');
    const value=await response.json();
    if(!value||typeof value!=='object'||Array.isArray(value))throw Error('Invalid region status');
    return value;
  }
  async function refresh(){
    if(!el.bindingDialog.open||saving)return;
    request?.abort();const current=++generation,controller=new AbortController();request=controller;
    const timeout=setTimeout(()=>controller.abort(),6000);
    try{
      const [voice,region]=await Promise.all(['/api/voice/status','/api/voice/region'].map(path=>fetch(path,{cache:'no-store',signal:controller.signal}).then(jsonResponse)));
      if(current!==generation||!el.bindingDialog.open)return;
      snapshot=voice;config=region;failed=false;
      selected=config.custom_endpoint?null:config.cloud_region;
      if(message==='bindingApplied'&&snapshot.cloud_connected===true&&snapshot.session_cloud_region===config.cloud_region)message='';
      render();
    }catch(error){
      if(current===generation&&el.bindingDialog.open){snapshot=null;failed=true;render();}
    }finally{clearTimeout(timeout);if(current===generation)request=null;}
  }
  async function select(value){
    if(saving||!changed(value)){render();return;}
    selected=value;request?.abort();++generation;saving=true;message='bindingApplying';render();
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
    try{
      config=await jsonResponse(await fetch('/api/voice/region',{method:'POST',headers:{'Content-Type':'application/json','X-Device-Test':'1'},body:JSON.stringify({cloud_region:value}),signal:controller.signal}));
      selected=config.cloud_region;snapshot=null;failed=false;message=config.changed?'bindingApplied':'bindingSame';
    }catch(error){selected=config.custom_endpoint?null:config.cloud_region;message='bindingSwitchFailed';}
    finally{clearTimeout(timeout);saving=false;render();refresh();}
  }
  for(const [input,value] of [[el.regionCn,'cn'],[el.regionGlobal,'global']])input.addEventListener('change',()=>select(value));
  function openBinding(){
    if(!el.bindingDialog.open){if(!saving)message='';el.bindingDialog.showModal();}
    hasOpenedBinding=true;
    render();refresh();if(!timer)timer=setInterval(refresh,3000);
  }
  el.openBinding.addEventListener('click',openBinding);
  el.closeBinding.addEventListener('click',()=>el.bindingDialog.close());
  el.bindingDialog.addEventListener('close',()=>{++generation;request?.abort();request=null;clearInterval(timer);timer=null;});
  async function headerRegion(){
    if(el.bindingDialog.open||saving)return;
    const current=++generation;
    try{
      const value=await jsonResponse(await fetch('/api/voice/region',{cache:'no-store'}));
      if(current!==generation||saving)return;
      config=value;selected=config.custom_endpoint?null:config.cloud_region;
      if(!hasOpenedBinding&&!document.hidden&&cameraAvailable&&config.configured===false)openBinding();
      else render();
    }catch(error){if(current===generation)render();}
  }
  function updateCameraAvailability(available){
    if(cameraAvailable===available)return;
    cameraAvailable=available;
    if(cameraAvailable&&!hasOpenedBinding&&!document.hidden)void headerRegion();
  }
  window.DuckBinding={updateCameraAvailability};
  window.addEventListener('duck-languagechange',render);
  window.addEventListener('focus',()=>el.bindingDialog.open?refresh():headerRegion());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)(el.bindingDialog.open?refresh:headerRegion)();});
  headerRegion();
})();
