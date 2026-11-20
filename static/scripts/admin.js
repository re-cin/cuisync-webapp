// CuiSync Admin: small helpers used on every admin page.
(() => {
  // Keep every prompt form mounted: category changes only hide/show panels,
  // preserving unsaved text and each editor's independent save/reset form.
  const prompts = document.querySelector('[data-prompt-management]');
  if (prompts) {
    const tabs = Array.from(prompts.querySelectorAll('[role="tab"]'));
    const panels = Array.from(prompts.querySelectorAll('[role="tabpanel"]'));
    const selectCategory = (id) => {
      if (!tabs.some((tab) => tab.getAttribute('aria-controls') === id)) return;
      tabs.forEach((tab) => {
        const selected = tab.getAttribute('aria-controls') === id;
        tab.classList.toggle('selected', selected);
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
      });
      panels.forEach((panel) => { panel.hidden = panel.id !== id; });
    };
    const activate = (tab) => {
      const id = tab.getAttribute('aria-controls');
      selectCategory(id);
      // Remember the selected category across a save/redirect without navigating.
      window.history.replaceState(null, '', `#${id}`);
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', (event) => {
        event.preventDefault();
        activate(tab);
      });
      tab.addEventListener('keydown', (event) => {
        let next;
        if (event.key === 'ArrowDown') next = (index + 1) % tabs.length;
        else if (event.key === 'ArrowUp') next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else if (event.key === ' ') next = index;
        else return;
        event.preventDefault();
        activate(tabs[next]);
        tabs[next].focus();
      });
    });
    const selectFromHash = () => {
      const id = window.location.hash.slice(1);
      selectCategory(panels.some((panel) => panel.id === id) ? id : 'prompt-explanation');
    };
    window.addEventListener('hashchange', selectFromHash);
    selectFromHash();

    prompts.querySelectorAll('[data-prompt-form]').forEach((form) => {
      const dishSearch = form.querySelector('[data-dish-search]');
      if (dishSearch) {
        const picker = dishSearch.closest('[data-dish-picker]');
        const results = picker.querySelector('.prompt-dish-results');
        const options = Array.from(results.querySelectorAll('[data-index]'));
        const index = form.querySelector('[data-sample-index]');
        const closeResults = () => { results.hidden = true; dishSearch.setAttribute('aria-expanded', 'false'); };
        const norm = (t) => t.toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        let active = -1;
        const visible = () => options.filter((option) => !option.hidden);
        const setActive = (i) => {
          const list = visible();
          options.forEach((option) => option.classList.remove('is-active'));
          if (!list.length) { active = -1; return; }
          active = (i + list.length) % list.length;
          list[active].classList.add('is-active');
          list[active].scrollIntoView({ block: 'nearest' });
        };
        // Every typed word must appear in the dish name or country ("adobo chicken", "japan ramen")
        const updateDish = (open = true) => {
          const words = norm(dishSearch.value.trim()).split(/\s+/).filter(Boolean);
          let shown = 0;
          options.forEach((option) => {
            const label = norm(option.dataset.label);
            const match = words.every((w) => label.includes(w));
            option.hidden = !match || shown >= 30;
            if (match && shown < 30) shown += 1;
          });
          empty.hidden = shown > 0;
          active = -1;
          options.forEach((option) => option.classList.remove('is-active'));
          if (open) {
            results.hidden = false;
            dishSearch.setAttribute('aria-expanded', 'true');
          }
        };
        const chooseDish = (option) => {
          dishSearch.value = option.dataset.label;
          index.value = option.dataset.index;
          closeResults();
        };
        // "No dish matches" line at the end of the list
        const empty = document.createElement('p');
        empty.className = 'prompt-dish-empty';
        empty.textContent = 'No dish matches. Try another name or a country.';
        empty.hidden = true;
        results.appendChild(empty);
        // Clicking the box selects its text, so typing starts a new search
        dishSearch.addEventListener('focus', () => { dishSearch.select(); updateDish(); });
        dishSearch.addEventListener('input', () => {
          const exact = options.find((option) => option.dataset.label === dishSearch.value);
          if (exact) index.value = exact.dataset.index;
          updateDish();
        });
        dishSearch.addEventListener('keydown', (event) => {
          if (event.key === 'ArrowDown') { event.preventDefault(); if (results.hidden) updateDish(); setActive(active + 1); }
          else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(active - 1); }
          else if (event.key === 'Enter') {
            // Enter picks the highlighted dish (or the only / first match) instead of submitting the form
            const list = visible();
            if (!results.hidden && list.length) { event.preventDefault(); chooseDish(list[Math.max(active, 0)]); }
          } else if (event.key === 'Escape') closeResults();
        });
        options.forEach((option) => option.addEventListener('click', () => chooseDish(option)));
        document.addEventListener('click', (event) => { if (!picker.contains(event.target)) closeResults(); });
      }
      const feedback = form.querySelector('[data-prompt-feedback]');
      const preview = form.querySelector('[data-prompt-preview]');
      const showPreview = (data) => {
        preview.replaceChildren();
        const add = (title, value) => {
          const heading = document.createElement('h4');
          heading.textContent = title;
          const body = document.createElement('p');
          body.textContent = value;
          preview.append(heading, body);
        };
        if (data.explanation) {
          const evidence = data.evidence;
          const items = (values) => values?.length ? values.join(', ') : 'none listed';
          const scoreLabels = { ingredients: 'Ingredients', actions: 'Cooking steps', cookware: 'Cookware', utensils: 'Utensils' };
          add('Preview: similarity explanation', data.explanation);
          add('Similarity score (unchanged by this prompt)', `${data.similarity_score}%`);
          add('What contributed to the score', Object.entries(data.shapley_contributions).map(([name, value]) => `${scoreLabels[name] || name}: ${value}%`).join('; '));
          add('Dish details used for this preview',
            `${evidence.input_dish} (${evidence.input_cuisine}) and ${evidence.matched_dish} (${evidence.matched_cuisine}).\n` +
            `Shared ingredients: ${items(evidence.shared_ingredients)}.\n` +
            `Only in ${evidence.input_dish}: ${items(evidence.unique_to_input)}.\n` +
            `Only in ${evidence.matched_dish}: ${items(evidence.unique_to_match)}.\n` +
            `Shared cooking steps: ${items(evidence.shared_actions)}.\n` +
            `Shared cookware: ${items(evidence.shared_cookware)}. Shared utensils: ${items(evidence.shared_utensils)}.`);
          add('About this preview', data.sample_note);
        } else {
          const history = data.historical_background || 'No supported history returned.';
          const culture = data.cultural_background || 'No supported culture returned.';
          add('History & culture preview', `${history}\n\n${culture}`);
          if (data.source_links?.length) {
            const heading = document.createElement('h4');
            heading.textContent = 'Source links';
            const links = document.createElement('p');
            data.source_links.forEach((source, index) => {
              if (index) links.append(document.createTextNode(' · '));
              const link = document.createElement('a');
              link.href = source.url;
              link.target = '_blank';
              link.rel = 'noopener noreferrer';
              link.textContent = source.title || source.url;
              links.append(link);
            });
            preview.append(heading, links);
          }
          if (data.source_status) feedback.textContent = data.source_status;
        }
        preview.hidden = false;
      };
      const send = async (action, restoreVersion) => {
        if ((action === 'test' || action === 'save') && !form.reportValidity()) return;
        const body = new FormData(form);
        body.set('action', action);
        if (restoreVersion) body.set('restore_version', restoreVersion);
        const buttons = Array.from(form.querySelectorAll('button'));
        buttons.forEach((button) => { button.disabled = true; });
        feedback.textContent = action === 'test' ? 'Creating a preview…' : 'Saving your instructions…';
        if (action === 'test') preview.hidden = true;
        try {
          const response = await fetch(action === 'test' ? form.dataset.previewUrl : form.getAttribute('action'), {
            method: 'POST', body, headers: { Accept: 'application/json' },
          });
          if (response.redirected) throw new Error('Your admin session expired. Sign in again and reload this page.');
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Request failed. Reload and try again.');
          if (action === 'test') {
            showPreview(data);
            feedback.textContent = 'Preview only. Click Save and use now to make these the active instructions.';
          } else {
            form.elements.expected_version.value = data.version;
            form.elements.prompt.value = data.instructions;
            const editor = form.closest('[data-prompt-editor]');
            editor.querySelector('[data-active-version]').textContent = data.version;
            const status = editor.querySelector('[data-prompt-status]');
            status.textContent = data.custom ? 'Custom' : 'Default';
            status.classList.toggle('pill-active', data.custom);
            status.classList.toggle('pill-hidden', !data.custom);
            const versions = form.querySelector('[data-version-list]');
            versions.querySelectorAll('[data-version-status]').forEach((item) => { item.textContent = 'Past'; });
            versions.querySelectorAll('[data-restore-version]').forEach((button) => { button.hidden = false; });
            const entry = document.createElement('details');
            entry.className = 'prompt-version';
            const summary = document.createElement('summary');
            summary.append(`Version ${data.version} · `);
            const active = document.createElement('span');
            active.dataset.versionStatus = '';
            active.textContent = 'In use';
            summary.append(active, ` · ${data.administrator} · ${data.modified_at} (UTC)`);
            const text = document.createElement('pre');
            text.className = 'mono';
            text.textContent = data.instructions;
            const restore = document.createElement('button');
            restore.type = 'button';
            restore.className = 'btn btn-outline';
            restore.dataset.restoreVersion = data.version;
            restore.textContent = `Use version ${data.version}`;
            restore.hidden = true;
            entry.append(summary, text, restore);
            versions.prepend(entry);
            while (versions.children.length > 100) versions.lastElementChild.remove();
            tabs.forEach((tab) => {
              const panel = prompts.querySelector(`#${tab.getAttribute('aria-controls')}`);
              const custom = Array.from(panel.querySelectorAll('[data-prompt-status]')).some((pill) => pill.textContent === 'Custom');
              const pill = tab.querySelector('.pill');
              pill.textContent = custom ? 'Custom' : 'Default';
              pill.classList.toggle('pill-active', custom);
              pill.classList.toggle('pill-hidden', !custom);
            });
            feedback.textContent = data.message;
          }
        } catch (error) {
          feedback.textContent = error.message || 'Request failed. Your edits are still in the editor.';
        } finally {
          buttons.forEach((button) => { button.disabled = false; });
        }
      };
      form.querySelector('[data-test-prompt]').addEventListener('click', () => send('test'));
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        send(event.submitter?.value || 'save');
      });
      form.addEventListener('click', (event) => {
        const button = event.target.closest('[data-restore-version]');
        if (button) {
          if (window.confirm(`Use the instructions from version ${button.dataset.restoreVersion} again?`)) {
            send('restore', button.dataset.restoreVersion);
          }
        }
      });
    });
  }

  // Mobile: open / close the sidebar menu
  const toggle = document.getElementById('sidebar-toggle');
  const nav = document.getElementById('admin-nav');
  if (toggle && nav) {
    toggle.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    });
  }

  // Show / hide password (admin login)
  document.querySelectorAll('[data-toggle-password]').forEach((button) => {
    const input = document.getElementById(button.dataset.togglePassword);
    button.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
  });

  // Ask before destructive actions in a pop-up: <button data-confirm="Are you sure?">
  // Optional: data-confirm-title, data-confirm-item (shown in bold first), data-confirm-ok (button text)
  let confirmDialog = null;
  function getConfirmDialog() {
    if (confirmDialog) return confirmDialog;
    confirmDialog = document.createElement('dialog');
    confirmDialog.className = 'admin-modal';
    confirmDialog.setAttribute('aria-labelledby', 'confirm-modal-title');
    confirmDialog.innerHTML = `
      <div class="admin-modal-icon admin-modal-icon-danger" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>
      </div>
      <h2 class="admin-modal-title" id="confirm-modal-title"></h2>
      <p class="admin-modal-text"><strong class="admin-modal-item"></strong> <span class="admin-modal-message"></span></p>
      <div class="admin-modal-actions">
        <button type="button" class="btn btn-outline" data-modal-cancel>Cancel</button>
        <button type="button" class="btn btn-delete" data-modal-ok></button>
      </div>`;
    document.body.appendChild(confirmDialog);
    confirmDialog.querySelector('[data-modal-cancel]').addEventListener('click', () => confirmDialog.close());
    confirmDialog.addEventListener('click', (e) => { if (e.target === confirmDialog) confirmDialog.close(); });
    return confirmDialog;
  }
  // The same pop-up for scripts (admin-ner.js): cuisyncConfirm({title, item, message, ok}) -> true/false
  window.cuisyncConfirm = ({ title, item, message, ok, icon } = {}) => new Promise((resolve) => {
    const dialog = getConfirmDialog();
    if (typeof dialog.showModal !== 'function') {             // very old browser: plain confirm
      resolve(window.confirm(((item || '') + ' ' + (message || '')).trim()));
      return;
    }
    dialog.querySelector('.admin-modal-title').textContent = title || 'Are you sure?';
    dialog.querySelector('.admin-modal-item').textContent = item || '';
    dialog.querySelector('.admin-modal-message').textContent = message || '';
    dialog.querySelector('.admin-modal-icon').hidden = icon === false;
    const okBtn = dialog.querySelector('[data-modal-ok]');
    okBtn.textContent = ok || 'Yes, continue';
    let answer = false;
    okBtn.onclick = () => { answer = true; dialog.close(); };
    dialog.addEventListener('close', () => resolve(answer), { once: true });
    dialog.showModal();
    dialog.querySelector('[data-modal-cancel]').focus();       // safe default: Enter cancels
  });

  document.querySelectorAll('[data-confirm]').forEach((button) => {
    button.addEventListener('click', async (event) => {
      if (button.dataset.confirmed) { delete button.dataset.confirmed; return; }
      event.preventDefault();
      const yes = await window.cuisyncConfirm({
        title: button.dataset.confirmTitle, item: button.dataset.confirmItem,
        message: button.dataset.confirm, ok: button.dataset.confirmOk,
      });
      if (yes) {
        button.dataset.confirmed = '1';
        button.click();                                        // submits with the button's name/value
      }
    });
  });

  // Dataset upload: confirm only when "Replace the whole dataset" is picked
  document.querySelectorAll('[data-confirm-replace]').forEach((button) => {
    button.addEventListener('click', (event) => {
      const mode = button.form && button.form.querySelector('input[name="mode"]:checked');
      if (mode && mode.value === 'replace' && !window.confirm(button.dataset.confirmReplace)) {
        event.preventDefault();
      }
    });
  });

  // Dropdowns that save / filter right away (role select, filters)
  document.querySelectorAll('select[data-autosubmit]').forEach((select) => {
    const original = select.value;
    select.addEventListener('change', () => {
      if (select.dataset.confirmValue && select.value === select.dataset.confirmValue &&
          !window.confirm(select.dataset.confirmText || 'Are you sure?')) {
        select.value = original;
        return;
      }
      if (select.form) select.form.submit();
    });
  });

  // Dish form: show comma-separated values as chips under the field
  document.querySelectorAll('[data-preview-for]').forEach((box) => {
    const field = document.getElementById(box.dataset.previewFor);
    if (!field) return;
    const render = () => {
      const items = [...new Set(field.value.split(/[,\n]/).map((s) => s.trim()).filter(Boolean))];
      box.replaceChildren(...items.slice(0, 60).map((text) => {
        const chip = document.createElement('span');
        chip.className = 'chip-static';
        chip.textContent = text;
        return chip;
      }));
    };
    field.addEventListener('input', render);
    render();
  });
})();
