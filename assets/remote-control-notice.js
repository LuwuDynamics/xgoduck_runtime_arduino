/* Shared first-use reminder for the control page and the cockpit. */
(() => {
  const key = 'xgo_duck_rear_cover_ack';
  const copy = {
    zh: ['遥控前建议安装后部保护壳', '小鸭在遥控过程中若跌倒或发生碰撞，裸露的开发板可能因直接撞击而受损。', '知道了'],
    en: ['We recommend fitting the rear protective cover before driving',
      'Duck may fall or collide with objects during remote control. An impact can damage the exposed board.', 'Got it']
  };
  let accepted = false, dialog;

  function remember() {
    accepted = true;
    // Cookies are shared by the two ports on the same Duck address.
    try { document.cookie = `${key}=1; Max-Age=31536000; Path=/; SameSite=Lax`; } catch {}
    try { localStorage.setItem(key, '1'); } catch {}
  }

  function allow() {
    if (dialog?.open) return false;
    if (accepted) return true;
    try {
      if (document.cookie.split(/;\s*/).includes(`${key}=1`)) return true;
    } catch {}
    try {
      if (localStorage.getItem(key) === '1') return true;
    } catch {}

    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'remote-control-notice';
      dialog.setAttribute('aria-labelledby', 'rear-cover-title');
      dialog.setAttribute('aria-describedby', 'rear-cover-description');
      dialog.innerHTML = `<h2 id="rear-cover-title" tabindex="-1" autofocus></h2>
        <p id="rear-cover-description"></p><form method="dialog"><button type="submit"></button></form>`;
      dialog.querySelector('form').addEventListener('submit', remember);
      dialog.addEventListener('keydown', ignoreRepeatedEnter);
      document.body.append(dialog);
    }
    const [title, description, button] = copy[document.documentElement.lang.startsWith('zh') ? 'zh' : 'en'];
    dialog.querySelector('h2').textContent = title;
    dialog.querySelector('p').textContent = description;
    dialog.querySelector('button').textContent = button;
    dialog.showModal();
    return false;
  }

  function ignoreRepeatedEnter(event) {
    if (event.repeat && event.key === 'Enter') event.preventDefault();
  }

  function bindButton(button) {
    if (!button) return;
    button.addEventListener('keydown', ignoreRepeatedEnter);
    button.addEventListener('click', event => {
      if (allow()) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }

  window.DuckRemoteNotice = {allow, bindButton};
})();
