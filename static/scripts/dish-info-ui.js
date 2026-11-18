/* Comparison page, Culture & history and Health info tabs: easier to read.
   - Long history/culture text is cut to a few lines with "Read more".
   - On phones (one column), a "Your dish | Most similar dish" switch shows one dish at a
     time instead of two very long columns one after the other. */
(function () {
  'use strict';

  const phone = window.matchMedia('(max-width: 850px)');

  // ---------- Read more / Read less for long history and culture text ----------
  function setupReadMore() {
    document.querySelectorAll('.rt-background-summary').forEach((p) => {
      if (p.dataset.readMore) return;
      p.dataset.readMore = '1';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'rt-read-more';
      btn.hidden = true;
      p.after(btn);
      const update = () => {
        const open = p.classList.contains('is-open');
        // Measure with the clamp on: only offer "Read more" when the text is really longer
        p.classList.add('is-clamped');
        const long = p.scrollHeight > p.clientHeight + 4;
        p.classList.toggle('is-clamped', !open);
        btn.hidden = !long;
        btn.textContent = open ? 'Read less ▴' : 'Read more ▾';
        btn.setAttribute('aria-expanded', String(open));
      };
      btn.addEventListener('click', () => { p.classList.toggle('is-open'); update(); });
      p._updateReadMore = update;
      update();
    });
  }

  // ---------- "Your dish | Most similar dish" switch on phones ----------
  function setupSwitches() {
    document.querySelectorAll('.rt-columns--split').forEach((cols, n) => {
      const columns = Array.from(cols.children).filter((c) => c.classList.contains('rt-column'));
      if (columns.length < 2 || cols.previousElementSibling?.classList.contains('rt-dish-switch')) return;
      const sw = document.createElement('div');
      sw.className = 'rt-dish-switch';
      sw.setAttribute('role', 'tablist');
      sw.setAttribute('aria-label', 'Choose a dish');
      columns.forEach((col, i) => {
        const name = col.querySelector('.rt-dish-name')?.textContent.trim() || '';
        const b = document.createElement('button');
        b.type = 'button';
        b.setAttribute('role', 'tab');
        b.innerHTML = `<small>${i === 0 ? 'Your dish' : 'Most similar'}</small><span></span>`;
        b.querySelector('span').textContent = name;
        b.addEventListener('click', () => show(i));
        sw.appendChild(b);
      });
      cols.before(sw);
      function show(i) {
        columns.forEach((col, k) => col.classList.toggle('is-switched-away', k !== i));
        sw.querySelectorAll('button').forEach((b, k) => {
          b.classList.toggle('is-active', k === i);
          b.setAttribute('aria-selected', String(k === i));
        });
        // The clamp needs a visible element to measure
        columns[i].querySelectorAll('.rt-background-summary').forEach((p) => p._updateReadMore && p._updateReadMore());
      }
      show(0);
    });
  }

  function refresh() {
    document.querySelectorAll('.rt-background-summary').forEach((p) => p._updateReadMore && p._updateReadMore());
  }

  // Country / market picker in "Compare allergen labeling" (the comparison page also has
  // its own copy of this in results.html)
  function setupMarkets(root) {
    (root || document).querySelectorAll('.rt-market-comparison').forEach((panel) => {
      const selector = panel.querySelector('.rt-market-select');
      if (!selector || selector.dataset.marketReady) return;
      selector.dataset.marketReady = '1';
      const update = () => {
        const code = selector.value;
        panel.querySelectorAll('[data-market-code]').forEach((el) => { el.hidden = el.dataset.marketCode !== code; });
        const visible = Array.from(panel.querySelectorAll('.rt-market-findings [data-market-code]')).some((el) => !el.hidden);
        const empty = panel.querySelector('.rt-market-empty');
        const profile = Array.from(panel.querySelectorAll('.rt-market-profile')).find((el) => !el.hidden);
        if (empty) {
          empty.textContent = profile?.dataset.coverage === 'specified'
            ? "No matching named ingredients for this market's documented categories. This does not mean the dish is allergen-free."
            : 'A detailed allergen list is not available for this country reference. See the potential allergens above.';
          empty.hidden = visible;
        }
      };
      selector.addEventListener('change', update);
      update();
    });
  }

  // Used by the Dish Dictionary popup after it loads this information
  window.CuiSyncDishInfo = {
    setup(root) { setupReadMore(); setupMarkets(root); refresh(); },
    refresh,
  };

  function init() {
    setupReadMore();
    setupSwitches();
    // Hidden tabs can't be measured: re-check when a tab opens or the screen size changes
    document.querySelectorAll('.rt-tab, .dp-tab').forEach((t) => t.addEventListener('click', () => requestAnimationFrame(refresh)));
    phone.addEventListener('change', refresh);
    window.addEventListener('resize', refresh);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
