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

  // Ask before destructive actions: <button data-confirm="Are you sure?">
  document.querySelectorAll('[data-confirm]').forEach((button) => {
    button.addEventListener('click', (event) => {
      if (!window.confirm(button.dataset.confirm)) event.preventDefault();
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
