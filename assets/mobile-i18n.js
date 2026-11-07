(() => {
  const strings = {
    en: {
      voltage:'Voltage', voltageLow:'Low voltage', voltageSource:'Servo supply voltage',
      startupTitle:'Automatic startup & wake',
      startupAutostart:'Complete the one-time README setup and a successful Run in App Lab. Duck then starts the cockpit, chat and motion services automatically on power-up; no need to click Run each time.',
      startupReady:'After Wi-Fi, account binding and USB audio are ready, two duck sounds play in order:',
      startupNetwork:'1. Network connected',startupNetworkHint:'First startup sound',startupVoice:'2. Voice service ready',startupVoiceHint:'Second startup sound',
      startupWake:'After both startup sounds, say the wake word for your selected region. Speak after the wake-up duck call.',
      startupWakeSelected:'After both startup sounds, say “{phrase}” to wake Duck. Speak after the wake-up duck call.',
      startupPreviewHint:'Examples play on this phone/computer. Real startup sounds play through Duck’s speaker.',
      startupPreviewListen:'Listen',startupPreviewStop:'Stop',startupPreviewLoading:'Loading…',startupPreviewPlaying:'Playing: {name}',startupPreviewEnded:'Finished: {name}',startupPreviewStopped:'Preview stopped',startupPreviewFailed:'Unable to play this example. Please try again.',
      startupListenNetwork:'Listen to the network-connected duck sound',startupListenVoice:'Listen to the voice-ready duck sound',startupStopNetwork:'Stop the network-connected example',startupStopVoice:'Stop the voice-ready example',
      pageTitle:'Duck Cockpit', cockpit:'COCKPIT', cameraAlt:"Live view from Duck's camera", desktopControl:'Open desktop control', desktopControlLink:'Open desktop control ↗',
      switchLanguage:'切换为中文', fillView:'Fill camera view', fitView:'Fit entire camera view', openSettings:'Open settings', closeSettings:'Close settings',
      enterFullscreen:'Enter fullscreen', exitFullscreen:'Exit fullscreen', fullscreen:'Fullscreen', fullscreenError:'Could not enter fullscreen. Open this page directly in Chrome and tap Fullscreen again.', fullscreenUnsupported:'This browser does not support fullscreen.',
      camera:'Camera', startCamera:'Start camera', stopCamera:'Stop camera', startingCamera:'Starting…', stoppingCamera:'Stopping…', cameraMissing:'Camera not found', cameraOff:'CAM OFF', cameraStarting:'STARTING', cameraLive:'LIVE · {fps} FPS',
      light:'Camera light', lightOn:'Turn light on', lightOff:'Turn light off', lightEnabled:'Light on', lightDisabled:'Light off', lightUnknown:'Light: checking', lightUnavailable:'Light unavailable', lightUnsupported:'Light not supported', lightChanging:'Switching light…',
      welcomeEyebrow:'YOUR DUCK. YOUR POINT OF VIEW.', welcomeTitle:"Duck's point of view.", welcomeTitleSecond:'Ready when you are.', welcomeDescription:'Tap Start camera above to see the live view.', welcomeNote:'Turn your phone sideways and mount it on your gamepad.',
      savePhoto:'Save photo', hideControls:'Hide controls', showControls:'Show controls', viewControls:'View tools', robotStatus:'ROBOT STATUS', liveAudio:'LIVE AUDIO',
      online:'Duck online', reconnecting:'Reconnecting', disconnected:'Disconnected', waitingFeedback:'Waiting for feedback', feedback:'{hz} Hz feedback · {torque}', torqueOn:'Torque on', torqueOff:'Torque off',
      modeShadow:'Inference only', modeOff:'Torque off', modeHold:'Default pose', modePolicy:'Walk', modeCalibrate:'Calibration', modeServo:'Servo setup',
      moveStick:'Move / strafe', moveStickRoller:'Forward / back', turnStick:'Turn in place', motionActions:'Motion actions',
      actionStand:'Stand up', actionSit:'Sit', actionPick:'Pick up', actionRoll:'Roll over', actionKickLeft:'Left kick', actionKickRight:'Right kick', actionStop:'Stop',
      keyboardControls:'Keyboard shortcuts', keyboardTravel:'Forward / back', keyboardStrafe:'Strafe', keyboardTurn:'Turn', keyboardSpace:'Space', keyboardStop:'Stop',
      moreActions:'More actions', closeDialog:'Close', actionHeadLeft:'Look left', actionHeadRight:'Look right', actionHeadCenter:'Centre head', actionMouthOpen:'Open beak', actionMouthClose:'Close beak',
      poseHint:'Use the head and beak controls while standing in Walk mode.', poseRoller:'Switch from Roller to Walk to use the head controls.',
      duckSounds:'Duck calls', duckGreet:'Greeting', duckInquire:'Curious', duckAlarm:'Alert', duckPeck:'Peck', duckChirp:'Chirp', duckCoo:'Coo', duckWheee:'Excited',
      duckSoundHint:'Choose a sound to play on Duck’s speaker.', duckSpeakerMissing:'Connect a USB speaker to play duck calls.',
      duckSoundPlaying:'Playing {sound}…', duckSoundDone:'Finished playing {sound}.', duckSoundFailed:'Could not play this call. Check Duck’s speaker and try again.',
      motionStandHint:'Press Stand up to start getting up.', motionSettling:'Waiting for Duck to stand steady.',
      motionStarting:'Starting get-up…', motionArming:'Enabling the servos…', motionGettingUp:'Duck is getting up…',
      motionArmFailed:'The servos are off. Press Stand up to try again.',
      motionReady:'Drag the sticks to drive. Release to stop.', motionBusy:'Sending {action}…',
      motionAccepted:'{action} command sent.', motionInProgress:'{action} in progress', motionSeated:'Seated · press Stand up to move',
      motionBattery:'Low battery · motion locked', motionOtherOwner:'Gamepad or setup is controlling Duck.',
      motionVoiceBusy:'Joysticks paused while a voice action runs.',
      motionTof:'Forward paused by ToF. Release the forward key or stick before trying again.',
      configureGamepad:'Configure gamepad', gamepadActive:'Gamepad active', gamepadReady:'Gamepad ready', gamepadMissing:'Gamepad —',
      listen:'Listen live', stopListening:'Stop listening', listenHint:'Duck mic · tap to start', stopListenHint:'Duck mic · tap to stop', listenIdle:'Tap Listen live to hear the sounds around Duck', listenStopped:'Live listening stopped', listenConnecting:'Connecting to Duck microphone…', listening:'Listening live · {seconds} s · tap to stop',
      audioBusy:'Duck audio is busy. Stop the current audio first.', audioPaused:'Audio is paused. Tap Listen live again.', liveUnavailable:'Live audio unavailable.', liveEnded:'Duck microphone stream ended. Tap Listen live to reconnect.', liveTimedOut:'Live audio timed out. Check the connection and tap Listen live again.',
      settings:'Settings', general:'General', gamepad:'Gamepad', servos:'Servos', calibration:'Calibration', configuration:'Duck configuration',
      bindAi:'Link AI',bindingEntryCurrent:'Link AI · Current server: {region}',bindingChipCn:'CN',bindingChipGlobal:'INTL',bindingChipCustom:'CUST',bindingChipNone:'—',bindingTitle:'Link AI interaction',closeBinding:'Close AI binding',bindingIntro:'Tap China or International to switch servers, then open its platform to bind Duck.',
      bindingRegion:'Current service region',bindingStatus:'Account binding',bindingCn:'China',bindingGlobal:'International',bindingCnHint:'Wake word · 小鸭小鸭',bindingGlobalHint:'Wake word · Hey Ducky',bindingOpenCn:'Open China platform ↗',bindingOpenGlobal:'Open international platform ↗',
      bindingStep1:'Sign in to your platform, open Devices and choose Add device.',bindingStep2:'Follow the Bluetooth prompts, select the device beginning with XGO-Duck, and complete Wi-Fi setup and account binding.',bindingStep3:'Select an agent, then return here to check that the voice service is online.',
      bindingNotice:'Switching reconnects voice. Accounts, devices and agents belong to each server; conversation language, volume and motion settings are preserved.',bindingBrowser:'For web Bluetooth, use a Chrome / Edge browser with Web Bluetooth support.',
      bindingChooseRegion:'Choose service region',bindingCurrent:'In use',bindingApplying:'Switching…',bindingApplied:'Server saved. Reconnecting voice…',bindingSame:'This server is already in use.',bindingSelect:'Tap a server to save it and reconnect voice.',bindingSwitchFailed:'Switch not confirmed. Check the current region and retry.',bindingCustom:'Custom server',bindingNotConfigured:'Not selected',bindingRegionUnknown:'Check installation settings',bindingChecking:'Checking…',bindingUnavailable:'Status unavailable',bindingOnline:'Bound · Voice online',bindingBound:'Bound · Connecting',bindingCached:'Saved binding · Reconnecting',bindingUnbound:'Not bound · Add device',
      usbConnection:'USB connection', usbDescription:'Enable Host for a USB camera, audio device or wired controller.', usbNotice:'Use Wi-Fi when switching modes; USB connections will be interrupted.', usbSwitching:'Switching…', usbOn:'USB Host: On', usbOff:'USB Host: Off', usbUnavailable:'USB control unavailable.', usbHostMode:'Host mode · USB accessories enabled', usbDeviceMode:'Device mode · Host is off',
      speakerTestHint:'Play synthesized duck calls',speakerPlayingHint:'Duck calls playing · tap to stop',speakerPreparing:'Preparing…',stopSpeakerTest:'Stop playback',speakerTestDone:'Speaker test finished',speakerTestFailed:'Speaker test failed. Please retry.',speaker:'Speaker', duckVolume:'Duck volume', applyVolume:'Apply volume', testSpeaker:'Test speaker', listenMicrophone:'Listen to Duck microphone', stopAudio:'Stop audio', sendAudioFile:'Send an audio file', audioFileHint:'Choose up to 30 seconds of audio to play on Duck.',
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
      voltage:'电压', voltageLow:'低电压', voltageSource:'舵机供电总线电压',
      startupTitle:'开机自启与唤醒',
      startupAutostart:'首次按 README 完成初始化，并在 App Lab 成功 Run 一次后，后续通电会自动启动网页、聊天和运动服务，无需每次再点 Run。',
      startupReady:'完成配网、账号绑定并接好 USB 音频设备后，启动就绪会依次播放两段鸭叫：',
      startupNetwork:'① 联网成功',startupNetworkHint:'第一段鸭叫',startupVoice:'② 语音服务就绪',startupVoiceHint:'第二段鸭叫',
      startupWake:'听到两段启动提示音后，说当前选区的唤醒词。听到唤醒鸭叫后就可以说话了。',
      startupWakeSelected:'听到两段启动提示音后，说「{phrase}」唤醒。听到唤醒鸭叫后就可以说话了。',
      startupPreviewHint:'试听在当前手机或电脑播放；开机提示音由小鸭扬声器播放。',
      startupPreviewListen:'试听',startupPreviewStop:'停止',startupPreviewLoading:'正在加载…',startupPreviewPlaying:'正在试听：{name}',startupPreviewEnded:'试听结束：{name}',startupPreviewStopped:'已停止试听',startupPreviewFailed:'试听失败，请重试。',
      startupListenNetwork:'试听联网成功鸭叫',startupListenVoice:'试听语音就绪鸭叫',startupStopNetwork:'停止联网成功试听',startupStopVoice:'停止语音就绪试听',
      pageTitle:'小鸭驾驶舱', cockpit:'驾驶舱', cameraAlt:'小鸭摄像头实时画面', desktopControl:'打开桌面控制页', desktopControlLink:'打开桌面控制页 ↗',
      switchLanguage:'Switch to English', fillView:'填满画面', fitView:'显示完整画面', openSettings:'打开设置', closeSettings:'关闭设置',
      enterFullscreen:'进入全屏', exitFullscreen:'退出全屏', fullscreen:'全屏', fullscreenError:'无法进入全屏，请在 Chrome 中直接打开此页面后重试。', fullscreenUnsupported:'当前浏览器不支持全屏。',
      camera:'摄像头', startCamera:'开启摄像头', stopCamera:'关闭摄像头', startingCamera:'正在开启…', stoppingCamera:'正在关闭…', cameraMissing:'未发现摄像头', cameraOff:'摄像头已关闭', cameraStarting:'摄像头启动中', cameraLive:'实时画面 · {fps} 帧/秒',
      light:'补光灯', lightOn:'打开补光灯', lightOff:'关闭补光灯', lightEnabled:'补光灯已打开', lightDisabled:'补光灯已关闭', lightUnknown:'补光灯：正在读取', lightUnavailable:'补光灯不可用', lightUnsupported:'此摄像头暂不支持补光灯控制', lightChanging:'正在切换补光灯…',
      welcomeEyebrow:'从小鸭的视角，看见身边。', welcomeTitle:'小鸭的视角，', welcomeTitleSecond:'由你开启。', welcomeDescription:'点击上方「开启摄像头」，查看实时画面。', welcomeNote:'横放手机，配合手柄使用。',
      savePhoto:'保存照片', hideControls:'隐藏控件', showControls:'显示控件', viewControls:'画面工具', robotStatus:'机器状态', liveAudio:'实时音频',
      online:'小鸭在线', reconnecting:'正在重新连接', disconnected:'连接已断开', waitingFeedback:'等待反馈', feedback:'反馈 {hz} Hz · {torque}', torqueOn:'舵机已使能', torqueOff:'舵机卸力',
      modeShadow:'仅推理', modeOff:'舵机卸力', modeHold:'默认姿态', modePolicy:'行走', modeCalibrate:'校准', modeServo:'舵机设置',
      moveStick:'前后 / 侧移', moveStickRoller:'前进 / 后退', turnStick:'原地转向', motionActions:'动作控制',
      actionStand:'站起来', actionSit:'坐下', actionPick:'叼东西', actionRoll:'翻滚', actionKickLeft:'左脚踢球', actionKickRight:'右脚踢球', actionStop:'停止移动',
      keyboardControls:'键盘快捷键', keyboardTravel:'前进 / 后退', keyboardStrafe:'侧移', keyboardTurn:'转向', keyboardSpace:'空格', keyboardStop:'停止',
      moreActions:'更多动作', closeDialog:'关闭', actionHeadLeft:'向左转头', actionHeadRight:'向右转头', actionHeadCenter:'头部回中', actionMouthOpen:'张嘴', actionMouthClose:'闭嘴',
      poseHint:'小鸭在行走模式下站稳后，可以转头或控制嘴巴。', poseRoller:'转头需要从滚轮模式切换到普通行走模式。',
      duckSounds:'鸭叫', duckGreet:'打招呼', duckInquire:'疑问', duckAlarm:'警觉', duckPeck:'啄叫', duckChirp:'轻鸣', duckCoo:'低鸣', duckWheee:'兴奋',
      duckSoundHint:'选择一种鸭叫，用小鸭的扬声器播放。', duckSpeakerMissing:'接上 USB 扬声器即可播放鸭叫。',
      duckSoundPlaying:'正在播放「{sound}」…', duckSoundDone:'「{sound}」播放完成。', duckSoundFailed:'播放失败，请检查小鸭的扬声器后重试。',
      motionStandHint:'点击「站起来」开始起身。', motionSettling:'等待小鸭站稳。',
      motionStarting:'正在启动起身…', motionArming:'正在使能舵机…', motionGettingUp:'小鸭正在起身…',
      motionArmFailed:'舵机未使能，请重新点击「站起来」。',
      motionReady:'拖动摇杆控制方向，松手停止。', motionBusy:'正在发送「{action}」…',
      motionAccepted:'已发送「{action}」指令。', motionInProgress:'正在{action}', motionSeated:'已坐下 · 点击「站起来」后可移动',
      motionBattery:'低电压保护中 · 运动已锁定', motionOtherOwner:'手柄或调试页面正在控制小鸭。',
      motionVoiceBusy:'语音动作执行中，摇杆已暂停。',
      motionTof:'ToF 已暂停前进，请松开前进键或摇杆后再试。',
      configureGamepad:'配置手柄', gamepadActive:'手柄控制中', gamepadReady:'手柄已就绪', gamepadMissing:'手柄未连接',
      listen:'实时监听', stopListening:'停止监听', listenHint:'点击收听小鸭麦克风', stopListenHint:'点击停止收听', listenIdle:'点击实时监听，收听小鸭周围的声音', listenStopped:'实时监听已停止', listenConnecting:'正在连接小鸭麦克风…', listening:'正在监听 · {seconds} 秒 · 点击停止',
      audioBusy:'小鸭音频正在使用中，请先停止当前音频。', audioPaused:'音频播放已暂停，请再次点击实时监听。', liveUnavailable:'实时监听暂不可用。', liveEnded:'麦克风音频流已结束，请再次点击实时监听。', liveTimedOut:'实时监听连接超时，请检查网络后重试。',
      settings:'设置', general:'常规', gamepad:'手柄', servos:'舵机', calibration:'校准', configuration:'小鸭配置',
      bindAi:'AI 绑定',bindingEntryCurrent:'AI 绑定 · 当前服务区域：{region}',bindingChipCn:'国内',bindingChipGlobal:'国际',bindingChipCustom:'自定',bindingChipNone:'未选',bindingTitle:'AI 交互绑定',closeBinding:'关闭绑定窗口',bindingIntro:'点击国内版或国际版即可切换服务器，再打开对应平台绑定设备。',
      bindingRegion:'当前服务区域',bindingStatus:'绑定状态',bindingCn:'国内版',bindingGlobal:'国际版',bindingCnHint:'唤醒词 · 小鸭小鸭',bindingGlobalHint:'唤醒词 · Hey Ducky',bindingOpenCn:'打开国内版 ↗',bindingOpenGlobal:'打开国际版 ↗',
      bindingStep1:'在所选平台登录，进入「设备」并点击「添加设备」。',bindingStep2:'按提示开启蓝牙，选择 XGO-Duck 开头的设备，完成配网和账号绑定。',bindingStep3:'在平台选择智能体，再回到这里确认语音已上线。',
      bindingNotice:'切换服务器会重新连接语音服务，两边的账号、设备绑定和智能体独立；对话语言、音量及运动设置保持。',bindingBrowser:'网页蓝牙请使用支持 Web Bluetooth 的 Chrome / Edge 浏览器。',
      bindingChooseRegion:'选择服务区域',bindingCurrent:'当前使用',bindingApplying:'正在切换…',bindingApplied:'服务区域已保存，正在重连语音…',bindingSame:'当前已使用该服务器。',bindingSelect:'点击服务器即可保存并重连语音。',bindingSwitchFailed:'未能确认切换，请核对当前服务区域后重试。',bindingCustom:'自定义服务器',bindingNotConfigured:'尚未选择',bindingRegionUnknown:'请核对安装时的选区',bindingChecking:'正在读取',bindingUnavailable:'暂时无法读取状态',bindingOnline:'已绑定 · 语音在线',bindingBound:'已绑定 · 正在连接',bindingCached:'已保存绑定 · 正在重连',bindingUnbound:'未绑定 · 请添加设备',
      usbConnection:'USB 连接', usbDescription:'使用 USB 摄像头、音频设备或有线手柄时，请开启 Host 模式。', usbNotice:'请通过 Wi-Fi 操作；切换模式会中断当前 USB 连接。', usbSwitching:'正在切换…', usbOn:'USB Host：已开启', usbOff:'USB Host：已关闭', usbUnavailable:'USB 控制暂不可用。', usbHostMode:'Host 模式 · USB 外设已启用', usbDeviceMode:'Device 模式 · Host 已关闭',
      speakerTestHint:'播放几声合成鸭叫',speakerPlayingHint:'鸭叫播放中 · 点击停止',speakerPreparing:'正在准备…',stopSpeakerTest:'停止播放',speakerTestDone:'扬声器测试已结束',speakerTestFailed:'扬声器测试失败，请重试。',speaker:'扬声器', duckVolume:'小鸭音量', applyVolume:'应用音量', testSpeaker:'测试扬声器', listenMicrophone:'监听小鸭麦克风', stopAudio:'停止音频', sendAudioFile:'发送音频文件', audioFileHint:'选择不超过 30 秒的音频，在小鸭扬声器播放。',
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
  const systemLanguage=()=>/^zh(?:[-_]|$)/i.test(navigator.languages?.[0]||navigator.language||'en')?'zh':'en';
  let language=systemLanguage();
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
    language=next;
    apply();window.dispatchEvent(new CustomEvent('duck-languagechange'));
  }};
  document.getElementById('language')?.addEventListener('click',()=>window.DuckI18n.setLanguage(language==='zh'?'en':'zh'));
  window.addEventListener('languagechange',()=>window.DuckI18n.setLanguage(systemLanguage()));
  apply();
})();
