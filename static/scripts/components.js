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
        const out = document.createElement('a');
        out.href = '/logout';
        out.textContent = 'Log out';
        account.after(out);
      })
      .catch(() => {});
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