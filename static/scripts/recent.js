// "All recent searches" page: search by dish name, filter by origin, sort.
// The list is short (it is capped on the server), so everything happens here.
document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('recent-grid');
  if (!grid) return;
  const cards = Array.from(grid.querySelectorAll('.rc-card'));
  const q = document.getElementById('recent-q');
  const origin = document.getElementById('recent-origin');
  const sort = document.getElementById('recent-sort');
  const count = document.getElementById('recent-count');
  const empty = document.getElementById('recent-empty');

  const norm = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const sorters = {
    recent: (a, b) => b.dataset.ts - a.dataset.ts,
    oldest: (a, b) => a.dataset.ts - b.dataset.ts,
    high: (a, b) => b.dataset.score - a.dataset.score || b.dataset.ts - a.dataset.ts,
    low: (a, b) => a.dataset.score - b.dataset.score || b.dataset.ts - a.dataset.ts,
    az: (a, b) => a.dataset.title.localeCompare(b.dataset.title),
  };

  function render() {
    const words = norm(q.value).split(/\s+/).filter(Boolean);
    const shown = cards.filter(c =>
      (!origin.value || c.dataset.origin === origin.value) &&
      words.every(w => norm(c.dataset.names).includes(w)));
    cards.forEach(c => { c.hidden = !shown.includes(c); });
    shown.sort(sorters[sort.value] || sorters.recent).forEach(c => grid.appendChild(c));
    count.textContent = shown.length;
    empty.hidden = shown.length > 0;
  }

  q.addEventListener('input', render);
  origin.addEventListener('change', render);
  sort.addEventListener('change', render);
  render();
});
