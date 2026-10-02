# Clans / territory mode — implementation plan

Status: **plan only, nothing built.** Branch `feat/clans`, local, nothing pushed.

A new map mode in which groups of riders colour the map by riding. Riders form a group, every
kilometre they ride counts toward whoever holds the ground under them, and the map fills with
coloured rectangles carrying each group's logo.

---

## 1. What to call it

The *group* and the *mode* want separate names, because the dock button names the mode and the
thing you join is the group. Options, with what each one implies:

| group | mode | reads as |
|---|---|---|
| **Crew** | **Turf** | what EUC riders already say — "my riding crew". Warm, local, not martial. |
| **Clan** | **Territory** | gamer-familiar and unambiguous, slightly tribal. |
| **Pack** | **Range** | wolfpack; good for a group that rides together. |
| **Colours** | **Colours** | "ride for your colours" — one word for both, and it is literally what the map shows. |

Recommendation: **Crews** holding **Turf**. It is the word riders already use, it survives
translation into the other eighteen locales better than "clan" (which carries baggage in a few),
and "turf" says *this is about ground covered* rather than about conquest.

This is a naming decision for Erwin; everything below is independent of it. The code will use
`clan` as the internal identifier regardless, because renaming a column later is expensive and
renaming a label is not.

---

## 2. Signing in without a login

This is the hard part, and the suggestion in the brief — scan a QR with the app — is the right
shape. It is the same flow a TV uses to sign in to a streaming service, and it fits what already
exists: the phone holds a `store_id`, the app already talks to the API, and the site has never
had passwords.

### The flow

```
browser                         server                        eucplanet app
   |  GET /api/v1/pair/new         |                                 |
   |------------------------------>|  mints pair_token (one use,     |
   |<------------------------------|  3 min, bound to nothing yet)   |
   |  shows QR + 6-char code       |                                 |
   |                               |                                 |
   |                               |<--- POST /api/v1/pair/confirm --|  rider scans,
   |                               |     {pair_token, store_id}      |  sees what they are
   |                               |     + the usual attestation     |  approving, taps yes
   |  poll /api/v1/pair/status     |                                 |
   |------------------------------>|  token -> store_id, one use     |
   |<------------------------------|  sets an opaque session cookie  |
   |  now acting as that rider     |                                 |
```

The browser never learns the `store_id`. It gets an opaque session that the server maps back,
so a screenshot of the page cannot leak a rider's identity key.

### Why a 6-character code as well as the QR

A QR needs a camera pointed at a screen. A rider sitting on the sofa with the site open on a
laptop can type six characters into the app instead. Same token, same expiry, no camera.

### The risk worth naming before we build it

**A pairing QR is a phishing primitive.** Print one on a poster at a meet-up, and anyone who
scans it hands the poster's owner a session as themselves. Three mitigations, all cheap:

1. The app shows exactly what is being authorised, in plain words, with the site name — and
   requires a deliberate tap. Not a silent confirm.
2. **The session is scoped to clan actions only.** It cannot upload a trip, cannot delete one,
   cannot change the rider's name. The worst a stolen session does is join or leave a crew,
   which a rider can undo and which the app can notify them about.
3. Short expiry on the token (3 min), one use, rate-limited per IP and per `store_id`.

A fourth, if we want belt and braces: the app shows the rough location of the browser asking.
Deferred — it needs an IP-geolocation call the site currently does not make.

### What the app has to add

A QR scanner and one approval screen. That is the whole app-side surface for v1 — new branch
from `next-experimental`.

---

## 3. Data model

New tables. Nothing existing changes except one nullable column on `trips`.

```python
class Clan(Base):
    clan_id       = Column(String, primary_key=True)   # uuid
    name          = Column(String, unique=True)        # display name
    slug          = Column(String, unique=True)        # url-safe
    description   = Column(String)
    colour        = Column(String)                     # hex, validated against a palette
    logo_png      = Column(LargeBinary)                # small, square, server-reencoded
    join_policy   = Column(String)                     # 'open' | 'approval' | 'invite'
    created_at    = Column(DateTime)
    created_by    = Column(String, ForeignKey("riders.store_id"))

class ClanMember(Base):
    clan_id   = Column(String, ForeignKey("clans.clan_id"), primary_key=True)
    store_id  = Column(String, ForeignKey("riders.store_id"), primary_key=True)
    role      = Column(String)      # 'leader' | 'member'
    status    = Column(String)      # 'active' | 'pending'
    joined_at = Column(DateTime)

class ClanCell(Base):                                  # the territory, rebuilt by the aggregator
    zoom     = Column(Float, primary_key=True)
    cell     = Column(String, primary_key=True)
    clan_id  = Column(String, primary_key=True)
    km       = Column(Float)
    riders   = Column(Integer)                         # distinct members who rode it

class PairToken(Base):
    token      = Column(String, primary_key=True)
    code       = Column(String)                        # the 6-char typeable form
    store_id   = Column(String)                        # null until confirmed
    created_at = Column(DateTime)
    used_at    = Column(DateTime)
```

And on `Trip`: `clan_id = Column(String, nullable=True)` — **stamped at ingest**, from the
rider's membership at the moment the ride is uploaded.

### Why stamp the trip rather than join through membership

If territory is computed from *current* membership, then one rider switching crews silently
redraws months of map. Stamping at ingest means a kilometre belongs to whoever you rode it for,
which is both fairer and stable. The cost is that a crew founded today starts with an empty map.

Offer one deliberate exception: **"bring my history"**, a one-time action when joining that
stamps a rider's existing trips. Once per rider, not per join, so it cannot be used to launder
a back-catalogue between crews.

---

## 4. Who owns a cell

The grid already exists: `ingest/geo.cell_id` quantises to degree cells, `MapCell` stores them
per zoom with `rider_count` and `total_km`, and the aggregator rebuilds them. Clan territory is
the same shape with an extra key.

Rules, in order:

1. **A cell is owned by the clan with the most kilometres in it.** Not most riders — the brief
   says distance, and distance is what the counters already hold.
2. **A claim needs a floor**, or a single pass-through paints a cell. Start at 5 km, admin
   tunable. Below the floor the cell is unowned and renders as it does today.
3. **Privacy: only cells that already pass the heatmap floor can be coloured.** The heatmap
   requires ≥2 distinct riders in a cell (`hm_floor`) precisely so one person's route cannot be
   located. A clan colour over a cell says "a member of this crew rides here", which is the same
   disclosure. Reusing the existing rule is defensible and needs no new policy.
   *This will make small crews' turf sparse. It is the right default; it can be revisited, but
   not by me unilaterally.*
4. **Ties** go to the earliest claim, recorded on `ClanCell`. A tie that flips every rebuild
   would make the map flicker.
5. Contested cells may render at opacity proportional to the leader's share, so a cell held
   60/40 looks weaker than one held outright. Nice, optional, second pass.

---

## 5. Rendering

Only in this mode. The heatmap and the existing layers stay exactly as they are.

Degree cells are already rectangles in Web Mercator — taller than wide, more so toward the
poles. That is fine and it is honest: it is the grid the site already uses.

- **One GeoJSON source, one fill layer.** Each feature is a cell polygon carrying `colour` and
  `clan_id`; MapLibre paints with `fill-color: ["get", "colour"]`. Cheap, and it scales to
  thousands of cells.
- **Logos sit on blocks, not cells.** Find maximal rectangles of same-clan contiguous cells and
  place one logo per block, centred, scaled to the smaller dimension. The classic
  largest-rectangle-in-a-histogram sweep does this in O(rows × cols), which is nothing at these
  sizes.
  - a 1×1 block gets no logo — it would be a smudge
  - a 2×2 or larger square gets the logo inset to ~70% of the cell block
  - a 3×2 gets the same logo centred, sized to the 2
- Logos are registered with `map.addImage` once per clan, drawn by a symbol layer at block
  centroids with `icon-allow-overlap: false` so they thin out as you zoom away.

Blocks are recomputed on the client from the cell set, not stored. The server sends cells; the
client decides where logos fit at the current zoom.

---

## 6. Phases

Each phase ends somewhere runnable, and nothing leaves the laptop.

| # | what | done when |
|---|---|---|
| 0 | **Decisions** — names, privacy floor, claim floor | Erwin has answered §1, §4.2, §4.3 |
| 1 | Data model + migrations + pairing endpoints, no UI | tests pass; a pair can be completed with curl |
| 2 | Clan CRUD behind a paired session: create, edit, join, approve, leave | tests pass; works in a local browser |
| 3 | `ClanCell` aggregation + API, including the floors | a seeded local DB produces sane territory |
| 4 | Map mode: dock button, rectangles, logo blocks | looks right locally at several zooms |
| 5 | App branch from `next-experimental`: QR scanner + approval screen | pairs against the local server |
| 6 | Admin controls, i18n (EN only first), rate limits | the admin tree can switch the whole mode off |

### Local only

- eucstats: `uvicorn main:app --reload`, branch `feat/clans`, commits local, **no push**.
- eucplanet: new branch off `next-experimental`, pointed at the laptop's address rather than
  prod.
- The app will need the local server reachable from the phone — same wifi and the laptop's LAN
  IP, since pairing is an HTTP round trip. Plain HTTP is fine on a LAN for this; the camera
  permission the scanner needs is an app permission, not a browser one.

---

## 7. Decisions needed before phase 1

1. **Names** — §1. Default if Erwin does not care: Crews / Turf.
2. **Privacy floor** — §4.3. Reuse the heatmap floor (recommended), or let a crew of one colour
   the map. This is a real privacy call, not a tuning knob.
3. **History** — §3. Stamp at ingest only, or offer "bring my history" once per rider.
4. **Who may create a crew** — anyone who pairs, or only riders with some minimum distance?
   An open door invites one-rider crews cluttering the map; a threshold is arbitrary but
   effective. Suggest: any rider with at least one validated trip.
5. **Logos** — accepting uploaded images means accepting whatever people upload. Server-side
   re-encode to a fixed small PNG is mandatory. An admin needs a way to remove one, and the
   repo-hygiene rule about public data applies.
