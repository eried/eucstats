"""The admin's own crew controls, which had no tests at all.

A reviewer put it precisely: the restore regression lived in the function immediately below
the one I had changed, and nothing in the suite imported this module. Disband here does what
a leader's disband does, and restore has to undo it completely, or it hands back the
leaderless shell the rest of the feature works to make impossible.

These drive the service functions the admin routes call rather than the HTML forms, because
the forms need a TOTP-backed admin session; what matters is that fold and unfold are
inverses.
"""
import pytest

from models import Clan, ClanCell, ClanMember, Rider, Trip, utcnow
from services import crews


def _rider(db, sid):
    db.add(Rider(store_id=sid, display_name=sid.title(), flag="NO"))
    db.commit()
    db.add(Trip(trip_uuid=f"a-{sid}", rider_store_id=sid, distance_km=5.0,
                validation_status="validated", start_utc=utcnow(), end_utc=utcnow()))
    db.commit()
    return sid


def _active(db, clan_id):
    return (db.query(ClanMember)
            .filter(ClanMember.clan_id == clan_id, ClanMember.status == "active",
                    ClanMember.left_at.is_(None)).count())


def test_folding_and_restoring_are_inverses(db):
    _rider(db, "ad1")
    _rider(db, "ad2")
    c = crews.create(db, "ad1", "Round Trip", join_policy="open")
    crews.join(db, "ad2", c.clan_id)
    name, slug, colour = c.name, c.slug, c.colour

    crews._retire(c)
    for m in db.query(ClanMember).filter(ClanMember.clan_id == c.clan_id,
                                         ClanMember.left_at.is_(None)).all():
        m.left_at = utcnow()
        m.status = "disbanded"
    db.commit()
    assert c.disbanded_at is not None and c.name != name

    assert crews.unretire(db, c) is None
    db.commit()
    db.refresh(c)
    assert (c.name, c.slug, c.colour) == (name, slug, colour)
    assert c.disbanded_at is None
    assert _active(db, c.clan_id) == 2


def test_restoring_reaches_a_rider_who_read_the_fold_notice(db):
    """`last_fold` rewrites the mark to `disbanded_seen` the moment the rider opens the
    panel, and restore only looked for `disbanded` -- so anyone who had actually read their
    notification was silently left out of the crew that came back."""
    _rider(db, "sn1")
    _rider(db, "sn2")
    c = crews.create(db, "sn1", "Seen It", join_policy="open")
    crews.join(db, "sn2", c.clan_id)
    crews.disband(db, "sn1", c.clan_id)

    told = crews.last_fold(db, "sn2")
    assert told and told["crew"] == "Seen It", "they have to actually be told"

    assert crews.unretire(db, c) is None
    db.commit()
    assert _active(db, c.clan_id) == 2, "reading the notice must not cost them their place"


def test_a_restored_crew_always_has_somebody_in_charge(db):
    _rider(db, "lc1")
    _rider(db, "lc2")
    c = crews.create(db, "lc1", "Who Runs It", join_policy="open")
    crews.join(db, "lc2", c.clan_id)
    crews.disband(db, "lc1", c.clan_id)
    crews.unretire(db, c)
    db.commit()
    leaders = (db.query(ClanMember)
               .filter(ClanMember.clan_id == c.clan_id, ClanMember.role == "leader",
                       ClanMember.left_at.is_(None)).count())
    assert leaders == 1


def test_a_fold_clears_the_ground_so_the_map_agrees_with_the_board(db):
    """The admin's disband deleted the cells and a leader's did not, so the same action left
    two different maps standing for up to a rebuild interval."""
    _rider(db, "gc1")
    c = crews.create(db, "gc1", "Ground Zero")
    db.add(ClanCell(tile="14/1/1", clan_id=c.clan_id, km=5.0, riders=1))
    db.commit()
    crews.disband(db, "gc1", c.clan_id)
    assert db.query(ClanCell).filter(ClanCell.clan_id == c.clan_id).count() == 0


def test_a_name_taken_since_the_fold_stops_the_restore_with_a_sentence(db):
    _rider(db, "nt1")
    _rider(db, "nt2")
    c = crews.create(db, "nt1", "Wanted Name")
    crews.disband(db, "nt1", c.clan_id)
    crews.create(db, "nt2", "Wanted Name")
    err = crews.unretire(db, c)
    assert err and "Wanted Name" in err
    db.refresh(c)
    assert c.disbanded_at is not None, "a refused restore changes nothing"


def test_the_spent_notice_rows_do_not_pile_up_for_ever(db):
    """`*_seen` rows exist only to stop a notice being shown twice, and the notice is bounded
    to a week, so after that they can never be read again by anything."""
    from datetime import timedelta
    from services.retention import _sweep_spent_notices
    _rider(db, "sw1")
    _rider(db, "sw2")
    c = crews.create(db, "sw1", "Old News", join_policy="approval")
    crews.join(db, "sw2", c.clan_id)
    crews.decide(db, "sw1", c.clan_id, "sw2", accept=False)
    # Reading reports; acknowledging is what spends it. Reading used to do both, which is
    # why a rider was never told they had been turned down -- see `crews.last_answer`.
    assert crews.last_answer(db, "sw2")      # reported
    crews.mark_notice_seen(db, "sw2", "declined")      # and now spent

    row = (db.query(ClanMember)
           .filter(ClanMember.store_id == "sw2", ClanMember.clan_id == c.clan_id).one())
    assert row.status == "declined_seen"
    row.left_at = utcnow() - timedelta(days=40)
    db.commit()

    assert _sweep_spent_notices(db, utcnow()) == 1
    assert (db.query(ClanMember)
            .filter(ClanMember.store_id == "sw2", ClanMember.clan_id == c.clan_id)
            .count()) == 0


def test_a_notice_nobody_ever_read_is_swept_too(db):
    """The row a rider who never came back leaves behind.

    While reading a notice retired it, essentially every one of these reached `*_seen` within
    a page load, so the sweep saw them all. Reading is a peek now -- it had to become one or
    the notice never reached anybody -- which means a rider who is turned down and never opens
    the panel again leaves a row sitting in `declined` for ever. It stopped being readable
    after seven days, so past thirty it is a tombstone under a different name.
    """
    from datetime import timedelta
    from services.retention import _sweep_spent_notices
    _rider(db, "nv1")
    _rider(db, "nv2")
    c = crews.create(db, "nv1", "Never Looked", join_policy="approval")
    crews.join(db, "nv2", c.clan_id)
    crews.decide(db, "nv1", c.clan_id, "nv2", accept=False)
    # nobody reads it, nobody acknowledges it
    row = (db.query(ClanMember)
           .filter(ClanMember.store_id == "nv2", ClanMember.clan_id == c.clan_id).one())
    assert row.status == "declined", "still waiting to be shown"
    row.left_at = utcnow() - timedelta(days=40)
    db.commit()
    assert _sweep_spent_notices(db, utcnow()) == 1
    assert (db.query(ClanMember)
            .filter(ClanMember.store_id == "nv2", ClanMember.clan_id == c.clan_id)
            .count()) == 0


def test_a_spent_notice_is_kept_while_it_could_still_matter(db):
    from services.retention import _sweep_spent_notices
    _rider(db, "kp1")
    _rider(db, "kp2")
    c = crews.create(db, "kp1", "Recent News", join_policy="approval")
    crews.join(db, "kp2", c.clan_id)
    crews.decide(db, "kp1", c.clan_id, "kp2", accept=False)
    crews.last_answer(db, "kp2")
    assert _sweep_spent_notices(db, utcnow()) == 0


def test_a_crew_nobody_is_left_in_does_not_come_back(db):
    """`unretire` revived whoever it could and appointed a leader only `if back` -- so when
    every member had joined somewhere else since, the crew returned with zero members and
    nobody in charge, and sat in the public join list under the default approval policy
    waiting for an approver who could never exist. That is word for word the shell the
    last-one-out branch of `leave()` was written to abolish, rebuilt by the admin screen and
    reported to the admin as "restored with its riders"."""
    _rider(db, "gh1")
    _rider(db, "gh2")
    c = crews.create(db, "gh1", "Ghost Ship", join_policy="approval")
    crews.join(db, "gh2", c.clan_id)
    crews.disband(db, "gh1", c.clan_id)

    # both of them get on with their lives
    other = crews.create(db, "gh1", "Somewhere Else", join_policy="open")
    crews.join(db, "gh2", other.clan_id)
    db.commit()

    err = crews.unretire(db, c)
    assert err and "Ghost Ship" in err, "the admin has to be told why, not told it worked"
    db.refresh(c)
    assert c.disbanded_at is not None, "a refused restore changes nothing"
    assert _active(db, c.clan_id) == 0


def test_a_crew_with_one_rider_left_to_come_back_still_restores(db):
    """The other side of the same guard: one returning rider is a crew, and they lead it."""
    _rider(db, "pt1")
    _rider(db, "pt2")
    c = crews.create(db, "pt1", "Partial Return", join_policy="open")
    crews.join(db, "pt2", c.clan_id)
    crews.disband(db, "pt1", c.clan_id)
    moved = crews.create(db, "pt1", "Moved On", join_policy="open")
    db.commit()

    assert crews.unretire(db, c) is None
    db.commit()
    assert _active(db, c.clan_id) == 1
    leaders = (db.query(ClanMember)
               .filter(ClanMember.clan_id == c.clan_id, ClanMember.role == "leader",
                       ClanMember.left_at.is_(None)).all())
    assert len(leaders) == 1 and leaders[0].store_id == "pt2"
