'use strict';
/* showme web UI: tabs, virtualized grid, raw single-image viewer. No build step. */

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem('showme.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('showme.' + k, JSON.stringify(v)); } catch { /* ignore */ } },
};

async function api(method, url, body) {
  const r = await fetch(url, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ------------------------------------------------------------------ state
const S = {
  tabs: [], active: null, data: new Map(), focusSeq: 0, firstMsg: true, connected: false,
  size: store.get('size', 160), info: store.get('info', false),
};
const GAP = 6, PAD = 8, CAP = 16;

function summary(id) { return S.tabs.find((t) => t.id === id); }
function td() { return S.active ? S.data.get(S.active) : null; }
function basename(p) { return p.slice(p.lastIndexOf('/') + 1); }
function flagCss(f) { return f && f[0] === '#' ? f : `var(--flag-${f || 'default'})`; }
function itemName(it) { return it.label || basename(it.path); }
function displayOf(id) { return (summary(id) || {}).display || { mode: 'native' }; }

function tabData(id) {
  let d = S.data.get(id);
  if (!d) {
    d = { id, items: null, version: -1, loading: null, list: new Int32Array(0), pos: 0,
      ui: { view: null, filter: '', flaggedOnly: false, scrollTop: 0, noteHidden: null, prevMode: 'native' } };
    S.data.set(id, d);
  }
  return d;
}

async function loadTab(id) {
  const d = tabData(id);
  if (d.loading) return d.loading;
  d.loading = (async () => {
    try {
      const j = await api('GET', `/api/tabs/${id}`);
      const sel = d.items ? d.list[d.pos] : 0;
      d.items = j.items; d.version = j.version;
      if (!d.ui.view) d.ui.view = d.items.length === 1 ? 'single' : 'grid';
      recomputeList(d, sel);
    } finally { d.loading = null; }
  })();
  return d.loading;
}

function recomputeList(d, keepSel) {
  const q = d.ui.filter.trim().toLowerCase(), fo = d.ui.flaggedOnly, items = d.items || [];
  if (!q && !fo) {
    d.list = new Int32Array(items.length);
    for (let i = 0; i < items.length; i++) d.list[i] = i;
  } else {
    const out = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (fo && !it.flag) continue;
      if (q && !(it.path.toLowerCase().includes(q) || (it.label || '').toLowerCase().includes(q)
        || (it.note || '').toLowerCase().includes(q))) continue;
      out.push(i);
    }
    d.list = Int32Array.from(out);
  }
  const p = keepSel == null ? -1 : d.list.indexOf(keepSel);
  d.pos = p >= 0 ? p : Math.min(d.pos, Math.max(0, d.list.length - 1));
}

// ------------------------------------------------------------------ server events
function connect() {
  const es = new EventSource('/api/events');
  es.onopen = () => { S.connected = true; $('conn').classList.add('ok'); $('conn').title = 'Connected'; };
  es.onerror = () => { S.connected = false; $('conn').classList.remove('ok'); $('conn').title = 'Disconnected (retrying)'; S.reconnected = true; };
  es.onmessage = (e) => applySnapshot(JSON.parse(e.data));
}

async function applySnapshot(m) {
  S.tabs = m.tabs;
  const ids = new Set(m.tabs.map((t) => t.id));
  for (const id of [...S.data.keys()]) if (!ids.has(id)) S.data.delete(id);
  let focus = null;
  if (m.focus) {
    if (S.reconnected && !S.firstMsg) { S.focusSeq = m.focus.seq; S.reconnected = false; }
    else if (m.focus.seq > S.focusSeq || S.firstMsg) { S.focusSeq = m.focus.seq; focus = m.focus; }
  }
  if (S.firstMsg) { S.firstMsg = false; if (!focus || focus.tab !== m.active) focus = m.active ? { tab: m.active } : null; }
  // refresh stale data of the active tab
  for (const t of m.tabs) {
    const d = S.data.get(t.id);
    if (d && d.items && d.version !== t.version && t.id === S.active && !(focus && focus.tab === t.id)) {
      await loadTab(t.id);
      if (S.active === t.id) render();
    }
  }
  if (focus && ids.has(focus.tab)) {
    await activate(focus.tab, { view: focus.view, index: focus.index });
  } else if (!ids.has(S.active)) {
    await activate(m.active && ids.has(m.active) ? m.active : (m.tabs.length ? m.tabs[m.tabs.length - 1].id : null));
  } else {
    renderTabs(); renderToolbar(); renderNote();
    const d = td();
    if (d && d.items) {
      if (d.ui.view === 'single') { if (V.img && V.dispKey !== dispKey()) { drawViewer(true); updateStatus(); } }
      else if (G.dispKey !== dispKey()) refreshThumbs();
    }
  }
}

// ------------------------------------------------------------------ tabs
async function activate(id, opts = {}) {
  const prev = td();
  if (prev && prev.id !== id) prev.ui.scrollTop = $('grid').scrollTop;
  S.active = id;
  if (!id) { render(); return; }
  const d = tabData(id);
  if (!d.items || d.version !== (summary(id) || {}).version) await loadTab(id);
  if (S.active !== id) return;
  if (opts.view) d.ui.view = opts.view;
  if (opts.index != null) {
    let p = d.list.indexOf(opts.index);
    if (p < 0) { d.ui.filter = ''; d.ui.flaggedOnly = false; recomputeList(d); p = opts.index; }
    d.pos = p;
  }
  render(true);
  if (opts.index != null && d.ui.view === 'grid') { ensureVisible(d.pos, true); renderGrid(); }
  reportView();
}

function closeTab(id) {
  api('DELETE', `/api/tabs/${id}`).catch((e) => console.warn(e));
}

function renderTabs() {
  const box = $('tabs');
  box.textContent = '';
  if (!S.tabs.length) { const e = document.createElement('span'); e.className = 'no-tabs'; e.textContent = 'No open tabs'; box.appendChild(e); }
  for (const t of S.tabs) {
    const el = document.createElement('div');
    el.className = 'tab' + (t.id === S.active ? ' active' : '');
    el.title = `${t.title}\n${t.count} image(s), ${t.flagged} flagged · id ${t.id}${t.note ? '\n\n' + t.note : ''}`;
    el.innerHTML = '<span class="t-title"></span><span class="t-flag"></span><span class="t-count"></span><button class="t-close" title="Close (W)">×</button>';
    el.querySelector('.t-title').textContent = t.title;
    el.querySelector('.t-count').textContent = t.count > 1 ? t.count.toLocaleString() : '';
    el.querySelector('.t-flag').textContent = t.flagged ? '⚑' + t.flagged : '';
    el.onmousedown = (e) => { if (e.button === 1) { e.preventDefault(); closeTab(t.id); } };
    el.onclick = (e) => { if (e.target.classList.contains('t-close')) closeTab(t.id); else if (t.id !== S.active) activate(t.id); };
    box.appendChild(el);
    if (t.id === S.active) requestAnimationFrame(() => el.scrollIntoView({ block: 'nearest', inline: 'nearest' }));
  }
}

// ------------------------------------------------------------------ top-level render
function render(resetScroll) {
  renderTabs();
  const d = td();
  const has = !!(d && d.items);
  $('empty').hidden = has && d.list.length > 0;
  if (has && d.list.length === 0) {
    $('empty').querySelector('.empty-title').textContent = d.items.length ? 'No match' : 'Empty tab';
  } else $('empty').querySelector('.empty-title').textContent = 'No images';
  const view = has ? d.ui.view : 'grid';
  document.body.classList.toggle('view-grid', view === 'grid');
  document.body.classList.toggle('view-single', view === 'single');
  $('grid').hidden = !has || view !== 'grid';
  $('viewer').hidden = !has || view !== 'single' || d.list.length === 0;
  $('info').hidden = !S.info;
  renderToolbar(); renderNote();
  if (!has) { clearGrid(); $('st-pos').textContent = ''; $('st-name').textContent = ''; $('st-flag').textContent = ''; $('st-pixel').textContent = ''; $('st-meta').textContent = ''; return; }
  if (view === 'grid') {
    layoutGrid(true);
    if (resetScroll) $('grid').scrollTop = d.ui.scrollTop || 0;
    renderGrid();
  } else {
    clearGrid();
    showCurrent();
  }
  updateStatus();
}

function renderToolbar() {
  const d = td();
  for (const b of $('seg-view').children) b.classList.toggle('on', !!d && b.dataset.view === d.ui.view);
  $('btn-flagged-only').classList.toggle('on', !!d && d.ui.flaggedOnly);
  $('btn-info').classList.toggle('on', S.info);
  if (d && document.activeElement !== $('filter')) $('filter').value = d.ui.filter;
  const s = d && summary(d.id);
  $('flag-count').textContent = '⚑ ' + (s ? s.flagged : 0);
  const disp = d ? displayOf(d.id) : { mode: 'native' };
  $('display').value = disp.mode;
  $('manual').hidden = disp.mode !== 'manual';
  if (disp.mode === 'manual') {
    if (document.activeElement !== $('lo')) $('lo').value = disp.lo;
    if (document.activeElement !== $('hi')) $('hi').value = disp.hi;
  }
}

function renderNote() {
  const s = S.active && summary(S.active), d = td();
  const note = s && s.note;
  $('notebar').hidden = !note || (d && d.ui.noteHidden === note);
  if (note) $('note').textContent = note;
}

// ------------------------------------------------------------------ grid (virtualized)
const G = { cols: 1, cellW: 160, cellH: 182, rows: 0, cells: new Map(), pool: [], loadTimer: 0, loadFirst: 0 };

function thumbReq() {
  const px = S.size * (window.devicePixelRatio || 1);
  return px <= 128 ? 128 : px <= 256 ? 256 : 512;
}
function dispQuery(id) {
  const d = displayOf(id);
  let q = `d=${d.mode}`;
  if (d.mode === 'manual') q += `&lo=${d.lo}&hi=${d.hi}`;
  return q;
}
function thumbUrl(id, i) { return `/api/tabs/${id}/thumb/${i}?s=${thumbReq()}&${dispQuery(id)}`; }

function layoutGrid() {
  const d = td(); if (!d) return;
  const W = $('grid').clientWidth - PAD * 2;
  G.cols = Math.max(1, Math.floor((W + GAP) / (S.size + GAP)));
  G.cellW = Math.floor((W - (G.cols - 1) * GAP) / G.cols);
  G.cellH = G.cellW + CAP;
  G.rows = Math.ceil(d.list.length / G.cols);
  $('grid-inner').style.height = (G.rows * (G.cellH + GAP) - GAP + PAD * 2) + 'px';
  for (const [p, el] of G.cells) placeCell(el, p);
}
function placeCell(el, p) {
  const r = Math.floor(p / G.cols), c = p % G.cols;
  el.style.transform = `translate(${PAD + c * (G.cellW + GAP)}px, ${PAD + r * (G.cellH + GAP)}px)`;
  el.style.width = G.cellW + 'px'; el.style.height = G.cellH + 'px';
}
function clearGrid() {
  for (const el of G.cells.values()) { el.remove(); recycle(el); }
  G.cells.clear();
}
function recycle(el) { const img = el._img; img.removeAttribute('src'); img.dataset.want = ''; G.pool.push(el); }

function makeCell() {
  const el = document.createElement('div');
  el.className = 'cell';
  el.innerHTML = '<div class="th"><img alt="" draggable="false"></div><div class="cap"></div>';
  el._img = el.querySelector('img'); el._cap = el.querySelector('.cap'); el._th = el.querySelector('.th');
  el._img.onload = () => {
    const img = el._img;
    const r = Math.min(img.clientWidth / img.naturalWidth, img.clientHeight / img.naturalHeight);
    img.classList.toggle('px', r * (window.devicePixelRatio || 1) > 1.01);
  };
  el._img.onerror = () => { if (el._img.getAttribute('src')) el._cap.textContent = '⚠ ' + el._cap.textContent; };
  el.onclick = () => { const d = td(); if (d) { d.pos = el._pos; markSelection(); updateStatus(); updateInfo(); reportView(); } };
  el.ondblclick = () => openSingle(el._pos);
  return el;
}

function fillCell(el, d, p) {
  const i = d.list[p], it = d.items[i];
  el._pos = p; el._i = i;
  el._cap.textContent = itemName(it);
  el.title = `#${i}  ${it.path}${it.note ? '\n⚑ ' + it.note : ''}`;
  el.classList.toggle('flagged', !!it.flag);
  el.classList.toggle('sel', p === d.pos);
  let badge = el._th.querySelector('.fbadge');
  if (it.flag) {
    el.style.setProperty('--fc', flagCss(it.flag));
    if (!badge) { badge = document.createElement('span'); badge.className = 'fbadge'; el._th.appendChild(badge); }
  } else if (badge) badge.remove();
  const want = thumbUrl(d.id, i);
  if (el._img.dataset.want !== want) {
    el._img.removeAttribute('src'); el._img.dataset.want = want; el._img.classList.remove('px');
  }
}

function renderGrid() {
  const d = td(); if (!d || d.ui.view !== 'grid') return;
  const g = $('grid'), rowH = G.cellH + GAP;
  const r0 = Math.max(0, Math.floor((g.scrollTop - PAD) / rowH) - 1);
  const r1 = Math.min(G.rows, Math.ceil((g.scrollTop + g.clientHeight) / rowH) + 1);
  const p0 = r0 * G.cols, p1 = Math.min(d.list.length, r1 * G.cols);
  G.dispKey = dispKey();
  for (const [p, el] of G.cells) {
    if (p < p0 || p >= p1) { el.remove(); recycle(el); G.cells.delete(p); }
  }
  const inner = $('grid-inner');
  for (let p = p0; p < p1; p++) {
    let el = G.cells.get(p);
    if (!el) {
      el = G.pool.pop() || makeCell();
      G.cells.set(p, el);
      placeCell(el, p);
      inner.appendChild(el);
    }
    fillCell(el, d, p);
  }
  scheduleLoad();
}

function scheduleLoad() {
  const now = performance.now();
  if (G.loadTimer) { if (now - G.loadFirst < 250) clearTimeout(G.loadTimer); else return; }
  else G.loadFirst = now;
  G.loadTimer = setTimeout(() => {
    G.loadTimer = 0;
    // load visible cells first (center outwards is overkill; top-down is fine)
    const ps = [...G.cells.keys()].sort((a, b) => a - b);
    for (const p of ps) {
      const img = G.cells.get(p)._img;
      if (img.dataset.want && img.getAttribute('src') !== img.dataset.want) img.src = img.dataset.want;
    }
  }, 60);
}

function refreshThumbs() {
  const d = td(); if (!d) return;
  G.dispKey = dispKey();
  for (const [p, el] of G.cells) fillCell(el, d, p);
  scheduleLoad();
}

function markSelection() {
  const d = td();
  for (const [p, el] of G.cells) el.classList.toggle('sel', p === d.pos);
}

function ensureVisible(p, center) {
  const g = $('grid'), rowH = G.cellH + GAP;
  const y = PAD + Math.floor(p / G.cols) * rowH;
  const far = y + G.cellH < g.scrollTop - rowH || y > g.scrollTop + g.clientHeight + rowH;
  if (center && (far || y < g.scrollTop || y + G.cellH > g.scrollTop + g.clientHeight)) g.scrollTop = y - (g.clientHeight - G.cellH) / 2;
  else if (y < g.scrollTop) g.scrollTop = y - PAD;
  else if (y + G.cellH > g.scrollTop + g.clientHeight) g.scrollTop = y + G.cellH - g.clientHeight + PAD;
}

// ------------------------------------------------------------------ navigation
function move(delta, abs) {
  const d = td(); if (!d || !d.list.length) return;
  const n = d.list.length;
  const p = abs != null ? abs : Math.max(0, Math.min(n - 1, d.pos + delta));
  goTo(p);
}
function goTo(p, center) {
  const d = td(); if (!d) return;
  d.pos = p;
  if (d.ui.view === 'grid') { ensureVisible(p, center); renderGrid(); markSelection(); }
  else showCurrent();
  updateStatus(); updateInfo(); reportView();
}
function nextFlagged(dir) {
  const d = td(); if (!d || !d.list.length) return;
  const n = d.list.length;
  for (let k = 1; k <= n; k++) {
    const p = ((d.pos + dir * k) % n + n) % n;
    if (d.items[d.list[p]].flag) { goTo(p, true); return; }
  }
  flash('No flagged image');
}
function openSingle(p) {
  const d = td(); if (!d) return;
  if (p != null) d.pos = p;
  d.ui.scrollTop = $('grid').scrollTop;
  d.ui.view = 'single'; render(); reportView();
}
function setView(v) {
  const d = td(); if (!d || d.ui.view === v) return;
  if (v === 'single') openSingle();
  else { d.ui.view = 'grid'; render(); ensureVisible(d.pos); renderGrid(); reportView(); }
}

let reportTimer = 0;
function reportView() {
  clearTimeout(reportTimer);
  reportTimer = setTimeout(() => {
    const d = td();
    api('POST', '/api/view', d ? { tab: d.id, view: d.ui.view, index: d.list[d.pos] ?? null } : { tab: null }).catch(() => {});
  }, 250);
}

async function toggleFlag() {
  const d = td(); if (!d || !d.list.length) return;
  const i = d.list[d.pos], it = d.items[i];
  try {
    await api('POST', `/api/tabs/${d.id}/flags`, it.flag ? { targets: [i], unflag: true } : { targets: [i], by: 'user' });
  } catch (e) { flash(e.message); }
}

// ------------------------------------------------------------------ viewer
const V = { key: null, img: null, bmp: null, scale: 1, tx: 0, ty: 0, fit: true, mouse: null, dispKey: null, cache: new Map(), err: null };
const canvas = $('canvas'), ctx = canvas.getContext('2d');

function dispKey() { const d = S.active && displayOf(S.active); return d ? JSON.stringify(d) : ''; }

function fetchRaw(id, i) {
  const key = `${id}:${i}`;
  if (V.cache.has(key)) { const v = V.cache.get(key); V.cache.delete(key); V.cache.set(key, v); return v; }
  const pr = (async () => {
    const r = await fetch(`/api/tabs/${id}/raw/${i}`);
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || r.statusText); }
    const meta = JSON.parse(r.headers.get('X-Meta'));
    const buf = await r.arrayBuffer();
    const data = meta.transport === 'u8' ? new Uint8Array(buf) : meta.transport === 'u16' ? new Uint16Array(buf) : new Float32Array(buf);
    return { meta, data, w: meta.width, h: meta.height, c: meta.channels };
  })();
  pr.catch(() => V.cache.delete(key));
  V.cache.set(key, pr);
  while (V.cache.size > 8) V.cache.delete(V.cache.keys().next().value);
  return pr;
}

function windowOf(img) {
  const d = displayOf(S.active);
  if (d.mode === 'manual') return [d.lo, d.hi === d.lo ? d.lo + 1 : d.hi];
  return img.meta[d.mode] || img.meta.native;
}

function buildBitmap(img) {
  const { w, h, c, data, meta } = img;
  const [lo, hi] = windowOf(img);
  const scale = 255 / (hi - lo);
  const id = new ImageData(w, h);
  const out = new Uint32Array(id.data.buffer);
  const n = w * h;
  let map;
  if (meta.transport !== 'f32') {
    const size = meta.transport === 'u8' ? 256 : 65536;
    const lut = new Uint8Array(size);
    for (let v = 0; v < size; v++) { const x = Math.round((v - lo) * scale); lut[v] = x < 0 ? 0 : x > 255 ? 255 : x; }
    map = (v) => lut[v];
  } else {
    map = (v) => { const x = Math.round((v - lo) * scale); return x >= 0 ? (x > 255 ? 255 : x) : 0; }; // NaN -> 0
  }
  const amax = meta.transport === 'u8' ? 255 : meta.transport === 'u16' ? 65535 : 1;
  const alpha = (v) => { const a = Math.round(v * 255 / amax); return a < 0 ? 0 : a > 255 ? 255 : a; };
  if (c === 1) for (let k = 0; k < n; k++) { const v = map(data[k]); out[k] = 0xff000000 | (v << 16) | (v << 8) | v; }
  else if (c === 2) for (let k = 0, j = 0; k < n; k++, j += 2) { const v = map(data[j]); out[k] = ((alpha(data[j + 1]) << 24) | (v << 16) | (v << 8) | v) >>> 0; }
  else if (c === 3) for (let k = 0, j = 0; k < n; k++, j += 3) out[k] = (0xff000000 | (map(data[j + 2]) << 16) | (map(data[j + 1]) << 8) | map(data[j])) >>> 0;
  else for (let k = 0, j = 0; k < n; k++, j += 4) out[k] = ((alpha(data[j + 3]) << 24) | (map(data[j + 2]) << 16) | (map(data[j + 1]) << 8) | map(data[j])) >>> 0;
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  cv.getContext('2d').putImageData(id, 0, 0);
  return cv;
}

async function showCurrent() {
  const d = td(); if (!d || !d.list.length) return;
  const i = d.list[d.pos], key = `${d.id}:${i}`;
  const it = d.items[i];
  $('viewer').classList.toggle('flagged', !!it.flag);
  if (it.flag) $('viewer').style.setProperty('--fc', flagCss(it.flag));
  if (V.key === key && V.img) { drawViewer(V.dispKey !== dispKey()); return; }
  V.key = key; V.err = null;
  let img;
  const slow = setTimeout(() => { if (V.key === key) $('viewer-msg').textContent = 'Loading…'; }, 150);
  try { img = await fetchRaw(d.id, i); } catch (e) { img = null; if (V.key === key) V.err = e.message; }
  clearTimeout(slow);
  if (V.key !== key) return;
  $('viewer-msg').textContent = V.err ? `⚠ ${V.err}` : '';
  const same = V.img && img && V.img.w === img.w && V.img.h === img.h;
  V.img = img;
  V.bmp = null;
  if (!img) { ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height); updateStatus(); updateInfo(); return; }
  if (V.fit || !same) fit();
  drawViewer(true);
  updateStatus(); updateInfo();
  // prefetch neighbours
  for (const dp of [1, -1]) {
    const p = d.pos + dp;
    if (p >= 0 && p < d.list.length) fetchRaw(d.id, d.list[p]).catch(() => {});
  }
}

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
}

function fit() {
  if (!V.img) return;
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  V.scale = Math.min(cw / V.img.w, ch / V.img.h) * 0.98;
  if (!isFinite(V.scale) || V.scale <= 0) V.scale = 1;
  V.tx = (cw - V.img.w * V.scale) / 2; V.ty = (ch - V.img.h * V.scale) / 2;
  V.fit = true;
}
function zoomAt(f, sx, sy) {
  if (!V.img) return;
  const s = Math.max(0.01, Math.min(256, V.scale * f));
  if (sx == null) { sx = canvas.clientWidth / 2; sy = canvas.clientHeight / 2; }
  V.tx = sx - (sx - V.tx) * (s / V.scale); V.ty = sy - (sy - V.ty) * (s / V.scale);
  V.scale = s; V.fit = false;
  drawViewer();
}
function oneToOne(sx, sy) { zoomAt((1 / (window.devicePixelRatio || 1)) / V.scale, sx, sy); }

function drawViewer(rebuild) {
  resizeCanvas();
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!V.img) return;
  if (rebuild || !V.bmp) { V.bmp = buildBitmap(V.img); V.dispKey = dispKey(); updateInfo(); }
  if (V.fit) fit();
  ctx.setTransform(dpr * V.scale, 0, 0, dpr * V.scale, dpr * V.tx, dpr * V.ty);
  ctx.imageSmoothingEnabled = V.scale * dpr < 1;
  ctx.imageSmoothingQuality = 'high';
  if (V.img.c === 2 || V.img.c === 4) { ctx.fillStyle = checker(); ctx.fillRect(0, 0, V.img.w, V.img.h); }
  ctx.drawImage(V.bmp, 0, 0);
  if (V.scale >= 36) drawPixelValues(dpr);
  $('zoom').textContent = Math.round(V.scale * dpr * 100) + '%';
}

let _checker = null, _checkerTheme = null;
function checker() {  // transparency pattern drawn under images with alpha only
  const css = getComputedStyle(document.documentElement), theme = document.documentElement.dataset.theme;
  if (!_checker || _checkerTheme !== theme) {
  const c = document.createElement('canvas'); c.width = c.height = 2;
  const g = c.getContext('2d');
  g.fillStyle = css.getPropertyValue('--check-a'); g.fillRect(0, 0, 2, 2);
  g.fillStyle = css.getPropertyValue('--check-b'); g.fillRect(0, 0, 1, 1); g.fillRect(1, 1, 1, 1);
  _checker = ctx.createPattern(c, 'repeat'); _checkerTheme = theme;
  }
  _checker.setTransform(new DOMMatrix().scale(8 / V.scale));
  return _checker;
}

function fmt(v) {
  if (Number.isInteger(v)) return String(v);
  if (Number.isNaN(v)) return 'NaN';
  const a = Math.abs(v);
  return a !== 0 && (a < 1e-3 || a >= 1e5) ? v.toExponential(2) : String(+v.toPrecision(4));
}
function pixel(x, y) {
  const { w, c, data } = V.img, o = (y * w + x) * c, out = [];
  for (let k = 0; k < c; k++) out.push(data[o + k]);
  return out;
}

function drawPixelValues(dpr) {
  const { w, h, c } = V.img;
  const x0 = Math.max(0, Math.floor(-V.tx / V.scale)), y0 = Math.max(0, Math.floor(-V.ty / V.scale));
  const x1 = Math.min(w, Math.ceil((canvas.clientWidth - V.tx) / V.scale)), y1 = Math.min(h, Math.ceil((canvas.clientHeight - V.ty) / V.scale));
  if ((x1 - x0) * (y1 - y0) > 4000) return;
  const lines = c === 1 ? 1 : (V.scale >= 24 * c ? c : 0);
  if (!lines) return;
  const [lo, hi] = windowOf(V.img);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const fs = Math.min(13, V.scale / (lines === 1 ? 4 : lines + 1.5));
  ctx.font = `${fs}px ui-monospace, Menlo, monospace`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    const px = pixel(x, y);
    const lum = c >= 3 ? (0.3 * px[0] + 0.59 * px[1] + 0.11 * px[2]) : px[0];
    ctx.fillStyle = (lum - lo) / (hi - lo) > 0.5 ? 'rgba(0,0,0,.8)' : 'rgba(255,255,255,.85)';
    const cx = V.tx + (x + 0.5) * V.scale, cy = V.ty + (y + 0.5) * V.scale;
    for (let k = 0; k < lines; k++) ctx.fillText(fmt(px[k]), cx, cy + (k - (lines - 1) / 2) * fs * 1.15);
  }
}

// pointer: pan / zoom / inspect
let drag = null;
canvas.addEventListener('pointerdown', (e) => {
  if (e.button !== 0) return;
  drag = { x: e.clientX, y: e.clientY, tx: V.tx, ty: V.ty };
  canvas.setPointerCapture(e.pointerId); canvas.classList.add('dragging');
});
canvas.addEventListener('pointermove', (e) => {
  const r = canvas.getBoundingClientRect();
  V.mouse = [e.clientX - r.left, e.clientY - r.top];
  if (drag) { V.tx = drag.tx + e.clientX - drag.x; V.ty = drag.ty + e.clientY - drag.y; V.fit = false; drawViewer(); }
  updatePixel();
});
canvas.addEventListener('pointerup', () => { drag = null; canvas.classList.remove('dragging'); });
canvas.addEventListener('pointerleave', () => { V.mouse = null; updatePixel(); });
canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
  zoomAt(Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0025)), e.clientX - r.left, e.clientY - r.top);
  updatePixel();
}, { passive: false });
canvas.addEventListener('dblclick', (e) => {
  const r = canvas.getBoundingClientRect();
  if (V.fit) oneToOne(e.clientX - r.left, e.clientY - r.top); else { fit(); drawViewer(); }
});

function updatePixel() {
  const el = $('st-pixel');
  if (!V.img || !V.mouse || td()?.ui.view !== 'single') { el.textContent = ''; return; }
  const x = Math.floor((V.mouse[0] - V.tx) / V.scale), y = Math.floor((V.mouse[1] - V.ty) / V.scale);
  if (x < 0 || y < 0 || x >= V.img.w || y >= V.img.h) { el.textContent = ''; return; }
  const px = pixel(x, y).map(fmt);
  el.textContent = `x ${x}  y ${y}  ${px.length === 1 ? px[0] : '(' + px.join(', ') + ')'}`;
}

// ------------------------------------------------------------------ status + info
function updateStatus() {
  const d = td();
  if (!d || !d.list.length) { $('st-pos').textContent = ''; $('st-name').textContent = ''; $('st-flag').textContent = ''; $('st-meta').textContent = ''; return; }
  const i = d.list[d.pos], it = d.items[i];
  const total = d.items.length, n = d.list.length;
  $('st-pos').textContent = `${(d.pos + 1).toLocaleString()} / ${n.toLocaleString()}${n !== total ? ` (of ${total.toLocaleString()})` : ''}`;
  $('st-name').textContent = it.path;
  $('st-name').title = it.path;
  const f = $('st-flag');
  f.textContent = it.flag ? `⚑ ${it.note || (it.by === 'user' ? 'flagged by you' : 'flagged')}` : '';
  f.title = it.note || '';
  if (it.flag) f.style.setProperty('--fc', flagCss(it.flag));
  const img = d.ui.view === 'single' && V.img && V.key === `${d.id}:${i}` ? V.img : null;
  if (img) {
    const m = img.meta, [lo, hi] = windowOf(img);
    $('st-meta').textContent = `${m.width}×${m.height}${m.channels > 1 ? '×' + m.channels : ''}  ${m.dtype}${m.bits ? ` (${m.bits}-bit)` : ''}  window ${fmt(lo)}..${fmt(hi)}`;
  } else $('st-meta').textContent = '';
  updatePixel();
}

function dl(el, rows) {
  el.textContent = '';
  for (const [k, v] of rows) {
    if (v == null || v === '') continue;
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    el.append(dt, dd);
  }
}

function updateInfo() {
  if (!S.info) return;
  const d = td(), s = d && summary(d.id);
  if (!d || !d.list.length) { dl($('info-img'), []); dl($('info-tab'), []); return; }
  const i = d.list[d.pos], it = d.items[i];
  const img = d.ui.view === 'single' && V.img && V.key === `${d.id}:${i}` ? V.img : null;
  const m = img && img.meta;
  const win = img ? windowOf(img) : null;
  dl($('info-img'), [
    ['Name', itemName(it)], ['Index', i], ['Path', it.path],
    ['Flag', it.flag ? (it.note || '✓') + (it.by === 'user' ? ' (you)' : '') : ''],
    ['Size', m && `${m.width} × ${m.height}`], ['Channels', m && m.channels], ['Type', m && m.dtype],
    ['Bits', m && m.bits], ['Min', m && fmt(m.min)], ['Max', m && fmt(m.max)], ['Mean', m && fmt(m.mean)],
    ['Window', win && `${fmt(win[0])} .. ${fmt(win[1])}`],
  ]);
  dl($('info-tab'), [['Title', s && s.title], ['Id', d.id], ['Images', d.items.length.toLocaleString()],
    ['Flagged', s && s.flagged], ['Display', s && s.display.mode], ['Note', s && s.note]]);
  drawHistogram(img, win);
}

function drawHistogram(img, win) {
  const cv = $('hist'), g = cv.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth || 232, H = cv.clientHeight || 72;
  cv.width = W * dpr; cv.height = H * dpr; g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  $('hist-range').textContent = '';
  if (!img) { g.fillStyle = getComputedStyle(document.body).getPropertyValue('--muted'); g.font = '11px sans-serif'; g.fillText('Open an image', 8, H / 2); return; }
  const { data, c, meta } = img, ch = Math.min(c, 3), B = 128;
  const mn = meta.min;
  let mx = meta.max > meta.min ? meta.max : meta.min + 1;
  if (meta.transport !== 'f32') mx = mn + Math.ceil((mx - mn + 1) / B) * B; // integer-aligned bins
  const n = data.length / c, step = Math.max(1, Math.floor(n / 2e6));
  const bins = Array.from({ length: ch }, () => new Float64Array(B));
  for (let k = 0; k < n; k += step) for (let j = 0; j < ch; j++) {
    const v = data[k * c + j];
    if (!(v >= mn && v <= mx)) continue;
    bins[j][Math.min(B - 1, Math.floor((v - mn) / (mx - mn) * B))]++;
  }
  let top = 1;
  for (const b of bins) for (const v of b) top = Math.max(top, Math.sqrt(v));
  const cs = getComputedStyle(document.documentElement), cv_ = (n) => cs.getPropertyValue(n).trim();
  const colors = ch === 1 ? [cv_('--hist')] : [cv_('--hist-r'), cv_('--hist-g'), cv_('--hist-b')];
  g.globalCompositeOperation = ch === 1 ? 'source-over' : 'lighter';
  bins.forEach((b, j) => {
    g.fillStyle = colors[j]; g.globalAlpha = ch === 1 ? 1 : 0.7;
    g.beginPath(); g.moveTo(0, H);
    for (let k = 0; k < B; k++) { const y = H - Math.sqrt(b[k]) / top * (H - 2); g.lineTo(k * W / B, y); g.lineTo((k + 1) * W / B, y); }
    g.lineTo(W, H); g.closePath(); g.fill();
  });
  g.globalCompositeOperation = 'source-over'; g.globalAlpha = 1;
  if (win) {
    g.strokeStyle = getComputedStyle(document.body).getPropertyValue('--accent');
    for (const v of win) { const x = (v - mn) / (mx - mn) * W; if (x >= 0 && x <= W) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); } }
  }
  $('hist-range').textContent = `${fmt(mn)} … ${fmt(mx)}  (√ count)`;
}

let flashTimer = 0;
function flash(msg) {
  $('viewer-msg').textContent = msg;
  const el = $('st-flag'); const old = el.textContent; el.textContent = msg;
  clearTimeout(flashTimer); flashTimer = setTimeout(() => { el.textContent = old; if ($('viewer-msg').textContent === msg) $('viewer-msg').textContent = ''; }, 1500);
}

// ------------------------------------------------------------------ toolbar wiring
$('seg-view').addEventListener('click', (e) => { const v = e.target.dataset.view; if (v) setView(v); });
$('size').value = S.size;
$('size').addEventListener('input', () => {
  const d = td(); const p = d ? d.pos : 0;
  S.size = +$('size').value; store.set('size', S.size);
  layoutGrid(); renderGrid(); if (d) ensureVisible(p);
});
$('filter').addEventListener('input', () => {
  const d = td(); if (!d) return;
  const sel = d.list[d.pos];
  d.ui.filter = $('filter').value; recomputeList(d, sel); render();
});
$('btn-flagged-only').onclick = () => {
  const d = td(); if (!d) return;
  const sel = d.list[d.pos];
  d.ui.flaggedOnly = !d.ui.flaggedOnly; recomputeList(d, sel); render();
};
$('btn-next-flag').onclick = () => nextFlagged(1);
$('btn-prev-flag').onclick = () => nextFlagged(-1);
$('btn-prev').onclick = () => move(-1);
$('btn-next').onclick = () => move(1);
$('btn-fit').onclick = () => { fit(); drawViewer(); };
$('btn-one').onclick = () => oneToOne();
$('btn-info').onclick = () => toggleInfo();
$('btn-help').onclick = () => { $('help').hidden = !$('help').hidden; };
$('help').onclick = () => { $('help').hidden = true; };
$('btn-note-close').onclick = () => { const d = td(), s = summary(S.active); if (d && s) { d.ui.noteHidden = s.note; renderNote(); } };
$('btn-theme').onclick = () => {
  const cur = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = cur; store.set('theme', cur);
  if (td()?.ui.view === 'single') drawViewer(); updateInfo();
};

async function setDisplay(mode, lo, hi) {
  const d = td(); if (!d) return;
  const body = { display: mode };
  if (mode === 'manual') {
    if (lo == null || hi == null) {
      const w = V.img ? windowOf(V.img) : [0, 255];
      lo = +fmt(w[0]); hi = +fmt(w[1]);
    }
    Object.assign(body, { lo, hi });
  }
  try { await api('PATCH', `/api/tabs/${d.id}`, body); } catch (e) { flash(e.message); }
}
$('display').addEventListener('change', () => { setDisplay($('display').value); $('display').blur(); });
for (const id of ['lo', 'hi']) $(id).addEventListener('change', () => {
  const lo = parseFloat($('lo').value), hi = parseFloat($('hi').value);
  if (Number.isFinite(lo) && Number.isFinite(hi) && hi !== lo) setDisplay('manual', lo, hi);
});

function toggleInfo() {
  S.info = !S.info; store.set('info', S.info);
  $('info').hidden = !S.info; renderToolbar();
  requestAnimationFrame(() => { onResize(); updateInfo(); });
}

$('grid').addEventListener('scroll', () => requestAnimationFrame(renderGrid), { passive: true });
function onResize() {
  const d = td(); if (!d || !d.items) return;
  if (d.ui.view === 'grid') { const top = Math.floor($('grid').scrollTop / (G.cellH + GAP)) * G.cols; layoutGrid(); $('grid').scrollTop = PAD + Math.floor(top / G.cols) * (G.cellH + GAP); renderGrid(); }
  else drawViewer();
}
new ResizeObserver(() => onResize()).observe($('stage'));

// ------------------------------------------------------------------ keyboard
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'select' || tag === 'textarea') {
    if (e.key === 'Escape' || (e.key === 'Enter' && e.target.id === 'filter')) e.target.blur();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (!$('help').hidden) { $('help').hidden = true; if (e.key === 'Escape') return; }
  const d = td(), single = d && d.ui.view === 'single';
  const k = e.key;
  let handled = true;
  switch (k) {
    case 'ArrowLeft': case 'a': case 'A': move(-1); break;
    case 'ArrowRight': case 'z': case 'Z': move(1); break;
    case 'ArrowUp': if (single) zoomAt(1.25); else move(-G.cols); break;
    case 'ArrowDown': if (single) zoomAt(0.8); else move(G.cols); break;
    case 'PageUp': if (!single) move(-G.cols * Math.max(1, Math.floor($('grid').clientHeight / (G.cellH + GAP)))); break;
    case 'PageDown': if (!single) move(G.cols * Math.max(1, Math.floor($('grid').clientHeight / (G.cellH + GAP)))); break;
    case 'Home': move(0, 0); break;
    case 'End': if (d) move(0, d.list.length - 1); break;
    case 'Enter': case ' ': if (d && !single) openSingle(); else handled = k === ' '; break;
    case 'Escape': case 'Backspace': case 'g': if (single) setView('grid'); break;
    case 'f': nextFlagged(1); break;
    case 'F': nextFlagged(-1); break;
    case 'x': case 'X': toggleFlag(); break;
    case '0': if (single) { fit(); drawViewer(); } break;
    case '1': if (single) oneToOne(...(V.mouse || [])); break;
    case '+': case '=': if (single) zoomAt(1.25, ...(V.mouse || [])); break;
    case '-': case '_': if (single) zoomAt(0.8, ...(V.mouse || [])); break;
    case 'n': case 'N': if (d) { const cur = displayOf(d.id).mode; if (cur === 'minmax') setDisplay(d.ui.prevMode || 'native'); else { d.ui.prevMode = cur; setDisplay('minmax'); } } break;
    case 'i': case 'I': toggleInfo(); break;
    case '/': $('filter').focus(); break;
    case '?': $('help').hidden = false; break;
    case '[': case ']': {
      const idx = S.tabs.findIndex((t) => t.id === S.active);
      const t = S.tabs[idx + (k === ']' ? 1 : -1)];
      if (t) activate(t.id);
      break;
    }
    case 'w': case 'W': if (S.active) closeTab(S.active); break;
    default: handled = false;
  }
  if (handled) e.preventDefault();
});

// ------------------------------------------------------------------ boot
(function boot() {
  const theme = store.get('theme', null) || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.documentElement.dataset.theme = theme;
  const da = new URLSearchParams(location.search).get('da') || store.get('da', null);
  if (da) { document.documentElement.dataset.da = da; store.set('da', da); }
  render();
  connect();
})();
