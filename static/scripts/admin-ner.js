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

  function render() {
    chipList.replaceChildren();
    const have = new Set(currentIngredients());
    items.forEach((item, index) => {
      const chip = document.createElement('span');
      chip.className = `chip chip-${item.status}`;
      chip.textContent = item.name;
      if (have.has(item.name.toLowerCase())) chip.title = 'Already in the Ingredients field';

      if (item.status === 'suggested' && item.suggestion) {
        const use = document.createElement('button');
        use.type = 'button';
        use.className = 'chip-action';
        use.textContent = `use "${item.suggestion}"`;
        use.addEventListener('click', () => {
          items[index] = { name: item.suggestion, status: 'existing' };
          render();
        });
        chip.appendChild(use);
      }

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'chip-remove';
      remove.setAttribute('aria-label', `Remove ${item.name}`);
      remove.textContent = '×';
      remove.addEventListener('click', () => {
        items.splice(index, 1);
        render();
      });
      chip.appendChild(remove);
      chipList.appendChild(chip);
    });
    results.hidden = items.length === 0;
  }

  function writeToField(replace) {
    const names = items.map((i) => i.name.toLowerCase());
    const merged = replace ? names : [...currentIngredients(), ...names];
    field.value = [...new Set(merged)].join(', ');
    field.dispatchEvent(new Event('input'));  // refreshes the chip preview under the field
    statusEl.textContent = `${names.length} ingredient(s) ${replace ? 'put in' : 'added to'} the Ingredients field. Save the dish to keep them.`;
    field.focus();
  }
  addBtn.addEventListener('click', () => writeToField(false));
  replaceBtn.addEventListener('click', () => {
    if (!field.value.trim() || window.confirm('Replace the current ingredients with these?')) writeToField(true);
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
      statusEl.textContent = items.length
        ? `Found ${items.length}: ${count('existing')} in the dataset, ${count('suggested')} to check, ${count('new')} new.`
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

  function render() {
    groups.forEach((box) => {
      const key = box.dataset.dirGroup;
      box.replaceChildren(...found[key].map((item, index) => {
        const chip = document.createElement('span');
        chip.className = `chip chip-${item.status || 'existing'}`;
        chip.textContent = item.name;
        if (item.status === 'suggested' && item.suggestion) {
          const use = document.createElement('button');
          use.type = 'button';
          use.className = 'chip-action';
          use.textContent = `use "${item.suggestion}"`;
          use.addEventListener('click', () => {
            found[key][index] = { name: item.suggestion, status: 'existing' };
            render();
          });
          chip.appendChild(use);
        }
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'chip-remove';
        remove.setAttribute('aria-label', `Remove ${item.name}`);
        remove.textContent = '×';
        remove.addEventListener('click', () => { found[key].splice(index, 1); render(); });
        chip.appendChild(remove);
        return chip;
      }));
    });
    results.hidden = !Object.values(found).some((list) => list.length);
  }

  function writeFields(replace) {
    let total = 0;
    groups.forEach((box) => {
      const field = document.getElementById(box.dataset.field);
      const names = found[box.dataset.dirGroup].map((i) => i.name.toLowerCase());
      if (!field || (!names.length && !replace)) return;
      const current = replace ? [] : field.value.split(/[,\n]/).map((s) => s.trim().toLowerCase()).filter(Boolean);
      field.value = [...new Set([...current, ...names])].join(', ');
      field.dispatchEvent(new Event('input'));
      total += names.length;
    });
    statusEl.textContent = `${total} item(s) ${replace ? 'put in' : 'added to'} the fields. Save the dish to keep them.`;
  }
  document.getElementById('directions-add').addEventListener('click', () => writeFields(false));
  document.getElementById('directions-replace').addEventListener('click', () => {
    if (window.confirm('Replace the current actions, cookware and utensils with these?')) writeFields(true);
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
      const newCount = all.filter((i) => i.status !== 'existing').length;
      statusEl.textContent = `Found ${found.actions.length} action(s), ${found.cookware.length} cookware, ${found.utensils.length} utensil(s)` +
        (data.source === 'model' ? ` with the directions model${newCount ? `, ${newCount} not in the dataset yet` : ''}.` : ' from the dataset vocabulary.');
      render();
    } catch (error) {
      statusEl.textContent = 'Could not reach the server. Please try again.';
    } finally {
      button.disabled = false;
    }
  });
})();
