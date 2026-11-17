/* Country picker: turns <select data-country-picker> into a card that matches the
   "Selected dish" card, with a list that opens under it.
   The select stays in the form (hidden) and keeps sending the value. */
(function () {
  'use strict';

  const GLOBE = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>';
  const SPARK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.8L20 10.7l-5 3.9 1.6 6.4L12 17.3 7.4 21l1.6-6.4-5-3.9 6.1-1.9z"></path></svg>';

  function esc(t) {
    return String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function build(select) {
    const options = [...select.options].filter((o) => o.value);
    const wrap = document.createElement('div');
    wrap.className = 'country-picker';
    select.classList.add('country-picker-native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    const listId = (select.id || 'country') + '-picker-list';
    wrap.insertAdjacentHTML('afterbegin', `
      <button type="button" class="country-picker-card is-empty" aria-haspopup="listbox" aria-expanded="false" aria-controls="${listId}">
        <span class="country-picker-icon" aria-hidden="true">${GLOBE}</span>
        <span class="country-picker-info">
          <span class="selected-dish-label">FIND A SIMILAR DISH IN</span>
          <strong class="country-picker-name">Choose a Target country</strong>
          <span class="country-picker-hint">Click to choose</span>
        </span>
        <span class="country-picker-arrow" aria-hidden="true">&rsaquo;</span>
      </button>
      <div class="country-picker-panel" hidden>
        <!-- Header only shows on phones, where the list opens as a popup -->
        <div class="country-picker-head">
          <span class="country-picker-head-title">Find a similar dish in</span>
          <button type="button" class="country-picker-close" aria-label="Close">&times;</button>
        </div>
        <ul class="country-picker-list" id="${listId}" role="listbox"></ul>
      </div>`);

    const card = wrap.querySelector('.country-picker-card');
    const nameEl = wrap.querySelector('.country-picker-name');
    const hintEl = wrap.querySelector('.country-picker-hint');
    const panel = wrap.querySelector('.country-picker-panel');
    const list = wrap.querySelector('.country-picker-list');
    let active = -1;
    let pendingDetail = null;

    function render() {
      list.innerHTML = options.map((o) => {
        const isAny = o.value === 'any';
        const sel = o.value === select.value;
        return `<li role="option" data-value="${esc(o.value)}" aria-selected="${sel}"
                    class="country-picker-option${isAny ? ' is-any' : ''}${sel ? ' is-selected' : ''}">
                  ${isAny ? `<span class="country-picker-any-icon" aria-hidden="true">${SPARK}</span>
                    <span><strong>Any country</strong><small>Best match across all countries</small></span>`
                  : `<span>${esc(o.textContent)}</span>`}
                  ${sel ? '<span class="country-picker-check" aria-hidden="true">&#10003;</span>' : ''}
                </li>`;
      }).join('');
      active = -1;
    }

    function setActive(i) {
      const items = list.querySelectorAll('.country-picker-option');
      if (!items.length) return;
      active = (i + items.length) % items.length;
      items.forEach((el, n) => el.classList.toggle('is-active', n === active));
      items[active].scrollIntoView({ block: 'nearest' });
    }

    function updateCard() {
      const o = select.options[select.selectedIndex];
      const has = Boolean(select.value && o);
      card.classList.toggle('is-empty', !has);
      if (!has) {
        nameEl.textContent = 'Choose a Target country';
        hintEl.textContent = 'Click to choose';
      } else if (select.value === 'any') {
        nameEl.textContent = 'Any country';
        hintEl.textContent = 'Best match across all countries · Click to change';
      } else {
        nameEl.textContent = o.textContent;
        hintEl.textContent = 'Click to change';
      }
    }

    let justOpened = false;
    function open(detail) {
      pendingDetail = detail || null;
      // Opened from another click (e.g. picking a dish in the popup): don't let that same
      // click close it again when it reaches the page
      justOpened = true;
      setTimeout(() => { justOpened = false; }, 0);
      panel.hidden = false;
      // Phones: the list is a popup on top of everything (moved to <body> so nothing
      // on the page can cover it), and the page behind it shouldn't scroll
      if (phone.matches) {
        document.body.appendChild(panel);
        document.documentElement.classList.add('country-picker-open');
      }
      card.setAttribute('aria-expanded', 'true');
      wrap.classList.add('is-open');
      render();
      const sel = list.querySelector('.is-selected');
      if (sel) sel.scrollIntoView({ block: 'nearest' });
      if (!phone.matches) wrap.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    const phone = window.matchMedia('(max-width: 600px)');
    function close() {
      document.documentElement.classList.remove('country-picker-open');
      panel.hidden = true;
      if (panel.parentNode !== wrap) wrap.insertBefore(panel, select);
      card.setAttribute('aria-expanded', 'false');
      wrap.classList.remove('is-open');
    }

    function pick(value) {
      select.value = value;
      updateCard();
      close();
      card.focus();
      select.dispatchEvent(new CustomEvent('change', { bubbles: true, detail: pendingDetail }));
      pendingDetail = null;
    }

    card.addEventListener('click', () => (panel.hidden ? open() : close()));
    wrap.querySelector('.country-picker-close').addEventListener('click', () => { close(); card.focus(); });
    // Tapping the dark area around the phone popup closes it
    panel.addEventListener('click', (e) => { if (e.target === panel) close(); });
    list.addEventListener('click', (e) => {
      const li = e.target.closest('.country-picker-option');
      if (li) pick(li.dataset.value);
    });
    wrap.addEventListener('keydown', (e) => {
      if (panel.hidden) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
      else if ((e.key === 'Enter' || e.key === ' ') && active >= 0) {
        e.preventDefault();
        pick(list.querySelectorAll('.country-picker-option')[active].dataset.value);
      } else if (e.key.length === 1 && /[a-z]/i.test(e.key)) {
        // Type a letter to jump to the first country that starts with it
        const items = [...list.querySelectorAll('.country-picker-option')];
        const i = items.findIndex((li) => li.textContent.trim().toLowerCase().startsWith(e.key.toLowerCase()));
        if (i >= 0) setActive(i);
      } else if (e.key === 'Escape') { e.stopPropagation(); close(); card.focus(); }
    });
    document.addEventListener('click', (e) => {
      if (!justOpened && !wrap.contains(e.target) && !panel.contains(e.target)) close();
    });

    // Value set from code (e.g. the low-similarity popup) -> refresh the card
    select.addEventListener('change', updateCard);
    select.addEventListener('picker:open', (e) => open(e.detail));
    // Mirror the "needs attention" highlight from the select onto the card
    new MutationObserver(() => {
      card.classList.toggle('needs-attention', select.classList.contains('needs-attention'));
    }).observe(select, { attributes: true, attributeFilter: ['class'] });

    updateCard();
  }

  function init() {
    document.querySelectorAll('select[data-country-picker]').forEach(build);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
