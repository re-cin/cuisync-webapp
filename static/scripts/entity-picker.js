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
    const region = (d.region || '').toLowerCase();
    const tagList = (d.tags || []).filter((t) => !region || t.toLowerCase() !== region);
    const tags = tagList.length
      ? `<div class="dish-card-tags pdc-tags">${tagList.slice(0, 4).map((t) => `<span class="tag-chip small">${esc(t)}</span>`).join('')}</div>` : '';
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
        ${tags}
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
    const list = names.join(' + ');
    popupTitle.textContent = n ? `${n} ${n === 1 ? 'Dish Uses' : 'Dishes Use'} All of Them`
      : (m ? 'No Dish Uses All of Them' : 'No Dishes Found');
    popupDesc.textContent = n || m
      ? `Ingredients: ${list}. Choose a dish to compare it with the same target country.`
      : `No dish uses any of these: ${names.join(', ')}.`;

    // Country pills: how many dishes come from each country; click one to show only those
    const all = data.dishes.concat(partial);
    const counts = new Map();
    all.forEach((d) => { if (d.country_label) counts.set(d.country_label, (counts.get(d.country_label) || 0) + 1); });
    const countries = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    popupCountries.hidden = countries.length < 2;
    popupCountries.replaceChildren(...(countries.length < 2 ? [] : countries.map(([c, k]) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'country-pill';
      pill.setAttribute('aria-pressed', 'false');
      pill.innerHTML = `<span>${esc(c)}</span><span class="country-pill-count">${k}</span>`;
      pill.addEventListener('click', () => {
        const on = pill.getAttribute('aria-pressed') !== 'true';
        popupCountries.querySelectorAll('.country-pill').forEach((x) => x.setAttribute('aria-pressed', 'false'));
        pill.setAttribute('aria-pressed', String(on));
        popupBox.querySelectorAll('.popup-dish-card[data-country]').forEach((card) => { card.hidden = on && card.dataset.country !== c; });
        popupBox.querySelectorAll('.item-dishes-section').forEach((sec) => {
          sec.hidden = !sec.querySelector('.popup-dish-card:not([hidden])');
        });
      });
      return pill;
    })));

    // Section 1: dishes that use every picked ingredient
    popupGrid.replaceChildren(...data.dishes.map(dishButton));
    allHead.hidden = !n || !m;   // a heading only when there are two sections
    allHead.textContent = `Uses all ${names.length} (${n})`;
    allSection.hidden = !n;
    // Section 2: dishes that use only some of them (more of them first)
    partialHead.textContent = `Uses only some of them (${m}${m > partial.length ? `, showing ${partial.length}` : ''})`;
    partialGrid.replaceChildren(...partial.map(dishButton));
    partialSection.hidden = !m || names.length < 2;
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

  popupClose.addEventListener('click', closePopup);
  popup.addEventListener('click', (e) => { if (e.target === popup) closePopup(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !popup.classList.contains('hidden')) closePopup();
  });

  update();
})();
