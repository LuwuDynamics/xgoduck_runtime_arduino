/* Local cockpit sticks and keyboard. The board expires travel if renewals stop. */
(() => {
  const $ = id => document.getElementById(id);
  const t = window.DuckI18n.t;
  const allowRemote = () => window.DuckRemoteNotice?.allow() !== false;
  const controllerId = Array.from(crypto.getRandomValues(new Uint8Array(12)),
    value => value.toString(16).padStart(2, '0')).join('');
  const actions = [...document.querySelectorAll('[data-motion]')];
  const names = Object.fromEntries(actions.map(button => [button.dataset.motion, button.dataset.i18n]));
  names.torque_off = 'torqueOff';
  const sticks = ['moveStick', 'turnStick'].map(id => {
    const pad = $(id);
    return {pad, knob:pad.querySelector('.stick-knob'), pointer:null, x:0, y:0};
  });
  const driveKeys = new Set(['KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE']);
  const heldKeys = new Set();
  const portraitOrientation = window.matchMedia('(orientation: portrait)');
  const rollerSwitch = $('rollerMode');
  let robot = null, gamepadActive = false, sequence = 0, gesture = null, pending = null, notice = null, noticeTimer;
  let rollerBusy = false, rollerRevision = 0;
  const deadzone = value => Math.abs(value) <= .15 ? 0 : value;

  async function send(kind, twist, rollerRelease = false) {
    const drive = kind === 'begin' || kind === 'drive';
    const response = await fetch('/api/cockpit/motion', {
      method:'POST', headers:{'Content-Type':'application/json', 'X-Duck-Cockpit':'1'},
      body:JSON.stringify({controller_id:controllerId, sequence:++sequence, kind, twist,
        roller_release:rollerRelease || undefined}),
      signal:AbortSignal.timeout(drive ? 350 : 3000),
      keepalive:kind === 'release' || kind === 'torque_off'
    });
    const data = await response.json();
    if (!response.ok || data.ignored) throw Error(data.detail || data.ignored || t('requestFailed'));
    return data;
  }

  function resetStick(stick) {
    const pointer = stick.pointer;
    stick.pointer = null;
    stick.x = stick.y = 0;
    stick.knob.style.transform = '';
    stick.pad.classList.remove('is-held');
    if (pointer !== null && stick.pad.hasPointerCapture(pointer)) stick.pad.releasePointerCapture(pointer);
  }

  function release() {
    const active = gesture !== null;
    clearInterval(gesture?.timer);
    gesture = null;
    heldKeys.clear();
    sticks.forEach(resetStick);
    if (active) send('release').catch(() => {});
  }

  function mode() {
    const exclusive = gamepadActive || robot?.input_source === 'gamepad' || ['calibrate','servo_debug'].includes(robot?.mode);
    const seated = robot?.sitstand?.phase === 'sit';
    const active = ['pick','roulade','left_kick','right_kick'].find(kind => robot?.[kind]?.active)
      || (robot?.sitstand?.phase === 'stand' ? 'stand' : null);
    const usable = robot?.control_ready === true && !exclusive && !robot.battery?.low;
    const upright = usable && robot.enabled === true && !robot.fault_hold && robot.recovery?.phase === 'walk';
    const angle = robot?.recovery?.angle_deg;
    const standAngle = robot?.recovery?.thresholds?.stand_deg ?? 15;
    const fallAngle = robot?.recovery?.thresholds?.fall_deg ?? 55;
    const walkingUpright = upright && robot.mode === 'policy' && Number.isFinite(angle);
    const gravity = robot?.gravity;
    const holdUpright = upright && robot.mode === 'hold' && Array.isArray(gravity) && gravity.length === 3
      && -gravity[2] > Math.cos(standAngle * Math.PI / 180) * Math.hypot(...gravity);
    const free = !seated && !active;
    return {exclusive, seated, active, usable, ready:free && walkingUpright && angle < (gesture ? fallAngle : standAngle),
      canSit:free && !robot?.action_preparation?.active && (holdUpright || walkingUpright && angle < standAngle)};
  }

  function render() {
    const state = mode();
    const roller = robot?.roller?.enabled === true;
    const busy = !!pending || rollerBusy;
    rollerSwitch.setAttribute('aria-checked', String(roller));
    rollerSwitch.textContent = t(rollerBusy ? 'rollerSwitching' : roller ? 'rollerOn' : 'rollerOff');
    rollerSwitch.title = t(state.seated ? 'rollerStandFirst' : 'rollerMode');
    rollerSwitch.disabled = typeof robot?.roller?.enabled !== 'boolean' || !state.usable || state.seated
      || !!state.active || busy || !!robot.arming || !!robot.action_preparation?.active || !!robot.imu_calibration?.active;
    $('moveStickLabel').textContent = t(roller ? 'moveStickRoller' : 'moveStick');
    $('keyboardStrafe').hidden = roller;
    sticks[0].pad.classList.toggle('roller-pad', roller);
    for (const stick of sticks) stick.pad.setAttribute('aria-disabled', String(!state.ready || busy));
    for (const button of actions) {
      const kind = button.dataset.motion;
      const headAction = kind.startsWith('head_');
      button.disabled = kind === 'stop' ? !robot || state.exclusive
        : kind === 'stand' || kind === 'default_pose' ? !state.usable || !!state.active || busy || robot?.arming === true || !!robot?.action_preparation?.active
        : kind === 'sit' ? !state.canSit || busy
        : !state.ready || busy || roller && headAction;
      if (headAction) button.title = roller ? t('poseRoller') : '';
      button.toggleAttribute('data-active', kind === state.active || kind === 'sit' && state.seated);
    }
    const feedback = robot?.battery?.low || state.exclusive ? null : notice;
    const standStarting = pending?.kind === 'stand' || feedback?.kind === 'stand' && feedback.snapshot === robot;
    const armFailed = robot?.arm_failed === true || robot?.mode === 'policy' && robot.enabled === false && !robot.arming;
    const gettingUp = robot?.mode === 'policy' && robot.enabled === true
      && ['default_pose','getup'].includes(robot.recovery?.phase);
    const key = !robot ? 'waitingFeedback' : robot.battery?.low ? 'motionBattery'
      : state.exclusive ? 'motionOtherOwner' : robot.arming ? 'motionArming' : standStarting ? 'motionStarting'
      : rollerBusy ? 'rollerSwitching' : pending ? 'motionBusy' : gesture?.forwardBlocked ? 'motionTof' : gesture?.paused ? 'motionVoiceBusy'
      : !state.usable ? 'waitingFeedback' : armFailed ? 'motionArmFailed'
      : state.active ? 'motionInProgress' : gettingUp ? 'motionGettingUp' : state.seated ? 'motionSeated'
      : state.ready ? 'motionReady' : robot.mode === 'policy' && !robot.fault_hold ? 'motionSettling' : 'motionStandHint';
    const action = pending?.kind || state.active;
    const status = $('motionStatus');
    const needsStandButton = ['motionStandHint', 'motionArmFailed', 'motionSeated'].includes(key);
    const sticksOnly = document.body.dataset.hud === 'sticks' && !document.body.classList.contains('compact-cockpit');
    const statusKey = sticksOnly && needsStandButton ? 'motionShowControls' : key;
    const text = feedback?.text || (feedback?.key ? t(feedback.key) : feedback && feedback.kind !== 'stand' && !state.active && !state.seated
      ? t('motionAccepted', {action:t(names[feedback.kind])})
      : t(statusKey, {action:action ? t(names[action]) : ''}));
    if (status.textContent !== text) status.textContent = text;
    status.title = text;
    status.toggleAttribute('data-warning', !!(feedback?.text || feedback?.key || gesture?.forwardBlocked
      || robot?.battery?.low || key === 'motionArmFailed'));
  }

  function showNotice(value) {
    clearTimeout(noticeTimer);
    notice = value;
    if (value) noticeTimer = setTimeout(() => {notice = null; render();}, 4500);
    render();
  }

  async function switchRoller() {
    if (rollerSwitch.disabled) return;
    const enabled = !robot.roller.enabled;
    release();
    rollerBusy = true;
    rollerRevision++;
    showNotice(null);
    try {
      const response = await fetch('/api/roller', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({enabled}), signal:AbortSignal.timeout(5000)
      });
      if (!response.ok || (await response.json()).enabled !== enabled) throw Error('Roller mode not confirmed');
      if (robot) robot = {...robot, roller:{...robot.roller, enabled}};
    } catch {
      showNotice({key:'rollerSwitchFailed'});
    } finally {
      rollerBusy = false;
      rollerRevision++;
      render();
    }
  }
  rollerSwitch.addEventListener('click', switchRoller);

  function startGesture(source) {
    if (!gesture) {
      gesture = {twist:[0,0,0], started:false, busy:false,
        timer:setInterval(drive, robot?.roller?.enabled ? 50 : 75)};
    }
    gesture.source = source;
  }

  function centerControls() {
    if (gesture && robot?.roller?.enabled && (gesture.twist[0] || gesture.finishing) && !gesture.paused && !gesture.forwardBlocked) {
      gesture.source = null;
      drive();
    } else {
      release();
    }
  }

  async function drive() {
    const current = gesture;
    if (!current) return;
    if (current.busy) return;
    current.busy = true;
    const [move,turn] = sticks, roller = robot?.roller?.enabled === true;
    const target = [deadzone(move.y) * (roller ? .4 : .15), roller ? 0 : -deadzone(move.x) * .3,
      -deadzone(turn.x) * (roller ? .45 : 1)];
    const rollerRelease = !current.source || (roller && current.started && !target.some(Boolean)
      && Boolean(current.twist[0] || current.finishing));
    current.twist = target.map((value, i) => {
      if (!roller) return value === 0 ? 0 : current.twist[i] + .35 * (value - current.twist[i]);
      const next = .75 * current.twist[i] + .25 * value;
      return value === 0 && Math.abs(next) < .001 ? 0 : next;
    });
    try {
      if (rollerRelease && !current.started) {release(); return;}
      const data = await send(current.started ? 'drive' : 'begin', rollerRelease ? [0,0,0] : current.twist, rollerRelease);
      if (gesture !== current) return;
      current.started = true;
      current.finishing = data.roller_gliding === true;
      current.paused = data.paused === true;
      current.forwardBlocked = data.forward_blocked === true;
      if (!current.source && (current.paused || current.forwardBlocked || (rollerRelease && !data.roller_gliding))) release();
      render();
    } catch (error) {
      if (gesture === current) {
        release();
        showNotice({text:error.message});
      }
    } finally {
      current.busy = false;
    }
  }

  function radius(stick) {
    return (stick.pad.getBoundingClientRect().width - stick.knob.offsetWidth) / 2;
  }

  function positionStick(stick, x, y, travel = radius(stick)) {
    const scale = Math.max(1, Math.hypot(x, y));
    stick.x = x / scale;
    stick.y = y / scale;
    stick.knob.style.transform = `translate(${stick.x * travel}px,${-stick.y * travel}px)`;
  }

  function move(stick, event) {
    const bounds = stick.pad.getBoundingClientRect();
    const roller = robot?.roller?.enabled === true;
    const travel = ((roller ? stick.pad.clientWidth : bounds.width) - stick.knob.offsetWidth) / 2;
    const x = (event.clientX - bounds.left - bounds.width / 2) / travel;
    const y = roller || stick === sticks[0] ? (bounds.top + bounds.height / 2 - event.clientY) / travel : 0;
    positionStick(stick, x, y, travel);
  }

  for (const stick of sticks) {
    const pad = stick.pad;
    pad.addEventListener('pointerdown', event => {
      if (event.button !== 0 || stick.pointer !== null || gesture?.source === 'keyboard' || !mode().ready || pending || rollerBusy) return;
      event.preventDefault();
      if (!allowRemote()) return;
      pad.setPointerCapture(event.pointerId);
      stick.pointer = event.pointerId;
      pad.classList.add('is-held');
      startGesture('pointer');
      notice = null;
      move(stick, event);
      drive();
    });
    pad.addEventListener('pointermove', event => {if (event.pointerId === stick.pointer) move(stick, event);});
    for (const type of ['pointerup','pointercancel','lostpointercapture']) {
      pad.addEventListener(type, event => {
        if (event.pointerId !== stick.pointer) return;
        resetStick(stick);
        if (type !== 'pointerup') {release(); render();}
        else if (sticks.every(item => item.pointer === null)) {centerControls(); render();}
        else drive();
      });
    }
  }

  function keyboardSticks() {
    const axis = (positive, negative) => Number(heldKeys.has(positive)) - Number(heldKeys.has(negative));
    const x = robot?.roller?.enabled ? 0 : axis('KeyD','KeyA');
    positionStick(sticks[0], x, axis('KeyW','KeyS'));
    positionStick(sticks[1], axis('KeyE','KeyQ'), 0);
    for (const stick of sticks) stick.pad.classList.toggle('is-held', !!(stick.x || stick.y));
    drive();
  }

  function keyboardBlocked(target) {
    return document.body.dataset.hud === 'video' || document.querySelector('dialog[open]')
      || target.closest?.('input, textarea, select, [contenteditable], [role="textbox"]');
  }

  window.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey
        || keyboardBlocked(event.target) || document.hidden) return;
    if (event.code === 'Space') {
      if (!robot || mode().exclusive || !gesture && event.target.closest?.('button:focus-visible')) return;
      event.preventDefault();
      if (!event.repeat) action('stop');
      return;
    }
    if (!driveKeys.has(event.code) || !mode().ready || pending || rollerBusy || gesture?.source === 'pointer') return;
    if (robot?.roller?.enabled && ['KeyA','KeyD'].includes(event.code)) return;
    event.preventDefault();
    if (event.repeat || heldKeys.has(event.code) || !allowRemote()) return;
    heldKeys.add(event.code);
    startGesture('keyboard');
    notice = null;
    keyboardSticks();
  });

  window.addEventListener('keyup', event => {
    if (!heldKeys.delete(event.code)) return;
    event.preventDefault();
    if (heldKeys.size) keyboardSticks();
    else {sticks.forEach(resetStick); centerControls(); render();}
  });
  document.addEventListener('focusin', event => {
    if (gesture?.source === 'keyboard' && keyboardBlocked(event.target)) {release(); render();}
  });

  async function action(kind) {
    release();
    const request = {kind};
    pending = request;
    showNotice(null);
    try {
      await send(kind);
      if (pending === request) showNotice({kind, snapshot:robot});
    } catch (error) {
      if (pending === request) showNotice({text:error.message});
    } finally {
      if (pending === request) {pending = null; render();}
    }
  }
  for (const button of actions) {
    if (button.dataset.motion !== 'stop') window.DuckRemoteNotice?.bindButton(button);
    button.addEventListener('click', () => action(button.dataset.motion));
  }
  $('torqueOff').addEventListener('click', () => action('torque_off'));
  portraitOrientation.addEventListener('change', () => {
    release();
    render();
  });
  for (const id of ['settings','padLink','openBinding','openWalkingHelp']) $(id)?.addEventListener('click', release);
  for (const event of ['blur','pagehide']) window.addEventListener(event, release);
  document.addEventListener('visibilitychange', () => {if (document.hidden) release();});
  window.addEventListener('duck-languagechange', render);
  window.DuckMotion = {
    get revision() {return rollerRevision;},
    release() {release(); render();},
    update(status, physicalGamepadActive, revision) {
      if (status && robot && (rollerBusy || revision !== rollerRevision)) status = {...status, roller:robot.roller};
      if (robot && robot.roller?.enabled !== status?.roller?.enabled) release();
      robot = status;
      gamepadActive = physicalGamepadActive;
      if (!mode().ready) release();
      render();
    }
  };
})();
