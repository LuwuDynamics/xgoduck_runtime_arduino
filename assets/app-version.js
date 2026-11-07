/* Show the installed App version beside the page's status or title. */
(async () => {
  const badge = document.querySelector('[data-app-version]');
  if (!badge) return;

  try {
    const response = await fetch('/api/version', {cache: 'no-store'});
    if (!response.ok) return;
    const {version} = await response.json();
    if (typeof version !== 'string' || !version) return;
    badge.textContent = 'v' + version;
    badge.hidden = false;
  } catch {
    // Keep the badge hidden when the version cannot be read.
  }
})();
