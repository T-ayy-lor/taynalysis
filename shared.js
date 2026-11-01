// Teams, bosses and data folders, shared by every page. The uploader keeps the same team ids (TEAMS in taynalysis_uploader.py).
// days: raid nights (0 = Sunday); start / end: raid hours, Eastern (the timelines span them); diff: the difficulty a boss page
// opens on when the team has no pulls yet (14 Normal, 15 Heroic, 16 Mythic). A team's data stays its own whatever the difficulty.
const TEAMS = [
  {id: 'ac-mythic',  name: 'Almost Casual Mythic',  days: [3, 4], start: '20:30', end: '23:30', diff: 16},
  {id: 'ac-heroic',  name: 'Almost Casual Heroic',  days: [2],    start: '20:30', end: '23:30', diff: 15},
  {id: 'ac-weekend', name: 'Almost Casual Weekend', days: [6],    start: '21:30', end: '24:00', diff: 15},
];
const TIER = 'venomous-abyss';                  // Midnight season 2 raid
// in raid order; page = the boss's page file (without .html), only for bosses the site covers
// false: "needs testing" pages can't be opened from the home page or the boss pickers (live site: tested pages only).
// Set it to true to test them; their data still uploads either way.
const SHOW_UNTESTED = false;
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
// the team in the address (?team=...); boss pages opened without one (old links) show Almost Casual Mythic
const TEAM = teamById(new URLSearchParams(location.search).get('team')) || TEAMS[0];
const dataDir = boss => `data/${TEAM.id}/${TIER}/${boss}/`;
const bossUrl = (page, team) => `${page}.html?team=${encodeURIComponent(team)}`;
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
  const rows = Object.keys(P).filter(n => tot(n) > 0).sort((a, b) => tot(b) - tot(a) || H.order(a, b));
  const draw = fmt => { el.innerHTML = `<tr><th class="w">Player</th>${abs.map(a => `<th class="ab" title="${tlEsc(a)}"><span class="lg">${tlEsc(a)}</span><span class="sh">${tlEsc(short[a] || a)}</span></th>`).join('')}<th>Total</th></tr>` +
    (rows.length ? rows.map(n => `<tr><td>${H.pname(n)}</td>${abs.map(a => { const c = P[n][a]; return c ? `<td class="n" title="${c.n} hit${c.n === 1 ? '' : 's'}">${fmt(c.d)}</td>` : '<td class="n z">–</td>'; }).join('')}<td class="n bad">${fmt(tot(n))}</td></tr>`).join('')
      : `<tr><td class="z">—</td><td class="n z">0</td></tr>`); };
  draw(stBig); el.classList.add('st'); el.classList.remove('narrow', 'vert');   // shorter headings, then upright ones and rounder numbers, when it doesn't fit
  if (el.scrollWidth > el.parentNode.clientWidth + 1) el.classList.add('narrow');
  if (el.scrollWidth > el.parentNode.clientWidth + 1){ el.classList.add('vert'); draw(stRound); }
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
    const val = td => { const x = td ? td.textContent.trim() : ''; const k = x.match(/^(-?\d+(\.\d+)?)([KMB])$/); if (k) return +k[1] * {K: 1e3, M: 1e6, B: 1e9}[k[3]];
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

// Draws #tl (one row per night), #scale and #tlHours. nights: [{key, date, cur, logs: [log names],
// pulls: [{key, date, clock, tz, dur, kill, diff, title}]}] oldest first; o: {enc: this boss's encounter ids, name, diff, sel: selected pull key}.
// Each night spans the team's raid hours; trash and other bosses come from ACT.
function drawTimeline(nights, o){
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
  document.getElementById('tlHours').textContent = `${tlClock(ref)}${ap0 !== ap1 ? ' ' + ap0 : ''}–${tlClock(ref + span)}${ap1 ? ' ' + ap1 : ''} your time${zone ? ` (${zone})` : ''}`;
}

// ---- the boss picker in each boss page's title: every boss with a page, this one selected ----
(() => { const b = document.getElementById('boss'); if (!b) return;
  const here = (location.pathname.split('/').pop() || '').replace(/\.html$/, '');
  const cur = BOSSES.some(x => x.page === here) ? here : b.value;
  b.innerHTML = BOSSES.filter(x => x.page && (!x.test || SHOW_UNTESTED || x.page === cur)).map(x => `<option value="${x.page}"${x.page === cur ? ' selected' : ''}>${tlEsc(x.name)}</option>`).join('');
})();

