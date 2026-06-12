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
    "dock.countries": "Countries",
    "dock.wheels": "Wheels",
    "dock.brands": "Brands",
    "dock.records": "Records",
    "dock.app": "App",
    # panel titles (header of the sliding panel)
    "title.riders": "Riders",
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

    # --- leaderboard trophies: NAME (creative, EUC-flavoured) + DESC (factual) ---
    "b.mileage.n": "Mile Muncher",
    "b.mileage.d": "Most distance ever ridden",
    "b.daily.n": "Day Crusher",
    "b.daily.d": "Most distance in a single day",
    "b.week.n": "Week Beast",
    "b.week.d": "Most distance in one week",
    "b.month.n": "Month Monster",
    "b.month.d": "Most distance in one calendar month",
    "b.speed.n": "Speedy Gonzales",
    "b.speed.d": "Highest speed reached on any ride",
    "b.accel.n": "Drag Racer",
    "b.accel.d": "Fastest launch from a stop to 40 km/h (≈25 mph) · lower is better",
    "b.accel.d_mph": "Fastest launch from a stop to ≈25 mph (40 km/h) · lower is better",
    "b.gforce.n": "G-Force Hero",
    "b.gforce.d": "Strongest g-force spike",
    "b.power.n": "Watt Beast",
    "b.power.d": "Highest power held for 2 seconds",
    "b.current.n": "Amp Demon",
    "b.current.d": "Highest current held for 2 seconds",
    "b.voltage.n": "Volt Lord",
    "b.voltage.d": "Highest battery voltage observed",
    "b.streak.n": "Streak Master",
    "b.streak.d": "Longest run of consecutive days ridden",
    "b.ascent.n": "Everest Climber",
    "b.ascent.d": "Total elevation climbed (Everest = 8849 m)",
    "b.range.n": "Long Hauler",
    "b.range.d": "Longest estimated full-charge range",
    "b.efficiency.n": "Eco Rider",
    "b.efficiency.d": "Lowest energy use per km · most efficient",
    "b.hours.n": "Steel Legs",
    "b.hours.d": "Most hours on the wheel",
    "b.cruise.n": "Sunday Cruiser",
    "b.cruise.d": "Longest calm ride held under 10 km/h",
    "b.globe.n": "Globe Trotter",
    "b.globe.d": "Most countries ridden in",
    "b.altking.n": "Altitude King",
    "b.altking.d": "Biggest altitude swing in one ride",
    "b.frequent.n": "Frequent Flyer",
    "b.frequent.d": "Most rides logged",
    "b.marathon.n": "Marathoner",
    "b.marathon.d": "Longest single ride by time",
    "b.pace.n": "Pace Maker",
    "b.pace.d": "Highest average speed on a single ride",
    "b.battery.n": "Battery Vampire",
    "b.battery.d": "Biggest battery drain in one ride",
    "b.night.n": "Night Rider",
    "b.night.d": "Most rides started at night (22:00–05:00 UTC)",
    "b.weekend.n": "Weekend Warrior",
    "b.weekend.d": "Most distance ridden on weekends",
    "b.early.n": "Early Bird",
    "b.early.d": "Most rides started in the morning (05:00–09:00 UTC)",
    "b.peak.n": "Peak Bagger",
    "b.peak.d": "Biggest elevation gain in a single ride",
    "b.energy.n": "Power Plant",
    "b.energy.d": "Most total energy used across all rides",
    "b.explorer.n": "Explorer",
    "b.explorer.d": "Most distinct map areas ridden",
    "b.bigday.n": "Big Day",
    "b.bigday.d": "Most rides in a single day",
    "b.commuter.n": "Commuter",
    "b.commuter.d": "Most distance ridden on weekdays",
    "b.freespin.n": "Freespin King",
    "b.freespin.d": "Biggest freespin / spin-up spike (wheel lifted or a crash)",
    "b.sag.n": "Sag Lord",
    "b.sag.d": "Biggest voltage drop under load — the hardest battery pull",
    "b.rocket.n": "Rocket",
    "b.rocket.d": "Hardest sustained acceleration held for 2s+",
    # new gated / extreme boards (ship disabled). The qualifying ride length + distance
    # is appended on the client, unit-aware, so it's not part of the description string.
    "b.temphigh.n": "Hot Rod",
    "b.temphigh.d": "Hottest the board ever ran",
    "b.templow.n": "Frostbite",
    "b.templow.d": "Coldest ride",
    "b.pwm.n": "Redline",
    "b.pwm.d": "Closest to maxing the motor (PWM)",
    "b.battlow.n": "Running on Fumes",
    "b.battlow.d": "Lowest battery % reached",
    "b.althigh.n": "Sky High",
    "b.althigh.d": "Highest altitude ever reached",
    "b.altlow.n": "Below Sea Level",
    "b.altlow.d": "Lowest altitude ever reached",
    # newer hidden metrics: longer sustained windows, high-speed / directional g, shake
    "b.g4.n": "G-Hold 4s",
    "b.g4.d": "Highest g-force held for 4 seconds",
    "b.g6.n": "G-Hold 6s",
    "b.g6.d": "Highest g-force held for 6 seconds",
    "b.pwm3.n": "Redline Hold",
    "b.pwm3.d": "Highest PWM held for 3 seconds",
    "b.spd5.n": "Flat-Out 5s",
    "b.spd5.d": "Highest speed held for 5 seconds",
    "b.spd10.n": "Flat-Out 10s",
    "b.spd10.d": "Highest speed held for 10 seconds",
    "b.pw6.n": "Watt Marathon",
    "b.pw6.d": "Highest power held for 6 seconds",
    "b.cur6.n": "Amp Marathon",
    "b.cur6.d": "Highest current held for 6 seconds",
    "b.gf20.n": "Fast & Loose 20",
    "b.gf20.d": "Strongest g-force sustained above 20 km/h (≈12 mph)",
    "b.gf30.n": "Fast & Loose 30",
    "b.gf30.d": "Strongest g-force sustained above 30 km/h (≈19 mph)",
    "b.gf40.n": "Fast & Loose 40",
    "b.gf40.d": "Strongest g-force sustained above 40 km/h (≈25 mph)",
    "b.glat.n": "Carver",
    "b.glat.d": "Strongest sustained sideways (cornering) g-force",
    "b.gbrk.n": "Brake Master",
    "b.gbrk.d": "Strongest sustained fore-aft (braking) g-force",
    "b.shake.n": "Wobble Warrior",
    "b.shake.d": "Biggest speed-wobble / shake index (experimental)",

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
