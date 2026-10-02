# Crews & Territory — specification

Status: **decided, not built.** Branch `feat/clans`, local only, nothing pushed.

A new map mode. Riders form a **crew**; every kilometre they ride claims the ground under
them; the map fills with coloured square tiles carrying each crew's emblem. Ground must be
ridden to be held — stop riding an area and you lose it.

Every open question from the first draft has been answered. What follows is the settled design
and the order to build it in.

---

## 1. The decisions

| | |
|---|---|
| Names | **Crews** holding **Territory**. Internal identifier stays `clan`. |
| Grid | **Web Mercator tiles, zoom 13** — square on screen at every latitude. ~2.4 km at Oslo, ~4.9 km at the equator. Admin-tunable. |
| Claiming | Most kilometres in the tile over a **rolling 90 days**. |
| Which rides | **Validated AND real rides only** — ≥10 min moving, ≥1 km. |
| Seeding | A crew's territory starts only at a **2×2 block** of led tiles, then **grows through connected tiles**. |
| Loose tiles | A led tile not reachable from a seed is **not drawn at all**. |
| Outline | Each **connected region** gets one border in a darker shade of the crew colour. |
| Colours | **Fixed palette of 24, globally unique**, first come. |
| Emblems | Small square PNG, server re-encoded; admin can remove. No logo → **generated pixel block with initials**. |
| History | A trip is **stamped with the rider's crew at ingest** and keeps it forever. |
| Membership | **One crew at a time, 7-day cooldown** after leaving. |
| Joining | **Open**, **approval**, or **invite-only** — leader's choice. |
| Founding | Any rider with **at least one validated trip**. |
| Crew size | **No cap.** |
| Leadership | **Officers** from the start, **plus** auto-handover if the leader is idle 90 days. |
| Disbanding | Colour released at once; territory **fades naturally** over the 90-day window. |
| Ranking | **Territory held, km²** (mi² for imperial visitors). |
| Elsewhere on site | A small **crew colour dot** beside rider names on existing boards. |
| Rider sign-in | **QR pair with the app**, session lasts **90 days, renewed on use**. |
| Admin sign-in | **QR scan + TOTP** — two factors, stronger than today. |
| Lost phone | **Admin override only** — Erwin verifies, then re-points the identity. |

---

## 2. Signing in without a login

The site has never had passwords and is not getting them. Instead the browser pairs with the
app, which already holds the rider's `store_id`.

```
browser                         server                        eucplanet app
   |  GET /api/v1/pair/new         |                                 |
   |------------------------------>|  mints token + 6-char code,     |
   |<------------------------------|  one use, 3 minutes             |
   |  shows QR and the code        |                                 |
   |                               |<--- POST /api/v1/pair/confirm --|  scan or type code,
   |                               |     {token, store_id}           |  app shows what is
   |  poll /pair/status            |     + the usual attestation     |  being approved
   |------------------------------>|  binds token -> store_id        |
   |<------------------------------|  issues opaque session, 90 days |
```

**The browser never learns the `store_id`.** It gets an opaque session id that the server maps
back, so a screenshot or a shared screen cannot leak a rider's identity key.

The 6-character code exists because a rider on a laptop cannot point its camera at its own
screen. Same token, same expiry, no camera.

### A pairing QR is a phishing primitive

Printed on a poster at a meet-up, it hands a session to whoever owns the poster. Three
mitigations, all required:

1. The app states in plain words what is being authorised, naming the site, and requires a
   deliberate tap.
2. **The rider session is scoped to crew actions only.** It cannot upload a trip, delete one,
   or rename the rider. The worst a stolen session can do is join or leave a crew — reversible,
   and the app can say so.
3. Token is one use, 3 minutes, rate-limited per IP and per `store_id`.

### Admin is the same flow plus TOTP

Scanning proves possession of the phone holding an allowlisted `store_id`; the TOTP code proves
the authenticator. That is **two factors where there is currently one**. The convenience is
real — no hunting for a login page — and nothing is given up.

A `store_id` on its own must never be sufficient for admin: it is a bearer string that travels
in every upload request and sits in phone backups, whereas a TOTP code exists for thirty
seconds on one device.

---

## 3. Data model

New tables. Nothing existing changes except one nullable column on `trips`.

```python
class Clan(Base):                                  # "crew" in the UI
    clan_id      = Column(String, primary_key=True)
    name         = Column(String, unique=True)
    slug         = Column(String, unique=True)
    description  = Column(String)
    colour       = Column(String, unique=True)     # from the fixed palette
    logo_png     = Column(LargeBinary)             # null -> generated placeholder
    join_policy  = Column(String)                  # open | approval | invite
    invite_code  = Column(String)                  # for invite-only
    created_at   = Column(DateTime)
    created_by   = Column(String, ForeignKey("riders.store_id"))

class ClanMember(Base):
    clan_id    = Column(String, primary_key=True)
    store_id   = Column(String, primary_key=True)
    role       = Column(String)       # leader | officer | member
    status     = Column(String)       # active | pending
    joined_at  = Column(DateTime)
    left_at    = Column(DateTime)     # drives the 7-day cooldown

class ClanCell(Base):                 # rebuilt from trips, never written by hand
    tile      = Column(String, primary_key=True)   # "13/x/y"
    clan_id   = Column(String, primary_key=True)
    km        = Column(Float)                      # rolling 90 days
    riders    = Column(Integer)
    first_led = Column(DateTime)                   # ties go to the earliest claim

class PairToken(Base):
    token      = Column(String, primary_key=True)
    code       = Column(String, index=True)        # the typeable form
    store_id   = Column(String)                    # null until confirmed
    purpose    = Column(String)                    # rider | admin
    created_at = Column(DateTime)
    used_at    = Column(DateTime)

class WebSession(Base):
    session_id = Column(String, primary_key=True)  # opaque, what the cookie holds
    store_id   = Column(String)
    scope      = Column(String)                    # crew | admin
    created_at = Column(DateTime)
    last_used  = Column(DateTime)                  # renewal clock
```

On `Trip`: `clan_id = Column(String, nullable=True)`, stamped at ingest from the rider's
membership at that moment.

**Why stamp rather than join through membership:** if territory came from *current* membership,
one rider switching crews would silently redraw months of map. A kilometre belongs to whoever
you rode it for.

---

## 4. Who holds a tile

The existing grid is degree-based (`ingest/geo.cell_id`). Territory uses **Web Mercator tiles**
instead, because they are square on screen at every latitude and an emblem dropped into one is
never stretched. Both grids coexist — the heatmap keeps its own.

Tiles are recomputed from the stored `TripTrack`, not from raw uploads, so **the entire history
can be rebuilt**: tracks exist for 1250 of 1262 trips (99.0%). The 12 without contribute only
their start point, exactly as they do today.

1. A tile is **led** by the crew with the most kilometres in it over the last 90 days, counting
   only validated real rides.
2. A lead needs **at least 1 km** in the tile, so a single clipped corner does not count.
   Admin-tunable; the 2×2 rule below does most of the work.
3. **Seeding.** A crew's territory exists only where it holds a **2×2 block** of led tiles.
4. **Growth.** From any seed, territory extends through every edge-adjacent led tile. A crew may
   hold several disconnected regions, each needing its own 2×2 to start.
5. **Loose tiles are not drawn.** A led tile unreachable from a seed shows nothing.
6. **Ties** go to the earliest `first_led`, so the map does not flicker between rebuilds.

The seed rule has one deliberate consequence: **a rider who only commutes a straight line never
seeds**, because a one-tile-wide corridor cannot contain a 2×2. Territory rewards covering
ground, not repeating a route.

### Computing it

```
for each crew:
    led    = {tile : crew leads it}
    seeds  = {tiles in any 2x2 block fully inside led}
    region = flood fill from seeds through led, edge-adjacent
    draw region, outline its boundary, place emblems in its blocks
```

Flood fill is O(tiles). At zoom 13 a 50 km ride crosses roughly twenty tiles, so the whole
dataset is tens of thousands of rows — nothing.

---

## 5. Rendering

Only in this mode. The heatmap and every existing layer are untouched.

- **One GeoJSON source, one fill layer.** Each feature is a tile polygon carrying `colour` and
  `clan_id`; MapLibre paints with `fill-color: ["get", "colour"]`.
- **One line layer** for region boundaries — the outline is drawn around each connected region,
  not around each tile, so territory reads as land rather than graph paper.
- **Emblems sit on blocks.** Within a region, find maximal rectangles of tiles; place one emblem
  per block, centred, scaled to the shorter side. A 3×2 block gets an emblem sized to the 2.
  Largest-rectangle-in-a-histogram, O(rows × cols).
  - 1×1 never carries an emblem
  - `icon-allow-overlap: false` so emblems thin out as you zoom away
- Blocks are computed client-side from the tile set, so they adapt to zoom without a round trip.

### Emblems

Leader uploads an image; the server crops to square, resizes to 128 px, re-encodes to PNG and
strips metadata. Admin can remove one, and the crew falls back to its placeholder.

**The placeholder is generated, not blank:** a small pixel pattern derived deterministically
from the crew name, in the crew colour, with one or two initials over it. Every crew looks
distinct from the moment it is founded, and most will never need to upload anything.

---

## 6. The UI

The **Crews** dock button sits second, after Riders. It shows the territory map plus the crew
ranking by km² held.

One button, top right of the section:

- **not paired** → "Sign in with the app", opening the QR and the 6-character code
- **paired, no crew** → "Create or join a crew"
- **paired, in a crew** → "Manage crew"

Everything happens in a panel over the map: create, edit colour, emblem and description, choose
the join rule, review join requests, promote an officer, leave. No separate page, no second nav.

Elsewhere on the site, a rider's crew shows as a **small colour dot** beside their name on the
existing boards, with the crew name on hover. The other modes stay about riders.

---

## 7. Build order

Each phase ends somewhere runnable. Nothing leaves the laptop.

| # | what | done when |
|---|---|---|
| 1 | Tables, migrations, Mercator tiling helper | tests pass |
| 2 | Pairing endpoints + sessions, no UI | a pair completes with curl; rate limits hold |
| 3 | Crew CRUD behind a paired session | create/join/approve/leave work in a local browser |
| 4 | `ClanCell` aggregation: rolling window, lead, seed, flood fill | a seeded local DB gives sane territory |
| 5 | Map mode: fills, outlines, emblem blocks, ranking | looks right at several zooms |
| 6 | Placeholder emblem generator + upload pipeline | both paths render on the map |
| 7 | App branch off `next-experimental`: QR scanner + approval screen | pairs against the laptop |
| 8 | Admin: QR+TOTP, crew moderation, kill switch, EN strings | the mode can be switched off whole |

### Running it locally

- eucstats: `uvicorn main:app --reload` on branch `feat/clans`. Commits local, **no push**.
- eucplanet: branch off `next-experimental`, pointed at the laptop's LAN address.
- The phone must reach the laptop — same wifi, LAN IP. Plain HTTP is fine for this; the camera
  permission the scanner needs is an app permission, not a browser one.

---

## 8. Things that will need deciding later, but not yet

- **The 24-colour palette caps crews at 24.** Fine for a test phase, and it keeps the map
  readable. When it binds, the answer is probably colour × pattern (24 × 4 = 96), which the
  renderer should be built to allow even if only solid fills ship.
- **No privacy floor** is a deliberate departure from the heatmap's 2-rider rule. It is
  defensible because joining a crew is a choice, where appearing in the heatmap is not — but a
  one-member crew's territory is a public map of one person's riding, under a name they chose.
  Worth revisiting once real crews exist.
- **Imperial units**: the ranking is km², and the site already has unit profiles. mi² needs to
  come from the same place, not a second conversion.
