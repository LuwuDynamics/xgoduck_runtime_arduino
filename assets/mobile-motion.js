/* Local cockpit sticks and keyboard. The board expires travel if renewals stop. */
(() => {
  const $ = id => document.getElementById(id);
  const t = window.DuckI18n.t;
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
  let robot = null, gamepadActive = false, sequence = 0, gesture = null, pending = null, notice = null, noticeTimer;
  const deadzone = value => Math.abs(value) <= .15 ? 0 : value;

  async function send(kind, twist) {
    const drive = kind === 'begin' || kind === 'drive';
    const response = await fetch('/api/cockpit/motion', {
      method:'POST', headers:{'Content-Type':'application/json', 'X-Duck-Cockpit':'1'},
      body:JSON.stringify({controller_id:controllerId, sequence:++sequence, kind, twist}),
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
    const standing = usable && robot.enabled === true && robot.mode === 'policy' && !robot.fault_hold
      && robot.recovery?.phase === 'walk' && Number.isFinite(robot.recovery.angle_deg)
      && robot.recovery.angle_deg < (gesture ? 55 : 15);
    return {exclusive, seated, active, usable, ready:standing && !seated && !active};
  }

  function render() {
    const state = mode();
    const roller = robot?.roller?.enabled === true;
    $('moveStickLabel').textContent = t(roller ? 'moveStickRoller' : 'moveStick');
    $('keyboardStrafe').hidden = roller;
    sticks[0].pad.classList.toggle('roller-pad', roller);
    for (const stick of sticks) stick.pad.setAttribute('aria-disabled', String(!state.ready || !!pending));
    for (const button of actions) {
      const kind = button.dataset.motion;
      button.disabled = kind === 'stop' ? !robot || state.exclusive
        : kind === 'stand' || kind === 'default_pose' ? !state.usable || !!state.active || !!pending || robot?.arming === true
        : !state.ready || !!pending || roller && kind.startsWith('head_');
      button.toggleAttribute('data-active', kind === state.active || kind === 'sit' && state.seated);
    }
    const feedback = robot?.battery?.low || state.exclusive ? null : notice;
    const standStarting = pending?.kind === 'stand' || feedback?.kind === 'stand' && feedback.snapshot === robot;
    const armFailed = robot?.arm_failed === true || robot?.mode === 'policy' && robot.enabled === false && !robot.arming;
    const gettingUp = robot?.mode === 'policy' && robot.enabled === true
      && ['default_pose','getup'].includes(robot.recovery?.phase);
    const key = !robot ? 'waitingFeedback' : robot.battery?.low ? 'motionBattery'
      : state.exclusive ? 'motionOtherOwner' : robot.arming ? 'motionArming' : standStarting ? 'motionStarting'
      : pending ? 'motionBusy' : gesture?.forwardBlocked ? 'motionTof' : gesture?.paused ? 'motionVoiceBusy'
      : !state.usable ? 'waitingFeedback' : armFailed ? 'motionArmFailed'
      : state.active ? 'motionInProgress' : gettingUp ? 'motionGettingUp' : state.seated ? 'motionSeated'
      : state.ready ? 'motionReady' : robot.mode === 'policy' && !robot.fault_hold ? 'motionSettling' : 'motionStandHint';
    const action = pending?.kind || state.active;
    const status = $('motionStatus');
    const text = feedback?.text || (feedback && feedback.kind !== 'stand' && !state.active && !state.seated
      ? t('motionAccepted', {action:t(names[feedback.kind])})
      : t(key, {action:action ? t(names[action]) : ''}));
    if (status.textContent !== text) status.textContent = text;
    const poseStatus = $('poseStatus');
    const poseText = pending || feedback || !state.ready ? text : t(roller ? 'poseRoller' : 'poseHint');
    if (poseStatus.textContent !== poseText) poseStatus.textContent = poseText;
    status.title = text;
    status.toggleAttribute('data-warning', !!(feedback?.text || gesture?.forwardBlocked
      || robot?.battery?.low || key === 'motionArmFailed'));
  }

  function showNotice(value) {
    clearTimeout(noticeTimer);
    notice = value;
    if (value) noticeTimer = setTimeout(() => {notice = null; render();}, 4500);
    render();
  }

  async function drive() {
    const current = gesture;
    if (!current || current.busy) return;
    current.busy = true;
    const [move,turn] = sticks, roller = robot?.roller?.enabled === true;
    const target = [deadzone(move.y) * .15, roller ? 0 : -deadzone(move.x) * .3,
      -deadzone(turn.x) * (roller ? .25 : 1)];
    current.twist = target.map((value, i) => value === 0 ? 0 : current.twist[i] + .35 * (value - current.twist[i]));
    try {
      const data = await send(current.started ? 'drive' : 'begin', current.twist);
      if (gesture !== current) return;
      current.started = true;
      current.paused = data.paused === true;
      current.forwardBlocked = data.forward_blocked === true;
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
    const travel = (bounds.width - stick.knob.offsetWidth) / 2;
    const verticalOnly = stick === sticks[0] && robot?.roller?.enabled === true;
    const x = verticalOnly ? 0 : (event.clientX - bounds.left - bounds.width / 2) / travel;
    const y = stick === sticks[0] ? (bounds.top + bounds.height / 2 - event.clientY) / travel : 0;
    positionStick(stick, x, y, travel);
  }

  for (const stick of sticks) {
    const pad = stick.pad;
    pad.addEventListener('pointerdown', event => {
      if (event.button !== 0 || stick.pointer !== null || gesture?.source === 'keyboard' || !mode().ready || pending) return;
      event.preventDefault();
      pad.setPointerCapture(event.pointerId);
      stick.pointer = event.pointerId;
      pad.classList.add('is-held');
      gesture ??= {source:'pointer', twist:[0,0,0], started:false, busy:false};
      notice = null;
      move(stick, event);
      drive();
    });
    pad.addEventListener('pointermove', event => {if (event.pointerId === stick.pointer) move(stick, event);});
    for (const type of ['pointerup','pointercancel','lostpointercapture']) {
      pad.addEventListener(type, event => {
        if (event.pointerId !== stick.pointer) return;
        resetStick(stick);
        if (sticks.every(item => item.pointer === null)) {release(); render();}
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
    return document.body.classList.contains('clean') || document.querySelector('dialog[open]')
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
    if (!driveKeys.has(event.code) || !mode().ready || pending || gesture?.source === 'pointer') return;
    if (robot?.roller?.enabled && ['KeyA','KeyD'].includes(event.code)) return;
    event.preventDefault();
    if (event.repeat || heldKeys.has(event.code)) return;
    heldKeys.add(event.code);
    gesture ??= {source:'keyboard', twist:[0,0,0], started:false, busy:false};
    notice = null;
    keyboardSticks();
  });

  window.addEventListener('keyup', event => {
    if (!heldKeys.delete(event.code)) return;
    event.preventDefault();
    if (heldKeys.size) keyboardSticks();
    else {release(); render();}
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
  for (const button of actions) button.addEventListener('click', () => action(button.dataset.motion));
  $('torqueOff').addEventListener('click', () => action('torque_off'));
  for (const [button, dialog] of [['moreActions','poseDialog'], ['openDuckSounds','duckSoundDialog']]) {
    $(button).addEventListener('click', () => {
      release();
      showNotice(null);
      $(dialog).showModal();
    });
  }
  for (const id of ['clean','settings','padLink','openBinding']) $(id).addEventListener('click', release);
  for (const event of ['blur','pagehide']) window.addEventListener(event, release);
  document.addEventListener('visibilitychange', () => {if (document.hidden) release();});
  window.addEventListener('duck-languagechange', render);
  setInterval(drive, 75);
  window.DuckMotion = {update(status, physicalGamepadActive) {
    if (robot && robot.roller?.enabled !== status?.roller?.enabled) release();
    robot = status;
    gamepadActive = physicalGamepadActive;
    if (!mode().ready) release();
    render();
  }};
})();
