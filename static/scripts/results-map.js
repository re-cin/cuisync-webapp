// Comparison page: interactive map of the most similar dishes (bottom of the page).
// - A pin for each dish (photo in a circle, rank badge); clicking it opens that dish's comparison
// - Your dish has a gold ring; the dish on this page is highlighted
// - Side panel with the dishes on the map (made in results.html). Bigger screens: beside the map.
//   Phones: hidden under the preview; in full screen it's a bottom sheet that grows when you scroll
//   the list up and shrinks back (showing the map) when you scroll down past its top. Tapping a pin
//   on a phone scrolls the list to that dish instead of opening it straight away.
// - On the page the map is a STATIC preview: a pre-drawn map file (assets/map/world-map.svg) with
//   the pins placed by simple arithmetic. No map library loads, so first visits don't lag.
//   "Full screen" (or a click on the map) loads d3 + the world data and builds the interactive
//   map in the overlay (moving and zooming, with a wide margin so the map can be moved even when
//   not zoomed in). Target button = focus on your dish, Reset = whole map.
// - Pin spots inside each country are worked out ahead of time (assets/map/pin-spots.json), so
//   the pins are in the same place in the preview and in the interactive map.
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
  const rmap = document.getElementById('rmap');
  const openBtn = document.getElementById('rmap-full-open');
  const closeBtn = document.getElementById('rmap-full-close');
  let isFull = false;
  const NO_IMAGE = '/static/assets/no-image.svg';
  const TOPOLOGY_URL = 'https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json';
  const STATIC_MAP_URL = '/static/assets/map/world-map.svg';
  const SPOTS_URL = '/static/assets/map/pin-spots.json';
  const LIBS = ['https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js',
                'https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js'];
  // The static map file: Mercator, 1000 wide, top at 85N (made from the same world data)
  const STATIC_S = 1000 / (2 * Math.PI);
  const STATIC_YTOP = 3.131301331471645;          // ln(tan(pi/4 + 85deg/2))
  const FRAME = { west: 22, east: 150, south: -12, north: 56 };   // same as ASIA_FRAME below
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
    const bottom = phoneLayout.matches && isFull ? Math.min(side.offsetHeight, h * 0.6) : 0;
    return { w: w - left, h: h - bottom, cx: left + (w - left) / 2, cy: (h - bottom) / 2 };
  }

  // ---------- pins ----------
  // Same random spot every time for the same dish (seeded by its index)
  function seeded(n) {
    let x = (n + 1) * 99991;
    return () => { x = (x * 16807) % 2147483647; return (x - 1) / 2147483646; };
  }
  let SPOTS = {};
  const featureFor = (country) => features.find(f => norm(f.properties.name) === norm(country));

  function buildPins() {
    pinsLayer.replaceChildren();
    pins = [];
    const placed = [];
    const add = (dish, isSource) => {
      const spots = SPOTS[norm(dish.country)];
      if (!spots || !spots.length) return;
      const rnd = seeded(dish.idx);
      // A few tries per dish; keep the spot farthest from the pins already placed
      let best = null, bestGap = -1;
      for (let t = 0; t < 10; t++) {
        const p = spots[Math.floor(rnd() * spots.length)];
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
      // Tooltips only for a real mouse (a tap on a phone fires these too)
      el.addEventListener('mouseenter', (e) => { if (!phoneLayout.matches) pinTip(e, pin); });
      el.addEventListener('mousemove', (e) => { if (!phoneLayout.matches) pinTip(e, pin); });
      el.addEventListener('mouseleave', () => { tip.hidden = true; });
      // A drag that starts on a pin moves the map instead of opening the link
      el.addEventListener('click', (e) => {
        if (box.dataset.dragged === '1') { e.preventDefault(); return; }
        // Phones have no hover tooltip: the first tap shows the dish in the list instead
        if (isFull && phoneLayout.matches && !isSource) {
          e.preventDefault();
          e.stopPropagation();
          tip.hidden = true;
          showInList(dish.idx);
        }
      });
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

  // ---------- static preview (no map library) ----------
  // Same as d3.geoMercator().fitSize([w, h], ASIA_FRAME), then centred in the part of the map
  // not covered by the panel (like homeTransform), done with plain arithmetic.
  const rad = (deg) => deg * Math.PI / 180;
  const ymerc = (lat) => Math.log(Math.tan(Math.PI / 4 + rad(lat) / 2));
  function staticFit() {
    const w = box.clientWidth, h = box.clientHeight;
    const x0 = rad(FRAME.west), x1 = rad(FRAME.east);
    const y0 = -ymerc(FRAME.north), y1 = -ymerc(FRAME.south);
    const k = Math.min(w / (x1 - x0), h / (y1 - y0));
    const v = visibleArea();
    return {
      k,
      tx: (w - k * (x0 + x1)) / 2 + (v.cx - w / 2),
      ty: (h - k * (y0 + y1)) / 2 + (v.cy - h / 2),
    };
  }
  const live = () => Boolean(isFull && projection && zoomT && rmap.classList.contains('is-live'));
  let staticSvg = null;
  function layoutStatic() {
    if (!staticSvg || box.clientWidth < 20) return;
    const f = staticFit();
    const scale = f.k / STATIC_S;
    staticSvg.style.setProperty('--stroke', `${0.5 / scale}px`);   // borders stay 0.5px on screen
    staticSvg.style.transform =
      `matrix(${scale}, 0, 0, ${scale}, ${f.tx - f.k * Math.PI}, ${f.ty - f.k * STATIC_YTOP})`;
  }

  function positionPins() {
    if (live()) {
      pins.forEach(pin => {
        const p = projection(pin.lonlat);
        if (!p) return;
        const [x, y] = zoomT.apply(p);
        pin.el.style.left = `${x}px`;
        pin.el.style.top = `${y}px`;
      });
      return;
    }
    if (box.clientWidth < 20) return;
    const f = staticFit();
    pins.forEach(pin => {
      pin.el.style.left = `${f.k * rad(pin.lonlat[0]) + f.tx}px`;
      pin.el.style.top = `${-f.k * ymerc(pin.lonlat[1]) + f.ty}px`;
    });
  }
  function layoutPreview() {
    layoutStatic();
    positionPins();
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
    if (!features || !isFull) return;
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

    if (!zoom) makeZoom();
    // Big margin around the map so it can be dragged anywhere, even when not zoomed in
    const padX = Math.max(w * 0.6, 440), padY = Math.max(h * 0.6, 320);
    zoom.translateExtent([[-padX, -padY], [w + padX, h + padY]]).extent([[0, 0], [w, h]]);
    // New size (opening full screen, window resize): show the whole map again
    d3.select(box).call(zoom.transform, homeTransform());
  }

  function makeZoom() {
    if (!zoom) {
      zoomT = d3.zoomIdentity;
      // The zoom behaviour is only attached to the map in full screen (see setFull)
      zoom = d3.zoom()
        .scaleExtent([1, 8])
        // Full screen only, so the scroll wheel can zoom (there's no page to scroll there)
        .filter((event) => event.type === 'wheel' || !event.button)
        .on('start', () => { box.dataset.dragged = '0'; })
        .on('zoom', (event) => {
          if (event.sourceEvent && event.sourceEvent.type !== 'wheel') box.dataset.dragged = '1';
          zoomT = event.transform;
          g.setAttribute('transform', zoomT.toString());
          positionPins();
        })
        .on('end', () => { setTimeout(() => { box.dataset.dragged = '0'; }, 0); });
    }
    return zoom;
  }

  // ---------- full screen ----------
  function setFull(on) {
    if (on === isFull) return;
    isFull = on;
    rmap.classList.toggle('is-full', on);
    document.documentElement.classList.toggle('rmap-lock', on);
    openBtn.hidden = on;
    closeBtn.hidden = !on;
    tip.hidden = true;
    setSheet(false);
    panel.scrollTop = 0;
    if (on) {
      requestAnimationFrame(() => {
        layoutPreview();                 // the static map fills the overlay straight away
        closeBtn.focus();
        loadInteractive().then((ok) => {
          if (!ok || !isFull) return;    // no network: stays on the static map
          rmap.classList.add('is-live');
          draw();                        // builds the projection + zoom
          d3.select(box).call(zoom).on('dblclick.zoom', null);   // drag + zoom on
        });
      });
    } else {
      rmap.classList.remove('is-live');
      if (zoom) d3.select(box).on('.zoom', null);                // back to the static preview
      requestAnimationFrame(() => {
        layoutPreview();
        openBtn.focus();
      });
    }
  }
  openBtn.addEventListener('click', () => setFull(true));
  closeBtn.addEventListener('click', () => setFull(false));
  // A click on the map (not on a pin) also opens full screen
  box.addEventListener('click', (e) => {
    if (!isFull) { setFull(true); return; }
    if (phoneLayout.matches && !e.target.closest('.rmap-pin') && box.dataset.dragged !== '1') setSheet(false);
  });

  // ---------- phones, full screen: the list is a bottom sheet ----------
  // Scrolling the list up grows the sheet (the map stays visible in a strip at the top);
  // scrolling down at the top of the list shrinks it back so the map shows again.
  const panel = document.getElementById('rmap-panel');
  let sheetOpen = false;
  let quietUntil = 0;           // ignore scroll events caused by showInList()
  function setSheet(open) {
    sheetOpen = open;
    side.classList.toggle('is-expanded', open);
  }
  const sheetActive = () => isFull && phoneLayout.matches;
  panel.addEventListener('scroll', () => {
    if (!sheetActive()) return;
    if (Date.now() < quietUntil) { quietUntil = Date.now() + 200; return; }   // still our own smooth scroll
    if (!sheetOpen && panel.scrollTop > 4) setSheet(true);
  }, { passive: true });
  let touchY = null;
  panel.addEventListener('touchstart', (e) => { touchY = e.touches[0].clientY; }, { passive: true });
  panel.addEventListener('touchmove', (e) => {
    if (!sheetActive() || touchY === null) return;
    const dy = e.touches[0].clientY - touchY;
    if (sheetOpen && panel.scrollTop <= 0 && dy > 24) { setSheet(false); touchY = null; }
    else if (!sheetOpen && dy < -24) { setSheet(true); touchY = null; }
  }, { passive: true });
  panel.addEventListener('touchend', () => { touchY = null; }, { passive: true });
  panel.addEventListener('wheel', (e) => {          // the same with a mouse / trackpad
    if (!sheetActive()) return;
    if (!sheetOpen && e.deltaY > 0) setSheet(true);
    else if (sheetOpen && e.deltaY < 0 && panel.scrollTop <= 0) setSheet(false);
  }, { passive: true });

  // A pin was tapped: bring its dish into view in the list and highlight it for a moment
  function showInList(idx) {
    const row = panel.querySelector(`li[data-idx="${idx}"]`);
    if (!row) return;
    quietUntil = Date.now() + 800;
    panel.scrollTo({ top: Math.max(0, row.offsetTop - panel.clientHeight / 2 + row.offsetHeight / 2), behavior: 'smooth' });
    panel.querySelectorAll('li.is-flash').forEach((li) => li.classList.remove('is-flash'));
    row.classList.add('is-flash');
    setTimeout(() => row.classList.remove('is-flash'), 1800);
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isFull) setFull(false); });

  // Whole map, centred in the part not covered by the panel
  function homeTransform() {
    if (!window.d3) return null;
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
    if (!pin || !live()) return;
    const [x, y] = projection(pin.lonlat);
    const v = visibleArea();
    const k = Math.max(zoomT.k, 3.5);
    goTo(d3.zoomIdentity.translate(v.cx - k * x, v.cy - k * y).scale(k), 450);
  });

  // ---------- load ----------
  // 1) Straight away: the static map file + the pin spots (small, cached by the browser)
  const staticWrap = document.createElement('div');
  staticWrap.className = 'rmap-static';
  staticWrap.setAttribute('aria-hidden', 'true');
  box.insertBefore(staticWrap, box.firstChild);
  Promise.all([
    fetch(STATIC_MAP_URL).then(r => { if (!r.ok) throw new Error(r.status); return r.text(); }),
    fetch(SPOTS_URL).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }),
  ]).then(([svgText, spots]) => {
    staticWrap.innerHTML = svgText;
    staticSvg = staticWrap.querySelector('svg');
    staticSvg.querySelectorAll('path').forEach(p => p.setAttribute('fill', fillFor(p.dataset.c)));
    SPOTS = spots;
    buildPins();
    layoutPreview();
  }).catch(() => box.classList.add('is-offline'));

  // 2) Only when the map is opened: d3 + the world data for the interactive map.
  //    Starts downloading as soon as the mouse/finger is on the map, so opening it feels quick.
  let interactivePromise = null;
  const loadScript = (src) => new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = resolve;
    el.onerror = reject;
    document.head.appendChild(el);
  });
  function loadInteractive() {
    if (!interactivePromise) {
      rmap.classList.add('is-loading');
      interactivePromise = (window.d3 && window.topojson ? Promise.resolve()
        : LIBS.reduce((p, src) => p.then(() => loadScript(src)), Promise.resolve()))
        .then(() => d3.json(TOPOLOGY_URL))
        .then(topo => {
          features = topojson.feature(topo, topo.objects.countries).features;
          return true;
        })
        .catch(() => { interactivePromise = null; return false; })
        .finally(() => rmap.classList.remove('is-loading'));
    }
    return interactivePromise;
  }
  ['pointerenter', 'touchstart', 'focusin'].forEach(type =>
    rmap.addEventListener(type, () => { loadInteractive(); }, { once: true, passive: true }));

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => (live() ? draw() : layoutPreview()), 150);
  });
});
