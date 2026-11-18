/* "Search by tag" tab on the home page.
   Four sub-tabs: REGION (regions and countries of origin), INGREDIENT, COOKING METHOD, DISH TYPE. Each has a search bar that
   takes several picks (like the old ingredient search) and quick picks below it.
   Picks from every sub-tab are combined and listed under "Your tags":
     - region / cooking method / dish type are tags: a dish must have all of them
     - countries (Region tab): the dish is from any of the picked countries
     - ingredients are any ingredient in the dataset: dishes using more of them come first
   FIND DISHES -> popup with the matching dishes (from text-search.js, window.CuiSyncPopup)
   -> pick one -> compared with the target country.
   Opened from history / old links: /?mode=tags&tag=Grilled&ing=chicken&country=japan or /?mode=ingredients&q=chicken */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('tag-search-form');
  if (!form) return;

  const GROUP_LABELS = { region: 'Region', country: 'Country', ingredient: 'Ingredient', method: 'Cooking method', type: 'Dish type' };
  const dishName = document.getElementById('tag-dish-name');
  const recipeId = document.getElementById('tag-recipe-id');
  const origin = document.getElementById('tag-origin');
  const ingredientsField = document.getElementById('tag-ingredients');
  const target = document.getElementById('tag-target');
  const submitButton = form.querySelector('button[type="submit"]');
  const tabButtons = Array.from(form.querySelectorAll('.tag-subtabs .search-mode-btn'));
  const summaryChips = document.getElementById('tag-summary-chips');
  const summaryEmpty = document.getElementById('tag-summary-empty');
  const summaryCount = document.getElementById('tag-summary-count');
  const summaryClear = document.getElementById('tag-summary-clear');

  const picks = { region: [], country: [], ingredient: [], method: [], type: [] };
  let ingredientList = [];          // [name, dish count] from /api/ingredients
  let ingredientsLoading = null;
  let pendingDish = null;           // dish picked before a target country was chosen

  const lower = (s) => String(s || '').toLowerCase();

  // Countries offered in the Region tab: [value, label, dish count]
  let COUNTRIES = [];
  try { COUNTRIES = JSON.parse(document.getElementById('tag-panel-region').dataset.countries || '[]'); } catch (e) { COUNTRIES = []; }
  const countryLabel = new Map(COUNTRIES.map(([v, l]) => [v, l]));
  const countryByLabel = new Map(COUNTRIES.map(([v, l]) => [lower(l), v]));
  const display = (key, value) => (key === 'country' ? countryLabel.get(value) || value : value);
  // Which picks a tab's search bar shows (the Region tab holds regions and countries)
  const keysOf = (panelKey) => (panelKey === 'region' ? ['region', 'country'] : [panelKey]);

  // ------------------------------------------------------------------
  // Sub-tabs
  // ------------------------------------------------------------------
  const panels = {};
  form.querySelectorAll('.tag-panel').forEach((el) => {
    const key = el.dataset.panel;
    let options = [];
    try { options = JSON.parse(el.dataset.options || '[]'); } catch (e) { options = []; }
    panels[key] = {
      el,
      key,
      free: el.dataset.free === '1',   // the Ingredient tab takes any ingredient
      options,                          // tag names for the other tabs
      chips: el.querySelector('[data-chips]'),
      input: el.querySelector('input[type="text"]'),
      dropdown: el.querySelector('[data-dropdown]'),
      box: el.querySelector('.tag-chip-input'),
      quick: Array.from(el.querySelectorAll('.tag-quick')),
      active: -1,
      matches: [],
    };
  });

  function showPanel(key) {
    tabButtons.forEach((b) => {
      const on = b.dataset.panel === key;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', String(on));
    });
    Object.values(panels).forEach((p) => { p.el.hidden = p.key !== key; hideDropdown(p); });
    if (key === 'ingredient') loadIngredients();
  }
  tabButtons.forEach((b) => b.addEventListener('click', () => {
    showPanel(b.dataset.panel);
    panels[b.dataset.panel].input.focus();
  }));

  // ------------------------------------------------------------------
  // Picks
  // ------------------------------------------------------------------
  function has(key, value) {
    return picks[key].some((v) => lower(v) === lower(value));
  }

  function add(key, value) {
    value = String(value || '').trim();
    if (!value || has(key, value)) return;
    picks[key].push(value);
    changed();
  }

  function remove(key, value) {
    picks[key] = picks[key].filter((v) => lower(v) !== lower(value));
    changed();
  }

  function changed() {
    pendingDish = null;
    renderAll();
  }

  function chip(label, onRemove, extraClass, groupLabel) {
    const span = document.createElement('span');
    span.className = 'ingredient-chip' + (extraClass ? ' ' + extraClass : '');
    if (groupLabel) {
      const g = document.createElement('small');
      g.className = 'tag-summary-group';
      g.textContent = groupLabel;
      span.appendChild(g);
    }
    span.appendChild(document.createTextNode(label));
    const x = document.createElement('button');
    x.type = 'button';
    x.textContent = '×';
    x.setAttribute('aria-label', 'Remove ' + label);
    x.addEventListener('click', (e) => { e.stopPropagation(); onRemove(); });
    span.appendChild(x);
    return span;
  }

  function renderAll() {
    let total = 0;
    summaryChips.replaceChildren();
    Object.values(panels).forEach((p) => {
      // Chips inside this tab's search bar
      p.chips.replaceChildren(...keysOf(p.key).flatMap((k) =>
        picks[k].map((v) => chip(display(k, v), () => remove(k, v)))));
      // Quick picks follow the picks
      p.quick.forEach((q) => {
        const on = has(q.dataset.kind || p.key, q.dataset.value);
        q.classList.toggle('is-active', on);
        q.setAttribute('aria-pressed', String(on));
      });
      // "Your tags" box: everything from every tab
      keysOf(p.key).forEach((k) => picks[k].forEach((v) => {
        summaryChips.appendChild(chip(display(k, v), () => remove(k, v), 'tag-summary-chip tag-summary-' + k, GROUP_LABELS[k]));
        total += 1;
      }));
    });
    summaryEmpty.hidden = total > 0;
    summaryClear.hidden = total === 0;
    summaryCount.textContent = total ? `(${total})` : '';
    submitButton.textContent = pendingDish ? 'SEARCH THE DISH' : 'FIND DISHES';
  }

  Object.values(panels).forEach((p) => {
    p.quick.forEach((q) => q.addEventListener('click', () => {
      const key = q.dataset.kind || p.key;
      has(key, q.dataset.value) ? remove(key, q.dataset.value) : add(key, q.dataset.value);
    }));
  });

  summaryClear.addEventListener('click', () => {
    Object.keys(picks).forEach((k) => { picks[k] = []; });
    changed();
  });

  // ------------------------------------------------------------------
  // Search bars with suggestions (same look and keys as the dish name dropdown)
  // ------------------------------------------------------------------
  function loadIngredients() {
    if (ingredientsLoading) return ingredientsLoading;
    ingredientsLoading = fetch('/api/ingredients')
      .then((r) => r.json())
      .then((list) => { ingredientList = list.map((x) => (Array.isArray(x) ? x : [x, 0])); })
      .catch(() => { ingredientsLoading = null; });
    return ingredientsLoading;
  }

  // Order: exact, starts with, a word starts with, contains. Picked ones are skipped.
  function suggestions(p, query, limit = 8) {
    const q = lower(query).trim();
    if (!q) return [];
    let source = p.free ? ingredientList : p.options.map((t) => [t, null]);
    if (p.key === 'region') {
      source = source.concat(COUNTRIES.filter(([v]) => !has('country', v)).map(([, l, n]) => [l, n, 'country']));
    }
    const rows = [];
    for (const [name, count, kind] of source) {
      if (!kind && has(p.key, name)) continue;
      const n = lower(name);
      const rank = n === q ? 0 : n.startsWith(q) ? 1
        : n.split(/[\s-]+/).some((w) => w.startsWith(q)) ? 2 : n.includes(q) ? 3 : -1;
      if (rank >= 0) rows.push({ name, count, rank, kind });
    }
    return rows.sort((a, b) => a.rank - b.rank || (b.count || 0) - (a.count || 0)).slice(0, limit);
  }

  function hideDropdown(p) {
    p.dropdown.classList.add('hidden');
    p.dropdown.innerHTML = '';
    p.active = -1;
    p.matches = [];
  }

  function renderDropdown(p) {
    p.matches = suggestions(p, p.input.value);
    p.dropdown.innerHTML = '';
    p.active = -1;
    if (!p.matches.length) {
      // Tag tabs only take tags from the list
      if (!p.free && p.input.value.trim()) {
        const li = document.createElement('li');
        li.className = 'dropdown-item dropdown-empty';
        li.textContent = p.key === 'region'
          ? `No region or country matches "${p.input.value.trim()}"`
          : `No ${GROUP_LABELS[p.key].toLowerCase()} tag matches "${p.input.value.trim()}"`;
        p.dropdown.appendChild(li);
        p.dropdown.classList.remove('hidden');
        return;
      }
      return hideDropdown(p);
    }
    p.matches.forEach((m) => {
      const li = document.createElement('li');
      li.className = 'dropdown-item';
      li.appendChild(document.createTextNode(m.name));
      if (m.count || m.kind === 'country' || p.key === 'region') {
        const hint = document.createElement('span');
        hint.className = 'dropdown-alt';
        hint.textContent = m.kind === 'country'
          ? `country · ${m.count} dish${m.count === 1 ? '' : 'es'}`
          : p.key === 'region' ? 'region'
            : `used in ${m.count} dish${m.count === 1 ? '' : 'es'}`;
        li.appendChild(hint);
      }
      li.addEventListener('mousedown', (e) => {
        e.preventDefault();
        pickFromInput(p, m.name);
      });
      p.dropdown.appendChild(li);
    });
    p.dropdown.classList.remove('hidden');
  }

  function pickFromInput(p, name) {
    // In the Region tab a country name adds the country
    if (p.key === 'region' && countryByLabel.has(lower(name))) add('country', countryByLabel.get(lower(name)));
    else add(p.key, name);
    p.input.value = '';
    hideDropdown(p);
    p.input.focus();
  }

  // Enter / comma: the highlighted suggestion, else the first one. The Ingredient tab
  // also takes what was typed (the server matches it to the closest dataset ingredient).
  function commitTyped(p) {
    const typed = p.input.value.trim();
    if (!typed) return false;
    if (p.free) {
      typed.split(',').map((s) => s.trim()).filter(Boolean).forEach((v) => add(p.key, v));
      p.input.value = '';
      hideDropdown(p);
      return true;
    }
    const best = suggestions(p, typed, 1)[0];
    if (best) {
      pickFromInput(p, best.name);
      return true;
    }
    renderDropdown(p);   // shows "No tag matches"
    return true;
  }

  Object.values(panels).forEach((p) => {
    p.box.addEventListener('click', () => p.input.focus());
    p.input.addEventListener('input', () => {
      if (p.free && !ingredientList.length) loadIngredients().then(() => renderDropdown(p));
      renderDropdown(p);
    });
    p.input.addEventListener('focus', () => { if (p.input.value.trim()) renderDropdown(p); });
    p.input.addEventListener('blur', () => setTimeout(() => hideDropdown(p), 120));
    p.input.addEventListener('keydown', (e) => {
      const items = p.dropdown.querySelectorAll('.dropdown-item:not(.dropdown-empty)');
      const open = !p.dropdown.classList.contains('hidden') && items.length;
      if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        p.active = e.key === 'ArrowDown'
          ? (p.active + 1) % items.length
          : (p.active - 1 + items.length) % items.length;
        items.forEach((item, i) => item.classList.toggle('highlighted', i === p.active));
        items[p.active].scrollIntoView({ block: 'nearest' });
        return;
      }
      if (open && e.key === 'Enter' && p.active >= 0) {
        e.preventDefault();
        pickFromInput(p, p.matches[p.active].name);
        return;
      }
      if (e.key === 'Escape') { hideDropdown(p); return; }
      if (e.key === 'Enter' || (e.key === ',' && p.free)) {
        e.preventDefault();
        if (!commitTyped(p) && e.key === 'Enter') form.requestSubmit();
      } else if (e.key === 'Backspace' && !p.input.value) {
        const k = keysOf(p.key).slice().reverse().find((x) => picks[x].length);
        if (k) remove(k, picks[k][picks[k].length - 1]);
      }
    });
  });

  // ------------------------------------------------------------------
  // Search
  // ------------------------------------------------------------------
  const tagPicks = () => [...picks.region, ...picks.method, ...picks.type];

  function sendForm(d) {
    dishName.value = d.title;
    recipeId.value = d.recipe_id ?? '';
    origin.value = d.country_value;
    ingredientsField.value = picks.ingredient.join(', ');
    HTMLFormElement.prototype.submit.call(form);
  }

  // Picking a dish sends it right away (or waits for the target country)
  function choose(d) {
    window.CuiSyncPopup.close();
    if (!target.value) {
      pendingDish = d;
      renderAll();
      target.classList.add('needs-attention');
      target.focus();
      showErrorModal('Choose a Target Country',
        `You picked ${d.title}. Now select the target country (or "Any country") to compare it with.`);
      return;
    }
    sendForm(d);
  }

  target.addEventListener('change', () => {
    target.classList.remove('needs-attention');
    if (pendingDish) sendForm(pendingDish);
  });

  async function findDishes() {
    Object.values(panels).forEach((p) => commitTyped(p));
    const tags = tagPicks();
    if (!tags.length && !picks.ingredient.length && !picks.country.length) {
      showErrorModal('Pick a Tag', 'Add at least one region, country, ingredient, cooking method or dish type.');
      return;
    }
    submitButton.disabled = true;
    submitButton.textContent = 'SEARCHING…';
    try {
      const url = `/api/find-dishes?mode=tags&q=${encodeURIComponent(tags.join('|'))}`
        + (picks.ingredient.length ? `&ing=${encodeURIComponent(picks.ingredient.join(', '))}` : '')
        + (picks.country.length ? `&country=${encodeURIComponent(picks.country.join('|'))}` : '');
      const r = await fetch(url, { credentials: 'same-origin' });
      const data = await r.json();
      if (!data.total) {
        showErrorModal('No Dishes Found', 'No dish matches all of these picks. Try removing one.');
        return;
      }
      window.CuiSyncPopup.show(data, choose);
    } finally {
      submitButton.disabled = false;
      renderAll();
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (pendingDish && target.value) {
      sendForm(pendingDish);
      return;
    }
    findDishes();
  });

  // ------------------------------------------------------------------
  // Opened from search history or an old link
  // ------------------------------------------------------------------
  const params = new URLSearchParams(window.location.search);
  const startMode = params.get('mode');
  if (startMode === 'tags' || startMode === 'ingredients') {
    switchTab('tag');
    // Each tag goes to the tab that has it (tags that are no longer offered are skipped)
    params.getAll('tag').forEach((t) => {
      const p = Object.values(panels).find((x) => !x.free && x.options.some((o) => lower(o) === lower(t)));
      if (p) picks[p.key].push(p.options.find((o) => lower(o) === lower(t)));
    });
    params.getAll('country').forEach((c) => {
      if (countryLabel.has(lower(c)) && !has('country', lower(c))) picks.country.push(lower(c));
    });
    const ing = startMode === 'ingredients' ? params.get('q') : params.get('ing');
    (ing || '').split(',').map((s) => s.trim()).filter(Boolean).forEach((v) => {
      if (!has('ingredient', v)) picks.ingredient.push(v);
    });
    if (startMode === 'ingredients') showPanel('ingredient');
    renderAll();
    if (tagPicks().length || picks.ingredient.length || picks.country.length) findDishes();
  } else {
    renderAll();
  }
});
