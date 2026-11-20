// Dish popup: the dish on the left (image, name, "Find similar dishes"), and the
// Recipe breakdown / Culture & history / Health info tabs on the right.
// Shared by the Dish Dictionary (dictionary.js) and the comparison page (entity-picker.js).
// Markup: templates/partials/dish_popup.html. Open it with
//   window.CuiSyncDishPopup.open({ id, t: title, a: other name, l: country, i: image, _region })
(() => {
  if (!document.getElementById('dp-overlay')) return;
  const NO_IMAGE = window.DICT_NO_IMAGE || '/static/assets/no-image.svg';
  // Recipe from /api/dish/<id>; culture & health from /api/dish/<id>/information
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

  let popOnClose = null;   // optional: runs after the popup closes (the comparison page reopens its list)
  function openDish(d, options = {}) {
    popFor = d;
    popOnClose = options.onClose || null;
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
    const after = popOnClose;
    popOnClose = null;
    if (after) after();
    else if (popOpener && document.contains(popOpener)) popOpener.focus();
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

  window.CuiSyncDishPopup = { open: openDish, close: closeDish };
})();
