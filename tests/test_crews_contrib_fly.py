"""A name in Best riders flies to where that rider rides, and what that costs in privacy.

The fold under a crew listed names and nothing happened when you pressed one. The note in
crews.js said it could not: "`flyToRider`, which needs a position this payload does not
carry". Erwin pointed at the boards -- "we already allow this in Riders" -- and the boards
have published a coordinate for every row since before crews existed, so what was missing was
the field and not the gesture.

A coordinate is not a name, though, and this list does not follow the boards' rule about
names: `contributors` publishes every rider who rode for the crew, while `_board` filters on
`consent_public`. So the two are held apart on purpose and the difference is what this file
pins:

  * the name of a rider who has turned public listing off stays on their crew's list, because
    that is what the list is for and it is how it already behaved;
  * their POSITION does not appear, because "a rider's last start point is their street"
    (services/privacy.py) and the leaderboards would not publish theirs either.

The failure that matters here is silent in every other test: a payload that carries lat/lon
for everybody still renders correctly, still passes the shape tests, and quietly puts an
opted-out rider's street in a public JSON response.
"""
import pathlib

import pytest

from models import Rider, Trip, utcnow
from services import crews, settings, territory

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"
PUBLIC_PY = ROOT / "web" / "public.py"

OSLO = (59.9139, 10.7522)
PARIS = (48.8566, 2.3522)


def _rider(db, sid, consent=True, handle=None):
    """A rider with one validated ride, which is what founding a crew needs. The seed ride
    carries no crew and no position, so it never shows up as a contribution or a coordinate."""
    r = Rider(store_id=sid, display_name=sid.title(), flag="NO", consent_public=consent)
    db.add(r)
    db.commit()
    if handle is not None:
        # After the insert: a handle passed to the constructor is replaced by the minter.
        r.public_id = handle
        db.commit()
    db.add(Trip(trip_uuid=f"seed-{sid}", rider_store_id=sid, distance_km=5.0,
                validation_status="validated", start_utc=utcnow(), end_utc=utcnow()))
    db.commit()
    return r


def _rode(db, sid, clan_id, km, at, where=None, uuid=None):
    db.add(Trip(trip_uuid=uuid or f"cf-{sid}-{km}", rider_store_id=sid, clan_id=clan_id,
                distance_km=km, validation_status="validated",
                start_lat=where[0] if where else None,
                start_lon=where[1] if where else None,
                start_utc=at, end_utc=at))
    db.commit()


@pytest.fixture
def crew(db):
    settings.set_crews(db, enabled=True, zoom=14, window_days=90, seed=2, cooldown_days=7,
                       max_members=0, opacity=0.55, creation_open=True)
    _rider(db, "fly1")
    c = crews.create(db, "fly1", "Flyers")
    return c


def _row(rows, sid):
    want = sid.title()
    for r in rows:
        if r["name"] == want:
            return r
    raise AssertionError(f"{want} is not on the list: {[r['name'] for r in rows]}")


def test_a_rider_on_the_list_carries_the_position_their_name_is_pressed_for(db, crew):
    """Without this the fold is five names and no gesture, which is where it started."""
    _rode(db, "fly1", crew.clan_id, 12.0, utcnow(), OSLO)
    row = _row(territory.contributors(db, crew.clan_id), "fly1")
    assert (row["lat"], row["lon"]) == OSLO, (
        f"the row came back as {row.get('lat')},{row.get('lon')}, and the rider's last ride "
        f"started at {OSLO}")


def test_the_position_is_the_latest_ride_not_the_first(db, crew):
    """The boards publish a rider's latest start. Two surfaces of this site putting the same
    person in two different cities is the kind of difference a reader notices and cannot
    explain."""
    from datetime import timedelta
    now = utcnow()
    _rode(db, "fly1", crew.clan_id, 30.0, now - timedelta(days=30), PARIS, uuid="cf-old")
    _rode(db, "fly1", crew.clan_id, 4.0, now, OSLO, uuid="cf-new")
    row = _row(territory.contributors(db, crew.clan_id), "fly1")
    assert (row["lat"], row["lon"]) == OSLO, (
        "the position is not the latest ride's start point, so it disagrees with the boards")


def test_a_rider_who_opted_out_of_public_listing_keeps_the_name_and_loses_the_position(db, crew):
    """The whole point of the file. `consent_public=False` is the switch the leaderboards
    filter on, and it has never hidden a crew contributor's name -- so the one thing this
    change must not do is hand out their street along with it.
    """
    _rider(db, "quiet1", consent=False)
    _rode(db, "quiet1", crew.clan_id, 50.0, utcnow(), PARIS)
    row = _row(territory.contributors(db, crew.clan_id), "quiet1")
    assert row["lat"] is None and row["lon"] is None, (
        f"an opted-out rider's position was published as {row['lat']},{row['lon']}")
    assert row["km"] == 50.0, "their riding is still counted, which is what the list is about"


def test_a_rider_with_no_ride_carrying_a_start_point_is_simply_not_pressable(db, crew):
    """A real state and not an error: the app can upload a trip with no position, and the
    client renders the row with no press rather than a control that cannot work."""
    _rode(db, "fly1", crew.clan_id, 7.0, utcnow(), None)
    row = _row(territory.contributors(db, crew.clan_id), "fly1")
    assert row["lat"] is None and row["lon"] is None


def test_an_unpublishable_handle_takes_the_position_with_it(db, crew):
    """`publishable_handle` exists because a reader took a polluted id off a public board and
    paired as that rider. A row whose id is suppressed has nothing to key the daily jitter on
    either, so it gets no coordinate."""
    _rider(db, "bad1", handle="h-bad1")          # the shape found in the wild
    _rode(db, "bad1", crew.clan_id, 9.0, utcnow(), OSLO)
    row = _row(territory.contributors(db, crew.clan_id), "bad1")
    assert row["id"] is None, "the guard this test is built on has moved"
    assert row["lat"] is None and row["lon"] is None, (
        "a rider whose handle is not publishable was given a position anyway")


def test_the_positions_cost_one_query_however_many_riders(db, crew):
    """`stats._rider_loc` is one query per rider, and this fold opens on a tap in a panel that
    is already waiting on a roster and a territory. Twenty names must not be twenty round
    trips."""
    import sqlalchemy as sa
    now = utcnow()
    for i in range(6):
        sid = f"many{i}"
        _rider(db, sid)
        _rode(db, sid, crew.clan_id, 10.0 + i, now, (59.9 + i / 100, 10.7))
    seen = []

    def watch(conn, cursor, statement, *rest):
        seen.append(statement)

    sa.event.listen(db.get_bind(), "before_cursor_execute", watch)
    try:
        rows = territory.contributors(db, crew.clan_id)
    finally:
        sa.event.remove(db.get_bind(), "before_cursor_execute", watch)
    assert len({r["name"] for r in rows}) >= 6
    starts = [s for s in seen if "start_lat" in s]
    assert len(starts) <= 2, (
        f"reading six riders' positions took {len(starts)} queries:\n"
        + "\n".join(starts[:4]))


def test_the_panel_presses_the_hosts_own_handler_and_the_host_hands_it_over():
    """Two halves in two files, and either one alone is a name that does nothing.

    The ring, the daily jitter that keeps the dot off somebody's doorstep and the closing of
    the panel are all decisions made once, inside the host's `flyToRider`. A second copy
    inside the crews panel would be a second privacy decision to keep in step with it.
    """
    js = CREWS_JS.read_text(encoding="utf-8")
    host = PUBLIC_PY.read_text(encoding="utf-8")
    assert "flyToRider:flyToRider" in host, (
        "the host does not hand `flyToRider` to the crews panel, so H.flyToRider is undefined "
        "and every name in Best riders is dead")
    assert "H.flyToRider(row)" in js, "the panel does not call the host's handler"
    assert "c.lat != null && c.lon != null" in js, (
        "the name is made pressable without checking there is a position behind it, which is "
        "the dead control this file keeps paying for")
