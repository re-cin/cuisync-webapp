// CuiSync Admin: small helpers used on every admin page.
(() => {
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
