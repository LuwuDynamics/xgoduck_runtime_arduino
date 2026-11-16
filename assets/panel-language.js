/* Switch the current panel without reloading or touching device state. */
(() => {
  const systemChinese = () => /^zh(?:[-_]|$)/i.test(navigator.languages?.[0] || navigator.language || 'en');
  let chinese = systemChinese();
  const t = (en, zh) => chinese ? zh : en;
  const button = document.getElementById('language');
  const labels = new Map();
  for (const element of document.querySelectorAll('[data-zh]')) {
    const en = element.textContent;
    labels.set(element, () => t(en, element.dataset.zh));
  }
  const attributes = ['alt', 'aria-label', 'title'].flatMap(attr =>
    [...document.querySelectorAll(`[data-zh-${attr}]`)].map(element =>
      [element, attr, element.getAttribute(attr), element.getAttribute('data-zh-' + attr)]));

  function renderLabel(element, label) {
    const value = label();
    if (element.textContent !== value) element.textContent = value;
  }

  function setText(id, label) {
    const element = document.getElementById(id);
    labels.set(element, label);
    renderLabel(element, label);
  }

  function render() {
    document.documentElement.lang = t('en', 'zh-CN');
    for (const [element, label] of labels) renderLabel(element, label);
    for (const [element, attr, en, zh] of attributes) {
      const value = t(en, zh);
      if (value === null) element.removeAttribute(attr);
      else element.setAttribute(attr, value);
    }
    button.textContent = t('中文', 'EN');
    button.title = t('切换为中文', 'Switch to English');
    button.setAttribute('aria-label', button.title);
  }

  button.onclick = () => { chinese = !chinese; render(); };
  window.addEventListener('languagechange', () => { chinese = systemChinese(); render(); });
  window.DuckPanelLanguage = { t, setText, get chinese() { return chinese; } };
  render();
})();
