/* 9527 hardware reminder. Only an explicit confirmation enables later arm clicks. */
(() => {
  const key = 'xgo_duck_hardware_check_v1';
  const stop = document.getElementById('stop');
  let choice = '', dialog;

  function savedChoice() {
    if (choice === 'confirmed') return choice;
    try { return localStorage.getItem(key) || choice; } catch { return choice; }
  }

  function dismiss(result) {
    if (result === 'confirmed' || savedChoice() !== 'confirmed') {
      choice = result;
      try { localStorage.setItem(key, result); } catch {}
      if (result === 'confirmed') {
        // This inspection also covers the cockpit's earlier rear-cover reminder.
        try { document.cookie = 'xgo_duck_rear_cover_ack=1; Max-Age=31536000; Path=/; SameSite=Lax'; } catch {}
      }
    }
    dialog.close();
  }

  function show() {
    if (dialog?.open) return;
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'hardware-check';
      dialog.setAttribute('aria-labelledby', 'hardware-check-title');
      document.body.append(dialog);
      dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss('later'); });
      dialog.addEventListener('keydown', event => {
        if (event.repeat && event.key === 'Enter') event.preventDefault();
      });
    }
    const t = (en, zh) => document.documentElement.lang.startsWith('zh') ? zh : en;
    dialog.innerHTML = `
      <div class="hardware-heading"><h2 id="hardware-check-title" tabindex="-1" autofocus>${t('Check the hardware before moving Duck', '运动前，请检查扩展板和保护壳')}</h2>
        ${stop ? '<button class="stop" type="button" data-release>' + t('Torque off', '舵机卸力') + '</button>' : ''}</div>
      <div class="hardware-body">
        <p class="hardware-risk">${t('A fall can bring the expansion board into contact with the UNO Q and cause a short circuit. Switch off Duck and disconnect the battery and USB before handling the boards.', '跌倒碰撞时，扩展板可能与 UNO Q 上的引脚接触，造成短路。拆装前请先关机，并断开电池和 USB 供电。')}</p>
        <div class="hardware-steps">
          <figure><a class="hardware-picture" href="hardware-insulation.webp" target="_blank" rel="noopener"><img src="hardware-insulation.webp" width="1208" height="910" alt="${t('Yellow outline showing where to apply tape on the underside of the expansion board', '黄框标出了扩展板背面要贴绝缘胶带的位置')}"></a>
            <figcaption><h3>${t('V1.1 and earlier · Insulate the board', 'V1.1 及之前的扩展板 · 贴绝缘胶带')}</h3>
            <p>${t('Cover the yellow-outlined area on the underside with electrical insulating tape, then reconnect the boards. Later revisions with a cutout here can skip this step.', '用电工绝缘胶带覆盖图中黄框区域，再插接两块板。后续版本若已在此处开孔，可跳过这一步。')}</p></figcaption></figure>
          <figure><a class="hardware-picture" href="hardware-rear-cover.webp" target="_blank" rel="noopener"><span class="hardware-cover"><img src="hardware-rear-cover.webp" width="686" height="950" alt="${t('Rear protective cover with the mounting screw hole circled', '保护壳实物样式，圆圈标出固定螺丝孔')}"></span></a>
            <figcaption><h3>${t('Secure the rear cover', '固定后部保护壳')}</h3>
            <p>${t('Use a rear cover with screw holes at the bottom. Fit the cover and fasten it with screws before moving Duck.', '使用底部带螺丝孔的后部保护壳。装好后，用螺丝将保护壳固定牢靠，再让小鸭运动。')}</p></figcaption></figure>
        </div>
        <details><summary>${t('See where the boards can touch', '查看可能短路的接触位置')}</summary>
          <img src="hardware-contact.webp" loading="lazy" width="968" height="796" alt="${t('Arrows indicating the possible contact points', '红色箭头标出了可能接触的位置')}"></details>
      </div>
      <div class="hardware-choices"><button type="button" data-later>${t('Check later', '稍后检查')}</button>
        <button type="button" data-confirm>${t('I checked my hardware', '已按板型完成检查')}</button></div>`;
    dialog.querySelector('[data-later]').onclick = () => dismiss('later');
    dialog.querySelector('[data-confirm]').onclick = () => dismiss('confirmed');
    if (stop) dialog.querySelector('[data-release]').onclick = () => { dismiss('later'); stop.click(); };
    dialog.showModal();
  }

  for (const button of document.querySelectorAll('[data-hardware-arm]')) {
    button.addEventListener('click', event => {
      if (savedChoice() === 'confirmed') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      show();
    }, true);
    button.addEventListener('keydown', event => {
      if (event.repeat && event.key === 'Enter') event.preventDefault();
    });
  }
  if (!savedChoice()) show();
})();
