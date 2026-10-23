// Simplified Chinese for the web console. English in the HTML and JS is the source text:
// t('Text {0}',x) returns it unchanged in English, or its entry below in Chinese.
(()=>{
const ZH=new Map(Object.entries({
  // Header and shared
  'Control':'控制','Calibration':'标定','Servo setup':'舵机设置','Torque off':'关闭力矩','Exit':'退出',
  'request failed':'请求失败','Request failed':'请求失败','status read failed':'状态读取失败',
  // Control page
  'Connecting…':'正在连接…','Feedback':'反馈','Policy':'策略','Servos online':'在线舵机','online':'在线','offline':'离线',
  'Mode':'模式','Inference only':'仅推理','Default pose':'默认姿态','Walk / get up':'行走 / 起身','Pick':'啄食','Roulade':'翻滚',
  'Roller mode':'轮滑模式','On':'开','Off':'关','Mode updated':'模式已更新',
  'Enable Roller mode to use the roller walking model during Walk / get up. Recovery uses the get-up model. Commands always cover all 15 IDs. A missing ID is shown here and does not block the others. Default pose and walk send position targets. Leaving this page or keeping it in the background zeros motion and preserves the selected control mode. Use Torque off or enter Calibration to unload. Pick runs only while walking upright: it plays xgoduck_pick for one 4 s cycle, then returns to walk. Roulade plays xgoduck_roulade for 1.9 s with twist and head at 0, and leg/neck smoothing at 0.15, then returns to walk and the previous smoothing. Tilt does not select get-up during those 1.9 s.':
    '开启轮滑模式后，“行走 / 起身”使用轮滑行走模型；摔倒恢复仍用起身模型。指令始终覆盖全部 15 个 ID，缺失的 ID 会在这里显示，但不影响其他舵机。默认姿态和行走发送位置目标。离开本页或切到后台会清零运动，但保留当前控制模式；要卸力请点“关闭力矩”或进入标定页。啄食只在直立行走时可用：播放 xgoduck_pick 一个 4 s 周期后回到行走。翻滚播放 xgoduck_roulade 1.9 s，期间运动与头部指令为 0，腿部/颈部平滑系数为 0.15，结束后回到行走并恢复原平滑设置。这 1.9 s 内倾斜不会触发起身。',
  'inference only':'仅推理','torque off':'力矩关闭','default pose':'默认姿态','walk':'行走','calibrate':'标定','servo setup':'舵机设置',
  '{0} · enabled':'{0} · 力矩开启','{0} · torque off':'{0} · 力矩关闭',
  'Roller mode on · {0}':'轮滑模式已开启 · {0}','Roller mode off · {0}':'轮滑模式已关闭 · {0}',
  'Pick started · {0}':'啄食已开始 · {0}','Roulade · {0}':'翻滚 · {0}',
  'Bridge {0} Mbps · state age {1} ms':'Bridge {0} Mbps · 状态延迟 {1} ms',
  'Walk and recovery':'行走与恢复','Active model':'当前模型','Tilt from (0, 0, -1)':'相对 (0, 0, -1) 的倾角','Condition timer':'条件计时',
  'Walk: tilt above 55° for 0.15 s selects the get-up model, holds action = 0 for 1 s, then runs the get-up action. Get-up: tilt below 15° for 1 s returns to walk. Twist and head commands are zero during get-up. Inference-only mode previews the switch without enabling torque. During pick the command is a phase encoding; the mouth opens to 30° and closes when φ ≥ 0.4.':
    '行走时：倾角超过 55° 持续 0.15 s 即切换到起身模型，先保持 action = 0 1 s，再执行起身动作。起身时：倾角低于 15° 持续 1 s 即回到行走。起身期间运动与头部指令为零。仅推理模式只预览切换，不开启力矩。啄食期间指令为相位编码；嘴张开到 30°，φ ≥ 0.4 时闭合。',
  'idle':'空闲','get-up':'起身','get-up · action = 0':'起身 · action = 0','pick':'啄食','roulade':'翻滚',
  'roulade {0} / {1} s':'翻滚 {0} / {1} s','pick φ {0} / 1.00':'啄食 φ {0} / 1.00','hold {0} s left':'保持中 · 剩余 {0} s',
  'upright {0} / 1.00 s':'直立 {0} / 1.00 s','fallen {0} / 0.15 s':'倾倒 {0} / 0.15 s',
  'Motion':'运动','Warning:':'注意：',
  'Holding a toe-out stance for long periods heats up the roll motors. Take regular breaks to let them cool down.':'长时间保持外八站姿会让横滚电机发热，请定时休息，让电机降温。',
  'Forward / strafe':'前进 / 横移','Forward / backward':'前进 / 后退','Yaw':'转向','Zero motion':'停止移动',
  'Drag a pad; release returns it to zero. Each axis has a 15% center dead zone and motion smoothing.':'拖动摇杆，松开即回零。每个轴都有 15% 的中心死区和运动平滑。',
  'Limits: vx -0.2 to +0.4, vy ±0.3, ωz ±1.0.':'范围：vx -0.2 至 +0.4，vy ±0.3，ωz ±1.0。',
  'Limits: vx -{0} to +0.4, vy {1}, ωz ±{2}.':'范围：vx -{0} 至 +0.4，vy {1}，ωz ±{2}。',
  'Ranges follow Roller mode; switching modes clears motion. Keys: W/S forward, A/D strafe, Q/E yaw. Space turns torque off.':'范围随轮滑模式变化，切换模式会清零运动。键盘：W/S 前进后退，A/D 横移，Q/E 转向，空格键关闭力矩。',
  'Head and mouth':'头部和嘴','head {0}':'头部 {0}','Mouth 34 (°)':'嘴 34 (°)',
  'Head 1–4 are radians (±1). The mouth is motor 34, 0–30°, and is sent with the 50 ms command heartbeat. It is not part of the policy observation.':'头部 1–4 的单位是弧度（±1）。嘴是 34 号电机，范围 0–30°，随 50 ms 指令心跳发送，不属于策略观测。',
  'Action settings ·':'动作参数 ·','Walking / other models':'行走 / 其他模型','Roller walking':'轮滑行走',
  'Action scale':'动作幅度','Leg α':'腿部 α','Head α':'头部 α',
  'Adjust separate settings for roller walking and other models. Roller defaults: scale 0.7, leg α 0.4, head α 0.5. Other defaults: scale 1, leg α 0.45, head α 0.2. Scale changes action offsets from the home pose. Release a slider to apply; restarting the app restores each group\'s defaults.':
    '轮滑行走和其他模型各有一组参数。轮滑默认：幅度 0.7，腿部 α 0.4，头部 α 0.5；其他默认：幅度 1，腿部 α 0.45，头部 α 0.2。幅度缩放相对初始姿态的动作偏移。松开滑块即生效，重启应用后各组恢复默认值。',
  'IMU filtering':'IMU 滤波','Angular velocity α':'角速度 α','Gravity projection τ (s)':'重力投影 τ (s)',
  'Higher angular velocity α gives more smoothing; higher gravity τ slows accelerometer correction. Changes apply immediately. Restarting the app restores defaults.':'角速度 α 越大越平滑；重力 τ 越大，加速度计校正越慢。修改立即生效，重启应用后恢复默认值。',
  'IMU and servos':'IMU 与舵机','Target':'目标','Waiting for command echo…':'等待指令回显…',
  'sent {0} · MCU echo {1} · command age {2} · gaps {3} · rejected {4}':'已发送 {0} · MCU 回显 {1} · 指令延迟 {2} · 丢帧 {3} · 拒收 {4}',
  // Calibration page
  'Servo centering · XGO Duck':'舵机中位标定 · XGO Duck','Servo centering':'舵机中位标定',
  'Entering calibration and unloading all servos…':'正在进入标定，所有舵机卸力…','Procedure':'操作步骤','Unload all':'全部卸力',
  'Opening this page stops motion and unloads every servo. Turn one joint by hand to its mechanical center, then press Set center & hold. Only that servo will hold position 2048; the others remain unloaded. Repeat for each joint. Use Unload all to release the centered joints for another adjustment. Exiting releases every servo.':
    '打开本页会停止运动并让所有舵机卸力。用手把一个关节转到中位，然后点“设为中位并保持”。只有该舵机会保持在 2048 位置，其余舵机保持卸力。每个关节重复一遍。点“全部卸力”可松开已设好中位的关节，重新调整。退出时所有舵机都会卸力。',
  'Joints':'关节','Waiting for feedback':'等待反馈','Unloaded':'已卸力','Set center & hold':'设为中位并保持',
  'Centering…':'正在设中位…','Holding 2048':'保持 2048','Encoder {0}':'编码器 {0}','No servo feedback':'无舵机反馈',
  'Setting the center of ID {0}…':'正在设置 ID {0} 的中位…','Center command sent to ID {0}. It is holding position 2048.':'中位指令已发送到 ID {0}，舵机正保持在 2048 位置。',
  'All servos are unloaded. Turn one joint by hand, then set its center.':'所有舵机已卸力。用手转动一个关节，然后设置它的中位。',
  'Calibration closed. All servos are unloaded.':'标定已关闭，所有舵机已卸力。','Calibration active':'标定中','Calibration closed':'标定已关闭',
  // Servo setup page
  'Servo setup · XGO Duck':'舵机设置 · XGO Duck',
  'When changing an ID, connect only the servo you want to change. Duplicate IDs cannot be distinguished on the same bus.':'修改 ID 时，总线上只接要修改的那个舵机。同一总线上的重复 ID 无法区分。',
  'Scan for servos, then select an ID to read or change its settings.':'先扫描舵机，再选择一个 ID 读取或修改它的设置。',
  'Scan all IDs':'扫描所有 ID','Stop scan':'停止扫描','Scan IDs 0–253 to find connected servos.':'扫描 ID 0–253，查找已连接的舵机。',
  'Servo scan progress':'舵机扫描进度','Discovered servo IDs':'已发现的舵机 ID',
  'Select a found ID to read or change that servo\'s settings. Stop scan keeps the results found so far. IDs 254 and 255 are reserved.':'选择找到的 ID 即可读取或修改该舵机的设置。停止扫描会保留已找到的结果。ID 254 和 255 为保留 ID。',
  'Reset Mid':'重设中位','Set KP/KD':'KP/KD 参数','Set KpKd':'设置 KpKd','Change ID to':'修改 ID 为','New ID':'新 ID','Change ID':'修改 ID','Read':'读取',
  'All-servo voltage / overcurrent protection':'全部舵机电压 / 过流保护','Enable':'启用','Disable':'禁用','Not applied':'未应用',
  'Protection current in amperes':'保护电流（安培）','Set protection current':'设置保护电流',
  'Enable / Disable changes only the protection switch. Set protection current changes only the current threshold. Both operations broadcast to every servo; no scan or ID selection is needed. Overheat protection stays enabled, and stored voltage limits are unchanged. Current resolution is 6.5 mA. Broadcast writes have no per-servo acknowledgement.':
    '“启用 / 禁用”只改变保护开关，“设置保护电流”只改变电流阈值。两者都会广播到所有舵机，无需扫描或选择 ID。过热保护始终开启，已保存的电压限值不变。电流分辨率为 6.5 mA。广播写入没有逐个舵机的确认。',
  'Selected ID {0}':'已选择 ID {0}','ID must be between 0 and 253':'ID 必须在 0 到 253 之间',
  'No MCU feedback. Servo setup is not ready.':'没有 MCU 反馈，舵机设置未就绪。','Stopping scan…':'正在停止扫描…','Servo setup closed':'舵机设置已关闭',
  'Changing ID {0} to {1}…':'正在把 ID {0} 改为 {1}…','ID {0} · Pos {1} · KP {2} · KD {3}':'ID {0} · 位置 {1} · KP {2} · KD {3}',
  'ID {0} · parameters read':'ID {0} · 参数已读取','ID {0} · Mid reset sent. Servo remains unloaded.':'ID {0} · 已发送重设中位，舵机保持卸力。',
  'ID changed. Scan again to select the updated servo.':'ID 已修改，请重新扫描并选择该舵机。','ID {0} → {1} · verified':'ID {0} → {1} · 已验证','ID {0} · KP/KD written':'ID {0} · KP/KD 已写入',
  'Preparing scan…':'正在准备扫描…','Deploy the updated project to enable servo setup.':'请部署更新后的项目以启用舵机设置。','Scanning · torque off':'正在扫描 · 力矩已关闭',
  'Scanning ID {0} · {1}/254 checked · {2} found':'正在扫描 ID {0} · 已检查 {1}/254 · 找到 {2} 个','Invalid scan reply':'扫描回复无效',
  'Scan complete · 254 IDs checked · {0} found':'扫描完成 · 已检查 254 个 ID · 找到 {0} 个',
  'Select a found ID to read or change its settings.':'选择找到的 ID 以读取或修改它的设置。',
  'No servos replied. Check servo power, bus connection and baud rate.':'没有舵机回复。请检查舵机电源、总线连接和波特率。',
  'Stopped · {0}/254 checked · {1} found (partial results)':'已停止 · 已检查 {0}/254 · 找到 {1} 个（部分结果）',
  'Scan failed · {0}/254 checked · {1} found (partial results)':'扫描失败 · 已检查 {0}/254 · 找到 {1} 个（部分结果）',
  'Scan stopped. Select a found ID to continue.':'扫描已停止，选择找到的 ID 继续。','No MCU reply. Scan stopped.':'MCU 无回复，扫描已停止。',
  'Enter a protection current between 0 and 425.9775 A.':'请输入 0 到 425.9775 A 之间的保护电流。',
  'Broadcasting protection settings to all servos…':'正在向所有舵机广播保护设置…','Invalid broadcast reply':'广播回复无效',
  'Broadcast sent · {0} A':'已广播 · {0} A','Broadcast sent · voltage / overcurrent on':'已广播 · 电压 / 过流保护开启','Broadcast sent · voltage / overcurrent off':'已广播 · 电压 / 过流保护关闭',
  'Protection broadcast sent. Individual servo writes are not verified.':'保护设置已广播，无法逐个确认舵机是否写入。'
}));
const EN=new Map([...ZH].map(([en,zh])=>[zh,en])), made=new Map(), seen=new WeakMap(), KEY='xgoduck-lang';
let lang=null;
try{lang=localStorage.getItem(KEY);}catch(_){}
if(lang!=='zh'&&lang!=='en')lang=/^zh/i.test(navigator.language||'')?'zh':'en';
function t(text,...args){
  let s=lang==='zh'&&ZH.has(text)?ZH.get(text):text;
  if(args.length)s=s.replace(/\{(\d)\}/g,(_,i)=>args[i]);
  if(args.length||ZH.has(text)){made.delete(s);made.set(s,[text,args]);if(made.size>1000)made.delete(made.keys().next().value);}
  return s;
}
// Text on the page (in either language) -> [source text, args]; the latest t() output wins.
function source(s){return made.get(s)||(ZH.has(s)?[s,[]]:EN.has(s)?[EN.get(s),[]]:null);}
function render([text,args]){return t(text,...args.map(a=>{const src=typeof a==='string'&&source(a);return src?render(src):a;}));}
// Page scripts replace whole text nodes, so each node keeps the source it was first seen with.
function remember(key,s){if(!seen.has(key))seen.set(key,s&&source(s));return seen.get(key);}
function apply(){
  document.documentElement.lang=lang==='zh'?'zh-CN':'en';
  const walk=document.createTreeWalker(document.documentElement,NodeFilter.SHOW_TEXT);
  for(let n;(n=walk.nextNode());){
    if(/^(SCRIPT|STYLE)$/.test(n.parentNode.nodeName))continue;
    const [,pre,text,post]=n.nodeValue.match(/^(\s*)([\s\S]*?)(\s*)$/),src=remember(n,text);
    const out=src&&render(src);
    if(out&&out!==text)n.nodeValue=pre+out+post;
  }
  document.querySelectorAll('[title],[aria-label]').forEach(el=>{
    if(!seen.has(el))seen.set(el,{});
    for(const a of ['title','aria-label']){
      const v=el.getAttribute(a),attrs=seen.get(el);
      if(!(a in attrs))attrs[a]=v&&source(v);
      const out=attrs[a]&&render(attrs[a]);
      if(out&&out!==v)el.setAttribute(a,out);
    }
  });
}
window.t=t;
apply();
document.querySelectorAll('.lang-toggle').forEach(b=>b.onclick=()=>{
  lang=lang==='zh'?'en':'zh';
  try{localStorage.setItem(KEY,lang);}catch(_){}
  apply();
});
})();
