#!/usr/bin/env python3
"""
Taynalysis upload service. Caddy forwards https://taynalysis.com/api/* here; everything else Caddy serves
straight from the site folder.

  PUT /api/files/<path>   save one site file (needs the upload token)     -> {"ok": true, "path": ..., "bytes": n}
  PUT /api/groups/<team>/<tier>/<boss>/<set>   save group picks made on a boss page (needs the editor key, which can do
                          nothing else): body {"groups": {"Player": value, ...}, "diffs": [16] (optional)}. Coiled Altar's
                          "spots" set holds assigned drop spots instead: {"cv:1": [x, y], ...}. Adds a change stamped with the server's
                          clock: pulls before it keep their groups. Writes data/<team>/<tier>/<boss>/groups-<set>.json
                          -> {"ok": true, "changes": [...]}
  PUT /api/flags/<team>/<tier>/<boss>   flag a pull with a data error (editor key): body {"pull": "<log name>#<pull n>",
                          "flagged": true | false, "note": "..."}. Flagged pulls are hidden from the boss page for
                          everyone without the editor key. Writes data/<team>/<tier>/<boss>/groups-flags.json
                          -> {"ok": true, "flags": {pull: {"at": time, "note": ...}}}
  GET /api/check          confirm an upload token works                    -> {"ok": true}
  GET /api/editor-check   confirm an editor (admin) key works              -> {"ok": true}
  GET /api/health         is the service running (no token needed)         -> {"ok": true}

Settings come from environment variables (set in the systemd unit):
  TAYNALYSIS_SITE        site folder Caddy serves                 (default /srv/taynalysis/site)
  TAYNALYSIS_TOKEN_FILE  file holding the upload token             (default /etc/taynalysis/upload-token)
  TAYNALYSIS_EDITOR_FILE file holding the editor key (groups only) (default /etc/taynalysis/editor-key)
  TAYNALYSIS_PORT        local port Caddy forwards to              (default 8787)
"""
import hmac, json, os, re, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

SITE = os.path.realpath(os.environ.get('TAYNALYSIS_SITE', '/srv/taynalysis/site'))
TOKEN_FILE = os.environ.get('TAYNALYSIS_TOKEN_FILE', '/etc/taynalysis/upload-token')
PORT = int(os.environ.get('TAYNALYSIS_PORT', '8787'))
MAX_BYTES = 25 * 1024 * 1024
ALLOWED = {'.html', '.json', '.txt', '.css', '.js', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.woff2'}
SEGMENT = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$')     # no leading dot, no slashes, no spaces
EDITOR_FILE = os.environ.get('TAYNALYSIS_EDITOR_FILE', '/etc/taynalysis/editor-key')
# boss page -> group sets it has -> what a player's value may be
KICK_SPOT = re.compile(r'^(ml|fm|mr|rl|rr|backup)[1-9]$')        # kick order: quadrant + place in it (melee left 1st = ml1)
SPOT_KEY = re.compile(r'^(s[0-9]{1,2}:)?(cv|vm):[^<>"&]{1,24}$')   # an assigned spot: [orb set:] kind (cv / vm) + label (s2:cv:1)
is_coord = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 100000
# each check gets (key, value); keys are player names except for spots
GROUP_SETS = {'twin-fangs': {'feast': lambda k, v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 3,
                             'kicks': lambda k, v: isinstance(v, str) and bool(KICK_SPOT.match(v))},
              # Coiled Altar: where Coalesced Venom (cv) / Virulent Mutation (vm) carriers drop them: {"cv:1": [world x, world y]}
              'coiled-altar': {'spots': lambda k, v: bool(SPOT_KEY.match(k)) and isinstance(v, list) and len(v) == 2 and all(is_coord(x) for x in v),
                               # who may pick up which globules: {"Player": "green" | "purple" | "both"}
                               'pickers': lambda k, v: v in ('green', 'purple', 'both'),
                               # Guillotine soak groups: {"11": [names], "12", "31", "32", "33"} (P1 soak 1-2, P3 soak 1-3)
                               'soaks': lambda k, v: k in ('11', '12', '31', '32', '33') and isinstance(v, list) and len(v) <= 15 and
                                        all(isinstance(n, str) and 0 < len(n) <= 40 and not any(ch in n for ch in '<>"&') for n in v)}}
GROUP_MAX = {'spots': 300}   # most entries in one save (default 40)
# sets any boss page may save: 'cal' = an admin's nudge of where the combat-log positions sit on the room image
# {"dx": map px, "dy": map px, "rot": degrees, "zoom": factor}
ANY_SETS = {'cal': lambda k, v: k in ('dx', 'dy', 'rot', 'zoom') and is_coord(v) and (k != 'zoom' or 0.2 <= v <= 5) and abs(v) <= 600}
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
GROUP_LOCK = threading.Lock()
PULL_KEY = re.compile(r'^[A-Za-z0-9_.-]{1,80}#[0-9]{1,4}$')        # a pull: its log's name and its number in that log


def load_token():
    with open(TOKEN_FILE, encoding='utf-8') as f:
        tok = f.read().strip()
    if len(tok) < 32:
        sys.exit(f'Upload token in {TOKEN_FILE} is missing or too short.')
    return tok

TOKEN = load_token()


def load_editor_key():
    """The editor key only saves group picks. Without the file, group saving is off (everything else still works)."""
    try:
        with open(EDITOR_FILE, encoding='utf-8') as f:
            key = f.read().strip()
    except OSError:
        return None
    return key if len(key) >= 16 else None

EDITOR_KEY = load_editor_key()


def write_atomic(dest, data):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix='.up-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)          # readers see the old file or the new one, never half of one
    except Exception:
        if os.path.exists(tmp): os.unlink(tmp)
        raise


def safe_path(raw):
    """Site-relative path from the URL -> absolute path inside SITE, or None if it isn't allowed."""
    rel = unquote(raw).strip('/')
    parts = rel.split('/')
    if not rel or len(parts) > 6 or not all(SEGMENT.match(p) for p in parts):
        return None
    if os.path.splitext(parts[-1])[1].lower() not in ALLOWED:
        return None
    full = os.path.realpath(os.path.join(SITE, *parts))
    return full if full.startswith(SITE + os.sep) else None


class Handler(BaseHTTPRequestHandler):
    server_version = 'taynalysis-upload/1'

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self, key=None):
        key = TOKEN if key is None else key
        got = self.headers.get('Authorization', '')
        return got.startswith('Bearer ') and hmac.compare_digest(got[7:].strip(), key)

    def read_body(self, limit):
        """The request body, or None after replying with the error."""
        try:
            n = int(self.headers.get('Content-Length', ''))
        except ValueError:
            self.reply(411, {'ok': False, 'error': 'Content-Length required'}); return None
        if n < 0 or n > limit:
            self.reply(413, {'ok': False, 'error': f'body over {limit} bytes'}); return None
        data = self.rfile.read(n)
        if len(data) != n:
            self.reply(400, {'ok': False, 'error': 'upload cut short'}); return None
        return data

    def put_groups(self, path):
        parts = path[len('/api/groups/'):].strip('/').split('/')
        valid = GROUP_SETS.get(parts[2], {}).get(parts[3]) or ANY_SETS.get(parts[3]) if len(parts) == 4 else None
        if len(parts) != 4 or not all(SLUG.match(x) for x in parts) or not valid:
            return self.reply(400, {'ok': False, 'error': 'path not allowed'})
        if not EDITOR_KEY:
            return self.reply(503, {'ok': False, 'error': 'group saving is off (no editor key on the server)'})
        if not self.authorized(EDITOR_KEY):
            return self.reply(401, {'ok': False, 'error': 'bad editor key'})
        data = self.read_body(65536)
        if data is None:
            return
        try:
            body = json.loads(data)
            groups, mode = body['groups'], body.get('from', 'now')   # only "now": nothing may rewrite earlier pulls' groups
            diffs = body.get('diffs')                                  # optional: the difficulties this change is for
            ok = (mode == 'now' and isinstance(groups, dict) and len(groups) <= GROUP_MAX.get(parts[3], 40) and
                  all(isinstance(k, str) and 0 < len(k) <= 40 and not any(ch in k for ch in '<>"&') and valid(k, v)
                      for k, v in groups.items()) and
                  (diffs is None or (isinstance(diffs, list) and 0 < len(diffs) <= 4 and
                                     all(d in (14, 15, 16, 17) and not isinstance(d, bool) for d in diffs))))
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            return self.reply(400, {'ok': False, 'error': 'groups must be {"Player": value}'})
        dest = os.path.realpath(os.path.join(SITE, 'data', *parts[:3], f'groups-{parts[3]}.json'))
        if not dest.startswith(SITE + os.sep):
            return self.reply(400, {'ok': False, 'error': 'path not allowed'})
        with GROUP_LOCK:
            changes = []
            if os.path.exists(dest):
                try:
                    with open(dest, encoding='utf-8') as f:
                        changes = json.load(f).get('changes', [])
                except (OSError, ValueError):
                    changes = []
            change = {'at': int(time.time()), 'groups': dict(sorted(groups.items()))}
            if diffs: change['diffs'] = sorted(set(diffs))
            changes.append(change)
            changes = sorted(changes, key=lambda c: c.get('at', 0))[-500:]
            write_atomic(dest, json.dumps({'changes': changes}, ensure_ascii=False, indent=1).encode())
        self.reply(200, {'ok': True, 'changes': changes})

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self.reply(200, {'ok': True})
        if path == '/api/editor-check':
            return self.reply(200, {'ok': True}) if EDITOR_KEY and self.authorized(EDITOR_KEY) else self.reply(401, {'ok': False, 'error': 'bad editor key'})
        if path == '/api/check':
            return self.reply(200, {'ok': True}) if self.authorized() else self.reply(401, {'ok': False, 'error': 'bad token'})
        self.reply(404, {'ok': False, 'error': 'not found'})

    def put_flag(self, path):
        parts = path[len('/api/flags/'):].strip('/').split('/')
        if len(parts) != 3 or not all(SLUG.match(x) for x in parts):
            return self.reply(400, {'ok': False, 'error': 'path not allowed'})
        folder = os.path.realpath(os.path.join(SITE, 'data', *parts))
        if not folder.startswith(SITE + os.sep) or not os.path.isdir(folder):      # only a boss folder that already has data
            return self.reply(404, {'ok': False, 'error': 'no such boss folder'})
        if not EDITOR_KEY:
            return self.reply(503, {'ok': False, 'error': 'flagging is off (no editor key on the server)'})
        if not self.authorized(EDITOR_KEY):
            return self.reply(401, {'ok': False, 'error': 'bad editor key'})
        data = self.read_body(4096)
        if data is None:
            return
        try:
            body = json.loads(data)
            pull, on, note = body['pull'], body['flagged'], body.get('note', '')
            ok = (isinstance(pull, str) and bool(PULL_KEY.match(pull)) and isinstance(on, bool) and isinstance(note, str)
                  and len(note) <= 300 and not any(ch in note for ch in '<>'))
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            return self.reply(400, {'ok': False, 'error': 'body must be {"pull": "<log>#<n>", "flagged": true|false, "note": "..."}'})
        dest = os.path.join(folder, 'groups-flags.json')
        with GROUP_LOCK:
            flags = {}
            if os.path.exists(dest):
                try:
                    with open(dest, encoding='utf-8') as f:
                        flags = json.load(f).get('flags', {})
                except (OSError, ValueError):
                    flags = {}
            if on:
                flags[pull] = {'at': int(time.time()), 'note': note}
            else:
                flags.pop(pull, None)
            write_atomic(dest, json.dumps({'flags': flags}, ensure_ascii=False, indent=1).encode())
        self.reply(200, {'ok': True, 'flags': flags})

    def do_PUT(self):
        path = urlsplit(self.path).path
        if path.startswith('/api/groups/'):
            return self.put_groups(path)
        if path.startswith('/api/flags/'):
            return self.put_flag(path)
        if not path.startswith('/api/files/'):
            return self.reply(404, {'ok': False, 'error': 'not found'})
        if not self.authorized():
            return self.reply(401, {'ok': False, 'error': 'bad token'})
        dest = safe_path(path[len('/api/files/'):])
        if not dest:
            return self.reply(400, {'ok': False, 'error': 'path not allowed'})
        data = self.read_body(MAX_BYTES)
        if data is None:
            return
        write_atomic(dest, data)
        self.reply(200, {'ok': True, 'path': os.path.relpath(dest, SITE), 'bytes': len(data)})

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.command, fmt % args))


if __name__ == '__main__':
    os.makedirs(SITE, exist_ok=True)
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'Taynalysis upload service on 127.0.0.1:{PORT}, saving into {SITE}', flush=True)
    srv.serve_forever()
