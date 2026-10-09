"""The loudest moment on the site, and a rename switched it off for a whole branch.

The rider boards used to publish `store_id`. This branch renamed it to `id`, because a store_id
is proof of identity at `pair/confirm` and has no business in a payload anybody can read. The
rename landed; the announcer's gate did not:

    if not entries or not entries[0].get("store_id"):
        continue

`store_id` was simply absent now, so every board fell through, `snap` stayed empty, and no
"New record!" message could be sent by any path. Nothing failed. Nothing logged. A reviewer
found it by reading, and then pointed out the sharper thing: this branch's stated principle is
that a class is only closed when something checks it, and it had shipped a test for the copy
register while leaving the class that silently killed the announcements guarded by nobody.

Two tests, because the class has two halves.

The first needs no fixture at all: it reads `services/telegram.py` for every key the announcer
pulls out of a board entry and checks each one against a real board's real output. Rename a
published field in either direction and this goes red naming the field, which is the whole
failure mode -- a producer and a consumer agreeing about a string, with nothing between them.

The second runs the thing. A previous snapshot says one rider holds the board, the data says
another does, and a message has to come out naming both of them -- which also covers the second
half of the same regression, the lookup that resolves the beaten rider. It used to read the
primary key and now reads the handle, and getting that wrong does not blank the message, it
makes it say the wrong name or no name at all.
"""
import json
import pathlib
import re
import sys
from datetime import datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from conftest import HANDLE                     # noqa: E402
from services import territory                  # noqa: E402
from repository.riders import RiderRepo         # noqa: E402
from repository.trips import TripRepo           # noqa: E402
from services import stats, telegram            # noqa: E402
from services.aggregator import Aggregator      # noqa: E402

TELEGRAM_PY = ROOT / "services" / "telegram.py"
NL = chr(10)


def _seed(db):
    """Two riders, one clearly ahead of the other on every board."""
    RiderRepo(db).upsert("win", "google_play", "Winner", "NO")
    RiderRepo(db).upsert("old", "google_play", "Previous", "SE")
    tr, agg = TripRepo(db), Aggregator(db)

    def t(uuid, store, dist, day, vmax, g):
        return tr.insert_trip(
            trip_uuid=uuid, rider_store_id=store, distance_km=dist,
            start_utc=datetime(2026, 6, day), end_utc=datetime(2026, 6, day, 2),
            max_speed=vmax, max_gforce=g, country="NO", start_lat=69.6, start_lon=18.9,
            validation_status="validated", created_at=datetime(2026, 6, day))

    agg.apply(t("a1", "win", 90.0, 1, 55.0, 4.0))
    agg.apply(t("a2", "win", 60.0, 2, 52.0, 3.5))
    agg.apply(t("b1", "old", 5.0, 1, 20.0, 1.0))
    db.commit()


def _seed_crew(db):
    """One crew holding ground, so `territory.ranking` has a row to publish."""
    import models
    from services import crews, settings
    settings.set_crews(db, enabled=True, zoom=14, window_days=90, seed=2, cooldown_days=7,
                       max_members=0, opacity=0.55, creation_open=True)
    c = crews.create(db, "win", "Contract Crew", "", join_policy="open")
    row = db.get(models.Clan, c.clan_id)
    row.terr_best_tiles = row.terr_tiles = 9
    row.terr_km2 = row.terr_best_km2 = 9.0
    db.commit()
    return row


ENTRY_KEY = re.compile(
    r"""(?:entries\[0\]|rows\[0\]|ctop|top)(?:\.get\(|\[)["']([a-z_]+)["']""")

# The announcer reads board entries under the first comment, group entries under the second
# and the crew board under the third; everything after the last is message-building, where the
# keys are optional and read with `.get()`.
#
# The crew section earned its own marker the moment it existed: it reads `slug`, which a
# country entry does not publish, so without the split the scan attributed a crew key to the
# group sources and this test failed on correct code. A fourth source with a fourth shape
# needs a fourth boundary, not a looser assertion.
BOARD_SECTION = "# individual rider leaderboards"
GROUP_SECTION = "# group standings"
CREW_SECTION = "# The crew holding #1 on the territory board"
MESSAGE_SECTION = "# group a rider's simultaneous new #1s"


def _keys_read_from_an_entry():
    """What the announcer requires of a board entry and of a group entry, from its source.

    Scanned rather than listed, and -- this is the part that was missing -- actually used. The
    first version of this scanned the keys, asserted the scan was non-empty, interpolated it
    into a failure message and then checked a hardcoded `{"id"}` instead: the second copy its
    own docstring warned against, three lines below the warning. Renaming the gate left it
    green.
    """
    src = TELEGRAM_PY.read_text(encoding="utf-8")
    i, j, k, m = (src.index(BOARD_SECTION), src.index(GROUP_SECTION),
                  src.index(CREW_SECTION), src.index(MESSAGE_SECTION))
    return (sorted(set(ENTRY_KEY.findall(src[i:j]))),
            sorted(set(ENTRY_KEY.findall(src[j:k]))),
            sorted(set(ENTRY_KEY.findall(src[k:m]))))


def test_the_announcer_reads_keys_the_boards_actually_publish(db):
    """The gate that went quiet, and anything else shaped like it."""
    _seed(db)
    board_keys, group_keys, crew_keys = _keys_read_from_an_entry()
    assert board_keys and group_keys and crew_keys, (
        "found no entry keys in telegram.py; the scan's shape has moved, and a scan that "
        "finds nothing is a test that checks nothing")

    # every rider board, the three group standings, and the crew board
    sources = {("board", name): fn for name, fn in stats.BOARDS.items()}
    sources[("group", "country")] = stats.by_country
    sources[("group", "wheel")] = stats.by_wheel
    sources[("group", "brand")] = stats.by_brand
    # The crew board needs a crew on it before it publishes anything, and `_seed` makes
    # riders, not crews. Without this the source returns nothing, the loop skips it, and the
    # newest of the four announcements is the one nobody is checking.
    _seed_crew(db)
    sources[("crew", "ranking")] = territory.ranking

    missing, checked = [], 0
    for (kind, name), fn in sorted(sources.items()):
        rows = fn(db, 1)
        # Some boards want data this seed does not produce -- `acc30_b` needs an acceleration
        # run. The agreement only exists where there is an entry; the floor below keeps this
        # from passing by checking nothing.
        if not rows:
            continue
        checked += 1
        got = set(rows[0])
        # The keys THIS SECTION of the announcer reads, not a second copy of them written
        # here. Rename either side and the other goes red naming the field.
        wanted = {"board": board_keys, "group": group_keys, "crew": crew_keys}[kind]
        for key in wanted:
            if key not in got:
                missing.append(
                    f"  {kind} {name} publishes {sorted(got)} -- telegram.py reads {key!r}")
    assert checked >= 6, (
        f"only {checked} sources produced an entry, so this checked almost nothing")
    assert not missing, (
        f"telegram.py reads {board_keys} out of a board entry, {group_keys} out of a group"
        f" entry and {crew_keys} out of a crew entry. A key it reads is not published, so the"
        f" gate falls through and no announcement can ever be sent:" + NL + NL.join(missing))


def test_a_takeover_actually_produces_a_message(db, monkeypatch):
    """Run it, rather than reasoning about it.

    The regression was invisible precisely because every individual piece still worked. The
    only thing that catches it is asking for the message.
    """
    _seed(db)
    from services import settings

    # Exactly ONE board changes hands, because a rider sweeping several takes the "swept N #1
    # spots" branch -- which lists titles and names nobody, and naming the beaten rider is the
    # half of this that exercises the handle lookup.
    #
    # The snapshot is read off the boards rather than assumed: neither of these two riders
    # tops everything (`old` holds Globe Trotter and Busiest Day on this seed), so pinning it
    # to one of them hands several over at once.
    snap, handed_over = {}, None
    for board, fn in stats.BOARDS.items():
        rows = fn(db, 1)
        if not rows:
            continue
        snap[board] = rows[0]["id"]
        if handed_over is None and rows[0]["id"] == HANDLE("win"):
            handed_over = board
    assert handed_over, "no board is held by the winner, so nothing can change hands"
    snap[handed_over] = HANDLE("old")
    settings.set_meta(db, "tg_record_holders", json.dumps(snap))
    db.commit()

    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda text, cfg=None: sent.append(text))
    monkeypatch.setattr(telegram, "get_config", lambda: {
        "enabled": True, "bot_token": "t", "chat_id": "c", "link_url": "https://example.test",
        "tk_rider": True, "tk_country": False, "tk_wheel": False, "tk_brand": False})
    monkeypatch.setattr(telegram, "is_configured", lambda cfg=None: True)

    telegram.check_records()

    assert sent, ("the board changed hands and nothing was announced -- which is exactly what "
                  "the renamed key did, silently, for a whole branch")
    joined = NL.join(sent)
    assert "Winner" in joined, f"the new champion is not named: {joined}"
    # and the beaten rider, resolved from the snapshot's handle rather than a primary key
    assert "Previous" in joined, (
        "the rider who was beaten is not named, so the snapshot handle did not resolve: "
        + joined)


def test_the_snapshot_records_the_handle_and_not_the_store_id(db, monkeypatch):
    """The other half of why this is published at all.

    The announcer writes whoever is #1 into `app_meta` and reads it back on the next trip. If
    it ever stores the store_id again, a readable copy of proof-of-identity is sitting in a
    settings table, which is the reason the boards stopped publishing it.
    """
    _seed(db)
    from services import settings

    monkeypatch.setattr(telegram, "send_message", lambda text, cfg=None: None)
    monkeypatch.setattr(telegram, "get_config", lambda: {
        "enabled": True, "bot_token": "t", "chat_id": "c", "link_url": "https://example.test",
        "tk_rider": True, "tk_country": False, "tk_wheel": False, "tk_brand": False})
    monkeypatch.setattr(telegram, "is_configured", lambda cfg=None: True)

    telegram.check_records()
    db.expire_all()
    raw = settings.get_meta(db, "tg_record_holders", "") or "{}"
    held = set(json.loads(raw).values())
    assert held, "nothing was recorded, so the gate fell through"
    assert "win" not in held and "old" not in held, (
        f"a store_id was written into the snapshot: {sorted(held)}")
    assert HANDLE("win") in held, f"the champion's handle is not in the snapshot: {sorted(held)}"
