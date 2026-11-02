// Teams, bosses and data folders, shared by every page. The uploader keeps the same team ids (TEAMS in taynalysis_uploader.py).
// days: raid nights (0 = Sunday); start / end: raid hours, Eastern (the timelines span them); diff: the difficulty a boss page
// opens on when the team has no pulls yet (14 Normal, 15 Heroic, 16 Mythic). A team's data stays its own whatever the difficulty.
// extra: other raid nights with their own hours (AC Mythic's optional Heroic Mondays, 8:30-11 PM; Taylor 2026-11-02).
const TEAMS = [
  {id: 'ac-mythic',  name: 'AC Mythic (W/TR)',      days: [3, 4], start: '20:30', end: '23:30', diff: 16,
   extra: [{days: [1], start: '20:30', end: '23:00'}]},
  {id: 'ac-heroic',  name: 'AC Heroic (T)',         days: [2],    start: '20:30', end: '23:30', diff: 15},
  {id: 'ac-weekend', name: 'AC Weekend (S)',        days: [6],    start: '21:30', end: '24:00', diff: 15},
];
const TIER = 'venomous-abyss';                  // Midnight season 2 raid
// in raid order; page = the boss's page file (without .html), only for bosses the site covers
// false: "needs testing" pages can't be opened from the home page or the boss pickers (live site: tested pages only).
// Set it to true to test them; their data still uploads either way.
const SHOW_UNTESTED = true;   // on since 2026-11-01 (Taylor), once the season's data was loaded
const BOSSES = [   // test: page not checked against a raid night yet (shows "needs testing")
  {id: 'nekzali',            name: "Nek'zali",              page: 'nekzali',            test: true},
  {id: 'entombed-sentinels', name: 'Entombed Sentinels',    page: 'entombed-sentinels', test: true},
  {id: 'vashnik',            name: 'Vashnik the Malignant', page: 'vashnik',            test: true},
  {id: 'lost-explorers',     name: 'The Lost Explorers',    page: 'lost-explorers',     test: true},
  {id: 'sszorak',            name: 'Sszorak',               page: 'sszorak'},
  {id: 'twin-fangs',         name: 'Twin Fangs',            page: 'fangs'},
  {id: 'coiled-altar',       name: 'The Coiled Altar',      page: 'coiled-altar',       test: true},
  {id: 'ulatek',             name: "Ula'tek",               page: 'ulatek',             test: true},
  {id: 'kithix',             name: "Kith'ix",               page: 'kithix',             test: true},   // its own one-boss raid (The Unbinding of Kith'ix), kept with this season's bosses
];
const teamById = id => TEAMS.find(t => t.id === id) || null;
// the team in the address (?team=...); boss pages opened without one (old links) show AC Mythic
const TEAM = teamById(new URLSearchParams(location.search).get('team')) || TEAMS[0];
const dataDir = boss => `data/${TEAM.id}/${TIER}/${boss}/`;
// ---- raid hours: while the team is raiding (its nights and hours, Eastern) boss pages open on the newest night's latest
// pull; any other time they open on every night with Overall everywhere (Taylor 2026-10-31). Decided once, on load.
function inRaidHours(team = TEAM, now = new Date()){
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'})
    .formatToParts(now).map(x => [x.type, x.value]));
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(p.weekday), mins = +p.hour * 60 + +p.minute;
  const toM = s => { const [h, m] = s.split(':').map(Number); return h * 60 + m; };
  // the main nights and any extra ones; hours past midnight (an end after 24:00) count on the next day's early morning
  return [team, ...(team.extra || [])].some(w => { const s = toM(w.start), e = toM(w.end);
    return w.days.some(d => (d === day && mins >= s && mins < Math.min(e, 1440)) || (e > 1440 && (d + 1) % 7 === day && mins < e - 1440)); });
}
const RAIDING = inRaidHours();
const DEF_SCOPE = RAIDING ? 'latest' : 'all';   // what every module's Latest / Overall switch starts on
// the night a boss page opens on: the newest during raid hours, else all nights (when there's more than one)
const startNight = ns => !ns.length ? null : !RAIDING && ns.length > 1 ? 'all' : ns[ns.length - 1].key;

// ---- "ignore events after N deaths": mechanics stop at the Nth raider death of each pull; meters (damage, healing,
// dispels) keep the whole pull. One dropdown beside the difficulty buttons; off (–, the whole pull) by default ----
let DEATH_CUT = 0;   // off by default (Taylor 2026-10-31)
// when the pull's Nth death happened (Infinity when fewer died or the filter is off)
function cutTime(p, n = DEATH_CUT){
  if (!n) return Infinity;
  const t = (p.deaths || []).map(d => d[0]).sort((a, b) => a - b);
  return t.length >= n ? t[n - 1] : Infinity;
}
// one list of a pull trimmed at time T. Rules: 'row' = [t, ...] rows, 'num' = plain times, 'at:i' = rows timed at index i,
// 'obj:f' = objects timed by field f, 'dict:<rule>' = a dict of such lists, or a function (list, T) => list
function cutList(v, rule, T){
  if (v == null) return v;
  if (typeof rule === 'function') return rule(v, T);
  if (rule.startsWith('dict:')){ const r = rule.slice(5); return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cutList(x, r, T)])); }
  if (!Array.isArray(v)) return v;
  if (rule === 'row') return v.filter(r => r[0] <= T);
  if (rule === 'num') return v.filter(x => x <= T);
  if (rule.startsWith('at:')){ const i = +rule.slice(3); return v.filter(r => r[i] <= T); }
  if (rule.startsWith('obj:')){ const f = rule.slice(4); return v.filter(o => o[f] <= T); }
  return v;
}
// a copy of the pull with every timed list in spec cut at the Nth death (the death itself still counts); cut = that time
function cutPull(p, spec, n = DEATH_CUT){
  const T = cutTime(p, n); if (T === Infinity) return p;
  const q = {...p, cut: T};
  Object.entries(spec).forEach(([k, r]) => { if (k in q) q[k] = cutList(q[k], r, T); });
  return q;
}
// the dropdown, right after the difficulty buttons; onChange redraws the page
function initDeathCut(onChange){
  const seg = document.getElementById('diffSeg'); if (!seg || document.getElementById('deathCut')) return;
  const lbl = (seg.previousElementSibling && seg.previousElementSibling.className) || 'lbl', w = document.createElement('span');
  w.className = 'dcut'; w.style.cssText = 'display:inline-flex;align-items:center;gap:6px';
  w.innerHTML = `<span class="${lbl}">Ignore events after</span><select id="deathCut" aria-label="Ignore mechanic events after this many deaths" style="padding:3px 6px;width:auto">` +
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20].map(n => `<option value="${n}"${n === DEATH_CUT ? ' selected' : ''}>${n}</option>`).join('') +
    `<option value="0" title="Off: the whole pull"${DEATH_CUT ? '' : ' selected'}>–</option></select><span class="${lbl}">deaths</span>`;
  w.title = 'Mechanics stop counting at that death in each pull (the death itself counts). Damage, healing and dispels keep the whole pull.';
  seg.after(w);
  w.querySelector('select').onchange = e => { DEATH_CUT = +e.target.value; onChange(); };
}

// ---- cards that end level with the card beside them scroll inside (.scrollfit); .more adds a fade at the bottom while
// there's more below (no visible scroll bars anywhere) ----
(() => {
  const upd = el => el.classList.toggle('more', el.scrollHeight - el.scrollTop - el.clientHeight > 4);
  const all = () => document.querySelectorAll('.scrollfit').forEach(upd);
  addEventListener('scroll', e => { const el = e.target; if (el.classList && el.classList.contains('scrollfit')) upd(el); }, true);
  const ro = new ResizeObserver(all);
  const watch = () => { document.querySelectorAll('.scrollfit').forEach(el => ro.observe(el)); all(); };
  let q = false;
  new MutationObserver(() => { if (!q){ q = true; requestAnimationFrame(() => { q = false; watch(); }); } })
    .observe(document.documentElement, {childList: true, subtree: true});
  addEventListener('load', watch);
})();

// ---- paired mechanic cards (.mods, two per row): a row ends where its shorter card ends and the taller card's table
// scrolls inside, but never under PAIR_MIN (Taylor 2026-11-02: a long table beside a one-row card was squeezed to one
// row); one column (narrow screens): every card at its own height ----
const PAIR_MIN = 480;   // about 12 rows before a paired card scrolls (Taylor: it scrolled at too small a size)
function fitPairs(){
  document.querySelectorAll('.mods').forEach(g => {
    const cards = [...g.children].filter(c => c.classList.contains('panel'));
    cards.forEach(c => { c.style.height = ''; c.classList.remove('fit'); const tw = c.querySelector(':scope > .tw'); if (tw) tw.classList.remove('scrollfit', 'more'); });
    if (getComputedStyle(g).gridTemplateColumns.split(' ').length < 2) return;
    const rows = {};
    cards.filter(c => c.offsetParent !== null).forEach(c => (rows[c.offsetTop] = rows[c.offsetTop] || []).push(c));
    Object.values(rows).forEach(r => { if (r.length < 2) return;
      const hs = r.map(c => c.offsetHeight), h = Math.min(Math.max(...hs), Math.max(PAIR_MIN, Math.min(...hs)));
      r.forEach((c, i) => { c.style.height = h + 'px';
        if (hs[i] > h){ c.classList.add('fit'); const tw = c.querySelector(':scope > .tw'); if (tw) tw.classList.add('scrollfit'); } }); });
  });
}
(() => { let q = false; const later = () => { if (!q){ q = true; requestAnimationFrame(() => { q = false; fitPairs(); }); } };
  new MutationObserver(later).observe(document.documentElement, {childList: true, subtree: true});
  addEventListener('resize', later); addEventListener('load', later); document.fonts && document.fonts.ready.then(later); })();

// ---- Wowhead tooltips (the in-game ability text) on ability names in buttons and table headings: the name is a Wowhead
// link (Wowhead's script shows its tooltip on hover); clicking it still presses the button / sorts the column ----
const whLink = (id, text) => id ? `<a class="ab" href="https://www.wowhead.com/spell=${id}" data-wowhead="spell=${id}" target="_blank" rel="noopener">${text}</a>` : text;
const whRefresh = () => { try { if (window.$WowheadPower && $WowheadPower.refreshLinks) $WowheadPower.refreshLinks(); } catch (e) {} };
document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a.ab'); if (a && a.closest('button, th')) e.preventDefault(); }, true);

// ---- difficulty: picked on the home page, carried in the address (?diff=14|15|16) and kept in sync by every boss page ----
const URL_DIFF = (d => [14, 15, 16].includes(d) ? d : null)(+new URLSearchParams(location.search).get('diff'));
// a boss page's address; diff defaults to the one in this page's address, so the boss pickers keep the difficulty
const bossUrl = (page, team, diff = new URLSearchParams(location.search).get('diff')) =>
  `${page}.html?team=${encodeURIComponent(team)}${diff ? '&diff=' + diff : ''}`;
// a boss page's first difficulty: the address's if this boss has pulls on it, else the newest pull's, else the team's usual one
function startDiff(all){
  if (URL_DIFF && all.some(p => p.diff === URL_DIFF)) return URL_DIFF;
  const last = all[all.length - 1]; return last ? last.diff : TEAM.diff;
}
// after each render: difficulties with no pulls on any night can't be clicked; the address and the team link follow the current one
function syncDiff(all, cur){
  document.querySelectorAll('#diffSeg button').forEach(b => { const d = +b.dataset.d;
    b.disabled = d !== cur && !all.some(p => p.diff === d);
    b.title = b.disabled ? `No ${{14: 'Normal', 15: 'Heroic', 16: 'Mythic'}[d]} pulls yet` : ''; });
  const u = new URL(location.href);
  if (u.searchParams.get('diff') !== String(cur)){ u.searchParams.set('diff', cur); history.replaceState(history.state, '', u); }
  const tl = document.getElementById('teamLink'); if (tl) tl.href = './?team=' + encodeURIComponent(TEAM.id) + '&diff=' + cur;
}
// ---- raid-night timeline (on every boss page), shown in each viewer's own time zone ----
// Logs record the logger's clock. Nights uploaded from Nov 2026 on also carry its UTC offset (tz, hours); older nights
// were all logged in Eastern, so they're read as Eastern.
const EASTERN = 'America/New_York';
function zoneOffset(ms, zone){   // minutes ahead of UTC in that zone at that moment
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: zone, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric'}).formatToParts(new Date(ms)).map(x => [x.type, x.value]));
  return (Date.UTC(+p.year, p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - ms) / 60000;
}
function wallToUtc(date, clock, tz){   // 'M/D/YYYY' + 'HH:MM:SS' on the logger's clock -> UTC milliseconds
  const [m, d, y] = date.split('/').map(Number), [h, mi, s] = clock.split(':').map(Number), naive = Date.UTC(y, m - 1, d, h, mi, s || 0);
  if (tz != null) return naive - tz * 3600000;
  const guess = naive - zoneOffset(naive, EASTERN) * 60000; return naive - zoneOffset(guess, EASTERN) * 60000;
}
const DIFF_NAME = {14: 'Normal', 15: 'Heroic', 16: 'Mythic', 17: 'LFR'};
const tlTime = new Intl.DateTimeFormat([], {hour: 'numeric', minute: '2-digit'});
const tlParts = ms => Object.fromEntries(tlTime.formatToParts(new Date(ms)).map(x => [x.type, x.value]));
const pullTime = (date, clock, tz) => tlTime.format(new Date(wallToUtc(date, clock, tz)));   // e.g. '11:29 PM', viewer's time
const tlClock = ms => { const p = tlParts(ms); return `${p.hour}:${p.minute}`; };
const tlLength = s => { const m = Math.round(s / 60); return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`; };
const tlEsc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
// the team's raid hours: length in seconds, and when that night's raid starts (Eastern) as UTC milliseconds
function raidHours(team = TEAM){
  const sec = s => { const [h, m] = s.split(':').map(Number); return h * 3600 + m * 60; };
  return {span: sec(team.end) - sec(team.start), startOf: date => wallToUtc(date, team.start + ':00', null)};
}

// ---- standard bottom of every boss page: avoidable damage by ability, then Damage beside Healing, Dispels, Early deaths.
// H = the page's helpers: {pname(n) -> name html, order(a, b) -> roster order, healer(n) -> bool}. Tables get class "st". ----
const stBig = v => v >= 1e9 ? (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'K' : String(Math.round(v));
const stRound = v => v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 500 ? Math.round(v / 1e3) + 'K' : String(Math.round(v));
const stMss = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
// hits: [[t, player, ability, amount], ...] already limited to avoidable ones; order: the abilities in column order; short: phone headings
function drawAvoid(el, hits, order, short, H){
  const P = {}, used = new Set();
  hits.forEach(h => { const c = ((P[h[1]] ||= {})[h[2]] ||= {d: 0, n: 0}); c.d += h[3]; c.n++; used.add(h[2]); });
  const abs = order.filter(a => used.has(a)), tot = n => abs.reduce((a, k) => a + ((P[n][k] || {}).d || 0), 0);
  const hitsOf = n => abs.reduce((a, k) => a + ((P[n][k] || {}).n || 0), 0);
  const rows = Object.keys(P).filter(n => tot(n) > 0).sort((a, b) => tot(b) - tot(a) || H.order(a, b));
  // each cell: the damage, then how many hits it took (×N, muted); stacked under the damage when the table is tight.
  // data-v keeps the sort on the damage
  const hc = (n, stack) => `<span style="color:var(--muted);font-weight:400;font-size:.8em;${stack ? 'display:block;line-height:1.1' : 'margin-left:.3em'}">×${n}</span>`;
  const sp = a => H.spell && H.spell(a);   // the ability's spell id: its heading gets the Wowhead tooltip
  const draw = (fmt, stack) => { el.innerHTML = `<tr><th class="w">Player</th>${abs.map(a => `<th class="ab"${sp(a) ? '' : ` title="${tlEsc(a)}"`}><span class="lg">${whLink(sp(a), tlEsc(a))}</span><span class="sh">${whLink(sp(a), tlEsc(short[a] || a))}</span></th>`).join('')}<th>Total</th></tr>` +
    (rows.length ? rows.map(n => `<tr><td>${H.pname(n)}</td>${abs.map(a => { const c = P[n][a]; return c ? `<td class="n" data-v="${c.d}" title="${c.n} hit${c.n === 1 ? '' : 's'}">${fmt(c.d)}${hc(c.n, stack)}</td>` : '<td class="n z">–</td>'; }).join('')}<td class="n bad" data-v="${tot(n)}">${fmt(tot(n))}${hc(hitsOf(n), stack)}</td></tr>`).join('')
      : `<tr><td class="z">—</td><td class="n z">0</td></tr>`); };
  // when it doesn't fit: hit counts under the damage, then shorter headings, then upright ones and rounder numbers
  const over = () => el.scrollWidth > el.parentNode.clientWidth + 1;
  el.classList.add('st'); el.classList.remove('narrow', 'vert'); el.style.fontSize = ''; draw(stBig, false);
  if (over()) draw(stBig, true);
  if (over()) el.classList.add('narrow');
  if (over()){ el.classList.add('vert'); draw(stRound, true); }
  if (over()) el.querySelectorAll('th, td').forEach(c => { c.style.paddingLeft = '3px'; });   // then tighter cells
  for (let fs = .74; over() && fs > .54; fs -= .04) el.style.fontSize = fs + 'rem';             // then smaller text, until it fits
  // measured before the page's font arrived: check again once it has
  if (document.fonts && document.fonts.status !== 'loaded') document.fonts.ready.then(() => { if (over()) drawAvoid(el, hits, order, short, H); });
  whRefresh();
}
// a bar meter; rows: [[name, value, text]] best first
function drawBars(el, rows, H, short){
  const top = Math.max(rows.length ? rows[0][1] : 0, 1);
  el.className = 'sm' + (short ? ' short' : '');
  el.innerHTML = rows.map(([n, v, txt, tip], i) => `<div class="smr" style="--cc:${(H.color || (() => '#8a8a8a'))(n)}"${tip ? ` title="${tlEsc(tip)}"` : ''}><span class="bar" style="width:${v / top * 100}%"></span><span class="r">${i + 1}</span><span>${H.pname(n)}</span><span class="v${v ? '' : ' z'}">${txt}</span></div>`).join('');
  if (short){   // short meters scroll; a fade at the bottom says there's more
    const more = () => el.classList.toggle('more', el.scrollHeight - el.scrollTop - el.clientHeight > 2);
    el.onscroll = more; more();
  }
}
const perSec = (list, key) => { const tot = {}, dur = {}; list.forEach(p => Object.entries(p[key] || {}).forEach(([n, v]) => { tot[n] = (tot[n] || 0) + v; dur[n] = (dur[n] || 0) + p.dur; })); return {tot, dur}; };
function drawDamage(el, list, H){
  const {tot, dur} = perSec(list, 'dmg'); H.people(list).forEach(n => { tot[n] ||= 0; });
  drawBars(el, Object.entries(tot).sort((a, b) => b[1] - a[1] || H.order(a[0], b[0])).map(([n, v]) => [n, v, `${stBig(dur[n] ? v / dur[n] : 0)}/s <span>${stBig(v)}</span>`]), H, false);
}
function drawHealing(el, list, H){   // everyone who healed, healers first by amount; short, scrolls
  const {tot, dur} = perSec(list, 'heal'); H.people(list).filter(H.healer).forEach(n => { tot[n] ||= 0; });
  drawBars(el, Object.entries(tot).filter(([n, v]) => v > 0 || H.healer(n)).sort((a, b) => b[1] - a[1] || H.order(a[0], b[0])).map(([n, v]) => [n, v, `${stBig(dur[n] ? v / dur[n] : 0)}/s <span>${stBig(v)}</span>`]), H, true);
}
function drawDispels(el, list, H){   // p.dispels: [t, dispeller, target, debuff]; hover a row for which debuffs
  const P = {}; H.people(list).filter(H.healer).forEach(n => { P[n] = {n: 0, what: {}}; });
  list.forEach(p => (p.dispels || []).forEach(d => { const r = (P[d[1]] ||= {n: 0, what: {}}); r.n++; r.what[d[3] || '?'] = (r.what[d[3] || '?'] || 0) + 1; }));
  drawBars(el, Object.entries(P).sort((a, b) => b[1].n - a[1].n || H.order(a[0], b[0])).map(([n, r]) => [n, r.n, String(r.n), Object.entries(r.what).map(([k, v]) => `${k} ×${v}`).join(', ')]), H, true);
}
function drawDeaths(el, list, H){   // p.deaths: [t, player, last hit by, back up at]; early = the first 3 of a pull
  const N = 3; el.classList.add('st');
  if (list.length <= 1){
    const ds = list.length ? (list[0].deaths || []).slice(0, N) : [];   // nights from before deaths were saved have none
    el.innerHTML = `<tr><th>#</th><th class="l w">Player</th><th class="l">Last hit by</th><th>Time</th></tr>` +
      ds.map((d, i) => `<tr><td class="n z">${i + 1}</td><td class="l">${H.pname(d[1])}</td><td class="l${d[2] ? '' : ' z'}">${tlEsc(d[2] || '–')}</td><td class="n">${stMss(d[0])}</td></tr>`).join('') +
      Array.from({length: N - ds.length}, (_, i) => `<tr><td class="n z">${ds.length + i + 1}</td><td class="l z">—</td><td class="l z">–</td><td class="n z">–</td></tr>`).join('');
    return;
  }
  const P = {}; list.forEach(p => (p.deaths || []).slice(0, N).forEach(d => { P[d[1]] = (P[d[1]] || 0) + 1; }));
  const rows = Object.entries(P).sort((a, b) => b[1] - a[1] || H.order(a[0], b[0]));
  el.innerHTML = `<tr><th class="w">Player</th><th>Early deaths</th></tr>` + rows.map(([n, c]) => `<tr><td>${H.pname(n)}</td><td class="n">${c}</td></tr>`).join('') +
    Array.from({length: Math.max(0, N - rows.length)}, () => `<tr><td class="z">—</td><td class="n z">0</td></tr>`).join('');
}
// column sorting for these tables (every heading but #): high to low first, A to Z for names; click again to flip
const ST_SORT = {};
function sortStd(){
  document.querySelectorAll('table.st').forEach(t => {
    const rows = [...t.rows]; if (!rows.length) return; const hs = [...rows[0].cells], key = t.id;
    hs.forEach(th => { th.classList.remove('asc', 'desc'); if (th.textContent.trim() !== '#') th.classList.add('so'); });
    const s = ST_SORT[key]; if (!s) return;
    const ci = hs.findIndex(th => (th.querySelector('.lg') || th).textContent.trim() === s.h); if (ci < 0) return;
    hs[ci].classList.add(s.dir > 0 ? 'asc' : 'desc');
    const val = td => { if (td && td.dataset.v != null) return +td.dataset.v; const x = td ? td.textContent.trim() : ''; const k = x.match(/^(-?\d+(\.\d+)?)([KMB])$/); if (k) return +k[1] * {K: 1e3, M: 1e6, B: 1e9}[k[3]];
      const m = x.match(/^(\d+):(\d\d)$/); if (m) return +m[1] * 60 + +m[2]; return /^-?\d+(\.\d+)?$/.test(x) ? +x : x === '' || x === '–' || x === '—' ? null : x.toLowerCase(); };
    const isBlank = r => [...r.cells].some(c => c.textContent.trim() === '—');   // placeholder rows stay at the bottom
    const data = rows.slice(1).filter(r => !isBlank(r)), blank = rows.slice(1).filter(isBlank);
    data.sort((a, b) => { const x = val(a.cells[ci]), y = val(b.cells[ci]); if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
      return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * s.dir; });
    [...data, ...blank].forEach(r => rows[0].parentNode.appendChild(r));
  });
}
document.addEventListener('click', e => {
  const th = e.target.closest('table.st th.so'); if (!th) return;
  const t = th.closest('table'), h = (th.querySelector('.lg') || th).textContent.trim(), cur = ST_SORT[t.id];
  const first = [...t.rows].slice(1).map(r => r.cells[th.cellIndex] && r.cells[th.cellIndex].textContent.trim()).find(x => x && x !== '–' && x !== '—');
  ST_SORT[t.id] = {h, dir: cur && cur.h === h ? -cur.dir : first && !/^[\d.:]+[KMB]?$/.test(first) ? 1 : -1};
  sortStd();
});

// every raid boss pull (any boss) and trash fight of each log, from data/<team>/<tier>/activity/ (logs from Nov 2026 on)
const ACT = {};
async function checkActivity(){   // true when something new arrived
  let changed = false;
  try {
    const r = await fetch(`data/${TEAM.id}/${TIER}/activity/nights.json?t=${Date.now()}`, {cache: 'no-store'}); if (!r.ok) return false;
    const idx = await r.json();
    await Promise.all((idx.nights || []).map(async e => { if (ACT[e.log] && ACT[e.log].updated === e.updated) return;
      const f = await fetch(e.file + '?t=' + Date.now(), {cache: 'no-store'}); if (f.ok){ ACT[e.log] = await f.json(); changed = true; } }));
  } catch (e) {}
  return changed;
}

// Draws #tl (one row per night), #scale and #tlHours (if the page has it). nights: [{key, date, cur, logs: [log names],
// pulls: [{key, date, clock, tz, dur, kill, diff, title}]}] oldest first; o: {enc: this boss's encounter ids, name, diff, sel: selected pull key}.
// Each night spans the team's raid hours; trash and other bosses come from ACT.
function drawTimeline(nights, o){
  // All nights: only the nights this boss was pulled on the chosen difficulty (Taylor 2026-11-02); one night always shows
  if (nights.length > 1) nights = nights.filter(n => n.pulls.some(p => p.diff === o.diff));
  const H = raidHours(), span = H.span * 1000, today = new Date();
  const ref = nights.length ? H.startOf(nights[nights.length - 1].date) : H.startOf(`${today.getMonth() + 1}/${today.getDate()}/${today.getFullYear()}`);
  const n30 = Math.round(H.span / 1800), ticks = Array.from({length: n30 - 1}, (_, i) => `<i style="left:${(i + 1) / n30 * 100}%"></i>`).join('');
  const box = (w0, a, d) => { const l = Math.max(0, Math.min(1, (a - w0) / span)), w = Math.max(0, Math.min(1 - l, d / span)); return `left:${l * 100}%;width:${w * 100}%`; };
  document.getElementById('tl').innerHTML = nights.length ? nights.slice().reverse().map(n => {
    const w0 = H.startOf(n.date), acts = n.logs.map(l => ACT[l]).filter(Boolean);
    const trash = acts.flatMap(a => a.trash || []).map(([a, b]) => `<span class="tr" style="${box(w0, a * 1000, (b - a) * 1000)}"></span>`).join('');
    const others = acts.flatMap(a => a.pulls || []).filter(p => !o.enc.includes(String(p[2]))).map(p =>
      `<span class="blk ob" style="${box(w0, p[0] * 1000, p[1] * 1000)}" title="${tlEsc(`${p[3]} · ${DIFF_NAME[p[4]] || ''} · ${tlClock(p[0] * 1000)} · ${p[6] ? 'in progress' : p[5] ? 'kill' : 'wipe'}`)}"></span>`).join('');
    const mine = n.pulls.filter(p => p.diff === o.diff), on = mine.reduce((a, p) => a + p.dur, 0), kills = mine.filter(p => p.kill).length;
    // this boss on another difficulty the same night is rare: it's drawn like any other boss
    const blocks = n.pulls.map(p => { const a = wallToUtc(p.date, p.clock, p.tz), tip = tlEsc(`${DIFF_NAME[p.diff] || ''} · ${tlClock(a)} · ${p.title}`);
      return p.diff !== o.diff ? `<span class="blk ob" style="${box(w0, a, p.dur * 1000)}" title="${tlEsc(o.name)} · ${tip}"></span>`
        : `<span class="blk${p.kill ? ' kill' : ''}${p.key === o.sel ? ' sel' : ''}" data-k="${tlEsc(p.key)}" data-d="${p.diff}" style="${box(w0, a, p.dur * 1000)}" title="${tip}"></span>`; }).join('');
    const d = new Date(Date.UTC(...(([m, dd, y]) => [y, m - 1, dd])(n.date.split('/').map(Number)))).toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC'});
    return `<div class="tlrow${n.cur ? ' cur' : ''}" data-n="${n.key}"><span class="d">${tlEsc(d)}</span><div class="track">${ticks}${trash}${others}${blocks}</div>` +
      `<span class="s">${tlLength(on)} · ${Math.round(on / H.span * 100)}%<br>${mine.length} pull${mine.length === 1 ? '' : 's'}${kills ? ' · kill' : ''}</span></div>`; }).join('')
    : `<div class="tlrow"><span class="d" style="color:var(--muted)">—</span><div class="track">${ticks}</div><span class="s">0m · 0%<br>0 pulls</span></div>`;
  // labels in the viewer's own time, from the newest night shown
  document.getElementById('scale').innerHTML = Array.from({length: n30 + 1}, (_, i) =>
    `<span style="left:${i / n30 * 100}%${i === 0 ? ';transform:none' : i === n30 ? ';transform:translateX(-100%)' : ''}">${tlClock(ref + i * 1800000)}</span>`).join('');
  const p0 = tlParts(ref), p1 = tlParts(ref + span), ap0 = p0.dayPeriod || '', ap1 = p1.dayPeriod || '';
  const zone = (new Intl.DateTimeFormat([], {timeZoneName: 'short'}).formatToParts(new Date(ref)).find(x => x.type === 'timeZoneName') || {}).value || '';
  const key = document.getElementById('tlKey');
  if (key) key.innerHTML = '<span><i></i>This boss</span><span><i class="k"></i>Kill</span><span><i class="ob"></i>Other bosses</span><span><i class="tr"></i>Trash</span>';
  const hours = document.getElementById('tlHours');   // pages dropped the hours line (2026-10-31); kept for any page that has it
  if (hours) hours.textContent = `${tlClock(ref)}${ap0 !== ap1 ? ' ' + ap0 : ''}–${tlClock(ref + span)}${ap1 ? ' ' + ap1 : ''} your time${zone ? ` (${zone})` : ''}`;
}

// ---- the boss picker in each boss page's title: every boss with a page, this one selected ----
(() => { const b = document.getElementById('boss'); if (!b) return;
  const here = (location.pathname.split('/').pop() || '').replace(/\.html$/, '');
  const cur = BOSSES.some(x => x.page === here) ? here : b.value;
  b.innerHTML = BOSSES.filter(x => x.page && (!x.test || SHOW_UNTESTED || x.page === cur)).map(x => `<option value="${x.page}"${x.page === cur ? ' selected' : ''}>${tlEsc(x.name)}</option>`).join('');
})();


// ---- group picks (Twin Fangs feast groups and kick order; Taylor 2026-11-02) ----
// data/<team>/<tier>/<boss>/groups-<set>.json = {changes: [{at: UTC seconds (0 = from the start), groups: {player: value},
// diffs?: [difficulties it applies to]}]}. Written by the page's Edit groups (Save: counts from that moment on (the server's PUT /api/groups/<team>/<tier>/
// <boss>/<set>, with the editor key, which can save group picks and nothing else)). A pull uses the last change made before it started, so a change never
// rewrites the pulls before it.
const EDITOR_KEY = 'tay-editor-key';
async function loadGroups(boss, set){
  try { const r = await fetch(dataDir(boss) + `groups-${set}.json?t=` + Date.now(), {cache: 'no-store'}); if (!r.ok) return null;
    const j = await r.json(); return Array.isArray(j.changes) ? j.changes.slice().sort((a, b) => a.at - b.at) : null; } catch (e) { return null; }
}
function groupsAt(changes, utcMs, diff){   // the groups in force at that moment on that difficulty, or null when none was saved yet
  let g = null; (changes || []).forEach(c => { if (c.at * 1000 <= utcMs && (!c.diffs || diff == null || c.diffs.includes(diff))) g = c.groups; }); return g;
}
const pullUtc = p => wallToUtc(p.date, p.clock, p.tz);
(() => { const s = document.createElement('style'); s.textContent = `
details.grped{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
details.grped summary{cursor:pointer;color:var(--muted);font:600 .85rem var(--f-body)}
.grped .gg{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:4px 12px;margin:10px 0}
.grped label{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:.85rem;min-width:0}
.grped label span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.grped select,.grped input{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;font:inherit;font-size:.85rem;padding:2px 4px}
.grped .gact{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.grped button{background:none;color:var(--ink);border:1px solid var(--line);border-radius:6px;font:600 .8rem var(--f-body);padding:5px 10px;cursor:pointer}
.grped button.pri{border-color:var(--accent);color:var(--accent)}
.grped .gmsg{font-size:.8rem;color:var(--muted)} .grped .gmsg.bad{color:var(--crit)}
.grped .ghist{font-size:.78rem;color:var(--muted);margin-top:6px}`; document.head.appendChild(s); })();
// el: a box under the card's table. o: {boss, set, options ([value, label] per choice), names (players to list), current
// (values now), changes (history), onSaved(changes)}. While it's open the page's re-renders leave it alone (picks stay).
function groupEditor(el, o){
  if (!isEditor()){ el.innerHTML = ''; return; }   // admin mode only (the title row's Admin key)
  const open = el.querySelector('details.grped') && el.querySelector('details.grped').open;
  if (open) return;
  const cur = o.current || {}, names = [...new Set([...o.names, ...Object.keys(cur)])];
  const opt = n => `<option value="">–</option>` + o.options.map(([v, l]) => `<option value="${v}"${String(cur[n]) === String(v) ? ' selected' : ''}>${l}</option>`).join('');
  const fmt = c => c.at ? new Date(c.at * 1000).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : 'from the start';
  const histText = ch => { const h = (ch || []).slice(-4).reverse().map(fmt).join(' · ');
    return h ? `Saved changes (newest first): ${h}. Each pull uses the groups saved before it started.` : 'Nothing saved yet. Saved groups count for pulls that start after you save; earlier pulls keep theirs.'; };
  el.innerHTML = `<details class="grped"${open ? ' open' : ''}><summary>${o.title || 'Edit groups'}</summary>
    <div class="gg">${names.map(n => `<label><span>${typeof pname === 'function' ? pname(n) : n}</span><select data-n="${n.replace(/"/g, '&quot;')}" aria-label="${o.label || 'Group'} for ${n.replace(/"/g, '&quot;')}">${opt(n)}</select></label>`).join('')}</div>
    <div class="gact"><button type="button" class="pri" data-from="now">Save</button>
      <span class="gmsg"></span></div>
    <div class="ghist">${histText(o.changes)}</div></details>`;
  const msg = (t, bad) => { const m = el.querySelector('.gmsg'); m.textContent = t; m.classList.toggle('bad', !!bad); };
  el.querySelectorAll('button[data-from]').forEach(b => b.onclick = async () => {
    const groups = {}; el.querySelectorAll('select[data-n]').forEach(s => { if (s.value) groups[s.dataset.n] = /^\d+$/.test(s.value) ? +s.value : s.value; });
    const k = adminKey();
    msg('Saving…');
    try {
      const r = await fetch(`api/groups/${TEAM.id}/${TIER}/${o.boss}/${o.set}`, {method: 'PUT', headers: {'Authorization': 'Bearer ' + k, 'Content-Type': 'application/json'},
        body: JSON.stringify({groups, from: b.dataset.from})});
      const j = await r.json().catch(() => ({}));
      if (r.status === 401){ msg('The server didn\'t accept the admin key: sign out (title row) and save it again.', true); return; }
      if (!r.ok || !j.ok){ msg(j.error || `Couldn't save (${r.status}).`, true); return; }
      msg('Saved.');
      const ch = j.changes.slice().sort((a, c) => a.at - c.at); el.querySelector('.ghist').textContent = histText(ch);
      o.onSaved && o.onSaved(ch);
    } catch (e) { msg('Couldn\'t reach the server.', true); }
  });
}

// ---- LOCAL PREVIEW ONLY (localhost): reload when the site's files change (Taylor 2026-11-02: "i cant see ur changes bc i
// havent refreshed" ... "this is for the local server only"). Every 2 s, while the tab is visible, ask the preview server
// (HEAD, nothing downloaded) whether this page or its own scripts / styles changed; reload when one did. Waits while an
// "Edit groups" box is open, so picks being made aren't lost. The live site never runs this. ----
(() => {
  if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return;
  const files = () => [location.pathname, ...[...document.scripts].map(s => s.src), ...[...document.querySelectorAll('link[rel="stylesheet"]')].map(l => l.href)]
    .filter(u => u).map(u => new URL(u, location.href)).filter(u => u.origin === location.origin).map(u => u.pathname);
  const stamp = async path => { try { const r = await fetch(path + '?v=' + Date.now(), {method: 'HEAD', cache: 'no-store'}); if (!r.ok) return null;
    return r.headers.get('etag') || r.headers.get('last-modified') || r.headers.get('content-length'); } catch (e) { return null; } };
  const seen = {};
  let pending = false;
  const busy = () => document.querySelector('details.grped[open]') || (document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) && document.activeElement.closest('details.grped'));
  async function check(){
    if (document.hidden) return;
    if (pending){ if (!busy()) location.reload(); return; }
    for (const f of [...new Set(files())]){
      const s = await stamp(f); if (s == null) continue;
      if (!(f in seen)){ seen[f] = s; continue; }
      if (s !== seen[f]){ pending = true; if (!busy()) location.reload(); return; }
    }
  }
  addEventListener('load', () => { check(); setInterval(check, 2000); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
})();

// ---- flagged pulls (Taylor 2026-11-02: "flagging a specific pull for data errors ... hides it from users"): data/<team>/<tier>/
// <boss>/groups-flags.json = {flags: {"<log name>#<pull n>": {at, note}}}, saved by the server's PUT /api/flags/... with the
// editor key. A flagged pull is left out of everything on the boss page, after the pulls are numbered (numbers don't shift);
// in admin mode (the title row's Admin key saved) it's still shown, marked, and its ⚑ button beside a pull dropdown unflags it. ----
const FLAGS = {map: {}, raw: null};
const flagKey = (D, p) => `${D.log}#${p.n}`;
const adminKey = () => { try { return localStorage.getItem(EDITOR_KEY) || ''; } catch (e) { return ''; } };
const isEditor = () => !!adminKey();   // admin mode: an admin (editor) key saved in this browser, from the title row's field
const isFlagged = fk => !!FLAGS.map[fk];
const showPull = fk => !isFlagged(fk) || isEditor();
const flagTag = fk => isFlagged(fk) ? ' · ⚑ flagged' : '';
async function refreshFlags(slug){   // true when the flags changed
  try { const r = await fetch(dataDir(slug) + 'groups-flags.json?t=' + Date.now(), {cache: 'no-store'});
    const j = r.ok ? await r.json() : {flags: {}}, raw = JSON.stringify(j.flags || {});
    if (raw !== FLAGS.raw){ FLAGS.raw = raw; FLAGS.map = j.flags || {}; return true; } } catch (e) {}
  return false;
}
(() => { const s = document.createElement('style'); s.textContent = `
.adminbox{display:inline-flex;align-items:center;gap:6px;font:500 .78rem var(--f-body);color:var(--muted);margin-left:4px}
.adminbox input{width:120px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;font:inherit;padding:4px 6px}
.adminbox button{background:none;color:var(--muted);border:1px solid var(--line);border-radius:6px;font:600 .74rem var(--f-body);padding:4px 8px;cursor:pointer}
.adminbox button:hover{color:var(--ink)} .adminbox .on{color:var(--accent);font-weight:600} .adminbox .bad{color:var(--crit)}
button.ficon{flex:none;background:none;border:1px solid var(--line);border-radius:6px;color:var(--muted);font-size:.85rem;line-height:1;padding:5px 7px;cursor:pointer}
button.ficon:hover{color:var(--ink)} button.ficon[aria-pressed="true"]{color:var(--crit);border-color:var(--crit)}`; document.head.appendChild(s); })();
// the title row's "Admin key" field (after the night picker): saving a key there turns on admin mode in this browser (flag
// icons beside the pull dropdowns, Edit groups); the server checks it (GET /api/editor-check) before it's kept
function adminBox(){
  const night = document.getElementById('night'); if (!night || document.getElementById('adminbox')) return;
  const box = document.createElement('span'); box.id = 'adminbox'; box.className = 'adminbox'; night.after(box);
  const draw = msg => {
    box.innerHTML = isEditor() ? `<span class="on">Admin</span><button type="button" data-a="out" title="Forget the admin key in this browser">Sign out</button>`
      : `<label for="adminkey">Admin key:</label><input type="password" id="adminkey" autocomplete="current-password"><button type="button" data-a="in">Save</button>${msg ? `<span class="bad">${msg}</span>` : ''}`;
    const inp = box.querySelector('input'); if (inp) inp.onkeydown = e => { if (e.key === 'Enter') save(); };
    box.querySelector('button').onclick = () => isEditor() ? (localStorage.removeItem(EDITOR_KEY), location.reload()) : save();
  };
  const save = async () => {
    const k = (box.querySelector('input').value || '').trim(); if (!k) return;
    try { const r = await fetch('api/editor-check', {headers: {'Authorization': 'Bearer ' + k}, cache: 'no-store'});
      if (r.status === 401){ draw('not accepted'); return; } } catch (e) {}   // no answer (server not updated yet): keep it anyway
    try { localStorage.setItem(EDITOR_KEY, k); } catch (e) { draw('your browser blocked saving it'); return; }
    location.reload();
  };
  draw();
}
addEventListener('DOMContentLoaded', adminBox); if (document.readyState !== 'loading') adminBox();
// a ⚑ button beside each pull dropdown, in admin mode only, while that dropdown has a single pull picked (not Latest /
// Overall): pressed = flagged; a click flags / unflags it. resolve(select) -> the picked pull's flag key, or null.
function flagIcons(slug, selects, resolve, rerender){
  selects.forEach(sel => {
    if (!sel) return;
    let b = sel.nextElementSibling && sel.nextElementSibling.classList.contains('ficon') ? sel.nextElementSibling : null;
    const fk = isEditor() ? resolve(sel) : null;
    if (!fk){ if (b) b.remove(); }
    else {
      if (!b){ b = document.createElement('button'); b.type = 'button'; b.className = 'ficon'; sel.after(b); }
      const on = isFlagged(fk);
      b.textContent = '⚑'; b.setAttribute('aria-pressed', String(on)); b.dataset.fk = fk;
      b.title = on ? `Flagged: hidden from everyone but you${FLAGS.map[fk].note ? ' (' + FLAGS.map[fk].note + ')' : ''}. Click to unflag.` : 'Flag this pull as bad data: hides it from everyone but you';
      b.onclick = async () => {
        b.disabled = true;
        try {
          const r = await fetch(`api/flags/${TEAM.id}/${TIER}/${slug}`, {method: 'PUT', headers: {'Authorization': 'Bearer ' + adminKey(), 'Content-Type': 'application/json'},
            body: JSON.stringify({pull: b.dataset.fk, flagged: !isFlagged(b.dataset.fk), note: ''})});
          const j = await r.json().catch(() => ({}));
          if (r.ok && j.ok){ FLAGS.map = j.flags || {}; FLAGS.raw = JSON.stringify(FLAGS.map); rerender(); }
          else b.title = r.status === 401 ? 'The server did not accept the admin key: sign out and save it again' : (j.error || `Could not save (${r.status})`);
        } catch (e) { b.title = 'Could not reach the server'; }
        b.disabled = false;
      };
    }
    if (!sel.dataset.ficon){ sel.dataset.ficon = '1'; sel.addEventListener('change', () => flagIcons(slug, [sel], resolve, rerender)); }
  });
}
