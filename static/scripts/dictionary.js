// Dish Dictionary: every dish in the dataset with search, filters, sort and pages.
// Filters: country (any of them), tags to SHOW (all of them), and tags / allergens to
// leave out ("Don't show": a dish with any of them is hidden).
// The state lives in the link (?q=&country=&tag=&xtag=&xallergen=&sort=&page=), so a tag clicked anywhere
// in the system can open this page already filtered (/dictionary?tag=Grilled).
document.addEventListener('DOMContentLoaded', () => {
  const PAGE_SIZE = 12;
  const DISHES = JSON.parse(document.getElementById('dict-data').textContent);
  // Ingredient / action / cookware / utensil filters from the link: the server already found
  // which dishes match each one ({kind, label, value, ids})
  const ENTITIES = JSON.parse((document.getElementById('dict-entities') || {}).textContent || '[]')
    .map(e => ({ ...e, ids: new Set(e.ids) }));
  const NO_IMAGE = window.DICT_NO_IMAGE;
  // {ingredient: {label, options: [[name, count, [dish ids]], ...]}, action: ..., ...}
  const ENTITY_OPTIONS = JSON.parse((document.getElementById('dict-entity-options') || {}).textContent || '{}');

  const grid = document.getElementById('dict-grid');
  const cardTpl = document.getElementById('dict-card-template');
  const qInput = document.getElementById('dict-q');
  const sortSelect = document.getElementById('dict-sort');
  const countEl = document.getElementById('dict-count');
  const countWord = document.getElementById('dict-count-word');
  const emptyEl = document.getElementById('dict-empty');
  const activeRow = document.getElementById('dict-active');
  const activeChips = document.getElementById('dict-active-chips');
  const pager = document.getElementById('dict-pager');
  const pagerInfo = document.getElementById('dict-pager-info');
  const pagesNav = document.getElementById('dict-pages');

  const norm = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  DISHES.forEach(d => {
    d._name = norm(`${d.t} ${d.a}`);
    d._region = d.g.find(t => t.endsWith('Asia')) || '~';
  });
  const countryLabel = new Map(DISHES.map(d => [d.c, d.l]));

  // ------------------------------------------------------------------
  // State <-> link
  // ------------------------------------------------------------------
  const state = { q: '', countries: new Set(), tags: new Set(), xtags: new Set(), xallergens: new Set(),
                  entities: [], sort: 'az', page: 1 };
  const knownCountries = new Set(Array.from(document.querySelectorAll('input[name="country"]')).map(i => i.value));
  // Tags come from the dishes (card tags and ?tag= links still filter the page)
  const knownTags = new Set(DISHES.flatMap(d => d.g));
  const tagByLower = new Map(Array.from(knownTags).map(t => [t.toLowerCase(), t]));
  // No "Hide" dropdown on the page any more, but links like ?xallergen=Peanuts
  // (from the dish popup's allergen tags) still work: allergens come from the dishes
  const allergenByLower = new Map(DISHES.flatMap(d => d.x || []).map(a => [a.toLowerCase(), a]));

  function readLink() {
    const p = new URLSearchParams(location.search);
    state.q = p.get('q') || '';
    state.countries = new Set(p.getAll('country').map(c => c.toLowerCase()).filter(c => knownCountries.has(c)));
    // Tags match without caring about upper/lower case ("grilled" finds "Grilled")
    state.tags = new Set(p.getAll('tag').map(t => tagByLower.get(t.toLowerCase())).filter(Boolean));
    state.xtags = new Set(p.getAll('xtag').map(t => tagByLower.get(t.toLowerCase()))
      .filter(t => t && !state.tags.has(t)));
    state.xallergens = new Set(p.getAll('xallergen').map(a => allergenByLower.get(a.toLowerCase())).filter(Boolean));
    state.entities = [];
    ['ingredient', 'action', 'cookware', 'utensil'].forEach(kind => {
      p.getAll(kind).forEach(v => {
        const value = v.trim().toLowerCase().split(/\s+/).join(' ');
        if (!value || state.entities.some(e => e.kind === kind && e.value === value)) return;
        const f = entityFilter(kind, value);
        if (f) state.entities.push(f);
      });
    });
    state.sort = ['az', 'za', 'country', 'region'].includes(p.get('sort')) ? p.get('sort') : 'az';
    state.page = Math.max(1, parseInt(p.get('page'), 10) || 1);
  }

  function writeLink(push) {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    state.countries.forEach(c => p.append('country', c));
    state.tags.forEach(t => p.append('tag', t));
    state.xtags.forEach(t => p.append('xtag', t));
    state.xallergens.forEach(a => p.append('xallergen', a));
    state.entities.forEach(e => p.append(e.kind, e.value));
    if (state.sort !== 'az') p.set('sort', state.sort);
    if (state.page > 1) p.set('page', state.page);
    const url = `${location.pathname}${p.toString() ? '?' + p : ''}`;
    history[push ? 'pushState' : 'replaceState'](null, '', url);
  }

  // ------------------------------------------------------------------
  // Filtering + sorting
  // ------------------------------------------------------------------
  function filtered() {
    const words = norm(state.q).split(/\s+/).filter(Boolean);
    const list = DISHES.filter(d =>
      (!state.countries.size || state.countries.has(d.c)) &&
      Array.from(state.tags).every(t => d.g.includes(t)) &&
      !d.g.some(t => state.xtags.has(t)) &&
      !(d.x || []).some(a => state.xallergens.has(a)) &&
      state.entities.every(e => e.ids.has(d.id)) &&
      words.every(w => d._name.includes(w))
    );
    const byName = (a, b) => a.t.localeCompare(b.t);
    const sorters = {
      az: byName,
      za: (a, b) => b.t.localeCompare(a.t),
      country: (a, b) => a.l.localeCompare(b.l) || byName(a, b),
      region: (a, b) => a._region.localeCompare(b._region) || a.l.localeCompare(b.l) || byName(a, b),
    };
    return list.sort(sorters[state.sort]);
  }

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  function card(d) {
    const li = cardTpl.content.firstElementChild.cloneNode(true);
    const img = li.querySelector('img');
    img.onerror = () => { img.onerror = null; img.src = NO_IMAGE; };
    img.src = d.i || NO_IMAGE;
    li.querySelector('.dict-card-country').textContent = d.l;
    li.querySelector('.dict-card-title').textContent = d.t;
    const alt = li.querySelector('.dict-card-alt');
    if (d.a && d.a.toLowerCase() !== d.t.toLowerCase()) alt.textContent = d.a;
    else alt.remove();

    // Tags are clickable: they filter this page by that tag
    const tagBox = li.querySelector('.dict-card-tags');
    d.g.forEach((t, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'dict-tag' + (i === 0 && t === d._region ? ' is-region' : '') + (state.tags.has(t) ? ' is-active' : '');
      b.textContent = t;
      b.title = state.tags.has(t) ? `Filtering by ${t}` : `Show all dishes tagged ${t}`;
      b.addEventListener('click', (e) => {
        e.stopPropagation();   // a tag filters the page; the rest of the card opens the popup
        if (state.tags.has(t)) return;
        state.tags.add(t);
        state.page = 1;
        update(true, true);
      });
      tagBox.appendChild(b);
    });

    // The rest of the card opens the dish popup
    li.tabIndex = 0;
    li.setAttribute('role', 'button');
    li.setAttribute('aria-label', `${d.t}, ${d.l}. Open details`);
    li.addEventListener('click', () => openDish(d));
    li.addEventListener('keydown', (e) => {
      if (e.target !== li || (e.key !== 'Enter' && e.key !== ' ')) return;
      e.preventDefault();
      openDish(d);
    });
    return li;
  }

  // ------------------------------------------------------------------
  // Dish popup: image + name + tags + "Find similar dishes" on the left,
  // Recipe breakdown / Culture & history / Health info tabs on the right
  // (TEMPLATE content for now, from /api/dish/<id>)
  // ------------------------------------------------------------------
  const pop = document.getElementById('dp-overlay');
  const popDialog = pop.querySelector('.dp-dialog');
  const popBody = document.getElementById('dp-body');
  const popTabs = Array.from(pop.querySelectorAll('.dp-tab'));
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dishCache = new Map();
  let popFor = null;       // the dish being shown
  let popOpener = null;    // focus goes back here on close

  function popTab(name, focus) {
    popTabs.forEach(t => {
      const on = t.dataset.tab === name;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
      if (on && focus) t.focus();
    });
    popBody.scrollTop = 0;
  }
  popTabs.forEach((t, i) => {
    t.addEventListener('click', () => popTab(t.dataset.tab));
    t.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault();
      popTab(popTabs[(i + (e.key === 'ArrowRight' ? 1 : popTabs.length - 1)) % popTabs.length].dataset.tab, true);
    });
  });

  const loading = (what) => `<p class="dp-loading">Loading ${what}&hellip;</p>`;
  const templateNote = (text) => `
    <div class="dp-note"><span class="dp-badge">Template preview</span><span>${text}</span></div>`;

  function recipeHtml(info) {
    const groups = [['ingredient', 'Ingredients'], ['action', 'Cooking actions'],
                    ['cookware', 'Cookware'], ['utensil', 'Utensils']];
    return `
      <p class="dp-intro">The ingredients, cooking actions and tools in this recipe. Click any of them to see every dish that has it.</p>
      ${groups.map(([kind, label]) => {
        const items = (info.entities && info.entities[kind]) || [];
        return `
          <div class="dp-group">
            <h4>${label}</h4>
            <div class="dp-chips">${items.length
              ? items.map(n => `<a class="dp-chip" href="/dictionary?${kind}=${encodeURIComponent(n.toLowerCase())}" title="See every dish with ${esc(n.toLowerCase())}">${esc(n)}</a>`).join('')
              : '<span class="dp-none">None listed.</span>'}</div>
          </div>`;
      }).join('')}
      ${info.recipe ? `<a class="dp-recipe" href="${esc(info.recipe)}" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>
        Original recipe</a>` : ''}`;
  }

  function cultureHtml(info) {
    const c = info.connection || { paragraphs: [] };
    const admin = info.admin_fields && info.admin_fields.length
      ? `<p class="dp-admin">${esc(info.admin_fields.join(' and '))} notes added by the CuiSync admin.</p>` : '';
    return `
      <h4>Culture</h4><p>${esc(info.culture)}</p>
      <h4>History</h4><p>${esc(info.history)}</p>
      ${admin}
      <div class="dp-heritage">
        <span class="dp-eyebrow">${esc(c.title || info.label)} &middot; ${esc(c.subtitle || 'Culinary origin & heritage')}</span>
        ${(c.paragraphs || []).map(p => `<p>${esc(p)}</p>`).join('')}
      </div>
      ${templateNote(info.source === 'ai'
        ? 'Culture and history written by GPT. The country note is still a template.'
        : 'This section will be generated by AI from a prompt. Showing a template built from the dataset for now.')}`;
  }

  function healthHtml(info) {
    const rows = ((info.nutrition && info.nutrition.rows) || []).map(r => `
      <li class="dp-nutrient">
        <strong>${esc(r.name)}</strong>
        <span>${r.value ? esc(r.value) : 'Value pending (nutrition API)'}${r.from && r.from.length ? ` &mdash; from ${esc(r.from.join(', '))}` : ''}</span>
      </li>`).join('');
    const notes = (info.health || []).length
      ? info.health.map(h => `<li>${esc(h)}</li>`).join('')
      : '<li>No nutrition notes for these ingredients yet.</li>';
    // Every allergen group as a tag: filled = in this dish, outlined = not.
    // Each one shows the dishes that are free from it.
    const found = new Map((info.allergens || []).map(a => [a.name, a]));
    const groups = info.allergen_groups || Array.from(found.keys());
    const tags = groups.map(name => {
      const a = found.get(name);
      const cls = 'dp-allergen' + (a ? ' is-present' : '') + (a && a.avoid ? ' is-avoid' : '');
      const title = a ? `In this dish (from ${a.from.join(', ')}). Click to see dishes without ${name}.`
                      : `Not in this dish. Click to see every dish without ${name}.`;
      return `<li><a class="${cls}" href="/dictionary?xallergen=${encodeURIComponent(name)}" title="${esc(title)}">${esc(name)}</a></li>`;
    }).join('');
    const fromLine = found.size
      ? `<p class="dp-allergen-from">${Array.from(found.values()).map(a => `<strong>${esc(a.name)}</strong> from ${esc(a.from.join(', '))}${a.avoid ? ' (on your avoid list)' : ''}`).join(' &middot; ')}</p>`
      : '<p class="dp-allergen-from">None of these were found in the listed ingredients.</p>';
    return `
      <h4>Nutrition facts</h4><ul class="dp-nutrients">${rows}</ul>
      <h4>Ingredient notes</h4><ul class="dp-list">${notes}</ul>
      <h4>Allergens</h4><ul class="dp-allergens">${tags}</ul>${fromLine}
      ${templateNote('Nutrition values will come from a nutrition API. Allergens are found from the listed ingredients only; always check the full recipe and product labels.')}`;
  }

  function fillPopup(info) {
    document.getElementById('dp-panel-recipe').innerHTML = recipeHtml(info);
    document.getElementById('dp-panel-culture').innerHTML = cultureHtml(info);
    document.getElementById('dp-panel-health').innerHTML = healthHtml(info);
  }

  function openDish(d) {
    popFor = d;
    popOpener = document.activeElement;
    const img = document.getElementById('dp-img');
    img.onerror = () => { img.onerror = null; img.src = NO_IMAGE; };
    img.src = d.i || NO_IMAGE;
    document.getElementById('dp-country').textContent = d.l;
    document.getElementById('dp-title').textContent = d.t;
    const alt = document.getElementById('dp-alt');
    alt.textContent = d.a && d.a.toLowerCase() !== d.t.toLowerCase() ? d.a : '';
    alt.hidden = !alt.textContent;

    // Tags: clickable, they close the popup and filter the page (like the card tags)
    const tagBox = document.getElementById('dp-tags');
    tagBox.innerHTML = '';
    d.g.forEach(t => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'dict-tag' + (t === d._region ? ' is-region' : '') + (state.tags.has(t) ? ' is-active' : '');
      b.textContent = t;
      b.title = `Show all dishes tagged ${t}`;
      b.addEventListener('click', () => {
        closeDish();
        if (state.tags.has(t)) return;
        state.tags.add(t);
        state.page = 1;
        update(true, true);
      });
      tagBox.appendChild(b);
    });
    // Puts this dish in the home page "Search by text" bar
    document.getElementById('dp-similar').href = `/?dish=${d.id}#search-section`;

    ['recipe', 'culture', 'health'].forEach((k, i) => {
      document.getElementById('dp-panel-' + k).innerHTML =
        loading(['the recipe breakdown', 'cultural information', 'health information'][i]);
    });
    popTab('recipe');
    pop.hidden = false;
    document.body.classList.add('dp-open');
    requestAnimationFrame(() => pop.classList.add('is-open'));
    popDialog.focus();

    const cached = dishCache.get(d.id);
    (cached ? Promise.resolve(cached) : fetch('/api/dish/' + d.id).then(r => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })).then(info => {
      dishCache.set(d.id, info);
      if (popFor === d) fillPopup(info);
    }).catch(() => {
      if (popFor !== d) return;
      ['recipe', 'culture', 'health'].forEach(k => {
        document.getElementById('dp-panel-' + k).innerHTML = '<p class="dp-loading">This information is not available right now.</p>';
      });
    });
  }

  function closeDish() {
    if (pop.hidden) return;
    popFor = null;
    pop.classList.remove('is-open');
    pop.hidden = true;
    document.body.classList.remove('dp-open');
    if (popOpener && document.contains(popOpener)) popOpener.focus();
  }
  document.getElementById('dp-close').addEventListener('click', closeDish);
  pop.addEventListener('click', (e) => { if (e.target === pop) closeDish(); });
  document.addEventListener('keydown', (e) => {
    if (pop.hidden) return;
    if (e.key === 'Escape') { closeDish(); return; }
    if (e.key !== 'Tab') return;
    // Keep keyboard focus inside the popup
    const f = Array.from(popDialog.querySelectorAll('a[href], button:not([disabled]), [tabindex="0"]'))
      .filter(el => el.offsetParent !== null);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  });

  function pageButtons(total) {
    // 1 2 3 … 9 style
    const cur = state.page;
    const set = new Set([1, total, cur - 1, cur, cur + 1].filter(n => n >= 1 && n <= total));
    if (cur <= 3) [2, 3, 4].forEach(n => n <= total && set.add(n));
    if (cur >= total - 2) [total - 3, total - 2, total - 1].forEach(n => n >= 1 && set.add(n));
    const nums = Array.from(set).sort((a, b) => a - b);
    const out = [];
    nums.forEach((n, i) => {
      if (i && n - nums[i - 1] > 1) out.push('…');
      out.push(n);
    });
    return out;
  }

  function pageButton(label, page, opts = {}) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'dict-page' + (opts.current ? ' is-current' : '');
    b.textContent = label;
    if (opts.aria) b.setAttribute('aria-label', opts.aria);
    if (opts.current) b.setAttribute('aria-current', 'page');
    if (opts.disabled) b.disabled = true;
    b.addEventListener('click', () => {
      state.page = page;
      update(true);
      // Page numbers go back to the very top (breadcrumb + title)
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    return b;
  }

  function render() {
    const list = filtered();
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * PAGE_SIZE;
    const pageItems = list.slice(start, start + PAGE_SIZE);

    grid.replaceChildren(...pageItems.map(card));
    countEl.textContent = list.length;
    countWord.textContent = list.length === 1 ? 'dish' : 'dishes';
    emptyEl.hidden = list.length > 0;

    // Pages
    pager.hidden = list.length === 0;
    pagerInfo.innerHTML = list.length
      ? `Showing <strong>${start + 1}–${start + pageItems.length}</strong> of <strong>${list.length}</strong>`
      : '';
    pagesNav.replaceChildren();
    if (pages > 1) {
      pagesNav.appendChild(pageButton('‹', state.page - 1, { aria: 'Previous page', disabled: state.page === 1 }));
      pageButtons(pages).forEach(n => {
        if (n === '…') {
          const s = document.createElement('span');
          s.className = 'dict-page-gap';
          s.textContent = '…';
          pagesNav.appendChild(s);
        } else {
          pagesNav.appendChild(pageButton(String(n), n, { current: n === state.page, aria: `Page ${n}` }));
        }
      });
      pagesNav.appendChild(pageButton('›', state.page + 1, { aria: 'Next page', disabled: state.page === pages }));
    }

    // Controls follow the state
    qInput.value = state.q;
    sortSelect.value = state.sort;
    document.querySelectorAll('.dict-sort-opt').forEach(o => {
      const on = o.dataset.sort === state.sort;
      o.classList.toggle('is-selected', on);
      o.setAttribute('aria-selected', String(on));
      if (on) setDropdownValue('sort', o.textContent);
    });
    document.querySelectorAll('input[name="country"]').forEach(i => { i.checked = state.countries.has(i.value); });
    document.querySelectorAll('input[name="tag"]').forEach(i => { i.checked = state.tags.has(i.value); });
    document.querySelectorAll('input[name="xtag"]').forEach(i => { i.checked = state.xtags.has(i.value); });
    document.querySelectorAll('input[name="xallergen"]').forEach(i => { i.checked = state.xallergens.has(i.value); });
    const hidden = state.xtags.size + state.xallergens.size;
    setDropdownValue('exclude', hidden === 0 ? 'None'
      : hidden === 1 ? [...state.xallergens, ...state.xtags][0] : `${hidden} things`);
    setDropdownValue('country', state.countries.size === 0
      ? 'All' 
      : state.countries.size === 1 ? countryLabel.get([...state.countries][0]) : `${state.countries.size} countries`);
    checklists.forEach(cl => cl.sync());

    // Active filters (each chip removes its filter -> clickable -> rounded)
    const chips = [];
    // The dish search is not listed here: Selected is only for Browse by category picks
    state.countries.forEach(c => chips.push([countryLabel.get(c) || c, () => state.countries.delete(c)]));
    state.tags.forEach(t => chips.push([t, () => state.tags.delete(t)]));
    state.xallergens.forEach(a => chips.push([`No ${a}`, () => state.xallergens.delete(a), true]));
    state.xtags.forEach(t => chips.push([`No ${t}`, () => state.xtags.delete(t), true]));
    state.entities.forEach(e => chips.push([titleCase(e.value), () => {
      state.entities = state.entities.filter(x => x !== e);
    }]));
    activeRow.hidden = chips.length === 0;
    activeChips.replaceChildren(...chips.map(([label, remove, isExclude]) => {
      const item = document.createElement('label');
      item.className = 'dict-picked' + (isExclude ? ' is-exclude' : '');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = true;
      box.setAttribute('aria-label', `Untick ${label}`);
      box.addEventListener('change', () => { remove(); state.page = 1; update(true); });
      const text = document.createElement('span');
      text.textContent = label;
      item.append(box, text);
      return item;
    }));
  }

  function update(push, scroll) {
    writeLink(push);
    render();
    if (scroll) document.getElementById('dict-results').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ------------------------------------------------------------------
  // Browse by category: two checklist dropdowns
  //   Ingredients                -> ingredient
  //   Directions (tabs inside)   -> cooking action, cookware, utensil
  // Each has a box to find an item. Ticked items are the same filters as the
  // ?ingredient= / ?action= / ?cookware= / ?utensil= links.
  // ------------------------------------------------------------------
  const CAT_WORDS = { ingredient: ['Ingredients', 'an ingredient'], action: ['Cooking actions', 'a cooking action'],
                      cookware: ['Cookware', 'cookware'], utensil: ['Utensils', 'a utensil'] };
  const titleCase = (t) => t.replace(/\b\w/g, c => c.toUpperCase());
  const isChecked = (kind, value) => state.entities.some(e => e.kind === kind && e.value === value);

  function entityFilter(kind, value) {
    const opt = (ENTITY_OPTIONS[kind] || { options: [] }).options.find(o => o[0] === value);
    const fromLink = ENTITIES.find(e => e.kind === kind && e.value === value);
    const ids = opt ? new Set(opt[2]) : fromLink ? fromLink.ids : null;
    if (!ids) return null;
    return { kind, label: (ENTITY_OPTIONS[kind] || fromLink).label, value, ids };
  }

  function toggleEntity(kind, value, on) {
    state.entities = state.entities.filter(e => !(e.kind === kind && e.value === value));
    if (on) {
      const f = entityFilter(kind, value);
      if (f) state.entities.push(f);
    }
    state.page = 1;
    update(false);
  }

  const checklists = Array.from(document.querySelectorAll('.dict-dd--browse')).map(dd => {
    const kinds = dd.dataset.kinds.split(' ').filter(k => ENTITY_OPTIONS[k]);
    const tabsBox = dd.querySelector('[data-cat-tabs]');
    const list = dd.querySelector('[data-cat-list]');
    const q = dd.querySelector('[data-cat-q]');
    const cl = { dd, kinds, kind: kinds[0] };

    cl.build = () => {
      if (tabsBox) tabsBox.querySelectorAll('.dict-cat-tab').forEach(b => {
        const on = b.dataset.kind === cl.kind;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', String(on));
      });
      q.placeholder = `Find ${(CAT_WORDS[cl.kind] || ['', 'an item'])[1]}`;
      const words = norm(q.value).split(/\s+/).filter(Boolean);
      // ticked ones first, then the most common
      const shown = ENTITY_OPTIONS[cl.kind].options.filter(([name]) => words.every(w => name.includes(w)))
        .sort((a, b) => isChecked(cl.kind, b[0]) - isChecked(cl.kind, a[0]));
      list.replaceChildren(...shown.map(([name, count]) => {
        const label = document.createElement('label');
        label.className = 'dict-check';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = name;
        input.addEventListener('change', () => toggleEntity(cl.kind, name, input.checked));
        const span = document.createElement('span');
        span.textContent = titleCase(name);
        const small = document.createElement('small');
        small.textContent = count;
        label.append(input, span, small);
        return label;
      }));
      if (!shown.length) {
        const none = document.createElement('p');
        none.className = 'dict-cat-none';
        none.textContent = 'Nothing found.';
        list.appendChild(none);
      }
      cl.sync();
    };

    // Ticks, tab counts and the button text follow the state (without rebuilding the list)
    cl.sync = () => {
      list.querySelectorAll('input[type="checkbox"]').forEach(i => { i.checked = isChecked(cl.kind, i.value); });
      if (tabsBox) tabsBox.querySelectorAll('.dict-cat-tab').forEach(b => {
        b.dataset.count = state.entities.filter(e => e.kind === b.dataset.kind).length || '';
      });
      const mine = state.entities.filter(e => kinds.includes(e.kind));
      setDropdownValue(dd.dataset.dd, mine.length === 0 ? 'All'
        : mine.length === 1 ? titleCase(mine[0].value) : `${mine.length} ticked`);
    };

    if (tabsBox) {
      tabsBox.replaceChildren(...kinds.map(kind => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'dict-cat-tab';
        b.dataset.kind = kind;
        b.setAttribute('role', 'tab');
        b.textContent = (CAT_WORDS[kind] || [ENTITY_OPTIONS[kind].label])[0];
        b.addEventListener('click', () => { cl.kind = kind; q.value = ''; cl.build(); q.focus(); });
        return b;
      }));
    }
    q.addEventListener('input', cl.build);
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') e.preventDefault(); });
    return cl;
  });

  // ------------------------------------------------------------------
  // Controls
  // ------------------------------------------------------------------
  let typingTimer = null;
  qInput.addEventListener('input', () => {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => {
      state.q = qInput.value.trim();
      state.page = 1;
      update(false);
    }, 200);
  });
  // Search button (and Enter): search now and jump to the results
  document.getElementById('dict-hero-search').addEventListener('submit', () => {
    clearTimeout(typingTimer);
    state.q = qInput.value.trim();
    state.page = 1;
    update(true, true);
  });
  sortSelect.addEventListener('change', () => {
    state.sort = sortSelect.value;
    state.page = 1;
    update(false);
  });
  document.querySelectorAll('input[name="country"], input[name="tag"], input[name="xtag"], input[name="xallergen"]').forEach(input => {
    input.addEventListener('change', () => {
      const set = { country: state.countries, tag: state.tags, xtag: state.xtags, xallergen: state.xallergens }[input.name];
      input.checked ? set.add(input.value) : set.delete(input.value);
      // A tag is either shown or hidden, not both
      if (input.checked && input.name === 'tag') state.xtags.delete(input.value);
      if (input.checked && input.name === 'xtag') state.tags.delete(input.value);
      state.page = 1;
      update(false);
    });
  });
  document.getElementById('dict-reset').addEventListener('click', () => {
    // "Untick all" clears the Browse by category picks but keeps the dish search
    state.countries.clear();
    state.tags.clear();
    state.xtags.clear();
    state.xallergens.clear();
    state.entities = [];
    state.page = 1;
    update(true);
  });
  // Sort: custom list (a native select shows the system's blue highlight)
  document.querySelectorAll('.dict-sort-opt').forEach(o => {
    o.addEventListener('click', () => {
      sortSelect.value = o.dataset.sort;
      sortSelect.dispatchEvent(new Event('change'));
      closeAll();
      document.querySelector('.dict-dd[data-dd="sort"] .dict-dd-btn').focus();
    });
  });
  window.addEventListener('popstate', () => { readLink(); render(); });

  // Dropdowns (Origin, Tags)
  const dropdowns = Array.from(document.querySelectorAll('.dict-dd'));
  function setDropdownValue(name, text) {
    const el = document.querySelector(`.dict-dd[data-dd="${name}"] [data-dd-value]`);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('is-empty', text === 'All');   // grey like the home page placeholders
  }
  function closeAll(except) {
    dropdowns.forEach(dd => {
      if (dd === except) return;
      dd.querySelector('.dict-dd-panel').hidden = true;
      dd.querySelector('.dict-dd-btn').setAttribute('aria-expanded', 'false');
      dd.classList.remove('is-open');
    });
  }
  dropdowns.forEach(dd => {
    const btn = dd.querySelector('.dict-dd-btn');
    const panel = dd.querySelector('.dict-dd-panel');
    btn.addEventListener('click', () => {
      const open = panel.hidden;
      closeAll(dd);
      panel.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
      dd.classList.toggle('is-open', open);
      const cl = checklists.find(c => c.dd === dd);
      if (open && cl) cl.build();
    });
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('.dict-dd')) closeAll(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(); });

  readLink();
  writeLink(false);
  checklists.forEach(cl => {
    const first = state.entities.find(e => cl.kinds.includes(e.kind));
    if (first) cl.kind = first.kind;
    cl.build();
  });
  render();
});
