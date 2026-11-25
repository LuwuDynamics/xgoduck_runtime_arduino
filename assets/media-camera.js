(() => {
  const el = id => document.getElementById(id);
  let status = null, busy = false, revision = 0, videoSession = null, objectUrl = null;
  function stopPreview() {
    const session = videoSession;
    videoSession = null;
    if (session) {
      clearTimeout(session.timer);
      clearTimeout(session.timeout);
      session.abort?.abort();
    }
    el('camera-feed').removeAttribute('src');
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
  }
  function startPreview() {
    if (videoSession) return;
    const session = {seq: 0, abort: null, timer: null, timeout: null, errors: 0};
    videoSession = session;
    const img = el('camera-feed');
    const next = async () => {
      if (videoSession !== session) return;
      const started = performance.now();
      const fpsLimit = Number(status?.preview_fps_limit) || 15;
      const minInterval = 1000 / Math.max(1, fpsLimit);
      const controller = new AbortController();
      session.abort = controller;
      session.timeout = setTimeout(() => controller.abort(), 4000);
      let waitMs = 0;
      try {
        const r = await fetch('/api/media/camera/frame?preview=1&after=' + session.seq,
          {cache: 'no-store', signal: controller.signal});
        clearTimeout(session.timeout); session.timeout = null;
        if (videoSession !== session) return;
        if (r.status === 204) {
          session.errors = 0;
          waitMs = 0;
        } else if (r.ok) {
          const seq = Number(r.headers.get('X-Frame-Seq'));
          if (Number.isFinite(seq) && seq > 0) session.seq = seq;
          const blob = await r.blob();
          if (videoSession !== session) return;
          const nextUrl = URL.createObjectURL(blob);
          const previous = objectUrl;
          objectUrl = nextUrl;
          img.src = nextUrl;
          try { if (typeof img.decode === 'function') await img.decode(); } catch {}
          if (previous) URL.revokeObjectURL(previous);
          session.errors = 0;
          waitMs = Math.max(0, minInterval - (performance.now() - started));
        } else {
          throw Error('HTTP ' + r.status);
        }
      } catch {
        clearTimeout(session.timeout); session.timeout = null;
        if (videoSession !== session) return;
        session.errors++;
        waitMs = Math.min(1000, 150 * session.errors);
      } finally {
        if (session.abort === controller) session.abort = null;
      }
      if (videoSession === session) {
        if (waitMs > 0) session.timer = setTimeout(next, waitMs);
        else next();
      }
    };
    next();
  }
  function preview() {
    const want = status?.running && !document.hidden;
    if (want && !videoSession) startPreview();
    else if (!want && videoSession) stopPreview();
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
