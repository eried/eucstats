# EUC Stats

[![License: MIT](https://img.shields.io/github/license/eried/eucstats)](LICENSE)
[![Live](https://img.shields.io/badge/Live-eucstats.ried.no-39d98a)](https://eucstats.ried.no)
[![App: EUC Planet](https://img.shields.io/badge/App-EUC_Planet-3DDC84?logo=googleplay&logoColor=white)](https://github.com/eried/eucplanet)
[![Web: Trip Viewer](https://img.shields.io/badge/Web-Trip_Viewer-2b6fd6)](https://github.com/eried/eucviewer)
[![Telegram](https://img.shields.io/badge/Telegram-EUCPlanetApp-26A5E4?logo=telegram&logoColor=white)](https://t.me/EUCPlanetApp)
[![Donate](https://img.shields.io/badge/Donate-PayPal-00457C?logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=AEB2RPZHNRTKG)

Public leaderboards and a world heatmap for electric unicycle riders, live at
[eucstats.ried.no](https://eucstats.ried.no). It's the scoreboard for the
[EUC Planet](https://github.com/eried/eucplanet) app: ride, sync, show up on the map.
No account to make, no email, no ads, and we don't sell your location to anyone (more
on that below, because it's the whole point).

## What's on it

**Lots of leaderboards.** Distance, top speed, g-force, sustained power and current,
voltage, streaks, climbing, range, efficiency, and a stack of sillier ones (Battery
Vampire, Sunday Cruiser, Freespin King), all crunched at once. Which ones are shown is
a toggle, so the lineup grows and shifts over time. Tap a rider to fly to their area on the map.

**Heatmap.** Where the community actually rides, as a glowing grid. Brighter where
more riders go, fainter where it's just one lonely soul.

**Crews.** A territory game on the same map. Start a crew or join one, and the squares
your crew rides the most turn its colour. Hold a block of them and you are on the board,
with every other crew out there coming to take it. You sign in by scanning a code with
the app, so there is still no password and no account.

**Country, wheel and brand boards.** The same stats, grouped. Settle whether your
brand is genuinely fast or just loud.

**App and OS stats.** Who's on the latest app, and who's still riding a build from 2021.

**Your language, your units.** It picks up your browser's language on its own, shows
distances and temperatures the way you read them, and lets you change either from the menu.

## Privacy (the whole point)

Strava's global heatmap once outed secret military bases and a lot of people's front
doors. This one is built the other way around:

- The map never shows your **route**, your start point, or any line. Every ride is
  snapped onto a fixed grid of coarse squares, far bigger than a street or a block.
- A square says that somebody rode somewhere inside it, and glows brighter the more
  riders share it. It never says who, when, or which way.
- The flag next to your name is the one **you picked**, not reverse-geocoded from GPS.
- You can opt out of public stats entirely, or delete everything you ever sent.
- **Crews is the one exception, and it is opt-in.** If you join a crew, the squares you
  ride are painted in your crew's colour and your name is on the crew's roster, so a
  square can be tied to a small group of named riders. It is still squares and never a
  route. If that is more than you want to show, don't join one; everything above stays
  as it is.

We keep aggregates, not breadcrumbs.

## How it works

FastAPI and SQLite (WAL), one process behind nginx, and the public site is one
server-rendered page with a MapLibre GL map, plus one script for Crews. The app uploads a trip, the
server parses it, runs it through an anti-fraud pipeline (impossible speed, teleports,
mock location, spoofed distance, overlapping rides), and folds the clean ones into
pre-computed leaderboard tables. The raw upload sits in the database for a retention
window, then gets evicted. Boring, cheap, quick.

## Run it yourself

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# open http://127.0.0.1:8000
```

The admin panel lives at `/admin` (one-time TOTP enrollment on first visit): named
dataset save-slots and backups, the ingest monitor and anti-fraud rules, retention and
heatmap tuning, metric visibility, an activity/health log, and a sandbox of magic
`store_id`s so the app team can test every response. Tests run with `pytest`.

## Why does this exist?

- a free public scoreboard makes riding more fun, and nobody had one,
- the numbers from [EUC Planet](https://github.com/eried/eucplanet) deserved somewhere nice to land.

## Contributing

The backend is `services/` and `ingest/`, the public site and the admin are in `web/`,
and the English in `web/i18n.py` is the source for every translation. PRs welcome, bugs go to
[Issues](../../issues), live chat is on [Telegram](https://t.me/EUCPlanetApp).

## Support

Free, and it stays free. The server costs a few euros a month; if you want to cover a
round: [donate via PayPal](https://www.paypal.com/donate/?hosted_button_id=AEB2RPZHNRTKG).
Optional, appreciated.

## License

[MIT](LICENSE). Fork it, host your own, just keep the copyright notice.
