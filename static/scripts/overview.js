// Overview page (list of similar dishes before the comparison page):
// filters, "more results" button, and the map of where the dishes come from.
document.addEventListener('DOMContentLoaded', () => {
  const PAGE_SIZE = 12;
  const items = Array.from(document.querySelectorAll('.ov-item'));
  const moreBtn = document.getElementById('ov-more');
  const emptyMsg = document.getElementById('ov-empty');
  const countNum = document.getElementById('ov-count-num');
  const activeRow = document.getElementById('ov-active-row');
  const activeChips = document.getElementById('ov-active-chips');
  const clearBtn = document.getElementById('ov-clear');

  const parse = (text) => { try { return JSON.parse(text || '[]'); } catch (e) { return []; } };
  items.forEach(li => {
    li._tags = parse(li.dataset.tags);
  });

  // ------------------------------------------------------------------
  // Filters: country (one at a time) and tags (all must match)
  // ------------------------------------------------------------------
  const filters = { country: null, tag: new Set() };
  let visibleLimit = PAGE_SIZE;

  function matches(li) {
    if (filters.country && li.dataset.country !== filters.country) return false;
    for (const t of filters.tag) if (!li._tags.includes(t)) return false;
    return true;
  }

  function render() {
    const matching = items.filter(matches);
    items.forEach(li => { li.hidden = true; });
    matching.slice(0, visibleLimit).forEach(li => { li.hidden = false; });

    countNum.textContent = matching.length;
    emptyMsg.hidden = matching.length > 0;

    const left = matching.length - visibleLimit;
    moreBtn.hidden = left <= 0;
    moreBtn.textContent = `+ ${left} more result${left === 1 ? '' : 's'}`;

    // Chip states
    document.querySelectorAll('[data-filter]').forEach(btn => {
      const { filter, value } = btn.dataset;
      const on = filter === 'country' ? filters.country === value : filters[filter].has(value);
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-pressed', String(on));
    });

    // "Showing:" row with removable chips
    const active = [];
    if (filters.country) active.push(['country', filters.country, labelFor('country', filters.country)]);
    filters.tag.forEach(v => active.push(['tag', v, v]));
    activeRow.hidden = active.length === 0;
    activeChips.innerHTML = '';
    active.forEach(([filter, value, label]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ov-chip is-active ov-chip--remove';
      b.textContent = label;
      b.setAttribute('aria-label', `Remove filter ${label}`);
      b.addEventListener('click', () => toggle(filter, value));
      activeChips.appendChild(b);
    });

    mapApi.highlight(filters.country);
  }

  function labelFor(filter, value) {
    const btn = document.querySelector(`[data-filter="${filter}"][data-value="${CSS.escape(value)}"]`);
    if (!btn) return value;
    return (btn.childNodes[0] && btn.childNodes[0].textContent.trim()) || value;
  }

  function toggle(filter, value) {
    if (filter === 'country') {
      filters.country = filters.country === value ? null : value;
    } else {
      filters[filter].has(value) ? filters[filter].delete(value) : filters[filter].add(value);
    }
    visibleLimit = PAGE_SIZE;
    render();
  }

  document.querySelectorAll('button[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      toggle(btn.dataset.filter, btn.dataset.value);
    });
  });

  clearBtn.addEventListener('click', () => {
    filters.country = null;
    filters.tag.clear();
    visibleLimit = PAGE_SIZE;
    render();
  });

  moreBtn.addEventListener('click', () => {
    visibleLimit += PAGE_SIZE;
    render();
  });

  // ------------------------------------------------------------------
  // Map (d3 + world-atlas, same data and Asia frame as the Analysis/Results pages)
  // ------------------------------------------------------------------
  const MAP_DATA = JSON.parse(document.getElementById('ov-map-data').textContent);
  const TOPOLOGY_URL = 'https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json';
  const ASIA_FRAME = { type: 'MultiPoint', coordinates: [[22, -12], [150, 56]] };
  const COLORS = { land: '#e4d9c6', border: '#fbf6ec', origin: '#dcb349', low: '#f2bfa8', high: '#b52a24' };

  const ALIASES = {
    'turkiye': 'turkey', 'korea': 'south korea', 'republic of korea': 'south korea',
    'viet nam': 'vietnam', 'burma': 'myanmar', 'russian federation': 'russia',
  };
  const norm = (name) => {
    const key = String(name || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
      .trim().toLowerCase().replace(/\s+/g, ' ');
    return ALIASES[key] || key;
  };

  const byKey = new Map(MAP_DATA.countries.map(c => [norm(c.country), c]));
  const originKey = norm(MAP_DATA.origin);
  const maxCount = Math.max(1, ...MAP_DATA.countries.map(c => c.count));
  let features = null;
  let highlighted = null;

  function fillFor(key) {
    if (key === originKey) return COLORS.origin;
    const c = byKey.get(key);
    if (!c) return COLORS.land;
    return mix(COLORS.low, COLORS.high, maxCount === 1 ? 1 : (c.count - 1) / (maxCount - 1));
  }

  // Blend two #rrggbb colors (no d3 needed, so the list still works if the map can't load)
  function mix(a, b, t) {
    const pa = [1, 3, 5].map(i => parseInt(a.slice(i, i + 2), 16));
    const pb = [1, 3, 5].map(i => parseInt(b.slice(i, i + 2), 16));
    return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(',')})`;
  }

  // ------------------------------------------------------------------
  // Small map in the right column (click it to open the full map)
  // ------------------------------------------------------------------
  function drawSmall() {
    drawSmallIn('ov-map-svg', 'ov-map-box', 'ov-map-tooltip');
    drawSmallIn('ov-kp-map-svg', 'ov-kp-mapbox', null);   // thumbnail in the phone panel
  }

  function drawSmallIn(svgId, boxId, tipId) {
    const svg = document.getElementById(svgId);
    const box = document.getElementById(boxId);
    const tip = tipId ? document.getElementById(tipId) : null;
    if (!features || !svg || !box) return;
    const w = box.clientWidth, h = box.clientHeight;
    if (w < 20 || h < 20) return;
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const path = d3.geoPath(d3.geoMercator().fitSize([w, h], ASIA_FRAME));
    d3.select(svg).selectAll('path')
      .data(features)
      .join('path')
      .attr('d', path)
      .attr('stroke', COLORS.border)
      .attr('stroke-width', 0.5)
      .attr('fill', f => fillFor(norm(f.properties.name)))
      .classed('is-listed', f => byKey.has(norm(f.properties.name)))
      .classed('is-origin', f => norm(f.properties.name) === originKey)
      .classed('is-highlight', f => highlighted && norm(f.properties.name) === norm(highlighted))
      .on('mousemove', tip ? (event, f) => showCountryTip(event, f, box, tip) : null)
      .on('mouseleave', tip ? () => { tip.hidden = true; } : null);
  }

  function showCountryTip(event, f, box, tip) {
    const key = norm(f.properties.name);
    const c = byKey.get(key);
    if (!c && key !== originKey) { tip.hidden = true; return; }
    tip.innerHTML = key === originKey && !c
      ? `<strong>${escapeHtml(f.properties.name)}</strong><span>Origin of your dish</span>`
      : `<strong>${escapeHtml(c.label)}</strong><span>${c.count} similar dish${c.count === 1 ? '' : 'es'}</span>`
        + `<span>Best: ${escapeHtml(c.best)} (${c.score}%)</span>`;
    const r = box.getBoundingClientRect();
    tip.style.left = `${event.clientX - r.left + 12}px`;
    tip.style.top = `${event.clientY - r.top + 12}px`;
    tip.hidden = false;
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  const modal = document.getElementById('ov-modal');
  const drawAll = () => {
    drawSmall();
    if (!modal.hidden) fullMap.draw();
  };

  const mapApi = {
    highlight(country) {
      highlighted = country;
      if (features) drawSmall();
    },
  };

  // ------------------------------------------------------------------
  // Full-screen interactive map: a pin for every listed dish (and your dish),
  // a country filter bar, zoom, and a side panel for the picked pin.
  // ------------------------------------------------------------------
  const fullMap = (() => {
    const box = document.getElementById('ovm-map');
    const svg = document.getElementById('ovm-svg');
    const g = document.getElementById('ovm-g');
    const pinsLayer = document.getElementById('ovm-pins');
    const tip = document.getElementById('ovm-tooltip');
    const panel = document.getElementById('ovm-panel');
    const filterBar = document.getElementById('ovm-filters');
    const side = document.getElementById('ovm-side');
    const toggleBtn = document.getElementById('ovm-toggle');

    // Side panel (bottom sheet on phones): can be collapsed, opens by itself when a pin is picked
    function setPanelOpen(open) {
      side.classList.toggle('is-collapsed', !open);
      modal.classList.toggle('panel-collapsed', !open);
      toggleBtn.setAttribute('aria-expanded', String(open));
      toggleBtn.setAttribute('aria-label', open ? 'Hide the dish panel' : 'Show the dish panel');
    }
    toggleBtn.addEventListener('click', () => setPanelOpen(side.classList.contains('is-collapsed')));
    const SOURCE = MAP_DATA.source;
    const DISHES = MAP_DATA.dishes || [];
    const NO_IMAGE = '/static/assets/no-image.svg';

    let projection = null;
    let zoom = null;
    let zoomT = d3IdentitySafe();
    let pins = [];                 // { dish, el, lonlat, isSource }
    let selected = null;           // the pin shown in the side panel
    let panelTab = 'recipe';
    const countriesOn = new Set(); // country filter: empty = every country
    const cultureCache = new Map();

    function d3IdentitySafe() { return window.d3 ? d3.zoomIdentity : { k: 1, x: 0, y: 0 }; }

    // ---------- pins ----------
    // Same random spot every time for the same dish (seeded by its index)
    function seeded(n) {
      let x = (n + 1) * 99991;
      return () => { x = (x * 16807) % 2147483647; return (x - 1) / 2147483646; };
    }
    function spotIn(feature, rnd) {
      const [[west, south], [east, north]] = d3.geoBounds(feature);
      const lonSpan = east >= west ? east - west : east + 360 - west;
      for (let i = 0; i < 200; i++) {
        let lon = west + rnd() * lonSpan;
        if (lon > 180) lon -= 360;
        const lat = south + rnd() * (north - south);
        if (d3.geoContains(feature, [lon, lat])) return [lon, lat];
      }
      return d3.geoCentroid(feature);
    }
    const featureFor = (country) => features.find(f => norm(f.properties.name) === norm(country));

    function buildPins() {
      pinsLayer.replaceChildren();
      pins = [];
      const placed = [];
      const add = (dish, isSource) => {
        const feature = featureFor(dish.country);
        if (!feature) return;
        const rnd = seeded(dish.idx);
        // A few tries per dish; keep the one farthest from the pins already placed
        let best = null, bestGap = -1;
        for (let t = 0; t < 10; t++) {
          const p = spotIn(feature, rnd);
          const gap = placed.length ? Math.min(...placed.map(q => Math.hypot(p[0] - q[0], p[1] - q[1]))) : 99;
          if (gap > bestGap) { bestGap = gap; best = p; }
        }
        placed.push(best);
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'ovm-pin' + (isSource ? ' ovm-pin--source' : '') + (!isSource && dish.rank <= 3 ? ' ovm-pin--top' : '');
        el.setAttribute('aria-label', isSource ? `Your dish: ${dish.title}` : `#${dish.rank} ${dish.title}, ${dish.score}% similar`);
        const img = document.createElement('img');
        img.alt = '';
        img.loading = 'lazy';
        img.referrerPolicy = 'no-referrer';
        img.onerror = () => { img.onerror = null; img.src = NO_IMAGE; };
        img.src = dish.image || NO_IMAGE;
        el.appendChild(img);
        if (!isSource) {
          const n = document.createElement('span');
          n.className = 'ovm-pin-rank';
          n.textContent = dish.rank;
          el.appendChild(n);
        }
        const pin = { dish, el, lonlat: best, isSource };
        el.addEventListener('click', (e) => { e.stopPropagation(); select(pin); });
        el.addEventListener('mouseenter', (e) => pinTip(e, pin));
        el.addEventListener('mousemove', (e) => pinTip(e, pin));
        el.addEventListener('mouseleave', () => { tip.hidden = true; });
        pinsLayer.appendChild(el);
        pins.push(pin);
      };
      DISHES.slice().reverse().forEach(d => add(d, false));   // rank 1 drawn last = on top
      add(SOURCE, true);
      applyFilter();
    }

    function pinTip(event, pin) {
      const d = pin.dish;
      tip.innerHTML = pin.isSource
        ? `<strong>${escapeHtml(d.title)}</strong><span>Your dish &middot; ${escapeHtml(d.label)}</span>`
        : `<strong>#${d.rank} ${escapeHtml(d.title)}</strong><span>${escapeHtml(d.label)} &middot; ${d.score}% similar</span>`;
      const r = box.getBoundingClientRect();
      tip.style.left = `${event.clientX - r.left + 14}px`;
      tip.style.top = `${event.clientY - r.top + 14}px`;
      tip.hidden = false;
    }

    function positionPins() {
      if (!projection) return;
      pins.forEach(pin => {
        const p = projection(pin.lonlat);
        if (!p) return;
        const [x, y] = zoomT.apply(p);
        pin.el.style.left = `${x}px`;
        pin.el.style.top = `${y}px`;
      });
    }

    // ---------- map ----------
    function draw() {
      if (!features) return;
      const w = box.clientWidth, h = box.clientHeight;
      if (w < 20 || h < 20) return;
      svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      projection = d3.geoMercator().fitSize([w, h], ASIA_FRAME);
      const path = d3.geoPath(projection);
      d3.select(g).selectAll('path')
        .data(features)
        .join('path')
        .attr('d', path)
        .attr('stroke', COLORS.border)
        .attr('stroke-width', 0.5)
        .attr('vector-effect', 'non-scaling-stroke')
        .attr('fill', f => fillFor(norm(f.properties.name)))
        .classed('is-listed', f => byKey.has(norm(f.properties.name)))
        .classed('is-origin', f => norm(f.properties.name) === originKey)
        .on('mousemove', (event, f) => showCountryTip(event, f, box, tip))
        .on('mouseleave', () => { tip.hidden = true; });
      if (!zoom) {
        zoom = d3.zoom()
          .scaleExtent([1, 8])
          .on('zoom', (event) => {
            zoomT = event.transform;
            g.setAttribute('transform', zoomT.toString());
            positionPins();
          });
        d3.select(box).call(zoom).on('dblclick.zoom', null);
      }
      // Big margin around the map so it can be dragged anywhere, even when not zoomed in
      // and even when part of it is under the side panel
      const padX = Math.max(w * 0.6, 440), padY = Math.max(h * 0.6, 320);
      zoom.translateExtent([[-padX, -padY], [w + padX, h + padY]]).extent([[0, 0], [w, h]]);
      if (!pins.length) buildPins();
      applyCountryShade();
      positionPins();
    }

    // The part of the map that isn't under the side panel (left on desktop, bottom sheet on phones)
    const phoneLayout = window.matchMedia('(max-width: 768px)');
    function visibleArea() {
      const w = box.clientWidth, h = box.clientHeight;
      const open = !side.classList.contains('is-collapsed');
      const left = open && !phoneLayout.matches ? side.offsetWidth : 0;
      const bottom = open && phoneLayout.matches ? side.offsetHeight : 0;
      return { w: w - left, h: h - bottom, cx: left + (w - left) / 2, cy: (h - bottom) / 2 };
    }
    // Whole map, centred in the visible part
    function homeTransform() {
      const v = visibleArea();
      return d3.zoomIdentity.translate(v.cx - box.clientWidth / 2, v.cy - box.clientHeight / 2);
    }
    function goTo(transform, ms = 250) {
      if (zoom) d3.select(box).transition().duration(ms).call(zoom.transform, transform);
    }

    const zoomBy = (k) => zoom && d3.select(box).transition().duration(250).call(zoom.scaleBy, k);
    document.getElementById('ovm-zoom-in').addEventListener('click', () => zoomBy(1.6));
    document.getElementById('ovm-zoom-out').addEventListener('click', () => zoomBy(1 / 1.6));
    document.getElementById('ovm-zoom-reset').addEventListener('click', () => goTo(homeTransform()));

    // Target button: zoom in on the searched dish and show it in the panel
    function focusSource() {
      const pin = pins.find(p => p.isSource);
      if (!pin || !projection) return;
      select(pin);                                   // opens the panel, so measure after
      const [x, y] = projection(pin.lonlat);
      const v = visibleArea();
      const k = Math.max(zoomT.k, 3.5);
      goTo(d3.zoomIdentity.translate(v.cx - k * x, v.cy - k * y).scale(k), 450);
    }
    document.getElementById('ovm-focus').addEventListener('click', focusSource);

    // ---------- country filter ----------
    function buildFilters() {
      const countries = MAP_DATA.countries.slice().sort((a, b) => b.count - a.count);
      const pill = (label, count, value) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'ovm-filter';
        b.dataset.value = value;
        b.innerHTML = `${escapeHtml(label)}${count != null ? ` <small>${count}</small>` : ''}`;
        b.addEventListener('click', () => {
          if (value === '') countriesOn.clear();
          else countriesOn.has(value) ? countriesOn.delete(value) : countriesOn.add(value);
          applyFilter();
        });
        filterBar.appendChild(b);
      };
      pill('All countries', DISHES.length, '');
      countries.forEach(c => pill(c.label, c.count, c.country));
    }

    function applyFilter() {
      filterBar.querySelectorAll('.ovm-filter').forEach(b => {
        const on = b.dataset.value === '' ? countriesOn.size === 0 : countriesOn.has(b.dataset.value);
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-pressed', String(on));
      });
      pins.forEach(pin => {
        pin.el.hidden = !pin.isSource && countriesOn.size > 0 && !countriesOn.has(pin.dish.country);
      });
      applyCountryShade();
      fitToPins();
      // The picked dish was filtered out: show the best one still on the map
      if (selected && selected.el.hidden) {
        const next = pins.filter(p => !p.isSource && !p.el.hidden).sort((a, b) => a.dish.rank - b.dish.rank)[0];
        select(next || pins.find(p => p.isSource));
      }
    }

    // Zoom to the pins of the filtered countries (or back out for "All countries")
    function fitToPins() {
      if (!zoom || !projection) return;
      const sel = d3.select(box).transition().duration(450);
      if (!countriesOn.size) { sel.call(zoom.transform, homeTransform()); return; }
      const pts = pins.filter(p => !p.isSource && !p.el.hidden).map(p => projection(p.lonlat)).filter(Boolean);
      if (!pts.length) return;
      const v = visibleArea();           // fit into the part not covered by the panel
      const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
      const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
      const k = Math.max(1, Math.min(6, 0.7 / Math.max((x1 - x0 + 1) / v.w, (y1 - y0 + 1) / v.h)));
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      sel.call(zoom.transform, d3.zoomIdentity.translate(v.cx - k * cx, v.cy - k * cy).scale(k));
    }

    function applyCountryShade() {
      d3.select(g).selectAll('path').attr('opacity', f => {
        if (!countriesOn.size) return 1;
        const key = norm(f.properties.name);
        if (key === originKey) return 1;
        const c = byKey.get(key);
        return c && countriesOn.has(c.country) ? 1 : 0.35;
      });
    }

    // ---------- side panel ----------
    const chip = (name, cls) =>
      `<a class="ovm-ing ${cls}" href="/dictionary?ingredient=${encodeURIComponent(name.toLowerCase())}" title="See every dish with ${escapeHtml(name.toLowerCase())}">${escapeHtml(name)}</a>`;
    const tagLinks = (tags) => tags.map((t, i) =>
      `<a class="ov-chip ov-chip--source ${i === 0 && t.endsWith('Asia') ? 'is-region' : ''}" href="/dictionary?tag=${encodeURIComponent(t)}">${escapeHtml(t)}</a>`).join('');

    function select(pin) {
      if (!pin) return;
      if (selected) selected.el.classList.remove('is-selected');
      selected = pin;
      pin.el.classList.add('is-selected');
      renderPanel();
      setPanelOpen(true);
    }

    function renderPanel() {
      const pin = selected;
      const d = pin.dish;
      const head = `
        <div class="ovm-dish-img">
          <img src="${escapeHtml(d.image || NO_IMAGE)}" alt="" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${NO_IMAGE}'">
          <span class="ovm-country">${escapeHtml(d.label)}</span>
          ${pin.isSource ? '' : `<span class="ovm-rank ${d.rank <= 3 ? 'is-top' : ''}"><small>Rank</small>${d.rank}</span>`}
        </div>
        <div class="ovm-dish-head">
          <span class="ovm-eyebrow">${pin.isSource ? 'Your dish' : `${d.score}% similar to ${escapeHtml(SOURCE.title)}`}</span>
          <h3>${escapeHtml(d.title)}</h3>
          ${d.alt && d.alt.toLowerCase() !== d.title.toLowerCase() ? `<p class="ovm-alt">${escapeHtml(d.alt)}</p>` : ''}
          <div class="ovm-actions">
            ${d.recipe ? `<a class="ovm-recipe" href="${escapeHtml(d.recipe)}" target="_blank" rel="noopener">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>
              Original recipe</a>` : ''}
            ${pin.isSource ? '' : `<a class="ovm-cta" href="${escapeHtml(d.url)}">View full comparison <span class="ovm-cta-arrow" aria-hidden="true">&rarr;</span></a>`}
          </div>
        </div>`;

      let recipe;
      if (pin.isSource) {
        recipe = `
          <h4>Ingredients</h4>
          <div class="ovm-ings">${d.ingredients.map(n => chip(n, 'is-source')).join('') || '<span class="ovm-none">None listed.</span>'}</div>`;
      } else {
        const ing = d.ingredients;
        recipe = `
          <div class="ovm-key">
            <span class="ovm-key-item is-shared">Shared</span>
            <span class="ovm-key-item is-source">Only in ${escapeHtml(SOURCE.title)}</span>
            <span class="ovm-key-item is-target">Only in ${escapeHtml(d.title)}</span>
          </div>
          <h4>Ingredients</h4>
          <div class="ovm-ings">
            ${ing.shared.map(n => chip(n, 'is-shared')).join('')}
            ${ing.source_unique.map(n => chip(n, 'is-source')).join('')}
            ${ing.target_unique.map(n => chip(n, 'is-target')).join('')}
          </div>`;
      }
      recipe += `<h4>Tags</h4><div class="ovm-tags">${tagLinks(d.tags)}</div>`;

      panel.innerHTML = `
        ${head}
        <div class="ovm-tabs" role="tablist">
          <button type="button" role="tab" class="ovm-tab" data-tab="recipe">Recipe breakdown</button>
          <button type="button" role="tab" class="ovm-tab" data-tab="culture">Cultural info</button>
        </div>
        <div class="ovm-tabpanel" data-tab="recipe">${recipe}</div>
        <div class="ovm-tabpanel" data-tab="culture"><p class="ovm-loading">Loading cultural information&hellip;</p></div>`;
      panel.querySelectorAll('.ovm-tab').forEach(t => t.addEventListener('click', () => { panelTab = t.dataset.tab; showTab(); }));
      showTab();
      panel.scrollTop = 0;
    }

    function showTab() {
      panel.querySelectorAll('.ovm-tab').forEach(t => {
        const on = t.dataset.tab === panelTab;
        t.classList.toggle('is-active', on);
        t.setAttribute('aria-selected', String(on));
      });
      panel.querySelectorAll('.ovm-tabpanel').forEach(p => { p.hidden = p.dataset.tab !== panelTab; });
      if (panelTab === 'culture') loadCulture(selected.dish.idx);
    }

    function loadCulture(idx) {
      const target = panel.querySelector('.ovm-tabpanel[data-tab="culture"]');
      const fill = (info) => {
        if (!selected || selected.dish.idx !== idx) return;
        const badge = info.source === 'ai'
          ? '<span class="badge-mini">AI-generated</span><span class="footer-note">By GPT. Verify before citing.</span>'
          : '<span class="badge-mini">Summary</span><span class="footer-note">Built from the dataset (GPT unavailable).</span>';
        target.innerHTML = `
          <h4>Culture</h4><p>${escapeHtml(info.culture)}</p>
          <h4>History</h4><p>${escapeHtml(info.history)}</p>
          <div class="ovm-badge">${badge}</div>`;
      };
      if (cultureCache.has(idx)) return fill(cultureCache.get(idx));
      fetch('/api/dish-info/' + idx)
        .then(r => r.json())
        .then(info => { cultureCache.set(idx, info); fill(info); })
        .catch(() => { target.innerHTML = '<p>Cultural information is not available right now.</p>'; });
    }

    buildFilters();

    return {
      draw,
      open() {
        setPanelOpen(true);
        draw();
        if (!selected) {
          select(pins.filter(p => !p.isSource).sort((a, b) => a.dish.rank - b.dish.rank)[0] || pins[0]);
          // First open: centre the map in the part not covered by the panel
          if (zoom) d3.select(box).call(zoom.transform, homeTransform());
        }
      },
    };
  })();

  if (window.d3 && window.topojson) {
    d3.json(TOPOLOGY_URL)
      .then(topo => {
        features = topojson.feature(topo, topo.objects.countries).features;
        drawAll();
      })
      .catch(() => {
        document.getElementById('ov-map-box').classList.add('is-offline');
        document.getElementById('ovm-map').classList.add('is-offline');
      });
  } else {
    document.getElementById('ov-map-box').classList.add('is-offline');
    document.getElementById('ovm-map').classList.add('is-offline');
  }
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawAll, 150);
  });

  // ------------------------------------------------------------------
  // Open / close the full map
  // ------------------------------------------------------------------
  function openModal() {
    modal.hidden = false;
    document.documentElement.classList.add('menu-open');
    requestAnimationFrame(() => fullMap.open());
    document.getElementById('ov-modal-close').focus();
  }
  function closeModal() {
    modal.hidden = true;
    document.documentElement.classList.remove('menu-open');
  }
  document.getElementById('ov-map-open').addEventListener('click', openModal);
  document.querySelectorAll('[data-open-map]').forEach(b => b.addEventListener('click', openModal));

  // ------------------------------------------------------------------
  // Phones: Google-style panel. "Show more" opens everything; "Show less" stays at the top.
  // ------------------------------------------------------------------
  const kp = document.getElementById('ov-kp');
  if (kp) {
    const less = kp.querySelector('.ov-kp-less');
    const more = kp.querySelector('.ov-kp-more');
    const setOpen = (open) => {
      kp.classList.toggle('is-open', open);
      less.hidden = !open;
      more.hidden = open;
      more.setAttribute('aria-expanded', String(open));
      if (!open) {
        // Back to the short version: bring the panel back into view
        const top = kp.getBoundingClientRect().top + window.scrollY - 70;
        if (window.scrollY > top) window.scrollTo({ top, behavior: 'smooth' });
      }
    };
    more.addEventListener('click', () => setOpen(true));
    less.addEventListener('click', () => setOpen(false));
  }
  document.getElementById('ov-map-box').addEventListener('click', openModal);
  document.getElementById('ov-modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) closeModal(); });

  render();
});
