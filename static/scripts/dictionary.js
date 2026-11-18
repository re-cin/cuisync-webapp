// Dish Dictionary: every dish in the dataset with search, filters, sort by dish title (A–Z / Z–A) and pages.
// Filters: Origin (any ticked country), Region (any ticked region), Ingredients and Directions
// (a dish must have every ticked item). Nothing else filters this page.
// The state lives in the link (?q=&country=&region=&ingredient=&action=&cookware=&utensil=&sort=&page=).
// An old ?tag=<region> link (e.g. ?tag=East Asia) still opens the page filtered by that region.
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

  // Region dropdown: one checkbox per region, with how many dishes it has
  const regionCounts = new Map();
  DISHES.forEach(d => { if (d._region !== '~') regionCounts.set(d._region, (regionCounts.get(d._region) || 0) + 1); });
  const regionBox = document.getElementById('dict-region-options');
  if (regionBox) {
    Array.from(regionCounts.keys()).sort((a, b) => a.localeCompare(b)).forEach(r => {
      const label = document.createElement('label');
      label.className = 'dict-check';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.name = 'region';
      input.value = r;
      const span = document.createElement('span');
      span.textContent = r;
      const small = document.createElement('small');
      small.textContent = regionCounts.get(r);
      label.append(input, span, small);
      regionBox.appendChild(label);
    });
  }
  const regionByLower = new Map(Array.from(regionCounts.keys()).map(r => [r.toLowerCase(), r]));

  // ------------------------------------------------------------------
  // State <-> link
  // ------------------------------------------------------------------
  const state = { q: '', countries: new Set(), regions: new Set(),
                  entities: [], sort: 'az', page: 1 };
  const knownCountries = new Set(Array.from(document.querySelectorAll('input[name="country"]')).map(i => i.value));

  function readLink() {
    const p = new URLSearchParams(location.search);
    state.q = p.get('q') || '';
    state.countries = new Set(p.getAll('country').map(c => c.toLowerCase()).filter(c => knownCountries.has(c)));
    state.regions = new Set([...p.getAll('region'), ...p.getAll('tag')]
      .map(r => regionByLower.get(r.toLowerCase())).filter(Boolean));
    state.entities = [];
    ['ingredient', 'action', 'cookware', 'utensil'].forEach(kind => {
      p.getAll(kind).forEach(v => {
        const value = v.trim().toLowerCase().split(/\s+/).join(' ');
        if (!value || state.entities.some(e => e.kind === kind && e.value === value)) return;
        const f = entityFilter(kind, value);
        if (f) state.entities.push(f);
      });
    });
    state.sort = p.get('sort') === 'za' ? 'za' : 'az';
    state.page = Math.max(1, parseInt(p.get('page'), 10) || 1);
  }

  function writeLink(push) {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    state.countries.forEach(c => p.append('country', c));
    state.regions.forEach(r => p.append('region', r));
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
      (!state.regions.size || state.regions.has(d._region)) &&
      state.entities.every(e => e.ids.has(d.id)) &&
      words.every(w => d._name.includes(w))
    );
    // Sorted by the dish title only: A–Z or Z–A
    const dir = state.sort === 'za' ? -1 : 1;
    return list.sort((a, b) => dir * a.t.localeCompare(b.t));
  }

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  function card(d) {
    const li = cardTpl.content.firstElementChild.cloneNode(true);
    const img = li.querySelector('img');
    img.onerror = () => { img.onerror = null; img.src = NO_IMAGE; };
    img.src = d.i || NO_IMAGE;
    li.querySelector('.dict-card-title').textContent = d.t;
    const alt = li.querySelector('.dict-card-alt');
    if (d.a && d.a.toLowerCase() !== d.t.toLowerCase()) alt.textContent = d.a;
    else alt.remove();

    // Footer: "India · South Asia"
    li.querySelector('.dict-card-place').textContent = d._region !== '~' ? `${d.l} · ${d._region}` : d.l;

    // The whole card opens the dish popup
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
  // Dish popup: image + name + "Find similar dishes" on the left,
  // Recipe breakdown / Culture & history / Health info tabs on the right
  // (recipe from /api/dish/<id>; culture & health from /api/dish/<id>/information)
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

  // Recipe breakdown: "View the original recipe" button on top, then one row per group
  // (label on the left, pills on the right), divided by thin lines
  function recipeHtml(info) {
    const groups = [['ingredient', 'Ingredients'], ['action', 'Cooking actions'],
                    ['cookware', 'Cookware'], ['utensil', 'Utensils']];
    const recipeLink = info.recipe ? `<a class="dp-recipe" href="${esc(info.recipe)}" target="_blank" rel="noopener">
        View the original recipe
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg></a>` : '';
    // Pill text in sentence case ("Amaretti cookie")
    const sentence = (t) => { const l = String(t).toLowerCase(); return l.charAt(0).toUpperCase() + l.slice(1); };
    const rows = groups.map(([kind, label]) => [label, ((info.entities && info.entities[kind]) || []).map(sentence)]);
    return `
      ${recipeLink}
      <div class="dp-rows">${rows.map(([label, items]) => {
        return `
        <div class="dp-row">
          <h4>${label}</h4>
          <div class="dp-chips">${items.length
            ? items.map(n => `<span class="dp-chip">${esc(n)}</span>`).join('')
            : '<span class="dp-none">None</span>'}</div>
        </div>`;
      }).join('')}
      </div>`;
  }

  function fillPopup(info) {
    document.getElementById('dp-panel-recipe').innerHTML = recipeHtml(info);
  }

  // Culture & history / Health info: the same GPT-generated information (stored in Supabase)
  // and the same layout as the comparison page, from /api/dish/<id>/information.
  // The first time a dish is opened GPT may still be writing it, so check again a few times.
  const infoCache = new Map();
  function fillInfo(info, final) {
    [['culture', 'dp-info-culture'], ['health', 'dp-info-health']].forEach(([part, cls]) => {
      const panel = document.getElementById('dp-panel-' + part);
      const note = !final && info.generating && info.generating[part]
        ? '<p class="dp-info-generating">Still being prepared. This updates by itself in a few seconds.</p>' : '';
      panel.innerHTML = `<div class="dp-info ${cls} rt-column-body">${note}${info[part] || ''}</div>`;
    });
    if (window.CuiSyncDishInfo) window.CuiSyncDishInfo.setup(popBody);
  }
  function loadInfo(d, attempt = 0) {
    const cached = infoCache.get(d.id);
    if (cached) { fillInfo(cached, true); return; }
    fetch(`/api/dish/${d.id}/information`).then(r => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(info => {
      if (popFor !== d) return;
      const waiting = info.generating && (info.generating.culture || info.generating.health);
      if (!waiting) infoCache.set(d.id, info);
      fillInfo(info, attempt >= 8);
      // GPT / the nutrition lookup runs in the background: check again (about 40 seconds in all)
      if (waiting && attempt < 8) setTimeout(() => { if (popFor === d) loadInfo(d, attempt + 1); }, 5000);
    }).catch(() => {
      if (popFor !== d) return;
      ['culture', 'health'].forEach(k => {
        document.getElementById('dp-panel-' + k).innerHTML = '<p class="dp-loading">This information is not available right now.</p>';
      });
    });
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

    // Country and region under the dish name: "Country  Malaysia" / "Region  Southeast Asia"
    const place = document.getElementById('dp-tags');
    place.innerHTML = '';
    [['Country', d.l], ['Region', d._region !== '~' ? d._region : '']].forEach(([label, value]) => {
      if (!value) return;
      const k = document.createElement('span');
      k.className = 'dp-place-label';
      k.textContent = label;
      const v = document.createElement('span');
      v.className = 'dp-place-value';
      v.textContent = value;
      place.append(k, v);
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

    loadInfo(d);
    const cached = dishCache.get(d.id);
    (cached ? Promise.resolve(cached) : fetch('/api/dish/' + d.id).then(r => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })).then(info => {
      dishCache.set(d.id, info);
      if (popFor === d) fillPopup(info);
    }).catch(() => {
      if (popFor !== d) return;
      ['recipe'].forEach(k => {
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
    document.querySelectorAll('.dict-sort-opt').forEach(o => {
      const on = o.dataset.sort === state.sort;
      o.classList.toggle('is-selected', on);
      o.setAttribute('aria-selected', String(on));
      if (on) setDropdownValue('sort', o.textContent);
    });
    document.querySelectorAll('input[name="country"]').forEach(i => { i.checked = state.countries.has(i.value); });
    document.querySelectorAll('input[name="region"]').forEach(i => { i.checked = state.regions.has(i.value); });
    setDropdownValue('country', state.countries.size === 0
      ? 'All' 
      : state.countries.size === 1 ? countryLabel.get([...state.countries][0]) : `${state.countries.size} countries`);
    setDropdownValue('region', state.regions.size === 0
      ? 'All'
      : state.regions.size === 1 ? [...state.regions][0] : `${state.regions.size} regions`);
    checklists.forEach(cl => cl.sync());

    // Active filters (each chip removes its filter -> clickable -> rounded)
    const chips = [];
    // The dish search is not listed here: Selected is only for Browse by category picks
    state.countries.forEach(c => chips.push([countryLabel.get(c) || c, () => state.countries.delete(c)]));
    state.regions.forEach(r => chips.push([r, () => state.regions.delete(r)]));
    state.entities.forEach(e => chips.push([titleCase(e.value), () => {
      state.entities = state.entities.filter(x => x !== e);
    }]));
    activeRow.hidden = chips.length === 0;
    activeChips.replaceChildren(...chips.map(([label, remove]) => {
      // Each chip is a button: the name, then an x that removes it
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'dict-picked';
      item.setAttribute('aria-label', `Remove ${label}`);
      item.addEventListener('click', () => { remove(); state.page = 1; update(true); });
      const text = document.createElement('span');
      text.textContent = label;
      const x = document.createElement('span');
      x.className = 'dict-picked-x';
      x.setAttribute('aria-hidden', 'true');
      x.textContent = '×';
      item.append(text, x);
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
        : mine.length === 1 ? titleCase(mine[0].value) : `${mine.length} selected`);
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
  document.querySelectorAll('input[name="country"], input[name="region"]').forEach(input => {
    input.addEventListener('change', () => {
      const set = { country: state.countries, region: state.regions }[input.name];
      input.checked ? set.add(input.value) : set.delete(input.value);
      state.page = 1;
      update(false);
    });
  });
  document.getElementById('dict-reset').addEventListener('click', () => {
    // "Remove selected" clears the Browse by category picks but keeps the dish search
    state.countries.clear();
    state.regions.clear();
    state.entities = [];
    state.page = 1;
    update(true);
  });
  // Sort: custom list (a native select shows the system's blue highlight)
  document.querySelectorAll('.dict-sort-opt').forEach(o => {
    o.addEventListener('click', () => {
      state.sort = o.dataset.sort;
      state.page = 1;
      update(false);
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
