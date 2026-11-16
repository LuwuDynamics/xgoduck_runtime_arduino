/* Startup examples play in this browser, without changing or commanding Duck. */
(() => {
  const dialog=document.getElementById('bindingDialog'),status=document.getElementById('startupPreviewStatus');
  const choices=[
    {button:document.getElementById('previewStartupNetwork'),source:'/startup-network.wav',name:'startupNetwork',listen:'startupListenNetwork',stop:'startupStopNetwork'},
    {button:document.getElementById('previewStartupVoice'),source:'/startup-voice.wav',name:'startupVoice',listen:'startupListenVoice',stop:'startupStopVoice'}
  ];
  if(!dialog||!status||choices.some(choice=>!choice.button))return;
  const t=(key,values)=>window.DuckI18n.t(key,values);
  let player=null,active=null,loading=false,message='',messageName=null;
  function render(){
    for(const choice of choices){
      const selected=choice===active;
      choice.button.textContent=t(selected?(loading?'startupPreviewLoading':'startupPreviewStop'):'startupPreviewListen');
      choice.button.setAttribute('aria-label',t(selected?choice.stop:choice.listen));
      choice.button.setAttribute('aria-pressed',String(selected));
    }
    status.textContent=message?t(message,{name:messageName?t(messageName):''}):'';status.hidden=!message;
  }
  function stop(next='',name=null){
    const previous=player;player=null;active=null;loading=false;message=next;messageName=name;
    if(previous){previous.pause();previous.removeAttribute('src');previous.load();}
    render();
  }
  async function preview(choice){
    if(active===choice)return stop('startupPreviewStopped');
    stop();
    const audio=new Audio();audio.preload='none';audio.src=choice.source;
    player=audio;active=choice;loading=true;message='startupPreviewLoading';render();
    audio.onended=()=>{if(player===audio)stop('startupPreviewEnded',choice.name);};
    audio.onerror=()=>{if(player===audio)stop('startupPreviewFailed');};
    try{
      await audio.play();
      if(player!==audio){audio.pause();return;}
      loading=false;message='startupPreviewPlaying';messageName=choice.name;render();
    }catch(error){if(player===audio)stop('startupPreviewFailed');}
  }
  for(const choice of choices)choice.button.addEventListener('click',()=>preview(choice));
  dialog.addEventListener('close',()=>stop());
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('pagehide',()=>stop());
  window.addEventListener('duck-languagechange',render);
  render();
})();
