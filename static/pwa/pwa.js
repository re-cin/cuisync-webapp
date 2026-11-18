/* Registers the service worker and shows an "Install CuiSync" button when the browser allows it.
   iPhone/iPad have no install prompt: there the user taps Share -> "Add to Home Screen". */
(() => {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((error) => {
        console.warn('[CuiSync] Service worker not registered:', error);
      });
    });
  }

  const DISMISS_KEY = 'cuisync-install-dismissed';
  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
  let deferredPrompt = null;

  function dismissedRecently() {
    try {
      const at = Number(localStorage.getItem(DISMISS_KEY) || 0);
      return Date.now() - at < 7 * 24 * 60 * 60 * 1000;   // ask again after a week
    } catch (e) {
      return false;
    }
  }

  function showInstallChip() {
    if (standalone || dismissedRecently() || document.getElementById('pwa-install-chip')) return;
    const chip = document.createElement('div');
    chip.id = 'pwa-install-chip';
    chip.className = 'pwa-install-chip';
    chip.innerHTML = `
      <img src="/static/pwa/icons/icon-192.png" alt="" width="36" height="36">
      <span class="pwa-install-text"><strong>Install CuiSync</strong><small>Open it like an app from your home screen</small></span>
      <button type="button" class="pwa-install-btn">INSTALL</button>
      <button type="button" class="pwa-install-close" aria-label="Not now">&times;</button>`;
    document.body.appendChild(chip);
    requestAnimationFrame(() => chip.classList.add('show'));

    chip.querySelector('.pwa-install-btn').addEventListener('click', async () => {
      chip.remove();
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      await deferredPrompt.userChoice.catch(() => null);
      deferredPrompt = null;
    });
    chip.querySelector('.pwa-install-close').addEventListener('click', () => {
      try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch (e) { /* private mode */ }
      chip.remove();
    });
  }

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();          // show our own button instead of the browser's mini bar
    deferredPrompt = event;
    showInstallChip();
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    const chip = document.getElementById('pwa-install-chip');
    if (chip) chip.remove();
  });
})();
