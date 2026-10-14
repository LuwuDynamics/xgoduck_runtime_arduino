(() => {
  const $=id=>document.getElementById(id);
  let media=null, robot=null, gamepad=null, streaming=false, cameraBusy=false, mediaBusy=false, volumeDirty=false;
  let voice=null, live=null, wake=null, noticeTimer=null, audioContext=null;
  const dialog=$('config');
  function notify(message,hold=false){clearTimeout(noticeTimer);$('notice').textContent=message;$('notice').hidden=!message;if(message&&!hold)noticeTimer=setTimeout(()=>$('notice').hidden=true,6500);}
  async function api(path,body,raw=false,keepalive=false){
    const options={signal:AbortSignal.timeout(15000)};
    if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':raw?'audio/wav':'application/json','X-Device-Test':'1','X-Duck-Gamepad':'1'},body:raw?body:JSON.stringify(body),keepalive});
    const r=await fetch('/api/'+path,options), data=await r.json();if(!r.ok)throw Error(data.error||data.detail||'Request failed.');return data;
  }
  function micReady(){return !!(window.isSecureContext&&navigator.mediaDevices?.getUserMedia&&window.AudioWorkletNode);}
  function render(){
    const cam=media?.camera,a=media?.audio;
    $('link').textContent='';const dot=document.createElement('i');$('link').append(dot,document.createTextNode(robot?'Duck online':'Reconnecting'));$('link').classList.toggle('online',!!robot);
    $('videoStats').textContent=cam?.running&&cam?.ready?`LIVE · ${cam.fps} FPS`:cam?.running?'STARTING':'CAM OFF';
    $('robotMode').textContent=robot?({shadow:'Inference only',off:'Torque off',hold:'Default pose',policy:'Walk',calibrate:'Calibration',servo_debug:'Servo setup'}[robot.mode]||robot.mode):'Disconnected';
    $('telemetry').textContent=robot?`${Number(robot.feedback_hz||0).toFixed(0)} Hz feedback · ${robot.enabled?'Torque on':'Torque off'}`:'Waiting for feedback';
    $('padLink').textContent=gamepad?.input?.connected?(gamepad.control.enabled?'Gamepad active':'Gamepad ready'):'Gamepad —';
    $('snapshot').disabled=!cam?.ready||!cam?.running;
    $('cameraToggle').disabled=cameraBusy;$('startCamera').disabled=cameraBusy||!cam?.available;
    $('startCamera').textContent=cameraBusy?'Starting…':cam&&!cam.available?'Camera not found':'Start camera ↗';
    $('cameraToggle').setAttribute('aria-label',cam?.running?'Stop camera':'Start camera');
    if(!volumeDirty&&a?.volume!=null){$('volume').value=a.volume;$('volumeLabel').textContent=a.volume+'%';}
    const busy=!!voice||!!live||mediaBusy;
    $('applyVolume').disabled=busy||!a?.available||!volumeDirty;
    $('testSpeaker').disabled=$('testMic').disabled=busy||!!live||!a?.available||a.state!=='idle';
    $('volume').disabled=busy||!a?.available;
    $('talk').disabled=!!live||mediaBusy||!a?.available;
    $('listen').disabled=!live&&(!!voice||mediaBusy||!a?.available);
    $('talk').classList.toggle('active',voice?.kind==='talk'&&voice.ready&&!voice.released);
    $('listen').classList.toggle('active',!!live);
    $('listen').setAttribute('aria-pressed',String(!!live));
    $('listenLabel').textContent=live?'Stop listening':'Listen live';
    $('listenHint').textContent=live?'Duck mic · tap to stop':'Duck mic · tap to start';
    $('cancelVoice').hidden=!voice;
    $('micSetup').textContent=micReady()?'Mic ready':'Phone mic setup';
    $('micAvailability').textContent=micReady()?'This browser can request microphone access.':'Microphone blocked on HTTP. Enable the Chrome test setting below.';
    const want=cam?.running&&!document.hidden;
    if(want&&!streaming){streaming=true;$('feed').src='/api/media/camera/stream?t='+Date.now();}
    if(!want&&streaming){streaming=false;$('feed').removeAttribute('src');}
    $('feed').hidden=!want||!cam?.ready;$('welcome').hidden=!!want;document.querySelector('.reticle').hidden=!want;
  }
  async function requestWake(){try{if(navigator.wakeLock&&!wake)wake=await navigator.wakeLock.request('screen');wake?.addEventListener('release',()=>wake=null,{once:true});}catch{}}
  async function camera(start){if(cameraBusy)return;cameraBusy=true;render();try{media=await api('media/camera/'+(start?'start':'stop'),{});if(start)requestWake();}catch(e){notify(e.message);}finally{cameraBusy=false;render();}}
  $('startCamera').onclick=()=>camera(true);$('cameraToggle').onclick=()=>camera(!media?.camera.running);
  $('feed').onerror=()=>{streaming=false;if(media?.camera?.running)notify('Camera stream interrupted. Reconnecting…');};
  $('fit').onclick=()=>{const fill=$('feed').classList.toggle('fill');$('fit').setAttribute('aria-label',fill?'Fit entire camera view':'Fill camera view');notify(fill?'Fill view · edges are cropped':'Fit view · entire camera frame');};
  document.addEventListener('fullscreenchange',()=>{if(document.fullscreenElement)requestWake();});
  $('clean').onclick=()=>{document.body.classList.add('clean');$('restoreHud').hidden=false;};$('restoreHud').onclick=()=>{document.body.classList.remove('clean');$('restoreHud').hidden=true;};
  $('snapshot').onclick=async()=>{try{const r=await fetch('/api/media/camera/frame');if(!r.ok)throw Error('No camera frame available.');const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='duck-'+Date.now()+'.jpg';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}catch(e){notify(e.message);}};
  $('torqueOff').onclick=async()=>{try{await api('mode',{mode:'off'});notify('Torque off');}catch(e){notify(e.message,true);}};
  function panel(name){
    document.querySelectorAll('[data-panel]').forEach(b=>{if(b.dataset.panel===name)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
    $('quickPanel').hidden=name!=='quick';$('framePanel').hidden=name==='quick';
    $('settingsFrame').src=name==='quick'?'about:blank':'/'+name+'.html?cockpit=1';
    $('frameHint').textContent=name==='gamepad'?'Scan, connect, and bind controller buttons.':name==='servo'?'Turn torque off before entering servo setup. Support Duck before changing servo positions.':'Turn torque off before calibration. Follow each joint’s calibration steps.';
  }
  function settings(name='quick'){if(voice)return notify('Finish the voice clip before opening settings.');if(!dialog.open)dialog.showModal();panel(name);}
  $('settings').onclick=()=>settings();$('padLink').onclick=()=>settings('gamepad');$('micSetup').onclick=()=>settings();
  document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>panel(b.dataset.panel));
  $('closeConfig').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{$('settingsFrame').src='about:blank';});
  $('settingsFrame').onload=()=>{try{const doc=$('settingsFrame').contentDocument;if(!doc?.body)return;const style=doc.createElement('style');style.textContent='header{display:none!important}main{padding:8px 14px!important;max-width:100%!important}section{margin-top:10px!important}.audio-grid{grid-template-columns:1fr!important}body{font-size:14px}.servo-grid{overflow:auto}';doc.head.append(style);doc.querySelectorAll('a').forEach(a=>a.target='_top');}catch{}};
  $('originAddress').value=location.origin;
  async function copy(id){const input=$(id);input.focus();input.select();try{if(navigator.clipboard)await navigator.clipboard.writeText(input.value);else if(!document.execCommand('copy'))throw Error();notify('Copied');}catch{notify('Long-press the selected address and choose Copy.');}}
  $('copyFlag').onclick=()=>copy('flagAddress');$('copyOrigin').onclick=()=>copy('originAddress');
  $('prepareMic').onclick=async()=>{if(!micReady())return notify('Enable the Chrome test setting, relaunch, then reopen this page.');try{const s=await navigator.mediaDevices.getUserMedia({audio:true});s.getTracks().forEach(t=>t.stop());notify('Phone microphone is ready.');}catch(e){notify(micError(e));}};
  const micError=e=>e.name==='NotAllowedError'?'Microphone permission denied. Allow it in Chrome site settings.':e.message||'Microphone unavailable.';
  $('volume').oninput=()=>{volumeDirty=true;$('volumeLabel').textContent=$('volume').value+'%';render();};
  async function mediaAction(path,body){if(mediaBusy||voice||live)return;mediaBusy=true;render();try{media=await api('media/'+path,body);return true;}catch(e){notify(e.message);return false;}finally{mediaBusy=false;render();}}
  $('applyVolume').onclick=async()=>{if(await mediaAction('audio/volume',{volume:+$('volume').value}))volumeDirty=false;render();};
  $('testSpeaker').onclick=()=>mediaAction('audio/play',{source:'music'});$('stopAudio').onclick=async()=>{stopLive();if(voice)await finishVoice(true);await mediaAction('audio/stop',{});};
  function player(){audioContext??=new AudioContext();audioContext.resume();return audioContext;}
  function stopLive(){
    const session=live;if(!session)return;
    live=null;session.abort.abort();session.pcm?.stop();clearTimeout(session.timer);
    $('voiceState').textContent='Live listening stopped';render();
  }
  async function startLive(){
    if(live)return stopLive();
    if(voice||mediaBusy)return;
    if(media?.audio.state!=='idle')return notify('Duck audio is busy. Stop the current audio first.');
    const session={abort:new AbortController(),started:Date.now()},ctx=player();live=session;
    $('voiceState').textContent='Connecting to Duck microphone…';render();
    const deadline=()=>{clearTimeout(session.timer);session.timer=setTimeout(()=>{session.timedOut=true;session.abort.abort();},5000);};
    try{
      deadline();await ctx.resume();
      if(ctx.state!=='running')throw Error('Phone audio is paused. Tap Listen live again.');
      const response=await fetch('/api/media/audio/stream',{headers:{'X-Device-Test':'1'},signal:session.abort.signal,cache:'no-store'});
      if(!response.ok){const error=await response.json();throw Error(error.error||'Live audio unavailable.');}
      session.pcm=new DuckAudio.LivePCM(ctx);session.reader=response.body.getReader();
      while(!session.abort.signal.aborted){
        const {done,value}=await session.reader.read();if(done)throw Error('Duck microphone stream ended. Tap Listen live to reconnect.');
        if(session.abort.signal.aborted)break;
        deadline();session.pcm.push(value);
        const seconds=Math.floor((Date.now()-session.started)/1000);
        if(seconds!==session.seconds){session.seconds=seconds;$('voiceState').textContent=`Listening live · ${seconds} s · tap to stop`;}
      }
    }catch(e){if(live===session){notify(session.timedOut?'Live audio timed out. Check the connection and tap Listen live again.':e.message);$('voiceState').textContent='Live listening stopped';}}
    finally{
      clearTimeout(session.timer);session.abort.abort();session.pcm?.stop();
      try{await session.reader?.cancel();}catch{}
      if(live===session){live=null;render();}
    }
  }
  $('listen').onclick=()=>startLive().catch(e=>notify(e.message));
  async function sendClip(blob,seconds,session=null){if(seconds<.2)throw Error('Clip too short. Hold the button while speaking.');await api('media/upload',blob,true);if(session&&(session.cancel||document.hidden))return;media=await api('media/audio/play',{source:'upload'});$('voiceState').textContent=`Playing ${seconds.toFixed(1)} s clip on Duck`;}
  async function startVoice(kind){
    if(voice||live||mediaBusy)return;
    if(kind==='talk'&&!micReady()){settings();return notify('Set up this HTTP origin in Chrome to enable the phone microphone.');}
    if(media?.audio.state!=='idle')return notify('Duck audio is busy. Stop the current playback or recording first.');
    const session={kind,ready:false,released:false,cancel:false,finishing:false};voice=session;
    player();render();$('voiceState').textContent='Opening phone microphone…';
    try{
      session.capture=new DuckAudio.Capture();await session.capture.start();
      session.ready=true;session.started=Date.now();
      if(session.released)return finishVoice(session.cancel);
      $('voiceState').textContent='Recording phone · release to send';
      session.timer=setInterval(()=>{const n=Math.min(30,(Date.now()-session.started)/1000);$('voiceState').textContent=`Recording phone · ${n.toFixed(1)} / 30 s`;if(n>=30)finishVoice(false);},100);
      render();
    }catch(e){await session.capture?.close();if(voice===session)voice=null;$('voiceState').textContent='Voice clip stopped';notify(micError(e));render();}
  }
  async function finishVoice(cancel=false){
    const session=voice;if(!session)return;session.released=true;session.cancel ||= cancel;if(!session.ready||session.finishing)return;
    session.finishing=true;clearInterval(session.timer);$('voiceState').textContent=session.cancel?'Cancelling…':'Preparing voice clip…';
    try{
      const recording=await session.capture.stop();if(!session.cancel&&!document.hidden){$('voiceState').textContent='Sending to Duck…';await sendClip(recording.blob,recording.seconds,session);}
      if(session.cancel)$('voiceState').textContent='Voice clip cancelled';
    }catch(e){notify(e.message);$('voiceState').textContent='Voice clip failed · try again';}
    finally{if(voice===session)voice=null;render();}
  }
  for(const [id,kind] of [['talk','talk']]){
    const b=$(id);b.oncontextmenu=e=>e.preventDefault();
    b.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();b.setPointerCapture(e.pointerId);startVoice(kind);};
    b.onpointerup=e=>{e.preventDefault();if(voice?.kind===kind)finishVoice();};
    b.onpointercancel=()=>{if(voice?.kind===kind)finishVoice(true);};
    b.onlostpointercapture=()=>{if(voice?.kind===kind&&!voice.released)finishVoice(true);};
    b.onkeydown=e=>{if((e.key===' '||e.key==='Enter')&&!e.repeat){e.preventDefault();startVoice(kind);}};
    b.onkeyup=e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();if(voice?.kind===kind)finishVoice();}};
  }
  $('cancelVoice').onclick=()=>finishVoice(true);
  $('testMic').onclick=()=>{dialog.close();startLive().catch(e=>notify(e.message));};
  $('voiceFile').onchange=async()=>{const file=$('voiceFile').files[0];$('voiceFile').value='';if(!file||voice||live||mediaBusy)return;if(file.size>12*1024*1024)return notify('Choose an audio file smaller than 12 MB.');mediaBusy=true;render();try{const data=await player().decodeAudioData(await file.arrayBuffer());if(data.duration>30)throw Error('Choose a clip of 30 seconds or less.');const mono=new Float32Array(data.length);for(let c=0;c<data.numberOfChannels;c++){const samples=data.getChannelData(c);for(let i=0;i<mono.length;i++)mono[i]+=samples[i]/data.numberOfChannels;}await sendClip(DuckAudio.wav([mono],data.sampleRate),data.duration);notify('Audio sent to Duck');}catch(e){notify(e.message);}finally{mediaBusy=false;render();}};
  function leave(){stopLive();if(voice){voice.cancel=true;voice.released=true;finishVoice(true);}streaming=false;$('feed').removeAttribute('src');wake?.release();wake=null;}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)leave();else{render();if(media?.camera.running)requestWake();}});window.addEventListener('pagehide',leave);
  async function poll(){try{if(document.hidden)return;const results=await Promise.allSettled([api('status'),api('media/status'),api('gamepad/status')]);robot=results[0].status==='fulfilled'?results[0].value:null;if(results[1].status==='fulfilled')media=results[1].value;else media=null;gamepad=results[2].status==='fulfilled'?results[2].value:null;render();}finally{setTimeout(poll,1500);}}
  render();poll();
})();
