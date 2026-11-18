(() => {
  const $ = id => document.getElementById(id);
  const i18n = window.DuckI18n;
  const { t } = i18n;
  const CONNECT_SOCKET_ERROR = 'org.bluez.Error.Failed: br-connection-create-socket';
  const sounds = {
    duck_greet: 'duckGreet', duck_inquire: 'duckInquire', duck_alarm: 'duckAlarm', duck_peck: 'duckPeck',
    duck_chirp: 'duckChirp', duck_coo: 'duckCoo', duck_wheee: 'duckWheee'
  };
  const actions = {
    walk: 'gamepadWalk', hold: 'modeHold', off: 'torqueOff', pick: 'actionPick', roulade: 'actionRoll',
    sit: 'actionSit', roller: 'gamepadRoller', left_kick: 'actionKickLeft', right_kick: 'actionKickRight', ...sounds
  };
  const buttonLabels = {
    south: ['A / bottom', 'A / 下方键'], east: ['B / right', 'B / 右侧键'],
    west: ['X / left', 'X / 左侧键'], north: ['Y / top', 'Y / 上方键'],
    lb: ['LB / L', 'LB / L'], rb: ['RB / R', 'RB / R'],
    back: ['View / −', '查看 / −'], start: ['Menu / +', '菜单 / +'], guide: ['Home', '主页键'],
    ls: ['Left stick click', '按下左摇杆'], rs: ['Right stick click', '按下右摇杆'],
    lt: ['LT / L2', 'LT / L2'], rt: ['RT / R2', 'RT / R2'], f1: ['F1', 'F1'], f3: ['F3', 'F3'],
    up: ['D-pad up', '十字键上'], down: ['D-pad down', '十字键下'],
    left: ['D-pad left', '十字键左'], right: ['D-pad right', '十字键右']
  };
  const messages = new Map(Object.entries({
    'Binding cancelled.': 'gamepadBindCancelled',
    'Default bindings restored.': 'gamepadBindRestored',
    'Binding cleared.': 'gamepadBindCleared',
    'Binding saved.': 'gamepadBindSaved',
    'Saved bindings could not be loaded. Defaults are in use.': 'gamepadBindLoadFailed',
    'Release held buttons, then press one button (15 seconds).': 'gamepadBindRelease',
    'Press only one supported button. Select Bind to try again.': 'gamepadBindMultiple',
    'Controller disconnected. Select Bind to try again.': 'gamepadBindDisconnected',
    'Binding timed out. Select Bind to try again.': 'gamepadBindTimeout',
    'Finish or cancel button binding first.': 'gamepadBindFinish',
    'Torque off must have a button assigned.': 'gamepadTorqueRequired',
    'Disable gamepad control before changing bindings.': 'gamepadDisableToBind',
    'Connect a supported gamepad first.': 'gamepadConnectFirst',
    'Center the sticks and release all buttons before enabling control.': 'gamepadCentreFirst',
    'Exit calibration or servo setup first.': 'gamepadExitSetup',
    'Gamepad control paused for calibration or servo setup.': 'gamepadPausedForSetup',
    'Controller disconnected. Reconnect and enable control again.': 'gamepadReconnect',
    'Gamepad service unavailable.': 'gamepadServiceUnavailable',
    'Gamepad service disconnected.': 'gamepadServiceUnavailable',
    'Gamepad service disconnected. Control stopped.': 'gamepadServiceUnavailable',
    'Gamepad input is not available.': 'gamepadNoInput',
    'Stand Duck up before using this action.': 'gamepadStandBeforeAction',
    'stand up before switching roller mode': 'rollerStandFirst',
    'stand up before switching to walk': 'gamepadStandFirst',
    'kick requires shadow/policy mode': 'gamepadKickInWalk',
    'MCU feedback is stale': 'gamepadFeedback',
    'required servo feedback / IMU missing': 'gamepadFeedback',
    'IMU feedback is stale': 'gamepadFeedback',
    'wait for IMU feedback before roulade': 'gamepadFeedback',
    'STM32 feedback timed out; motion paused, holding last targets': 'gamepadBridge',
    'STM32 Bridge is reconnecting. 运动通信恢复中，请稍后重试。': 'gamepadBridge',
    'IMU calibration is in progress. Wait for it to finish or exit calibration.': 'gamepadIMU',
    '低电压保护中，请充电后重新使能。 Low battery protection is active.': 'motionBattery',
    'No Bluetooth adapter found.': 'gamepadBluetoothUnavailable',
    'A Bluetooth operation is already in progress.': 'gamepadBluetoothBusy',
    'Bluetooth is busy. Check status before retrying.': 'gamepadBluetoothBusy',
    'Controller not found. Scan again.': 'gamepadControllerMissing',
    'Unitree BLE timed out. Keep the remote on and disconnect it from other apps.': 'gamepadBLETimeout',
    'Audio is busy. Stop playback or recording first.': 'gamepadSoundBusy',
    'Chat speaker busy; try after the answer. 请等当前语音播放结束再鸭叫。': 'gamepadSoundBusy',
    'USB speaker unavailable. 未连接可用的 USB 扬声器。': 'duckSpeakerMissing',
    'USB role control is unavailable.': 'usbUnavailable'
  }));
  let state = null, busy = '', revision = 0, connectionError = '', actionError = null;
  let devicesKey = '', inputsKey = '', bindingsKey = '';

  const buttonLabel = name => buttonLabels[name]?.[i18n.language === 'zh' ? 1 : 0] || name.toUpperCase();
  function setText(id, value) {
    const element = $(id);
    if (element.textContent !== value) element.textContent = value;
  }

  function feedback(message, fallback) {
    const text = typeof message === 'string' ? message : '';
    const duplicate = /^Button already assigned to (\w+)\./.exec(text);
    if (duplicate && Object.hasOwn(actions, duplicate[1])) {
      return t('gamepadBindDuplicate', {action:t(actions[duplicate[1]])});
    }
    if (text.startsWith('Could not save binding:')) return t('gamepadBindSaveFailed');
    if (/upright|wait for get-up recovery/.test(text)) return t('gamepadWaitUpright');
    if (/^(wait for |sit\/stand is already active)/.test(text) && !messages.has(text)) return t('gamepadWaitAction');
    if (text.startsWith('旧版软件零位')) return t('gamepadServoCentres');
    if (text.startsWith('IMU 标定文件无效')) return t('gamepadIMUFile');
    return t(messages.get(text) || fallback);
  }

  async function act(action, data = {}) {
    if (busy || !state || connectionError) return;
    busy = action;
    actionError = null;
    revision++;
    render();
    try {
      const response = await fetch('/api/gamepad/' + action, {
        method: 'POST', headers: {'Content-Type':'application/json', 'X-Duck-Gamepad':'1'},
        body: JSON.stringify(data), signal: AbortSignal.timeout(12000)
      });
      const body = await response.json();
      if (response.ok) state = body;
      else actionError = {message: body.error, fallback: action === 'usb' ? 'gamepadUSBSwitchFailed' : 'gamepadRequestFailed'};
    } catch {
      actionError = {fallback:'connectionFailed'};
    } finally {
      busy = '';
      render();
    }
  }

  function button(key, click, disabled) {
    const element = document.createElement('button');
    element.type = 'button';
    element.textContent = t(key);
    element.onclick = click;
    element.disabled = disabled;
    return element;
  }

  function renderDevices(devices, working) {
    const list = devices.map(({rssi, ...device}) => device);
    const key = JSON.stringify([i18n.language, [...list].sort((a, b) => a.address.localeCompare(b.address)), working]);
    if (key === devicesKey) return;
    devicesKey = key;
    $('devices').replaceChildren();
    for (const device of list) {
      const row = document.createElement('div');
      row.className = 'device-row';
      const info = document.createElement('div');
      info.className = 'device-info';
      const title = document.createElement('strong');
      title.textContent = device.name;
      const detail = document.createElement('p');
      detail.className = 'small';
      detail.textContent = device.address + ' · ' + t(device.connected ? 'gamepadDeviceConnected' : device.paired ? 'gamepadDevicePaired' : 'gamepadDeviceUnpaired');
      info.append(title, detail);
      if (device.kind === 'unitree_ble') {
        const note = document.createElement('p');
        note.className = 'small';
        note.textContent = t('gamepadUnitree');
        info.append(note);
      }
      const buttons = document.createElement('div');
      buttons.className = 'row';
      const request = action => () => act(action, {address: device.address});
      if (device.connected) {
        buttons.append(button('gamepadDisconnect', request('disconnect'), working));
      } else if (device.connect_supported !== false) {
        const label = device.kind === 'unitree_ble' ? 'gamepadConnectBLE' : device.paired ? 'gamepadConnect' : 'gamepadPairConnect';
        buttons.append(button(label, request('connect'), working));
      } else {
        const detected = document.createElement('span');
        detected.className = 'badge';
        detected.textContent = t('gamepadDetected');
        buttons.append(detected);
      }
      if (device.paired) buttons.append(button('gamepadForget', request('forget'), working));
      row.append(info, buttons);
      $('devices').append(row);
    }
  }

  function renderInputs(devices) {
    const key = JSON.stringify([i18n.language, devices]);
    if (key === inputsKey) return;
    inputsKey = key;
    $('inputDevice').replaceChildren();
    // A lone HID option can look selected while the active BLE input is disconnected.
    if (!devices.some(device => device.selected)) {
      const label = devices.length ? 'gamepadSelect' : 'gamepadNoInput';
      const placeholder = new Option(t(label), '', true, true);
      placeholder.disabled = true;
      $('inputDevice').append(placeholder);
    }
    for (const device of devices) $('inputDevice').append(new Option(device.name, device.path, false, device.selected));
  }

  function renderBindings(control, input, working) {
    const {bindings, learning, enabled, roller_mode: roller} = control;
    const key = JSON.stringify([i18n.language, bindings, learning, enabled, working, input.connected, roller]);
    if (key === bindingsKey) return;
    bindingsKey = key;
    const motionRows = $('bindings'), soundRows = $('soundBindings');
    motionRows.replaceChildren();
    soundRows.replaceChildren();
    const locked = working || enabled || !!learning || !bindings;
    for (const [action, label] of Object.entries(actions)) {
      if (bindings && !Object.hasOwn(bindings, action)) continue;
      const row = document.createElement('div');
      row.className = 'binding-row';
      const info = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = t(label);
      const value = document.createElement('span');
      value.className = 'binding-value';
      const assigned = bindings?.[action];
      value.textContent = learning === action ? t('gamepadPressButton') : assigned ? buttonLabel(assigned) : t('gamepadUnassigned');
      if (action === 'roller' && typeof roller === 'boolean') value.append(' · ' + t(roller ? 'rollerOn' : 'rollerOff'));
      info.append(name, value);
      const buttons = document.createElement('div');
      buttons.className = 'row';
      buttons.append(button(learning === action ? 'gamepadListening' : 'gamepadBind',
        () => act('bindings', {operation:'learn', action}), locked || !input.connected));
      if (action !== 'off') buttons.append(button('gamepadClear',
        () => act('bindings', {operation:'clear', action}), locked || !assigned));
      row.append(info, buttons);
      (Object.hasOwn(sounds, action) ? soundRows : motionRows).append(row);
    }
    $('soundBindingGroup').hidden = !soundRows.childElementCount;
  }

  function render() {
    const {bluetooth = {}, input = {}, control = {}, usb} = state || {};
    const online = !!state && !connectionError;
    const working = !online || !!busy || !!bluetooth.busy;
    const devices = bluetooth.devices || [];
    const inputs = input.devices || [];
    setText('connection', online ? t('gamepadConnectedTo', {host: location.hostname}) : t(connectionError || 'gamepadConnecting'));
    $('usbHost').disabled = working || !usb?.available;
    $('usbHost').setAttribute('aria-checked', String(!!usb?.host));
    setText('usbHost', busy === 'usb' ? t('usbSwitching') : usb?.available ? t(usb.host ? 'usbOn' : 'usbOff') : 'USB Host');
    setText('usbStatus', !state && !connectionError ? t('gamepadChecking')
      : !online || !usb?.available ? t('usbUnavailable') : t(usb.host ? 'usbHostMode' : 'usbDeviceMode'));
    if (online && usb?.error) setText('usbStatus', feedback(usb.error, 'usbUnavailable'));
    setText('adapter', t(!state && !connectionError ? 'gamepadChecking'
      : !online || !bluetooth.available ? 'gamepadBluetoothUnavailable' : bluetooth.powered ? 'gamepadBluetoothOn' : 'gamepadBluetoothOff'));
    $('scan').disabled = working || !bluetooth.available || !!bluetooth.discovering;
    $('stopScan').disabled = working || !bluetooth.discovering;
    setText('scanStatus', busy ? t('gamepadSending') : bluetooth.busy ? t('gamepadBluetoothBusy')
      : bluetooth.discovering ? t('gamepadScanning', {seconds: bluetooth.scan_remaining || 0}) : '');
    renderDevices(devices, working);
    $('empty').hidden = !online || devices.length > 0;
    setText('empty', t(bluetooth.discovering ? 'gamepadSearching' : 'gamepadNoDevices'));

    renderInputs(inputs);
    $('inputDevice').disabled = working || !inputs.length || !!control.enabled;
    setText('controlState', t(control.enabled ? 'gamepadEnabled' : 'gamepadDisabled'));
    $('enable').disabled = working || !!control.enabled || !!control.learning || !input.connected;
    $('disable').disabled = !online || !!busy || !control.enabled;
    if (input.connected) {
      const battery = input.kind === 'unitree_ble' && Number.isFinite(input.battery)
        ? ' · ' + t('gamepadBattery', {level: input.battery}) : '';
      setText('inputStatus', t('gamepadInputReady', {name: input.name}) + battery);
    } else {
      setText('inputStatus', t(inputs.length ? 'gamepadSelectInput'
        : devices.some(device => device.connected) ? 'gamepadBluetoothWaiting' : 'gamepadWaiting'));
    }
    const {lx = 0, ly = 0, rx = 0, ry = 0} = input.axes || {};
    $('leftStick').style.transform = 'translate(' + (lx * 38) + 'px,' + (ly * 38) + 'px)';
    $('rightStick').style.transform = 'translate(' + (rx * 38) + 'px,' + (ry * 38) + 'px)';
    const pressed = Object.entries(input.buttons || {}).filter(([, down]) => down).map(([name]) => buttonLabel(name));
    setText('buttons', t('gamepadButtons', {names: pressed.join(' · ') || '—'}));

    renderBindings(control, input, working);
    $('cancelBinding').hidden = !control.learning;
    $('cancelBinding').disabled = !online || !!busy;
    $('resetBindings').disabled = working || !!control.enabled || !!control.learning || !control.bindings;
    setText('bindingStatus', !online ? '' : !control.bindings ? t('gamepadBindingsRestart')
      : control.enabled ? t('gamepadDisableToBind') : feedback(control.binding_message, 'gamepadBindPrompt'));
    const recoveredConnect = bluetooth.error === CONNECT_SOCKET_ERROR && input.connected && control.enabled;
    const currentError = control.error || (recoveredConnect ? '' : bluetooth.error);
    const error = actionError ? feedback(actionError.message, actionError.fallback)
      : currentError ? feedback(currentError, 'gamepadRequestFailed')
      : control.sound_error ? feedback(control.sound_error, 'duckSoundFailed') : '';
    $('error').hidden = !error;
    setText('error', error);
  }

  $('usbHost').onclick = () => {
    if (state?.usb?.available) act('usb', {host: !state.usb.host});
  };
  $('scan').onclick = () => act('scan');
  $('stopScan').onclick = () => act('stop-scan');
  $('enable').onclick = () => act('control', {enabled: true});
  $('disable').onclick = () => act('control', {enabled: false});
  $('inputDevice').onchange = () => act('select', {path: $('inputDevice').value});
  $('cancelBinding').onclick = () => act('bindings', {operation:'cancel'});
  $('resetBindings').onclick = () => act('bindings', {operation:'reset'});
  window.addEventListener('duck-languagechange', render);

  async function poll() {
    const current = revision;
    try {
      if (busy || document.hidden) return;
      const response = await fetch('/api/gamepad/status', {signal: AbortSignal.timeout(4000)});
      const next = response.ok ? await response.json() : null;
      if (current !== revision || busy) return;
      connectionError = next ? '' : response.status === 404 ? 'gamepadRestart' : 'gamepadRetrying';
      if (next) state = next;
      render();
    } catch {
      if (current === revision && !busy) {
        connectionError = 'gamepadRetrying';
        render();
      }
    } finally {
      setTimeout(poll, 500);
    }
  }
  render();
  poll();
})();
