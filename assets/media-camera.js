(() => {
  const el = id => document.getElementById(id);
  let status = null, busy = false, streaming = false, revision = 0;
  function preview() {
    const want = status?.running && !document.hidden;
    if (want && !streaming) {
      streaming = true;
      el('camera-feed').src = '/api/media/camera/stream?t=' + Date.now();
    } else if (!want && streaming) {
      streaming = false;
      el('camera-feed').removeAttribute('src');
    }
    el('camera-feed').hidden = !want || !status?.ready;
    el('camera-placeholder').hidden = !!want && !!status?.ready;
  }
  function render(s) {
    status = s;
    el('camera-view').hidden = !s.running;
    el('camera-toggle').disabled = busy || (!s.running && !s.available);
    el('camera-toggle').textContent = s.running ? 'Stop camera' : 'Start camera';
    el('camera-toggle').setAttribute('aria-pressed', String(s.running));
    el('camera-status').textContent = s.running ? (s.ready ? 'Live' : 'Starting…') : 'Off';
    el('camera-detail').textContent = s.error || (s.running
      ? `640 × 480 · ${s.fps} fps · ${s.device}` : 'USB camera · Live video stream');
    el('camera-snapshot').disabled = !s.ready || !s.running;
    preview();
  }
  async function refresh() {
    const version = revision;
    try {
      if (busy || document.hidden) return;
      const r = await fetch('/api/media/status', {signal: AbortSignal.timeout(5000)});
      const s = await r.json();
      if (!r.ok) throw Error(s.error || 'Camera service unavailable');
      if (!busy && version === revision) render(s.camera);
    } catch (e) {
      if (version !== revision) return;
      status = null; preview();
      el('camera-status').textContent = 'Disconnected';
      el('camera-detail').textContent = e.message;
    } finally { setTimeout(refresh, 1000); }
  }
  el('camera-toggle').onclick = async () => {
    if (busy) return;
    busy = true; revision++; el('camera-toggle').disabled = true;
    try {
      const action = status?.running ? 'stop' : 'start';
      const r = await fetch('/api/media/camera/' + action, {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Device-Test': '1'}, body: '{}',
        signal: AbortSignal.timeout(8000)});
      const s = await r.json();
      if (!r.ok) throw Error(s.error || 'Camera request failed');
      busy = false; render(s.camera);
    } catch (e) { el('camera-detail').textContent = e.message; }
    finally { busy = false; el('camera-toggle').disabled = false; }
  };
  el('camera-feed').onerror = () => { streaming = false; };
  el('camera-snapshot').onclick = async () => {
    try {
      const r = await fetch('/api/media/camera/frame', {signal: AbortSignal.timeout(5000)});
      if (!r.ok) throw Error('No camera frame available');
      const url = URL.createObjectURL(await r.blob()), a = document.createElement('a');
      a.href = url; a.download = 'duck-camera-' + Date.now() + '.jpg'; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { el('camera-detail').textContent = e.message; }
  };
  document.addEventListener('visibilitychange', preview);
  window.addEventListener('pagehide', () => { status = null; preview(); });
  refresh();
})();
