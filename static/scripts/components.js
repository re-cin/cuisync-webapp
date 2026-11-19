class AppNav extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <header class="navbar" id="navbar">
        <a href="/home"><div class="logo">CUISYNC</div></a>

        <!-- Login / profile pill: at the far right on desktop; on phones it stays at the top
             (the page links move to the bottom bar) -->
        <a href="/login" class="top-account-btn" id="top-account-btn">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
          <span>Log in</span>
        </a>
        
        <button class="hamburger-toggle" id="hamburger-btn" aria-label="Toggle Navigation Menu" aria-controls="nav-menu" aria-expanded="false">
          <span class="hamburger-line"></span>
          <span class="hamburger-line"></span>
          <span class="hamburger-line"></span>
        </button>

        <nav class="nav-links" id="nav-menu">
          <a href="/home">Home</a>
          <a href="/analysis">Statistics</a>
          <a href="/dictionary">Dishes</a>
          <a href="/faqs">FAQs</a>
          <a href="/login" class="nav-account" id="nav-account">Log in</a>
        </nav>
      </header>
      <div class="nav-overlay" id="nav-overlay"></div>

      <!-- Phones: bottom navigation bar -->
      <nav class="bottom-nav" aria-label="Main">
        <a href="/home" class="bottom-nav-item" data-match="/home /">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5"/></svg>
          <span>Home</span>
        </a>
        <a href="/analysis" class="bottom-nav-item" data-match="/analysis">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/></svg>
          <span>Statistics</span>
        </a>
        <a href="/dictionary" class="bottom-nav-item" data-match="/dictionary">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/></svg>
          <span>Dishes</span>
        </a>
        <a href="/faqs" class="bottom-nav-item" data-match="/faqs">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.5"/><path d="M9.2 9.2a2.9 2.9 0 0 1 5.6 1c0 1.9-2.8 2.6-2.8 4.3"/><path d="M12 17.6h.01"/></svg>
          <span>FAQs</span>
        </a>
      </nav>
    `;
    document.documentElement.classList.add('has-bottom-nav');

    // Set up the navbar right after it's added
    this.initNavigation();
    this.initAccount();
  }

  // Login / profile links (panel revision: user pages). The admin is a separate app (admin_app.py).
  initAccount() {
    const path = window.location.pathname;
    this.querySelectorAll('.nav-links a').forEach(link => {
      const href = link.getAttribute('href');
      if (href === path || (href === '/home' && path === '/')) link.classList.add('active');
    });
    this.querySelectorAll('.bottom-nav-item').forEach(link => {
      const on = link.dataset.match.split(' ').some(p => p === path || (p !== '/' && path.startsWith(p + '/')));
      link.classList.toggle('active', on);
      if (on) link.setAttribute('aria-current', 'page');
    });
    const topAccount = this.querySelector('#top-account-btn');
    if (path.startsWith('/login') || path.startsWith('/register')) topAccount && topAccount.classList.add('active');
    const account = this.querySelector('#nav-account');
    fetch('/api/me', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null)
      .then(me => {
        if (!me || !me.logged_in || !account) return;
        if (topAccount) {
          topAccount.href = '/profile';
          topAccount.querySelector('span').textContent = 'Profile';
          topAccount.classList.toggle('active', path.startsWith('/profile'));
        }
        account.textContent = 'Profile';
        account.href = '/profile';
        if (path.startsWith('/profile')) account.classList.add('active');
        // Logged in: the pill becomes an account menu (avatar + name + arrow).
        // Search history, Account settings and Log out live inside it.
        if (topAccount) this.buildAccountMenu(topAccount, me, path);
      })
      .catch(() => {});
  }

  buildAccountMenu(topAccount, me, path) {
    const esc = (t) => String(t || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const name = me.name || me.username || 'Account';
    const wrap = document.createElement('div');
    wrap.className = 'acct-menu' + (path.startsWith('/profile') ? ' is-current' : '');
    wrap.innerHTML = `
      <button type="button" class="acct-btn" aria-haspopup="menu" aria-expanded="false" aria-label="Account menu for ${esc(name)}">
        <span class="acct-avatar" aria-hidden="true">${esc(name.charAt(0).toUpperCase())}</span>
        <span class="acct-name">${esc(name)}</span>
        <svg class="acct-chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
      </button>
      <div class="acct-panel" role="menu" hidden>
        <div class="acct-who">
          <span class="acct-who-name">${esc(name)}</span>
          ${me.username ? `<span class="acct-who-user">@${esc(me.username)}</span>` : ''}
        </div>
        <a role="menuitem" href="/profile#history">Search history</a>
        <a role="menuitem" href="/profile?open=account">Account settings</a>
        <form method="post" action="/logout" class="acct-logout-form">
          <button type="submit" role="menuitem" class="acct-logout">Log out</button>
        </form>
      </div>`;
    topAccount.replaceWith(wrap);
    const btn = wrap.querySelector('.acct-btn');
    const panel = wrap.querySelector('.acct-panel');
    const setOpen = (open) => {
      panel.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
      wrap.classList.toggle('is-open', open);
    };
    btn.addEventListener('click', (e) => { e.stopPropagation(); setOpen(panel.hidden); });
    document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) setOpen(false); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) { setOpen(false); btn.focus(); } });
    panel.querySelectorAll('a').forEach(a => a.addEventListener('click', () => setOpen(false)));
  }

  initNavigation() {
    const navbar = this.querySelector('#navbar');
    const hamburgerBtn = this.querySelector('#hamburger-btn');
    const navMenu = this.querySelector('#nav-menu');
    const navOverlay = this.querySelector('#nav-overlay');

    // Sticky navbar on scroll (also runs on load in case the page starts scrolled)
    const onScroll = () => {
      navbar && navbar.classList.toggle('scrolled', window.scrollY > 40);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();

    // Mobile menu open/close
    if (hamburgerBtn && navMenu && navOverlay) {
      const setMenu = (open) => {
        hamburgerBtn.classList.toggle('open', open);
        navMenu.classList.toggle('active', open);
        navOverlay.classList.toggle('active', open);
        hamburgerBtn.setAttribute('aria-expanded', String(open));
        // Class on <html> so it also works on iOS Safari
        document.documentElement.classList.toggle('menu-open', open);
      };
      const toggleMenu = () => setMenu(!navMenu.classList.contains('active'));

      hamburgerBtn.addEventListener('click', toggleMenu);
      navOverlay.addEventListener('click', () => setMenu(false));

      this.querySelectorAll('.nav-links a').forEach(link => {
        link.addEventListener('click', () => setMenu(false));
      });

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setMenu(false);
      });

      // Close the menu if the screen gets wider than mobile
      window.matchMedia('(min-width: 821px)').addEventListener('change', (e) => {
        if (e.matches) setMenu(false);
      });
    }
  }
}

class AppFooter extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <footer class="footer">
        <div class="footer-container">
          
          <div class="footer-col footer-brand">
            <h3 class="footer-logo">CUISYNC</h3>
            <p>
              Developed for academic research on Asian cuisine similarities. 
              This evidence-based platform is AI-powered; content is for 
              informational and research purposes only.
            </p>
            <div class="brand-underline"></div>
          </div>

          <div class="footer-col">
            <h4 class="footer-heading">EXPLORE</h4>
            <ul class="footer-links">
              <li><a href="/home">Home</a></li>
              <li><a href="/analysis">Statistics</a></li>
              <li><a href="/dictionary">Dishes</a></li>
              <li><a href="/faqs">FAQs</a></li>
              <li><a href="/privacy">Privacy Policy</a></li>
            </ul>
          </div>

          <div class="footer-col">
            <h4 class="footer-heading">DATASET COVERAGE</h4>
            <p class="footer-dataset-desc">
              Our research dataset analyzes recipes across Asian cuisines, incorporating culinary data from 
              Cambodia, China, India, Indonesia, Japan, Malaysia, Myanmar, Philippines, Saudi Arabia, 
              South Korea, Thailand, Turkey, and Vietnam.
            </p>
          </div>

        </div>

        <div class="footer-bottom">
          <p>© 2025 <span>Cuisync</span>. All rights reserved.</p>
        </div>
      </footer>
    `;
  }
}

customElements.define('app-nav', AppNav);
customElements.define('app-footer', AppFooter);

/* ------------------------------------------------------------------
   Busy screen: while a search runs (or the page is loading the results),
   the button reads "Searching..." and a translucent tint covers the page
   so nothing can be clicked twice.
     CuiSyncBusy.start(button, label?)   CuiSyncBusy.stop()
   ------------------------------------------------------------------ */
window.CuiSyncBusy = (() => {
  let overlay = null;
  let busyButtons = [];
  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'busy-overlay';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML = '<div class="busy-box">'
      + '<span class="busy-spinner" aria-hidden="true"></span>'
      + '<span class="busy-words"><span class="busy-text">Searching...</span>'
      + '<span class="busy-time" aria-hidden="true">0 seconds</span></span>'
      + '</div>';
    // Swallow every click while busy
    overlay.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); });
    document.body.appendChild(overlay);
    return overlay;
  }
  // Seconds counting up under the label
  let timer = null;
  let startedAt = 0;
  function tick() {
    const n = Math.floor((Date.now() - startedAt) / 1000);
    overlay.querySelector('.busy-time').textContent = `${n} second${n === 1 ? '' : 's'}`;
  }
  // Searches that fetch (not a page load) listen to this to stop their request
  let controller = new AbortController();
  function start(button, label = 'Searching...') {
    const o = ensureOverlay();
    o.querySelector('.busy-text').textContent = label;
    if (!o.classList.contains('is-on')) {
      if (controller.signal.aborted) controller = new AbortController();
      startedAt = Date.now();
      tick();
      clearInterval(timer);
      timer = setInterval(tick, 1000);
    }
    if (button && !button.dataset.busyLabel) {
      button.dataset.busyLabel = button.innerHTML;
      button.textContent = label;
      button.disabled = true;
      button.classList.add('is-busy');
      busyButtons.push(button);
    }
    document.documentElement.classList.add('is-busy');
    o.classList.add('is-on');
  }
  // The browser's stop button (or Esc): stop the page load or the request and go back to the page
  function cancel() {
    if (!document.documentElement.classList.contains('is-busy')) return;
    controller.abort();
    try { window.stop(); } catch (e) { /* old browsers */ }
    stop();
    window.dispatchEvent(new CustomEvent('cuisync:cancel'));
  }
  function stop() {
    clearInterval(timer);
    timer = null;
    busyButtons.forEach((b) => {
      if (b.dataset.busyLabel !== undefined) b.innerHTML = b.dataset.busyLabel;
      delete b.dataset.busyLabel;
      b.disabled = false;
      b.classList.remove('is-busy');
    });
    busyButtons = [];
    document.documentElement.classList.remove('is-busy');
    if (overlay) overlay.classList.remove('is-on');
  }
  // Coming back with the browser's Back button (page restored from cache): not busy any more
  // The browser's own stop button (the x in place of reload) cancels the page that was loading:
  // browsers with the Navigation API report it as a failed navigation, so the busy screen goes
  // away and the page can be used again
  if (window.navigation) {
    window.navigation.addEventListener('navigateerror', () => {
      if (document.documentElement.classList.contains('is-busy')) cancel();
    });
  }
  // (only then: a normal page load also fires pageshow, after a search may already have started)
  window.addEventListener('pageshow', (e) => { if (e.persisted) stop(); });
  document.addEventListener('keydown', (e) => {
    if (!document.documentElement.classList.contains('is-busy')) return;
    if (e.key === 'Escape') { e.preventDefault(); cancel(); return; }
    if (e.key !== 'Tab') e.preventDefault();
  }, true);

  // Every link that opens a comparison or a search (map pins and list, search history,
  // "Find similar dishes", ...) and every normal form sent to a search page shows the
  // busy screen while the next page loads.
  // (/history/<id> opens a past search from "My search history"; /history/<id>/delete doesn't count)
  const SEARCH_PAGES = /^\/(?:(?:compare|similar-dishes|search-text|search-image)(?:\/|$)|history\/\d+\/?$)/;
  const isSearchUrl = (url) => url.origin === location.origin
    && (SEARCH_PAGES.test(url.pathname) || (url.pathname === '/' && url.searchParams.has('dish')));
  // Only short text buttons/links change their label; cards and map pins keep theirs
  function labelTarget(el) {
    if (el.querySelector('img, .rmap-pin-rank') || el.classList.contains('rmap-pin')) return null;
    return el.textContent.trim().length <= 40 ? el : null;
  }
  document.addEventListener('click', (e) => {
    // Runs after the element's own handlers: a click they cancelled (a map drag, a pin tap
    // that only scrolls the list on phones) doesn't count
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest('a[href]');
    if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
    const url = new URL(a.href, location.href);
    if (!isSearchUrl(url)) return;
    if (url.pathname === location.pathname && url.search === location.search) return;   // this page
    start(labelTarget(a));
  });
  document.addEventListener('submit', (e) => {
    if (e.defaultPrevented) return;
    const form = e.target;
    if ((form.method || '').toLowerCase() === 'dialog') return;
    if (!isSearchUrl(new URL(form.action, location.href))) return;
    start(e.submitter || form.querySelector('button[type="submit"]'));
  });

  return { start, stop, cancel, get signal() { return controller.signal; } };
})();

/* ==============================
   REUSABLE INFO TOOLTIP
============================== */

document.addEventListener('click', (event) => {
    const trigger = event.target.closest('.info-tooltip-trigger');

    // Close tooltips when clicking outside
    if (!trigger) {
        document.querySelectorAll('.info-tooltip.is-open').forEach((tooltip) => {
            if (!tooltip.contains(event.target)) {
                tooltip.classList.remove('is-open');

                const button = tooltip.querySelector('.info-tooltip-trigger');
                button?.setAttribute('aria-expanded', 'false');
            }
        });

        return;
    }

    const tooltip = trigger.closest('.info-tooltip');
    const isOpen = tooltip.classList.contains('is-open');

    // Close other open tooltips
    document.querySelectorAll('.info-tooltip.is-open').forEach((item) => {
        item.classList.remove('is-open');
        item.querySelector('.info-tooltip-trigger')
            ?.setAttribute('aria-expanded', 'false');
    });

    // Toggle selected tooltip
    if (!isOpen) {
        tooltip.classList.add('is-open');
        trigger.setAttribute('aria-expanded', 'true');
    }
});

// Close tooltips using Escape
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        document.querySelectorAll('.info-tooltip.is-open').forEach((tooltip) => {
            tooltip.classList.remove('is-open');
            tooltip.querySelector('.info-tooltip-trigger')
                ?.setAttribute('aria-expanded', 'false');
        });
    }
});
