(() => {
  const $=id=>document.getElementById(id), t=window.DuckI18n.t;
  let media=null,robot=null,gamepad=null,streaming=false,cameraBusy=false,lightBusy=false,mediaBusy=false,usbBusy=false;
  let volumeDirty=false,volumeSaving=false,pendingVolume=null;
  let live=null,wake=null,noticeTimer=null,audioContext=null,mediaRevision=0,usbRevision=0,activePanel='quick',speakerTesting=false;
  let audioState='listenIdle',audioValues={};
  let duckCall=null;
  const duckSounds=[...document.querySelectorAll('[data-duck-sound]')];
  const usbButtons=[$('usbHostQuick'),$('usbHost')];
  const dialog=$('config'),volumeButton=$('speakerVolume'),volumeSlider=$('volume'),volumeStatus=$('volumeStatus');
  $('applyVolume')?.remove(); // An older cached page may still contain this button.
  // Port 80 opens the cockpit; the original desktop page stays on port 9527.
  const desktopURL=new URL('/',location.href);desktopURL.port='9527';
  document.querySelectorAll('[data-desktop-link]').forEach(link=>link.href=new URL(link.dataset.desktopLink||'/',desktopURL).href);
  $('openWalkingHelp')?.addEventListener('click',()=>$('walkingHelpDialog').showModal());
  const modeKeys={shadow:'modeShadow',off:'modeOff',hold:'modeHold',policy:'modePolicy',calibrate:'modeCalibrate',servo_debug:'modeServo'};
  const errorKeys={
    '读取补光灯状态失败，请检查摄像头连接。':'lightReadFailed',
    '设置补光灯失败，请检查摄像头连接。':'lightSetFailed',
    '补光灯状态没有生效，请重试。':'lightNotApplied',
    '无法确认补光灯状态。':'lightNotConfirmed',
    '当前摄像头不支持补光灯控制。':'lightUnsupported',
    'Failed to fetch':'connectionFailed','Load failed':'connectionFailed'
  };
  const message=error=>{const text=error?.message||String(error||'');return errorKeys[text]?t(errorKeys[text]):text||t('requestFailed');};
  function notify(text,hold=false){clearTimeout(noticeTimer);$('notice').textContent=text;$('notice').hidden=!text;if(text&&!hold)noticeTimer=setTimeout(()=>$('notice').hidden=true,6500);}
  function audioStatus(key,values={}){audioState=key;audioValues=values;$('voiceState').textContent=t(key,values);}
  async function api(path,body,raw=false){
    const options={signal:AbortSignal.timeout(15000)};
    if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':raw?'audio/wav':'application/json','X-Device-Test':'1','X-Duck-Gamepad':'1'},body:raw?body:JSON.stringify(body)});
    const response=await fetch('/api/'+path,options),data=await response.json();
    if(!response.ok)throw Error(data.error||data.detail||t('requestFailed'));
    return data;
  }
  function render(){
    const cam=media?.camera,a=media?.audio,light=cam?.light,controlsBusy=cameraBusy||lightBusy;
    $('link').textContent='';$('link').append(document.createElement('i'),document.createTextNode(t(robot?'online':'reconnecting')));$('link').classList.toggle('online',!!robot);
    const battery=robot?.battery, voltage=battery?.voltage_v, batteryChip=$('batteryVoltage');
    const voltageLabel=t(battery?.low?'voltageLow':'voltage'), voltageText=Number.isFinite(voltage)?voltage.toFixed(1)+' V':'—';
    $('batteryLabel').textContent=voltageLabel+' ';
    $('batteryValue').textContent=voltageText;
    batteryChip.setAttribute('aria-label',voltageLabel+' '+voltageText);
    batteryChip.title=t('voltageSource');
    batteryChip.classList.toggle('low-voltage',battery?.low===true);
    const previewFps=Number.isFinite(cam?.preview_fps_limit)?`≤${cam.preview_fps_limit}`:cam?.fps||0;
    $('videoStats').textContent=cam?.running&&cam.ready?t('cameraLive',{fps:previewFps}):t(cam?.running?'cameraStarting':'cameraOff');
    $('robotMode').textContent=robot?(modeKeys[robot.mode]?t(modeKeys[robot.mode]):robot.mode):t('disconnected');
    $('telemetry').textContent=robot?t('feedback',{hz:Number(robot.feedback_hz||0).toFixed(0),torque:t(robot.enabled?'torqueOn':'torqueOff')}):t('waitingFeedback');
    $('padLink').textContent=t(gamepad?.input?.connected?(gamepad.control?.enabled?'gamepadActive':'gamepadReady'):'gamepadMissing');
    $('snapshot').disabled=!cam?.ready||!cam?.running;
    $('startCamera').disabled=controlsBusy||!cam?.available||cam.running;
    $('stopCamera').disabled=controlsBusy||!cam?.running;
    $('startCamera').textContent=t(cameraBusy&&!cam?.running?'startingCamera':cam&&!cam.available?'cameraMissing':'startCamera');
    $('stopCamera').textContent=t(cameraBusy&&cam?.running?'stoppingCamera':'stopCamera');
    $('lightOn').disabled=controlsBusy||!light?.supported||light.enabled===true;
    $('lightOff').disabled=controlsBusy||!light?.supported||light.enabled===false;
    $('lightOn').setAttribute('aria-pressed',String(light?.enabled===true));$('lightOff').setAttribute('aria-pressed',String(light?.enabled===false));
    $('lightState').textContent=lightBusy?t('lightChanging'):light?.error?message(light.error):t(!cam?.available?'lightUnavailable':!light?.supported?'lightUnsupported':light.enabled===true?'lightEnabled':light.enabled===false?'lightDisabled':'lightUnknown');
    const fill=$('feed').classList.contains('fill');$('fit').setAttribute('aria-label',t(fill?'fitView':'fillView'));
    const volumeText=Number.isFinite(a?.volume)?a.volume+'%':'—';
    const busy=!!live||mediaBusy;
    if(volumeButton){
      $('speakerVolumeValue').textContent=volumeText;
      volumeButton.disabled=busy||!a?.available;
    }
    if(!volumeDirty){
      if(Number.isFinite(a?.volume))volumeSlider.value=a.volume;
      $('volumeLabel').textContent=volumeText;
    }
    if(speakerTesting&&!mediaBusy&&a?.state==='idle'){
      speakerTesting=false;audioStatus(a.error?'speakerTestFailed':'speakerTestDone');
      if(a.error)notify(message(a.error));
    }
    const speakerPlaying=speakerTesting&&a?.state==='playing';
    $('speakerTest').disabled=busy||!a?.available||(!speakerTesting&&a.state!=='idle');
    $('speakerTest').classList.toggle('active',!!speakerPlaying);$('speakerTest').setAttribute('aria-pressed',String(!!speakerPlaying));
    $('speakerLabel').textContent=t(speakerTesting&&mediaBusy?'speakerPreparing':speakerPlaying?'stopSpeakerTest':'testSpeaker');
    $('speakerTest').title=t(speakerPlaying?'speakerPlayingHint':'speakerTestHint');
    renderDuckSounds(a,busy);
    $('testSpeaker').disabled=$('testMic').disabled=busy||!a?.available||a.state!=='idle';
    volumeSlider.disabled=!!live||(mediaBusy&&!volumeSaving)||!a?.available;
    $('voiceFile').disabled=busy||!a?.available||a.state!=='idle';
    $('stopAudio').disabled=mediaBusy||(!live&&(!a?.available||a.state==='idle'));
    $('listen').disabled=!live&&(mediaBusy||!a?.available||a.state!=='idle');
    $('listen').classList.toggle('active',!!live);$('listen').setAttribute('aria-pressed',String(!!live));
    $('listenLabel').textContent=t(live?'stopListening':'listen');$('listen').title=t(live?'stopListenHint':'listenHint');
    $('voiceState').textContent=audioState==='listenIdle'?'':t(audioState,audioValues);
    const usb=gamepad?.usb;
    const usbStatus=usb?.error?message(usb.error):t(!usb?.available?'usbUnavailable':usb.host?'usbHostMode':usb.mode==='device'?'usbDeviceMode':'usbOff');
    for(const button of usbButtons){
      button.disabled=usbBusy||!usb?.available;
      button.setAttribute('aria-checked',String(!!usb?.host));
      button.textContent=usbBusy?t('usbSwitching'):usb?.available?t(usb.host?'usbOn':'usbOff'):'USB Host';
      button.title=usbStatus+' '+t('usbNotice');
    }
    $('usbStatus').textContent=usbStatus;
    $('frameHint').textContent=activePanel==='quick'?'':t(activePanel==='gamepad'?'gamepadHint':activePanel==='servo'?'servoHint':'calibrationHint');
    const want=cam?.running&&!document.hidden;
    if(want&&!streaming){streaming=true;$('feed').src='/api/media/camera/stream?t='+Date.now();}
    if(!want&&streaming){streaming=false;$('feed').removeAttribute('src');}
    $('feed').hidden=!want||!cam?.ready;$('welcome').hidden=!!want||!!window.DuckToF?.expanded;document.querySelector('.reticle').hidden=!want;
  }
  async function requestWake(){try{if(navigator.wakeLock&&!wake)wake=await navigator.wakeLock.request('screen');wake?.addEventListener('release',()=>wake=null,{once:true});}catch{}}
  async function camera(start){
    if(cameraBusy||lightBusy)return;
    cameraBusy=true;mediaRevision++;render();
    try{media=await api('media/camera/'+(start?'start':'stop'),{});if(start)requestWake();}
    catch(error){notify(message(error));}finally{cameraBusy=false;render();}
  }
  $('startCamera').onclick=()=>camera(true);$('stopCamera').onclick=()=>camera(false);
  async function light(enabled){
    if(cameraBusy||lightBusy)return;
    lightBusy=true;mediaRevision++;render();
    try{media=await api('media/camera/light',{enabled});}
    catch(error){notify(message(error));}finally{lightBusy=false;render();}
  }
  $('lightOn').onclick=()=>light(true);$('lightOff').onclick=()=>light(false);
  $('feed').onerror=()=>{streaming=false;if(media?.camera?.running)notify(t('cameraInterrupted'));};
  $('fit').onclick=()=>{const fill=$('feed').classList.toggle('fill');render();notify(t(fill?'fillNotice':'fitNotice'));};
  document.addEventListener('fullscreenchange',()=>{if(document.fullscreenElement)requestWake();});
  $('clean').onclick=()=>{document.body.classList.add('clean');$('restoreHud').hidden=false;};
  $('restoreHud').onclick=()=>{document.body.classList.remove('clean');$('restoreHud').hidden=true;};
  $('snapshot').onclick=async()=>{try{
    const response=await fetch('/api/media/camera/frame');if(!response.ok)throw Error(t('photoMissing'));
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='duck-'+Date.now()+'.jpg';link.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
  }catch(error){notify(message(error));}};
  function panel(name){
    activePanel=name;
    document.querySelectorAll('[data-panel]').forEach(button=>{if(button.dataset.panel===name)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
    $('quickPanel').hidden=name!=='quick';$('framePanel').hidden=name==='quick';
    $('settingsFrame').src=name==='quick'?'about:blank':'/'+name+'.html?cockpit=1';render();
  }
  function settings(name='quick'){if(!dialog.open)dialog.showModal();panel(name);}
  $('settings').onclick=()=>settings();$('padLink').onclick=()=>settings('gamepad');
  document.querySelectorAll('[data-panel]').forEach(button=>button.onclick=()=>panel(button.dataset.panel));
  $('closeConfig').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{$('settingsFrame').src='about:blank';});
  $('settingsFrame').onload=()=>{try{
    const doc=$('settingsFrame').contentDocument;if(!doc?.body)return;
    const style=doc.createElement('style');style.textContent='header{display:none!important}main{padding:8px 14px!important;max-width:100%!important}section{margin-top:10px!important}.audio-grid{grid-template-columns:1fr!important}body{font-size:14px}.servo-grid{overflow:auto}';doc.head.append(style);doc.querySelectorAll('a').forEach(link=>link.target='_top');
  }catch{}};
  async function switchUsb(){
    if(usbBusy||!gamepad?.usb?.available)return;
    usbBusy=true;usbRevision++;render();
    try{const data=await api('gamepad/usb',{host:!gamepad.usb.host});gamepad={...gamepad,usb:data.usb};}
    catch(error){notify(message(error));}finally{usbBusy=false;render();}
  }
  usbButtons.forEach(button=>button.onclick=switchUsb);
  volumeButton?.addEventListener('click',()=>{
    settings();
    volumeSlider.focus({preventScroll:true});volumeSlider.scrollIntoView({block:'center'});
  });
  volumeSlider.oninput=()=>{
    volumeDirty=true;$('volumeLabel').textContent=volumeSlider.value+'%';volumeStatus?.replaceChildren();
  };
  async function mediaAction(path,body){
    if(mediaBusy||live)return false;
    mediaBusy=true;mediaRevision++;render();
    try{media=await api('media/'+path,body);return true;}
    catch(error){notify(message(error));return false;}finally{mediaRevision++;mediaBusy=false;render();}
  }
  function renderDuckSounds(audio,busy){
    if(duckCall?.phase==='playing'&&!busy&&audio?.state==='idle'){
      duckCall.phase=audio.error?'failed':'done';
      if(audio.error)notify(t('duckSoundFailed'));
    }
    const active=duckCall?.phase==='loading'||duckCall?.phase==='playing';
    for(const button of duckSounds){
      button.disabled=!!busy||!audio?.available||audio.state!=='idle';
      button.setAttribute('aria-pressed',String(active&&duckCall.kind===button.dataset.duckSound));
    }
    const key=!audio?.available?'duckSpeakerMissing':active?'duckSoundPlaying'
      :busy||audio.state!=='idle'?'audioBusy':duckCall?.phase==='failed'?'duckSoundFailed'
      :duckCall?.phase==='done'?'duckSoundDone':'duckSoundHint';
    const text=t(key,{sound:duckCall?t(duckCall.label):''}),status=$('duckSoundStatus');
    if(status.textContent!==text)status.textContent=text;
    $('duckCalls')?.setAttribute('title',text);
  }
  for(const button of duckSounds)button.onclick=async()=>{
    if(button.disabled)return;
    duckCall={kind:button.dataset.duckSound,label:button.dataset.i18n,phase:'loading'};
    const ok=await mediaAction('audio/play',{source:'duck-'+duckCall.kind});
    duckCall.phase=ok?'playing':'failed';render();
  };
  volumeSlider.onchange=async()=>{
    pendingVolume=+volumeSlider.value;
    if(volumeSaving)return;
    volumeSaving=true;
    // Serialize writes to the speaker and keep only the latest queued setting.
    while(pendingVolume!==null){
      const volume=pendingVolume;
      pendingVolume=null;
      const saved=await mediaAction('audio/volume',{volume});
      if(pendingVolume===null&&+volumeSlider.value===volume){
        volumeDirty=false;
        volumeStatus?.replaceChildren(saved?'':t('volumeSaveFailed'));
        if(saved)notify('');
      }
    }
    volumeSaving=false;
    render();
  };
  $('testSpeaker').onclick=()=>mediaAction('audio/play',{source:'music'});
  $('speakerTest').onclick=async()=>{
    if(mediaBusy||live||!media?.audio.available)return;
    if(speakerTesting){await mediaAction('audio/stop',{});return;}
    if(media.audio.state!=='idle')return notify(t('audioBusy'));
    speakerTesting=true;audioStatus('speakerPreparing');
    if(await mediaAction('audio/play',{source:'duck'})){
      audioStatus(speakerTesting?'speakerPlayingHint':'speakerTestDone');
    }else{speakerTesting=false;audioStatus('speakerTestFailed');}
    render();
  };
  $('stopAudio').onclick=async()=>{stopLive();await mediaAction('audio/stop',{});};
  function player(){audioContext??=new (window.AudioContext||window.webkitAudioContext)();audioContext.resume();return audioContext;}
  function stopLive(){
    const session=live;if(!session)return;
    live=null;session.abort.abort();session.pcm?.stop();clearTimeout(session.timer);audioStatus('listenStopped');render();
  }
  async function startLive(){
    if(live)return stopLive();
    if(mediaBusy)return;
    if(media?.audio.state!=='idle')return notify(t('audioBusy'));
    const session={abort:new AbortController(),started:Date.now()},ctx=player();live=session;audioStatus('listenConnecting');render();
    const deadline=()=>{clearTimeout(session.timer);session.timer=setTimeout(()=>{session.timedOut=true;session.abort.abort();},5000);};
    try{
      deadline();await ctx.resume();if(ctx.state!=='running')throw Error(t('audioPaused'));
      const response=await fetch('/api/media/audio/stream',{headers:{'X-Device-Test':'1'},signal:session.abort.signal,cache:'no-store'});
      if(!response.ok){const error=await response.json();throw Error(error.error||t('liveUnavailable'));}
      session.pcm=new DuckAudio.LivePCM(ctx);session.reader=response.body.getReader();
      while(!session.abort.signal.aborted){
        const {done,value}=await session.reader.read();if(done)throw Error(t('liveEnded'));if(session.abort.signal.aborted)break;
        deadline();session.pcm.push(value);
        const seconds=Math.floor((Date.now()-session.started)/1000);if(seconds!==session.seconds){session.seconds=seconds;audioStatus('listening',{seconds});}
      }
    }catch(error){if(live===session){notify(session.timedOut?t('liveTimedOut'):message(error));audioStatus('listenStopped');}}
    finally{
      clearTimeout(session.timer);session.abort.abort();session.pcm?.stop();try{await session.reader?.cancel();}catch{}
      if(live===session){live=null;render();}
    }
  }
  $('listen').onclick=()=>startLive().catch(error=>notify(message(error)));
  $('testMic').onclick=()=>{dialog.close();startLive().catch(error=>notify(message(error)));};
  $('voiceFile').onchange=async()=>{
    const file=$('voiceFile').files[0];$('voiceFile').value='';if(!file||live||mediaBusy)return;
    if(file.size>12*1024*1024)return notify(t('fileTooLarge'));
    mediaBusy=true;mediaRevision++;render();
    try{
      const data=await player().decodeAudioData(await file.arrayBuffer());
      if(data.duration>30)throw Error(t('fileTooLong'));if(data.duration<.2)throw Error(t('fileTooShort'));
      const mono=new Float32Array(data.length);
      for(let c=0;c<data.numberOfChannels;c++){const samples=data.getChannelData(c);for(let i=0;i<mono.length;i++)mono[i]+=samples[i]/data.numberOfChannels;}
      await api('media/upload',DuckAudio.wav([mono],data.sampleRate),true);media=await api('media/audio/play',{source:'upload'});
      audioStatus('playingClip',{seconds:data.duration.toFixed(1)});notify(t('audioSent'));
    }catch(error){notify(message(error));}finally{mediaBusy=false;render();}
  };
  function leave(){stopLive();streaming=false;$('feed').removeAttribute('src');wake?.release();wake=null;}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)leave();else{render();if(media?.camera.running)requestWake();}});
  window.addEventListener('pagehide',leave);window.addEventListener('duck-languagechange',()=>{notify('');render();});
  window.addEventListener('duck-tof-layout',render);
  function layout(){
    const root=$('cockpit');
    root.style.setProperty('--hud-top',document.querySelector('.top').offsetHeight+'px');
    root.style.setProperty('--hud-bottom',document.querySelector('.bottom').offsetHeight+'px');
  }
  if(window.ResizeObserver){const observer=new ResizeObserver(layout);observer.observe(document.querySelector('.top'));observer.observe(document.querySelector('.bottom'));}
  window.addEventListener('resize',layout);layout();
  async function poll(){
    try{
      if(document.hidden)return;
      const revision=mediaRevision,usbSnapshot=usbRevision;
      const results=await Promise.allSettled([api('status'),api('media/status'),api('gamepad/status')]);
      robot=results[0].status==='fulfilled'?results[0].value:null;
      if(revision===mediaRevision&&!cameraBusy&&!lightBusy&&!mediaBusy)media=results[1].status==='fulfilled'?results[1].value:null;
      if(!usbBusy&&usbSnapshot===usbRevision)gamepad=results[2].status==='fulfilled'?results[2].value:null;
      window.DuckMotion?.update(robot,gamepad?.control?.enabled===true);
      render();
    }finally{setTimeout(poll,1500);}
  }
  render();poll();
})();
