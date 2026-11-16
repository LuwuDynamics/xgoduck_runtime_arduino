(() => {
  const button = document.getElementById('camera-toggle');
  const label = document.getElementById('camera-status');
  const detail = document.getElementById('camera-detail');
  const image = document.getElementById('camera-feed');
  const placeholder = document.getElementById('camera-placeholder');
  let state = 'off', changing = false, polling = false, revision = 0;
  let timer = null, request = null, imageURL = null;

  function clearPreview() {
    clearTimeout(timer);
    timer = null;
    if (request) request.abort();
    request = null;
    image.hidden = true;
    image.removeAttribute('src');
    if (imageURL) URL.revokeObjectURL(imageURL);
    imageURL = null;
    placeholder.hidden = false;
  }

  function render(s) {
    state = s.state;
    label.textContent = {off: 'Off', starting: 'Starting…', on: 'Live', stopping: 'Stopping…', error: 'Unavailable'}[state] || state;
    label.dataset.state = state;
    button.disabled = changing || state === 'stopping';
    button.textContent = state === 'on' || state === 'starting' ? 'Turn camera off' : 'Turn camera on';
    button.setAttribute('aria-pressed', String(state === 'on' || state === 'starting'));
    detail.textContent = s.error || (state === 'on'
      ? `${s.width} × ${s.height} · up to ${s.fps} fps · UNO Q USB camera`
      : state === 'starting' ? 'Opening the USB camera…'
      : state === 'stopping' ? 'Releasing the USB camera…'
      : 'Connect a USB camera to UNO Q, then turn it on here.');
    placeholder.textContent = s.error || (state === 'off' ? 'Camera is off' : label.textContent);
    if (state !== 'on' && state !== 'starting') clearPreview();
    else if (!document.hidden && !request && timer === null) preview();
  }

  async function preview() {
    timer = null;
    if (document.hidden || changing || !['on', 'starting'].includes(state)) return;
    const current = new AbortController();
    request = current;
    const timeout = setTimeout(() => current.abort(), 3000);
    try {
      const response = await fetch('/api/camera/frame', {cache: 'no-store', signal: current.signal});
      if (response.ok) {
        const blob = await response.blob();
        if (request !== current || current.signal.aborted) return;
        const previous = imageURL;
        imageURL = URL.createObjectURL(blob);
        image.src = imageURL;
        image.hidden = false;
        placeholder.hidden = true;
        if (previous) URL.revokeObjectURL(previous);
      } else if (response.status !== 503) {
        throw new Error('Camera preview could not be loaded.');
      }
    } catch (error) {
      if (request === current) {
        image.hidden = true;
        placeholder.hidden = false;
        placeholder.textContent = 'Waiting for camera connection…';
      }
    } finally {
      clearTimeout(timeout);
      if (request === current) {
        request = null;
        if (!document.hidden && ['on', 'starting'].includes(state)) timer = setTimeout(preview, 100);
      }
    }
  }

  async function refresh() {
    if (polling || changing || document.hidden) return;
    polling = true;
    const currentRevision = revision;
    try {
      const response = await fetch('/api/camera/status', {cache: 'no-store', signal: AbortSignal.timeout(4000)});
      if (!response.ok) throw new Error('Camera status unavailable. Check that the updated app is running.');
      const status = await response.json();
      if (!changing && currentRevision === revision) render(status);
    } catch (error) {
      if (!changing && currentRevision === revision) {
        clearPreview();
        label.textContent = 'Disconnected';
        detail.textContent = error.message;
        placeholder.textContent = 'Waiting for UNO Q…';
      }
    } finally { polling = false; }
  }

  button.onclick = async () => {
    if (changing) return;
    const action = ['on', 'starting'].includes(state) ? 'stop' : 'start';
    revision++;
    changing = true;
    button.disabled = true;
    clearPreview();
    try {
      const response = await fetch('/api/camera/' + action, {method: 'POST', signal: AbortSignal.timeout(5000)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Camera request failed.');
      changing = false;
      render(result);
    } catch (error) {
      detail.textContent = error.message;
    } finally {
      changing = false;
      button.disabled = state === 'stopping';
      refresh();
    }
  };

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearPreview();
    else refresh();
  });
  window.addEventListener('pagehide', clearPreview);
  setInterval(refresh, 1000);
  refresh();
})();
