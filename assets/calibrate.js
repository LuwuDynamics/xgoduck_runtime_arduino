const $=id=>document.getElementById(id);
const MOTORS=[10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
// Used when calibrate-joints.json cannot be loaded: the buttons still work, just without photos and pictures.
const FALLBACK_ZONES=[
  {key:'head',ids:[30,31,32,33,34],title:{en:'Head & neck',zh:'头颈'}},
  {key:'left',ids:[10,11,12,13,14],title:{en:'Left leg',zh:'左腿'}},
  {key:'right',ids:[20,21,22,23,24],title:{en:'Right leg',zh:'右腿'}}
];
let statusBusy=false, active=false, centering=null, presentMask=0, polled=false, built=false;
let sessionBusy=false, fresh=false, epoch=0, guideUnavailable=false;
let held=Array(15).fill(false);
let rawPos=[];
let errors={};
let imuState=null, imuRequest=null, imuError=null;
let imuFeedbackReady=false, servosEnabled=false;
let guide=null;
let note={key:'entering'};

const strings={
  en:{
    docTitle:'Calibration · XGO Duck',title:'Calibration',languageButton:'中文',switchLanguage:'切换为中文',
    navControl:'Control',navCalibration:'Calibration',navServo:'Servo setup',
    imuTitle:'IMU calibration',imuStart:'Calibrate IMU',
    imuInstructions:'Support Duck with the board upright and its battery connector facing up. Rest the board’s bottom edge on a level table, then calibrate and hold still for about 1.5 seconds.',
    imuPoseAlt:'Board upright with the battery connector pointing up',imuZoom:'Enlarge IMU posture guide',
    imuWaiting:'Waiting for IMU feedback…',imuUnavailable:'IMU calibration is unavailable on this App version.',
    imuNoFeedback:'Check the IMU connection and wait for feedback.',imuReady:'Ready to calibrate.',
    imuEnterFirst:'Click Unload all to enter calibration.',imuUnloadFirst:'Unload all servos before calibrating the IMU.',
    imuWaitForServo:'Wait for the current servo operation to finish.',
    imuStarting:'Starting IMU calibration…',imuSampling:'Hold still. Sampling {percent}%.',
    imuSaved:'IMU calibration saved.',imuCancelled:'IMU calibration cancelled.',imuFailed:'IMU calibration failed.',
    imuUpright:'Hold the board upright with the battery connector pointing up.',
    imuMovement:'Movement detected. Hold the board still and try again.',
    imuGyroZero:'Gyroscope zero offset is too large. Check the IMU and try again.',
    imuInterrupted:'IMU feedback was interrupted. Check the connection and try again.',
    imuPreviousKept:'The previous calibration is still saved.',
    imuRecalibrate:'The saved IMU calibration is invalid or from an older version. Calibrate again here.',
    exit:'Exit',unloadAll:'Unload all',setCenter:'Set center',
    entering:'Entering calibration and unloading all servos…',
    unloaded:'All servos are unloaded. Turn one joint by hand, then set its center.',
    centeringMsg:'Setting the center of ID {id}…',centeredMsg:'The center of ID {id} has been set.',
    allDone:'All 15 servos are set. Click Unload all, then Exit.',
    closed:'Calibration closed. All servos are unloaded.',
    stateUnloaded:'Not set this session',stateCentering:'Centering…',stateCentered:'✓ Center set',
    waiting:'Waiting for feedback',encoder:'Encoder {value}',noFeedback:'No servo feedback',
    modeActive:'Calibration active',modeClosed:'Calibration closed',
    introTitle:'Set the center of all 15 servos',
    steps:[
      'Rest Duck securely on a table, or place it in a calibration stand. Opening this page unloads every servo. Duck does not need to stand or bear its own weight.',
      'Find the servo in the photo by its white ID tag. Turn that joint by hand until it matches its picture.',
      'Click Set center to record the current angle as that servo’s center. Then position and set the next joint.',
      'After all 15, click Unload all, then Exit. To redo a servo, click Unload all and set it again.'
    ],
    zeroNote:'The zero pose cannot stand up. That is normal: this step only calibrates the servos. Stand Duck up later from Control.',
    savedNote:'Centers are saved in the servos. Center set confirms calibration, not torque status. Progress counts centers set in this session; unloading or reopening resets the count but does not erase saved centers.',
    legendPart:'Orange part / circle: the joint to adjust',legendLine:'Green dashed line: line the part up with it',
    legendWrong:'Red part and arrow: a wrong position, turn it back the way the arrow shows',
    progress:'Set this session {done} / {total}',zoneIds:'IDs {first}–{last}',legPerspective:' (robot’s perspective)',
    photoAlt:'Photo of the servos, each marked with its white ID tag',photoOpen:'Open the full-size photo',poseAlt:'ID {id} {name}: zero pose',
    finishTitle:'Finished?',finishText:'Click Unload all, then Exit. After that, go back to Control to stand Duck up.',
    credits:'Photos: the official XGO Duck developer guide. Zero-pose pictures: rendered from the XGO Duck URDF model.',
    guideLink:'Open the official calibration guide ↗',pictureOpen:'Enlarge picture',pictureClose:'Close picture',
    guideUnavailable:'Picture guide unavailable. You can still set centers using the official guide linked below.',
    connectionLost:'Cannot read Duck’s status. Check the connection before setting a center.',
    feedbackRestored:'Feedback restored. Continue using the joint states shown below.',
    requestFailed:'The request failed. Check the connection and try again.',
    invalidReply:'Duck did not confirm the selected servo. Read its status before retrying.',
    notCalibrating:'Calibration is closed. Click Unload all to enter again.',
    staleFeedback:'Servo feedback is stale. Check servo power and the connection.',
    centerTimeout:'No centering confirmation. Check this servo’s feedback before retrying.',
    noServo:'ID {id} did not reply. Check its power, cable and ID.',
    sessionChanged:'Calibration was closed or changed. Enter again before setting a center.'
  },
  zh:{
    docTitle:'标定 · XGO Duck',title:'舵机与 IMU 标定',languageButton:'EN',switchLanguage:'Switch to English',
    navControl:'控制',navCalibration:'标定',navServo:'舵机设置',
    imuTitle:'IMU 标定',imuStart:'标定 IMU',
    imuInstructions:'按图扶稳小鸭，让板子的电池接口朝上、下边缘贴在水平桌面上。点击标定后，保持静止约 1.5 秒。',
    imuPoseAlt:'板子直立，电池接口朝上，下边缘贴在桌面上',imuZoom:'放大 IMU 摆放图',
    imuWaiting:'等待 IMU 反馈…',imuUnavailable:'当前应用版本不支持 IMU 标定。',
    imuNoFeedback:'请检查 IMU 连接并等待反馈。',imuReady:'可以开始标定。',
    imuEnterFirst:'请先点「全部卸力」进入标定。',imuUnloadFirst:'请先点「全部卸力」，再标定 IMU。',
    imuWaitForServo:'请等待当前舵机操作完成。',
    imuStarting:'正在启动 IMU 标定…',imuSampling:'请保持静止，已采样 {percent}%。',
    imuSaved:'IMU 标定已保存。',imuCancelled:'IMU 标定已取消。',imuFailed:'IMU 标定失败。',
    imuUpright:'请让板子直立，电池接口朝上。',imuMovement:'检测到晃动，请保持板子静止后重试。',
    imuGyroZero:'陀螺仪零偏过大，请检查 IMU 后重试。',
    imuInterrupted:'IMU 反馈中断，请检查连接后重试。',imuPreviousKept:'上一次标定结果已保留。',
    imuRecalibrate:'IMU 标定记录无效或为旧版，请在此重新标定。',
    exit:'Exit',unloadAll:'Unload all',setCenter:'Set center',
    gloss:{exit:'退出',unloadAll:'全部卸力',setCenter:'设置中位'},
    entering:'正在进入标定，所有舵机卸力中…',
    unloaded:'所有舵机已卸力。先用手把一个关节摆到图示位置，再设定它的中位。',
    centeringMsg:'正在设定 ID {id} 的中位…',centeredMsg:'ID {id} 已设中位。',
    allDone:'15 个舵机都已设好。先点 Unload all，再点 Exit。',
    closed:'标定已关闭，所有舵机已卸力。',
    stateUnloaded:'本轮未设',stateCentering:'设定中…',stateCentered:'✓ 已设中位',
    waiting:'等待反馈',encoder:'编码器 {value}',noFeedback:'无舵机反馈',
    modeActive:'标定中',modeClosed:'标定已关闭',
    introTitle:'逐个设定 15 个舵机的中位',
    steps:[
      '把小鸭安放在桌面上，或固定在标定架中。打开本页后所有舵机卸力；不需要让小鸭站立承重。',
      '在照片里按白色编号牌找到这个舵机，用手把这个关节摆成它图里的样子。',
      '点 Set center，将当前角度记录为这个舵机的中位。然后摆放并标定下一个关节。',
      '15 个都做完后，先点 Unload all，再点 Exit。想重做某个舵机：先点 Unload all，再重新设定。'
    ],
    zeroNote:'零位姿态下 Duck 站不起来，这是正常的：这一步只标定舵机。标定完成后再回到 Control 页让它站起来。',
    savedNote:'中位保存在舵机内。“已设中位”表示标定成功，不表示已确认上力。进度统计本轮已设中位的关节；卸力或重新打开页面后计数归零，不清除已保存的中位。',
    legendPart:'橙黄色部件／圆圈：要调整的关节',legendLine:'绿色虚线：把部件对齐到这里',
    legendWrong:'红色部件和箭头：错误的位置，按箭头方向转回来',
    progress:'本轮已设 {done} / {total}',zoneIds:'ID {first}–{last}',legPerspective:'（机器人的视角）',
    photoAlt:'舵机实物照片，每个舵机贴有白色编号牌',photoOpen:'打开原尺寸照片',poseAlt:'ID {id} {name}：零位姿态',
    finishTitle:'都做完了？',finishText:'先点 Unload all，再点 Exit。之后回到 Control 页让 Duck 站起来。',
    credits:'实物照片：XGO Duck 官方开发者指南。零位姿态图：由 XGO Duck 的 URDF 模型渲染。',
    guideLink:'打开官方标定指南 ↗',pictureOpen:'放大图片',pictureClose:'关闭大图',
    guideUnavailable:'图示暂不可用，仍可对照下方官方指南设置中位。',
    connectionLost:'暂时读不到小鸭状态，请检查连接后再设置中位。',
    feedbackRestored:'反馈已恢复，请按下方各关节的当前状态继续操作。',
    requestFailed:'请求未完成，请检查连接后重试。',
    invalidReply:'小鸭尚未确认所选舵机的中位，请先核对状态再重试。',
    notCalibrating:'标定已关闭，点 Unload all 可重新进入。',
    staleFeedback:'舵机反馈已过期，请检查舵机供电和连接。',
    centerTimeout:'尚未收到中位设置确认，请检查该舵机反馈后重试。',
    noServo:'ID {id} 没有回复，请检查供电、接线和编号。',
    sessionChanged:'标定已关闭或切换，请重新进入后再设置中位。'
  }
};
// Same rule as the cockpit: Chinese browsers get Chinese, everything else English; the toggle only lasts for this page.
const systemLanguage=()=>/^zh(?:[-_]|$)/i.test(navigator.languages?.[0]||navigator.language||'en')?'zh':'en';
let language=systemLanguage();
const t=(key,values={})=>String(strings[language][key]??strings.en[key]??key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''));
const list=key=>strings[language][key]||strings.en[key]||[];
const pick=value=>value?.[language]||value?.en||'';
const label=key=>[h('span',{text:t(key)}),strings[language].gloss?.[key]&&h('small',{text:strings[language].gloss[key]})];

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
async function post(path, body={}){
  return readJSON('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
}
function renderMessage(){$('message').textContent=note.text??t(note.key,note.values);}
function setNote(key,values){note={key,values};renderMessage();}
function errorText(e){
  const message=e.message||String(e);
  const id=message.match(/No servo replied at ID (\d+)/)?.[1];
  if(id)return t('noServo',{id});
  const known={'not in calibrate mode':'notCalibrating','MCU feedback is stale':'staleFeedback',
    'centering acknowledgement timeout':'centerTimeout','calibration session changed':'sessionChanged',
    'Unload all joints before calibrating the IMU.':'imuUnloadFirst',
    'Hold the board upright with the battery connector pointing up.':'imuUpright',
    'Movement detected. Hold the board still and try again.':'imuMovement',
    'Gyroscope zero offset is too large. Check the IMU and try again.':'imuGyroZero',
    'IMU readings were interrupted. Try again.':'imuInterrupted',
    'IMU readings are stale. Check the connection and try again.':'imuInterrupted',
    'Calibration could not be loaded. Calibrate again or reset it.':'imuRecalibrate',
    'IMU 标定文件无效或为旧版。请在 9527 的 Control 页重新标定 IMU。':'imuRecalibrate'};
  if(known[message])return t(known[message]);
  if(e.name==='AbortError'||e instanceof TypeError||e instanceof SyntaxError)return t('requestFailed');
  return message;
}
function showError(e){note={text:errorText(e)};renderMessage();}

const zones=()=>guide?.zones||FALLBACK_ZONES;
const joint=id=>guide?.joints?.[id]||{};
const index=id=>MOTORS.indexOf(id);

function legend(kind,text){return h('li',{},h('span',{class:'cal-swatch '+kind,'aria-hidden':'true'}),text);}
function renderIntro(){
  $('intro').replaceChildren(
    h('h2',{},t('introTitle'),h('span',{id:'mode'}),h('span',{id:'total',class:'cal-count'})),
    h('ol',{class:'cal-steps'},list('steps').map(step=>h('li',{text:step}))),
    h('p',{class:'warn',text:t('zeroNote')}),
    h('p',{class:'hint',text:t('savedNote')}),
    ...(guideUnavailable?[h('p',{class:'warn',text:t('guideUnavailable')})]:[]),
    h('div',{class:'buttons'},h('button',{type:'button','data-action':'unload'},label('unloadAll')))
  );
}
function renderBar(){
  $('zonebar').replaceChildren(
    h('nav',{class:'cal-chips'},zones().map(zone=>h('a',{class:'cal-chip',href:'#zone-'+zone.key},
      h('span',{text:pick(zone.title)||zone.key}),h('b',{id:'chip-'+zone.key})))),
    h('button',{type:'button','data-action':'unload'},label('unloadAll')),
    h('button',{type:'button','data-action':'exit',class:'stop'},label('exit'))
  );
}
function photoFigure(zone,photo){
  return h('figure',{class:'cal-photo'},
    h('button',{type:'button',class:'cal-enlarge','data-picture':photo.src,'aria-label':t('photoOpen')},
      h('img',{src:photo.src,width:photo.width,height:photo.height,alt:t('photoAlt')})),
    (photo.labels||[]).map(label=>h('span',{class:'cal-photo-label',style:`left:${label.x*100}%;top:${label.y*100}%`,text:pick(label)})),
    (zone.focus==='left'||zone.focus==='right')&&h('span',{class:'cal-dim '+(zone.focus==='left'?'right':'left'),'aria-hidden':'true'})
  );
}
function card(id){
  const i=index(id), info=joint(id), name=pick(info.name);
  return h('article',{class:'cal-card',id:'row'+i},
    h('div',{class:'cal-card-head'},
      h('span',{class:'cal-tag','aria-label':'ID '+id,text:String(id)}),
      name&&h('b',{text:name}),
      h('span',{class:'cal-state',id:'state'+i})),
    info.image&&h('button',{type:'button',class:'cal-enlarge','data-picture':info.image,'aria-label':t('pictureOpen')+' · ID '+id},
      h('img',{class:'cal-pose',src:info.image,width:340,height:330,loading:'lazy',alt:t('poseAlt',{id,name})})),
    info.pose&&h('p',{class:'cal-how',text:pick(info.pose)}),
    h('p',{class:'cal-error',id:'err'+i,role:'alert',hidden:true}),
    h('div',{class:'cal-card-foot'},
      h('button',{type:'button','data-center':i,disabled:true},label('setCenter')),
      h('span',{class:'cal-raw',id:'raw'+i}))
  );
}
function renderZones(){
  $('zones').replaceChildren(...zones().map(zone=>{
    const photo=guide?.photos?.[zone.photo];
    const wide=photo&&photo.width/photo.height>1.6;
    const title=pick(zone.title)||zone.key;
    const isLeg=zone.key==='left'||zone.key==='right';
    return h('section',{class:'cal-zone',id:'zone-'+zone.key},
      h('div',{class:'cal-zone-head'},
        h('h2',{text:isLeg?title+t('legPerspective'):title}),
        h('small',{text:t('zoneIds',{first:zone.ids[0],last:zone.ids[zone.ids.length-1]})}),
        h('span',{class:'cal-count',id:'count-'+zone.key})),
      h('div',{class:'cal-top'+(wide?' wide':'')},
        photo&&photoFigure(zone,photo),
        h('div',{class:'cal-side'},
          zone.note&&h('p',{class:'cal-note',text:pick(zone.note)}),
          h('ul',{class:'cal-legend'},legend('part',t('legendPart')),legend('line',t('legendLine')),legend('ghost',t('legendWrong'))))),
      h('div',{class:'cal-cards'},zone.ids.map(card))
    );
  }));
}
function renderFinish(){
  $('finish').replaceChildren(
    h('h2',{text:t('finishTitle')}),
    h('p',{text:t('finishText')}),
    h('div',{class:'buttons'},
      h('button',{type:'button','data-action':'unload'},label('unloadAll')),
      h('button',{type:'button','data-action':'exit'},label('exit'))),
    h('p',{class:'hint'},t('credits')+' ',
      h('a',{href:'https://www.xgorobot.com/duck-developer-guide/07-calibration-control.html',target:'_blank',rel:'noopener',text:t('guideLink')}))
  );
}

function renderRaw(){
  if(!built)return;
  MOTORS.forEach((_,i)=>{
    $(`raw${i}`).textContent=rawPos[i]===undefined?t('waiting'):presentMask&(1<<i)?t('encoder',{value:rawPos[i]}):t('noFeedback');
  });
}
function imuBusy() {
  return Boolean(imuRequest || imuState?.active);
}

function imuBlockReason() {
  if (!fresh) return 'imuWaiting';
  if (!imuState) return 'imuUnavailable';
  if (!imuFeedbackReady) return 'imuNoFeedback';
  if (sessionBusy || centering !== null) return 'imuWaitForServo';
  if (!active) return 'imuEnterFirst';
  if (servosEnabled || held.some(Boolean)) return 'imuUnloadFirst';
  return '';
}

function refreshImu() {
  const blocked = imuBlockReason();
  const sampling = fresh && Boolean(imuState?.active);
  $('imu-start').disabled = Boolean(blocked) || imuBusy();
  $('imu-progress').hidden = !sampling;
  $('imu-progress').value = imuState?.progress ?? 0;

  let message = t(blocked || 'imuReady');
  let tone = '';
  if (imuRequest) {
    message = t('imuStarting');
  } else if (sampling) {
    message = t('imuSampling', {percent: Math.round($('imu-progress').value * 100)});
  } else if (fresh && imuState?.state === 'cancelled') {
    message = t('imuCancelled');
  } else if (!blocked && (imuError || imuState?.state === 'error')) {
    const failure = imuState?.message?.replace(/^Previous calibration kept\. /, '');
    message = imuError ? errorText(imuError) : failure ? errorText(new Error(failure)) : t('imuFailed');
    if (!imuError && imuState.calibrated) message += ' ' + t('imuPreviousKept');
    tone = 'error';
  } else if (!blocked && imuState?.calibrated) {
    message = t('imuSaved');
    tone = 'saved';
  }
  const status = $('imu-status');
  status.dataset.state = tone;
  if (status.textContent !== message) status.textContent = message;
}

function refreshRows(){
  if(!built)return;
  const imuIsBusy = imuBusy();
  refreshImu();
  document.querySelectorAll('[data-center]').forEach(btn=>{
    const i=+btn.dataset.center;
    btn.disabled=!active||!fresh||sessionBusy||centering!==null||imuIsBusy||held[i]||!(presentMask&(1<<i));
    $(`row${i}`).classList.toggle('done',held[i]);
    $(`state${i}`).textContent=centering===i?t('stateCentering'):!fresh?t('waiting'):held[i]?t('stateCentered'):t('stateUnloaded');
    $(`err${i}`).textContent=errors[i]||'';
    $(`err${i}`).hidden=!errors[i];
  });
  document.querySelectorAll('[data-action=unload]').forEach(btn=>{btn.disabled=sessionBusy||imuIsBusy||centering!==null;});
  let total=0;
  zones().forEach(zone=>{
    const done=zone.ids.filter(id=>held[index(id)]).length;
    total+=done;
    $('count-'+zone.key).textContent=t('progress',{done,total:zone.ids.length});
    $('chip-'+zone.key).textContent=`${done}/${zone.ids.length}`;
    $('chip-'+zone.key).parentElement.classList.toggle('done',done===zone.ids.length);
  });
  $('total').textContent=t('progress',{done:total,total:MOTORS.length});
  $('mode').textContent=!polled?'—':!fresh?t('waiting'):active?t('modeActive'):t('modeClosed');
}
function renderAll(){
  document.documentElement.lang=language==='zh'?'zh-CN':'en';
  document.title=t('docTitle');
  $('title').textContent=t('title');
  $('exit').replaceChildren(...label('exit').filter(Boolean));
  document.querySelectorAll('[data-label]').forEach(el=>{el.textContent=t(el.dataset.label);});
  $('imu-pose').alt = t('imuPoseAlt');
  $('imu-picture').setAttribute('aria-label', t('imuZoom'));
  $('picture-close').textContent=t('pictureClose');
  $('picture-dialog').setAttribute('aria-label',t('pictureOpen'));
  const button=$('language');
  button.textContent=t('languageButton');button.title=t('switchLanguage');button.setAttribute('aria-label',t('switchLanguage'));
  renderIntro();renderBar();renderZones();renderFinish();
  built=true;
  renderMessage();renderRaw();refreshRows();
}

async function startImu() {
  if (imuBusy() || imuBlockReason()) return;
  const revision = ++epoch;
  imuError = null;
  imuRequest = post('imu-calibration', {action: 'start'});
  refreshRows();
  try {
    const result = await imuRequest;
    if (revision === epoch) imuState = result;
  } catch (error) {
    if (revision === epoch) imuError = error;
  } finally {
    if (revision === epoch) epoch++;
    imuRequest = null;
    refreshRows();
  }
}

async function center(i){
  if(!active||!fresh||sessionBusy||centering!==null||imuBusy()||held[i]||!(presentMask&(1<<i)))return;
  const revision=++epoch;
  centering=i;
  delete errors[i];
  refreshRows();
  setNote('centeringMsg',{id:MOTORS[i]});
  try{
    const result=await post('cal/center',{index:i});
    if(revision!==epoch||!active)return;
    if(result.ok!==true||result.index!==i||result.id!==MOTORS[i])throw new Error(t('invalidReply'));
    held[i]=true;
    setNote(held.every(Boolean)?'allDone':'centeredMsg',{id:MOTORS[i]});
  }catch(e){if(revision===epoch){errors[i]=errorText(e);showError(e);}}
  finally{if(revision===epoch)epoch++;centering=null;refreshRows();}
}
async function enter(){
  if(sessionBusy||centering!==null||imuBusy())return false;
  const revision=++epoch;
  sessionBusy=true;fresh=false;active=false;refreshRows();
  try{
    await post('cal/enter');
    if(revision!==epoch)return false;
    active=true;
    held.fill(false);
    errors={};
    imuError=null;
    setNote('unloaded');
    return true;
  }catch(e){if(revision===epoch)showError(e);return false;}
  finally{if(revision===epoch)sessionBusy=false;refreshRows();}
}
async function exit(){
  const revision=++epoch;
  sessionBusy=true;active=false;fresh=false;
  refreshRows();
  try{
    // Exit after an in-flight start so the controller can cancel that sample.
    if (imuRequest) await imuRequest.catch(() => {});
    if (revision !== epoch) return;
    await post('cal/exit');
    if(revision!==epoch)return;
    held.fill(false);
    imuState=null;
    imuError=null;
    setNote('closed');
  }catch(e){if(revision===epoch)showError(e);}
  finally{if(revision===epoch)sessionBusy=false;refreshRows();}
}
document.addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button||button.disabled)return;
  if(button.dataset.picture){
    const image=$('picture-image');
    image.src=button.dataset.picture;image.alt=button.querySelector('img')?.alt||t('pictureOpen');
    $('picture-dialog').showModal();
  }
  else if(button.dataset.center!==undefined)center(+button.dataset.center);
  else if(button.dataset.action==='unload')enter();
  else if(button.dataset.action==='exit')exit();
});
$('exit').onclick=exit;
$('imu-start').onclick=startImu;
$('language').onclick=()=>{language=language==='zh'?'en':'zh';renderAll();};
$('picture-close').onclick=()=>{$('picture-dialog').close();};
$('picture-dialog').onclick=event=>{if(event.target===$('picture-dialog'))$('picture-dialog').close();};
window.addEventListener('languagechange',()=>{language=systemLanguage();renderAll();});
window.addEventListener('pagehide',()=>navigator.sendBeacon('/api/cal/exit',new Blob(['{}'],{type:'application/json'})));
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(active||sessionBusy))exit();});

setInterval(async()=>{
  if(statusBusy||sessionBusy||document.hidden)return;
  const revision=epoch;
  statusBusy=true;
  try{
    if(active)await post('cal/heartbeat').catch(()=>{});
    const s=await readJSON('/api/status');
    if(revision!==epoch||sessionBusy)return;
    const wasActive=active;
    active=s.mode==='calibrate';
    presentMask=s.raw_mask||0;
    held=s.cal_done||Array(15).fill(false);
    imuState=s.imu_calibration||null;
    imuFeedbackReady=Boolean(s.imu_ok && Number.isFinite(s.feedback_age_ms) && s.feedback_age_ms < 150);
    servosEnabled=Boolean(s.enabled);
    polled=true;
    fresh=true;
    if(note.key==='connectionLost')setNote('feedbackRestored');
    if(wasActive&&!active&&centering===null)setNote('closed');
    if(s.last_error)showError(new Error(s.last_error));
    if(s.raw_pos)rawPos=s.raw_pos;
    renderRaw();
    refreshRows();
  }catch(e){if(revision===epoch){fresh=false;presentMask=0;setNote('connectionLost');renderRaw();refreshRows();}}
  finally{statusBusy=false;}
},250);

async function loadGuide(){
  try{
    const data=await readJSON('calibrate-joints.json?v=20261107-2');
    const ids=(data?.zones||[]).flatMap(zone=>zone.ids||[]);
    if(!data?.joints||ids.length!==MOTORS.length||!MOTORS.every(id=>ids.includes(id))||
      !FALLBACK_ZONES.every(zone=>data.zones.some(z=>z.key===zone.key&&JSON.stringify(z.ids)===JSON.stringify(zone.ids)))||
      !MOTORS.every(id=>data.joints[id]?.image&&data.joints[id]?.name?.en&&data.joints[id]?.pose?.en))throw new Error('invalid guide');
    guide=data;
  }catch{guideUnavailable=true;}
}
renderAll();
if(!document.hidden)enter();
loadGuide().then(renderAll);
