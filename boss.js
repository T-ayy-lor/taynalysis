// Shared page code for the bosses that run on one config each (Nek'zali, Entombed Sentinels, Vashnik, The Lost Explorers,
// The Coiled Altar, Ula'tek). The page defines CFG before loading this file:
//   CFG = {slug, name, enc: [encounter ids], map: {src, cal: {cx, cy, ppy, center: [x, y], rot}}, hitNames, modules: [...],
//          avoid: {names, short, skip(hit, pull), note}, abilities: {name: spell id}, how: 'text'}
// The uploader's FailParser writes the data (data/<team>/<tier>/<slug>/). Each module is ONE mechanic of the fight and lists
// what went wrong in it, and who:
//   {id, title (the mechanic), diffs: '15,16' (only on those difficulties), note,
//    one(pull) -> {head, rows}       the table for one pull
//    all(pulls) -> {head, rows}      the Overall table
//    foot(pulls) -> html             optional line under the table (e.g. who kicked)
//    overlay: {label, diffs, get(pull) -> [{pos, n, t, tip, mark: 'dot' | 'cross', color}], key() -> legend html}}
// head: column titles ('Time', ['Player', 'l'] for a left-aligned one); rows: arrays of cells made with the helpers below.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const ROSTER = [
  ['Integralgrip','Death Knight','Tank'],['Phurry','Warrior','Tank'],
  ['Sueldh','Demon Hunter','Melee'],['Sighhigh','Monk','Melee'],['Tayluor','Paladin','Melee'],['Jeckw','Rogue','Melee'],['Waynegry','Warrior','Melee'],['Whispersix','Warrior','Melee'],
  ['Shènròn','Evoker','Healer'],['Roychi','Monk','Healer'],['Skwup','Paladin','Healer'],['Snakespeare','Priest','Healer'],['Zynzii','Shaman','Healer'],
  ['Progenesis','Demon Hunter','Ranged'],['Kiirelan','Druid','Ranged'],['Agilitÿ','Hunter','Ranged'],['Aktapewpew','Hunter','Ranged'],['Neineimuu','Mage','Ranged'],
  ['Phypherion','Mage','Ranged'],['Spellrift','Mage','Ranged'],['Calexanich','Priest','Ranged'],['Yuña','Priest','Ranged'],['Bigbot','Shaman','Ranged'],
  ['Daragnos','Warlock','Ranged'],['Monostereo','Warlock','Ranged']];
const SPECS = {250:'Death Knight',251:'Death Knight',252:'Death Knight',577:'Demon Hunter',581:'Demon Hunter',1480:'Demon Hunter',102:'Druid',103:'Druid',104:'Druid',105:'Druid',
  1467:'Evoker',1468:'Evoker',1473:'Evoker',253:'Hunter',254:'Hunter',255:'Hunter',62:'Mage',63:'Mage',64:'Mage',268:'Monk',269:'Monk',270:'Monk',65:'Paladin',66:'Paladin',70:'Paladin',
  256:'Priest',257:'Priest',258:'Priest',259:'Rogue',260:'Rogue',261:'Rogue',262:'Shaman',263:'Shaman',264:'Shaman',265:'Warlock',266:'Warlock',267:'Warlock',71:'Warrior',72:'Warrior',73:'Warrior'};
const TANK_SPECS = new Set([250, 581, 104, 268, 66, 73]), HEAL_SPECS = new Set([105, 1468, 270, 65, 256, 257, 264]);
const CLASS_COLOR = {'Death Knight':'#C41E3A','Demon Hunter':'#A330C9','Druid':'#FF7C0A','Evoker':'#33937F','Hunter':'#AAD372','Mage':'#3FC7EB','Monk':'#00FF98','Paladin':'#F48CBA','Priest':'#FFFFFF','Rogue':'#FFF468','Shaman':'#0070DD','Warlock':'#8788EE','Warrior':'#C69B6D'};
const CLASS_OF = Object.fromEntries(ROSTER.map(r => [r[0], r[1]]));
let SPEC_OF = {};
const color = n => CLASS_COLOR[CLASS_OF[n] || SPECS[SPEC_OF[n]]] || '#8a8a8a';
const pname = n => `<span class="pn" style="--cc:${color(n)}">${esc(n)}</span>`;
const role = n => SPEC_OF[n] != null ? (TANK_SPECS.has(SPEC_OF[n]) ? 'Tank' : HEAL_SPECS.has(SPEC_OF[n]) ? 'Healer' : 'DPS') : ((ROSTER.find(r => r[0] === n) || [])[2] || null);
const isTank = n => role(n) === 'Tank';
const isTankIn = (p, n) => p && p.specs && p.specs[n] != null ? TANK_SPECS.has(p.specs[n]) : isTank(n);   // who tanked THIS pull
const rosterIdx = n => { const i = ROSTER.findIndex(r => r[0] === n); return i < 0 ? 999 : i; };
const dayDate = d => { const [m, dd, y] = d.split('/'); return new Date(Date.UTC(+y, +m - 1, +dd)).toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC'}); };
const nightKey = d => { const [m, dd, y] = d.split('/'); return `${y}-${m.padStart(2, '0')}-${dd.padStart(2, '0')}`; };
const mss = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const big = v => v >= 1e9 ? (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'K' : String(Math.round(v));
const pct = v => Math.round(v * 100) + '%';
const DIFF = {14: 'Normal', 15: 'Heroic', 16: 'Mythic'};
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

// ---- helpers over a pull, for the boss configs ----
const auras = (p, a) => (p.auras && p.auras[a]) || [];            // [player, on, off (null: still on), top stack, source, [x, y] when it went on]
const npcAuras = (p, a) => (p.npc || []).filter(x => x[0] === a);  // [aura, unit, unit id, on, off]
const castsOf = (p, c) => (p.casts && p.casts[c]) || [];            // times
const pulsesOf = (p, k) => (p.pulses && p.pulses[k]) || [];         // [time, [players hit], damage]
const addsDied = (p, a) => (p.adds || []).filter(x => x[2] === a);  // [time, unit id, name]
// [t, player, ability, amount, [x, y]]. Abilities that do two kinds of damage at once log two lines per hit: lines for the
// same player and ability under 0.5 s apart are one hit. Hits the uploader keeps by spell ID ('#1305878') get their
// names from CFG.hitNames.
function merged(p){
  if (p._mh) return p._mh;
  const last = {}, out = [], HN = CFG.hitNames || {};
  (p.hits || []).forEach(h => { const k = h[1] + '|' + h[2], prev = last[k];
    if (prev && h[0] - prev[0] < 0.5){ prev[3] += h[3]; return; }
    const c = h.slice(); if (HN[c[2]]) c[2] = HN[c[2]]; last[k] = c; out.push(c); });
  return (p._mh = out);
}
const hitsOf = (p, s) => merged(p).filter(h => h[2] === s);
const hadAura = (p, n, a, t, pad = 0.3) => auras(p, a).some(x => x[0] === n && x[1] - pad <= t && (x[2] == null || t <= x[2] + pad));   // n had debuff a at time t
const inWindows = (t, ws) => ws.some(([a, b]) => t >= a && t <= b);
const diedNear = (p, n, t, w) => p.deaths.some(d => d[1] === n && Math.abs(d[0] - t) <= w);
const posAt = (p, n, t) => { const h = merged(p).filter(x => x[1] === n && x[4]).sort((a, b) => Math.abs(a[0] - t) - Math.abs(b[0] - t))[0]; return h && Math.abs(h[0] - t) < 3 ? h[4] : null; };
const who = ns => ns.length ? ns.map(pname).join(', ') : '<span class="dim">nobody in the log</span>';
const dist = (a, b) => a && b ? Math.hypot(a[0] - b[0], a[1] - b[1]) : null;
const dedupe = (xs, gap, key = x => x[0]) => { const out = []; xs.slice().sort((a, b) => key(a) - key(b)).forEach(x => { if (!out.length || key(x) - key(out[out.length - 1]) > gap) out.push(x); }); return out; };
const bossAt = (p, b, t) => { const tr = (p.btrack || {})[b] || []; let best = null; tr.forEach(q => { if (!best || Math.abs(q[0] - t) < Math.abs(best[0] - t)) best = q; }); return best && Math.abs(best[0] - t) <= 3 ? [best[1], best[2]] : null; };
const addsOf = (p, name) => (p.adds || []).filter(a => a[2] === name);   // [time, unit id, name, [x, y], 'died' / 'gone']
const tgts = (p, c) => ((p.ctgt || {})[c] || []);                       // [time, player a cast landed on]
// ---- table cells for the module configs ----
const T = t => `<td class="num">${mss(t)}</td>`;                          // a time in the pull
const P = n => `<td class="l">${pname(n)}</td>`;                          // a player
const PS = ns => `<td class="wr">${ns.length ? ns.map(pname).join(', ') : '<span class="dim">–</span>'}</td>`;   // several players
const N = (v, cls = 'bad', fmt) => `<td class="num${v ? (cls ? ' ' + cls : '') : ' dim'}">${fmt && v ? fmt(v) : v}</td>`;   // a count
const V = (v, cls = '') => `<td class="num${cls ? ' ' + cls : ''}">${v}</td>`;   // a plain value
const X = (html, cls = '') => `<td class="wr${cls ? ' ' + cls : ''}">${html}</td>`;   // text that wraps
const B = s => `<td class="what bad">${esc(s)}</td>`;                     // what went wrong, in red
// per-player counts over the pulls shown: cols = [{h, get: p => [{n, t}]}]; players with nothing don't show
function perPlayer(list, cols, first = ['Player', 'l']){
  const R = {}, row = n => (R[n] ||= cols.map(() => ({c: 0, t: []})));
  list.forEach(p => cols.forEach((col, i) => col.get(p).forEach(x => { const r = row(x.n)[i]; r.c += x.c || 1; if (x.t != null) r.t.push((list.length > 1 ? 'pull ' + p.seq + ' ' : '') + mss(x.t)); })));
  const rows = Object.entries(R).sort((a, b) => b[1].reduce((s, x) => s + x.c, 0) - a[1].reduce((s, x) => s + x.c, 0) || byRoster(a[0], b[0]));
  return {head: [first, ...cols.map(c => c.h)],
    rows: rows.map(([n, r]) => [P(n), ...r.map((x, i) => `<td class="num${x.c ? ' ' + (cols[i].cls || 'bad') : ' dim'}"${x.t.length ? ` title="${esc(x.t.join(', '))}"` : ''}>${x.c}</td>`)])};
}
// kicks for some casts: one pull lists every missed one; Overall counts per cast
const kicksOf = (p, casts) => (p.kicks || []).filter(k => casts.includes(k[2]));
const kickOne = (p, casts) => ({head: ['Time', ['Missed kick', 'l'], ['From', 'l']],
  rows: kicksOf(p, casts).filter(k => k[3] === 'missed').map(k => [T(k[0]), B(k[2]), `<td class="l">${esc(k[1])}</td>`])});
function kickAll(list, casts){
  const C = {}; casts.forEach(c => { C[c] = {n: 0, k: 0, m: 0}; });
  list.forEach(p => kicksOf(p, casts).forEach(k => { const r = C[k[2]]; r.n++; if (k[3] === 'kicked') r.k++; if (k[3] === 'missed') r.m++; }));
  return {head: [['Cast', 'l'], 'Casts', 'Kicked', 'Missed'], rows: Object.entries(C).map(([c, r]) => [`<td class="l">${esc(c)}</td>`, N(r.n, ''), N(r.k, ''), N(r.m)])};
}
function kickFoot(list, casts){
  const all = list.flatMap(p => kicksOf(p, casts)), kicked = all.filter(k => k[3] === 'kicked'), by = {};
  kicked.forEach(k => k[4] && inc(by, k[4]));
  const top = Object.entries(by).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([n, c]) => `${pname(n)} ${c}`).join(', ');
  return `${kicked.length} of ${all.length} kicked${top ? ' · kicks: ' + top : ''}.`;
}

// ---- theme (shared with the other boss pages) ----
function setTheme(t, remember){
  document.documentElement.dataset.theme = t;
  $('thLight').setAttribute('aria-pressed', String(t === 'light')); $('thDark').setAttribute('aria-pressed', String(t === 'dark'));
  if (remember){ try { localStorage.setItem('vir-theme', t); } catch (e) {} }
}

// ---- the page: built from CFG ----
const panelHead = (title, s) => `<div class="ph"><h2>${title}</h2><select class="scope" data-s="${s}" aria-label="Pulls to show"></select></div>`;
// the map overlays come from the modules: one per mechanic where where-it-happened matters
const OVERLAYS = CFG.modules.filter(m => m.overlay).map(m => Object.assign({diffs: m.diffs}, m.overlay, {id: m.id}));   // overlay.key() is its legend
function buildPage(){
  const opts = BOSSES.filter(b => b.page && (!b.test || SHOW_UNTESTED || b.page === CFG.slug)).map(b => `<option value="${b.page}"${b.page === CFG.slug ? ' selected' : ''}>${esc(b.name)}</option>`).join('');
  const ovs = OVERLAYS;
  const mod = m => `<div class="panel"${m.diffs ? ` data-diffs="${m.diffs}"` : ''}>${panelHead(esc(m.title), m.id)}<div class="tw"><table id="${m.id}T"></table></div><p class="note" id="${m.id}N"></p></div>`;
  const [first, ...rest] = CFG.modules;
  $('wrap').innerHTML = `
  <header>
    <a class="crumb" id="teamLink" href="./">Taynalysis</a>
    <h1><span class="bosspick"><select id="boss" aria-label="Boss">${opts}</select></span></h1>
    <select id="night" aria-label="Raid night" hidden></select>
    <span class="sub" id="sub">Loading…</span>
    <div class="seg" id="wlSeg" role="group" aria-label="Warlock mode" hidden><button type="button" id="wlBtn" aria-pressed="false">Warlock mode</button></div>
    <div class="seg" id="themeSeg" role="group" aria-label="Color theme">
      <button type="button" id="thLight" aria-pressed="false">Light</button>
      <button type="button" id="thDark" aria-pressed="true">Dark</button>
    </div>
    <p class="needs">Needs testing</p>
  </header>
  <div class="diffbar">
    <span class="lbl">Difficulty</span>
    <div class="seg" id="diffSeg" role="group" aria-label="Difficulty">
      <button type="button" data-d="14">Normal<span></span></button><button type="button" data-d="15">Heroic<span></span></button><button type="button" data-d="16">Mythic<span></span></button>
    </div>
    <span class="sub" id="diffNote"></span>
  </div>
  <div class="panel">
    <div class="ph"><h2>Raid nights</h2><span class="sub"><span id="tlHours"></span></span></div>
    <div class="tl" id="tl"></div>
    <div class="tlrow sc"><span></span><div class="scale" id="scale"></div><span></span></div>
    <div class="tlkey" id="tlKey"></div>
  </div>
  <div class="livebar" id="livebar"><span class="ldot" id="liveDot"></span><span id="liveText"></span></div>
  <div class="mapgrid">
    <div class="panel mpanel">
      ${panelHead('Map', 'map')}
      <div class="mapbox" id="mapbox"><div id="mapsvg"></div><div class="tip" id="tip" hidden></div></div>
      <div class="seg ovseg" id="ovSeg" role="group" aria-label="Map overlay">${ovs.map(o => `<button type="button" data-ov="${o.id}">${esc(o.label)}</button>`).join('')}</div>
      <div class="key" id="mapKey"></div>
    </div>
    <div class="panel vpanel"${first.diffs ? ` data-diffs="${first.diffs}"` : ''}>
      ${panelHead(esc(first.title), first.id)}
      <div class="tw vt"><table id="${first.id}T"></table></div>
      <p class="note" id="${first.id}N"></p>
    </div>
  </div>
  ${rest.length ? `<div class="mods">${rest.map(mod).join('')}</div>` : ''}
  <div class="panel">
    ${panelHead('Avoidable damage taken', 'avoid')}
    <div class="tw"><table id="avoidT"></table></div>
    <p class="note">${CFG.avoid.note || 'Damage taken from each (hover a number for how many hits; shields count).'}</p>
  </div>
  <div class="bottom">
    <div class="panel">${panelHead('Damage', 'dmg')}<div class="meter" id="dmg"></div></div>
    <div class="stack">
      <div class="panel">${panelHead('Healing', 'heal')}<div class="meter" id="heal"></div></div>
      <div class="panel">${panelHead('Dispels', 'disp')}<div class="meter" id="disp"></div></div>
      <div class="panel">${panelHead('Early deaths', 'deaths')}<div class="tw"><table id="deaths"></table></div></div>
    </div>
  </div>
  <div class="panel">
    <h2>How this is measured</h2>
    <p class="note">${CFG.how}</p>
  </div>`;
}
buildPage();
(() => { let saved = null; try { saved = localStorage.getItem('vir-theme'); } catch (e) {} setTheme(saved || 'dark', false); })();
$('thLight').onclick = () => setTheme('light', true); $('thDark').onclick = () => setTheme('dark', true);

// ---- view state ----
const SCOPES = ['map', ...CFG.modules.map(m => m.id), 'avoid', 'dmg', 'heal', 'disp', 'deaths'];
const OVS = OVERLAYS.map(o => o.id);
const st = {scope: Object.fromEntries(SCOPES.map(k => [k, 'latest'])), ov: OVS[0], diff: null};   // scope: 'latest', 'all' or a pull's key
try { const v = JSON.parse(localStorage.getItem(CFG.slug + '-view') || '{}'); if (OVS.includes(v.ov)) st.ov = v.ov; } catch (e) {}
const saveView = () => { try { localStorage.setItem(CFG.slug + '-view', JSON.stringify({ov: st.ov})); } catch (e) {} };

// ---- data: <team data folder>/<slug>/nights.json lists each log's file; a night is every log for one date ----
const SITE = {index: null, files: {}, view: null, err: null};
async function fetchJSON(u){ const r = await fetch(u + '?t=' + Date.now(), {cache: 'no-store'}); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }
function nights(){
  const g = {};
  ((SITE.index && SITE.index.nights) || []).forEach(e => { if (!e.date) return; const k = nightKey(e.date); (g[k] ||= {key: k, date: e.date, entries: []}).entries.push(e); });
  const ns = Object.values(g).sort((a, b) => a.key.localeCompare(b.key));
  ns.forEach(n => {   // every pull of the night, in clock order, across the night's logs
    n.pulls = [];
    n.entries.forEach(e => { const D = SITE.files[e.file]; if (!D) return; Object.assign(SPEC_OF, D.specs || {});
      (D.pulls || []).forEach(p => n.pulls.push(Object.assign({}, p, {night: n.date, nk: n.key, key: e.file + '#' + p.n, tz: D.tz}))); });
    n.pulls.sort((a, b) => a.clock.localeCompare(b.clock));
    const c = {}; n.pulls.forEach(p => { p.seq = c[p.diff] = (c[p.diff] || 0) + 1; });   // numbered within each difficulty
  });
  return ns;
}
function viewPulls(ns){ return SITE.view === 'all' ? ns.flatMap(n => n.pulls) : ((ns.find(n => n.key === SITE.view) || {}).pulls || []); }
// checked every few seconds; the page only redraws when something actually changed
async function checkSite(){
  let changed = !SITE.index; const err0 = SITE.err;
  try {
    const idx = await fetchJSON(dataDir(CFG.slug) + 'nights.json');
    await Promise.all((idx.nights || []).map(async e => { const have = SITE.files[e.file]; if (!have || have.updated !== e.updated){ SITE.files[e.file] = await fetchJSON(e.file); changed = true; } }));
    if (SITE.index && (SITE.index.nights || []).length !== (idx.nights || []).length) changed = true;
    SITE.index = idx; SITE.err = null;
    if (!SITE.view){ const ns = nights(); SITE.view = ns.length ? ns[ns.length - 1].key : null; }   // newest night on load only
  } catch (e) {
    SITE.err = /404/.test(String(e)) ? null : 'Could not load the data; trying again in a few seconds.';
    if (!SITE.index) SITE.index = {nights: []};
  }
  if (await checkActivity()) changed = true;
  if (!changed && SITE.err === err0) return;
  render();
}

const byRoster = (a, b) => rosterIdx(a) - rosterIdx(b) || a.localeCompare(b);
const present = p => [...new Set([...Object.keys(p.dmg || {}), ...Object.keys(p.heal || {}), ...p.deaths.map(d => d[1])])];
const people = list => [...new Set(list.flatMap(present))].sort(byRoster);
const hpText = p => Object.entries(p.hp || {}).map(([b, v]) => `${b} ${pct(v)}`).join(' · ');
const pullLabel = (p, all) => `${all ? dayDate(p.night) + ' · ' : ''}Pull ${p.seq} · ${pullTime(p.date, p.clock, p.tz)} · ${mss(p.dur)} · ${p.live ? 'in progress' : p.kill ? 'kill' : 'wipe'}` +
  (p.kill || !hpText(p) ? '' : ' · ' + hpText(p));
const inc = (o, k, v = 1) => { o[k] = (o[k] || 0) + v; };
const cell = (v, cls, fmt) => `<td class="num${v ? (cls ? ' ' + cls : '') : ' dim'}">${fmt ? fmt(v) : v}</td>`;
const blankRows = (cols, n) => Array.from({length: n}, () => `<tr><td class="l dim">—</td>${'<td class="num dim">0</td>'.repeat(cols)}</tr>`).join('');

// ---- avoidable hits: the config's abilities, minus the ones its skip() rule lets through ----
function avoidHits(p){ return merged(p).filter(h => CFG.avoid.names.includes(h[2]) && !(CFG.avoid.skip && CFG.avoid.skip(h, p))); }

// ---- map: a square crop of the room image. cal: world (cx, cy) sits at image pixel center; ppy pixels per yard; north up,
// turned rot degrees. Positions come straight from the log, so marks land where the player stood. ----
const MAPW = 600;
function toMap(q){
  const c = CFG.map.cal, r = (c.rot || 0) * Math.PI / 180, u = -(q[1] - c.cy), v = -(q[0] - c.cx);
  return [c.center[0] + (u * Math.cos(r) + v * Math.sin(r)) * c.ppy, c.center[1] + (-u * Math.sin(r) + v * Math.cos(r)) * c.ppy];
}
let MK = 1;   // marks grow on small screens so they stay readable
const at = (x, y) => `transform="translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${MK})"`;
const dot = (x, y, c, tip) => `<g ${at(x, y)} data-tip="${esc(tip)}"><circle r="12" fill="transparent"/><circle r="6.5" fill="${c}" stroke="#000" stroke-width="1.6"/></g>`;
const cross = (x, y, c, tip) => `<g ${at(x, y)} data-tip="${esc(tip)}" opacity=".95"><circle r="12" fill="transparent"/><path d="M-7 -7L7 7M7 -7L-7 7" stroke="#000" stroke-width="5" stroke-linecap="round"/><path d="M-7 -7L7 7M7 -7L-7 7" stroke="${c}" stroke-width="2.4" stroke-linecap="round"/></g>`;
const KEY_DOT = '<svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.5" fill="var(--muted)"/></svg>';
const keyDot = c => `<svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.5" fill="${c}" stroke="#000"/></svg>`;
const keyCross = c => `<svg width="12" height="12" viewBox="0 0 12 12"><path d="M2 2L10 10M10 2L2 10" stroke="${c}" stroke-width="2.2" stroke-linecap="round"/></svg>`;
function renderMap(list, all){
  const w = $('mapbox').clientWidth || 600; MK = Math.max(1, Math.min(2, 520 / w));
  const ov = OVERLAYS.find(o => o.id === st.ov);
  let marks = '';
  if (ov) list.forEach(p => ov.get(p).forEach(m => { if (!m.pos) return; const [x, y] = toMap(m.pos);
    if (x < -20 || y < -20 || x > MAPW + 20 || y > MAPW + 20) return;
    const tip = m.tip + (m.t != null ? `<br>${all ? 'pull ' + p.seq + ', ' : ''}${mss(m.t)}` : '');
    const c = m.color || (m.n ? color(m.n) : '#d8d2cf');
    marks += m.mark === 'cross' ? cross(x, y, m.color || '#ff5a4a', tip) : dot(x, y, c, tip); }));
  // lots of marks (Overall, many pulls): smaller, see-through dots, so crowded spots read as density, not one solid blob
  const nMarks = (marks.match(/data-tip=/g) || []).length, dens = nMarks > 150 ? ' dense' : nMarks > 60 ? ' busy' : '';
  $('mapsvg').innerHTML = `<svg class="${dens.trim()}" viewBox="0 0 ${MAPW} ${MAPW}" role="img" aria-label="${esc(CFG.name)} room"><image href="${CFG.map.src}" x="0" y="0" width="${MAPW}" height="${MAPW}"/>${marks}</svg>`;
  $('tip').hidden = true;
  $('ovSeg').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.ov === st.ov)));
  $('mapKey').innerHTML = `${ov ? ov.key() : ''}<span class="hint">Hover a mark for details</span>`;
}
(() => {   // map tooltips: hover (or tap) a mark
  const box = $('mapbox'), tip = $('tip');
  const show = e => { const g = e.target.closest('[data-tip]'); if (!g){ tip.hidden = true; return; }
    tip.innerHTML = g.dataset.tip; tip.hidden = false;
    const r = box.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
    let x = e.clientX - r.left + 14, y = e.clientY - r.top + 14;
    if (x + tw > r.width - 4) x = e.clientX - r.left - tw - 14;
    if (y + th > r.height - 4) y = e.clientY - r.top - th - 14;
    tip.style.left = Math.max(4, x) + 'px'; tip.style.top = Math.max(4, y) + 'px'; };
  box.addEventListener('pointermove', show); box.addEventListener('pointerdown', show);
  box.addEventListener('pointerleave', () => { tip.hidden = true; });
})();
$('ovSeg').querySelectorAll('button').forEach(b => b.onclick = () => { st.ov = b.dataset.ov; saveView(); render(); });

// ---- the modules: each one mechanic; one pull or Overall ----
function renderModule(m, list){
  const r = list.length === 1 ? m.one(list[0]) : m.all(list);
  const head = '<tr>' + r.head.map((h, i) => { const [s, c] = Array.isArray(h) ? h : [h, '']; return `<th${c === 'l' ? ` class="l${i === r.head.length - 1 || i === 1 ? ' fill' : ''}"` : ''}>${esc(s)}</th>`; }).join('') + '</tr>';
  const blank = '<tr>' + r.head.map((h, i) => (Array.isArray(h) && h[1] === 'l') ? '<td class="l dim">—</td>' : `<td class="num dim">${i ? '0' : '–'}</td>`).join('') + '</tr>';
  $(m.id + 'T').innerHTML = head + (r.rows.length ? r.rows.map(x => '<tr>' + x.join('') + '</tr>').join('') : blank);
  $(m.id + 'N').innerHTML = [m.foot ? m.foot(list) : '', m.note || ''].filter(Boolean).join(' ');
}

// ---- timeline: the chosen night's row (every night's with All nights), drawn by drawTimeline in shared.js ----
function renderTimeline(ns){
  const shown = SITE.view === 'all' ? ns : ns.filter(n => n.key === SITE.view);
  const sel = (viewPulls(ns).find(p => SCOPES.every(k => st.scope[k] === p.key)) || {}).key;
  drawTimeline(shown.map(n => ({key: n.key, date: n.date, cur: SITE.view === n.key, logs: n.entries.map(e => e.log),
    pulls: n.pulls.map(p => ({key: p.key, date: p.date, clock: p.clock, tz: p.tz, dur: p.dur, kill: p.kill, diff: p.diff,
      title: `Pull ${p.seq} · ${mss(p.dur)} · ${p.live ? 'in progress' : p.kill ? 'kill' : 'wipe'}`}))})), {enc: CFG.enc, name: CFG.name, diff: st.diff, sel});
  $('tl').querySelectorAll('.tlrow[data-n]').forEach(r => r.onclick = e => {
    const b = e.target.closest('.blk:not(.ob)');
    if (SITE.view !== r.dataset.n) SITE.view = r.dataset.n;
    if (b){ st.diff = +b.dataset.d; SCOPES.forEach(k => { st.scope[k] = b.dataset.k; }); }   // clicking a pull also switches to its difficulty
    render(); });
}

// ---- bottom panels: shared.js draws them with this page's helpers ----
const H = {pname, order: byRoster, healer: n => role(n) === 'Healer', people, color};

// ---- ability tooltips: ability names link to Wowhead, whose tooltip script shows the in-game ability text on hover ----
const ABILITY = CFG.abilities || {};
const AB_RE = Object.keys(ABILITY).length ? new RegExp('(^|[^\\w])(' + Object.keys(ABILITY).sort((a, b) => b.length - a.length).map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s')).join('|') + ')(?![\\w])', 'g') : null;
function linkify(root){
  if (!AB_RE) return;
  const test = new RegExp(AB_RE.source);   // no 'g' flag: test() keeps no state between nodes
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: n =>
    n.parentElement.closest('a, select, option, script, style, svg, .tip, button, input, th, .key, h2') ? NodeFilter.FILTER_REJECT : test.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP});
  const nodes = []; while (walk.nextNode()) nodes.push(walk.currentNode);
  nodes.forEach(n => {
    const frag = document.createDocumentFragment(); let last = 0; const txt = n.nodeValue;
    txt.replace(AB_RE, (m, pre, name, i) => {
      const s = i + pre.length; frag.append(txt.slice(last, s));
      const id = ABILITY[name.replace(/\s/g, ' ')], a = document.createElement('a');
      a.className = 'ab'; a.href = `https://www.wowhead.com/spell=${id}`; a.dataset.wowhead = `spell=${id}`; a.target = '_blank'; a.rel = 'noopener'; a.textContent = name;
      frag.append(a); last = s + name.length; return m; });
    frag.append(txt.slice(last)); n.replaceWith(frag);
  });
  try { if (window.$WowheadPower && $WowheadPower.refreshLinks) $WowheadPower.refreshLinks(); } catch (e) {}
}

// ---- difficulty: picks which pulls every module shows, and hides modules that don't happen on it ----
function renderDiff(ns){
  const all = ns.flatMap(n => n.pulls);
  if (st.diff == null){ const last = all[all.length - 1]; st.diff = last ? last.diff : TEAM.diff; }   // on load: the newest pull's difficulty, else the team's usual one
  const inView = viewPulls(ns);
  $('diffSeg').querySelectorAll('button').forEach(b => { const d = +b.dataset.d, c = inView.filter(p => p.diff === d).length;
    b.setAttribute('aria-pressed', String(d === st.diff)); b.querySelector('span').textContent = c; });
  document.querySelectorAll('[data-diffs]').forEach(el => { el.hidden = !el.dataset.diffs.split(',').includes(String(st.diff)); });
  const ovOk = o => !o.diffs || o.diffs.split(',').includes(String(st.diff));
  OVERLAYS.forEach(o => { const b = $('ovSeg').querySelector(`[data-ov="${o.id}"]`); if (b) b.hidden = !ovOk(o); });
  if (!OVERLAYS.some(o => o.id === st.ov && ovOk(o))) st.ov = (OVERLAYS.find(ovOk) || {}).id;
}
$('diffSeg').querySelectorAll('button').forEach(b => b.onclick = () => { st.diff = +b.dataset.d; render(); });

// ---- render ----
function render(){
  const ns = nights(), all = SITE.view === 'all';
  const sel = $('night'); sel.hidden = !ns.length;
  if (ns.length){ const opts = ns.slice().reverse().map(n => [n.key, dayDate(n.date)]); if (ns.length > 1) opts.push(['all', `All nights (${ns.length})`]);
    sel.innerHTML = opts.map(([v, t]) => `<option value="${v}"${v === SITE.view ? ' selected' : ''}>${esc(t)}</option>`).join(''); }
  renderDiff(ns);
  const viewAll = viewPulls(ns).filter(p => p.diff === st.diff);
  const done = viewAll.filter(p => !p.live), latest = done.length ? done[done.length - 1] : viewAll[viewAll.length - 1];   // a pull still going is partial: only shown when picked
  const opts = [['latest', latest ? `Latest · ${all ? dayDate(latest.night) + ' · ' : ''}Pull ${latest.seq}` : 'Latest pull'],
    ...viewAll.slice().reverse().map(p => [p.key, pullLabel(p, all)]), ['all', `Overall (${plural(viewAll.length, 'pull')})`]];
  const L = {};
  document.querySelectorAll('select.scope').forEach(el => { const k = el.dataset.s;
    if (st.scope[k] !== 'latest' && st.scope[k] !== 'all' && !viewAll.some(p => p.key === st.scope[k])) st.scope[k] = 'latest';
    el.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}"${v === st.scope[k] ? ' selected' : ''}>${esc(t)}</option>`).join('');
    const sg = document.querySelector(`.seg.mini[data-s="${k}"]`);
    if (sg) sg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === st.scope[k])));
    L[k] = st.scope[k] === 'all' ? viewAll : st.scope[k] === 'latest' ? (latest ? [latest] : []) : viewAll.filter(p => p.key === st.scope[k]); });
  $('sub').textContent = '';
  const kills = viewAll.filter(p => p.kill).length;
  $('diffNote').textContent = `${viewAll.length} ${DIFF[st.diff]} pull${viewAll.length === 1 ? '' : 's'}${kills ? ` · ${plural(kills, 'kill')}` : ''}`;
  renderTimeline(ns);
  renderMap(L.map, L.map.length > 1);
  CFG.modules.forEach(m => renderModule(m, L[m.id]));
  drawAvoid($('avoidT'), L.avoid.flatMap(avoidHits), CFG.avoid.names, CFG.avoid.short || {}, H);
  drawDamage($('dmg'), L.dmg, H); drawHealing($('heal'), L.heal, H); drawDispels($('disp'), L.disp, H); drawDeaths($('deaths'), L.deaths, H);
  document.querySelectorAll('.panel').forEach(linkify);
  sortTables(); sortStd(); setLiveStatus();
}

// ---- last-updated bar: this difficulty's pulls in view, the latest one, and when the uploader last sent data ----
function ago(ms){ const s = Math.round((Date.now() - ms) / 1000); return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; }
function setLiveStatus(){
  const ns = nights(), ps = viewPulls(ns).filter(p => p.diff === st.diff), dot = $('liveDot'), t = $('liveText'), dn = DIFF[st.diff] || '';
  dot.className = 'ldot' + (SITE.err ? ' err' : ps.length ? ' on' : '');
  if (SITE.err){ t.textContent = SITE.err; return; }
  if (!ps.length){ t.textContent = `0 ${dn} ${CFG.name} pulls`; return; }
  const up = (SITE.view === 'all' ? ns : ns.filter(n => n.key === SITE.view)).flatMap(n => n.entries).reduce((m, e) => (e.updated || '') > m ? e.updated : m, '');
  const when = up ? ` · updated ${ago(Date.parse(up))}` : '';
  t.textContent = SITE.view === 'all' ? `All nights · ${ps.length} ${dn} ${CFG.name} pulls${when}` : `${ps.length} ${dn} ${CFG.name} pulls · latest: ${pullLabel(ps[ps.length - 1], false)}${when}`;
}
setInterval(setLiveStatus, 15000);

// ---- click any column heading to sort: high to low first (A to Z for names), click again to flip ----
const SORT = {};   // table id -> {h: heading text, dir: 1 | -1}
const hText = th => (th.querySelector('.lg') || th).textContent.trim();
const blankRow = r => r.classList.contains('tot') || !!r.querySelector('td[colspan]') || [...r.querySelectorAll('td.l, td.what')].some(td => /^(\d+\.\s*)?—$/.test(td.textContent.trim()));
function cellVal(td){
  const t = td ? td.textContent.trim().replace(/^\d+\.\s+/, '') : '';
  if (/^-?\d+(\.\d+)?$/.test(t)) return +t;
  const k = t.match(/^(-?\d+(\.\d+)?)([KMB])$/); if (k) return +k[1] * {K: 1e3, M: 1e6, B: 1e9}[k[3]];
  const m = t.match(/^(\d+):(\d\d)$/); if (m) return +m[1] * 60 + +m[2];
  return t === '' || t === '–' || t === '—' ? null : t.toLowerCase();
}
function sortTables(){   // this page's tables (shared.js sorts the .st ones)
  document.querySelectorAll('table:not(.st)').forEach(t => {
    let rows = [...t.rows]; const hi = rows.findIndex(r => !r.querySelector('th')) - 1, h0 = hi < -1 ? rows.length - 1 : hi;
    if (h0 < 0) return; const hdr = rows[h0]; rows = [hdr, ...rows.slice(h0 + 1)];
    const hs = [...hdr.cells];
    hs.forEach(th => { th.classList.remove('asc', 'desc'); if (hText(th) !== '#') th.classList.add('so'); });
    const st0 = SORT[t.id]; if (!st0) return;
    const ci = hs.findIndex(th => hText(th) === st0.h); if (ci < 0) return;
    hs[ci].classList.add(st0.dir > 0 ? 'asc' : 'desc');
    const data = rows.slice(1).filter(r => !blankRow(r)), blanks = rows.slice(1).filter(blankRow);
    data.sort((a, b) => { const x = cellVal(a.cells[ci]), y = cellVal(b.cells[ci]);
      if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;   // empty cells always last
      return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * st0.dir; });
    const body = rows[0].parentNode; [...data, ...blanks].forEach(r => body.appendChild(r));
  });
}
document.addEventListener('click', e => {
  const th = e.target.closest('th.so'); if (!th || th.closest('table.st')) return;
  const t = th.closest('table'), h = hText(th), cur = SORT[t.id];
  const first = [...t.rows].filter(r => !r.querySelector('th')).map(r => cellVal(r.cells[th.cellIndex])).find(v => v !== null);
  SORT[t.id] = {h, dir: cur && cur.h === h ? -cur.dir : typeof first === 'string' ? 1 : -1};
  sortTables();
});
document.querySelectorAll('select.scope').forEach(el => {
  el.onchange = () => { st.scope[el.dataset.s] = el.value; render(); };
  // a quick Latest / Overall switch next to each dropdown
  const ctl = document.createElement('div'); ctl.className = 'ctl'; el.parentNode.insertBefore(ctl, el);
  const seg = document.createElement('div'); seg.className = 'seg mini'; seg.setAttribute('role', 'group'); seg.dataset.s = el.dataset.s;
  seg.innerHTML = '<button type="button" data-v="latest">Latest</button><button type="button" data-v="all">Overall</button>';
  seg.querySelectorAll('button').forEach(b => b.onclick = () => { st.scope[el.dataset.s] = b.dataset.v; render(); });
  ctl.append(seg, el);
});
$('night').onchange = e => { SITE.view = e.target.value; render(); };

checkSite();
let rsz; addEventListener('resize', () => { clearTimeout(rsz); rsz = setTimeout(render, 150); });
setInterval(checkSite, 5000);
// boss picker in the title: each boss has its own page
(() => { const b = $('boss'), cur = b.value;
  const fit = () => { const m = document.createElement('span'), cs = getComputedStyle(b);
    m.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${cs.font};letter-spacing:${cs.letterSpacing}`;
    m.textContent = b.options[b.selectedIndex].text; document.body.appendChild(m);
    b.style.width = (m.getBoundingClientRect().width + parseFloat(cs.paddingRight) + 2) + 'px'; m.remove(); };
  fit(); if (document.fonts) document.fonts.ready.then(fit);
  b.onchange = () => { location.href = bossUrl(b.value, TEAM.id); };
  const tl = $('teamLink'); tl.textContent = TEAM.name; tl.href = './?team=' + encodeURIComponent(TEAM.id);
  addEventListener('pageshow', () => { b.value = cur; fit(); });
})();
// Wowhead's tooltip script (falls back to its older power.js if the first URL doesn't load)
window.whTooltips = {colorLinks: false, iconizeLinks: false, renameLinks: false};
(() => { const load = (src, next) => { const sc = document.createElement('script'); sc.src = src; sc.async = true; if (next) sc.onerror = next; document.body.appendChild(sc); };
  load('https://wow.zamimg.com/js/tooltips.js', () => load('https://wow.zamimg.com/widgets/power.js')); })();
// clicking Daragnos's name (only the name) turns warlock mode on or off and brings up a "Warlock mode" button beside Light/Dark.
// Nothing is saved: a reload is back to normal with the button hidden (Light/Dark is still remembered).
(() => { const root = document.documentElement, seg = $('wlSeg'), btn = $('wlBtn');
  const toggle = () => { const on = root.dataset.accent !== 'warlock';
    if (on) root.dataset.accent = 'warlock'; else delete root.dataset.accent;
    btn.setAttribute('aria-pressed', String(on)); render(); };
  document.addEventListener('click', e => {
    const el = e.target.closest('.pn'); if (!el || el.textContent.trim() !== 'Daragnos') return;
    seg.hidden = false; toggle(); });
  btn.onclick = toggle;
})();
