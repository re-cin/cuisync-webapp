// Comparison page: interactive map of the most similar dishes (bottom of the page).
// - A pin for each dish (photo in a circle, rank badge); clicking it opens that dish's comparison
// - Your dish has a gold ring; the dish on this page is highlighted
// - Side panel with the top 10 (made in results.html); on phones it sits under the map
// - Drag / scroll to move and zoom, with a wide margin so the map can be moved even when not
//   zoomed in. Target button = focus on your dish, Reset = whole map.
document.addEventListener('DOMContentLoaded', () => {
  const dataEl = document.getElementById('rmap-data');
  const box = document.getElementById('rmap-map');
  if (!dataEl || !box) return;

  const DATA = JSON.parse(dataEl.textContent);
  const svg = document.getElementById('rmap-svg');
  const g = document.getElementById('rmap-g');
  const pinsLayer = document.getElementById('rmap-pins');
  const tip = document.getElementById('rmap-tooltip');
  const side = document.getElementById('rmap-side');
  const toggleBtn = document.getElementById('rmap-toggle');
  const NO_IMAGE = '/static/assets/no-image.svg';
  const TOPOLOGY_URL = 'https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json';
  const ASIA_FRAME = { type: 'MultiPoint', coordinates: [[22, -12], [150, 56]] };
  const COLORS = { land: '#e4d9c6', border: '#fbf6ec', origin: '#dcb349', target: '#b52a24', other: '#f2bfa8' };

  const ALIASES = {
    'turkiye': 'turkey', 'korea': 'south korea', 'republic of korea': 'south korea',
    'viet nam': 'vietnam', 'burma': 'myanmar', 'russian federation': 'russia',
  };
  const norm = (name) => {
    const key = String(name || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
      .trim().toLowerCase().replace(/\s+/g, ' ');
    return ALIASES[key] || key;
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  const byKey = new Map(DATA.countries.map(c => [norm(c.country), c]));
  const originKey = norm(DATA.origin);
  const targetKey = DATA.target ? norm(DATA.target) : null;

  // Countries with pins: the target country red, the others light red, your dish's country gold
  function fillFor(key) {
    if (key === originKey) return COLORS.origin;
    if (!byKey.has(key)) return COLORS.land;
    return key === targetKey ? COLORS.target : COLORS.other;
  }

  let features = null;
  let projection = null;
  let zoom = null;
  let zoomT = null;
  let pins = [];

  // ---------- side panel (overlay on bigger screens, under the map on phones) ----------
  const phoneLayout = window.matchMedia('(max-width: 768px)');
  function setPanelOpen(open) {
    side.classList.toggle('is-collapsed', !open);
    box.parentElement.classList.toggle('panel-collapsed', !open);
    toggleBtn.setAttribute('aria-expanded', String(open));
    toggleBtn.setAttribute('aria-label', open ? 'Hide the top 10 list' : 'Show the top 10 list');
  }
  toggleBtn.addEventListener('click', () => setPanelOpen(side.classList.contains('is-collapsed')));

  // The part of the map not covered by the panel
  function visibleArea() {
    const w = box.clientWidth, h = box.clientHeight;
    const left = !phoneLayout.matches && !side.classList.contains('is-collapsed') ? side.offsetWidth : 0;
    return { w: w - left, h, cx: left + (w - left) / 2, cy: h / 2 };
  }

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
      // A few tries per dish; keep the spot farthest from the pins already placed
      let best = null, bestGap = -1;
      for (let t = 0; t < 10; t++) {
        const p = spotIn(feature, rnd);
        const gap = placed.length ? Math.min(...placed.map(q => Math.hypot(p[0] - q[0], p[1] - q[1]))) : 99;
        if (gap > bestGap) { bestGap = gap; best = p; }
      }
      placed.push(best);

      // Your dish isn't a link (it's the dish you searched); every other pin opens its comparison
      const el = document.createElement(isSource ? 'span' : 'a');
      el.className = 'rmap-pin'
        + (isSource ? ' rmap-pin--source' : '')
        + (!isSource && dish.inTarget ? ' rmap-pin--target' : '')
        + (!isSource && dish.current ? ' is-current' : '');
      if (isSource) {
        el.setAttribute('role', 'img');
        el.setAttribute('aria-label', `Your dish: ${dish.title}`);
      } else {
        el.href = dish.url;
        el.setAttribute('aria-label', `${dish.rank ? '#' + dish.rank + ' ' : ''}${dish.title}, ${dish.label}, ${dish.score}% similar`
          + (dish.current ? ' (this comparison)' : ''));
      }
      const img = document.createElement('img');
      img.alt = '';
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      img.onerror = () => { img.onerror = null; img.src = NO_IMAGE; };
      img.src = dish.image || NO_IMAGE;
      el.appendChild(img);
      if (!isSource && dish.rank) {
        const n = document.createElement('span');
        n.className = 'rmap-pin-rank';
        n.textContent = dish.rank;
        el.appendChild(n);
      }
      const pin = { dish, el, lonlat: best, isSource };
      el.addEventListener('mouseenter', (e) => pinTip(e, pin));
      el.addEventListener('mousemove', (e) => pinTip(e, pin));
      el.addEventListener('mouseleave', () => { tip.hidden = true; });
      // A drag that starts on a pin moves the map instead of opening the link
      el.addEventListener('click', (e) => { if (box.dataset.dragged === '1') e.preventDefault(); });
      pinsLayer.appendChild(el);
      pins.push(pin);
    };
    DATA.dishes.slice().reverse().forEach(d => add(d, false));   // rank 1 drawn last = on top
    add(DATA.source, true);
  }

  function pinTip(event, pin) {
    const d = pin.dish;
    tip.innerHTML = pin.isSource
      ? `<strong>${esc(d.title)}</strong><span>Your dish &middot; ${esc(d.label)}</span>`
      : `<strong>${d.rank ? '#' + d.rank + ' ' : ''}${esc(d.title)}</strong>`
        + `<span>${esc(d.label)} &middot; ${d.score}% similar</span>`
        + `<span>${d.current ? 'You are viewing this comparison' : 'Click to compare'}</span>`;
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

  function showCountryTip(event, f) {
    const key = norm(f.properties.name);
    const c = byKey.get(key);
    if (!c && key !== originKey) { tip.hidden = true; return; }
    tip.innerHTML = c
      ? `<strong>${esc(c.label)}</strong><span>${c.count} dish${c.count === 1 ? '' : 'es'} on the map</span>`
      : `<strong>${esc(DATA.source.label)}</strong><span>Your dish's country</span>`;
    const r = box.getBoundingClientRect();
    tip.style.left = `${event.clientX - r.left + 12}px`;
    tip.style.top = `${event.clientY - r.top + 12}px`;
    tip.hidden = false;
  }

  // ---------- map ----------
  function draw() {
    if (!features) return;
    const w = box.clientWidth, h = box.clientHeight;
    if (w < 20 || h < 20) return;
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    projection = d3.geoMercator().fitSize([w, h], ASIA_FRAME);
    d3.select(g).selectAll('path')
      .data(features)
      .join('path')
      .attr('d', d3.geoPath(projection))
      .attr('stroke', COLORS.border)
      .attr('stroke-width', 0.5)
      .attr('vector-effect', 'non-scaling-stroke')
      .attr('fill', f => fillFor(norm(f.properties.name)))
      .on('mousemove', showCountryTip)
      .on('mouseleave', () => { tip.hidden = true; });

    const first = !zoom;
    if (first) {
      zoomT = d3.zoomIdentity;
      zoom = d3.zoom()
        .scaleExtent([1, 8])
        .filter((event) => {
          // Scroll-zoom only with Ctrl/Cmd (or a pinch), so the page still scrolls over the map
          if (event.type === 'wheel') return event.ctrlKey || event.metaKey;
          return !event.button;
        })
        .on('start', () => { box.dataset.dragged = '0'; })
        .on('zoom', (event) => {
          if (event.sourceEvent && event.sourceEvent.type !== 'wheel') box.dataset.dragged = '1';
          zoomT = event.transform;
          g.setAttribute('transform', zoomT.toString());
          positionPins();
        })
        .on('end', () => { setTimeout(() => { box.dataset.dragged = '0'; }, 0); });
      d3.select(box).call(zoom).on('dblclick.zoom', null);
    }
    // Big margin around the map so it can be dragged anywhere, even when not zoomed in
    const padX = Math.max(w * 0.6, 440), padY = Math.max(h * 0.6, 320);
    zoom.translateExtent([[-padX, -padY], [w + padX, h + padY]]).extent([[0, 0], [w, h]]);
    if (first) {
      buildPins();
      d3.select(box).call(zoom.transform, homeTransform());
    }
    positionPins();
  }

  // Whole map, centred in the part not covered by the panel
  function homeTransform() {
    const v = visibleArea();
    return d3.zoomIdentity.translate(v.cx - box.clientWidth / 2, v.cy - box.clientHeight / 2);
  }
  function goTo(transform, ms = 250) {
    if (zoom) d3.select(box).transition().duration(ms).call(zoom.transform, transform);
  }

  const zoomBy = (k) => zoom && d3.select(box).transition().duration(250).call(zoom.scaleBy, k);
  document.getElementById('rmap-zoom-in').addEventListener('click', () => zoomBy(1.6));
  document.getElementById('rmap-zoom-out').addEventListener('click', () => zoomBy(1 / 1.6));
  document.getElementById('rmap-zoom-reset').addEventListener('click', () => goTo(homeTransform()));
  // Target button: zoom in on your dish
  document.getElementById('rmap-focus').addEventListener('click', () => {
    const pin = pins.find(p => p.isSource);
    if (!pin || !projection) return;
    const [x, y] = projection(pin.lonlat);
    const v = visibleArea();
    const k = Math.max(zoomT.k, 3.5);
    goTo(d3.zoomIdentity.translate(v.cx - k * x, v.cy - k * y).scale(k), 450);
  });

  // ---------- load ----------
  if (window.d3 && window.topojson) {
    d3.json(TOPOLOGY_URL)
      .then(topo => {
        features = topojson.feature(topo, topo.objects.countries).features;
        draw();
      })
      .catch(() => box.classList.add('is-offline'));
  } else {
    box.classList.add('is-offline');
  }
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(draw, 150);
  });
});
