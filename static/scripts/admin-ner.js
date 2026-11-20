// CuiSync Admin: "Extract ingredients" on the Add / Edit Dish page.
// Sends the pasted recipe text to /api/extract-ingredients (admin_app.py) (BERT NER, ner.py),
// shows the ingredients as chips, then adds them to the Ingredients field.
(() => {
  const textBox = document.getElementById('recipe-text');
  const button = document.getElementById('extract-btn');
  const statusEl = document.getElementById('extract-status');
  const results = document.getElementById('ner-results');
  const chipList = document.getElementById('ner-chips');
  const addBtn = document.getElementById('ner-add');
  const replaceBtn = document.getElementById('ner-replace');
  const field = document.getElementById('ingredient_text');
  if (!button || !field) return;

  let items = [];

  const currentIngredients = () =>
    field.value.split(/[,\n]/).map((s) => s.trim().toLowerCase()).filter(Boolean);

  // Three groups, each with a plain explanation. "Check these" comes first because it needs a choice.
  const GROUPS = [
    { status: 'suggested', title: 'Check these',
      text: 'Spelled a little differently from an ingredient already in the dataset. Pick the one you mean.' },
    { status: 'new', title: 'New ingredients',
      text: 'Not in the dataset yet. They are added (and get a vector) when you save the dish.' },
    { status: 'existing', title: 'Already in the dataset',
      text: 'These match an ingredient the dataset already has.' },
  ];

  function removeButton(index, name) {
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'chip-remove';
    remove.setAttribute('aria-label', `Remove ${name}`);
    remove.title = 'Remove from this list';
    remove.textContent = '×';
    remove.addEventListener('click', () => { items.splice(index, 1); render(); });
    return remove;
  }

  function render() {
    chipList.replaceChildren();
    const have = new Set(currentIngredients());
    GROUPS.forEach((group) => {
      const members = items.map((item, index) => ({ item, index })).filter(({ item }) => item.status === group.status);
      if (!members.length) return;
      const box = document.createElement('section');
      box.className = `ner-group ner-group-${group.status}`;
      const head = document.createElement('h3');
      head.className = 'ner-group-title';
      head.innerHTML = `<span class="ner-dot" aria-hidden="true"></span><span></span><span class="ner-count"></span>`;
      head.children[1].textContent = group.title;
      head.children[2].textContent = members.length;
      const text = document.createElement('p');
      text.className = 'ner-group-text';
      text.textContent = group.text;
      box.append(head, text);

      if (group.status === 'suggested') {
        members.forEach(({ item, index }) => {
          const row = document.createElement('div');
          row.className = 'ner-choice';
          const found = document.createElement('span');
          found.className = 'ner-choice-found';
          found.innerHTML = 'Found <b></b>';
          found.querySelector('b').textContent = item.name;
          const use = document.createElement('button');
          use.type = 'button';
          use.className = 'ner-option ner-option-main';
          use.textContent = `Use “${item.suggestion}”`;
          use.title = 'Use the name the dataset already has (recommended)';
          use.addEventListener('click', () => { items[index] = { name: item.suggestion, status: 'existing' }; render(); });
          const keep = document.createElement('button');
          keep.type = 'button';
          keep.className = 'ner-option';
          keep.textContent = `Keep “${item.name}” as new`;
          keep.addEventListener('click', () => { items[index] = { name: item.name, status: 'new' }; render(); });
          row.append(found, use, keep, removeButton(index, item.name));
          box.appendChild(row);
        });
      } else {
        const list = document.createElement('div');
        list.className = 'chip-list';
        members.forEach(({ item, index }) => {
          const chip = document.createElement('span');
          chip.className = `chip chip-${item.status}`;
          chip.textContent = item.name;
          if (have.has(item.name.toLowerCase())) {
            chip.classList.add('chip-in-field');
            chip.title = 'Already in the Ingredients field';
            const tag = document.createElement('span');
            tag.className = 'chip-tag';
            tag.textContent = '✓ in field';
            chip.appendChild(tag);
          }
          chip.appendChild(removeButton(index, item.name));
          list.appendChild(chip);
        });
        box.appendChild(list);
      }
      chipList.appendChild(box);
    });
    results.hidden = items.length === 0;
    updateSummary();
  }

  function updateSummary() {
    const summary = document.getElementById('ner-summary');
    const open = items.filter((i) => i.status === 'suggested').length;
    const n = items.length;
    addBtn.querySelector('span').textContent = `Add ${n} to ingredients`;
    if (summary) {
      summary.textContent = open
        ? `${open} still to check. If you skip it, it is added as written.`
        : `${n} ingredient${n === 1 ? '' : 's'} ready to add.`;
      summary.classList.toggle('ner-summary-warn', open > 0);
    }
  }

  function writeToField(replace) {
    const names = items.map((i) => i.name.toLowerCase());
    const merged = replace ? names : [...currentIngredients(), ...names];
    field.value = [...new Set(merged)].join(', ');
    field.dispatchEvent(new Event('input'));  // refreshes the chip preview under the field
    statusEl.textContent = '';
    field.scrollIntoView({ behavior: 'smooth', block: 'center' });
    field.focus({ preventScroll: true });
    render();                                 // marks the chips that are now in the field
  }
  addBtn.addEventListener('click', () => writeToField(false));
  replaceBtn.addEventListener('click', async () => {
    if (!field.value.trim()) { writeToField(true); return; }
    const ok = await window.cuisyncConfirm({
      title: 'Replace the ingredients?',
      message: `The Ingredients field already has ${currentIngredients().length} ingredient(s). They will be replaced by the ${items.length} found here.`,
      ok: 'Replace', icon: false,
    });
    if (ok) writeToField(true);
  });

  button.addEventListener('click', async () => {
    const text = textBox.value.trim();
    if (!text) { statusEl.textContent = 'Paste the recipe text first.'; textBox.focus(); return; }
    button.disabled = true;
    statusEl.textContent = 'Finding ingredients… (the first time can take a minute while the model loads)';
    try {
      const response = await fetch(window.CUISYNC_EXTRACT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await response.json().catch(() => ({ message: 'Your login may have expired. Reload the page.' }));
      if (!response.ok) { statusEl.textContent = data.message || 'Extraction failed.'; return; }
      items = data.ingredients;
      const count = (s) => items.filter((i) => i.status === s).length;
      statusEl.classList.remove('status-done');
      statusEl.textContent = items.length
        ? `Found ${items.length} ingredient${items.length === 1 ? '' : 's'}` +
          (count('suggested') ? ` (${count('suggested')} to check)` : '') + '. Review them below.'
        : 'No ingredients were found in that text.';
      render();
    } catch (error) {
      statusEl.textContent = 'Could not reach the server. Please try again.';
    } finally {
      button.disabled = false;
    }
  });
})();

// "Extract from directions": cooking actions, cookware and utensils (ner.extract_directions)
(() => {
  const textBox = document.getElementById('directions-text');
  const button = document.getElementById('directions-btn');
  const statusEl = document.getElementById('directions-status');
  const results = document.getElementById('directions-results');
  const groups = [...document.querySelectorAll('[data-dir-group]')];
  if (!button || !groups.length) return;
  const found = { actions: [], cookware: [], utensils: [] };

  const box = document.getElementById('directions-groups');
  const summaryEl = document.getElementById('directions-summary');
  const addBtn = document.getElementById('directions-add');
  const fieldOf = (key) => document.getElementById(groups.find((g) => g.dataset.dirGroup === key).dataset.field);
  const titleOf = (key) => groups.find((g) => g.dataset.dirGroup === key).dataset.title;
  const singular = { actions: 'cooking action', cookware: 'cookware', utensils: 'utensil' };
  const inField = (key) => new Set((fieldOf(key)?.value || '').split(/[,\n]/).map((s) => s.trim().toLowerCase()).filter(Boolean));

  function removeButton(key, index, name) {
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'chip-remove';
    remove.setAttribute('aria-label', `Remove ${name}`);
    remove.title = 'Remove from this list';
    remove.textContent = '×';
    remove.addEventListener('click', () => { found[key].splice(index, 1); render(); });
    return remove;
  }

  function groupHead(title, count, text, color) {
    const section = document.createElement('section');
    section.className = `ner-group ner-group-${color}`;
    const head = document.createElement('h3');
    head.className = 'ner-group-title';
    head.innerHTML = '<span class="ner-dot" aria-hidden="true"></span><span></span><span class="ner-count"></span>';
    head.children[1].textContent = title;
    head.children[2].textContent = count;
    const p = document.createElement('p');
    p.className = 'ner-group-text';
    p.textContent = text;
    section.append(head, p);
    return section;
  }

  function render() {
    box.replaceChildren();
    const keys = groups.map((g) => g.dataset.dirGroup);

    // 1. Check these: names spelled a little differently from one the dataset has
    const toCheck = keys.flatMap((key) => found[key].map((item, index) => ({ key, item, index })))
      .filter(({ item }) => item.status === 'suggested' && item.suggestion);
    if (toCheck.length) {
      const section = groupHead('Check these', toCheck.length,
        'Spelled a little differently from one already in the dataset. Pick the one you mean.', 'suggested');
      toCheck.forEach(({ key, item, index }) => {
        const row = document.createElement('div');
        row.className = 'ner-choice';
        const label = document.createElement('span');
        label.className = 'ner-choice-found';
        label.innerHTML = 'Found <b></b> <span class="muted small"></span>';
        label.querySelector('b').textContent = item.name;
        label.querySelector('span').textContent = `(${singular[key]})`;
        const use = document.createElement('button');
        use.type = 'button';
        use.className = 'ner-option ner-option-main';
        use.textContent = `Use “${item.suggestion}”`;
        use.title = 'Use the name the dataset already has (recommended)';
        use.addEventListener('click', () => { found[key][index] = { name: item.suggestion, status: 'existing' }; render(); });
        const keep = document.createElement('button');
        keep.type = 'button';
        keep.className = 'ner-option';
        keep.textContent = `Keep “${item.name}” as new`;
        keep.addEventListener('click', () => { found[key][index] = { name: item.name, status: 'new' }; render(); });
        row.append(label, use, keep, removeButton(key, index, item.name));
        section.appendChild(row);
      });
      box.appendChild(section);
    }

    // 2. One group per field: Cooking actions, Cookware, Utensils
    keys.forEach((key) => {
      const members = found[key].map((item, index) => ({ item, index }))
        .filter(({ item }) => !(item.status === 'suggested' && item.suggestion));
      const newOnes = members.filter(({ item }) => item.status === 'new').length;
      const text = members.length
        ? `Go into the ${titleOf(key)} field.` + (newOnes ? ' Items marked “new” are added to the dataset when you save the dish.' : '')
        : 'None found.';
      const section = groupHead(titleOf(key), members.length, text, 'existing');
      if (members.length) {
        const have = inField(key);
        const list = document.createElement('div');
        list.className = 'chip-list';
        members.forEach(({ item, index }) => {
          const chip = document.createElement('span');
          chip.className = `chip chip-${item.status === 'new' ? 'new' : 'existing'}`;
          chip.textContent = item.name;
          const tag = (text, cls) => { const t = document.createElement('span'); t.className = cls; t.textContent = text; chip.appendChild(t); };
          if (item.status === 'new') tag('new', 'chip-tag chip-tag-new');
          if (have.has(item.name.toLowerCase())) {
            chip.classList.add('chip-in-field');
            chip.title = `Already in the ${titleOf(key)} field`;
            tag('✓ in field', 'chip-tag');
          }
          chip.appendChild(removeButton(key, index, item.name));
          list.appendChild(chip);
        });
        section.appendChild(list);
      }
      box.appendChild(section);
    });

    const total = keys.reduce((n, key) => n + found[key].length, 0);
    results.hidden = total === 0;
    addBtn.querySelector('span').textContent = `Add ${total} to fields`;
    summaryEl.textContent = toCheck.length
      ? `${toCheck.length} still to check. If you skip it, it is added as written.`
      : `${total} item${total === 1 ? '' : 's'} ready to add.`;
    summaryEl.classList.toggle('ner-summary-warn', toCheck.length > 0);
  }

  function writeFields(replace) {
    let total = 0;
    groups.forEach((g) => {
      const field = document.getElementById(g.dataset.field);
      const names = found[g.dataset.dirGroup].map((i) => i.name.toLowerCase());
      if (!field || (!names.length && !replace)) return;
      const current = replace ? [] : field.value.split(/[,\n]/).map((s) => s.trim().toLowerCase()).filter(Boolean);
      field.value = [...new Set([...current, ...names])].join(', ');
      field.dispatchEvent(new Event('input'));
      total += names.length;
    });
    statusEl.textContent = '';
    const first = document.getElementById(groups[0].dataset.field);
    if (first) { first.scrollIntoView({ behavior: 'smooth', block: 'center' }); first.focus({ preventScroll: true }); }
    render();
  }
  document.getElementById('directions-add').addEventListener('click', () => writeFields(false));
  document.getElementById('directions-replace').addEventListener('click', () => {
    window.cuisyncConfirm({
      title: 'Replace the cooking process?',
      message: 'The current actions, cookware and utensils will be replaced by the ones found here.',
      ok: 'Replace', icon: false,
    }).then((ok) => { if (ok) writeFields(true); });
  });

  button.addEventListener('click', async () => {
    const text = textBox.value.trim();
    if (!text) { statusEl.textContent = 'Paste the directions first.'; textBox.focus(); return; }
    button.disabled = true;
    statusEl.textContent = 'Reading the directions… (the first time can take a minute while the model loads)';
    try {
      const response = await fetch(window.CUISYNC_DIRECTIONS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await response.json().catch(() => ({ message: 'Your login may have expired. Reload the page.' }));
      if (!response.ok) { statusEl.textContent = data.message || 'Extraction failed.'; return; }
      const asItems = (list) => (list || []).map((i) => (typeof i === 'string' ? { name: i, status: 'existing' } : i));
      Object.assign(found, { actions: asItems(data.actions), cookware: asItems(data.cookware), utensils: asItems(data.utensils) });
      const all = [...found.actions, ...found.cookware, ...found.utensils];
      const toCheck = all.filter((i) => i.status === 'suggested').length;
      statusEl.classList.remove('status-done');
      statusEl.textContent = all.length
        ? `Found ${all.length} item${all.length === 1 ? '' : 's'}` + (toCheck ? ` (${toCheck} to check)` : '') + '. Review them below.'
        : 'No cooking actions, cookware or utensils were found in that text.';
      render();
    } catch (error) {
      statusEl.textContent = 'Could not reach the server. Please try again.';
    } finally {
      button.disabled = false;
    }
  });
})();
