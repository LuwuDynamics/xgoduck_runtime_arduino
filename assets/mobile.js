(() => {
  const $=id=>document.getElementById(id), t=window.DuckI18n.t;
  let media=null,robot=null,gamepad=null,streaming=false,cameraBusy=false,lightBusy=false,mediaBusy=false,usbBusy=false,volumeDirty=false;
  let live=null,wake=null,noticeTimer=null,audioContext=null,mediaRevision=0,activePanel='quick';
  let audioState='listenIdle',audioValues={};
  const dialog=$('config');
  // Port 80 opens the cockpit; the original desktop page stays on port 9527.
  const desktopURL=new URL('/',location.href);desktopURL.port='9527';
  document.querySelectorAll('[data-desktop-link]').forEach(link=>link.href=desktopURL.href);
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
    $('videoStats').textContent=cam?.running&&cam.ready?t('cameraLive',{fps:cam.fps||0}):t(cam?.running?'cameraStarting':'cameraOff');
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
    if(!volumeDirty&&a?.volume!=null){$('volume').value=a.volume;$('volumeLabel').textContent=a.volume+'%';}
    const busy=!!live||mediaBusy;
    $('applyVolume').disabled=busy||!a?.available||!volumeDirty;
    $('testSpeaker').disabled=$('testMic').disabled=busy||!a?.available||a.state!=='idle';
    $('volume').disabled=busy||!a?.available;$('voiceFile').disabled=busy||!a?.available||a.state!=='idle';
    $('stopAudio').disabled=mediaBusy||(!live&&(!a?.available||a.state==='idle'));
    $('listen').disabled=!live&&(mediaBusy||!a?.available||a.state!=='idle');
    $('listen').classList.toggle('active',!!live);$('listen').setAttribute('aria-pressed',String(!!live));
    $('listenLabel').textContent=t(live?'stopListening':'listen');$('listenHint').textContent=t(live?'stopListenHint':'listenHint');
    $('voiceState').textContent=t(audioState,audioValues);
    const usb=gamepad?.usb;
    $('usbHost').disabled=usbBusy||!usb?.available;$('usbHost').setAttribute('aria-checked',String(!!usb?.host));
    $('usbHost').textContent=t(usbBusy?'usbSwitching':usb?.host?'usbOn':'usbOff');
    $('usbStatus').textContent=usb?.error?message(usb.error):t(!usb?'usbUnavailable':usb.host?'usbHostMode':'usbDeviceMode');
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
  $('torqueOff').onclick=async()=>{try{await api('mode',{mode:'off'});notify(t('torqueOff'));}catch(error){notify(message(error),true);}};
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
  $('usbHost').onclick=async()=>{
    if(usbBusy||!gamepad?.usb?.available)return;
    usbBusy=true;render();
    try{const data=await api('gamepad/usb',{host:!gamepad.usb.host});gamepad={...gamepad,usb:data.usb};}
    catch(error){notify(message(error));}finally{usbBusy=false;render();}
  };
  $('volume').oninput=()=>{volumeDirty=true;$('volumeLabel').textContent=$('volume').value+'%';render();};
  async function mediaAction(path,body){
    if(mediaBusy||live)return false;
    mediaBusy=true;mediaRevision++;render();
    try{media=await api('media/'+path,body);return true;}
    catch(error){notify(message(error));return false;}finally{mediaBusy=false;render();}
  }
  $('applyVolume').onclick=async()=>{if(await mediaAction('audio/volume',{volume:+$('volume').value}))volumeDirty=false;render();};
  $('testSpeaker').onclick=()=>mediaAction('audio/play',{source:'music'});
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
      const revision=mediaRevision,results=await Promise.allSettled([api('status'),api('media/status'),api('gamepad/status')]);
      robot=results[0].status==='fulfilled'?results[0].value:null;
      if(revision===mediaRevision&&!cameraBusy&&!lightBusy&&!mediaBusy)media=results[1].status==='fulfilled'?results[1].value:null;
      if(!usbBusy)gamepad=results[2].status==='fulfilled'?results[2].value:null;
      render();
    }finally{setTimeout(poll,1500);}
  }
  render();poll();
})();
