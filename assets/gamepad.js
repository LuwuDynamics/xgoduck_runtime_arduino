(() => {
  const $ = id => document.getElementById(id);
  let busy = false, state = null, devicesKey = '', inputsKey = '', revision = 0, bindingsKey = '', actionError = '';
  const labels = {south:'A / bottom', east:'B / right', west:'X / left', north:'Y / top', lb:'LB / L', rb:'RB / R', back:'View / −', start:'Menu / +', guide:'Home', ls:'Left stick click', rs:'Right stick click', lt:'LT / L2', rt:'RT / R2', f1:'F1', f3:'F3', up:'D-pad up', down:'D-pad down', left:'D-pad left', right:'D-pad right'};
  const actions = {walk:'Walk / get up', hold:'Default pose', off:'Torque off', pick:'Pick', roulade:'Roulade'};
  function error(text='') { $('error').textContent = text; $('error').hidden = !text; }
  async function act(action, data={}) {
    if (busy) return;
    busy = true; actionError = ''; revision++; if (state) render(state); error();
    try {
      const r = await fetch('/api/gamepad/' + action, {method:'POST',
        headers:{'Content-Type':'application/json','X-Duck-Gamepad':'1'}, body:JSON.stringify(data),
        signal:AbortSignal.timeout(12000)});
      const body = await r.json(); if (!r.ok) throw Error(body.error || 'Request failed.');
      state = body;
    } catch(e) { actionError=e.message; error(actionError); }
    finally { busy = false; devicesKey = ''; if (state) render(state); }
  }
  function render(s) {
    state = s;
    const b=s.bluetooth, input=s.input, c=s.control, working=busy || b.busy;
    $('connection').textContent='Connected to ' + location.hostname;
    $('adapter').textContent=b.available ? b.powered ? 'Bluetooth on' : 'Bluetooth off' : 'Unavailable';
    $('scan').disabled=working || !b.available || b.discovering;
    $('stopScan').disabled=working || !b.discovering;
    $('scanStatus').textContent=busy ? 'Sending request…' : b.busy ? 'Pairing / connecting…' : b.discovering ? `Scanning · ${b.scan_remaining || 0} s` : '';
    const key=JSON.stringify([(b.devices || []).map(({rssi,...d})=>d).sort((a,b)=>a.address.localeCompare(b.address)),working]);
    if (key !== devicesKey) {
      devicesKey=key; $('devices').replaceChildren();
      for (const d of b.devices || []) {
        const row=document.createElement('div');row.className='device-row';
        const info=document.createElement('div');info.className='device-info';
        const title=document.createElement('strong');title.textContent=d.name;
        const detail=document.createElement('p');detail.className='small';
        detail.textContent=`${d.address} · ${d.connected ? 'Connected' : d.paired ? 'Paired' : 'Not paired'}`;
        info.append(title,detail);
        if(d.kind==='unitree_ble'){
          const note=document.createElement('p');note.className='small';
          note.textContent='Unitree BLE remote · Direct connection, no PIN required.';
          info.append(note);
        }
        row.append(info);
        const buttons=document.createElement('div');buttons.className='row';
        const button=(label,action)=>{const el=document.createElement('button');el.textContent=label;el.disabled=working;el.onclick=()=>act(action,{address:d.address});buttons.append(el);};
        if(d.connected || d.connect_supported!==false) {
          button(d.connected ? 'Disconnect' : d.kind==='unitree_ble' ? 'Connect BLE' : d.paired ? 'Connect' : 'Pair & connect',d.connected?'disconnect':'connect');
        } else {
          const detected=document.createElement('span');detected.className='badge';detected.textContent='Detected';buttons.append(detected);
        }
        if(d.paired)button('Forget','forget');
        row.append(buttons);$('devices').append(row);
      }
    }
    $('empty').hidden=!!b.devices?.length;
    $('empty').textContent=b.discovering?'Searching for controllers in pairing mode…':'No controllers found. Put the controller in pairing mode, then scan.';
    const ikey=JSON.stringify(input.devices);
    if (ikey !== inputsKey) {
      inputsKey=ikey;$('inputDevice').replaceChildren();
      // Without a placeholder, a lone HID option looks selected even while
      // the actual input source is a disconnected BLE remote.
      if(input.devices?.length&&!input.devices.some(d=>d.selected)){
        const option=document.createElement('option');option.value='';option.textContent='Select a controller…';option.disabled=true;option.selected=true;$('inputDevice').append(option);
      }
      for(const d of input.devices || []) {const option=document.createElement('option');option.value=d.path;option.textContent=d.name;option.selected=d.selected;$('inputDevice').append(option);}
      if(!input.devices?.length){const option=document.createElement('option');option.textContent='No gamepad connected';$('inputDevice').append(option);}
    }
    $('inputDevice').disabled=working || !input.devices?.length || c.enabled;
    $('controlState').textContent=c.enabled?'Enabled':'Off';
    $('enable').disabled=working || c.enabled || !!c.learning || !input.connected;
    $('disable').disabled=busy || !c.enabled;
    $('inputStatus').textContent=input.connected?input.name+' · Input ready'+(input.kind==='unitree_ble' ? ` · Battery ${input.battery}%` : ''):input.devices?.length?'Select a controller below to enable gamepad control.':b.devices?.some(d=>d.connected)?'Bluetooth connected; waiting for a supported gamepad input device.':'Waiting for a gamepad input device.';
    const a=input.axes || {lx:0,ly:0,rx:0,ry:0};
    $('leftStick').style.transform=`translate(${a.lx*38}px,${a.ly*38}px)`;
    $('rightStick').style.transform=`translate(${a.rx*38}px,${a.ry*38}px)`;
    $('buttons').textContent='Buttons: '+(Object.entries(input.buttons||{}).filter(([,down])=>down).map(([name])=>labels[name] || name.toUpperCase()).join(' · ')||'—');
    const bk=JSON.stringify([c.bindings,c.learning,c.enabled,working,input.connected]);
    if(bk!==bindingsKey){
      bindingsKey=bk;$('bindings').replaceChildren();
      for(const [action,title] of Object.entries(actions)){
        const row=document.createElement('div');row.className='binding-row';
        const info=document.createElement('div');
        const name=document.createElement('strong');name.textContent=title;
        const value=document.createElement('span');value.className='binding-value';
        value.textContent=c.learning===action?'Press a button…':labels[c.bindings?.[action]] || 'Not assigned';
        info.append(name,value);row.append(info);
        const buttons=document.createElement('div');buttons.className='row';
        const bind=document.createElement('button');bind.textContent=c.learning===action?'Listening…':'Bind';
        bind.disabled=working || c.enabled || !!c.learning || !input.connected || !c.bindings;
        bind.onclick=()=>act('bindings',{operation:'learn',action});buttons.append(bind);
        if(action!=='off'){
          const clear=document.createElement('button');clear.textContent='Clear';
          clear.disabled=working || c.enabled || !!c.learning || !c.bindings?.[action];
          clear.onclick=()=>act('bindings',{operation:'clear',action});buttons.append(clear);
        }
        row.append(buttons);$('bindings').append(row);
      }
    }
    $('cancelBinding').hidden=!c.learning;$('cancelBinding').disabled=busy;
    $('resetBindings').disabled=working || c.enabled || !!c.learning || !c.bindings;
    $('bindingStatus').textContent=!c.bindings?'Restart the Duck app to activate configurable bindings.':c.enabled?'Disable gamepad control before changing bindings.':c.binding_message || 'Select Bind, then press a controller button.';
    error(actionError || b.error || c.error || '');
  }
  $('scan').onclick=()=>act('scan');$('stopScan').onclick=()=>act('stop-scan');
  $('enable').onclick=()=>act('control',{enabled:true});$('disable').onclick=()=>act('control',{enabled:false});
  $('inputDevice').onchange=()=>act('select',{path:$('inputDevice').value});
  $('cancelBinding').onclick=()=>act('bindings',{operation:'cancel'});
  $('resetBindings').onclick=()=>act('bindings',{operation:'reset'});
  async function poll(){const v=revision;try{if(busy||document.hidden)return;const r=await fetch('/api/gamepad/status',{signal:AbortSignal.timeout(4000)});if(r.status===404)throw Error('Restart the Duck app to activate gamepad support.');if(!r.ok)throw Error('Disconnected. Retrying…');const s=await r.json();if(!busy&&v===revision)render(s);}catch(e){$('connection').textContent=e.message||'Disconnected. Retrying…';document.querySelectorAll('button,select').forEach(el=>el.disabled=true);}finally{setTimeout(poll,500);}}
  poll();
})();
