(() => {
  const button = document.getElementById('full');
  const label = document.getElementById('fullLabel');
  const notice = document.getElementById('notice');
  const active = () => document.fullscreenElement || document.webkitFullscreenElement;
  let busy = false;
  const t = (key, fallback) => window.DuckI18n ? window.DuckI18n.t(key) : fallback;

  function sync() {
    const on = !!active();
    label.textContent = on ? t('exitFullscreen', 'Exit fullscreen') : t('fullscreen', 'Fullscreen');
    const action = on ? t('exitFullscreen', 'Exit fullscreen') : t('enterFullscreen', 'Enter fullscreen');
    button.setAttribute('aria-label', action);
    button.setAttribute('title', action);
    button.setAttribute('aria-pressed', String(on));
    if (!on) {
      try { screen.orientation?.unlock?.(); } catch {}
    }
  }

  function failed(error) {
    notice.hidden = false;
    notice.textContent = t('fullscreenError', 'Could not enter fullscreen. Open this page directly in Chrome and tap Fullscreen again.') +
      (error?.message ? ' (' + error.message + ')' : '');
  }

  // Keep this handler independent of camera/audio startup. The native request
  // must happen inside the user's click, before any asynchronous work.
  button.addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    try {
      if (active()) {
        const exit = document.exitFullscreen || document.webkitExitFullscreen;
        await exit.call(document);
      } else {
        const root = document.documentElement;
        const request = root.requestFullscreen || root.webkitRequestFullscreen;
        if (!request) throw Error(t('fullscreenUnsupported', 'This browser does not support fullscreen.'));
        notice.hidden = true;
        await request.call(root, {navigationUI: 'hide'});
        // Rotation lock is optional; it must never delay further button clicks.
        try { screen.orientation?.lock?.('landscape')?.catch(() => {}); } catch {}
      }
    } catch (error) {
      failed(error);
    } finally {
      busy = false;
      sync();
    }
  });
  document.addEventListener('fullscreenchange', sync);
  document.addEventListener('webkitfullscreenchange', sync);
  window.addEventListener('pageshow', sync);
  window.addEventListener('duck-languagechange', sync);
  sync();
})();
