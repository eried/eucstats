"""Public-site localization.

`EN` is the canonical English source — the single source of truth and the
fallback for any missing key. `TRANSLATIONS` holds the other 14 locales
supported by eucplanet (da, de, es, es-419, fr, it, nl, no, pl, pt-BR, ru,
sv, uk, zh). `langs_payload()` returns {locale: {key: text}} for injection
into the public page as window.__I18N__; the client picks a locale (browser
auto-detect or the saved cogwheel choice) and falls back to EN per key.

Admin is intentionally NOT localized. The big red banner stays in its
original wording (it's set by the admin).
"""
from __future__ import annotations

# --- canonical English source (key -> text) -------------------------------
# Keys are grouped by area. {x} placeholders are filled on the client and must
# be preserved verbatim by every translation.
EN: dict[str, str] = {
    # dock (bottom bar) — short labels, keep tight
    "dock.riders": "Riders",
    "dock.crews": "Crews",
    "dock.countries": "Countries",
    "dock.wheels": "Wheels",
    "dock.brands": "Brands",
    "dock.records": "Records",
    "dock.app": "App",
    # crews & territory. Kept short and plain on purpose: this is a game mode, not a manual.
    "crew.board": "Who holds what",
    "crew.tiles": "{n} tiles",
    "crew.tile1": "1 tile",
    "crew.patches": "{n} patches",
    "crew.inall": "{v} all told",
    # signing in
    "crew.signin.h": "Get your pass",
    "crew.signin.p": "Your app vouches for you once. Then you can start a crew and go take ground.",
    "crew.signin.scan": "Scan it with EUC Planet, or type the code in. Good for 3 minutes.",
    "crew.signin.same": "Reading this on your phone? You can't scan your own screen. Tap below.",
    "crew.signin.open": "Open EUC Planet",
    # starting one
    "crew.new.h": "Start a crew",
    "crew.new.p": "We picked colours nobody else is flying. Change them if you like.",
    "crew.new.name": "Name",
    "crew.new.desc": "Description",
    "crew.new.colours": "Colours",
    "crew.new.who": "Who can join",
    "crew.new.approval": "A leader says yes",
    "crew.new.open": "Anyone",
    "crew.new.invite": "Invite code only",
    "crew.new.go": "Create crew",
    # joining
    "crew.join.h": "Join a crew",
    "crew.join.btn": "Join",
    "crew.join.ask": "Ask",
    "crew.join.code": "Enter code",
    "crew.join.codeask": "{name}'s invite code?",
    "crew.cancel": "Cancel",
    "crew.join.pending": "Waiting on a leader to let you in.",
    "crew.join.wait.h": "Cooling off",
    "crew.join.wait.p": "You just walked out of one. Next crew in {n}. Keeps people from hopping around farming ground.",
    "crew.first.h": "Ride something first",
    "crew.first.p": "Crews are for riders. Send up one ride and you can start your own. Joining one works right now.",
    # your crew
    "crew.mine.ao": "in one piece",
    "crew.mine.start": "Ride a {n}x{n} block and you're on the map",
    "crew.mine.who": "Who rode for it",
    "crew.targets.h": "Where to ride next",
    "crew.targets.p": "How hard each one is to take. Tap it to find it.",
    "crew.targets.first": "puts you on the map",
    "crew.take.1": "one lap",
    "crew.take.2": "a short ride",
    "crew.take.3": "a proper trip",
    "crew.take.4": "a big ask",
    "crew.hold.1": "hanging on",
    "crew.hold.2": "comfortable",
    "crew.hold.3": "miles clear",
    "crew.tile.needw": "{v} from taking it",
    "crew.tile.fresh": "new this week",
    "crew.targets.joins": "joins two patches",
    "crew.targets.blocked": "ridden out, take the one next door",
    "crew.targets.taken": "{name} has it",
    "crew.targets.none": "Nothing to aim at yet. Ride a {n}x{n} block anywhere and this fills up.",
    "crew.targets.n": "N",
    "crew.targets.ne": "NE",
    "crew.targets.e": "E",
    "crew.targets.se": "SE",
    "crew.targets.s": "S",
    "crew.targets.sw": "SW",
    "crew.targets.w": "W",
    "crew.targets.nw": "NW",
    "crew.lose.h": "What you could lose",
    "crew.lose.p": "Squares somebody is riding harder than you. Go back over them.",
    "crew.mine.cancel": "Pull the request",
    "crew.mine.cancelq": "Pull your request to {name}? Costs you nothing.",
    "crew.mine.disband": "Disband",
    "crew.mine.disbandq": "Disband {name}? Colours go back in the box and the map fades out on its own.",
    "crew.mine.claim": "Take over",
    "crew.mine.claimq": "Your leader has gone quiet. Take the crew over?",
    "crew.e.full": "That crew is full. Pick another.",
    "crew.e.closed": "Not taking new crews right now.",
    "crew.e.forbidden": "Only a leader or officer can do that.",
    "crew.e.pass": "Your pass ran out. Grab a new one.",
    "crew.e.rate": "Slow down a second.",
    "crew.e.invite": "That code is not it.",
    "crew.e.name": "3 to 28 characters, and nothing exotic.",
    "crew.e.taken": "Name is taken.",
    "crew.e.increw": "Leave your crew first.",
    "crew.e.notrips": "Send up one ride first.",
    "crew.e.cooldown": "Still cooling off from the last one.",
    "crew.e.identity": "Another crew already flies those colours.",
    "crew.e.gone": "That crew is gone.",
    "crew.e.promote": "Hand leadership over before you go.",
    "crew.e.image": "That image is no good. Under 1 MB, square-ish.",
    "crew.mine.invite": "Invite code",
    "crew.mine.settings": "Crew settings",
    "crew.mine.emblem": "Emblem (small, square)",
    "crew.mine.emblemp": "Leave it empty and we draw one from your name.",
    "crew.mine.generated": "Use the drawn one",
    "crew.mine.save": "Save",
    "crew.mine.leave": "Leave crew",
    "crew.mine.signout": "Hand the pass back",
    "crew.mine.leaveq": "Leave {name}? No new crew for {n}.",
    "crew.role.leader": "leader",
    "crew.role.officer": "officer",
    "crew.role.member": "member",
    "crew.role.past": "left",
    "crew.accept": "Let in",
    "crew.decline": "No",
    "crew.pending.h": "Knocking",
    # how it works
    "crew.how.h": "How ground works",
    "crew.how.1": "Ride it, it turns your colour. Most km in a square over the last {d} days holds it.",
    "crew.how.2": "You need a {n}x{n} block before anything shows. One ride down one street gets you nothing.",
    "crew.how.3": "It grows out from what you already hold. One long ride can stitch two patches into one, and anything you ride a full loop around is yours.",
    "crew.how.4": "Nothing is forever. Stop riding and it fades. Ride more than someone and you take theirs.",
    "crew.how.5": "Old rides stay with the crew you rode them for. Walking out does not wipe the map.",
    "crew.empty": "Nobody holds anything yet.",
    "crew.board.sub": "Biggest patch a crew holds in one piece.",
    "crew.signin.noapp": "No app yet? Grab it from the App tab.",
    "crew.mine.invite2": "Invite link code",
    "crew.mine.leaveq0": "Leave {name}? You can join another one straight away.",
    "crew.days": "{n} days",
    "crew.day1": "1 day",
    "crew.now": "right now",
    "crew.signin.again": "Still waiting? Get a fresh code",
    "crew.signin.ok": "You're in",
    "crew.mine.youare": "you run it",
    "crew.mine.youofficer": "you are an officer",
    "crew.mine.youmember": "you ride for them",
    "crew.riders": "{n} riders",
    "crew.rider1": "1 rider",
    "crew.policy.open": "anyone can join",
    "crew.policy.approval": "leader says yes",
    "crew.policy.invite": "invite code",
    "crew.roles.h": "The crew",
    "crew.roles.promote": "Make officer",
    "crew.roles.demote": "Stand down",
    "crew.mine.signoutq": "Hand the pass back? You need your phone to get back in.",
    "crew.mine.emblembad": "That image was not accepted.",
    "crew.closed.h": "Not taking new crews",
    "crew.closed.p": "New crews are off for now. Join one instead.",
    "crew.err": "That didn't work.",
    # what a tile says when you tap it
    "crew.tile.safe": "Yours, comfortably",
    "crew.tile.pushed": "Someone else is riding it",
    "crew.tile.slipping": "About to flip",
    "crew.tile.free": "Up for grabs",
    "crew.tile.fading": "Going cold, nobody has been back",
    "crew.tile.days": "fades in {n} days",
    "crew.tile.day1": "fades tomorrow",
    "crew.tile.ringed": "Surrounded",
    "crew.tile.ringedp": "you rode a loop around it",
    "crew.tile.clear": "{v}",
    "crew.held": "{v} held",
    # panel titles (header of the sliding panel)
    "title.riders": "Riders",
    "title.crews": "Crews & Territory",
    "title.countries": "Countries",
    "title.wheels": "Wheel models",
    "title.brands": "Wheel brands",
    "title.records": "All-time records",
    "title.tech": "App & OS",
    # aria / tooltips
    "aria.settings": "Settings",
    "act.refresh": "Refresh",
    # cogwheel settings menu
    "cfg.units": "Units",
    "u.metric": "Metric",
    "u.imperial": "Imperial",
    "cfg.map": "Map",
    "cfg.intro": "Intro",
    "cfg.language": "Language",
    "cfg.enabled": "Enabled",
    "cfg.replay": "Replay",
    "cfg.intro_off": "Disabled site-wide",
    "cfg.intro_play": "Play the cinematic intro on load",
    # map styles
    "map.dark": "Dark",
    "map.light": "Light",
    "map.voyager": "Voyager",
    "map.satellite": "Satellite",
    "map.topo": "Topo",
    # footer
    "foot.poweredby": "powered by",
    # top stat chips
    "chip.riders": "Riders",
    "chip.trips": "Trips",
    "chip.total": "Total {unit}",
    "chip.countries": "Countries",
    # champions strip
    "champ.title": "EUC Planet Champions",
    "champ.day": "Day",
    "champ.week": "Week",
    "champ.month": "Month",
    "champ.pts": "{n} pts",
    "champ.norides": "no rides yet",
    "champ.toggle": "Show / hide",
    "champ.tip": "Our secret recipe: distance is king, lifted by your top speed and time on the wheel.",
    # empty / error states
    "empty.nodata": "no data yet",
    "empty.norecords": "no records yet",
    "empty.noapp": "no app data yet",
    "empty.apierror": "API error",
    # podium ranks (very short)
    "pod.1": "1ST",
    "pod.2": "2ND",
    "pod.3": "3RD",
    # generic units / sub-labels
    "u.riders": "{n} riders",
    "u.activeRiders": "Active riders",
    "u.ridesLogged": "Rides logged",
    "g.riders": "Riders",
    "g.rides": "Rides",
    # tech / app panel section titles (emoji kept, words translated)
    "tech.adoption": "Adoption",
    "tech.adoptionPct": "{pct}% of riders on the latest app · v{ver}",
    "tech.adopters": "Bleeding Edge · newest app",
    "tech.laggards": "Living in the past · oldest app",
    "tech.appvers": "App versions",
    "tech.osvers": "OS versions",
    "tech.countries": "Up-to-date countries",
    # all-time record labels
    "rec.mileage_king": "Mileage King",
    "rec.top_speed": "Top Speed",
    "rec.longest_trip": "Longest Trip",
    "rec.max_gforce": "Max G-Force",
    "rec.sustained_w": "Sustained Power",
    "rec.sustained_a": "Sustained Current",
    "rec.peak_voltage": "Voltage Peak",
    "rec.max_altitude": "Highest Altitude",
    "rec.min_altitude": "Lowest Altitude",
    "rec.biggest_climb": "Biggest Climb",
    "rec.biggest_downhill": "Biggest Downhill",
    "rec.most_rides": "Most Rides",

    # --- leaderboard trophies: NAME (creative, EUC-flavoured) + DESC (factual) ---
    "b.mileage.n": "Most Distance",
    "b.mileage.d": "Most distance ever ridden",
    "b.daily.n": "Best Day",
    "b.daily.d": "Most distance in a single day",
    "b.week.n": "Best Week",
    "b.week.d": "Most distance in one week",
    "b.month.n": "Best Month",
    "b.month.d": "Most distance in one calendar month",
    "b.speed.n": "Top Speed",
    "b.speed.d": "Highest speed reached on any ride",
    "b.accel.n": "0→40 km/h",
    "b.accel.d": "Fastest launch from a stop to 40 km/h (≈25 mph) · lower is better",
    "b.accel.d_mph": "Fastest launch from a stop to ≈25 mph (40 km/h) · lower is better",
    "b.gforce.n": "Top G",
    "b.gforce.d": "Strongest g-force spike",
    "b.power.n": "Power 2s",
    "b.power.d": "Highest power held for 2 seconds",
    "b.current.n": "Current 2s",
    "b.current.d": "Highest current held for 2 seconds",
    "b.voltage.n": "Peak Voltage",
    "b.voltage.d": "Highest battery voltage observed",
    "b.streak.n": "Streak Master",
    "b.streak.d": "Longest run of consecutive days ridden",
    "b.ascent.n": "Total Climb",
    "b.ascent.d": "Total elevation climbed (Everest = 8849 m)",
    "b.range.n": "Best Range",
    "b.range.d": "Longest estimated full-charge range",
    "b.efficiency.n": "Efficiency",
    "b.efficiency.d": "Lowest energy use per km · most efficient",
    "b.hours.n": "Steel Legs",
    "b.hours.d": "Most hours on the wheel",
    "b.cruise.n": "Calm Ride",
    "b.cruise.d": "Longest calm ride held under 10 km/h",
    "b.globe.n": "Globe Trotter",
    "b.globe.d": "Most countries ridden in",
    "b.altking.n": "Altitude King",
    "b.altking.d": "Biggest altitude swing in one ride",
    "b.frequent.n": "Most Rides",
    "b.frequent.d": "Most rides logged",
    "b.marathon.n": "Longest Ride",
    "b.marathon.d": "Longest single ride by time",
    "b.pace.n": "Avg Speed",
    "b.pace.d": "Highest average speed on a single ride",
    "b.battery.n": "Battery Drain",
    "b.battery.d": "Biggest battery drain in one ride",
    "b.night.n": "Night Rider",
    "b.night.d": "Most rides started at night (22:00–05:00 UTC)",
    "b.weekend.n": "Weekend Warrior",
    "b.weekend.d": "Most distance ridden on weekends",
    "b.early.n": "Early Bird",
    "b.early.d": "Most rides started in the morning (05:00–09:00 UTC)",
    "b.peak.n": "Biggest Climb",
    "b.peak.d": "Biggest elevation gain in a single ride",
    "b.energy.n": "Most Energy",
    "b.energy.d": "Most total energy used across all rides",
    "b.explorer.n": "Explorer",
    "b.explorer.d": "Most distinct map areas ridden",
    "b.bigday.n": "Busiest Day",
    "b.bigday.d": "Most rides in a single day",
    "b.commuter.n": "Commuter",
    "b.commuter.d": "Most distance ridden on weekdays",
    "b.freespin.n": "Biggest Freespin",
    "b.freespin.d": "Highest speed a wheel reached while spinning free — one spike, in km/h",
    "b.cutouts.n": "Falls / Cutouts",
    "b.cutouts.d": "Most falls detected — riding, then the wheel spinning free and the ride ending",
    "b.spins.n": "Freespins",
    "b.spins.d": "Most times the wheel was spun up off the ground — harmless, just for fun",
    "b.cutout.n": "Falls / Cutouts",
    "b.sag.n": "Voltage Sag",
    "b.sag.d": "Biggest voltage drop under load — the hardest battery pull",
    "b.rocket.n": "Rocket",
    "b.rocket.d": "Hardest sustained acceleration held for 2s+",
    # new gated / extreme boards (ship disabled). The qualifying ride length + distance
    # is appended on the client, unit-aware, so it's not part of the description string.
    "b.temphigh.n": "High Temp",
    "b.temphigh.d": "Hottest the board ever ran",
    "b.templow.n": "Low Temp",
    "b.templow.d": "Coldest ride",
    "b.temprise.n": "Temp Climb",
    "b.temprise.d": "Fastest the board heated up while riding (deg/s)",
    "b.tempdrop.n": "Temp Drop",
    "b.tempdrop.d": "Fastest the board cooled down while riding (deg/s)",
    "b.pwm.n": "Peak PWM",
    "b.pwm.d": "Closest to maxing the motor (PWM)",
    "b.battlow.n": "Low Battery",
    "b.battlow.d": "Lowest battery % reached",
    "b.althigh.n": "Max Altitude",
    "b.althigh.d": "Highest altitude ever reached",
    "b.altlow.n": "Min Altitude",
    "b.altlow.d": "Lowest altitude ever reached",
    # newer hidden metrics: longer sustained windows, high-speed / directional g, shake
    "b.g4.n": "G-Hold 4s",
    "b.g4.d": "Highest g-force held for 4 seconds",
    "b.g6.n": "G-Hold 6s",
    "b.g6.d": "Highest g-force held for 6 seconds",
    "b.pwm3.n": "PWM 3s",
    "b.pwm3.d": "Highest PWM held for 3 seconds",
    "b.spd5.n": "Speed 5s",
    "b.spd5.d": "Highest speed held for 5 seconds",
    "b.spd10.n": "Speed 10s",
    "b.spd10.d": "Highest speed held for 10 seconds",
    "b.pw6.n": "Power 6s",
    "b.pw6.d": "Highest power held for 6 seconds",
    "b.cur6.n": "Current 6s",
    "b.cur6.d": "Highest current held for 6 seconds",
    "b.gf20.n": "Grip G @20",
    "b.gf20.d": "Strongest g-force sustained above 20 km/h (≈12 mph)",
    "b.gf30.n": "Grip G @30",
    "b.gf30.d": "Strongest g-force sustained above 30 km/h (≈19 mph)",
    "b.gf40.n": "Grip G @40",
    "b.gf40.d": "Strongest g-force sustained above 40 km/h (≈25 mph)",
    "b.shake.n": "Wobble",
    "b.shake.d": "Biggest speed-wobble / shake index (experimental)",
    "b.accg.n": "Accel G",
    "b.accg.d": "Hardest acceleration as a g-force (from speed)",
    "b.brkg.n": "Braking G",
    "b.brkg.d": "Hardest braking as a g-force (from speed)",
    "b.sprint60.n": "0→60",
    "b.sprint60.d": "Fastest launch from a stop to 60 km/h (≈37 mph) · lower is better",
    "b.sprint100.n": "0→100",
    "b.sprint100.d": "Fastest launch from a stop to 100 km/h (≈62 mph) · lower is better",
    "b.acc30.n": "Accel 30+",
    "b.acc30.d": "Hardest acceleration g while already above 30 km/h (≈19 mph)",
    "b.acc50.n": "Accel 50+",
    "b.acc50.d": "Hardest acceleration g while already above 50 km/h (≈31 mph)",
    "b.brk30.n": "Brake 30+",
    "b.brk30.d": "Hardest braking g coming down from 30 km/h (≈19 mph)",
    "b.brk50.n": "Brake 50+",
    "b.brk50.d": "Hardest braking g coming down from 50 km/h (≈31 mph)",
    "b.stop30.n": "Stop 30",
    "b.stop30.d": "Fastest stop from 30 km/h (≈19 mph) to a standstill · lower is better",
    "b.stop50.n": "Stop 50",
    "b.stop50.d": "Fastest stop from 50 km/h (≈31 mph) to a standstill · lower is better",

    # --- group-panel boards (countries / wheels / brands) descriptions ---
    "g.dist.d": "Most distance ridden",
    "g.speed.d": "Fastest ride in the group",
    "g.accel.d": "Fastest 0→40 km/h · lower is better",
    "g.gforce.d": "Strongest g-force spike",
    "g.power.d": "Highest power held 2s",
    "g.current.d": "Highest current held 2s",
    "g.voltage.d": "Highest battery voltage",
    "g.riders.d": "Active riders",
    "g.trips.d": "Rides logged",
    "g.ascent.d": "Total elevation climbed",
    "g.range.d": "Longest est. range",
    "g.eff.d": "Lowest Wh/km · best",
    "g.cutout.d": "Cutouts per 1000 km ridden",
    "g.climb.d": "Biggest single climb",
    "g.alt.d": "Highest altitude reached",
    "g.temp.d": "Hottest the board ran",
}

# Native names shown in the language selector (never translated).
LANG_NAMES: dict[str, str] = {
    "en": "English", "da": "Dansk", "de": "Deutsch", "es": "Español",
    "es-419": "Español (LatAm)", "fr": "Français", "it": "Italiano",
    "nl": "Nederlands", "no": "Norsk", "pl": "Polski", "pt-BR": "Português (BR)",
    "ru": "Русский", "sv": "Svenska", "uk": "Українська",
    "zh": "中文 (简体)", "zh-Hant": "中文 (繁體)", "ja": "日本語", "ko": "한국어",
    "tr": "Türkçe",
}

# Filled by the translation workflow (web/i18n_data.py). Imported lazily so the
# module loads even before translations exist.
try:
    from web.i18n_data import TRANSLATIONS  # type: ignore
except Exception:  # pragma: no cover - until translations are generated
    try:
        from i18n_data import TRANSLATIONS  # type: ignore
    except Exception:
        TRANSLATIONS = {}


def langs_payload() -> dict:
    """{locale: {key: text}} for every supported locale, English-complete."""
    out = {"en": EN}
    for loc, table in (TRANSLATIONS or {}).items():
        if loc == "en":
            continue
        # English fallback per key keeps the client simple and crash-proof
        out[loc] = {k: (table.get(k) or v) for k, v in EN.items()}
    return out


def locale_table(loc: str) -> dict | None:
    """One locale's English-complete table, or None if unknown. Lazy-loaded by the
    client for any language beyond the one injected on first paint."""
    if loc == "en":
        return EN
    table = (TRANSLATIONS or {}).get(loc)
    return {k: (table.get(k) or v) for k, v in EN.items()} if table is not None else None


def pick(accept_language: str) -> str:
    """Best supported locale for an Accept-Language header (a server-side mirror of the
    client's language mapping). Lets the page inject just EN + the visitor's language
    instead of all of them."""
    sup = set(LANG_NAMES)
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()
        if not code:
            continue
        base = code.split("-")[0]
        reg = code.split("-")[1] if "-" in code else ""
        if base == "pt":
            loc = "pt-BR"
        elif base == "zh":
            loc = "zh-Hant" if any(x in code for x in ("tw", "hk", "mo", "hant")) else "zh"
        elif base in ("nb", "nn", "no"):
            loc = "no"
        elif base == "es":
            lat = {"419", "mx", "ar", "co", "cl", "pe", "ve", "ec", "gt", "cu",
                   "bo", "do", "hn", "py", "sv", "ni", "cr", "pa", "uy", "pr"}
            loc = "es-419" if reg in lat else "es"
        else:
            loc = base
        if loc in sup:
            return loc
    return "en"
