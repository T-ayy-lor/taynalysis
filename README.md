# Taynalysis

Raid-progression analysis for World of Warcraft, built from the combat log. A quick reference for what went wrong on each pull and who caused it.

**Use:** open https://taynalysis.com, pick your team, then a boss.

**Pages** (every boss page has a Normal / Heroic / Mythic filter, the raid-night timeline, and Latest / Overall views per pull):
- `index.html`: home page. Team picker, then the bosses in raid order. Teams with no data yet can't be picked.
- `sszorak.html`: Sszorak. Virulence positions against assigned spots (Mythic), Venomous Surge drops, Raging Crosswinds (arrows, deaths, and a map of each set), Tempest hits, damage done.
- `fangs.html`: Twin Fangs. Eternal Venom sources, Caustic Globule CC and soaks, Rouse the Brood kick order (Mythic), Ravenous Feast soaks.
- `kithix.html`: Kith'ix. Bomber Beetles, intermissions, Light's Embrace, Overwhelming Fear, Mindstingers, tank swaps.
- `nekzali.html`, `entombed-sentinels.html`, `vashnik.html`, `lost-explorers.html`, `coiled-altar.html`, `ulatek.html`: the rest of the raid, one module per mechanic. Each is a small config drawn by `boss.js` with `boss.css`.
- Every boss page ends with avoidable damage taken, damage, healing, dispels and early deaths.

**Needs testing:** Kith'ix and the six newest bosses haven't been checked on a live raid night yet. They're marked "Needs testing" and can't be opened from the home page while `SHOW_UNTESTED` in `shared.js` is `false`; their data still uploads.

**Groups:** Twin Fangs soak groups and kick order are set on the boss page (Edit groups), saved with an editor key that can save groups and nothing else. Each pull is scored with the groups set before it started. Pulls from before any were set: soak groups are worked out from who soaked which slam; the kick order comes from `setup.txt`.

**How it's made:** `taynalysis_uploader.py` reads the WoW combat log (Advanced Combat Logging on) and uploads each pull to the server the moment it ends, into the folder of the team picked when it starts (`data/<team>/<raid>/<boss>/`), plus an `activity/` folder with every boss pull and trash fight for the timelines. `shared.js` lists the teams and bosses for every page and draws the shared panels. The pages check for new data every few seconds.

**Images:** boss portraits in `img/bosses/` are Encounter Journal portraits (via Wowhead and wago.tools). Room maps in `img/maps/` and the Sszorak room image are from raidstrats.gg.

**Local preview:** `python3 -m http.server 8765` in this folder, then open http://localhost:8765.

**Server:** `server/setup.sh` sets up the Lightsail server (Caddy for HTTPS, plus `server/upload_server.py`, which accepts uploads that carry the upload token).
