const $=id=>document.getElementById(id);
const MOTOR_IDS=[10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
const MOTOR_COUNT=MOTOR_IDS.length, ALL_MASK=(1<<MOTOR_COUNT)-1;
const UNIFIED_PHOTOS=[[983,770],[620,743],[690,661],[599,669]];

const strings={
  en:{
    docTitle:'Calibration · XGO Duck',title:'Calibration',
    imuTitle:'IMU calibration',imuStart:'Calibrate IMU',
    imuInstructions:'Unload the servos and support Duck with the board upright, the battery connector pointing up and the board’s bottom edge against a level table. Keep Duck still until calibration finishes.',
    imuPhotoCaption:'IMU placement reference',
    imuPhotoAlt:'Duck supported with its board upright and the bottom edge against a level table',
    imuWaiting:'Waiting for IMU feedback…',imuUnavailable:'IMU calibration is unavailable on this App version.',
    imuNoFeedback:'Waiting for live IMU feedback. Keep the board still and check the IMU connection if the wait continues.',imuReady:'Ready to calibrate.',
    imuEnterFirst:'Click Unload all to enter calibration.',imuUnloadFirst:'Unload all servos before calibrating the IMU.',
    imuWaitForServo:'Wait for the current servo operation to finish.',
    imuStarting:'Starting IMU calibration…',imuSampling:'Hold still. Sampling {percent}%.',
    imuSettling:'Hold still. Sampling will start shortly.',
    imuRetry:'Readings fluctuated. Automatic retry {retry}/{max_retries}.',
    imuExhausted:'No stable readings after {retries} automatic retries. Support the board and try again.',
    imuSaved:'IMU calibration saved.',imuCancelled:'IMU calibration cancelled.',imuFailed:'IMU calibration failed.',
    imuUpright:'Hold the board upright with the battery connector pointing up.',
    imuMovement:'Movement detected. Hold the board still and try again.',
    imuGyroZero:'Gyroscope zero offset is too large. Check the IMU and try again.',
    imuInvalid:'Invalid IMU readings. Check the IMU and try again.',
    imuAcceleration:'Unexpected acceleration. Check the board position and try again.',
    imuTooFew:'Too few IMU readings. Check the connection and try again.',
    imuRestarted:'The IMU restarted. Start calibration again.',
    imuInterrupted:'IMU feedback was interrupted. Check the connection and try again.',
    imuPreviousKept:'The previous calibration is still saved.',
    imuRecalibrate:'The saved IMU calibration is invalid or from an older version. Calibrate again here.',
    exit:'Exit',unloadAll:'Unload all',
    entering:'Entering calibration and unloading all servos…',
    unloaded:'All servos are unloaded. Use the reference photos to position Duck.',
    centerWritten:'Center saved for ID {id}.',
    closed:'Calibration closed. All servos are unloaded.',
    unifiedTitle:'Unified calibration',
    unifiedSequence:'Servo centers → Neck (ID 30) → IMU → Default pose check',
    neckTitle:'Calibrate the neck servo (ID 30) separately',neckStart:'Calibrate ID 30',
    neckInstruction:'With the servos unloaded, position the neck parallel to the torso as shown. Click Calibrate ID 30, then unload it before calibrating the IMU.',
    neckPhoto:'ID 30 neck calibration pose',encoder:'Encoder {value}',
    jointOffline:'Waiting for feedback from servo ID {id}.',jointUnload:'Unload all servos before positioning the joint.',
    jointWorking:'Setting the center of ID {id}…',jointSaved:'ID {id} is holding its saved center. Select the next servo to continue, or click Unload all to adjust this one again.',
    jointNoReply:'ID {id} did not reply. Check the servo connection.',jointTimeout:'No centering confirmation. Check the servo feedback before retrying.',
    finishSingle:'If the servos are installed correctly and the pose is still off, use the guide below to recalibrate the affected joint.',
    singleTitle:'Single-servo calibration',singleSelect:'Servo',
    singleIntro:'Support Duck. Find the selected servo in the photo and position its unloaded joint by hand to match the diagram. After setting its center, you can move on to the next unloaded joint.',
    singleLegend:'Orange marks the joint. Align it with the green dashed line. Red shows a misaligned position.',
    singleLoading:'Loading the calibration guide…',singleUnavailable:'The guide could not load. Close and reopen Single-servo calibration to retry.',
    singlePose:'ID {id} · Center position',singleStart:'Set center for ID {id}',
    unifiedPose:'Set the servo centers',photoNumber:'Photo {number}',
    unifiedPoseWarning:'Unload the servos before moving the joints by hand. Keep wires clear of the gaps between the legs and body.',
    completeCalibration:'Calibrate all 15 servos',
    unifiedWorking:'Calibrating the servo centers. Keep Duck in the calibration pose.',
    unifiedRetry:'Retrying servo calibration ({retry}/{max_retries}). Keep Duck in the calibration pose.',
    unifiedSaved:'All 15 servo centers saved. Position the neck for the separate ID 30 calibration below.',
    unifiedExhausted:'Servo calibration still could not finish after {retries} automatic retries.',
    unifiedCheckHardware:'Fully charge the battery, turn off Duck and check that the servo cables are secure, then rerun servo calibration.',
    unifiedUnloadFirst:'Unload all servos before unified calibration.',
    unifiedAllOnline:'Waiting for live feedback from all 15 servos. Keep Duck in the calibration pose.',
    unifiedWaitingIds:'Waiting for live feedback from servo IDs {ids}. Keep Duck in the calibration pose.',
    mcuWaiting:'Waiting for STM32 calibration mode and fresh servo feedback…',
    photoOpen:'Open the full-size picture',
    unifiedPhotoAlts:['Front view of the reference pose','Side view of the head and neck','Close both legs together','Move both legs back until they touch the body'],
    unifiedSteps:[
      'Position Duck as shown in photos 1 and 2. Tilt the neck back to match the reference, lower the head against the neck, and gently close the beak.',
      'Squeeze both legs together with both hands until they close completely (photo 3).',
      'Move both legs back at the same time until they touch the body (photo 4). Move the wires to one side to avoid pinching them.',
      'Let go and click Calibrate all 15 servos.'
    ],
    finishTitle:'Check the default pose',
    finishText:'After calibrating the IMU and all 15 servos, put Duck on a level surface and support it. Click Default pose to enable the servos and compare its stance with the photo.',
    finishMismatch:'If the pose differs from the photo, support Duck and click Unload all.',
    finishRecheckCenters:'Recheck the servo centers',
    finishCheckInstallation:'If the centers are correct, a servo may be installed at the wrong joint.',
    finishOpenServoSetup:'Open Servo setup',
    finishJogCheck:', select an ID on the 3D model, and click Start test. Move the slider slightly and check that the moving joint matches the highlighted one. When done, click Stop and unload.',
    poseStart:'Default pose',posePhoto:'Default pose reference photo',
    posePhotoCaption:'Default pose · roller feet on the left, standard feet on the right',
    poseWaiting:'Waiting for feedback…',poseNeedUnload:'Unload the servos before testing the default pose.',
    poseFeedback:'Waiting for live feedback from all 15 servos and the IMU. Support Duck while you wait.',
    poseCalBusy:'Wait for the current calibration to finish.',poseStarting:'Waiting for the servos to enable…',
    poseHolding:'Default pose is active. Compare Duck with the photo.',poseEnded:'Default pose test ended.',
    poseArmFailed:'STM32 did not confirm servo enable. Check the servo power and try again.',
    poseLowBattery:'Low battery protection is active. Recharge before testing the default pose.',
    pictureOpen:'Enlarge picture',pictureClose:'Close picture',
    connectionLost:'Cannot read Duck’s status. Check the connection before setting a center.',
    feedbackRestored:'Feedback restored. Check the current calibration state below.',
    requestFailed:'The request failed. Check the connection and try again.',
    invalidReply:'Duck did not confirm the servo calibration. Check its status before retrying.',
    notCalibrating:'Calibration is closed. Click Unload all to enter again.',
    staleFeedback:'STM32 feedback stopped. Check the board connection.',
    sessionChanged:'Calibration was closed or changed. Enter again before setting a center.'
  },
  zh:{
    docTitle:'标定 · XGO Duck',title:'舵机与 IMU 标定',
    imuTitle:'IMU 标定',imuStart:'标定 IMU',
    imuInstructions:'先卸力，再扶稳小鸭，让板子竖直、电池接口朝上、下边缘贴在水平桌面上。点「标定 IMU」后，保持静止直到标定结束。',
    imuPhotoCaption:'IMU 摆放参考',
    imuPhotoAlt:'扶稳小鸭，让板子竖直，下边缘贴住水平桌面',
    imuWaiting:'等待 IMU 反馈…',imuUnavailable:'当前应用版本不支持 IMU 标定。',
    imuNoFeedback:'正在等待 IMU 的实时反馈，请保持板子静止。若持续等待，请检查 IMU 连接。',imuReady:'可以开始标定。',
    imuEnterFirst:'请先点「全部卸力」进入标定。',imuUnloadFirst:'请先点「全部卸力」，再标定 IMU。',
    imuWaitForServo:'请等待当前舵机操作完成。',
    imuStarting:'正在启动 IMU 标定…',imuSampling:'请保持静止，已采样 {percent}%。',
    imuSettling:'请保持静止，即将开始采样。',
    imuRetry:'读数有波动，正在自动重试（{retry}/{max_retries}）。',
    imuExhausted:'自动重试 {retries} 次仍未得到稳定读数。请扶稳主板后再试。',
    imuSaved:'IMU 标定已保存。',imuCancelled:'IMU 标定已取消。',imuFailed:'IMU 标定失败。',
    imuUpright:'请让板子直立，电池接口朝上。',imuMovement:'检测到晃动，请保持板子静止后重试。',
    imuGyroZero:'陀螺仪零偏过大，请检查 IMU 后重试。',
    imuInvalid:'IMU 读数无效，请检查 IMU 后重试。',
    imuAcceleration:'加速度异常，请检查主板摆放姿态后重试。',
    imuTooFew:'IMU 读数不足，请检查连接后重试。',
    imuRestarted:'IMU 已重启，请重新开始标定。',
    imuInterrupted:'IMU 反馈中断，请检查连接后重试。',imuPreviousKept:'上一次标定结果已保留。',
    imuRecalibrate:'IMU 标定记录无效或为旧版，请在此重新标定。',
    exit:'退出',unloadAll:'全部卸力',
    entering:'正在进入标定，所有舵机卸力中…',
    unloaded:'所有舵机已卸力。请按参考图摆放小鸭。',
    centerWritten:'已保存 {id} 号舵机的中位。',
    closed:'标定已关闭，所有舵机已卸力。',
    unifiedTitle:'统一标定',
    unifiedSequence:'舵机中位 → 30 号颈部舵机 → IMU → 检查默认姿势',
    neckTitle:'单独标定 30 号颈部舵机',neckStart:'标定 30 号舵机',
    neckInstruction:'确认舵机已卸力，按图将脖子摆到与躯干平行，再点「标定 30 号舵机」。标定后点「全部卸力」，再继续标定 IMU。',
    neckPhoto:'30 号颈部舵机标定姿态',encoder:'编码器 {value}',
    jointOffline:'正在等待 {id} 号舵机反馈。',jointUnload:'请先点「全部卸力」，再手动摆放关节。',
    jointWorking:'正在设置 {id} 号舵机中位…',jointSaved:'{id} 号舵机已标定，正在保持中位。可继续标定下一个。要重调这个舵机，请先点「全部卸力」。',
    jointNoReply:'{id} 号舵机未应答，请检查舵机连接。',jointTimeout:'尚未收到舵机的中位确认，请检查反馈后重试。',
    finishSingle:'如果舵机安装位置正确，但姿势仍有偏差，可展开下方的单舵机标定，单独校准对应关节。',
    singleTitle:'单舵机标定',singleSelect:'选择舵机',
    singleIntro:'扶稳小鸭，在实拍图上找到所选舵机，将这个已卸力的关节手动摆到图示中位。标定后可直接选择下一个已卸力的关节。',
    singleLegend:'橙黄色标出要调整的关节，绿色虚线是对齐位置，红色示意偏离中位的姿势。',
    singleLoading:'正在加载标定图例…',singleUnavailable:'标定图例加载失败，请收起后重新展开。',
    singlePose:'ID {id} · 中位图例',singleStart:'设置 {id} 号舵机中位',
    unifiedPose:'舵机中位标定',photoNumber:'图 {number}',
    unifiedPoseWarning:'手动摆位前先确认舵机已卸力。拨开线束，以免被腿和躯干夹住。',
    completeCalibration:'标定 15 个舵机',
    unifiedWorking:'正在标定舵机，请保持小鸭的标定姿势。',
    unifiedRetry:'正在自动重试（{retry}/{max_retries}）。请保持小鸭的标定姿势。',
    unifiedSaved:'15 个舵机的中位已保存。请按下图摆放脖子，单独标定 30 号舵机。',
    unifiedExhausted:'自动重试 {retries} 次后仍未完成标定。',
    unifiedCheckHardware:'请把电池充满，关机检查舵机线是否插紧，再重新标定。',
    unifiedUnloadFirst:'统一标定前，请先让所有舵机卸力。',
    unifiedAllOnline:'正在等待 15 个舵机的实时反馈，请保持标定姿势。',
    unifiedWaitingIds:'正在等待 {ids} 号舵机的实时反馈，请保持标定姿势。',
    mcuWaiting:'正在等待 STM32 进入标定模式并返回舵机反馈…',
    photoOpen:'打开原尺寸图片',
    unifiedPhotoAlts:['标定姿势正面','头颈摆放侧面','同时捏住双腿，确保腿完全闭合','双腿后掰至接触躯干'],
    unifiedSteps:[
      '按图 1 和图 2 摆放小鸭。将脖子向后仰到图示位置，低头靠近脖子并自然闭嘴。',
      '双手同时捏住左右腿，确保腿完全闭合（图 3）。',
      '双手同时向后掰，让左右腿与躯干接触（图 4）。把线调整到一边，避免夹到线。',
      '松手，点击「标定 15 个舵机」。'
    ],
    finishTitle:'检查默认姿势',
    finishText:'完成 IMU 和 15 个舵机的标定后，把小鸭放到平坦处并扶稳。点击「默认姿势」让舵机上力，再从侧面对照实拍。',
    finishMismatch:'如果姿势和实拍不一致，先扶稳小鸭并点「全部卸力」。',
    finishRecheckCenters:'复查舵机的中位标定',
    finishCheckInstallation:'如果标定位置正确，可能是舵机装错了位置。',
    finishOpenServoSetup:'打开舵机设置',
    finishJogCheck:'，在 3D 图上选中舵机，点击「开始测试」。轻拖滑条，确认实际微动的关节和图中高亮的位置一致。测完点「停止并卸力」。',
    poseStart:'默认姿势',posePhoto:'默认姿势实拍',
    posePhotoCaption:'默认姿势实拍 · 左为轮脚，右为普通脚掌',
    poseWaiting:'等待反馈…',poseNeedUnload:'请先卸力，再测试默认姿势。',
    poseFeedback:'正在等待 15 个舵机和 IMU 的实时反馈，请扶稳小鸭。',
    poseCalBusy:'请等待当前标定操作完成。',poseStarting:'正在等待舵机使能…',
    poseHolding:'默认姿势已启用，请对照实拍。',poseEnded:'默认姿势测试已结束。',
    poseArmFailed:'STM32 未确认舵机使能，请检查舵机供电后重试。',
    poseLowBattery:'低电压保护中，请充电后再测试默认姿势。',
    pictureOpen:'放大图片',pictureClose:'关闭大图',
    connectionLost:'暂时读不到小鸭状态，请检查连接后再设置中位。',
    feedbackRestored:'反馈已恢复，请根据下方的标定状态继续操作。',
    requestFailed:'请求未完成，请检查连接后重试。',
    invalidReply:'小鸭尚未确认舵机标定结果，请先核对状态再重试。',
    notCalibrating:'标定已关闭，点「全部卸力」可重新进入。',
    staleFeedback:'STM32 反馈中断，请检查主板连接。',
    sessionChanged:'标定已关闭或切换，请重新进入后再设置中位。'
  }
};

// Same rule as the cockpit: Chinese browsers get Chinese, everything else English; the toggle only lasts for this page.
const panelLanguage=()=>window.DuckPanelLanguage.chinese?'zh':'en';
let language=panelLanguage();
const t=(key,values={})=>String(strings[language][key]??strings.en[key]??key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''));
const list=key=>strings[language][key]||strings.en[key]||[];
function h(tag,attrs,...kids){
  const el=document.createElement(tag);
  for(const [name,value] of Object.entries(attrs||{})){
    if(value==null||value===false)continue;
    if(name==='class')el.className=value;
    else if(name==='text')el.textContent=value;
    else el.setAttribute(name,value===true?'':value);
  }
  el.append(...kids.flat().filter(kid=>kid!=null&&kid!==false));
  return el;
}
async function readJSON(url,options={},timeout=3000){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const r=await fetch(url,{...options,signal:controller.signal,cache:'no-store'});
    const x=await r.json();
    if(!r.ok)throw new Error(typeof x.detail==='string'?x.detail:t('requestFailed'));
    return x;
  }finally{clearTimeout(timer);}
}
async function post(path, body={}, timeout=3000){
  return readJSON('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},timeout);
}
function renderMessage(){$('message').textContent=note.error?errorText(note.error):t(note.key,note.values);}
function setNote(key,values){note={key,values};renderMessage();}
function errorText(e){
  const message=e.message||String(e);
  const missingJoint=message.match(/^No servo replied at ID (\d+); centering was not started$/);
  if(missingJoint)return t('jointNoReply',{id:missingJoint[1]});
  const imuRetries=message.match(/^No stable IMU readings after (\d+) automatic retries\./);
  if(imuRetries)return t('imuExhausted',{retries:imuRetries[1]});
  const exhausted=message.match(/^Sitting calibration failed after (\d+) retries\./);
  if(exhausted)return t('unifiedExhausted',{retries:exhausted[1]})+' '+t('unifiedCheckHardware');
  if(/^Sitting calibration (timed out|failed) at (2048|target positions)\.$/.test(message)){
    return t('unifiedCheckHardware');
  }
  const known={'not in calibrate mode':'notCalibrating','MCU feedback is stale':'staleFeedback',
    'Unload all joints before sitting calibration.':'unifiedUnloadFirst',
    'All 15 servos must be online for sitting calibration.':'unifiedAllOnline',
    'calibration session changed':'sessionChanged',
    'centering acknowledgement timeout':'jointTimeout',
    'Unload the servos before testing the default pose.':'poseNeedUnload',
    'All 15 servos and the IMU need fresh feedback for the default pose test.':'poseFeedback',
    'Wait for the current calibration to finish.':'poseCalBusy',
    '低电压保护中，请充电后重新使能。 Low battery protection is active.':'poseLowBattery',
    'STM32 未确认舵机使能，请重新点击「站起来」。':'poseArmFailed',
    'Unload all joints before calibrating the IMU.':'imuUnloadFirst',
    'Hold the board upright with the battery connector pointing up.':'imuUpright',
    'Movement detected. Hold the board still and try again.':'imuMovement',
    'Gyroscope zero offset is too large. Check the IMU and try again.':'imuGyroZero',
    'IMU vector must have three values':'imuInvalid','IMU contains nonfinite values':'imuInvalid',
    'Unexpected acceleration. Keep the board still and try again.':'imuAcceleration',
    'Not enough IMU readings. Check the connection and try again.':'imuTooFew',
    'Not enough IMU readings. Keep the board still and try again.':'imuTooFew',
    'The IMU restarted. Try again.':'imuRestarted','required servo feedback / IMU missing':'imuInterrupted',
    'IMU readings were interrupted. Try again.':'imuInterrupted',
    'IMU readings are stale. Check the connection and try again.':'imuInterrupted',
    'Calibration could not be loaded. Calibrate again or reset it.':'imuRecalibrate',
    'IMU 标定文件无效或为旧版。请在 9527 的 Control 页重新标定 IMU。':'imuRecalibrate'};
  if(known[message])return t(known[message]);
  if(e.name==='AbortError'||e instanceof TypeError||e instanceof SyntaxError)return t('requestFailed');
  return message;
}
function showError(e){note={error:e};renderMessage();}

function renderUnified(){
  $('unified-photos').replaceChildren(...UNIFIED_PHOTOS.map(([width,height],i)=>{
    const number=t('photoNumber',{number:i+1});
    const src=`cal-unified-${i+1}.png`;
    return h('figure',{},
      h('button',{type:'button',class:'cal-enlarge','data-picture':src,'aria-label':t('pictureOpen')+' · '+number},
        h('img',{src,width,height,loading:'lazy',alt:list('unifiedPhotoAlts')[i]})),
      h('figcaption',{},h('b',{text:number}),h('span',{text:list('unifiedPhotoAlts')[i]})));
  }));
  $('unified-steps').replaceChildren(...list('unifiedSteps').map(step=>h('li',{text:step})));
}

function renderSinglePictures(){
  const zone=guide.zones.find(group=>group.ids.includes(selectedId));
  const info=guide.joints[selectedId], photo=guide.photos[zone.photo];
  const caption=t('singlePose',{id:selectedId});
  function picture(src,width,height,title,detail,dim){
    const img=h('img',{src,width,height,alt:title});
    const content=dim?h('span',{class:'single-zone-photo'},img,
      h('span',{class:'single-photo-dim '+dim,'aria-hidden':'true'})):img;
    return h('figure',{},h('button',{type:'button',class:'cal-enlarge','data-picture':src,
      'aria-label':t('pictureOpen')+' · '+title},content),
      h('figcaption',{},h('b',{text:title}),detail&&h('span',{text:detail})));
  }
  const src=info.image_version?info.image+'?v='+info.image_version:info.image;
  const figures=[picture(photo.src,photo.width,photo.height,zone.title[language],zone.note[language],
    zone.focus==='right'?'left':zone.focus==='left'?'right':null),
    picture(src,340,330,caption)];
  if(info.photo)figures.push(picture(info.photo.src,info.photo.width,info.photo.height,info.photo[language]));
  $('single-pictures').replaceChildren(...figures);
  $('single-pose').textContent=info.pose[language];
  $('single-start').textContent=t('singleStart',{id:selectedId});
}
function renderSingleGuide(){
  if($('single-panel').hidden)return;
  renderStatus('single-loading',guide?'':t(guideLoading?'singleLoading':'singleUnavailable'));
  $('single-guide').hidden=!guide;
  if(!guide)return;
  $('single-id').replaceChildren(...guide.zones.map(zone=>h('optgroup',{label:zone.title[language]},
    zone.ids.map(id=>h('option',{value:id,text:'ID '+id+' · '+zone.title[language]+' · '+guide.joints[id].name[language]})))));
  $('single-id').value=String(selectedId);
  renderSinglePictures();
}
async function toggleSingleGuide(){
  const panel=$('single-panel');
  panel.hidden=!panel.hidden;
  $('single-toggle').setAttribute('aria-expanded',String(!panel.hidden));
  if(panel.hidden)return;
  $('single-title').focus({preventScroll:true});
  panel.scrollIntoView({block:'start'});
  if(!guide&&!guideLoading){
    guideLoading=true;
    renderSingleGuide();
    try{
      const data=await readJSON('calibrate-joints.json');
      const zoneIds={head:MOTOR_IDS.slice(10),left:MOTOR_IDS.slice(0,5),right:MOTOR_IDS.slice(5,10)};
      if(!Array.isArray(data.zones)||data.zones.length!==3||
          !Object.entries(zoneIds).every(([key,ids])=>data.zones.some(zone=>zone.key===key&&
            ids.join()===zone.ids?.join()&&data.photos?.[zone.photo]?.src&&zone.title?.en&&zone.title?.zh&&zone.note?.en&&zone.note?.zh))||
          !MOTOR_IDS.every(id=>{const j=data.joints?.[id];return j?.image&&j.name?.en&&j.name?.zh&&j.pose?.en&&j.pose?.zh;})){
        throw new Error('Invalid calibration guide');
      }
      guide=data;
    }catch{guide=null;}
    finally{guideLoading=false;}
  }
  renderSingleGuide();
  refresh();
}

let note={key:'entering'}, statusBusy=false, active=false, fresh=false, epoch=0;
let robotStatus=null, presentMask=0, held=Array(MOTOR_COUNT).fill(false);
let sessionBusy=false, enterRequest=null, unifiedBusy=false;
let unifiedNotice=null, jointNotice=null, centeringId=null;
let guide=null, guideLoading=false, selectedId=30;
let imuState=null, imuRequest=null, imuError=null;
let poseRequest=null, posePreview=false, poseNotice=null;

const imuBusy=()=>Boolean(imuRequest||imuState?.active);
const calibrationBusy=()=>sessionBusy||unifiedBusy||centeringId!==null||imuBusy();
const feedbackReady=()=>fresh&&Number.isFinite(robotStatus?.feedback_age_ms)&&robotStatus.feedback_age_ms<150;
const servosEnabled=()=>Boolean(robotStatus?.enabled);
const calibrationHolding=()=>servosEnabled()||held.some(Boolean);

function calibrationBlockReason(){
  if(!fresh)return 'mcuWaiting';
  if(calibrationBusy())return 'imuWaitForServo';
  if(!active)return 'notCalibrating';
  if(!feedbackReady()||!robotStatus.mcu_calibrating)return 'mcuWaiting';
  if(robotStatus.battery?.low)return 'poseLowBattery';
  return '';
}
function unifiedBlockReason(){
  return calibrationBlockReason()
    ||(calibrationHolding()?'unifiedUnloadFirst':'')
    ||(presentMask!==ALL_MASK?'unifiedAllOnline':'');
}
function jointOnline(id){
  const index=MOTOR_IDS.indexOf(id);
  return index>=0&&feedbackReady()&&robotStatus.motors?.[index]===id&&Boolean(presentMask&(1<<index));
}
function jointBlockReason(id){
  const index=MOTOR_IDS.indexOf(id);
  return calibrationBlockReason()
    ||(robotStatus.sitting_calibration?'imuWaitForServo':'')
    ||(servosEnabled()?'jointUnload':'')
    ||(!jointOnline(id)?'jointOffline':'')
    ||(typeof robotStatus.cal_done?.[index]!=='boolean'?'mcuWaiting':'')
    ||(held[index]?'jointSaved':'');
}
function imuBlockReason(){
  if(!fresh)return 'imuWaiting';
  if(!imuState)return 'imuUnavailable';
  if(!feedbackReady()||!robotStatus.imu_ok)return 'imuNoFeedback';
  if(sessionBusy||unifiedBusy||centeringId!==null)return 'imuWaitForServo';
  if(!active)return 'imuEnterFirst';
  if(!robotStatus.mcu_calibrating)return 'mcuWaiting';
  if(calibrationHolding())return 'imuUnloadFirst';
  return '';
}
function poseBlockReason(){
  if(!fresh)return 'poseWaiting';
  if(calibrationBusy())return 'poseCalBusy';
  if(!['calibrate','shadow','off'].includes(robotStatus?.mode)||calibrationHolding())return 'poseNeedUnload';
  if(!robotStatus.full_robot_ready)return 'poseFeedback';
  if(robotStatus.battery?.low)return 'poseLowBattery';
  return '';
}

function renderStatus(id,message,state=''){
  const el=$(id);
  el.hidden=!message;
  el.dataset.state=state;
  if(el.textContent!==message)el.textContent=message;
}
function refreshImu(){
  const blocked=imuBlockReason();
  const sampling=fresh&&Boolean(imuState?.active);
  $('imu-start').disabled=Boolean(blocked)||imuBusy();
  $('imu-unload').hidden=blocked!=='imuUnloadFirst'&&blocked!=='imuEnterFirst';
  const settling=imuState?.phase==='settling';
  $('imu-progress').hidden=!sampling||settling;
  $('imu-progress').value=imuState?.progress??0;

  let message=t(blocked||'imuReady'), tone='';
  if(imuRequest)message=t('imuStarting');
  else if(sampling){
    message=settling?t('imuSettling'):t('imuSampling',{percent:Math.round($('imu-progress').value*100)});
    if(imuState.retry)message=t('imuRetry',imuState)+' '+message;
  }
  else if(fresh&&imuState?.state==='cancelled')message=t('imuCancelled');
  else if((!blocked||blocked==='imuNoFeedback')&&(imuError||imuState?.state==='error')){
    const failure=imuState?.message?.replace(/^Previous calibration kept\. /,'');
    message=imuError?errorText(imuError):failure?errorText(new Error(failure)):t('imuFailed');
    if(!imuError&&imuState.calibrated)message+=' '+t('imuPreviousKept');
    tone='error';
  }else if(!blocked&&imuState?.calibrated){
    message=t('imuSaved');
    tone='saved';
  }
  renderStatus('imu-status',message,tone);
}
function refreshJoint(prefix,id){
  const blocked=jointBlockReason(id), index=MOTOR_IDS.indexOf(id);
  $(prefix+'-start').disabled=Boolean(blocked);
  $(prefix+'-unload').hidden=active&&!calibrationHolding();
  const value=jointOnline(id)?robotStatus.raw_pos?.[index]:null;
  $(prefix+'-encoder').textContent=Number.isFinite(value)?t('encoder',{value}):'';
  const status=prefix+'-status';
  if(centeringId===id&&active)renderStatus(status,t('jointWorking',{id}));
  else if(jointNotice?.id===id&&jointNotice.error&&(!blocked||blocked==='jointSaved'))renderStatus(status,errorText(jointNotice.error),'error');
  else renderStatus(status,blocked?t(blocked,{id}):'',blocked==='jointSaved'?'saved':'');
}
function refreshPose(){
  const blocked=poseBlockReason();
  const holding=posePreview&&fresh&&robotStatus?.full_robot_ready&&servosEnabled();
  $('pose-start').disabled=Boolean(blocked)||posePreview;
  let message='';
  if(poseRequest)message=t('poseStarting');
  else if(posePreview)message=t(!fresh||!robotStatus?.full_robot_ready?'poseWaiting':holding?'poseHolding':'poseStarting');
  else if(poseNotice)message=poseNotice.error?errorText(poseNotice.error):t(poseNotice.key);
  else if(blocked)message=t(blocked);
  const failed=Boolean(poseNotice?.error||poseNotice?.key==='poseArmFailed');
  renderStatus('pose-status',message,holding?'saved':failed?'error':'');
}
function refresh(){
  refreshImu();
  refreshJoint('neck',30);
  if(guide&&!$('single-panel').hidden){
    refreshJoint('single',selectedId);
    $('single-id').disabled=centeringId!==null;
  }
  refreshPose();
  const blocked=unifiedBlockReason();
  $('complete-calibration').disabled=Boolean(blocked);
  $('unified-unload').hidden=active&&!calibrationHolding();
  const missing=blocked==='unifiedAllOnline'&&robotStatus.motors?.length===MOTOR_COUNT
    ?robotStatus.motors.filter((_,i)=>!(presentMask&(1<<i))):[];
  let unifiedText=missing.length?t('unifiedWaitingIds',{ids:missing.join(language==='zh'?'、':', ')}):blocked?t(blocked):'';
  if(unifiedNotice)unifiedText=unifiedNotice.error?errorText(unifiedNotice.error):t(unifiedNotice.key,unifiedNotice.values);
  renderStatus('unified-status',unifiedText,unifiedNotice?.error?'error':unifiedNotice?.key==='unifiedSaved'?'saved':'');

  document.querySelectorAll('[data-action=unload]').forEach(btn=>{btn.disabled=calibrationBusy();});
}
function renderAll(){
  document.title=t('docTitle');
  $('title').textContent=t('title');
  document.querySelectorAll('[data-label]').forEach(el=>{el.textContent=t(el.dataset.label);});
  $('imu-photo')?.setAttribute('alt',t('imuPhotoAlt'));
  $('imu-photo-button')?.setAttribute('aria-label',t('pictureOpen')+' · '+t('imuPhotoCaption'));
  $('neck-photo').alt=t('neckPhoto');
  $('default-pose-photo').alt=t('posePhoto');
  $('picture-close').textContent=t('pictureClose');
  $('picture-dialog').setAttribute('aria-label',t('pictureOpen'));
  renderUnified();
  renderSingleGuide();
  renderMessage();
  refresh();
}

async function calibrateUnified(){
  if(unifiedBlockReason())return;
  const revision=++epoch;
  unifiedBusy=true;jointNotice=null;
  unifiedNotice={key:'unifiedWorking'};
  refresh();
  try{
    // Allow both stages and up to three board-side retries to finish.
    const result=await post('cal/sitting',{},20000);
    if(revision!==epoch||!active)return;
    if(result.ok!==true||result.positions?.length!==MOTOR_COUNT)throw new Error(t('invalidReply'));
    unifiedNotice={key:'unifiedSaved'};
  }catch(error){
    if(revision===epoch)unifiedNotice={error};
  }finally{if(revision===epoch)epoch++;unifiedBusy=false;refresh();}
}
async function calibrateJoint(id){
  if(jointBlockReason(id))return;
  const index=MOTOR_IDS.indexOf(id);
  const revision=++epoch;
  centeringId=id;jointNotice=null;
  refresh();
  try{
    const result=await post('cal/center',{index});
    if(revision!==epoch||!active)return;
    if(result.ok!==true||result.index!==index||result.id!==id)throw new Error(t('invalidReply'));
    held[index]=true;
    setNote('centerWritten',{id});
  }catch(error){if(revision===epoch)jointNotice={id,error};}
  finally{if(revision===epoch)epoch++;centeringId=null;refresh();}
}
async function startImu(){
  if(imuBusy()||imuBlockReason())return;
  const revision=++epoch;
  imuError=null;
  imuRequest=post('imu-calibration',{action:'start'});
  refresh();
  try{
    const result=await imuRequest;
    if(revision===epoch)imuState=result;
  }catch(error){if(revision===epoch)imuError=error;}
  finally{if(revision===epoch)epoch++;imuRequest=null;refresh();}
}
async function enter(){
  if(calibrationBusy())return;
  const revision=++epoch;
  sessionBusy=true;fresh=false;active=false;
  jointNotice=null;
  enterRequest=post('cal/enter');
  setNote('entering');
  refresh();
  try{
    await enterRequest;
    if(revision!==epoch)return;
    active=true;
    posePreview=false;
    poseNotice=null;
    held.fill(false);
    imuError=null;
  }catch(error){if(revision===epoch)showError(error);}
  finally{enterRequest=null;if(revision===epoch)sessionBusy=false;refresh();}
}
async function startDefaultPose(){
  if(posePreview||poseBlockReason())return;
  const revision=++epoch;
  sessionBusy=true;
  poseNotice=null;
  poseRequest=post('cal/default-pose');
  refresh();
  try{
    const result=await poseRequest;
    if(revision!==epoch)return;
    if(result.mode!=='hold')throw new Error(t('requestFailed'));
    posePreview=true;active=false;fresh=false;
    held.fill(false);
    setNote('poseStarting');
  }catch(error){if(revision===epoch)poseNotice={error};}
  finally{poseRequest=null;if(revision===epoch)sessionBusy=false;refresh();}
}
async function exit(){
  const revision=++epoch;
  sessionBusy=true;active=false;fresh=false;
  jointNotice=null;
  if(unifiedBusy)unifiedNotice=null;
  refresh();
  try{
    // Send the release after any pending request to enter, start IMU, or arm the pose.
    if(enterRequest)await enterRequest.catch(()=>{});
    if(imuRequest)await imuRequest.catch(()=>{});
    if(poseRequest)await poseRequest.catch(()=>{});
    if(revision!==epoch)return;
    await post('cal/exit');
    if(revision!==epoch)return;
    held.fill(false);
    imuState=null;imuError=null;posePreview=false;poseNotice=null;
    setNote('closed');
  }catch(error){if(revision===epoch)showError(error);}
  finally{if(revision===epoch)sessionBusy=false;refresh();}
}

document.addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button||button.disabled)return;
  if(button.dataset.picture){
    const image=$('picture-image');
    image.src=button.dataset.picture;
    image.alt=button.querySelector('img')?.alt||t('pictureOpen');
    $('picture-dialog').showModal();
  }else if(button.dataset.action==='unload')enter();
  else if(button.dataset.action==='exit')exit();
});
$('exit').onclick=exit;
$('complete-calibration').onclick=calibrateUnified;
$('neck-start').onclick=()=>calibrateJoint(30);
$('single-toggle').onclick=toggleSingleGuide;
$('single-id').onchange=()=>{
  selectedId=Number($('single-id').value);
  renderSinglePictures();
  refresh();
};
$('single-start').onclick=()=>{if(guide&&!$('single-panel').hidden)calibrateJoint(selectedId);};
$('imu-start').onclick=startImu;
$('pose-start').onclick=startDefaultPose;
function updateLanguage(){language=panelLanguage();renderAll();}
$('language').addEventListener('click',updateLanguage);
$('picture-close').onclick=()=>{$('picture-dialog').close();};
$('picture-dialog').onclick=event=>{if(event.target===$('picture-dialog'))$('picture-dialog').close();};
window.addEventListener('languagechange',updateLanguage);
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/cal/exit',new Blob(['{}'],{type:'application/json'})));
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(active||sessionBusy||posePreview))exit();});

setInterval(async()=>{
  if(statusBusy||sessionBusy||document.hidden)return;
  const revision=epoch;
  statusBusy=true;
  try{
    let poseEnded=false;
    if(posePreview){
      await post('cal/heartbeat',{default_pose:true}).catch(error=>{
        poseEnded=error.message==='Default pose test has ended.';
      });
    }else if(active)await post('cal/heartbeat').catch(()=>{});
    const s=await readJSON('/api/status');
    if(revision!==epoch||sessionBusy)return;
    const wasActive=active;
    robotStatus=s;
    active=s.mode==='calibrate';
    if(active&&unifiedBusy&&s.sitting_calibration?.retry>0){
      unifiedNotice={key:'unifiedRetry',values:s.sitting_calibration};
    }
    presentMask=s.raw_mask||0;
    held=s.cal_done||Array(MOTOR_COUNT).fill(false);
    imuState=s.imu_calibration||null;
    fresh=true;
    if(note.key==='entering'&&feedbackReady()&&active&&s.mcu_calibrating&&!calibrationHolding())setNote('unloaded');
    if(note.key==='connectionLost')setNote('feedbackRestored');
    if(wasActive&&!active&&!unifiedBusy)setNote('closed');
    if(posePreview&&(poseEnded||s.mode!=='hold')){
      posePreview=false;
      poseNotice={key:s.arm_failed?'poseArmFailed':'poseEnded'};
      setNote(poseNotice.key);
    }else if(posePreview&&servosEnabled()&&s.full_robot_ready&&note.key!=='poseHolding'){
      setNote('poseHolding');
    }
    if(s.last_error)showError(new Error(s.last_error));
    refresh();
  }catch(error){
    if(revision===epoch){fresh=false;presentMask=0;setNote('connectionLost');refresh();}
  }finally{statusBusy=false;}
},250);

renderAll();
if(!document.hidden)enter();
