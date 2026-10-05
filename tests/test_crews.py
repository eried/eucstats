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


# --- coming back ----------------------------------------------------------------------

def test_you_can_rejoin_a_crew_you_were_in(db):
    """(clan_id, store_id) is the primary key and join() inserted a fresh row, so the second
    time anybody joined a crew they had ever been in -- rejoining, re-asking after a decline,
    re-asking after withdrawing -- the database refused and the panel said "That didn't work",
    permanently. It is the most ordinary action in a social feature and nothing went near it."""
    from services import settings
    _rider(db, "rj1")
    _rider(db, "rj2")
    c = crews.create(db, "rj1", "Revolving Door", join_policy="open")
    cfg = settings.get_crews(db)
    settings.set_crews(db, enabled=cfg["enabled"], zoom=cfg["zoom"],
                       window_days=cfg["window_days"], seed=cfg["seed"], cooldown_days=0,
                       max_members=cfg["max_members"], opacity=cfg["opacity"],
                       creation_open=cfg["creation_open"])
    crews.join(db, "rj2", c.clan_id)
    crews.leave(db, "rj2")
    again = crews.join(db, "rj2", c.clan_id)
    assert again.status == "active"
    assert again.role == "member", "coming back is not coming back in charge"


def test_a_declined_rider_can_ask_the_same_crew_again(db):
    from services import settings
    _rider(db, "ag1")
    _rider(db, "ag2")
    c = crews.create(db, "ag1", "Second Thoughts", join_policy="approval")
    cfg = settings.get_crews(db)
    settings.set_crews(db, enabled=cfg["enabled"], zoom=cfg["zoom"],
                       window_days=cfg["window_days"], seed=cfg["seed"], cooldown_days=0,
                       max_members=cfg["max_members"], opacity=cfg["opacity"],
                       creation_open=cfg["creation_open"])
    crews.join(db, "ag2", c.clan_id)
    crews.decide(db, "ag1", c.clan_id, "ag2", accept=False)
    assert crews.join(db, "ag2", c.clan_id).status == "pending"


def test_a_leader_cannot_wave_someone_past_the_cap(db):
    """The cap was enforced when a rider walked in and not when a leader approved one, so a
    crew could sit over the line while its own row in the join list read "Full"."""
    from services import settings
    _rider(db, "cap1")
    _rider(db, "cap2")
    c = crews.create(db, "cap1", "One Seat", join_policy="approval")
    cfg = settings.get_crews(db)
    settings.set_crews(db, enabled=cfg["enabled"], zoom=cfg["zoom"],
                       window_days=cfg["window_days"], seed=cfg["seed"],
                       cooldown_days=cfg["cooldown_days"], max_members=1,
                       opacity=cfg["opacity"], creation_open=cfg["creation_open"])
    crews.join(db, "cap2", c.clan_id)
    with pytest.raises(crews.CrewError) as e:
        crews.decide(db, "cap1", c.clan_id, "cap2", accept=True)
    assert e.value.code == "crew_full"


# --- folding --------------------------------------------------------------------------

def test_a_pending_rider_is_not_left_behind_when_the_crew_folds(db):
    """The fold marked the leaver and left every other open row alone, so a rider waiting on
    a request sat in front of a crew that no longer existed, told to wait for a leader who
    was gone."""
    _rider(db, "pf1")
    _rider(db, "pf2")
    c = crews.create(db, "pf1", "Lights Out", join_policy="approval")
    crews.join(db, "pf2", c.clan_id)
    crews.leave(db, "pf1")
    db.refresh(c)
    assert c.disbanded_at is not None
    assert crews.membership(db, "pf2") is None, "nobody is left waiting on a folded crew"
    assert crews.cooldown_until(db, "pf2") is None, "they did not walk out of anything"


def test_nobody_is_benched_when_their_leader_folds_the_crew(db):
    """The cooldown exists to stop crew-hopping. A member whose leader disbands took no
    action at all, and was being given a week and the words "You just walked out of one"."""
    _rider(db, "bf1")
    _rider(db, "bf2")
    c = crews.create(db, "bf1", "Not My Call", join_policy="open")
    crews.join(db, "bf2", c.clan_id)
    crews.disband(db, "bf1", c.clan_id)
    assert crews.cooldown_until(db, "bf1") is None
    assert crews.cooldown_until(db, "bf2") is None


def test_a_rider_is_told_once_that_their_crew_folded(db):
    _rider(db, "tf1")
    _rider(db, "tf2")
    c = crews.create(db, "tf1", "Here Today", join_policy="open")
    crews.join(db, "tf2", c.clan_id)
    crews.disband(db, "tf1", c.clan_id)
    first = crews.last_fold(db, "tf2")
    assert first and first["crew"] == "Here Today", "and without the retirement tag"
    # Reading it again still reports it. This assertion used to be `is None` -- the reader
    # retired the notice as it read it -- and that is exactly the bug: the panel makes three
    # `/crews/me` requests per load, so the first one spent the news and the render that
    # reached the screen saw nothing. All three of these notices were written, translated into
    # nineteen languages, and shown to nobody. The reader is a peek now.
    assert crews.last_fold(db, "tf2") == first, "a read must not spend it"
    assert crews.last_fold(db, "tf2") == first, "however many times it is read"
    # Told once still holds -- it is the panel that says when, once the card is on screen.
    assert crews.mark_notice_seen(db, "tf2", "folded") is True
    assert crews.last_fold(db, "tf2") is None, "news once, not for ever"
    assert crews.mark_notice_seen(db, "tf2", "folded") is False, "and spending it twice is a no-op"


def test_restoring_a_crew_gives_back_its_name_and_its_riders(db):
    """Admin disband retires the name, so restore had to become a real undo: clearing only
    `disbanded_at` handed back a crew called "X (folded abc123)" with no members and no
    leader, sitting in the public join list, which is the one state the rest of this file
    works to make impossible."""
    _rider(db, "re1")
    _rider(db, "re2")
    c = crews.create(db, "re1", "Back Again", join_policy="open")
    crews.join(db, "re2", c.clan_id)
    crews.disband(db, "re1", c.clan_id)
    assert crews.unretire(db, c) is None
    db.commit()
    db.refresh(c)
    assert c.name == "Back Again" and "(folded" not in c.name
    assert c.slug == "back-again"
    assert c.disbanded_at is None
    assert _active(db, c.clan_id) == 2
    leaders = (db.query(ClanMember)
               .filter(ClanMember.clan_id == c.clan_id, ClanMember.role == "leader",
                       ClanMember.left_at.is_(None)).count())
    assert leaders == 1, "a restored crew needs somebody in charge"


def test_a_name_taken_since_the_fold_blocks_the_restore_with_a_sentence(db):
    _rider(db, "tk1")
    _rider(db, "tk2")
    c = crews.create(db, "tk1", "Popular", join_policy="open")
    crews.disband(db, "tk1", c.clan_id)
    crews.create(db, "tk2", "Popular")
    err = crews.unretire(db, c)
    assert err and "taken" in err.lower()


def test_a_leader_can_take_somebody_off_the_crew(db):
    """The last missing leader power. Without it membership was write-once: on an open crew
    with a cap, anyone who walked in held a seat for good and the fix was to email an admin."""
    _rider(db, "kk1")
    _rider(db, "kk2")
    c = crews.create(db, "kk1", "House Rules", join_policy="open")
    crews.join(db, "kk2", c.clan_id)
    crews.remove(db, "kk1", c.clan_id, "kk2")
    assert crews.membership(db, "kk2") is None
    assert crews.cooldown_until(db, "kk2") is None, "being removed is not walking out"
    gone = crews.last_removal(db, "kk2")
    assert gone and gone["crew"] == "House Rules"
    # Same contract as the folded notice above, and for the same reason: reading reports,
    # acknowledging retires. See `crews.last_answer` for the measurement.
    assert crews.last_removal(db, "kk2") == gone, "a read must not spend it"
    assert crews.mark_notice_seen(db, "kk2", "removed") is True
    assert crews.last_removal(db, "kk2") is None, "told once"


def test_removing_cannot_empty_a_crew_or_touch_a_leader(db):
    _rider(db, "kg1")
    _rider(db, "kg2")
    c = crews.create(db, "kg1", "Guards", join_policy="open")
    with pytest.raises(crews.CrewError) as e:
        crews.remove(db, "kg1", c.clan_id, "kg1")
    assert e.value.code == "not_yourself"

    crews.join(db, "kg2", c.clan_id)
    crews.set_role(db, "kg1", c.clan_id, "kg2", "officer")
    with pytest.raises(crews.CrewError) as e:
        crews.remove(db, "kg2", c.clan_id, "kg1")
    assert e.value.code == "forbidden", "an officer cannot remove the leader"


def test_a_leader_can_take_back_a_decline(db):
    """A declined row was final for both sides: the rider could not re-ask without a fresh
    request and the leader could not reconsider at all."""
    _rider(db, "tb1")
    _rider(db, "tb2")
    c = crews.create(db, "tb1", "Second Look", join_policy="approval")
    crews.join(db, "tb2", c.clan_id)
    crews.decide(db, "tb1", c.clan_id, "tb2", accept=False)
    assert crews.membership(db, "tb2") is None
    crews.decide(db, "tb1", c.clan_id, "tb2", accept=True)
    m = crews.membership(db, "tb2")
    assert m is not None and m.status == "active"


def test_a_leader_cannot_demote_themselves_out_of_the_job(db):
    """Found while verifying something else: nothing stopped a leader setting their own role
    to member, which leaves the crew with nobody in charge -- the exact state leave() and
    disband() are written to prevent. Handing over is role="leader" on somebody else."""
    _rider(db, "sd1")
    _rider(db, "sd2")
    c = crews.create(db, "sd1", "Captain Goes Down", join_policy="open")
    crews.join(db, "sd2", c.clan_id)
    with pytest.raises(crews.CrewError) as e:
        crews.set_role(db, "sd1", c.clan_id, "sd1", "member")
    assert e.value.code == "promote_first"
    assert crews.membership(db, "sd1").role == "leader"
    # handing over properly still works, and the old leader steps down to officer
    crews.set_role(db, "sd1", c.clan_id, "sd2", "leader")
    assert crews.membership(db, "sd2").role == "leader"
    assert crews.membership(db, "sd1").role == "officer"


def test_accepting_a_rider_who_has_since_joined_elsewhere_is_refused(db):
    """The reconsider path revived a declined row with none of the guards the front door has,
    so a leader's Accept could put somebody in two crews at once."""
    _rider(db, "tw1")
    _rider(db, "tw2")
    _rider(db, "tw3")
    a = crews.create(db, "tw1", "First Choice", join_policy="approval")
    b = crews.create(db, "tw3", "Second Choice", join_policy="open")
    crews.join(db, "tw2", a.clan_id)
    crews.decide(db, "tw1", a.clan_id, "tw2", accept=False)
    crews.join(db, "tw2", b.clan_id)

    with pytest.raises(crews.CrewError) as e:
        crews.decide(db, "tw1", a.clan_id, "tw2", accept=True)
    assert e.value.code == "already_in_crew"
    assert crews.membership(db, "tw2").clan_id == b.clan_id, "and nothing was written"


def test_restoring_does_not_put_somebody_in_two_crews(db):
    """`unretire` revived every disbanded row regardless, so an admin's restore could land a
    rider in a crew they had already left for another -- two Leaves and a week's cooldown to
    get out of something they had no part in."""
    _rider(db, "dm1")
    _rider(db, "dm2")
    _rider(db, "dm3")
    folded = crews.create(db, "dm1", "Gone Fishing", join_policy="open")
    crews.join(db, "dm2", folded.clan_id)
    crews.disband(db, "dm1", folded.clan_id)

    other = crews.create(db, "dm3", "Still Going", join_policy="open")
    crews.join(db, "dm2", other.clan_id)

    assert crews.unretire(db, folded) is None
    db.commit()
    assert crews.membership(db, "dm2").clan_id == other.clan_id
    rows = (db.query(ClanMember)
            .filter(ClanMember.store_id == "dm2", ClanMember.left_at.is_(None)).count())
    assert rows == 1, "one live membership, not two"


def test_an_admin_folded_crews_leader_is_told_too(db):
    """`last_fold` skipped anyone whose role was leader, to spare the person who pressed
    Disband. An admin's fold writes the same mark, so the one person who pressed nothing and
    most needs the explanation got none."""
    _rider(db, "af1")
    c = crews.create(db, "af1", "Admin Folded", join_policy="open")
    # what web/admin_crews.py does: mark the members, retire the crew, no actor
    for m in db.query(ClanMember).filter(ClanMember.clan_id == c.clan_id,
                                         ClanMember.left_at.is_(None)).all():
        m.left_at = utcnow()
        m.status = "disbanded"
    crews._retire(c)
    db.commit()
    told = crews.last_fold(db, "af1")
    assert told and told["crew"] == "Admin Folded"


def test_the_leader_who_folded_their_own_crew_is_not_told_about_it(db):
    _rider(db, "ow1")
    c = crews.create(db, "ow1", "My Own Doing", join_policy="open")
    crews.disband(db, "ow1", c.clan_id)
    assert crews.last_fold(db, "ow1") is None, "they read the confirm dialog"


def test_walking_out_of_your_own_crew_is_not_news_that_it_was_folded(db):
    """The last-one-out sweep marks everybody still in the crew `disbanded`, which is the flag
    `last_fold` turns into "your crew was folded" plus the cooldown line that goes with it.
    The session does not autoflush, so the leaver's own row still read as present and they got
    the notice too -- told that something had happened to them, about their own decision."""
    _rider(db, "solo")
    c = crews.create(db, "solo", "On My Own", join_policy="open")
    crews.leave(db, "solo")

    row = db.query(ClanMember).filter(ClanMember.store_id == "solo").one()
    assert row.status != "disbanded", "they left; nobody folded anything on them"
    assert crews.last_fold(db, "solo") is None, "no notice about your own decision"


def test_the_last_one_out_still_closes_the_crew_behind_them(db):
    """The other half: the crew itself does have to go, and anybody left waiting on it has to
    be told, or they sit on a request to a crew that no longer exists."""
    _rider(db, "lo1")
    _rider(db, "lo2")
    c = crews.create(db, "lo1", "Lights Out", join_policy="approval")
    crews.join(db, "lo2", c.clan_id)          # pending, never approved
    crews.leave(db, "lo1")

    db.refresh(c)
    assert c.disbanded_at is not None, "nobody is in it; it does not stay in the join list"
    told = crews.last_fold(db, "lo2")
    assert told and told["crew"] == "Lights Out", "the rider waiting has to hear about it"


def test_an_officer_cannot_depose_the_leader(db):
    """`remove()` has carried this guard since it was written; `set_role` never had it. An
    officer could set the leader's role to member and walk away from a crew with nobody in
    charge -- the leaderless shell leave() and disband() exist to prevent, reachable by
    anybody the leader had trusted with a badge."""
    _rider(db, "ld")
    _rider(db, "of")
    c = crews.create(db, "ld", "Chain Of Command", join_policy="open")
    crews.join(db, "of", c.clan_id)
    crews.set_role(db, "ld", c.clan_id, "of", "officer")

    with pytest.raises(crews.CrewError) as e:
        crews.set_role(db, "of", c.clan_id, "ld", "member")
    assert e.value.code == "forbidden"

    lead = (db.query(ClanMember)
            .filter(ClanMember.clan_id == c.clan_id, ClanMember.role == "leader",
                    ClanMember.left_at.is_(None)).all())
    assert len(lead) == 1 and lead[0].store_id == "ld", "somebody is still in charge"


def test_an_officer_cannot_demote_a_fellow_officer(db):
    """The panel offered the button and the server refused it, rendering "Only a leader or
    officer can do that" at somebody who is one. Only the leader outranks an officer."""
    _rider(db, "o1")
    _rider(db, "o2")
    _rider(db, "boss")
    c = crews.create(db, "boss", "Two Badges", join_policy="open")
    for sid in ("o1", "o2"):
        crews.join(db, sid, c.clan_id)
        crews.set_role(db, "boss", c.clan_id, sid, "officer")

    with pytest.raises(crews.CrewError) as e:
        crews.set_role(db, "o1", c.clan_id, "o2", "member")
    assert e.value.code == "forbidden"
    # but standing down yourself is still yours to do
    crews.set_role(db, "o1", c.clan_id, "o1", "member")
    assert crews.membership(db, "o1").role == "member"
    # and the leader can still do either
    crews.set_role(db, "boss", c.clan_id, "o2", "member")
    assert crews.membership(db, "o2").role == "member"


def test_a_rider_waiting_to_be_let_in_is_not_riding_for_that_crew(db):
    """The panel badged a pending rider MEMBER and told them "you ride for them", while this
    function -- the one that decides what a trip is stamped with -- requires an active
    membership. A week of riding for nobody, with the card saying it counted."""
    _rider(db, "wait")
    _rider(db, "chief")
    c = crews.create(db, "chief", "Picky Two", join_policy="approval")
    crews.join(db, "wait", c.clan_id)
    assert crews.membership(db, "wait").status == "pending"
    assert crews.current_clan_id(db, "wait") is None, "their rides are stamped with no crew"
    crews.decide(db, "chief", c.clan_id, "wait", accept=True)
    assert crews.current_clan_id(db, "wait") == c.clan_id


def test_a_crew_cannot_be_handed_to_somebody_who_has_not_joined(db):
    """`set_role` matched on clan, store_id and left_at but not status, and a rider who has
    only asked to join matches all three. Promoting them to leader demoted the real leader to
    officer and handed the title to somebody `_require_power` refuses to act for: claim then
    refused for ninety days because the crew looked led, and nobody could disband it. One call
    from the leader's own panel, on a handle that panel published."""
    _rider(db, "boss2")
    _rider(db, "asker")
    c = crews.create(db, "boss2", "Not Yours Yet", join_policy="approval")
    crews.join(db, "asker", c.clan_id)
    assert crews.membership(db, "asker").status == "pending"

    for role in ("leader", "officer", "member"):
        with pytest.raises(crews.CrewError) as e:
            crews.set_role(db, "boss2", c.clan_id, "asker", role)
        assert e.value.code == "not_member"

    still = crews.membership(db, "boss2")
    assert still.role == "leader", "the leader still runs the crew"
    assert crews.membership(db, "asker").role != "leader"


def test_a_waiting_rider_does_not_count_as_an_officer(db):
    """`_officers` counts towards the guard that lets a leader walk out. A pending `officer`
    satisfied it, so a leader could leave a crew whose only officer had no powers at all."""
    _rider(db, "ld2")
    _rider(db, "wait2")
    c = crews.create(db, "ld2", "Paper Officer", join_policy="approval")
    crews.join(db, "wait2", c.clan_id)
    # force the shape the old code allowed, then check the guard still holds
    row = crews.membership(db, "wait2")
    row.role = "officer"
    db.commit()

    assert crews._officers(db, c.clan_id) == 0, "a rider who has not joined is not an officer"

    # And so the leader is the last ACTIVE member: leaving folds the crew and tells the rider
    # who was still waiting, rather than handing it to somebody who cannot run it.
    crews.leave(db, "ld2")
    db.refresh(c)
    assert c.disbanded_at is not None
    told = crews.last_fold(db, "wait2")
    assert told and told["crew"] == "Paper Officer"
