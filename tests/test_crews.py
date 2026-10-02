"""The crew lifecycle, at the level a rider actually meets it.

Everything here is a state a crew could reach and not leave, or a promise the panel made that
the server did not keep. Three reviewers walked this feature and every one of the dead ends
they found would have been caught by one of these: the suite had territory covered in depth
and the crew around it not at all, because territory is where the interesting arithmetic is
and a crew is "just" rows in a table. The rows in the table are what strands people.
"""
from datetime import timedelta

import pytest

from models import Clan, ClanMember, Rider, Trip, utcnow
from services import crews


def _rider(db, sid, name=None):
    db.add(Rider(store_id=sid, display_name=name or sid.title(), flag="NO"))
    db.commit()
    # one validated ride, because founding a crew asks for one
    db.add(Trip(trip_uuid=f"tr-{sid}", rider_store_id=sid, distance_km=5.0,
                validation_status="validated", start_utc=utcnow() - timedelta(days=1),
                end_utc=utcnow() - timedelta(days=1)))
    db.commit()
    return sid


def _active(db, clan_id):
    return (db.query(ClanMember)
            .filter(ClanMember.clan_id == clan_id, ClanMember.status == "active",
                    ClanMember.left_at.is_(None)).count())


# --- founding -------------------------------------------------------------------------

def test_a_crew_can_be_founded_and_its_founder_runs_it(db):
    _rider(db, "f1")
    c = crews.create(db, "f1", "First Light", "Dawn patrol.", join_policy="open")
    assert c.slug == "first-light"
    m = crews.membership(db, "f1")
    assert (m.role, m.status) == ("leader", "active")


def test_founding_needs_a_ride_behind_it(db):
    db.add(Rider(store_id="f0", display_name="No Rides", flag="NO"))
    db.commit()
    with pytest.raises(crews.CrewError) as e:
        crews.create(db, "f0", "Paper Crew")
    assert e.value.code == "no_trips"


def test_two_names_that_look_different_and_slugify_the_same(db):
    """`Night Riders` and `Night.Riders` are two legal names and one slug, and the column is
    UNIQUE, so the second founder passed the name check and then hit an IntegrityError that
    nothing caught: a 500 the client rendered as "That didn't work"."""
    _rider(db, "s1")
    _rider(db, "s2")
    crews.create(db, "s1", "Night Riders")
    c2 = crews.create(db, "s2", "Night.Riders")
    assert c2.slug != "night-riders"
    assert c2.slug.startswith("night-riders")


def test_a_folded_crew_gives_its_name_back(db):
    """`name` is UNIQUE for the life of the table, so a disbanded crew went on holding its
    name for ever and founding under it again was an uncaught database error. The colours
    come free the moment a crew folds; so does the name."""
    _rider(db, "d1")
    c = crews.create(db, "d1", "Second Chances")
    crews.disband(db, "d1", c.clan_id)
    _rider(db, "d2")
    again = crews.create(db, "d2", "Second Chances")
    assert again.clan_id != c.clan_id
    assert again.name == "Second Chances"


def test_a_name_in_use_by_a_living_crew_is_still_taken(db):
    _rider(db, "n1")
    _rider(db, "n2")
    crews.create(db, "n1", "Only One")
    with pytest.raises(crews.CrewError) as e:
        crews.create(db, "n2", "Only One")
    assert e.value.code == "name_taken"


# --- joining and leaving --------------------------------------------------------------

def test_an_open_crew_lets_you_straight_in_and_an_approval_crew_does_not(db):
    _rider(db, "o1")
    _rider(db, "o2")
    _rider(db, "o3")
    openc = crews.create(db, "o1", "Doors Open", join_policy="open")
    assert crews.join(db, "o2", openc.clan_id).status == "active"
    _rider(db, "a1")
    appr = crews.create(db, "a1", "Ask First", join_policy="approval")
    assert crews.join(db, "o3", appr.clan_id).status == "pending"


def test_the_wrong_invite_code_is_told_so(db):
    _rider(db, "i1")
    _rider(db, "i2")
    c = crews.create(db, "i1", "Code Only", join_policy="invite")
    with pytest.raises(crews.CrewError) as e:
        crews.join(db, "i2", c.clan_id, invite_code="NOPE")
    assert e.value.code == "bad_invite"
    assert crews.join(db, "i2", c.clan_id, invite_code=c.invite_code).status == "active"


def test_the_last_one_out_turns_the_lights_off(db):
    """A crew with nobody in it kept its ground, kept its place on the board and sat in the
    join list reading "0 riders"; with the default approval policy anyone who joined waited
    for a leader who did not exist. Only an admin could clear it, and nothing said it was
    there."""
    _rider(db, "l1")
    c = crews.create(db, "l1", "Briefly Ours")
    crews.leave(db, "l1")
    db.refresh(c)
    assert c.disbanded_at is not None, "an empty crew must not stay standing"
    assert _active(db, c.clan_id) == 0


def test_a_crew_with_other_people_in_it_survives_someone_leaving(db):
    _rider(db, "k1")
    _rider(db, "k2")
    c = crews.create(db, "k1", "Still Here", join_policy="open")
    crews.join(db, "k2", c.clan_id)
    crews.leave(db, "k2")
    db.refresh(c)
    assert c.disbanded_at is None
    assert _active(db, c.clan_id) == 1


def test_a_leader_cannot_walk_out_leaving_nobody_in_charge(db):
    _rider(db, "p1")
    _rider(db, "p2")
    c = crews.create(db, "p1", "Promote First", join_policy="open")
    crews.join(db, "p2", c.clan_id)
    with pytest.raises(crews.CrewError) as e:
        crews.leave(db, "p1")
    assert e.value.code == "promote_first"


def test_leaving_costs_a_cooldown_and_folding_your_own_crew_does_not(db):
    """Disband sits one button from Leave with the same styling, and used to carry the same
    week out of the game without saying so. Founding a crew by mistake and undoing it is not
    walking out on one."""
    _rider(db, "c1")
    _rider(db, "c2")
    host = crews.create(db, "c1", "The Host", join_policy="open")
    crews.join(db, "c2", host.clan_id)
    crews.leave(db, "c2")
    assert crews.cooldown_until(db, "c2") is not None, "walking out costs a week"

    _rider(db, "c3")
    own = crews.create(db, "c3", "My Mistake")
    crews.disband(db, "c3", own.clan_id)
    assert crews.cooldown_until(db, "c3") is None, "folding your own crew is not leaving one"


def test_the_cooldown_follows_the_admin_setting_not_a_module_global(db):
    """The admin screen used to assign the module global on save: one process's memory of a
    number, gone on the next restart and never shared with a second worker, while the browser
    went on rendering the configured figure. A rider waited out a countdown the server had
    never agreed to."""
    from services import settings
    _rider(db, "cd1")
    _rider(db, "cd2")
    c = crews.create(db, "cd1", "Cooling", join_policy="open")
    crews.join(db, "cd2", c.clan_id)
    crews.leave(db, "cd2")

    def _set(days):
        cfg = settings.get_crews(db)
        settings.set_crews(db, enabled=cfg["enabled"], zoom=cfg["zoom"],
                           window_days=cfg["window_days"], seed=cfg["seed"],
                           cooldown_days=days, max_members=cfg["max_members"],
                           opacity=cfg["opacity"], creation_open=cfg["creation_open"])

    _set(1)
    short = crews.cooldown_until(db, "cd2")
    _set(30)
    long = crews.cooldown_until(db, "cd2")
    assert short is not None and long is not None
    assert long > short, "the figure the admin set is the figure that is enforced"


# --- who is in charge -----------------------------------------------------------------

def test_only_the_longest_serving_member_may_take_a_quiet_crew_over(db):
    _rider(db, "q1")
    _rider(db, "q2")
    _rider(db, "q3")
    c = crews.create(db, "q1", "Gone Quiet", join_policy="open")
    crews.join(db, "q2", c.clan_id)
    crews.join(db, "q3", c.clan_id)
    for sid, when in (("q2", 40), ("q3", 20)):
        m = crews.membership(db, sid)
        m.joined_at = utcnow() - timedelta(days=when)
    lead = crews.membership(db, "q1")
    lead.last_seen = utcnow() - timedelta(days=crews.IDLE_LEADER_DAYS + 3)
    db.commit()

    assert crews.claim_eligible(db, "q2", c.clan_id) is True
    assert crews.claim_eligible(db, "q3", c.clan_id) is False
    with pytest.raises(crews.CrewError):
        crews.claim_leadership(db, "q3", c.clan_id)
    crews.claim_leadership(db, "q2", c.clan_id)
    assert crews.membership(db, "q2").role == "leader"


def test_a_rider_still_waiting_to_be_let_in_is_not_offered_the_crew(db):
    """The take-over button rendered on "nobody is in charge" alone, to every non-leader
    including pending ones, for whom it can only ever fail."""
    _rider(db, "w1")
    _rider(db, "w2")
    c = crews.create(db, "w1", "Waiting Room", join_policy="approval")
    crews.join(db, "w2", c.clan_id)
    lead = crews.membership(db, "w1")
    lead.last_seen = utcnow() - timedelta(days=crews.IDLE_LEADER_DAYS + 3)
    db.commit()
    assert crews.claim_eligible(db, "w2", c.clan_id) is False


def test_an_active_leader_cannot_be_deposed(db):
    _rider(db, "r1")
    _rider(db, "r2")
    c = crews.create(db, "r1", "Running Fine", join_policy="open")
    crews.join(db, "r2", c.clan_id)
    assert crews.claim_eligible(db, "r2", c.clan_id) is False


def test_only_a_leader_folds_a_crew(db):
    _rider(db, "x1")
    _rider(db, "x2")
    c = crews.create(db, "x1", "Not Yours", join_policy="open")
    crews.join(db, "x2", c.clan_id)
    crews.set_role(db, "x1", c.clan_id, "x2", "officer")
    with pytest.raises(crews.CrewError) as e:
        crews.disband(db, "x2", c.clan_id)
    assert e.value.code == "not_leader"


# --- identity -------------------------------------------------------------------------

def test_no_two_crews_fly_the_same_colours(db):
    _rider(db, "y1")
    _rider(db, "y2")
    a = crews.create(db, "y1", "Ours")
    with pytest.raises(crews.CrewError) as e:
        crews.create(db, "y2", "Theirs", colour=a.colour, pattern=a.pattern)
    assert e.value.code == "identity_taken"


def test_a_folded_crew_hands_its_colours_straight_back(db):
    _rider(db, "z1")
    _rider(db, "z2")
    a = crews.create(db, "z1", "Shortlived")
    colour, pattern = a.colour, a.pattern
    crews.disband(db, "z1", a.clan_id)
    b = crews.create(db, "z2", "Next Up", colour=colour, pattern=pattern)
    assert (b.colour, b.pattern) == (colour, pattern)
