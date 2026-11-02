#!/usr/bin/env python3
"""
Taynalysis uploader: watches your WoW combat log and, after every pull or wipe, sends the night's data
to taynalysis.com (or, with --github, to the GitHub Pages copy of the site). Open pages pick it up on their own.
On start it asks which team the raid is for (or give --team ac-mythic / ac-heroic / ac-weekend, or 1 / 2 / 3).
Each team's data goes in its own folder, data/<team>/<raid>/<boss>/:
  Sszorak: positions, Virulence casts, explosions, damage, Tempest -> .../sszorak/<log name>.json, listed in nights.json there
  Twin Fangs: positions, Eternal Venom, globules, Coiling Ichor, kicks, Ravenous Feast soaks, damage, healing, deaths
              -> .../twin-fangs/<log name>.json, listed in nights.json there; past Mythic kick orders in setup.txt there; groups set on the page in groups-<set>.json
  Kith'ix (any difficulty): boss casts, chosen debuffs, avoidable hits, adds, Abyssal Grasp healing, damage, healing, deaths
              -> .../kithix/<log name>.json, listed in nights.json there
  Nek'zali, Entombed Sentinels, Vashnik, The Lost Explorers, The Coiled Altar, Ula'tek (FAIL_BOSSES, any difficulty):
              casts, kicks, chosen debuffs, avoidable and failure hits, raid-wide failure hits, adds, damage, healing,
              dispels, deaths -> .../<boss>/<log name>.json, listed in nights.json there
"""
import base64, codecs, csv, getpass, glob, json, os, re, select, ssl, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

# ---- settings ----
LOG_DIR = '/Applications/World of Warcraft/_retail_/Logs'
REPO = 't-ayy-lor/taynalysis'      # owner/repository of your GitHub Pages site
BRANCH = 'main'
TEAMS = [('ac-mythic', 'AC Mythic (W/TR)'), ('ac-heroic', 'AC Heroic (T)'), ('ac-weekend', 'AC Weekend (S)')]   # same ids as shared.js
TIER = 'venomous-abyss'                    # this raid's folder name, also in shared.js
# set by set_team(): Sszorak nights folder and its list of nights (read by the page's night picker), same for Twin Fangs
NIGHT_DIR = INDEX_PATH = FANGS_DIR = FANGS_INDEX = SETUP_PATH = KITH_DIR = KITH_INDEX = ACT_DIR = ACT_INDEX = None
TOKEN_FILE = os.path.expanduser('~/.taynalysis_uploader_token')
POLL_SECONDS = 3
MIN_UPLOAD_GAP = 360        # seconds between uploads to GitHub: GitHub Pages has a soft limit of 10 site builds per hour
SITE_URL = 'https://taynalysis.com'                                 # your own server: no upload limit
SERVER_TOKEN_FILE = os.path.expanduser('~/.taynalysis_server_token')  # upload token from the server setup (outside the repo)
SYNC_FILE = os.path.expanduser('~/.taynalysis_sync.json')            # which page files were last sent to the server
PAGE_EXT = {'.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.woff2'}   # page files, sent when they change
DATA_EXT = {'.json', '.txt'}                                         # raid data and setup files, sent by --sync-site
# files the boss pages save on the server with the editor key (data/<team>/<tier>/<page>/groups-<set>.json): group picks
# (Twin Fangs) and pulls flagged with bad data (every boss); site_files never sends them, backup_groups copies them home
GROUP_FILES = {'twin-fangs': ('feast', 'kicks')}
def group_files():
    """page folder -> its saved files: every boss page can have flagged pulls."""
    pages = ['sszorak', 'twin-fangs', 'kithix'] + [b['slug'] for b in FAIL_BOSSES]
    return {pg: GROUP_FILES.get(pg, ()) + ('flags',) for pg in pages}
is_groups_file = lambda f: f.startswith('groups-') and f.endswith('.json')

ENC, BOSS_NPC = '3420', '-257347-'           # Sszorak encounter ID and creature ID
VIR_A, VIR_B, BOOM = '1297707', '1299899', '1300089'
TEMPEST, TEMPEST_CAST = '1287083', '1287072'   # Tempest hit/debuff and the boss's Tempest cast
SURGE_CAST, SURGE_DEBUFF = '1305959', '1305963'   # Venomous Surge: the boss's cast, and the debuff on 2 ranged players
SURGE_LEN = 9.5                                # a debuff that lasted at least this long ran out on its own (it lasts 10 s)
TELEPORT = '48020'                             # Demonic Circle: Teleport, counted per player per pull
# Heroic (and every difficulty) mechanics, checked in Taylor's Heroic logs Aug-Oct 2026:
# Raging Crosswinds arrow on a player (its spell = the direction): Heroic / Normal use two, Mythic all four (1297111 and 1297096
# found 2026-11-02 in Oct 22's log, ~150 each that night; before that Mythic sets showed only half their arrows)
SSZ_AURAS = {'1285425': 'cross', '1285453': 'cross', '1297111': 'cross', '1297096': 'cross',
             '1285447': 'gust'}                # Turbulent Gusts: in the air after the launch; two players' end together = they collided
DMG_STEP = 5                                   # seconds per damage window in each pull's timeline
# avoidable hits (Wowhead guide + journal): tornadoes, the tank's frontal on anyone else, claw impacts, residue pools,
# someone's Raging Crosswinds blowing up next to you (1285616; not the DoT), the Mythic charge. The page drops tanks for Ravage.
AVOID_S = {'1287083': 'Tempest', '1277101': 'Ravage', '1305998': 'Caustic Claws', '1296667': 'Caustic Residue',
           '1285616': 'Raging Crosswinds burst', '1299592': 'To the Slaughter'}


TZ_RE = re.compile(r'\S+ \d+:\d+:\d+\.\d+([+-]\d+(?:\.\d+)?)\s')
def log_tz(line):
    """The logger's UTC offset in hours, from a line's timestamp ('10/29/2026 20:38:51.444-4  ...' -> -4.0), or None.
    Saved with every night so the pages can show the logger's clock times in each viewer's own time zone."""
    m = TZ_RE.match(line)
    return float(m.group(1)) if m else None


class Parser:
    """Reads combat-log text and keeps only Sszorak encounters."""
    def __init__(self):
        self.mark = 'ENCOUNTER_START,' + ENC + ','
        self.tz = None             # the logger's UTC offset (hours)
        self.pulls, self.cur, self.leftover = [], None, ''
        self.names, self.pets, self.specs = {}, {}, {}
        self.finished = 0          # count of pulls that have ended

    @staticmethod
    def split(s):
        return s.split(',') if '"' not in s else next(csv.reader([s]))

    _days = {}
    @classmethod
    def ts(cls, line):
        d, rest = line.split(' ', 1)
        base = cls._days.get(d)
        if base is None:
            m, dd, y = d.split('/')
            base = cls._days[d] = datetime(int(y), int(m), int(dd), tzinfo=timezone.utc).timestamp()
        h, mi, sec = rest[:rest.index('  ')].split(':')
        sec = float(sec.split('-')[0].split('+')[0])
        return base + int(h) * 3600 + int(mi) * 60 + sec

    def start(self, line, rest):
        r = self.split(rest)
        self.tz = log_tz(line)
        clock = line.split(' ', 1)[1][:8].split('.')[0]
        self.cur = dict(n=len(self.pulls) + 1, t0=self.ts(line), clock=clock, date=line.split(' ', 1)[0],
                        diff=int(r[3]), size=int(r[4]), ended=False, kill=False, dur=0.0, lastT=0.0,
                        track={}, specGuid={}, aura=[], boom=[], died={}, bossGuid=None, boss=[], bossHp=None, dmg={},
                        tempest={}, tempestCasts=0, now=0.0, ports={}, surgeCasts=[], sur=[], sdeb={},
                        heal={}, dispels=[], avoid=[], lasthit={}, hau=[], hauOpen={})
        self.pulls.append(self.cur)

    def end(self, rest):
        c = self.cur
        if c is None: return
        if rest:
            r = rest.split(',')
            c['kill'] = r[5] == '1'
            try: c['dur'] = int(r[6]) / 1000 or c['lastT']
            except ValueError: c['dur'] = c['lastT']
        else:
            c['dur'] = c['lastT']
        c['ended'] = True
        self.cur = None
        self.finished += 1

    @staticmethod
    def add_pt(arr, t, x, y):
        if arr and t - arr[-1][0] < 0.1: return
        arr.append((t, x, y))

    def credit(self, c, r, val):
        if not val > 0: return
        try:
            if int(r[7], 16) & 0x10: return          # only damage to non-friendly units
        except ValueError:
            return
        src = r[1]
        owner = src if src.startswith('Player-') else self.pets.get(src)
        if not owner or not owner.startswith('Player-'): return
        d = c['dmg'].setdefault(owner, {'all': 0.0, 'boss': 0.0, 'b': []})
        d['all'] += val
        i = int(c['now'] // DMG_STEP)                # damage per DMG_STEP-second window, for the timeline graph
        if i >= len(d['b']): d['b'].extend([0.0] * (i + 1 - len(d['b'])))
        d['b'][i] += val
        if BOSS_NPC in r[5]: d['boss'] += val

    @staticmethod
    def tempest(c, g, amount, direct, t):
        d = c['tempest'].setdefault(g, {'hits': 0, 'dmg': 0.0, 't': []})
        if direct:
            d['hits'] += 1
            d['t'].append(round(t, 1))
        d['dmg'] += max(amount, 0)

    def event(self, line, rest):
        c = self.cur
        t = self.ts(line) - c['t0']
        c['now'] = t
        c['lastT'] = t
        ev = rest[:rest.index(',')]
        if ev == 'COMBATANT_INFO':
            r = rest.split(',', 27)
            try: c['specGuid'][r[1]] = int(r[25])
            except (ValueError, IndexError): pass
            return
        if ev == 'UNIT_DIED':
            r = self.split(rest)
            if len(r) > 5 and r[5].startswith('Player-') and r[5] not in c['died']: c['died'][r[5]] = t
            return
        swing = ev.startswith('SWING_')
        spell = ev.startswith('SPELL_') or ev.startswith('RANGE_')
        if not swing and not spell: return
        if ev in ('SPELL_AURA_APPLIED', 'SPELL_AURA_REMOVED'):
            if SURGE_DEBUFF in rest:
                r = self.split(rest)
                if r[9] == SURGE_DEBUFF and r[5].startswith('Player-'):
                    self.names[r[5]] = r[6].split('-')[0]
                    if ev == 'SPELL_AURA_APPLIED':
                        rec = {'g': r[5], 'on': t, 'off': None}
                        c['sdeb'][r[5]] = rec; c['sur'].append(rec)
                    else:
                        rec = c['sdeb'].pop(r[5], None)
                        if rec: rec['off'] = t
                return
            if any(a in rest for a in SSZ_AURAS):
                r = self.split(rest)
                kind = SSZ_AURAS.get(r[9])
                if kind and r[5].startswith('Player-'):
                    self.names[r[5]] = r[6].split('-')[0]
                    key = (kind, r[5])
                    if ev == 'SPELL_AURA_APPLIED':
                        rec = [kind, r[5], t, None, r[9]]; c['hauOpen'][key] = rec; c['hau'].append(rec)
                    else:
                        rec = c['hauOpen'].pop(key, None)
                        if rec: rec[3] = t
                    return
            if VIR_A not in rest and VIR_B not in rest: return
            r = self.split(rest)
            if r[9] in (VIR_A, VIR_B) and r[5].startswith('Player-'):
                c['aura'].append((t, ev[11:], r[9], r[5]))
                self.names[r[5]] = r[6].split('-')[0]
            return
        if ev == 'SPELL_ABSORBED':           # shields: healing credited to whoever cast the shield
            r = self.split(rest)
            a = 9 if (r[9].startswith('Player-') or r[9].startswith('Creature-') or r[9].startswith('Pet-')) else 12
            try:
                o = r[a] if r[a].startswith('Player-') else self.pets.get(r[a]); val = float(r[a + 7])
                if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            except (ValueError, IndexError): pass
            return
        if ev.startswith('SPELL_AURA') or ev in ('SWING_DAMAGE_LANDED', 'SPELL_CAST_START'): return
        r = self.split(rest)

        if ev == 'SPELL_DISPEL':                 # a player cleansing a debuff off a player
            o = r[1] if r[1].startswith('Player-') else self.pets.get(r[1])
            if o and len(r) > 13 and r[5].startswith('Player-'): c['dispels'].append((t, o, r[5], r[13]))
            return
        if len(r) > 2 and r[1].startswith('Player-'): self.names[r[1]] = r[2].split('-')[0]
        if len(r) > 6 and r[5].startswith('Player-'): self.names[r[5]] = r[6].split('-')[0]
        if not c['bossGuid'] and len(r) > 5:
            if BOSS_NPC in r[1]: c['bossGuid'] = r[1]
            elif BOSS_NPC in r[5]: c['bossGuid'] = r[5]
        if ev == 'SPELL_CAST_SUCCESS' and r[9] == TEMPEST_CAST and BOSS_NPC in r[1]:
            c['tempestCasts'] += 1
        if ev == 'SPELL_CAST_SUCCESS' and r[9] == SURGE_CAST and BOSS_NPC in r[1]:
            c['surgeCasts'].append(t)
        if ev == 'SPELL_CAST_SUCCESS' and r[9] == TELEPORT and r[1].startswith('Player-'):
            c['ports'][r[1]] = c['ports'].get(r[1], 0) + 1
        if ev == 'SPELL_SUMMON':
            if r[1].startswith('Player-'): self.pets[r[5]] = r[1]
            elif r[1] in self.pets: self.pets[r[5]] = self.pets[r[1]]
            return
        k = 9 if swing else 12
        x = y = None
        if len(r) > k + 17 and '-' in r[k]:
            try:
                x, y = float(r[k + 14]), float(r[k + 15])
            except ValueError:
                x = y = None
            info, own = r[k], r[k + 1]
            if own and own != '0000000000000000' and own.startswith('Player-') and not info.startswith('Player-'):
                self.pets[info] = own
            if x is not None:
                if info.startswith('Player-'):
                    self.add_pt(c['track'].setdefault(info, []), t, x, y)
                elif info == c['bossGuid']:
                    self.add_pt(c['boss'], t, x, y)
                    try:
                        hp, mx = float(r[k + 2]), float(r[k + 3])
                        if mx > 0: c['bossHp'] = (hp, mx)
                    except ValueError:
                        pass
        if ev in ('SPELL_HEAL', 'SPELL_PERIODIC_HEAL'):
            o = r[1] if r[1].startswith('Player-') else self.pets.get(r[1])
            try: val = float(r[k + 19]) - max(float(r[k + 21]), 0)
            except (ValueError, IndexError): val = 0.0
            if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            return
        enemy_hit = r[5].startswith('Player-') and not r[1].startswith('Player-') and r[1] not in self.pets
        if ev in ('SPELL_DAMAGE', 'SPELL_PERIODIC_DAMAGE', 'RANGE_DAMAGE', 'SWING_DAMAGE'):
            if ev == 'SPELL_DAMAGE' and r[9] == BOOM and r[5].startswith('Player-') and x is not None:
                c['boom'].append((t, r[5], x, y))
            try:
                amt, over, ab = float(r[k + 19]), float(r[k + 21]), float(r[k + 25])
            except (ValueError, IndexError):
                return
            if enemy_hit:
                c['lasthit'][r[5]] = (t, 'Melee' if swing else r[10])
                if not swing and r[9] in AVOID_S: c['avoid'].append((t, r[5], AVOID_S[r[9]], amt - max(over, 0) + max(ab, 0)))
            self.credit(c, r, amt - max(over, 0) + max(ab, 0))
            if r[9] == TEMPEST and r[5].startswith('Player-'):
                self.tempest(c, r[5], amt - max(over, 0) + max(ab, 0), ev == 'SPELL_DAMAGE', t)
        elif ev.endswith('_MISSED'):
            m = 9 if swing else 12
            if len(r) > m + 2 and r[m] == 'ABSORB':
                try: self.credit(c, r, float(r[m + 2]))
                except ValueError: pass
                if enemy_hit and not swing and r[9] in AVOID_S:      # fully soaked by a shield: still an avoidable hit
                    try: c['avoid'].append((t, r[5], AVOID_S[r[9]], float(r[m + 2])))
                    except ValueError: pass
            if not swing and r[9] == TEMPEST and r[5].startswith('Player-'):
                amt = 0.0
                if len(r) > m + 2 and r[m] == 'ABSORB':
                    try: amt = float(r[m + 2])
                    except ValueError: pass
                self.tempest(c, r[5], amt, ev == 'SPELL_MISSED', t)

    def line(self, l):
        l = l.rstrip('\r')
        sep = l.find('  ')
        if sep < 0: return
        rest = l[sep + 2:]
        if self.cur is None:
            if rest.startswith(self.mark): self.start(l, rest)
            return
        if rest.startswith('ENCOUNTER_END,'):
            self.end(rest); return
        if rest.startswith('ENCOUNTER_START,'):
            self.end(None)
            if rest.startswith(self.mark): self.start(l, rest)
            return
        try:
            self.event(l, rest)
        except (IndexError, ValueError):
            pass

    def feed(self, text):
        text = self.leftover + text
        end = text.rfind('\n')
        if end < 0:
            self.leftover = text; return
        pos = 0
        while pos < end:
            if self.cur is None:
                m = text.find(self.mark, pos)
                if m < 0 or m >= end:
                    break
                pos = text.rfind('\n', 0, m) + 1
            nl = text.find('\n', pos)
            if nl < 0 or nl > end: nl = end
            self.line(text[pos:nl])
            pos = nl + 1
        self.leftover = text[end + 1:]

    # ---- output in the page's data shape ----
    @staticmethod
    def pos_at(tr, t):
        if not tr: return None
        lo, hi = 0, len(tr)
        while lo < hi:
            mid = (lo + hi) // 2
            if tr[mid][0] < t: lo = mid + 1
            else: hi = mid
        a = tr[lo - 1] if lo > 0 else None
        b = tr[lo] if lo < len(tr) else None
        if a and b and b[0] - a[0] < 1.6 and b[0] != a[0]:
            f = (t - a[0]) / (b[0] - a[0])
            return [round(a[1] + (b[1] - a[1]) * f, 2), round(a[2] + (b[2] - a[2]) * f, 2)]
        cand = [p for p in (a, b) if p]
        best = min(cand, key=lambda p: abs(p[0] - t)) if cand else None
        return [round(best[1], 2), round(best[2], 2)] if best and abs(best[0] - t) <= 1.0 else None

    @staticmethod
    def surge_groups(p):
        """Venomous Surges in a pull: each boss cast and the debuffs it put on players (applied a few seconds after)."""
        groups = []
        for rec in sorted(p['sur'], key=lambda d: d['on']):
            cast = max((t for t in p['surgeCasts'] if 0 <= rec['on'] - t <= 8), default=None)
            g = groups[-1] if groups else None
            if g and ((cast is not None and g['cast'] == cast) or (cast is None and g['cast'] is None and rec['on'] - g['deb'][-1]['on'] <= 4)):
                g['deb'].append(rec)
            else:
                groups.append({'cast': cast, 't': cast if cast is not None else rec['on'], 'deb': [rec]})
        return groups

    def build_surges(self, p, nm):
        out = []
        for g in self.surge_groups(p):
            deb = []
            for d in g['deb']:
                tr, off = p['track'].get(d['g'], []), d['off']
                died = p['died'].get(d['g'])
                if off is None: end = 'open'
                elif off - d['on'] >= SURGE_LEN: end = 'expired'
                elif died is not None and abs(died - off) <= 0.6: end = 'died'
                else: end = 'early'
                stop = off if off is not None else p['lastT']
                trail = [self.pos_at(tr, d['on'] + k * 0.5) for k in range(int((stop - d['on']) / 0.5) + 1)]
                deb.append({'n': nm(d['g']), 'on': round(d['on'] - g['t'], 2), 'off': round(stop - g['t'], 2), 'end': end,
                            'pos': self.pos_at(tr, stop), 'tr': trail})
            out.append({'t': round(g['t'], 2), 'deb': deb})
        return out

    def build(self, log_name):
        nm = lambda g: self.names.get(g, g)
        for p in self.pulls:
            for g, sp in p['specGuid'].items():
                if g in self.names: self.specs[self.names[g]] = sp
        pulls = []
        for p in self.pulls:
            if p['ended'] and (p['dur'] or 0) < 2: continue     # instant resets
            casts = []
            for t, ev, sid, g in sorted(p['aura']):
                if ev != 'APPLIED': continue
                if not casts or t - casts[-1]['t'] > 10: casts.append({'t': t, 'assign': {}, 'extra': []})
                c = casts[-1]
                if t - c['t'] <= 1.5:
                    c['assign'].setdefault(g, 'A' if sid == VIR_A else 'B')
                else:
                    c['extra'].append([round(t - c['t'], 2), nm(g), 'A' if sid == VIR_A else 'B'])
            out = []
            for c in casts:
                players = {}
                for g, typ in c['assign'].items():
                    tr = p['track'].get(g)
                    if not tr: continue
                    dt = p['died'].get(g)
                    s = []
                    for k in range(27):
                        t = c['t'] + k * 0.25
                        s.append(None if (dt is not None and t > dt + 0.05) or t > p['lastT'] + 0.05 else self.pos_at(tr, t))
                    players[nm(g)] = {'type': typ, 's': s}
                out.append({'t': round(c['t'], 2), 'boss': self.pos_at(p['boss'], c['t'] + 5), 'players': players, 'extra': c['extra'],
                            'booms': [[round(b[0] - c['t'], 2), nm(b[1]), round(b[2], 2), round(b[3], 2)] for b in p['boom'] if 0 <= b[0] - c['t'] <= 10],
                            'deaths': [[round(t - c['t'], 2), nm(g)] for g, t in p['died'].items() if 0 <= t - c['t'] <= 10]})
            pulls.append({'n': len(pulls) + 1, 'clock': p['clock'], 'date': p['date'], 'diff': p['diff'],
                          'dur': round(p['dur'] if p['ended'] else p['lastT'], 1), 'kill': p['kill'], 'live': not p['ended'],
                          'hp': (p['bossHp'][0] / p['bossHp'][1]) if p['bossHp'] else None, 'casts': out,
                          'dmg': {nm(g): {'all': round(d['all']), 'boss': round(d['boss']), 'b': [round(v) for v in d['b']]} for g, d in p['dmg'].items()},
                          'dmgStep': DMG_STEP,
                          'tempest': {nm(g): {'hits': d['hits'], 'dmg': round(d['dmg']), 't': d['t']} for g, d in p['tempest'].items()},
                          'tempestCasts': p['tempestCasts'],
                          'ports': {nm(g): n for g, n in p['ports'].items()},
                          'surges': self.build_surges(p, nm),
                          # [kind, player, on, off, spell, position at on, position at off]: cross = Raging Crosswinds
                          # arrow (its spell sets the direction), gust = Turbulent Gusts (in the air)
                          'hau': [[k, nm(g), round(on, 2), None if off is None else round(off, 2), sid,
                                   self.pos_at(p['track'].get(g, []), on),
                                   self.pos_at(p['track'].get(g, []), off) if off is not None else None]
                                  for k, g, on, off, sid in p['hau']],
                          'heal': {nm(g): round(v) for g, v in p['heal'].items()},
                          'dispels': [[round(t, 2), nm(a), nm(b), s] for t, a, b, s in p['dispels']],
                          'avoid': [[round(t, 2), nm(g), s, round(v)] for t, g, s, v in p['avoid']],
                          'deaths': [[round(t, 2), nm(g), (lambda h: h[1] if h and t - h[0] <= 5 else None)(p['lasthit'].get(g)), None]
                                     for g, t in sorted(p['died'].items(), key=lambda kv: kv[1])]})
        return {'step': 0.25, 'log': log_name, 'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'tz': self.tz,
                'pulls': pulls, 'specs': dict(self.specs)}


class FangsParser:
    """Mythic The Twin Fangs (Vexhul and Ithraz). Any boss encounter with those NPCs in it is kept; everything the
    Twin Fangs page needs is recorded: positions, Eternal Venom stacks and what caused them, Caustic Globules
    (Caustic Deluge sets, soakers, CC, where they ruptured), Coiling Ichor drops, Visceral Burst kicks,
    Ravenous Feast / Tainted Blood hits, damage, healing and deaths."""
    BOSSES = {'257361': 'Vexhul', '257368': 'Ithraz'}
    # IDs as the combat log records them (several differ from the Encounter Journal's)
    VENOM, DELUGE = '1290336', '1289192'
    SOAK, RUPTURE, DROP = '1289201', '1290338', '1289994'   # walking into a globule / an unsoaked globule bursting on the raid / a falling globule
    GLOB_NPC = '-269194-'                                      # Barbed Bulwark: the globule (its 1303378 shield comes off when it's CC'd)
    ICHORS = ('1290814', '1290809')                            # Coiling Ichor debuff on the player (and the boss's cast ID)
    BULWARK, ROUSE, BURST = '1303378', '1308356', '1308385'
    FEAST, FEAST_HITS = '1290516', ('1290662', '1290516')   # the cast, and the slam's damage
    TAINTED = ('1310102', '1310099')                          # Tainted Blood: a debuff re-applied while standing in it (and the journal's ID)
    OK_VENOM = {'1289201': 'Caustic Globule', '1308122': 'Venomous Emergence', '1291404': 'Venomous Emergence',
                '1293295': 'Corrosive Spit', '1293979': 'Corrosive Spit', '1291478': 'Corrosive Spit'}
    NOT_CAUSE = {'1290480', '1290336', '1308482', '1303235', '1294976', '1310102', '1310099', '1310096', '1303230', '1289192', '1289237'}
    # ^ Eternal Venom itself, Rouse the Brood, Blood Torrent, Toxic Fumes, Tainted Blood, Feasted, the tank's Caustic Deluge debuff
    #   and its damage ticks (1289237: on the tank the whole time, never the cause)
    LABEL = {'1290338': 'Caustic Globule burst (unsoaked)', '1289994': 'Caustic Deluge (falling globule)'}
    # avoidable hits (Wowhead guide + journal): Stir the Depths waves (1292807; not its raid pulse), Vile Flood, acid
    # splashes, Coiling Ichor on anyone but its carrier (the page checks), gore pools, Sanguine Storm, Submerge impacts and
    # slicks, Barbed Bulwark. The page drops tanks for Vile Flood.
    AVOID = {'1292807': 'Stir the Depths wave', '1294605': 'Vile Flood', '1289994': 'Caustic Deluge splash', '1290878': 'Coiling Ichor',
             '1292552': 'Congealed Gore', '1306876': 'Sanguine Storm', '1308556': 'Submerge', '1309471': 'Noxious Slick', '1307363': 'Barbed Bulwark'}
    # CC auras (cast ID / applied aura ID where they differ) from the raid's classes
    CC = {'221562': 'Asphyxiate', '207167': 'Blinding Sleet', '46968': 'Shockwave', '132168': 'Shockwave', '107570': 'Storm Bolt',
          '132169': 'Storm Bolt', '5246': 'Intimidating Shout', '179057': 'Chaos Nova', '1234195': 'Void Nova', '217832': 'Imprison',
          '207684': 'Sigil of Misery', '207685': 'Sigil of Misery', '211881': 'Fel Eruption', '119381': 'Leg Sweep', '115078': 'Paralysis',
          '853': 'Hammer of Justice', '115750': 'Blinding Light', '105421': 'Blinding Light', '20066': 'Repentance', '1833': 'Cheap Shot',
          '408': 'Kidney Shot', '1776': 'Gouge', '2094': 'Blind', '6770': 'Sap', '360806': 'Sleep Walk', '8122': 'Psychic Scream',
          '64044': 'Psychic Horror', '192058': 'Capacitor Totem', '118905': 'Capacitor Totem', '51514': 'Hex', '5211': 'Mighty Bash',
          '99': 'Incapacitating Roar', '33786': 'Cyclone', '109248': 'Binding Shot', '117526': 'Binding Shot', '19577': 'Intimidation',
          '24394': 'Intimidation', '187650': 'Freezing Trap', '3355': 'Freezing Trap', '213691': 'Scatter Shot', '31661': "Dragon's Breath",
          '118': 'Polymorph', '113724': 'Ring of Frost', '82691': 'Ring of Frost', '30283': 'Shadowfury', '5484': 'Howl of Terror',
          '5782': 'Fear', '118699': 'Fear', '6789': 'Mortal Coil', '89766': 'Axe Toss',
          '427773': 'Blind', '1258508': 'Intimidation', '20549': 'War Stomp', '198909': 'Song of Chi-Ji', '116844': 'Ring of Peace',
          '108194': 'Asphyxiate', '91800': 'Gnaw', '91797': 'Monstrous Blow', '47476': 'Strangulate', '102359': 'Mass Entanglement', '339': 'Entangling Roots',
          '2637': 'Hibernate', '22570': 'Maim', '163505': 'Rake', '61391': 'Typhoon', '127797': "Ursol's Vortex", '102793': "Ursol's Vortex",
          '355689': 'Landslide', '368970': 'Tail Swipe', '357214': 'Wing Buffet', '372245': 'Terror of the Skies', '236777': 'High Explosive Trap',
          '462031': 'Implosive Trap', '162480': 'Steel Trap', '122': 'Frost Nova', '157981': 'Blast Wave', '157997': 'Ice Nova', '28272': 'Polymorph',
          '28271': 'Polymorph', '61305': 'Polymorph', '61721': 'Polymorph', '61780': 'Polymorph', '126819': 'Polymorph', '161353': 'Polymorph',
          '161354': 'Polymorph', '161355': 'Polymorph', '161372': 'Polymorph', '277787': 'Polymorph', '277792': 'Polymorph', '321395': 'Polymorph',
          '391622': 'Polymorph', '10326': 'Turn Evil', '605': 'Mind Control', '9484': 'Shackle Undead', '200200': 'Holy Word: Chastise',
          '200196': 'Holy Word: Chastise', '226943': 'Mind Bomb', '205369': 'Mind Bomb', '210873': 'Hex', '211004': 'Hex', '211010': 'Hex', '211015': 'Hex',
          '269352': 'Hex', '277778': 'Hex', '277784': 'Hex', '309328': 'Hex', '64695': 'Earthgrab', '51490': 'Thunderstorm', '305485': 'Lightning Lasso',
          '197214': 'Sundering', '118345': 'Pulverize', '710': 'Banish', '6358': 'Seduction', '261589': 'Seduction', '1122': 'Summon Infernal',
          '22703': 'Infernal Awakening', '107079': 'Quaking Palm', '287712': 'Haymaker', '255723': 'Bull Rush', '202244': 'Overrun', '204490': 'Sigil of Silence'}

    def __init__(self):
        self.pulls, self.cur, self.leftover = [], None, ''
        self.names, self.specs, self.pets = {}, {}, {}
        self.finished = 0          # count of kept pulls that have ended
        self.tz = None

    def has_data(self): return bool(self.pulls) or bool(self.cur and self.cur['twin'])

    def feed(self, text):
        text = self.leftover + text
        end = text.rfind('\n')
        if end < 0:
            self.leftover = text; return
        pos = 0
        while pos < end:
            if self.cur is None:                 # skip to the next boss pull that isn't Sszorak
                m = text.find('ENCOUNTER_START,', pos)
                if m < 0 or m >= end: break
                pos = text.rfind('\n', 0, m) + 1
            nl = text.find('\n', pos)
            if nl < 0 or nl > end: nl = end
            try: self.line(text[pos:nl])
            except (IndexError, ValueError): pass
            pos = nl + 1
        self.leftover = text[end + 1:]

    def start(self, l, rest):
        r = Parser.split(rest)
        if r[1] == ENC: self.cur = None; return              # Sszorak has its own parser
        when = l.split(' ', 1); self.tz = log_tz(l)
        self.cur = dict(enc=r[1], encName=r[2], t0=Parser.ts(l), clock=when[1][:8].split('.')[0], date=when[0], diff=int(r[3]),
                        twin='twin fangs' in r[2].lower(), dur=0.0, kill=False, ended=False, lastT=0.0,
                        track={}, ctrack={}, specGuid={}, died=[], dmg={}, heal={}, hp={},
                        hits={}, stacks={}, venom=[], deluges=[], rouses=[], glob={}, soaks=[], bursts=[], cc=[], bulwark=set(),
                        ichor={}, ichorOut=[], casts=[], open={}, feasts=[], feastHits=[], tainted=[], dispels=[], avoid=[])

    def close(self, r):
        c = self.cur
        if r: c['kill'], c['dur'] = r[5] == '1', int(r[6]) / 1000
        else: c['dur'] = c['lastT']
        c['ended'] = True
        if c['twin']:
            self.pulls.append(c); self.finished += 1
        self.cur = None

    def owner(self, g):
        return g if g.startswith('Player-') else self.pets.get(g)

    def line(self, l):
        l = l.rstrip('\r')
        sep = l.find('  ')
        if sep < 0: return
        rest = l[sep + 2:]
        ev = rest[:rest.find(',')]
        if ev == 'ENCOUNTER_START':
            if self.cur: self.close(None)
            self.start(l, rest); return
        c = self.cur
        if c is None: return
        t = Parser.ts(l) - c['t0']; c['lastT'] = t
        if ev == 'ENCOUNTER_END': self.close(rest.split(',')); return
        if ev == 'COMBATANT_INFO':
            r = rest.split(',', 27)
            try: c['specGuid'][r[1]] = int(r[25])
            except (ValueError, IndexError): pass
            return
        r = Parser.split(rest)
        if ev == 'UNIT_DIED':
            if len(r) > 5 and r[5].startswith('Player-'): c['died'].append((t, r[5]))
            return
        if not (ev.startswith('SPELL_') or ev.startswith('SWING_') or ev.startswith('RANGE_')): return
        src, dst = r[1], r[5]
        if src.startswith('Player-'): self.names[src] = r[2].split('-')[0]
        if dst.startswith('Player-'): self.names[dst] = r[6].split('-')[0]
        if not c['twin'] and any(('-' + k + '-') in src or ('-' + k + '-') in dst for k in self.BOSSES): c['twin'] = True
        swing = ev.startswith('SWING_')
        sid = '' if swing else r[9]
        for g in (src, dst):                       # when each globule was first and last seen
            if self.GLOB_NPC in g:
                gl = c['glob'].setdefault(g, {'first': t, 'last': t}); gl['last'] = t
        if ev == 'SPELL_SUMMON':
            o = self.owner(src)
            if o: self.pets[dst] = o
            return
        # positions from the advanced-logging block
        k = 9 if swing else 12
        if len(r) > k + 17 and '-' in r[k]:
            info = r[k]
            own = r[k + 1]
            if own and own.startswith('Player-') and not info.startswith('Player-'): self.pets[info] = own
            try:
                x, y = float(r[k + 14]), float(r[k + 15])
                if info.startswith('Player-'): Parser.add_pt(c['track'].setdefault(info, []), t, x, y)
                else:
                    tr = c['ctrack'].setdefault(info, [])
                    if not tr or t - tr[-1][0] >= 0.25: tr.append((t, x, y))
                    for npc, nm in self.BOSSES.items():
                        if '-' + npc + '-' in info:
                            hp, mx = float(r[k + 2]), float(r[k + 3])
                            if mx > 0: c['hp'][nm] = hp / mx
            except ValueError: pass
        # ---- auras
        if ev.startswith('SPELL_AURA'):
            if sid in self.TAINTED and ev in ('SPELL_AURA_APPLIED', 'SPELL_AURA_REFRESH', 'SPELL_AURA_APPLIED_DOSE') and dst.startswith('Player-'):
                tl = c['tainted']
                if not any(g == dst and t - t2 < 1.0 for t2, g in tl[-30:]): tl.append((t, dst))   # one entry per second is plenty
            if ev == 'SPELL_AURA_APPLIED' and dst.startswith('Player-') and not src.startswith('Player-') and sid not in self.NOT_CAUSE:
                c['hits'].setdefault(dst, []).append((t, sid, r[10]))      # boss debuffs can cause a stack too (Corrosive Spit's mark, Stir the Depths)
            if sid == self.VENOM and dst.startswith('Player-'):
                prev = c['stacks'].get(dst, 0)
                if ev == 'SPELL_AURA_APPLIED': n = 1
                elif ev == 'SPELL_AURA_APPLIED_DOSE': n = int(r[-1]) if r[-1].isdigit() else prev + 1
                elif ev == 'SPELL_AURA_REMOVED_DOSE': n = int(r[-1]) if r[-1].isdigit() else max(prev - 1, 0)
                elif ev == 'SPELL_AURA_REMOVED': n = 0
                else: return
                c['stacks'][dst] = n
                if n > prev:
                    c['venom'].append({'t': t, 'g': dst, 'n': n, 'auraSrc': r[2]})
            elif sid in self.ICHORS and dst.startswith('Player-'):
                if ev == 'SPELL_AURA_APPLIED': c['ichor'][dst] = t
                elif ev == 'SPELL_AURA_REMOVED':
                    on = c['ichor'].pop(dst, None); c['ichorOut'].append({'g': dst, 'on': on, 'off': t})
            elif sid == self.BULWARK and not dst.startswith('Player-'):
                c['bulwark'].add(dst)
            elif ev == 'SPELL_AURA_APPLIED' and sid in self.CC and not dst.startswith('Player-'):
                o = self.owner(src)
                if o: c['cc'].append((t, o, dst, self.CC[sid]))
            return
        # ---- casts
        if ev == 'SPELL_CAST_SUCCESS' or ev == 'SPELL_CAST_START':
            if sid == self.DELUGE and ev == 'SPELL_CAST_SUCCESS': c['deluges'].append(t)
            elif sid == self.ROUSE and ev == 'SPELL_CAST_SUCCESS': c['rouses'].append(t)
            elif sid == self.FEAST and ev == 'SPELL_CAST_START': c['feasts'].append(t)
            if sid == self.BURST and ev == 'SPELL_CAST_START':
                cast = {'t': t, 'src': src, 'kick': None, 'res': None}
                c['casts'].append(cast); c['open'][src] = cast
            elif sid == self.BURST and ev == 'SPELL_CAST_SUCCESS':
                cast = c['open'].pop(src, None)
                if cast: cast['res'] = 'completed'
            return
        if ev == 'SPELL_CAST_FAILED' and sid == self.BURST:
            cast = c['open'].pop(src, None)
            if cast: cast['res'] = 'stopped'
            return
        if ev == 'SPELL_DISPEL':                 # a player cleansing a debuff off a player
            o = self.owner(src)
            if o and len(r) > 13 and dst.startswith('Player-'): c['dispels'].append((t, o, dst, r[13].strip('"')))
            return
        if ev == 'SPELL_INTERRUPT' and self.BURST in (r[12] if len(r) > 12 else '', r[29] if len(r) > 29 else ''):
            cast = c['open'].pop(dst, None)
            if cast:
                o = self.owner(src) or src
                cast['kick'] = {'g': o, 'name': r[2].split('-')[0], 'pet': not src.startswith('Player-'), 'spell': r[10], 'd': t - cast['t']}
                cast['res'] = 'kicked'
            return
        # ---- damage and healing
        if ev.endswith('_DAMAGE') or ev.endswith('_MISSED'):
            if dst.startswith('Player-') and not src.startswith('Player-'):
                c['hits'].setdefault(dst, []).append((t, sid, 'Melee' if swing else r[10]))
                if sid in self.AVOID:          # damage taken, shields included
                    try: a = float(r[k + 19]) - max(float(r[k + 21]), 0) + max(float(r[k + 25]), 0) if ev.endswith('_DAMAGE') else float(r[k + 2]) if r[k] == 'ABSORB' else 0.0
                    except (ValueError, IndexError): a = 0.0
                    c['avoid'].append((t, dst, self.AVOID[sid], a))
                if sid == self.SOAK: c['soaks'].append((t, dst))
                elif sid == self.RUPTURE: c['bursts'].append((t, dst))
                elif sid in self.FEAST_HITS: c['feastHits'].append((t, dst))
                elif sid in self.TAINTED: c['tainted'].append((t, dst))
            if ev.endswith('_DAMAGE'):
                try: val = float(r[k + 19]) - max(float(r[k + 21]), 0) + max(float(r[k + 25]), 0)
                except (ValueError, IndexError): val = 0.0
            elif len(r) > k + 2 and r[k] == 'ABSORB':
                try: val = float(r[k + 2])
                except ValueError: val = 0.0
            else: val = 0.0
            if val > 0:
                try: hostile_dst = not (int(r[7], 16) & 0x10)
                except ValueError: hostile_dst = False
                o = self.owner(src)
                if hostile_dst and o: c['dmg'][o] = c['dmg'].get(o, 0) + val
            return
        if ev in ('SPELL_HEAL', 'SPELL_PERIODIC_HEAL'):
            try: val = float(r[k + 19]) - max(float(r[k + 21]), 0)
            except (ValueError, IndexError): val = 0.0
            o = self.owner(src)
            if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            return
        if ev == 'SPELL_ABSORBED':           # shields: credited to whoever cast the shield
            a = 9 if (r[9].startswith('Player-') or r[9].startswith('Creature-') or r[9].startswith('Pet-')) else 12
            try:
                o = self.owner(r[a]); val = float(r[a + 7])
                if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            except (ValueError, IndexError): pass

    def build(self, log_name):
        nm = lambda g: self.names.get(g, g)
        pulls = self.pulls + ([self.cur] if self.cur and self.cur['twin'] else [])
        for p in pulls:
            for g, sp in p['specGuid'].items():
                if g in self.names: self.specs[self.names[g]] = sp
        out = []
        for n, p in enumerate(pulls, 1):
            stop = p['dur'] if p['ended'] else p['lastT']
            pos = lambda g, t: Parser.pos_at(p['track'].get(g, []), t)
            def cpos(g, t):
                tr = p['ctrack'].get(g) or []
                prior = [q for q in tr if q[0] <= t + 0.5]
                q = prior[-1] if prior else (tr[0] if tr else None)
                return [round(q[1], 2), round(q[2], 2)] if q else None
            setno = lambda t: sum(1 for d in p['deluges'] if d <= t + 0.5)
            # Eternal Venom stacks and what caused each one: the hit that landed with the stack
            venom = []
            for v in p['venom']:
                around = [h for h in p['hits'].get(v['g'], []) if -0.35 <= v['t'] - h[0] <= 0.5]
                cause = [h for h in around if h[1] not in self.NOT_CAUSE]
                okh = [h for h in cause if h[1] in self.OK_VENOM]
                near = lambda hs: min(hs, key=lambda h: abs(v['t'] - h[0]))
                if okh: src, ok = self.OK_VENOM[near(okh)[1]], True
                elif cause: h = near(cause); src, ok = self.LABEL.get(h[1], h[2].strip('"')), False
                elif any(h[1] == '1294976' for h in around): src, ok = 'Toxic Fumes', False
                else: src, ok = 'Unknown', False
                venom.append({'t': round(v['t'], 2), 'n': nm(v['g']), 'st': v['n'], 'src': src, 'ok': ok, 'pos': pos(v['g'], v['t'])})
            # Caustic Globules (Barbed Bulwark NPCs): set, who soaked each, which burst on the raid unsoaked and where, who CC'd it
            gl = p['glob']
            info = {g: {'id': g.rsplit('-', 1)[-1], 'set': setno(d['first']), 'born': round(d['first'], 2), 'end': round(d['last'], 2),
                        'rupt': None, 'pos': None, 'soak': [], 'cc': [[round(t, 2), nm(o), sp] for t, o, d2, sp in p['cc'] if d2 == g]}
                    for g, d in gl.items()}
            bursts = []                       # an unsoaked globule bursts on the whole raid at once (hits within 0.25 s), about 10.8 s after it landed
            for t, _ in sorted(p['bursts']):
                if not bursts or t - bursts[-1] > 0.25: bursts.append(t)
            for bt in bursts:                 # the globule that landed about 10.8 s before (its Barbed Bulwark marks where it is)
                cand = [g for g in info if info[g]['rupt'] is None and abs(gl[g]['first'] + 10.8 - bt) <= 0.7]
                g = min(cand, key=lambda g: abs(gl[g]['first'] + 10.8 - bt)) if cand else None
                if g: info[g]['rupt'] = round(bt, 2); info[g]['pos'] = cpos(g, gl[g]['first'] + 0.5)
                else: info['burst%d' % len(info)] = {'id': '', 'set': setno(bt - 10.8), 'born': None, 'end': None, 'rupt': round(bt, 2), 'pos': None, 'soak': [], 'cc': []}
            soaks = [[round(t, 2), nm(d), setno(t)] for t, d in sorted(p['soaks'])]   # each soak hit is one globule walked into
            globules = sorted(info.values(), key=lambda x: x['born'] if x['born'] is not None else (x['rupt'] or 0) - 10.8)
            # Coiling Ichor: where each player stood when it ran out
            died = {}
            for t, g in p['died']: died.setdefault(g, t)
            ichor = []
            for d in p['ichorOut'] + [{'g': g, 'on': on, 'off': None} for g, on in p['ichor'].items()]:
                off = d['off'] if d['off'] is not None else stop
                dt = died.get(d['g'])
                end = 'open' if d['off'] is None else 'died' if dt is not None and abs(dt - off) <= 0.6 else 'expired'
                ichor.append({'n': nm(d['g']), 'on': round(d['on'], 2) if d['on'] is not None else None, 'off': round(off, 2), 'end': end, 'pos': pos(d['g'], off)})
            # Visceral Burst casts (Broodlings of Ithraz) and kicks; the bosses move partway through the fight, so each cast
            # carries where the bosses and the middle of the raid were right then (for its quadrant)
            def boss_at(t):
                out = {}
                for npc, bn in self.BOSSES.items():
                    pts = [q for g, tr in p['ctrack'].items() if '-' + npc + '-' in g for q in tr if q[0] <= t]
                    if pts: q = max(pts); out[bn] = [round(q[1], 2), round(q[2], 2)]
                return out
            def raid_at(t):
                pts = [(x, y) for tr in p['track'].values() for tt, x, y in tr if t - 3 <= tt <= t + 3]
                if not pts: return None
                xs, ys = sorted(x for x, _ in pts), sorted(y for _, y in pts)
                return [round(xs[len(xs) // 2], 2), round(ys[len(ys) // 2], 2)]
            brood = [{'t': round(x['t'], 2), 'src': x['src'].rsplit('-', 1)[-1], 'pos': cpos(x['src'], x['t']), 'res': x['res'],
                      'bp': boss_at(x['t']), 'rc': raid_at(x['t']),
                      'kick': dict(by=nm(x['kick']['g']), pet=x['kick']['pet'], spell=x['kick']['spell'], d=round(x['kick']['d'], 3)) if x['kick'] else None}
                     for x in p['casts']]
            # Ravenous Feast: up to 3 slams per cast (a slam's hits land together, so hits under 0.8 s apart are one slam),
            # plus every Tainted Blood hit until the next Ravenous Feast; the page checks each soaker got one after their slam
            slams = []
            for t, g in sorted(p['feastHits']):
                if not slams or t - slams[-1]['t1'] > 0.8: slams.append({'t': t, 't1': t, 'hit': set()})
                slams[-1]['t1'] = t; slams[-1]['hit'].add(g)
            feasts = []
            starts = sorted(p['feasts'])
            for s_ in slams:
                f = max((i for i, ft in enumerate(starts) if ft <= s_['t'] + 0.5), default=None)
                key = f if f is not None else -1
                if not feasts or feasts[-1]['key'] != key or len(feasts[-1]['slams']) >= 3:
                    feasts.append({'key': key, 't': starts[f] if f is not None else s_['t'], 'slams': []})
                feasts[-1]['slams'].append({'t': round(s_['t'], 2), 'hit': sorted(nm(g) for g in s_['hit'])})
            for k, f in enumerate(feasts):
                end = feasts[k + 1]['t'] if k + 1 < len(feasts) else f['t'] + 60
                f['tb'] = [[round(t, 2), nm(g)] for t, g in sorted(p['tainted']) if f['t'] <= t < end]
                f['t'] = round(f['t'], 2); f.pop('key')
            # deaths: what hit them last, and when they were back up (a battle rez)
            deaths = []
            for t, g in sorted(p['died']):
                last = [h for h in p['hits'].get(g, []) if t - 5 <= h[0] <= t + 0.2]
                back = next((q[0] for q in p['track'].get(g, []) if q[0] > t + 1.0), None)
                deaths.append([round(t, 2), nm(g), last[-1][2] if last else None, round(back, 2) if back is not None else None])
            # where the bosses stood and the middle of the raid (medians), for the kick quadrants
            med = lambda a: sorted(a)[len(a) // 2] if a else None
            bpos = {}
            for npc, bn in self.BOSSES.items():
                pts = [q for g, tr in p['ctrack'].items() if '-' + npc + '-' in g for q in tr]
                if pts: bpos[bn] = [round(med([q[1] for q in pts]), 2), round(med([q[2] for q in pts]), 2)]
            allp = [q for tr in p['track'].values() for q in tr]
            rc = [round(med([q[1] for q in allp]), 2), round(med([q[2] for q in allp]), 2)] if allp else None
            stacks_max = {}
            for v in p['venom']: stacks_max[nm(v['g'])] = max(stacks_max.get(nm(v['g']), 0), v['n'])
            out.append({'n': n, 'enc': p['enc'], 'encName': p['encName'], 'clock': p['clock'], 'date': p['date'], 'diff': p['diff'],
                        'dur': round(stop, 1), 'kill': p['kill'], 'live': not p['ended'], 'hp': {k: round(v, 4) for k, v in p['hp'].items()},
                        'deaths': deaths, 'bpos': bpos, 'rc': rc,
                        'deluges': [round(t, 2) for t in p['deluges']], 'rouses': [round(t, 2) for t in p['rouses']],
                        'venom': venom, 'stacks': stacks_max, 'globules': globules, 'soaks': soaks, 'ichor': ichor, 'brood': brood, 'feasts': feasts,
                        'dispels': [[round(t, 2), nm(a), nm(b), s] for t, a, b, s in p['dispels']],
                        'avoid': [[round(t, 2), nm(g), s, round(v)] for t, g, s, v in p['avoid']],
                        'dmg': {nm(g): round(v) for g, v in p['dmg'].items()}, 'heal': {nm(g): round(v) for g, v in p['heal'].items()}})
        return {'log': log_name, 'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'tz': self.tz, 'pulls': out, 'specs': dict(self.specs)}


class KithixParser:
    """Kith'ix (The Unbinding of Kith'ix), any difficulty. Abilities are matched by spell NAME inside the encounter, not by
    ID: the Encounter Journal's IDs often differ from the log's, and each difficulty can use its own ID. The page does
    the scoring; this keeps the raw events it needs: boss casts, chosen debuffs (on/off and top stack), avoidable and
    tracked hits (with where the player stood), add deaths, Gut Reaction / Dark Frenzy on adds, healing into Abyssal Grasp,
    damage, healing and deaths. Every hostile spell seen is also listed per pull ('seen'), to check the names against real logs.
    Checked against 32 Heroic pulls (Oct 2026): the log uses the journal's names. Fixate never shows in the log, so which
    player a Bomber Beetle chased isn't known; Gut Reaction (on the beetle from spawn, 30 s) ends early when it touches someone."""
    ENC, BOSS, BEETLE = '3513', '-267861-', '-268194-'   # Kith'ix, and the Bomber Beetle's creature id
    CASTS = {"Suffocating Darkness", 'Extinguish', 'Stygian Howl', 'Divine Radiance', 'Eightfold Eclipse', 'Voidswarm',
             'Dark Devastation', 'Null Gate', 'Unspeakable Horrors', 'Twisted Appendage', 'Negation'}
    AURAS = {'Abyssal Grasp', "Light's Embrace", 'Overwhelming Fear', 'Unspeakable Horrors', 'Digestive Juices',
             'Mindsting', 'Eclipse Fragment', 'Lost Refulgence', 'Null Gate', 'Nullify', 'Negation', 'Devastated', 'Exhausted'}
    HITS = {'Venom Roar', 'Twisted Appendage', 'Suffocation', 'Dark Devastation', 'Voidscar', 'Overwhelming Fear', 'Extinguish',
            'Null Gate', 'Nullshards', 'Explosive Restabilization'}
    NPC_AURAS = {'Dark Frenzy', 'Gut Reaction'}
    ADDS = {'Venomous Hulk', 'Voidweaver', 'Bomber Beetle', 'Mindstinger', 'Mindstinger Egg'}

    def __init__(self):
        self.pulls, self.cur, self.leftover = [], None, ''
        self.names, self.specs, self.pets = {}, {}, {}
        self.finished = 0
        self.tz = None

    def has_data(self): return bool(self.pulls) or bool(self.cur)

    def feed(self, text):
        text = self.leftover + text
        end = text.rfind('\n')
        if end < 0:
            self.leftover = text; return
        pos = 0
        while pos < end:
            if self.cur is None:                 # skip to the next Kith'ix pull
                m = text.find('ENCOUNTER_START,' + self.ENC + ',', pos)
                if m < 0 or m >= end: break
                pos = text.rfind('\n', 0, m) + 1
            nl = text.find('\n', pos)
            if nl < 0 or nl > end: nl = end
            try: self.line(text[pos:nl])
            except (IndexError, ValueError): pass
            pos = nl + 1
        self.leftover = text[end + 1:]

    def owner(self, g):
        return g if g.startswith('Player-') else self.pets.get(g)

    def line(self, l):
        l = l.rstrip('\r')
        sep = l.find('  ')
        if sep < 0: return
        rest = l[sep + 2:]
        ev = rest[:rest.find(',')]
        c = self.cur
        if ev == 'ENCOUNTER_START':
            r = Parser.split(rest)
            if c: self.close(None)
            if r[1] != self.ENC: return
            when = l.split(' ', 1); self.tz = log_tz(l)
            self.cur = dict(enc=r[1], t0=Parser.ts(l), clock=when[1][:8].split('.')[0], date=when[0], diff=int(r[3]), size=int(r[4]),
                            dur=0.0, kill=False, ended=False, lastT=0.0, hp=None, track={}, specGuid={}, died=[], npcDied=[],
                            dmg={}, heal={}, lasthit={}, casts={}, auras={}, open={}, hits=[], npc=[], npcOpen={}, grasp={}, seen={},
                            juice=[], bpos={}, hatched=[], dispels=[])
            return
        if c is None: return
        t = Parser.ts(l) - c['t0']; c['lastT'] = t
        if ev == 'ENCOUNTER_END':
            r = rest.split(',')
            if r[1] == self.ENC: self.close(r)
            return
        if ev == 'COMBATANT_INFO':
            r = rest.split(',', 27)
            try: c['specGuid'][r[1]] = int(r[25])
            except (ValueError, IndexError): pass
            return
        r = Parser.split(rest)
        if ev == 'UNIT_DIED':
            if len(r) > 5:
                if r[5].startswith('Player-'): c['died'].append((t, r[5]))
                elif r[5].startswith('Creature-') and r[6].strip('"') in self.ADDS: c['npcDied'].append((t, r[5].rsplit('-', 1)[-1], r[6].strip('"')))
            return
        if not (ev.startswith('SPELL_') or ev.startswith('SWING_') or ev.startswith('RANGE_')): return
        src, dst = r[1], r[5]
        if src.startswith('Player-'): self.names[src] = r[2].split('-')[0]
        if dst.startswith('Player-'): self.names[dst] = r[6].split('-')[0]
        swing = ev.startswith('SWING_')
        sid, sname = ('', 'Melee') if swing else (r[9], r[10].strip('"'))
        if ev == 'SPELL_SUMMON':
            o = self.owner(src)
            if o: self.pets[dst] = o
            return
        hostile_src = not src.startswith('Player-') and not self.owner(src)
        if ev == 'SPELL_HEAL_ABSORBED':        # a heal eaten by Abyssal Grasp: credit the healer (r[12]) with the amount
            if sname == 'Abyssal Grasp':
                o = self.owner(r[12])
                try: v = float(r[-2])
                except ValueError: v = 0.0
                if o and v > 0: c['grasp'][o] = c['grasp'].get(o, 0) + v
            return
        if not swing and ev != 'SPELL_ABSORBED':   # every enemy spell (and Liadrin's) in the pull, for checking names against the journal
            try: hostile = int(r[3], 16) & 0x40
            except ValueError: hostile = 0
            if hostile or r[2] == '"Lady Liadrin"':
                s = c['seen'].setdefault(sname, {}); s[sid] = s.get(sid, 0) + 1
        # positions from the advanced-logging block (and the boss's health)
        k = 9 if swing else 12
        if len(r) > k + 17 and '-' in r[k]:
            info = r[k]
            own = r[k + 1]
            if own and own.startswith('Player-') and not info.startswith('Player-'): self.pets[info] = own
            try:
                if info.startswith('Player-'): Parser.add_pt(c['track'].setdefault(info, []), t, float(r[k + 14]), float(r[k + 15]))
                elif self.BOSS in info:
                    hp, mx = float(r[k + 2]), float(r[k + 3])
                    if mx > 0: c['hp'] = hp / mx
                elif self.BEETLE in info:             # a Bomber Beetle's last known spot (for where it blew up)
                    c['bpos'][info.rsplit('-', 1)[-1]] = (t, float(r[k + 14]), float(r[k + 15]))
            except ValueError: pass
        # ---- an egg that finished hatching (killing it in time stops the cast), and who dispelled each Overwhelming Fear
        if sname == 'Hatch' and ev == 'SPELL_CAST_SUCCESS':
            c['hatched'].append(t); return
        if ev == 'SPELL_DISPEL' and len(r) > 13 and dst.startswith('Player-'):   # a player cleansing a debuff off a player
            o = self.owner(src) or (src if src.startswith('Player-') else None)
            if o: c['dispels'].append((t, o, dst, r[13].strip('"')))
            return
        # ---- boss casts (first event of each cast; a cast's START and SUCCESS a few seconds apart count once)
        if ev in ('SPELL_CAST_START', 'SPELL_CAST_SUCCESS') and hostile_src and sname in self.CASTS:
            ts_ = c['casts'].setdefault(sname, [])
            if not ts_ or t - ts_[-1] > 4.0: ts_.append(t)
            return
        # ---- debuffs on players (and Dark Frenzy on adds)
        if ev.startswith('SPELL_AURA'):
            if sname in self.NPC_AURAS and not dst.startswith('Player-'):     # [aura, add, add id, on, off]
                key = (sname, dst)
                if ev == 'SPELL_AURA_APPLIED':
                    a = c['npcOpen'][key] = [sname, r[6].strip('"'), dst.rsplit('-', 1)[-1], t, None]; c['npc'].append(a)
                elif ev == 'SPELL_AURA_REMOVED' and key in c['npcOpen']:
                    c['npcOpen'].pop(key)[4] = t
                return
            # every beetle explosion puts a Digestive Juices stack on the raid and resets its timer: keep each explosion
            # with the highest stack it took anyone to (lines under 0.3 s apart are one explosion)
            if sname == 'Digestive Juices' and dst.startswith('Player-') and ev in ('SPELL_AURA_APPLIED', 'SPELL_AURA_APPLIED_DOSE'):
                try: n = int(r[-1]) if ev == 'SPELL_AURA_APPLIED_DOSE' else 1
                except ValueError: n = 1
                j = c['juice']
                if j and t - j[-1][0] < 0.3: j[-1][1] = max(j[-1][1], n)
                else: j.append([t, n])
            if sname not in self.AURAS or not dst.startswith('Player-'): return   # any source: Light's Embrace may be logged as the player's own
            key = (sname, dst)
            if ev == 'SPELL_AURA_APPLIED':
                if key in c['open']: self.shut(c, key, t)
                c['open'][key] = [sname, dst, t, None, 1, src.rsplit('-', 1)[-1] if '-' in src else '']
            elif ev in ('SPELL_AURA_APPLIED_DOSE', 'SPELL_AURA_REFRESH'):
                a = c['open'].get(key)
                if not a: a = c['open'][key] = [sname, dst, t, None, 1, src.rsplit('-', 1)[-1] if '-' in src else '']
                if ev == 'SPELL_AURA_APPLIED_DOSE':
                    try: a[4] = max(a[4], int(r[-1]))
                    except ValueError: a[4] += 1
            elif ev == 'SPELL_AURA_REMOVED' and key in c['open']:
                self.shut(c, key, t)
            return
        # ---- hits on players, and damage / healing done
        if ev.endswith('_DAMAGE') or ev.endswith('_MISSED'):
            amt = 0.0
            if ev.endswith('_DAMAGE'):
                try: amt = float(r[k + 19]) - max(float(r[k + 21]), 0) + max(float(r[k + 25]), 0)
                except (ValueError, IndexError): amt = 0.0
            elif len(r) > k + 2 and r[k] == 'ABSORB':      # fully soaked by a shield: still damage taken (or done)
                try: amt = float(r[k + 2])
                except ValueError: amt = 0.0
            if dst.startswith('Player-') and hostile_src:
                c['lasthit'][dst] = (t, sname)
                if sname in self.HITS:
                    c['hits'].append((t, dst, sname, amt, Parser.pos_at(c['track'].get(dst, []), t)))
            elif amt > 0 and not dst.startswith('Player-'):
                try: hostile_dst = not (int(r[7], 16) & 0x10)
                except ValueError: hostile_dst = False
                o = self.owner(src)
                if hostile_dst and o: c['dmg'][o] = c['dmg'].get(o, 0) + amt
            return
        if ev in ('SPELL_HEAL', 'SPELL_PERIODIC_HEAL'):
            try: val = float(r[k + 19]) - max(float(r[k + 21]), 0)
            except (ValueError, IndexError): val = 0.0
            o = self.owner(src)
            if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            return
        if ev == 'SPELL_ABSORBED':           # shields: credited to whoever cast the shield
            a = 9 if (r[9].startswith('Player-') or r[9].startswith('Creature-') or r[9].startswith('Pet-')) else 12
            try:
                o = self.owner(r[a]); val = float(r[a + 7])
                if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            except (ValueError, IndexError): pass

    def beetles(self, p, nm):
        """Each Bomber Beetle: [spawn, end, outcome, stack, [x, y], nearest player]. Gut Reaction is on it from spawn (30 s);
        it exploded if Digestive Juices went out as Gut Reaction ended: 'contact' when that was early (someone reached it),
        'timer' when it ran out. 'killed' died without exploding; 'alive' was still up when the pull ended. stack = the raid's
        Digestive Juices stack that explosion caused (1 is fine). Position: its last known spot, if seen in its final 3 s;
        nearest player: whoever stood closest to that spot (within 6 yd), the likely one who touched it."""
        dead = {i for _, i, s in p['npcDied'] if s == 'Bomber Beetle'}
        out = []
        for a, s, i, on, off in p['npc']:
            if a != 'Gut Reaction': continue
            boom = next((j for j in p['juice'] if off is not None and off - 0.05 <= j[0] <= off + 0.5), None)
            outcome = ('contact' if off - on < 29 else 'timer') if boom else 'killed' if i in dead else 'alive'
            bp = p['bpos'].get(i)
            pos = [round(bp[1], 2), round(bp[2], 2)] if bp and off is not None and off - 3 <= bp[0] <= off + 0.5 else None
            near = None
            if pos and boom:
                best = None
                for g, tr in p['track'].items():
                    q = Parser.pos_at(tr, off)
                    if q:
                        d = ((q[0] - pos[0]) ** 2 + (q[1] - pos[1]) ** 2) ** 0.5
                        if d <= 6 and (best is None or d < best[0]): best = (d, g)
                near = nm(best[1]) if best else None
            out.append([round(on, 2), round(off, 2) if off is not None else None, outcome, boom[1] if boom else 0, pos, near])
        return sorted(out, key=lambda b: b[0])

    def shut(self, c, key, t):
        a = c['open'].pop(key); a[3] = t
        c['auras'].setdefault(a[0], []).append(a)

    def close(self, r):
        c = self.cur
        if r: c['kill'], c['dur'] = r[5] == '1', int(r[6]) / 1000
        else: c['dur'] = c['lastT']
        c['ended'] = True
        self.pulls.append(c); self.finished += 1
        self.cur = None

    def build(self, log_name):
        nm = lambda g: self.names.get(g, g)
        pulls = self.pulls + ([self.cur] if self.cur else [])
        for p in pulls:
            for g, sp in p['specGuid'].items():
                if g in self.names: self.specs[self.names[g]] = sp
        out = []
        for n, p in enumerate(pulls, 1):
            stop = p['dur'] if p['ended'] else p['lastT']
            r2 = lambda v: round(v, 2) if v is not None else None
            # debuffs: name -> [player, on, off (null = still on at the end), top stack, source id]
            auras = {}
            for a in [x for v in p['auras'].values() for x in v] + list(p['open'].values()):
                auras.setdefault(a[0], []).append([nm(a[1]), r2(a[2]), r2(a[3]), a[4], a[5],
                                                   Parser.pos_at(p['track'].get(a[1], []), a[3] if a[3] is not None else stop)])
            for v in auras.values(): v.sort(key=lambda x: x[1])
            deaths = []
            for t, g in sorted(p['died']):
                lh = p['lasthit'].get(g)
                back = next((q[0] for q in p['track'].get(g, []) if q[0] > t + 1.0), None)
                deaths.append([round(t, 2), nm(g), lh[1] if lh and t - lh[0] <= 5 else None, r2(back)])
            out.append({'n': n, 'enc': p['enc'], 'clock': p['clock'], 'date': p['date'], 'diff': p['diff'], 'size': p['size'],
                        'dur': round(stop, 1), 'kill': p['kill'], 'live': not p['ended'], 'hp': r2(p['hp']) if p['hp'] is not None else None,
                        'casts': {k: [round(t, 2) for t in v] for k, v in p['casts'].items()},
                        'auras': auras,
                        'hits': [[round(t, 2), nm(g), s, round(a), q] for t, g, s, a, q in p['hits']],
                        'adds': [[round(t, 2), i, s] for t, i, s in p['npcDied']],
                        'npc': [[a, s, i, round(on, 2), r2(off)] for a, s, i, on, off in p['npc']],
                        'beetles': self.beetles(p, nm),
                        'hatched': [round(t, 2) for t in p['hatched']],
                        'dispels': [[round(t, 2), nm(a), nm(b), s] for t, a, b, s in p['dispels']],   # [time, dispeller, target, debuff]
                        'grasp': {nm(g): round(v) for g, v in p['grasp'].items()},
                        'deaths': deaths, 'seen': {s: v for s, v in p['seen'].items()},
                        'dmg': {nm(g): round(v) for g, v in p['dmg'].items()}, 'heal': {nm(g): round(v) for g, v in p['heal'].items()}})
        return {'log': log_name, 'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'tz': self.tz, 'pulls': out, 'specs': dict(self.specs)}


# ---- the other bosses of the tier: one parser, set up per boss by ability NAME (checked against Taylor's logs,
# Aug-Oct 2026). A key that starts with '#' is a spell ID instead, for abilities whose name is shared by two spells
# (e.g. Catalytic Bile: the soak and the raid-wide hit when nobody soaked it). The pages do the scoring, mechanic by mechanic.
#   casts:  boss / add casts kept as times (and who a cast landed on)  kicks: casts that should be interrupted
#   auras:  debuffs on players (on, off, top stack, stack when it ended, where the player was when it went on and off)
#   npc:    buffs and debuffs on NPCs (on, off, top stack, where the NPC was)
#   hits:   damage to players kept one by one, with where the player stood
#   pulses: raid-wide hits kept as one line per hit (time, players hit, damage); 'aura:Name' = a debuff going out
#   adds:   NPCs whose spawns and deaths (with where they died) are kept
FAIL_BOSSES = [
    dict(slug='nekzali', name="Nek'zali", enc='3470', boss={'259927': "Nek'zali"},
         casts={'Invoke', 'Soulcoil Ignition', 'Uncoiled Rage', 'Ritual of Awakening', 'Hungering Pyre', 'Essence Rend',
                'Possession Barrage', 'Soul Transfer', 'Grasping Depths'},
         kicks={"Soulcoiler's Curse"},
         auras={'Soulcoiled', 'Invoke', 'Swirling Spirit', '#1287434', 'Ritual Burn', 'Hollowing Strikes', 'Possession Barrage',
                'Slithering Flame'},
         npc={'Soulcoil Ignition', 'Vessel of Awakening', 'Uncoiled Rage', 'Ritual of Awakening', 'Uncoiling', 'Gravebound Advance',
              'Tether of Awakening'},
         hits={'Latent Cultist', 'Anguished Echoes', 'Soul Transfer', 'Swirling Spirit', 'Soulcoil Well', 'Deadly Venom', 'Hungering Pyre'},
         pulses={'Soulcoil Rite', 'Possession Barrage', 'Hungering Pyre'},
         adds={'Restless Amani', 'Echo of Jawae', 'Drowned Echo'}),
    dict(slug='entombed-sentinels', name='Entombed Sentinels', enc='3445', boss={'258557': 'Breath', '258558': 'Blood'},
         casts={'Vitriolic Stasis', 'Toxic Droplets', 'Venom Coagulation', 'Unstable Miasma', 'Shifting Protovenom', 'Berserk', 'Blighted Blood'},
         kicks=set(),
         auras={'Shifting Protovenom', 'Cultivated Burst', 'Helical Toxins', 'Unstable Miasma', 'Blighted Blood', 'Clinging Murk',
                'Bloodvenom Injection'},
         npc={"Ula'tek's Dominance", 'Berserk', 'Vitriolic Stasis'},
         hits={'Protovenom Eruption', 'Blood Venom', 'Living Venom', 'Deadly Venom', 'Toxic Droplets', 'Unstable Miasma', 'Cultivated Burst'},
         pulses={'Noxious Blast', 'Unstable Miasma', 'Protovenom Eruption', 'Contaminate'},
         adds={'Venom Coagulation'}),
    dict(slug='vashnik', name='Vashnik', enc='3455', boss={'259181': 'Vashnik'},
         casts={'Imbibe', 'Malignance', 'Malignant Burst', 'Malignant Catalyst', 'Plague Froth', 'Adaptive Infection', 'Dripping Fangs'},
         kicks=set(),
         auras={'#1281913', 'Caustic Surge', 'Exploding Infection', 'Stygian Infection', 'Siphoning Infection', 'Dripping Fangs'},
         npc={'Imbibe', 'Flame Infusion', 'Shadow Infusion', 'Blood Infusion', 'Hardened Venom'},
         hits={'Plague Wave', 'Plague Froth', 'Stygian Burst', 'Virulent Fumes', 'Deadly Venom', 'Umbral Ejection', '#1282602'},
         pulses={'Malignance', 'Malignant Burst', '#1282616', '#1282602', 'Caustic Surge', 'Caustic Explosion'},
         adds={'Burning Venom', 'Shrouded Venom', 'Clotting Venom', 'Malignant Totem'}),
    dict(slug='lost-explorers', name='The Lost Explorers', enc='3497', boss={'261835': 'Nama', '261843': 'Iku', '261848': 'Gebbo'},
         casts={'Final Ascension', "Mor'zahi's Command", 'Explosive Surprise', 'Mighty Thud', 'Frostfire Volley', 'Shell Spin',
                'Throw Junk', 'Mushroom Toss', 'Blink Nova'},
         kicks={'Icebound Flames'},
         auras={'Splinters', 'Shell Spin', 'Burning Flames', 'Piercing Frost', 'Shredding Shards', 'Steady Strikes', 'Explosive Surprise',
                'Mighty Thud', 'Icebound Flames'},
         npc={'United Defense', 'Relentless Escalation', 'Smashing Shovel', 'Final Ascension', "Mor'zahi's Command", 'Disgusting Fish'},
         hits={'Blast Wave', 'Evil Eyes', 'Fungal Burst', 'Spreading Flames', 'Throw Junk', 'Concussive Blast', 'Fire Patch',
               'Frost Patch', 'Shell Spin', 'Deadly Venom', 'Aftershock', 'Frostfire Volley', '#1300237'},
         pulses={'Final Ascension', 'Relic Rupture', 'Elemental Explosion', 'Cataclysmic Invocation', 'Fishy Feedback', '#1300237'},
         adds=set()),
    dict(slug='coiled-altar', name='The Coiled Altar', enc='3429', boss={'257911': "Zul'jan", '259854': 'Malacrass'},   # Zul'jan is logged as a Vehicle
         casts={'Dreadmarch', 'Guillotine', 'Grim Guillotine', 'Spiritcackle', 'Soulbinding', 'Toxic Deluge', 'Gloombomb', 'Sever',
                'Soul Sever', 'Blighted Sever', 'Venomfang'},
         kicks={'Wail of Terror', 'Eternal Nightfall'},
         auras={'Dreadmarch', 'Gravebound', 'Gloombomb', 'Volatile Venom', 'Sever', 'Soul Sever', 'Blighted Sever', '#1283485',
                '#1307425', '#1299266', '#1307652', 'Venomfang', 'Wail of Terror', 'Venom Rupture'},
         npc={'Consumed by Resentment', 'Veil of Twilight', 'Soulbinding', 'Soul Sever', 'Blighted Sever'},   # Severs: on the ghosts
         fixate={'Unnerving Fixation'},   # which Manifestation of Dread chases which player
         hits={'Axegrinder', 'Noxious Ground', 'Defiled Ground', "Widow's Kiss", "Death's Embrace", 'Gloombomb', 'Volatile Venom',
               'Sever', 'Soul Sever', 'Blighted Sever', 'Deadly Venom', 'Unworthy', '#1297906', '#1283594', '#1299296'},
         pulses={'Execution', 'Grim Execution', 'Reclaim Essence', 'Eternal Nightfall', '#1283594', '#1299296', 'Spirit Erasure'},
         adds={'Spiteful Soulcoiler', 'Manifestation of Dread'}),
    dict(slug='ulatek', name="Ula'tek", enc='3492', boss={'257758': "Ula'tek"},   # logged as a Vehicle (268956 is a short-lived copy)
         casts={'Rage of the Shackled', 'Call of the Serpent', "Serpent's Bite", 'Circling Prey', 'Spectral Coils', 'Caustic Waves',
                'Grasping Fangs', "Mother's Wrath"},
         kicks={'Malice', 'Anguished Cry', 'Vicious Echoes'},
         auras={'Calcified Corpse', "Serpent's Bite", 'Volatile Purge', 'Soul Constrictor', 'Malignant Shell', 'Noxious Shell',
                'Grasping Fangs', 'Blight Vein', 'Stone Venom', 'Caustic Waves', 'Doomscale Shell'},
         npc={'Fury Unleashed', 'Berserk', 'Boiling Venom', 'Writhing Gestation'},
         hits={'Caustic Waves', 'Falling Debris', 'Virulent Spit', '#1305878', 'Desperate Thrash', '#1304270', 'Deadly Venom',
               'Petrifying Sting'},
         pulses={'aura:Putrid Membrane', 'Rattler Slam', 'Unchecked Rage', '#1301122', 'Dread Roar', 'Fury Unleashed', '#1318329',
                 'aura:Soul Constrictor', 'aura:Blight Vein', 'Spectral Coils'},
         adds={'Doomscale Warden', 'Weakened Doomscale', 'Ravenous Doomscale', 'Blightscale Viper', 'Blightscale Shrieker'}),
]


class FailParser:
    """One boss from FAIL_BOSSES, any difficulty: the raw events its page scores mechanic by mechanic (casts and their
    targets, kicks, debuffs with stacks and where they went on and off, hits with positions, raid-wide hits, NPC auras,
    add spawns and deaths, the bosses' positions), plus the standard ones every page has (damage, healing, dispels,
    deaths). Every hostile spell seen is also listed per pull ('seen'), to check the names against real logs."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.ENC = cfg['enc']
        self.pulls, self.cur, self.leftover = [], None, ''
        self.names, self.specs, self.pets = {}, {}, {}
        self.finished = 0
        self.tz = None

    def has_data(self): return bool(self.pulls) or bool(self.cur)

    def feed(self, text):
        text = self.leftover + text
        end = text.rfind('\n')
        if end < 0:
            self.leftover = text; return
        pos = 0
        while pos < end:
            if self.cur is None:                 # skip to the next pull of this boss
                m = text.find('ENCOUNTER_START,' + self.ENC + ',', pos)
                if m < 0 or m >= end: break
                pos = text.rfind('\n', 0, m) + 1
            nl = text.find('\n', pos)
            if nl < 0 or nl > end: nl = end
            try: self.line(text[pos:nl])
            except (IndexError, ValueError): pass
            pos = nl + 1
        self.leftover = text[end + 1:]

    def owner(self, g):
        return g if g.startswith('Player-') else self.pets.get(g)

    def want(self, group, sname, sid):
        s = self.cfg.get(group, ())
        return sname if sname in s else ('#' + sid) if ('#' + sid) in s else None

    def npc_pos(self, c, g, t):
        q = c['npos'].get(g)
        return [round(q[1], 2), round(q[2], 2)] if q and t - q[0] <= 5 else None

    def line(self, l):
        l = l.rstrip('\r')
        sep = l.find('  ')
        if sep < 0: return
        rest = l[sep + 2:]
        ev = rest[:rest.find(',')]
        c = self.cur
        if ev == 'ENCOUNTER_START':
            r = Parser.split(rest)
            if c: self.close(None)
            if r[1] != self.ENC: return
            when = l.split(' ', 1); self.tz = log_tz(l)
            self.cur = dict(enc=r[1], t0=Parser.ts(l), clock=when[1][:8].split('.')[0], date=when[0], diff=int(r[3]), size=int(r[4]),
                            dur=0.0, kill=False, ended=False, lastT=0.0, hp={}, track={}, specGuid={}, died=[], npcDied=[],
                            dmg={}, heal={}, lasthit={}, casts={}, ctgt={}, auras={}, open={}, hits=[], npc=[], npcOpen={}, seen={},
                            kicks=[], kickOpen={}, pulses={}, dispels=[], npos={}, spawns=[], spawnG={}, diedG=set(), btrack={},
                            fixate=[], fixOpen={})
            return
        if c is None: return
        t = Parser.ts(l) - c['t0']; c['lastT'] = t
        if ev == 'ENCOUNTER_END':
            r = rest.split(',')
            if r[1] == self.ENC: self.close(r)
            return
        if ev == 'COMBATANT_INFO':
            r = rest.split(',', 27)
            try: c['specGuid'][r[1]] = int(r[25])
            except (ValueError, IndexError): pass
            return
        r = Parser.split(rest)
        if ev == 'UNIT_DIED':
            if len(r) > 5:
                if r[5].startswith('Player-'): c['died'].append((t, r[5]))
                elif r[5].startswith(('Creature-', 'Vehicle-')) and r[6].strip('"') in self.cfg['adds'] and r[5] not in c['diedG']:
                    c['diedG'].add(r[5])      # the log can say a unit died twice
                    c['npcDied'].append((t, r[5].rsplit('-', 1)[-1], r[6].strip('"'), self.npc_pos(c, r[5], t), 'died'))
            return
        if not (ev.startswith('SPELL_') or ev.startswith('SWING_') or ev.startswith('RANGE_')): return
        src, dst = r[1], r[5]
        if src.startswith('Player-'): self.names[src] = r[2].split('-')[0]
        if dst.startswith('Player-'): self.names[dst] = r[6].split('-')[0]
        swing = ev.startswith('SWING_')
        sid, sname = ('', 'Melee') if swing else (r[9], r[10].strip('"'))
        if ev == 'SPELL_SUMMON':
            o = self.owner(src)
            if o: self.pets[dst] = o
            elif r[6].strip('"') in self.cfg['adds']:
                c['spawns'].append((t, r[6].strip('"'), dst.rsplit('-', 1)[-1])); c['spawnG'][dst] = (t, r[6].strip('"'))
            return
        hostile_src = not src.startswith('Player-') and not self.owner(src)
        if not swing and ev != 'SPELL_ABSORBED':   # every enemy spell in the pull, for checking names against the journal
            try: hostile = int(r[3], 16) & 0x40
            except ValueError: hostile = 0
            if hostile:
                s = c['seen'].setdefault(sname, {}); s[sid] = s.get(sid, 0) + 1
        # positions from the advanced-logging block: players, every NPC's last spot, the bosses' path and health
        k = 9 if swing else 12
        if len(r) > k + 17 and '-' in r[k]:
            info = r[k]
            own = r[k + 1]
            if own and own.startswith('Player-') and not info.startswith('Player-'): self.pets[info] = own
            try:
                x, y = float(r[k + 14]), float(r[k + 15])
                if info.startswith('Player-'): Parser.add_pt(c['track'].setdefault(info, []), t, x, y)
                elif info.startswith(('Creature-', 'Vehicle-')) and not own.startswith('Player-'):
                    c['npos'][info] = (t, x, y)
                    b = self.cfg['boss'].get(info.split('-')[5])
                    if b:
                        hp, mx = float(r[k + 2]), float(r[k + 3])
                        if mx > 0: c['hp'][b] = hp / mx
                        bt = c['btrack'].setdefault(b, [])
                        if not bt or t - bt[-1][0] >= 1.0: bt.append((t, x, y))
            except (ValueError, IndexError): pass
        if ev == 'SPELL_DISPEL' and len(r) > 13 and dst.startswith('Player-'):   # a player cleansing a debuff off a player
            o = self.owner(src) or (src if src.startswith('Player-') else None)
            if o: c['dispels'].append((t, o, dst, r[13].strip('"')))
            return
        # ---- kicks: each cast that should be interrupted, kicked (by whom) or missed (it went off)
        if ev == 'SPELL_INTERRUPT' and len(r) > 13:
            e = c['kickOpen'].get(dst)
            if e and r[13].strip('"') == e[2] and e[3] in (None, 'missed') and (e[3] is None or t - e[6] < 0.6):
                o = self.owner(src)
                e[3], e[4] = 'kicked', self.names.get(o, o) if o else None
            return
        if hostile_src and sname in self.cfg['kicks']:
            if ev == 'SPELL_CAST_START':
                e = [t, r[2].strip('"'), sname, None, None, src.rsplit('-', 1)[-1], t]
                c['kicks'].append(e); c['kickOpen'][src] = e
            elif ev in ('SPELL_CAST_SUCCESS', 'SPELL_DAMAGE', 'SPELL_PERIODIC_DAMAGE', 'SPELL_AURA_APPLIED'):
                e = c['kickOpen'].get(src)
                if e and e[2] == sname and e[3] is None and t - e[0] > 0.2: e[3], e[6] = 'missed', t
        # ---- boss and add casts (first event of each cast; a cast's START and SUCCESS a few seconds apart count once),
        # and the players a cast landed on
        if ev in ('SPELL_CAST_START', 'SPELL_CAST_SUCCESS') and hostile_src and sname in self.cfg['casts']:
            ts_ = c['casts'].setdefault(sname, [])
            if not ts_ or t - ts_[-1] > 4.0: ts_.append(t)
            if ev == 'SPELL_CAST_SUCCESS' and dst.startswith('Player-'): c['ctgt'].setdefault(sname, []).append((t, dst))
            return
        # ---- debuffs on players, auras on NPCs, and debuffs going out raid-wide ('aura:' pulses)
        if ev.startswith('SPELL_AURA'):
            # an add fixating a player (cfg fixate): [player, on, off, the add's unit id], to tell several adds of one kind apart
            if sname in self.cfg.get('fixate', ()) and dst.startswith('Player-') and not src.startswith('Player-') and not self.owner(src):
                key = (src, dst)
                if ev == 'SPELL_AURA_APPLIED':
                    a = c['fixOpen'][key] = [dst, t, None, src.rsplit('-', 1)[-1]]; c['fixate'].append(a)
                elif ev == 'SPELL_AURA_REMOVED' and key in c['fixOpen']:
                    c['fixOpen'].pop(key)[2] = t
            if not dst.startswith('Player-'):
                if sname in self.cfg['npc'] and not self.owner(dst):     # [aura, unit, unit id, on, off, where the unit was, top stack]
                    key = (sname, dst)
                    if ev == 'SPELL_AURA_APPLIED':
                        a = c['npcOpen'][key] = [sname, r[6].strip('"'), dst.rsplit('-', 1)[-1], t, None, self.npc_pos(c, dst, t), 1]
                        c['npc'].append(a)
                    elif ev == 'SPELL_AURA_APPLIED_DOSE' and key in c['npcOpen']:
                        try: c['npcOpen'][key][6] = max(c['npcOpen'][key][6], int(r[-1]))
                        except ValueError: pass
                    elif ev == 'SPELL_AURA_REMOVED' and key in c['npcOpen']:
                        c['npcOpen'].pop(key)[4] = t
                return
            if ev == 'SPELL_AURA_APPLIED' and ('aura:' + sname) in self.cfg['pulses']:
                self.pulse(c, 'aura:' + sname, t, dst, 0.0)
            name = self.want('auras', sname, sid)
            if not name: return
            key = (name, dst)
            srcname = r[2].strip('"') if r[2] != 'nil' else ''
            here = lambda: Parser.pos_at(c['track'].get(dst, []), t)
            if ev == 'SPELL_AURA_APPLIED':
                if key in c['open']: self.shut(c, key, t, None)
                c['open'][key] = [name, dst, t, None, 1, srcname, here(), None, 1]
            elif ev in ('SPELL_AURA_APPLIED_DOSE', 'SPELL_AURA_REMOVED_DOSE', 'SPELL_AURA_REFRESH'):
                a = c['open'].get(key)
                if not a: a = c['open'][key] = [name, dst, t, None, 1, srcname, here(), None, 1]
                if ev != 'SPELL_AURA_REFRESH':
                    try: a[8] = int(r[-1])
                    except ValueError: a[8] += 1 if ev == 'SPELL_AURA_APPLIED_DOSE' else -1
                    a[4] = max(a[4], a[8])
            elif ev == 'SPELL_AURA_REMOVED' and key in c['open']:
                self.shut(c, key, t, here())
            return
        # ---- hits on players (kept one by one, or as raid-wide pulses), and damage / healing done
        if ev.endswith('_DAMAGE') or ev.endswith('_MISSED'):
            amt = 0.0
            if ev.endswith('_DAMAGE'):
                try: amt = float(r[k + 19]) - max(float(r[k + 21]), 0) + max(float(r[k + 25]), 0)
                except (ValueError, IndexError): amt = 0.0
            elif len(r) > k + 2 and r[k] == 'ABSORB':      # fully soaked by a shield: still damage taken (or done)
                try: amt = float(r[k + 2])
                except ValueError: amt = 0.0
            if dst.startswith('Player-') and (hostile_src or src == '0000000000000000'):
                c['lasthit'][dst] = (t, sname)
                h = self.want('hits', sname, sid)
                if h: c['hits'].append((t, dst, h, amt, Parser.pos_at(c['track'].get(dst, []), t)))
                pk = self.want('pulses', sname, sid)
                if pk and 'PERIODIC' not in ev: self.pulse(c, pk, t, dst, amt)
            elif amt > 0 and not dst.startswith('Player-'):
                try: hostile_dst = not (int(r[7], 16) & 0x10)
                except ValueError: hostile_dst = False
                o = self.owner(src)
                if hostile_dst and o: c['dmg'][o] = c['dmg'].get(o, 0) + amt
            return
        if ev in ('SPELL_HEAL', 'SPELL_PERIODIC_HEAL'):
            if hostile_src and ev == 'SPELL_HEAL':
                pk = self.want('pulses', sname, sid)
                if pk: self.pulse(c, pk, t, dst, 0.0)
                return
            try: val = float(r[k + 19]) - max(float(r[k + 21]), 0)
            except (ValueError, IndexError): val = 0.0
            o = self.owner(src)
            if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            return
        if ev == 'SPELL_ABSORBED':           # shields: credited to whoever cast the shield
            a = 9 if (r[9].startswith('Player-') or r[9].startswith('Creature-') or r[9].startswith('Pet-')) else 12
            try:
                o = self.owner(r[a]); val = float(r[a + 7])
                if o and val > 0: c['heal'][o] = c['heal'].get(o, 0) + val
            except (ValueError, IndexError): pass

    @staticmethod
    def pulse(c, key, t, dst, amt):
        """One raid-wide hit: lines of the same ability under 1 s apart are one hit. [time, players hit, damage]."""
        ps = c['pulses'].setdefault(key, [])
        if ps and t - ps[-1][3] <= 1.0:
            p = ps[-1]; p[3] = t
        else:
            p = [t, set(), 0.0, t]; ps.append(p)
        if dst.startswith('Player-'): p[1].add(dst)
        p[2] += amt

    def shut(self, c, key, t, where):
        a = c['open'].pop(key); a[3] = t; a[7] = where
        c['auras'].setdefault(a[0], []).append(a)

    def close(self, r):
        c = self.cur
        if r: c['kill'], c['dur'] = r[5] == '1', int(r[6]) / 1000
        else: c['dur'] = c['lastT']
        c['ended'] = True
        self.pulls.append(c); self.finished += 1
        self.cur = None

    def build(self, log_name):
        nm = lambda g: self.names.get(g, g)
        pulls = self.pulls + ([self.cur] if self.cur else [])
        for p in pulls:
            for g, sp in p['specGuid'].items():
                if g in self.names: self.specs[self.names[g]] = sp
        out = []
        r2 = lambda v: round(v, 2) if v is not None else None
        for n, p in enumerate(pulls, 1):
            stop = p['dur'] if p['ended'] else p['lastT']
            # debuffs: name -> [player, on, off (null = still on at the end), top stack, source, [x, y] when it went on,
            # [x, y] when it went off, stack when it went off]
            auras = {}
            for a in [x for v in p['auras'].values() for x in v] + list(p['open'].values()):
                auras.setdefault(a[0], []).append([nm(a[1]), r2(a[2]), r2(a[3]), a[4], a[5], a[6], a[7], a[8]])
            for v in auras.values(): v.sort(key=lambda x: x[1])
            deaths = []
            for t, g in sorted(p['died']):
                lh = p['lasthit'].get(g)
                back = next((q[0] for q in p['track'].get(g, []) if q[0] > t + 1.0), None)
                deaths.append([round(t, 2), nm(g), lh[1] if lh and t - lh[0] <= 5 else None, r2(back), Parser.pos_at(p['track'].get(g, []), t)])
            adds = list(p['npcDied'])
            for g, (t, s) in p['spawnG'].items():      # spawned and never died: last seen where (an add that reached its goal vanishes)
                if g in p['diedG']: continue
                q = p['npos'].get(g)
                adds.append((q[0] if q else t, g.rsplit('-', 1)[-1], s, [round(q[1], 2), round(q[2], 2)] if q else None, 'gone'))
            adds.sort(key=lambda a: a[0])
            out.append({'n': n, 'enc': p['enc'], 'clock': p['clock'], 'date': p['date'], 'diff': p['diff'], 'size': p['size'],
                        'dur': round(stop, 1), 'kill': p['kill'], 'live': not p['ended'],
                        'hp': {b: round(v, 3) for b, v in p['hp'].items()},
                        'specs': {nm(g): sp for g, sp in p['specGuid'].items() if g in self.names},   # this pull's specs (who tanked)
                        'casts': {k: [round(t, 2) for t in v] for k, v in p['casts'].items()},
                        'ctgt': {k: [[round(t, 2), nm(g)] for t, g in v] for k, v in p['ctgt'].items()},   # [time, player it landed on]
                        # [start, caster, cast, 'kicked' / 'missed' / null (stopped some other way), kicked by, caster id]
                        'kicks': [[round(e[0], 2), e[1], e[2], e[3], e[4], e[5]] for e in p['kicks']],
                        'auras': auras,
                        'npc': [[a, s, i, round(on, 2), r2(off), q, top] for a, s, i, on, off, q, top in p['npc']],
                        'hits': [[round(t, 2), nm(g), s, round(a), q] for t, g, s, a, q in p['hits']],
                        'pulses': {k: [[round(x[0], 2), sorted(nm(g) for g in x[1]), round(x[2])] for x in v] for k, v in p['pulses'].items()},
                        'spawns': [[round(t, 2), s, i] for t, s, i in p['spawns']],
                        'fixate': [[nm(g), round(on, 2), r2(off), i] for g, on, off, i in p['fixate']],   # [player, on, off, add's unit id]
                        # [time it died (or was last seen), unit id, name, where, 'died' / 'gone' (never died: reached its goal or still up)]
                        'adds': [[round(t, 2), i, s, q, how] for t, i, s, q, how in adds],
                        'btrack': {b: [[round(t, 1), round(x, 1), round(y, 1)] for t, x, y in v] for b, v in p['btrack'].items()},
                        'dispels': [[round(t, 2), nm(a), nm(b), s] for t, a, b, s in p['dispels']],   # [time, dispeller, target, debuff]
                        'deaths': deaths, 'seen': {s: v for s, v in p['seen'].items()},
                        'dmg': {nm(g): round(v) for g, v in p['dmg'].items()}, 'heal': {nm(g): round(v) for g, v in p['heal'].items()}})
        return {'log': log_name, 'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'tz': self.tz, 'pulls': out, 'specs': dict(self.specs)}


class ActivityParser:
    """Everything the raid fought in this log, for the timeline on every boss page: each boss pull (any boss, any
    raid difficulty) and each stretch of trash combat (the raid's players and hostile NPCs hitting each other outside a
    boss pull, in a zone where a raid boss was pulled). Times are UTC seconds, from the offset on the log's lines."""
    GAP, MIN = 8.0, 3.0      # a trash fight ends after 8 s without a hit; fights under 3 s aren't kept
    RAID = {14, 15, 16, 17}  # raid difficulties: Normal, Heroic, Mythic, LFR
    HEAD = re.compile(r'([^,]+),([^,]*),(?:"[^"]*"|nil),(0x[0-9a-fA-F]+),0x[0-9a-fA-F]+,([^,]*),(?:"[^"]*"|nil),(0x[0-9a-fA-F]+)')
    # flags: 0x100 = controlled by a player (players and their pets), 0x7 = in our group (mine / party / raid), 0x40 = hostile, 0x800 = NPC

    def __init__(self):
        self.leftover, self.tz, self.zone, self.last = '', None, None, None
        self.pulls, self.open, self.trash, self.fight = [], None, [], None
        self.zones, self.finished = set(), 0

    def has_data(self): return bool(self.pulls) or bool(self.open)

    def feed(self, text):
        text = self.leftover + text
        end = text.rfind('\n')
        if end < 0:
            self.leftover = text; return
        for l in text[:end].split('\n'):
            try: self.line(l)
            except (IndexError, ValueError): pass
        self.leftover = text[end + 1:]

    def line(self, l):
        sep = l.find('  ')
        if sep < 0: return
        if self.tz is None: self.tz = log_tz(l)
        rest = l[sep + 2:]
        if rest.startswith('SWING_DAMAGE') or rest.startswith('RANGE_DAMAGE') or (rest.startswith('SPELL_') and '_DAMAGE,' in rest[:30]):
            if self.open: return
            m = self.HEAD.match(rest)
            if not m: return
            sf, df = int(m.group(3), 16), int(m.group(5), 16)
            ours = lambda f: f & 0x100 and f & 0x7
            foe = lambda f: f & 0x40 and f & 0x800
            if not ((ours(sf) and foe(df)) or (foe(sf) and ours(df))): return
            t = self.utc(l); self.last = t
            f = self.fight
            if f and t - f[1] <= self.GAP: f[1] = t
            else:
                self.close_fight(); self.fight = [t, t, self.zone]
            return
        if rest.startswith('ENCOUNTER_START,'):
            r = Parser.split(rest); t = self.utc(l)
            self.close_fight()
            if int(r[3]) not in self.RAID: return       # dungeons (Mythic+ etc.) don't belong on a raid timeline
            self.open = [t, None, r[1], r[2], int(r[3]), False]
            self.zones.add(self.zone)
        elif rest.startswith('ENCOUNTER_END,') and self.open:
            r = rest.split(',')
            self.open[1], self.open[5] = int(r[6]) / 1000, r[5] == '1'
            self.pulls.append(self.open); self.open = None; self.finished += 1
        elif rest.startswith('ZONE_CHANGE,'):
            self.close_fight(); self.zone = Parser.split(rest)[2]   # by name: the log gives every zone the same number (Silvermoon City, housing...)

    def utc(self, l):
        return Parser.ts(l) - (self.tz or 0) * 3600

    def close_fight(self):
        if self.fight: self.trash.append(self.fight)
        self.fight = None

    def build(self, log_name):
        pulls = [[round(s, 1), round(d, 1), e, n, df, k, False] for s, d, e, n, df, k in self.pulls]
        if self.open:      # a pull still going: as long as the log so far
            s = self.open[0]; pulls.append([round(s, 1), round(max((self.last or s) - s, 0), 1), self.open[2], self.open[3], self.open[4], False, True])
        trash = [[round(a, 1), round(b, 1)] for a, b, z in self.trash + ([self.fight] if self.fight else []) if z in self.zones and b - a >= self.MIN]
        return {'log': log_name, 'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'tz': self.tz,
                'pulls': pulls, 'trash': trash}

def activity_entry(data, f):
    """nights.json entry for an activity file (its date is the logger's date of the first pull or fight)."""
    first = min([p[0] for p in data['pulls']] + [a for a, _ in data['trash']], default=None)
    d = datetime.fromtimestamp(first + (data['tz'] or 0) * 3600, timezone.utc) if first is not None else None
    return {'file': f, 'log': data['log'], 'date': f'{d.month}/{d.day}/{d.year}' if d else None, 'updated': data['updated']}


# ---- GitHub ----
def get_token():
    tok = os.environ.get('GITHUB_TOKEN')
    if tok: return tok.strip()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f: return f.read().strip()
    print('Paste your GitHub token (it will not show as you type), then press Return:')
    tok = getpass.getpass('> ').strip()
    with open(TOKEN_FILE, 'w') as f: f.write(tok)
    os.chmod(TOKEN_FILE, 0o600)
    print(f'Saved to {TOKEN_FILE}')
    return tok

def gh(method, url, token, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                                          'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'taynalysis-uploader'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b'{}')

def fmt_size(n):
    return f'{n / 1048576:.1f} MB' if n >= 1048576 else f'{n / 1024:.0f} KB' if n >= 1024 else f'{n} B'

def bar(done, total, label='', width=28):
    """Draw a one-line progress bar in Terminal (redrawn in place)."""
    frac = min(1.0, done / total) if total else 1.0
    filled = int(width * frac)
    sys.stdout.write(f'\r  [{"#" * filled}{"-" * (width - filled)}] {frac * 100:5.1f}%  {label}\033[K')
    sys.stdout.flush()

class Uploader:
    """Publishes the night file and nights.json together as ONE commit (one Pages build)."""
    live = False
    where = f'github.com/{REPO}'

    def __init__(self, token):
        self.token = token

    def read_text(self, path):
        """(text, sha) of a file in the repo; ('', None) when it doesn't exist yet."""
        try:
            cur = self.api('GET', f'contents/{path}?ref={BRANCH}')
            return base64.b64decode(cur['content']).decode('utf-8'), cur['sha']
        except urllib.error.HTTPError as e:
            if e.code == 404: return '', None
            raise

    def write_text(self, path, text, sha, message):
        body = {'message': message, 'content': base64.b64encode(text.encode('utf-8')).decode(), 'branch': BRANCH}
        if sha: body['sha'] = sha
        self.api('PUT', f'contents/{path}', body)

    def api(self, method, path, body=None):
        return gh(method, f'https://api.github.com/repos/{REPO}/{path}', self.token, body)

    def publish(self, parts, message):
        """parts: list of (night_file, data, index_path, index_entry). Everything goes up as one commit."""
        texts = [(f, json.dumps(d, separators=(',', ':'), ensure_ascii=False)) for f, d, _, _ in parts]
        steps = 5 + len(parts) * 2
        for attempt in range(3):
            bar(0, steps, 'Checking the latest commit')
            head = self.api('GET', f'git/ref/heads/{BRANCH}')['object']['sha']
            bar(1, steps, 'Reading the latest commit')
            base_tree = self.api('GET', f'git/commits/{head}')['tree']['sha']
            step = 2
            for _, _, ip, entry in parts:
                bar(step, steps, f'Reading {ip}'); step += 1
                try:
                    cur = self.api('GET', f'contents/{ip}?ref={head}')
                    idx = json.loads(base64.b64decode(cur['content']).decode())
                except urllib.error.HTTPError as e:
                    if e.code != 404: raise
                    idx = None
                texts.append((ip, json.dumps(merge_index(idx, entry), indent=1, ensure_ascii=False)))
            entries, total = [], 0
            for path, text in texts[:len(parts) * 2]:
                bar(min(step, steps - 3), steps, f'Uploading {path} ({fmt_size(len(text.encode()))})'); step += 1
                total += len(text.encode())
                blob = self.api('POST', 'git/blobs', {'content': text, 'encoding': 'utf-8'})
                entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
            bar(steps - 3, steps, 'Building the file tree')
            tree = self.api('POST', 'git/trees', {'base_tree': base_tree, 'tree': entries})
            bar(steps - 2, steps, 'Creating the commit')
            commit = self.api('POST', 'git/commits', {'message': message, 'tree': tree['sha'], 'parents': [head]})
            bar(steps - 1, steps, 'Publishing the commit')
            try:
                self.api('PATCH', f'git/refs/heads/{BRANCH}', {'sha': commit['sha'], 'force': False})
                bar(steps, steps, f'Done · {fmt_size(total)} uploaded')
                print()
                return
            except urllib.error.HTTPError as e:
                if e.code == 422 and attempt < 2:            # someone else committed meanwhile; rebuild on top of it
                    print(); print(f'[{stamp()}] GitHub has a newer commit; rebuilding on top of it')
                    texts = texts[:len(parts)]
                    continue
                raise


class ServerUploader:
    """Sends files straight to your server: no commits, no build limit, live the moment they land."""
    live = True
    where = SITE_URL.split('//', 1)[1]
    keep_dir = None      # repo folder: every raid data file sent is also saved here, so the repo stays a full copy of the server's data

    def __init__(self, token):
        self.token = token

    def keep(self, path, data):
        """Save a sent data file into keep_dir too (same bytes the server has). A failure here never stops an upload."""
        if not self.keep_dir: return
        full = os.path.join(self.keep_dir, path)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full + '.tmp', 'wb') as fh: fh.write(data)
            os.replace(full + '.tmp', full)
        except OSError as e:
            print(f'\n[{stamp()}] Uploaded, but could not save the copy in {full}: {e}')

    def request(self, method, url, data=None, auth=True):
        headers = {'User-Agent': 'taynalysis-uploader'}
        if auth: headers['Authorization'] = f'Bearer {self.token}'
        if data is not None: headers['Content-Type'] = 'application/octet-stream'
        req = urllib.request.Request(url, method=method, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    def check(self):
        """True if the server accepts this token."""
        try:
            self.request('GET', f'{SITE_URL}/api/check'); return True
        except urllib.error.HTTPError as e:
            if e.code == 401: return False
            raise

    def put(self, path, data):
        self.request('PUT', f'{SITE_URL}/api/files/{path}', data)

    def get_text(self, path):
        """A file as the site serves it right now, or None if it isn't there yet."""
        try:
            return self.request('GET', f'{SITE_URL}/{path}?t={int(time.time() * 1000)}', auth=False).decode('utf-8')
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            raise

    def read_text(self, path):
        return self.get_text(path) or '', None

    def write_text(self, path, text, _sha, _message):
        self.put(path, text.encode('utf-8'))

    def publish(self, parts, message):
        """parts: list of (night_file, data, index_path, index_entry). Night file first, then its index."""
        steps, step, total = len(parts) * 3, 0, 0
        for f, d, ip, entry in parts:
            text = json.dumps(d, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
            bar(step, steps, f'Uploading {f} ({fmt_size(len(text))})'); step += 1
            self.put(f, text); total += len(text); self.keep(f, text)
            bar(step, steps, f'Reading {ip}'); step += 1
            cur = self.get_text(ip)
            try: idx = json.loads(cur) if cur else None
            except ValueError: idx = None
            itext = json.dumps(merge_index(idx, entry), indent=1, ensure_ascii=False).encode('utf-8')
            bar(step, steps, f'Uploading {ip}'); step += 1
            self.put(ip, itext); total += len(itext); self.keep(ip, itext)
        bar(steps, steps, f'Done · {fmt_size(total)} uploaded')
        print()


def get_server_token(force_prompt=False):
    tok = os.environ.get('TAYNALYSIS_UPLOAD_TOKEN')
    if tok and not force_prompt: return tok.strip()
    if os.path.exists(SERVER_TOKEN_FILE) and not force_prompt:
        with open(SERVER_TOKEN_FILE) as f: return f.read().strip()
    print('Paste the upload token the server setup printed (it will not show as you type), then press Return:')
    tok = getpass.getpass('> ').strip()
    with open(SERVER_TOKEN_FILE, 'w') as f: f.write(tok)
    os.chmod(SERVER_TOKEN_FILE, 0o600)
    print(f'Saved to {SERVER_TOKEN_FILE}')
    return tok

def connect_server():
    """ServerUploader with a token the server accepts, asking for it again if the saved one is wrong."""
    up = ServerUploader(get_server_token())
    for attempt in range(3):
        try:
            ok = up.check()
        except urllib.error.URLError as e:
            if isinstance(getattr(e, 'reason', None), ssl.SSLError):
                sys.exit('Python could not verify the server\'s certificate. If you installed Python from python.org, '
                         'open Applications > Python 3.x and run "Install Certificates.command", then try again. '
                         'If the server was set up in the last few minutes, wait a minute: its certificate may still be on the way.')
            sys.exit(f'Could not reach {SITE_URL}: {getattr(e, "reason", e)}')
        if ok:
            print(f'[{stamp()}] Connected to {up.where}')
            return up
        print(f'[{stamp()}] The server did not accept that upload token.')
        if attempt == 2: sys.exit('Run the uploader again and paste the token exactly as the server setup printed it.')
        up = ServerUploader(get_server_token(force_prompt=True))

def site_files(root, with_data):
    """Site files in the repo folder, as paths relative to it (the server/ folder and dot files stay home)."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith('.') and x not in ('server', '__pycache__'))
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if f.startswith('.') or is_groups_file(f) or not (ext in PAGE_EXT or (with_data and ext in DATA_EXT)): continue
            out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, '/'))
    return out

def send_site(up, root, with_data):
    """Send page files that changed since the last send (all site files, data included, with with_data)."""
    try: sent = json.load(open(SYNC_FILE))
    except (OSError, ValueError): sent = {}
    sent = sent if isinstance(sent, dict) and sent.get('site') == SITE_URL else {'site': SITE_URL, 'files': {}}
    files = site_files(root, with_data)
    todo = [f for f in files if with_data or sent['files'].get(f) != [os.path.getsize(os.path.join(root, f)), int(os.path.getmtime(os.path.join(root, f)))]]
    if not todo: return 0
    total = 0
    for i, f in enumerate(todo):
        full = os.path.join(root, f)
        data = open(full, 'rb').read()
        bar(i, len(todo), f'Sending {f} ({fmt_size(len(data))})')
        up.put(f, data); total += len(data)
        sent['files'][f] = [len(data), int(os.path.getmtime(full))]
    bar(len(todo), len(todo), f'Done · {len(todo)} file{"" if len(todo) == 1 else "s"}, {fmt_size(total)}')
    print()
    with open(SYNC_FILE, 'w') as fh: json.dump(sent, fh, indent=1)
    return len(todo)


def backup_groups(up, root):
    """Copy the group picks saved on the boss pages (server) into the repo folder, so the repo stays a full backup."""
    n = 0
    for team, _ in TEAMS:
        for page, sets in group_files().items():
          for s in sets:
            rel = f'data/{team}/{TIER}/{page}/groups-{s}.json'
            try: text = up.get_text(rel)
            except (urllib.error.URLError, OSError): continue
            if text is None: continue
            full = os.path.join(root, rel)
            try:
                if os.path.exists(full) and open(full, encoding='utf-8').read() == text: continue
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w', encoding='utf-8') as fh: fh.write(text)
                n += 1
            except OSError as e:
                print(f'[{stamp()}] Could not save the group picks backup {full}: {e}')
    if n: print(f'[{stamp()}] Saved {n} group / flag file{"" if n == 1 else "s"} from the site into {root}.')


# ---- "now pulling" (Taylor 2026-11-02): the moment a supported boss is pulled, data/<team>/<tier>/live.json says so, and
# again when the pull ends, so open pages can follow the raid to that boss (only viewers who aren't using the page) ----
LIVE_RE = re.compile(r'^(\d+)/(\d+)/(\d+) (\d+):(\d+):(\d+)[^ ]*  ENCOUNTER_(START|END),(\d+),"([^"]*)",(\d+)', re.M)
def live_pages():
    """encounter id -> the boss's page (file name without .html, as in shared.js BOSSES)."""
    return {'3420': 'sszorak', '3421': 'fangs', '3513': 'kithix', **{b['enc']: b['slug'] for b in FAIL_BOSSES}}

def live_note(up, text, state):
    """Send live.json when this new log text starts or ends a supported boss's pull (only lines from the last 15 minutes:
    reading an older part of the log never announces a pull)."""
    last = None
    for m in LIVE_RE.finditer(text):
        if m.group(8) in live_pages(): last = m
    if not last: return
    mo, d, y, hh, mi, ss = (int(last.group(i)) for i in range(1, 7))
    try: when = datetime(y, mo, d, hh, mi, ss).timestamp()
    except ValueError: return
    if time.time() - when > 900: return
    start = last.group(7) == 'START'
    note = {'active': start, 'page': live_pages()[last.group(8)], 'boss': last.group(9), 'diff': int(last.group(10)), 'at': int(time.time())}
    if note == state.get('note') or (state.get('note') or {}).get('active') == start and (state.get('note') or {}).get('page') == note['page']: return
    state['note'] = note
    try: up.put(f'{os.path.dirname(NIGHT_DIR)}/live.json', json.dumps(note).encode('utf-8'))
    except Exception as e: print(f'[{stamp()}] Could not send the now-pulling note: {e}')


def date_key(d):
    m, dd, y = (d or '01/01/1970').split('/')
    return f'{y}{int(m):02d}{int(dd):02d}'

def best_hp(pulls):
    # lowest boss health on a wipe (Twin Fangs pulls carry one health per boss: use the higher of the two)
    hps = [max(p['hp'].values()) if isinstance(p.get('hp'), dict) and p['hp'] else p.get('hp') for p in pulls if not p['kill']]
    hps = [h for h in hps if isinstance(h, (int, float))]
    return min(hps) if hps else None

def night_entry(data, night_file):
    pulls = data['pulls']
    first = pulls[0] if pulls else {}
    # byDiff: pulls, kills and best health per difficulty, for the home page (a night can hold more than one difficulty)
    by_diff = {}
    for d in sorted({p.get('diff') for p in pulls if p.get('diff') is not None}):
        ps = [p for p in pulls if p.get('diff') == d]
        by_diff[str(d)] = {'pulls': len(ps), 'kills': sum(1 for p in ps if p['kill']), 'best': best_hp(ps)}
    return {'file': night_file, 'log': data['log'], 'date': first.get('date'), 'diff': first.get('diff'),
            'pulls': len(pulls), 'kill': any(p['kill'] for p in pulls), 'best': best_hp(pulls),
            'byDiff': by_diff, 'updated': data['updated']}

def merge_index(idx, entry):
    idx = idx if isinstance(idx, dict) and 'nights' in idx else {'nights': []}
    idx['nights'] = [e for e in idx['nights'] if e.get('file') != entry['file']] + [entry]
    idx['nights'].sort(key=lambda e: (date_key(e.get('date')), e.get('log', '')))
    return idx


def newest_log():
    files = glob.glob(os.path.join(LOG_DIR, 'WoWCombatLog*.txt'))
    return max(files, key=os.path.getmtime) if files else None

def stamp(): return datetime.now().strftime('%H:%M:%S')

def set_team(team):
    """Point every data path at this team's folder."""
    global NIGHT_DIR, INDEX_PATH, FANGS_DIR, FANGS_INDEX, SETUP_PATH, KITH_DIR, KITH_INDEX, ACT_DIR, ACT_INDEX
    base = f'data/{team}/{TIER}'
    NIGHT_DIR, FANGS_DIR = f'{base}/sszorak', f'{base}/twin-fangs'
    INDEX_PATH, FANGS_INDEX, SETUP_PATH = f'{NIGHT_DIR}/nights.json', f'{FANGS_DIR}/nights.json', f'{FANGS_DIR}/setup.txt'
    KITH_DIR = f'{base}/kithix'; KITH_INDEX = f'{KITH_DIR}/nights.json'   # Kith'ix is its own one-boss raid, kept with this season's bosses
    ACT_DIR = f'{base}/activity'; ACT_INDEX = f'{ACT_DIR}/nights.json'   # every boss pull and trash fight, for the timelines
    for b in FAIL_BOSSES: b['dir'] = f"{base}/{b['slug']}"; b['index'] = f"{b['dir']}/nights.json"   # the other bosses of the tier

def pick_team():
    """The team from --team, or asked in the Terminal window. Returns (id, name)."""
    def find(s):
        s = s.strip().lower()
        return next((t for i, t in enumerate(TEAMS) if s in (t[0], str(i + 1))), None)
    if '--team' in sys.argv:
        got = find(sys.argv[sys.argv.index('--team') + 1] if sys.argv.index('--team') + 1 < len(sys.argv) else '')
        if not got: sys.exit('--team takes ' + ', '.join(t[0] for t in TEAMS) + ' (or 1 to ' + str(len(TEAMS)) + ').')
        return got
    if not sys.stdin.isatty(): sys.exit('Which team? Add --team ' + ' / '.join(t[0] for t in TEAMS) + '.')
    print('Which team is this raid for?')
    for i, (_, name) in enumerate(TEAMS): print(f'  {i + 1}  {name}')
    while True:
        try: got = find(input(f'Type 1 to {len(TEAMS)} and press Return: '))
        except EOFError: sys.exit('\nNo team chosen.')
        if got: return got

def main():
    dry = '--dry-run' in sys.argv           # write the night file and nights.json locally instead of uploading
    out_dir = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else os.path.dirname(os.path.abspath(__file__))
    once = '--once' in sys.argv             # read the log once, upload, and exit
    if '--log' in sys.argv: path_override = sys.argv[sys.argv.index('--log') + 1]
    else: path_override = None
    global ENC, BOSS_NPC
    if '--enc' in sys.argv: ENC = sys.argv[sys.argv.index('--enc') + 1]
    if '--boss' in sys.argv: BOSS_NPC = '-' + sys.argv[sys.argv.index('--boss') + 1] + '-'

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    # your server unless --github is given; the first run asks for the server's upload token
    use_server = not dry and '--github' not in sys.argv
    if '--sync-site' in sys.argv:
        if not use_server: sys.exit('--sync-site sends the site to your server; leave out --dry-run and --github.')
        up = connect_server()
        print(f'[{stamp()}] Sending every page and data file in {repo_dir} to {up.where}...')
        n = send_site(up, repo_dir, with_data=True)
        print(f'[{stamp()}] Sent {n} files. Open {SITE_URL} to check it.')
        backup_groups(up, repo_dir)
        return
    if '--pages' in sys.argv:          # page files changed since the last send, and nothing else: raid data stays as the server has it
        if not use_server: sys.exit('--pages sends page files to your server; leave out --dry-run and --github.')
        up = connect_server()
        n = send_site(up, repo_dir, with_data=False)
        backup_groups(up, repo_dir)
        print(f'[{stamp()}] ' + (f'Sent {n} changed page file{"" if n == 1 else "s"}. Open {SITE_URL} to check it.' if n else
                                 'No page files changed since the last send. Nothing to do.'))
        return
    team, team_name = pick_team()
    set_team(team)
    print(f'[{stamp()}] Team: {team_name}. Data goes to data/{team}/{TIER}/<boss>/.')
    up = None if dry else connect_server() if use_server else Uploader(get_token())
    if use_server:
        up.keep_dir = repo_dir
        n = send_site(up, repo_dir, with_data=False)       # page changes since the last run go live now
        if n: print(f'[{stamp()}] Sent {n} changed page file{"" if n == 1 else "s"} to {up.where}.')
        backup_groups(up, repo_dir)
    if up and not up.live:
        try: gh('GET', f'https://api.github.com/repos/{REPO}', up.token)
        except urllib.error.HTTPError as e:
            sys.exit(f'GitHub said {e.code} for {REPO}. Check the token has Contents read and write on that repository.')
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                sys.exit('Python could not verify GitHub\'s certificate. If you installed Python from python.org, '
                         'open Applications > Python 3.x and run "Install Certificates.command", then try again.')
            sys.exit(f'Could not reach GitHub: {e.reason}')
        print(f'[{stamp()}] Connected to github.com/{REPO}')

    path, fh, parser, fangs, kith, act, seen_finished, pending, last_upload = None, None, None, None, None, None, None, False, 0.0
    fails = []      # one FailParser per boss in FAIL_BOSSES

    dec, pos = None, 0
    print(f'[{stamp()}] Watching {LOG_DIR} for pulls of every supported boss. Press Control-C to stop.')
    gap = 0 if (up and up.live) else MIN_UPLOAD_GAP      # your server takes every pull the moment it ends
    if not (up and up.live): print(f'[{stamp()}] Type "push" and press Return to upload right now instead of waiting.')
    # only logs WoW writes to while the uploader runs: an older log (a past raid night) is never uploaded, so starting the
    # uploader before WoW opens tonight's log can't send last night's pulls (to this team or any other). --log still reads any log.
    started, waiting_on = time.time() - 5, None
    live_state = {}   # the last now-pulling note sent


    def send():
        """Build the night from everything read so far and publish it. Returns True on success."""
        log_name = os.path.basename(path).replace('Archive-', '', 1)
        stem = os.path.splitext(log_name)[0]                 # logs moved by the Warcraft Logs uploader app lose their Archive- prefix
        parts, whats = [], []
        if parser.pulls:
            data = parser.build(log_name)
            last = data['pulls'][-1]
            whats.append(f"Sszorak pull {last['n']} " + ('kill' if last['kill'] else 'in progress' if last['live'] else
                         f"wipe at {last['hp'] * 100:.1f}%" if last['hp'] is not None else 'wipe'))
            f = f'{NIGHT_DIR}/{stem}.json'; parts.append((f, data, INDEX_PATH, night_entry(data, f)))
        fdata = fangs.build(log_name)
        if fdata['pulls']:
            last = fdata['pulls'][-1]
            whats.append(f"Twin Fangs pull {last['n']} " + ('kill' if last['kill'] else 'in progress' if last['live'] else 'wipe'))
            f = f'{FANGS_DIR}/{stem}.json'; parts.append((f, fdata, FANGS_INDEX, night_entry(fdata, f)))
        kdata = kith.build(log_name)
        if kdata['pulls']:
            last = kdata['pulls'][-1]
            whats.append(f"Kith'ix pull {last['n']} " + ('kill' if last['kill'] else 'in progress' if last['live'] else
                         f"wipe at {last['hp'] * 100:.1f}%" if last['hp'] is not None else 'wipe'))
            f = f'{KITH_DIR}/{stem}.json'; parts.append((f, kdata, KITH_INDEX, night_entry(kdata, f)))
        for fp in fails:
            d = fp.build(log_name)
            if not d['pulls']: continue
            last = d['pulls'][-1]; b = fp.cfg
            whats.append(f"{b['name']} pull {last['n']} " + ('kill' if last['kill'] else 'in progress' if last['live'] else 'wipe'))
            f = f"{b['dir']}/{stem}.json"; parts.append((f, d, b['index'], night_entry(d, f)))
        adata = act.build(log_name)
        if adata['pulls'] or adata['trash']:      # every boss pull and trash fight, for the timelines
            f = f'{ACT_DIR}/{stem}.json'; parts.append((f, adata, ACT_INDEX, activity_entry(adata, f)))
            if not whats: whats.append(f"{adata['pulls'][-1][3].strip(chr(34))} pull {len(adata['pulls'])}" if adata['pulls'] else 'trash')
        if not parts: return True
        what = ', '.join(whats)
        if dry:
            for f, d, ip, entry in parts:
                os.makedirs(os.path.join(out_dir, os.path.dirname(f)), exist_ok=True)
                with open(os.path.join(out_dir, f), 'w') as fh_: json.dump(d, fh_, separators=(',', ':'), ensure_ascii=False)
                ipp = os.path.join(out_dir, ip)
                idx = json.load(open(ipp)) if os.path.exists(ipp) else None
                with open(ipp, 'w') as fh_: json.dump(merge_index(idx, entry), fh_, indent=1, ensure_ascii=False)
                print(f'[{stamp()}] Wrote {f} ({fmt_size(os.path.getsize(os.path.join(out_dir, f)))}) and {ip} in {out_dir} ({len(d["pulls"])} pulls)')
            print(f'[{stamp()}] Latest: {what}')
            return True
        size = sum(len(json.dumps(d, separators=(',', ':'), ensure_ascii=False).encode()) for _, d, _, _ in parts)
        print(f'[{stamp()}] Uploading {", ".join(f for f, _, _, _ in parts)} ({fmt_size(size)}): {what}')
        try:
            up.publish(parts, f'Raid data: {what}')
            print(f'[{stamp()}] Uploaded. ' + ('Live on the site now.' if up.live else 'The site updates in a minute or two.'))
            return True
        except Exception as e:
            print(); print(f'[{stamp()}] Upload failed, will try again shortly: {e}')
            return False

    try:
        while True:
            cmd = ''
            if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
                cmd = sys.stdin.readline().strip().lower()
                if cmd and cmd != 'push':
                    print(f'[{stamp()}] Commands: "push" uploads now.'); cmd = ''
                elif cmd == 'push' and not fh:
                    print(f'[{stamp()}] Nothing to upload yet: no combat log found.'); cmd = ''
            newest = path_override or newest_log()
            if newest and newest != path and not path_override and os.path.getmtime(newest) < started:
                if waiting_on != newest:
                    waiting_on = newest
                    print(f'[{stamp()}] Waiting for WoW to write tonight\'s combat log. The newest one, {os.path.basename(newest)}, '
                          'is from before the uploader started, so it won\'t be uploaded.')
                newest = None
            if newest and newest != path:
                if fh: fh.close()
                path, fh, parser, fangs, kith, act = newest, open(newest, 'rb'), Parser(), FangsParser(), KithixParser(), ActivityParser()
                fails = [FailParser(b) for b in FAIL_BOSSES]
                dec, pos = codecs.getincrementaldecoder('utf-8')(errors='replace'), 0
                seen_finished, pending = None, False
                print(f'[{stamp()}] Reading {os.path.basename(path)} ({fmt_size(os.path.getsize(path))})')
            if fh:
                total = os.path.getsize(path)
                show = total - pos > (32 << 20)            # only draw a bar when there's a lot to read
                while True:
                    chunk = fh.read(8 << 20)
                    if not chunk: break
                    pos += len(chunk)
                    text = dec.decode(chunk)
                    parser.feed(text); fangs.feed(text); kith.feed(text); act.feed(text)
                    for fp in fails: fp.feed(text)
                    if up and up.live and not path_override: live_note(up, text, live_state)   # pages follow the raid to the boss being pulled
                    if show: bar(pos, total, f'Reading log  {fmt_size(pos)} of {fmt_size(total)}')
                if show:
                    n = sum(1 for p in parser.pulls if not p['ended'] or (p['dur'] or 0) >= 2)   # same pulls the site shows
                    nf = len(fangs.pulls) + (1 if fangs.cur and fangs.cur['twin'] else 0)
                    nk = len(kith.pulls) + (1 if kith.cur else 0)
                    found = [(n, 'Sszorak'), (nf, 'Twin Fangs'), (nk, "Kith'ix")] + [(len(fp.pulls) + (1 if fp.cur else 0), fp.cfg['name']) for fp in fails]
                    found = [f'{k} {s} pull{"" if k == 1 else "s"}' for k, s in found if k]
                    print(f'\n[{stamp()}] Found ' + (', '.join(found) if found else 'no boss pulls') + ' so far')
                # a pull ended (or first look at a log with Sszorak pulls): queue an upload
                wait = gap - (time.time() - last_upload)
                # only a finished pull queues an upload; a pull that just started doesn't use up the 6-minute slot
                done = (parser.finished, fangs.finished, kith.finished, act.finished) + tuple(fp.finished for fp in fails)   # any boss pull ending updates the timelines
                if sum(done) > 0 and done != seen_finished:
                    seen_finished, pending = done, True
                    if not (dry or once) and wait > 0:
                        print(f'[{stamp()}] Pull ended; uploading in {int(wait // 60)} min {int(wait % 60)} s '
                              '(at most one upload every 6 minutes, to stay under GitHub Pages\' build limit).')
                # typed in this window: "push" uploads now, skipping the 6-minute wait
                push = False
                if cmd == 'push':
                    if parser.pulls or fangs.has_data() or kith.has_data() or act.has_data() or any(fp.has_data() for fp in fails): push = True
                    else: print(f'[{stamp()}] Nothing to upload yet: no boss pulls in this log.')
                if push or (pending and (dry or once or wait <= 0)):
                    if push: print(f'[{stamp()}] Uploading now (push).')
                    if send():
                        pending, last_upload = False, time.time()
                    else:
                        last_upload = time.time() - gap + (10 if up.live else 30)   # retry shortly
            if once: break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        if pending and fh:
            print(f'\n[{stamp()}] Sending the last update before stopping...')
            send()
        raise

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print('\nStopped.')
