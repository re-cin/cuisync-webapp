/* Text search on the home page (panel revisions).
   - Search by dish name only (the ingredient search is now a sub-tab of "Search by tag",
     see tag-search.js)
   - The country of origin is filled in from the chosen dish, like the image search
   - A vague dish name ("adobo") opens a popup with every matching dish. The user picks
     one, then CuiSync compares it with the target country.
   - The popup is shared with the tag and image searches (window.CuiSyncPopup).
   Needs DISH_RECORDS / recordsForName() from index.html and showErrorModal(). */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('text-search-form');
  if (!form) return;

  const dishInput = document.getElementById('dish-name-input');
  const dropdownList = document.getElementById('dish-dropdown-list');
  const recipeIdInput = document.getElementById('dish-recipe-id');
  const originSelect = document.getElementById('text-origin');
  const originDisplay = document.getElementById('text-origin-display');
  const targetSelect = document.getElementById('text-target');
  const submitButton = form.querySelector('button[type="submit"]');
  const selectPrompt = document.getElementById('text-select-prompt');
  const selectPromptCount = document.getElementById('text-select-prompt-count');

  const selectedCard = document.getElementById('text-selected-dish');
  const selectedName = document.getElementById('text-selected-name');
  const selectedAlt = document.getElementById('text-selected-alt');
  const selectedCountry = document.getElementById('text-selected-country');
  const selectedImage = document.getElementById('text-selected-image');

  const popup = document.getElementById('dish-results-overlay');
  const popupTitle = document.getElementById('dish-results-title');
  const popupDesc = document.getElementById('dish-results-desc');
  const popupCuisines = document.getElementById('dish-results-cuisines');
  const popupGrid = document.getElementById('dish-results-grid');
  const popupClose = document.getElementById('dish-results-close');
  const NO_IMAGE = '/static/assets/no-image.svg';

  // The checks are done here, so the browser's own "fill in this field" bubbles don't
  // stop the popup from opening before a target country is chosen
  form.noValidate = true;

  const mode = 'dish';
  let lastResults = null;      // so "Click to change" can reopen the popup
  let submitAfterPick = false;

  // ---------------- country of origin ----------------
  function updateOriginDisplay() {
    const option = originSelect.options[originSelect.selectedIndex];
    const hasCountry = Boolean(originSelect.value && option);
    originDisplay.textContent = hasCountry ? option.textContent : 'Country of Origin';
    originDisplay.classList.toggle('is-placeholder', !hasCountry);
  }

  function setOrigin(country) {
    const option = [...originSelect.options].find(
      (o) => o.value && o.value.toLowerCase() === String(country || '').toLowerCase()
    );
    originSelect.value = option ? option.value : '';
    updateOriginDisplay();
  }
  originSelect.addEventListener('change', () => {
    updateOriginDisplay();
    if (typeof updateInputActions === 'function') updateInputActions();
    updateTargetVisibility();
  });

  // ---------------- chosen dish ----------------
  function updateButton() {
    submitButton.textContent = 'SEARCH THE DISH';
  }

  function clearPick() {
    recipeIdInput.value = '';
    setOrigin('');
    selectedCard.style.display = 'none';
    updateButton();
    updateTargetVisibility();
  }

  // The target country only shows once a dish is chosen (less confusing for first-time users)
  const targetGroup = document.getElementById('text-target-group');
  function updateTargetVisibility() {
    const show = Boolean(originSelect.value);
    if (targetGroup.hidden === !show) return;
    targetGroup.hidden = !show;
    if (show) {
      targetGroup.classList.remove('is-revealed');
      void targetGroup.offsetWidth;
      targetGroup.classList.add('is-revealed');
    }
  }

  function pickDish(dish, showCard) {
    recipeIdInput.value = dish.recipe_id ?? '';
    dishInput.value = dish.title;
    updateClearBtn();
    setOrigin(dish.country_value || dish.country);
    if (showCard) {
      selectedName.textContent = dish.title;
      selectedAlt.textContent = dish.alternative_title || '';
      selectedAlt.style.display = dish.alternative_title ? '' : 'none';
      selectedCountry.textContent = dish.country_label || dish.country;
      // Same "no image" placeholder as the popup cards when a dish has no photo
      selectedImage.onerror = () => { selectedImage.onerror = null; selectedImage.src = NO_IMAGE; };
      selectedImage.src = dish.image_link || NO_IMAGE;
      selectedImage.alt = dish.title;
      selectedImage.style.display = '';
      selectedCard.style.display = 'flex';
    }
    updateButton();
    updateTargetVisibility();
  }

  function hasPick() {
    return Boolean(dishInput.value.trim() && originSelect.value);
  }

  // Same as the image search: a "Selected dish" card after picking from the popup, and a
  // "Choose a dish" card when the popup was closed without picking one.
  function updateInputActions() {
    const several = Boolean(lastResults && lastResults.dishes.length > 1);
    const popupOpen = !popup.classList.contains('hidden');
    const notPicked = several && !originSelect.value;
    // Popup closed without a pick: a "Choose a dish" button inside the search bar reopens it
    const chooseBtn = document.getElementById('dish-choose-btn');
    selectPrompt.style.display = 'none';
    if (chooseBtn) {
      const showChoose = notPicked && !popupOpen;
      chooseBtn.hidden = !showChoose;
      chooseBtn.textContent = showChoose ? `Choose a dish (${lastResults.dishes.length}) \u25be` : '';
      dishInput.classList.toggle('has-choose-btn', showChoose);
    }
    // "Click to change" only makes sense when there were other dishes to choose from
    const changeLabel = selectedCard.querySelector('.selected-dish-change');
    if (changeLabel) changeLabel.style.display = several ? '' : 'none';
    selectedCard.setAttribute('aria-label', several ? 'Change selected dish' : 'Selected dish');
    // Only one dish: nothing to change, so the card isn't clickable
    selectedCard.classList.toggle('is-static', !several);
    selectedCard.tabIndex = several ? 0 : -1;
  }

  selectPrompt.addEventListener('click', () => {
    if (lastResults) showPopup(lastResults);
  });
  document.getElementById('dish-choose-btn').addEventListener('click', () => {
    if (lastResults) showPopup(lastResults);
  });

  // ---------------- dish name autocomplete ----------------
  let activeIndex = -1;
  let currentMatches = [];

  function titleCase(text) {
    return String(text || '').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // One row per recipe. Order: exact match, starts with, contains.
  function getTopMatches(query, limit = 8) {
    const q = query.trim().toLowerCase();
    if (!q || typeof DISH_RECORDS === 'undefined') return [];
    const rows = [];
    for (const record of DISH_RECORDS) {
      const labels = [{ label: record.title, isAlt: false },
        ...record.alternatives.map((label) => ({ label, isAlt: true }))];
      let best = null;
      for (const { label, isAlt } of labels) {
        const lower = label.toLowerCase();
        const rank = lower === q ? 0 : lower.startsWith(q) ? 1 : lower.includes(q) ? 2 : -1;
        if (rank < 0) continue;
        if (!best || rank < best.rank || (rank === best.rank && best.isAlt && !isAlt)) {
          best = { label, isAlt, rank };
        }
      }
      if (best) rows.push({ ...best, record });
    }
    return rows
      .sort((x, y) => x.rank - y.rank || x.label.localeCompare(y.label) || x.record.title.localeCompare(y.record.title))
      .slice(0, limit);
  }

  function hideDropdown() {
    dropdownList.classList.add('hidden');
    dropdownList.innerHTML = '';
    activeIndex = -1;
  }

  function renderDropdown(matches) {
    dropdownList.innerHTML = '';
    activeIndex = -1;
    currentMatches = matches;
    if (!matches.length) return hideDropdown();
    matches.forEach((match) => {
      const li = document.createElement('li');
      li.className = 'dropdown-item';
      li.appendChild(document.createTextNode(match.label.trim()));
      const notes = [];
      if (match.isAlt) notes.push('alternative name for ' + match.record.title);
      else if (match.record.alternatives.length) notes.push('alternative name: ' + match.record.alternatives.join(', '));
      if (match.record.country) notes.push(titleCase(match.record.country));
      if (notes.length) {
        const hint = document.createElement('span');
        hint.className = 'dropdown-alt';
        hint.textContent = notes.join(' · ');
        li.appendChild(hint);
      }
      li.addEventListener('mousedown', (e) => {
        e.preventDefault();
        chooseMatch(match);
      });
      dropdownList.appendChild(li);
    });
    dropdownList.classList.remove('hidden');
  }

  // Picking a row picks that exact recipe, so its country is the origin
  // Picking a row also shows the "Selected dish" card (photo filled in from the server)
  let detailRequest = 0;
  function chooseMatch(match) {
    const dish = {
      title: match.label.trim(),
      recipe_id: match.record.id,
      country_value: match.record.country.toLowerCase(),
      country_label: titleCase(match.record.country),
      alternative_title: match.isAlt ? match.record.title : (match.record.alternatives[0] || ''),
      image_link: '',
    };
    pickDish(dish, true);
    hideDropdown();
    updateInputActions();
    loadDishDetails(dish);
  }

  async function loadDishDetails(dish) {
    const ticket = ++detailRequest;
    try {
      const r = await fetch(`/api/find-dishes?mode=dish&q=${encodeURIComponent(dish.title)}`,
        { credentials: 'same-origin' });
      const data = await r.json();
      // Ignore the answer if the user picked or typed something else meanwhile
      if (ticket !== detailRequest || recipeIdInput.value !== String(dish.recipe_id ?? '')) return;
      const full = (data.dishes || []).find((d) => String(d.recipe_id) === String(dish.recipe_id));
      if (full) pickDish(full, true);
    } catch (e) { /* keep the card without a photo */ }
  }

  // × inside the search bar: clears the dish name (and the picked dish and target country)
  const clearInputBtn = document.getElementById('dish-clear-input');
  function updateClearBtn() {
    const btn = document.getElementById('dish-clear-input');
    if (btn) btn.hidden = !dishInput.value;
  }
  clearInputBtn.addEventListener('click', () => {
    dishInput.value = '';
    dishInput.dispatchEvent(new Event('input'));
    hideDropdown();
    dishInput.focus();
  });

  dishInput.addEventListener('input', () => {
    updateClearBtn();
    lastResults = null;
    clearPick();
    // Search bar cleared: start over, so the target country is reset too
    if (!dishInput.value.trim() && targetSelect.value) {
      targetSelect.value = '';
      targetSelect.classList.remove('needs-attention');
      targetSelect.dispatchEvent(new Event('change'));
    }
    renderDropdown(getTopMatches(dishInput.value));
    // No auto-pick while typing: "sushi" matches one record exactly, but the search should
    // still show every sushi dish in the popup. Picking a row from the dropdown still picks it.
    updateInputActions();
  });
  dishInput.addEventListener('focus', () => {
    if (dishInput.value.trim() && !originSelect.value) renderDropdown(getTopMatches(dishInput.value));
  });
  dishInput.addEventListener('keydown', (e) => {
    const items = dropdownList.querySelectorAll('.dropdown-item');
    if (dropdownList.classList.contains('hidden') || !items.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      activeIndex = e.key === 'ArrowDown'
        ? (activeIndex + 1) % items.length
        : (activeIndex - 1 + items.length) % items.length;
      items.forEach((item, i) => item.classList.toggle('highlighted', i === activeIndex));
      items[activeIndex].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      chooseMatch(currentMatches[activeIndex]);
    } else if (e.key === 'Escape') {
      hideDropdown();
    }
  });
  document.addEventListener('click', (e) => {
    if (e.target !== dishInput && !dropdownList.contains(e.target)) hideDropdown();
  });

  // ---------------- popup ----------------
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let popupOnClose = null;   // the image search runs its own code when the popup closes

  function closePopup() {
    popup.classList.add('hidden');
    submitAfterPick = false;
    updateInputActions();
    const onClose = popupOnClose;
    popupOnClose = null;
    if (onClose) onClose();
  }

  function dishButton(d, onPick) {
    const button = document.createElement('button');
    button.type = 'button';
    // Same card as the Dish Dictionary ("Dishes" page): photo, name, other name, then a
    // footer with the region and "Choose →"
    button.className = 'dish-card popup-dish-card pdc';
    const found = (d.found || []).length
      ? `<p class="dish-card-found">Uses: ${d.found.map(([want, have]) => `<span title="you typed ${esc(want)}">${esc(have)}</span>`).join(', ')}</p>`
      : ((d.ingredients || []).length ? `<p class="dish-card-ings pdc-ings">${esc(d.ingredients.join(', '))}</p>` : '');
    const region = (d.region || '').toLowerCase();
    const tagList = (d.tags || []).filter((t) => !region || t.toLowerCase() !== region);
    const tags = tagList.length
      ? `<div class="dish-card-tags pdc-tags">${tagList.slice(0, 4).map((t) => `<span class="tag-chip small">${esc(t)}</span>`).join('')}</div>` : '';
    const alt = d.alternative_title && d.alternative_title.toLowerCase() !== d.title.toLowerCase()
      ? `<p class="pdc-alt">${esc(d.alternative_title)}</p>` : '';
    // Country sits on the photo; the footer shows the region (so no region tag chip)
    const place = esc(d.region || '');
    button.innerHTML = `
      <div class="dish-card-img">
        <img src="${esc(d.image_link || NO_IMAGE)}" alt="${esc(d.title)}" loading="lazy">
        ${d.country_label || d.country ? `<span class="dish-card-country">${esc(d.country_label || d.country)}</span>` : ''}
        ${d.score != null ? `<span class="dish-card-score">${d.score}% match</span>` : ''}
      </div>
      <div class="pdc-body">
        <h3 class="pdc-title">${esc(d.title)}</h3>
        ${alt}
        ${found}
        ${tags}
      </div>
      <div class="pdc-foot">
        <span class="pdc-place">${place}</span>
        <span class="pdc-select">Choose <span class="pdc-arrow" aria-hidden="true">&rarr;</span></span>
      </div>`;
    const img = button.querySelector('img');
    img.addEventListener('error', () => { img.onerror = null; img.src = NO_IMAGE; }, { once: true });
    button.dataset.country = d.country_label || d.country || '';
    button.addEventListener('click', () => onPick(d));
    return button;
  }

  // onPick: what happens when a dish is clicked (the tag tab passes its own)
  function showPopup(data, onPick = choose, onClose = null) {
    if (onPick === choose) lastResults = data;
    popupOnClose = onClose;
    const n = data.total;
    if (data.mode === 'image') {
      popupTitle.textContent = 'Choose a Dish from the Menu';
      popupDesc.textContent = `We found ${n} dishes in the image. Choose the one you want to compare.`;
    } else if (data.mode === 'tags') {
      const parts = [];
      if (data.tags && data.tags.length) parts.push(`tagged ${data.tags.join(' + ')}`);
      if (data.ingredients && data.ingredients.length) parts.push(`using ${data.ingredients.join(', ')}`);
      if (data.countries && data.countries.length) parts.push(`from ${data.countries.join(' or ')}`);
      popupTitle.textContent = `${n} Dish${n === 1 ? '' : 'es'} Found`;
      popupDesc.textContent = `Dishes ${parts.join(' and ')}`
        + (n > data.dishes.length ? ` (showing the first ${data.dishes.length})` : '')
        + '. '
        + (data.ingredients && data.ingredients.length ? 'Dishes that use more of your ingredients come first. ' : '')
        + 'Choose the one you want to compare.'
        + (data.unknown && data.unknown.length ? ` Not in our ingredient list: ${data.unknown.join(', ')}.` : '');
    } else if (data.mode === 'ingredients' && data.country) {
      popupTitle.textContent = `${n} ${data.country_label} Dish${n === 1 ? '' : 'es'} Use Your Ingredients`;
      popupDesc.textContent = `Only dishes from ${data.country_label} are shown because it is your target country. `
        + 'Choose "Any country" to see every country.'
        + (data.unknown && data.unknown.length ? ` Not in our ingredient list: ${data.unknown.join(', ')}.` : '');
    } else if (data.mode === 'ingredients') {
      popupTitle.textContent = `${n} Dish${n === 1 ? '' : 'es'} Use Your Ingredients`;
      popupDesc.textContent = 'Choose the dish you want to compare. Dishes that use more of your ingredients come first.'
        + (data.unknown && data.unknown.length ? ` Not in our ingredient list: ${data.unknown.join(', ')}.` : '');
    } else {
      popupTitle.textContent = data.close_spelling ? 'Did You Mean One of These?' : `${n} Dishes Found`;
      popupDesc.textContent = data.close_spelling
        ? `No dish name contains "${dishInput.value.trim()}". These have a similar spelling.`
        : `We found several dishes for "${dishInput.value.trim()}". Choose the one you want to compare.`;
    }

    // Small country pills: how many of these dishes come from each country.
    // Clicking one shows only that country's dishes; clicking it again shows them all.
    const counts = new Map();
    data.dishes.forEach((d) => {
      const c = d.country_label || d.country;
      if (c) counts.set(c, (counts.get(c) || 0) + 1);
    });
    const countries = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    popupCuisines.hidden = countries.length < 2;
    popupCuisines.className = 'popup-countries';
    popupCuisines.setAttribute('role', 'group');
    popupCuisines.setAttribute('aria-label', 'Show dishes from one country');
    popupCuisines.replaceChildren(...(countries.length < 2 ? [] : countries.map(([c, k]) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'country-pill';
      pill.setAttribute('aria-pressed', 'false');
      pill.innerHTML = `<span>${esc(c)}</span><span class="country-pill-count">${k}</span>`;
      pill.addEventListener('click', () => {
        const on = pill.getAttribute('aria-pressed') !== 'true';
        popupCuisines.querySelectorAll('.country-pill').forEach((x) => x.setAttribute('aria-pressed', 'false'));
        pill.setAttribute('aria-pressed', String(on));
        popupGrid.querySelectorAll('[data-country]').forEach((card) => {
          card.hidden = on && card.dataset.country !== c;
        });
      });
      return pill;
    })));

    popupGrid.replaceChildren(...data.dishes.map((d) => dishButton(d, onPick)));
    popup.classList.remove('hidden');
    updateInputActions();
    popup.querySelector('.dish-results-box').scrollTop = 0;
    popupClose.focus();
  }

  function choose(d) {
    const wantSubmit = submitAfterPick;
    closePopup();
    // Like the image search: the dish shows as a "Selected dish" card under the search bar
    pickDish(d, true);
    updateInputActions();
    targetSelect.dispatchEvent(new CustomEvent('picker:reveal'));
    if (!targetSelect.value) {
      // Open the target country picker so the user can finish the search
      targetSelect.classList.add('needs-attention');
      targetSelect.dispatchEvent(new CustomEvent('picker:open', { detail: { submitAfter: wantSubmit } }));
      return;
    }
    if (wantSubmit) sendForm();
  }

  popupClose.addEventListener('click', closePopup);
  popup.addEventListener('click', (e) => { if (e.target === popup) closePopup(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !popup.classList.contains('hidden')) closePopup();
  });
  selectedCard.addEventListener('click', () => {
    if (lastResults && lastResults.dishes.length > 1) showPopup(lastResults);
    else dishInput.focus();
  });
  targetSelect.addEventListener('change', (e) => {
    targetSelect.classList.remove('needs-attention');
    // Picked from the picker right after choosing a dish: run the search
    if (e.detail && e.detail.submitAfter && hasPick()) sendForm();
  });

  // The tag tab (tag-search.js) uses the same popup
  window.CuiSyncPopup = { show: showPopup, close: closePopup };

  // ---------------- search ----------------
  async function findDishes(searchMode, q) {
    window.CuiSyncBusy.start(submitButton);
    try {
      const r = await fetch(`/api/find-dishes?mode=${searchMode}&q=${encodeURIComponent(q)}`,
        { credentials: 'same-origin', signal: window.CuiSyncBusy.signal });
      return await r.json();
    } catch (e) {
      if (e.name === 'AbortError') return null;   // cancelled with the x on the busy screen
      throw e;
    } finally {
      window.CuiSyncBusy.stop();
    }
  }

  function sendForm() {
    window.CuiSyncBusy.start(submitButton);   // the comparison page takes a moment
    HTMLFormElement.prototype.submit.call(form);
  }

  async function runSearch(autoSubmit) {
    submitAfterPick = autoSubmit;
    const q = dishInput.value.trim();
    if (!q) {
      showErrorModal('Enter a Dish', 'Type a dish name, for example Pho, Adobo or Ramen.');
      return;
    }
    const data = await findDishes('dish', q);
    if (!data) return;
    if (!data.total) {
      showErrorModal("We Can't Find Your Dish",
        `"${q}" isn't in our recipe database yet. Please check the spelling or try a more general dish name.`);
      return;
    }
    if (data.total === 1 && !data.close_spelling) {
      choose(data.dishes[0]);
      return;
    }
    showPopup(data);
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    hideDropdown();
    // A dish is already chosen: go straight to the comparison
    if (hasPick()) {
      if (!targetSelect.value) {
        // Open the target country picker instead of an error popup
        targetSelect.classList.add('needs-attention');
        targetSelect.dispatchEvent(new CustomEvent('picker:open'));
        return;
      }
      sendForm();
      return;
    }
    runSearch(Boolean(targetSelect.value));
  });

  // ---------------- open a past search: /?mode=dish&q=... ----------------
  // (/?mode=ingredients&q=... is handled by tag-search.js, in the Ingredient sub-tab)
  const params = new URLSearchParams(window.location.search);
  if (params.get('mode') === 'dish' && params.get('q')) {
    dishInput.value = params.get('q');
    runSearch(false);
  }

  // ---------------- "Find similar dishes" on the Dishes page: /?dish=<index> ----------------
  // Puts that exact dish in the search bar as the selected dish, then the user picks a country
  const dishIdx = params.get('dish');
  if (dishIdx && /^\d+$/.test(dishIdx)) {
    if (typeof window.switchTab === 'function') window.switchTab('text');
    // Go straight to the search section (the browser's own #search-section jump can be undone
    // by the page still loading)
    const goToSearch = () => document.getElementById('search-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    (async () => {
      window.CuiSyncBusy.start(submitButton);
      try {
        const info = await fetch(`/api/dish/${dishIdx}`, { credentials: 'same-origin', signal: window.CuiSyncBusy.signal }).then((r) => r.json());
        if (!info || !info.title) return;
        dishInput.value = info.title;
        updateClearBtn();
        const data = await fetch(`/api/find-dishes?mode=dish&q=${encodeURIComponent(info.title)}`,
          { credentials: 'same-origin', signal: window.CuiSyncBusy.signal }).then((r) => r.json());
        const match = (data.dishes || []).find((d) => String(d.recipe_id) === String(info.recipe_id))
          || ((data.dishes || []).length === 1 ? data.dishes[0] : null);
        if (!match) { window.CuiSyncBusy.stop(); goToSearch(); runSearch(false); return; }
        lastResults = data;
        pickDish(match, true);
        updateInputActions();
        goToSearch();
        // Clean the address so a refresh doesn't pick it again
        history.replaceState(null, '', location.pathname + location.hash);
      } catch (e) { /* leave the search bar as it is */ }
      finally { window.CuiSyncBusy.stop(); }
    })();
  }
  updateOriginDisplay();
  updateInputActions();
});
