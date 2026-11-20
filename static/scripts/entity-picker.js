// Comparison page, Recipe breakdown: pick ingredients (several at once),
// then "Find dishes" opens a popup with every dish that uses all of them (/api/dishes-with-items).
// The popup looks like the "Search by tag" popup on the home page. Choosing a dish compares it
// with the same target country.
(() => {
  const chips = Array.from(document.querySelectorAll('.entities-card .entity-chip[data-kind]'));
  const findBtn = document.getElementById('entity-pick-find');
  const clearBtn = document.getElementById('entity-pick-clear');
  const status = document.getElementById('entity-pick-status');
  const popup = document.getElementById('item-dishes-overlay');
  if (!chips.length || !findBtn || !popup) return;

  const popupTitle = document.getElementById('item-dishes-title');
  const popupDesc = document.getElementById('item-dishes-desc');
  const popupCountries = document.getElementById('item-dishes-countries');
  const popupGrid = document.getElementById('item-dishes-grid');
  const popupClose = document.getElementById('item-dishes-close');
  const form = document.getElementById('item-dishes-form');
  const popupBox = popup.querySelector('.dish-results-box');
  const allSection = document.getElementById('item-dishes-all');
  const allHead = document.getElementById('item-dishes-all-head');
  const partialSection = document.getElementById('item-dishes-some');
  const partialHead = document.getElementById('item-dishes-some-head');
  const partialGrid = document.getElementById('item-dishes-some-grid');
  const pager = document.getElementById('item-dishes-pager');
  const pagerPrev = document.getElementById('item-dishes-prev');
  const pagerNext = document.getElementById('item-dishes-next');
  const pagerInfo = document.getElementById('item-dishes-page');
  const PAGE_SIZE = 15;   // dishes per page in the popup
  const tabs = document.getElementById('item-dishes-tabs');
  const tabAll = document.getElementById('item-dishes-tab-all');
  const tabSome = document.getElementById('item-dishes-tab-some');
  const NO_IMAGE = '/static/assets/no-image.svg';
  const KIND_WORD = { ingredient: 'ingredient', action: 'cooking action', cookware: 'cookware', utensil: 'utensil' };

  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // The same item can show twice (e.g. shared and in one dish): one pick per kind + value
  const key = (chip) => `${chip.dataset.kind}|${chip.dataset.value}`;
  const picked = new Map();   // key -> { kind, value, label }

  function update() {
    chips.forEach((chip) => chip.setAttribute('aria-pressed', String(picked.has(key(chip)))));
    const n = picked.size;
    findBtn.disabled = n === 0;
    clearBtn.hidden = n === 0;
    // The picked ingredients as tags (same colours as above), each with an x to remove it
    if (n === 0) {
      status.textContent = 'Choose ingredients above.';
    } else {
      const count = document.createElement('span');
      count.className = 'entity-pick-count';
      count.textContent = `${n} selected:`;
      const tags = [...picked].map(([k, p]) => {
        const tag = document.createElement('span');
        tag.className = `entity-chip entity-pick-tag ${p.color}`;
        tag.textContent = p.label;
        const x = document.createElement('button');
        x.type = 'button';
        x.className = 'entity-pick-tag-x';
        x.setAttribute('aria-label', `Remove ${p.label}`);
        x.innerHTML = '&times;';
        x.addEventListener('click', () => { picked.delete(k); update(); });
        tag.appendChild(x);
        return tag;
      });
      status.replaceChildren(count, ...tags);
    }
  }

  chips.forEach((chip) => {
    chip.addEventListener('click', () => {
      const k = key(chip);
      if (picked.has(k)) picked.delete(k);
      else picked.set(k, { kind: chip.dataset.kind, value: chip.dataset.value, label: chip.textContent.trim(),
                           color: ['chip-gold', 'chip-maroon', 'chip-green'].find((c) => chip.classList.contains(c)) || 'chip-gold' });
      update();
    });
  });
  clearBtn.addEventListener('click', () => { picked.clear(); update(); });

  // ---------------- popup (same cards as the home page popup) ----------------
  function dishButton(d) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'dish-card popup-dish-card pdc';
    const found = (d.found || []).length
      ? `<p class="dish-card-found">Uses: ${d.found.map(([want, have]) => `<span title="you picked ${esc(want)}">${esc(have)}</span>`).join(', ')}</p>`
      : '';
    const missing = (d.missing || []).length
      ? `<p class="dish-card-missing">No ${d.missing.map(esc).join(', ')}</p>` : '';
    // Only what the dish uses (and lacks) from the picked ingredients: no recipe tags here
    const alt = d.alternative_title && d.alternative_title.toLowerCase() !== d.title.toLowerCase()
      ? `<p class="pdc-alt">${esc(d.alternative_title)}</p>` : '';
    button.innerHTML = `
      <div class="dish-card-img">
        <img src="${esc(d.image_link || NO_IMAGE)}" alt="${esc(d.title)}" loading="lazy" referrerpolicy="no-referrer">
        ${d.country_label ? `<span class="dish-card-country">${esc(d.country_label)}</span>` : ''}
      </div>
      <div class="pdc-body">
        <h3 class="pdc-title">${esc(d.title)}</h3>
        ${alt}
        ${found}
        ${missing}
      </div>
      <div class="pdc-foot">
        <span class="pdc-place">${esc(d.region || '')}</span>
        <span class="pdc-select">Choose <span class="pdc-arrow" aria-hidden="true">&rarr;</span></span>
      </div>`;
    const img = button.querySelector('img');
    img.addEventListener('error', () => { img.onerror = null; img.src = NO_IMAGE; }, { once: true });
    button.dataset.country = d.country_label || '';
    button.addEventListener('click', () => choose(d));
    return button;
  }

  function showPopup(data) {
    const n = data.total;
    const m = data.partial_total || 0;
    const partial = data.partial || [];
    const names = data.items.map((i) => i.value);
    // A general heading: every dish found (both sections), the details are in the sections
    const found = n + m;
    popupTitle.textContent = found ? `${found} Dish${found === 1 ? '' : 'es'} Found` : 'No Dishes Found';
    popupDesc.textContent = found
      ? 'Dishes with your ingredients. Choose a dish to compare it with the same target country.'
      : `No dish uses any of these: ${names.join(', ')}.`;

    // Two tabs: "Uses all" and "Uses some"; pages run inside a tab
    const all = data.dishes.map((d) => ({ d, every: true })).concat(partial.map((d) => ({ d, every: false })));
    const nAll = data.dishes.length;
    const nSome = partial.length;
    let tab = nAll ? 'all' : 'some';
    let page = 0;
    // Both labels always show (when something is found); a tab with no dishes is greyed out
    tabs.hidden = !(nAll || nSome);
    tabAll.disabled = !nAll;
    tabSome.disabled = !nSome;
    tabAll.textContent = `Uses all ${names.length} (${nAll})`;
    tabSome.textContent = `Uses some (${nSome})`;
    const pickTab = (name) => { tab = name; page = 0; render(); popupBox.scrollTop = 0; };
    tabAll.onclick = () => pickTab('all');
    tabSome.onclick = () => pickTab('some');

    function render() {
      tabAll.setAttribute('aria-selected', String(tab === 'all'));
      tabSome.setAttribute('aria-selected', String(tab === 'some'));
      const shown = all.filter((x) => x.every === (tab === 'all'));
      const pages = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
      page = Math.min(page, pages - 1);
      const slice = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((x) => x.d);
      // One section at a time (the tab says which), so no section headings
      allHead.hidden = true;
      partialHead.hidden = true;
      popupGrid.replaceChildren(...(tab === 'all' ? slice.map(dishButton) : []));
      partialGrid.replaceChildren(...(tab === 'some' ? slice.map(dishButton) : []));
      allSection.hidden = tab !== 'all';
      partialSection.hidden = tab !== 'some';
      // Floating pages bar: only when this tab has more than PAGE_SIZE dishes
      pager.hidden = shown.length <= PAGE_SIZE;
      pagerInfo.innerHTML = `Page ${page + 1} of ${pages}<span class="pager-count"> · ${shown.length} dishes</span>`;
      pagerPrev.disabled = page === 0;
      pagerNext.disabled = page >= pages - 1;
      pagerPrev.onclick = () => { page -= 1; render(); popupBox.scrollTop = 0; };
      pagerNext.onclick = () => { page += 1; render(); popupBox.scrollTop = 0; };
    }

    // The picked ingredients as tags under the heading (same colours as on the page, no counts)
    popupCountries.hidden = !picked.size;
    popupCountries.replaceChildren(...[...picked.values()].map((p) => {
      const tag = document.createElement('span');
      tag.className = `entity-chip item-dishes-tag ${p.color}`;
      tag.textContent = p.label;
      return tag;
    }));
    render();
    popup.classList.remove('hidden');
    document.documentElement.classList.add('item-dishes-open');
    popupBox.scrollTop = 0;
    popupClose.focus();
  }

  function closePopup() {
    popup.classList.add('hidden');
    document.documentElement.classList.remove('item-dishes-open');
    findBtn.focus();
  }

  function choose(d) {
    form.elements.dish_name.value = d.title;
    form.elements.recipe_id.value = d.recipe_id ?? '';
    form.elements.origin_country.value = d.country_value;
    popupTitle.textContent = 'Comparing…';
    form.submit();
  }

  findBtn.addEventListener('click', async () => {
    if (!picked.size) return;
    const params = new URLSearchParams();
    picked.forEach((p) => params.append(p.kind, p.value));
    findBtn.disabled = true;
    findBtn.classList.add('is-loading');
    try {
      const r = await fetch(`/api/dishes-with-items?${params}`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(String(r.status));
      showPopup(await r.json());
    } catch (e) {
      status.textContent = 'Could not load the dishes. Please try again.';
    } finally {
      findBtn.classList.remove('is-loading');
      findBtn.disabled = picked.size === 0;
    }
  });

  // The toolbar (tabs + ingredients) sticks at the top: mark it while it is stuck
  const toolbar = document.getElementById('item-dishes-toolbar');
  if (toolbar) {
    // An empty marker where the toolbar would be: once it scrolls above the toolbar, the toolbar is stuck
    const marker = document.createElement('div');
    marker.setAttribute('aria-hidden', 'true');
    toolbar.before(marker);
    const markStuck = () => {
      toolbar.classList.toggle('is-stuck', marker.getBoundingClientRect().top < toolbar.getBoundingClientRect().top - 1);
    };
    popupBox.addEventListener('scroll', markStuck, { passive: true });
  }

  popupClose.addEventListener('click', closePopup);
  popup.addEventListener('click', (e) => { if (e.target === popup) closePopup(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !popup.classList.contains('hidden')) closePopup();
  });

  update();
})();
