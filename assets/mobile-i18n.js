(() => {
  const strings = {
    en: {
      pageTitle:'Duck Cockpit', cockpit:'COCKPIT', cameraAlt:"Live view from Duck's camera", desktopControl:'Open desktop control', desktopControlLink:'Open desktop control ↗',
      switchLanguage:'切换为中文', fillView:'Fill camera view', fitView:'Fit entire camera view', openSettings:'Open settings', closeSettings:'Close settings',
      enterFullscreen:'Enter fullscreen', exitFullscreen:'Exit fullscreen', fullscreen:'Fullscreen', fullscreenError:'Could not enter fullscreen. Open this page directly in Chrome and tap Fullscreen again.', fullscreenUnsupported:'This browser does not support fullscreen.',
      camera:'Camera', startCamera:'Start camera', stopCamera:'Stop camera', startingCamera:'Starting…', stoppingCamera:'Stopping…', cameraMissing:'Camera not found', cameraOff:'CAM OFF', cameraStarting:'STARTING', cameraLive:'LIVE · {fps} FPS',
      light:'Camera light', lightOn:'Turn light on', lightOff:'Turn light off', lightEnabled:'Light on', lightDisabled:'Light off', lightUnknown:'Light: checking', lightUnavailable:'Light unavailable', lightUnsupported:'Light not supported', lightChanging:'Switching light…',
      welcomeEyebrow:'YOUR DUCK. YOUR POINT OF VIEW.', welcomeTitle:"Duck's point of view.", welcomeTitleSecond:'Ready when you are.', welcomeDescription:'Tap Start camera above to see the live view.', welcomeNote:'Turn your phone sideways and mount it on your gamepad.',
      savePhoto:'Save photo', hideControls:'Hide controls', showControls:'Show controls', viewControls:'View tools', robotStatus:'ROBOT STATUS', liveAudio:'LIVE AUDIO',
      online:'Duck online', reconnecting:'Reconnecting', disconnected:'Disconnected', waitingFeedback:'Waiting for feedback', feedback:'{hz} Hz feedback · {torque}', torqueOn:'Torque on', torqueOff:'Torque off',
      modeShadow:'Inference only', modeOff:'Torque off', modeHold:'Default pose', modePolicy:'Walk', modeCalibrate:'Calibration', modeServo:'Servo setup',
      configureGamepad:'Configure gamepad', gamepadActive:'Gamepad active', gamepadReady:'Gamepad ready', gamepadMissing:'Gamepad —',
      listen:'Listen live', stopListening:'Stop listening', listenHint:'Duck mic · tap to start', stopListenHint:'Duck mic · tap to stop', listenIdle:'Tap Listen live to hear the sounds around Duck', listenStopped:'Live listening stopped', listenConnecting:'Connecting to Duck microphone…', listening:'Listening live · {seconds} s · tap to stop',
      audioBusy:'Duck audio is busy. Stop the current audio first.', audioPaused:'Audio is paused. Tap Listen live again.', liveUnavailable:'Live audio unavailable.', liveEnded:'Duck microphone stream ended. Tap Listen live to reconnect.', liveTimedOut:'Live audio timed out. Check the connection and tap Listen live again.',
      settings:'Settings', general:'General', gamepad:'Gamepad', servos:'Servos', calibration:'Calibration', configuration:'Duck configuration',
      usbConnection:'USB connection', usbDescription:'Enable Host for a USB camera, audio device or wired controller.', usbNotice:'Use Wi-Fi when switching modes; USB connections will be interrupted.', usbSwitching:'Switching…', usbOn:'USB Host: On', usbOff:'USB Host: Off', usbUnavailable:'USB control unavailable.', usbHostMode:'Host mode · USB accessories enabled', usbDeviceMode:'Device mode · Host is off',
      speaker:'Speaker', duckVolume:'Duck volume', applyVolume:'Apply volume', testSpeaker:'Test speaker', listenMicrophone:'Listen to Duck microphone', stopAudio:'Stop audio', sendAudioFile:'Send an audio file', audioFileHint:'Choose up to 30 seconds of audio to play on Duck.',
      handheldView:'Handheld view', handheldDescription:'Use landscape orientation for a controller-mounted display. Fullscreen hides browser controls; hide the overlay for a clearer view.',
      gamepadHint:'Scan, connect, and bind controller buttons.', servoHint:'Turn torque off before entering servo setup. Support Duck before changing servo positions.', calibrationHint:'Turn torque off before calibration. Follow each joint’s calibration steps.',
      cameraInterrupted:'Camera stream interrupted. Reconnecting…', photoMissing:'No camera frame available.', fillNotice:'Fill view · edges are cropped', fitNotice:'Fit view · entire camera frame',
      fileTooLarge:'Choose an audio file smaller than 12 MB.', fileTooLong:'Choose a clip of 30 seconds or less.', fileTooShort:'Choose a clip of at least 0.2 seconds.', audioSent:'Audio sent to Duck', playingClip:'Playing {seconds} s clip on Duck', requestFailed:'Request failed.', connectionFailed:'Unable to connect to Duck.',
      lightReadFailed:'Unable to read the camera light. Check the camera connection.', lightSetFailed:'Unable to set the camera light. Check the camera connection.', lightNotApplied:'The camera light did not change. Please retry.', lightNotConfirmed:'Unable to confirm the camera light state.',
      tofTitle:'ToF live depth',tofCenter:'Centre distance',tofNearest:'Nearest distance',tofNear:'Near',tofFar:'Far · 2 m+',tofCollapse:'Collapse',tofExpand:'Expand',tofValid:'{count} / 64 valid zones',tofTiming:'{hz} Hz · {age} ms ago',
      tofStarting:'Connecting',tofLive:'Live',tofAbsent:'Not connected',tofOffline:'Offline',tofError:'Sensor error',tofUnavailable:'Unavailable',tofDisconnected:'Disconnected',tofStale:'Stale data',tofEmpty:'No valid target',
      tofGrid:'8×8 depth heatmap',tofGridDescription:'8×8 depth heatmap, {count} valid zones, centre {distance}',tofZone:'Row {row}, column {column}: {distance} mm',tofZoneInvalid:'Row {row}, column {column}: no valid distance',
      tofRpcMissing:'The current firmware has no ToF read interface.',tofReadFailed:'Unable to read ToF. Camera and audio remain available.',tofSensorError:'Sensor offline or read failed. Check its connection.',tofNotConnected:'No ToF detected. Check the Qwiic connection.',tofStaleHint:'No fresh frame. Previous distances have been hidden.',tofNoTarget:'No valid target. Try an object in front of the sensor.',tofConnectionLost:'Connection lost. Waiting to reconnect…',tofWaiting:'Waiting for a real sensor frame…'
    },
    zh: {
      pageTitle:'小鸭驾驶舱', cockpit:'驾驶舱', cameraAlt:'小鸭摄像头实时画面', desktopControl:'打开桌面控制页', desktopControlLink:'打开桌面控制页 ↗',
      switchLanguage:'Switch to English', fillView:'填满画面', fitView:'显示完整画面', openSettings:'打开设置', closeSettings:'关闭设置',
      enterFullscreen:'进入全屏', exitFullscreen:'退出全屏', fullscreen:'全屏', fullscreenError:'无法进入全屏，请在 Chrome 中直接打开此页面后重试。', fullscreenUnsupported:'当前浏览器不支持全屏。',
      camera:'摄像头', startCamera:'开启摄像头', stopCamera:'关闭摄像头', startingCamera:'正在开启…', stoppingCamera:'正在关闭…', cameraMissing:'未发现摄像头', cameraOff:'摄像头已关闭', cameraStarting:'摄像头启动中', cameraLive:'实时画面 · {fps} 帧/秒',
      light:'补光灯', lightOn:'打开补光灯', lightOff:'关闭补光灯', lightEnabled:'补光灯已打开', lightDisabled:'补光灯已关闭', lightUnknown:'补光灯：正在读取', lightUnavailable:'补光灯不可用', lightUnsupported:'此摄像头暂不支持补光灯控制', lightChanging:'正在切换补光灯…',
      welcomeEyebrow:'从小鸭的视角，看见身边。', welcomeTitle:'小鸭的视角，', welcomeTitleSecond:'由你开启。', welcomeDescription:'点击上方「开启摄像头」，查看实时画面。', welcomeNote:'横放手机，配合手柄使用。',
      savePhoto:'保存照片', hideControls:'隐藏控件', showControls:'显示控件', viewControls:'画面工具', robotStatus:'机器状态', liveAudio:'实时音频',
      online:'小鸭在线', reconnecting:'正在重新连接', disconnected:'连接已断开', waitingFeedback:'等待反馈', feedback:'反馈 {hz} Hz · {torque}', torqueOn:'舵机已使能', torqueOff:'舵机卸力',
      modeShadow:'仅推理', modeOff:'舵机卸力', modeHold:'默认姿态', modePolicy:'行走', modeCalibrate:'校准', modeServo:'舵机设置',
      configureGamepad:'配置手柄', gamepadActive:'手柄控制中', gamepadReady:'手柄已就绪', gamepadMissing:'手柄未连接',
      listen:'实时监听', stopListening:'停止监听', listenHint:'点击收听小鸭麦克风', stopListenHint:'点击停止收听', listenIdle:'点击实时监听，收听小鸭周围的声音', listenStopped:'实时监听已停止', listenConnecting:'正在连接小鸭麦克风…', listening:'正在监听 · {seconds} 秒 · 点击停止',
      audioBusy:'小鸭音频正在使用中，请先停止当前音频。', audioPaused:'音频播放已暂停，请再次点击实时监听。', liveUnavailable:'实时监听暂不可用。', liveEnded:'麦克风音频流已结束，请再次点击实时监听。', liveTimedOut:'实时监听连接超时，请检查网络后重试。',
      settings:'设置', general:'常规', gamepad:'手柄', servos:'舵机', calibration:'校准', configuration:'小鸭配置',
      usbConnection:'USB 连接', usbDescription:'使用 USB 摄像头、音频设备或有线手柄时，请开启 Host 模式。', usbNotice:'请通过 Wi-Fi 操作；切换模式会中断当前 USB 连接。', usbSwitching:'正在切换…', usbOn:'USB Host：已开启', usbOff:'USB Host：已关闭', usbUnavailable:'USB 控制暂不可用。', usbHostMode:'Host 模式 · USB 外设已启用', usbDeviceMode:'Device 模式 · Host 已关闭',
      speaker:'扬声器', duckVolume:'小鸭音量', applyVolume:'应用音量', testSpeaker:'测试扬声器', listenMicrophone:'监听小鸭麦克风', stopAudio:'停止音频', sendAudioFile:'发送音频文件', audioFileHint:'选择不超过 30 秒的音频，在小鸭扬声器播放。',
      handheldView:'手持视图', handheldDescription:'横放手机适合配合手柄使用。全屏可隐藏浏览器栏；点击隐藏控件可获得更完整的画面。',
      gamepadHint:'扫描、连接手柄，并配置按键。', servoHint:'进入舵机设置前，请先卸力。调整关节位置时请支撑好小鸭。', calibrationHint:'校准前请先卸力，并按各关节的校准步骤操作。',
      cameraInterrupted:'视频流已中断，正在重新连接…', photoMissing:'摄像头画面尚未就绪。', fillNotice:'填满画面 · 边缘会被裁切', fitNotice:'适应画面 · 显示完整视野',
      fileTooLarge:'请选择小于 12 MB 的音频文件。', fileTooLong:'请选择不超过 30 秒的音频。', fileTooShort:'请选择至少 0.2 秒的音频。', audioSent:'音频已发送到小鸭', playingClip:'正在小鸭上播放 {seconds} 秒音频', requestFailed:'操作失败。', connectionFailed:'无法连接到小鸭，请检查网络。',
      lightReadFailed:'读取补光灯状态失败，请检查摄像头连接。', lightSetFailed:'设置补光灯失败，请检查摄像头连接。', lightNotApplied:'补光灯状态没有生效，请重试。', lightNotConfirmed:'无法确认补光灯状态。',
      tofTitle:'ToF 实时测距',tofCenter:'中心距离',tofNearest:'最近距离',tofNear:'近',tofFar:'远 · 2 m+',tofCollapse:'收起',tofExpand:'展开',tofValid:'有效分区 {count} / 64',tofTiming:'{hz} Hz · {age} ms 前',
      tofStarting:'正在连接',tofLive:'实时',tofAbsent:'未连接',tofOffline:'已离线',tofError:'传感器异常',tofUnavailable:'暂不可用',tofDisconnected:'连接中断',tofStale:'数据已过期',tofEmpty:'无有效目标',
      tofGrid:'8×8 深度热力图',tofGridDescription:'8×8 深度热力图，有效分区 {count} 个，中心 {distance}',tofZone:'第 {row} 行第 {column} 列：{distance} mm',tofZoneInvalid:'第 {row} 行第 {column} 列：无有效距离',
      tofRpcMissing:'当前固件未注册 ToF 读取接口。',tofReadFailed:'暂时无法读取 ToF，摄像头和音频可继续使用。',tofSensorError:'传感器离线或读取失败，请检查连接。',tofNotConnected:'未检测到 ToF，请检查 Qwiic 连接。',tofStaleHint:'未收到新帧，已隐藏旧的距离。',tofNoTarget:'无有效测距目标，请将物体放到传感器前方。',tofConnectionLost:'连接已中断，正在等待重连…',tofWaiting:'正在等待传感器的真实数据…'
    }
  };
  let saved;
  try { saved=localStorage.getItem('duck-cockpit-language'); } catch {}
  let language=['zh','en'].includes(saved)?saved:/^zh\b/i.test(navigator.languages?.[0]||navigator.language||'en')?'zh':'en';
  const t=(key,values={})=>(strings[language][key]||strings.en[key]||key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''));
  function apply(){
    document.documentElement.lang=language==='zh'?'zh-CN':'en';
    document.title=t('pageTitle');
    document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=t(el.dataset.i18n));
    for(const attr of ['alt','title','aria-label'])document.querySelectorAll(`[data-i18n-${attr}]`).forEach(el=>el.setAttribute(attr,t(el.getAttribute('data-i18n-'+attr))));
    const button=document.getElementById('language');
    if(button){button.textContent=language==='zh'?'EN':'中文';button.setAttribute('aria-label',t('switchLanguage'));button.title=t('switchLanguage');}
  }
  window.DuckI18n={t,get language(){return language;},setLanguage(next){
    if(!['zh','en'].includes(next))return;
    language=next;try{localStorage.setItem('duck-cockpit-language',next);}catch{}
    apply();window.dispatchEvent(new CustomEvent('duck-languagechange'));
  }};
  document.getElementById('language')?.addEventListener('click',()=>window.DuckI18n.setLanguage(language==='zh'?'en':'zh'));
  apply();
})();
