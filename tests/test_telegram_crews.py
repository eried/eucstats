"""Crews on Telegram: a crew being founded, a crew taking #1, and the all-time line.

Erwin asked for crew events in the bot and for the recap's all-time line to carry
"X crews (x riders)".

Three things here are easy to get wrong and are each worth a test rather than a reading:

* The all-time line must not grow a limb when crews are off or when nobody has founded one.
  "0 crews (0 riders)" is a line about nothing, posted daily.
* The new-crew message names the founder, and a rider who has closed their account or opted
  out of being public does not become public by founding a crew. The crew still gets
  announced; the person does not.
* The #1 takeover snapshots the crew by SLUG. The country, wheel and brand takeovers beside it
  key on a NAME, which is right for them -- a country does not get renamed -- and would be
  wrong here: on a name, a crew editing its own title announces itself as having overtaken
  itself.
"""
from datetime import timedelta

import models
from services import crews, settings, telegram

NL = chr(10)
CFG = {
    "enabled": True, "chat_id": "c", "link_url": "https://example.test",
    "summary_tz": "Europe/Oslo", "new_crew": True, "tk_crew": True,
    "tk_rider": False, "tk_country": False, "tk_wheel": False, "tk_brand": False,
}


def _rider(db, sid, name, public=True):
    """A rider with one validated ride behind them -- `crews.create` refuses anybody who has
    never uploaded, which is the rule that keeps empty crews off the board."""
    db.add(models.Rider(store_id=sid, display_name=name, platform="google_play",
                        flag="NO", consent_public=public))
    db.commit()
    when = models.utcnow() - timedelta(days=2)
    db.add(models.Trip(trip_uuid="seed-" + sid, rider_store_id=sid, distance_km=5.0,
                       validation_status="validated", start_utc=when, end_utc=when))
    db.commit()
    return db.get(models.Rider, sid)


def _on(db, enabled=True):
    """`set_crews` takes the whole settings block, so every call passes all of it."""
    settings.set_crews(db, enabled=enabled, zoom=14, window_days=90, seed=2, cooldown_days=7,
                       max_members=0, opacity=0.55, creation_open=True)


def _crew(db, sid, name):
    _on(db)
    return crews.create(db, sid, name, "", join_policy="open")


# --- the count the summary line is built from ------------------------------

def test_counts_sees_live_crews_and_the_riders_still_in_them(db):
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    a = _crew(db, "a", "Northern Lights")
    _crew(db, "b", "Southern Cross")
    assert crews.counts(db) == {"crews": 2, "riders": 2, "squares": 0}
    a.terr_tiles, _ = 31, None
    db.commit()
    assert crews.counts(db)["squares"] == 31, (
        "the ground every crew holds between them is not being summed for the recap line")

    # a disbanded crew is not a crew, and its member is not in one
    a.disbanded_at = models.utcnow()
    db.commit()
    assert crews.counts(db) == {"crews": 1, "riders": 1, "squares": 0}, (
        "a disbanded crew is still being counted, so the recap overstates the feature "
        "every morning -- and its ground is still on the board's total")


def test_a_rider_who_left_is_not_counted(db):
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    c = _crew(db, "a", "Northern Lights")
    db.add(models.ClanMember(clan_id=c.clan_id, store_id="b", role="member"))
    db.commit()
    assert crews.counts(db)["riders"] == 2
    m = (db.query(models.ClanMember)
         .filter(models.ClanMember.store_id == "b").first())
    m.left_at = models.utcnow()
    db.commit()
    assert crews.counts(db)["riders"] == 1, (
        "someone who walked out is still counted as riding for a crew")


# --- the all-time line -----------------------------------------------------

def _summary(db, cfg=None):
    """Yesterday's recap text, with one ride in it so the day is not silent."""
    when = models.utcnow() - timedelta(days=1)
    db.add(models.Trip(trip_uuid="recap-trip", rider_store_id="a", distance_km=12.0,
                       validation_status="validated", start_utc=when, end_utc=when))
    db.commit()
    return telegram.daily_summary_text(db, (models.utcnow() - timedelta(days=1)).date(),
                                       dict(cfg or CFG))


def test_the_crew_line_is_its_own_line_and_carries_the_ground(db):
    """Erwin: "can we split the crew info into a new line".

    It used to ride on the end of the all-time line as `· 2 crews (2 riders)`, behind the
    country count -- a whole second leaderboard inside a parenthesis, saying nothing about
    the thing the feature is about.
    """
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    c = _crew(db, "a", "Northern Lights")
    db.add(models.ClanMember(clan_id=c.clan_id, store_id="b", role="member"))
    c.terr_tiles, c.terr_best_tiles = 31, 18
    db.commit()
    text = _summary(db)
    assert text, "the recap went silent on a day with a ride in it"
    lines = text.split(NL)
    crew_line = [ln for ln in lines if "Crews:" in ln]
    assert len(crew_line) == 1, (
        "the crew counts are not on a line of their own:" + NL + text)
    assert "riders" in crew_line[0] and "31" in crew_line[0], (
        "the crew line does not carry the riders and the ground they hold:" + NL + text)
    assert "crew" not in [ln for ln in lines if ln.startswith("All-time")][0].lower(), (
        "the all-time line is still carrying the crew tail as well:" + NL + text)


def test_the_crew_line_names_who_is_leading(db):
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    big = _crew(db, "a", "Northern Lights")
    small = _crew(db, "b", "Fjord Few")
    big.terr_tiles, big.terr_best_tiles = 40, 30
    small.terr_tiles, small.terr_best_tiles = 12, 9
    db.commit()
    text = _summary(db)
    assert "Northern Lights" in text and "leads on" in text, (
        "the recap does not say which crew is on top:" + NL + text)
    assert "Fjord Few" not in text, (
        "the recap named a crew that is not leading:" + NL + text)
    # the board says 40.0 points for 30 + 0.25 x 40; the recap must not round differently
    assert "40.0" in text, (
        "the recap states the standing in a different precision from the board:" + NL + text)


def test_a_board_of_crews_holding_nothing_has_no_leader_to_name(db):
    """A crew that has never ridden anywhere scores zero, and three zeroes in a row is a
    line about nothing. The count line stays; the crown does not appear."""
    _on(db)
    _rider(db, "a", "Ann")
    _crew(db, "a", "Northern Lights")
    text = _summary(db)
    assert "Crews:" in text, "the count line vanished along with the leader"
    assert "leads on" not in text, (
        "the recap crowned a crew that holds no ground:" + NL + text)


def test_no_crews_means_no_crew_line(db):
    _on(db)
    _rider(db, "a", "Ann")
    text = _summary(db)
    assert text
    assert "crews:" not in text.lower(), (
        "the recap advertises crews on a site that has none:" + NL + text)


def test_crews_switched_off_keeps_the_line_out(db):
    _on(db)
    _rider(db, "a", "Ann")
    _crew(db, "a", "Northern Lights")
    _on(db, enabled=False)
    text = _summary(db)
    assert text
    assert "crews:" not in text.lower(), (
        "the feature is dark and the recap is still talking about it:" + NL + text)


# --- a crew is founded -----------------------------------------------------

def _capture(monkeypatch, cfg=None):
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda text, cfg=None: sent.append(text))
    monkeypatch.setattr(telegram, "get_config", lambda: dict(cfg or CFG))
    monkeypatch.setattr(telegram, "is_configured", lambda cfg=None: True)
    return sent


def test_founding_a_crew_is_announced_with_its_founder(db, monkeypatch):
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    sent = _capture(monkeypatch)
    telegram.notify_new_crew(c.clan_id)
    assert sent, "a crew was founded and nothing was said"
    msg = sent[0]
    assert "Northern Lights" in msg
    assert "Ann" in msg, "the founder is not named: " + msg
    assert "/c/" + c.slug in msg, "the message does not link to the crew: " + msg


def test_a_private_founder_is_not_named_but_the_crew_still_is(db, monkeypatch):
    _on(db)
    _rider(db, "a", "Ann", public=False)
    c = _crew(db, "a", "Northern Lights")
    sent = _capture(monkeypatch)
    telegram.notify_new_crew(c.clan_id)
    assert sent, "the crew is public and was not announced"
    assert "Northern Lights" in sent[0]
    assert "Ann" not in sent[0], (
        "founding a crew outed a rider who had opted out of being public: " + sent[0])


def test_a_disbanded_crew_is_not_announced(db, monkeypatch):
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    c.disbanded_at = models.utcnow()
    db.commit()
    sent = _capture(monkeypatch)
    telegram.notify_new_crew(c.clan_id)
    assert not sent, "announced a crew that no longer exists: " + NL.join(sent)


def test_the_toggle_is_honoured(db, monkeypatch):
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    sent = _capture(monkeypatch, dict(CFG, new_crew=False))
    telegram.notify_new_crew(c.clan_id)
    assert not sent, "the new-crew toggle is off and it posted anyway"


# --- a crew takes #1 -------------------------------------------------------

def _rank(db, slug, best_tiles):
    """Put a crew on the board at a given size, which is what `ranking` sorts on."""
    c = db.query(models.Clan).filter(models.Clan.slug == slug).first()
    c.terr_best_tiles = best_tiles
    c.terr_tiles = best_tiles
    c.terr_km2 = c.terr_best_km2 = float(best_tiles)
    db.commit()


def test_a_new_top_crew_is_announced_and_names_the_one_it_passed(db, monkeypatch):
    import json
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    first = _crew(db, "a", "Northern Lights")
    second = _crew(db, "b", "Southern Cross")
    _rank(db, first.slug, 10)
    _rank(db, second.slug, 40)                 # Southern Cross is now top
    settings.set_meta(db, "tg_record_holders", json.dumps({"g:crew": first.slug}))
    db.commit()

    sent = _capture(monkeypatch)
    telegram.check_records()
    joined = NL.join(sent)
    assert sent, "the board changed hands at the top and nothing was announced"
    assert "Southern Cross" in joined, "the new leader is not named: " + joined
    assert "Northern Lights" in joined, "the crew it passed is not named: " + joined


def test_renaming_the_leader_announces_nothing(db, monkeypatch):
    """The whole reason the snapshot holds a slug. On a name, this fires."""
    import json
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    _rank(db, c.slug, 40)
    settings.set_meta(db, "tg_record_holders", json.dumps({"g:crew": c.slug}))
    db.commit()
    c.name = "Northern Lights Reborn"           # the slug does not move with the name
    db.commit()

    sent = _capture(monkeypatch)
    telegram.check_records()
    assert not sent, (
        "a crew renaming itself was announced as a takeover, naming it as both the winner "
        "and the loser: " + NL.join(sent))


# --- the crews card beside the rider one -----------------------------------

def test_champions_is_the_top_of_the_board_in_the_board_order(db):
    """Erwin: "there is a mismatch now, crew domination shows squares and who owns the
    streets shows pts". The card is the first three rows of the board now, so it cannot
    disagree with it -- same comparator, same unit."""
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    _rider(db, "c", "Cal")
    spread = _crew(db, "a", "Two Cities")
    tight = _crew(db, "b", "One Block")
    small = _crew(db, "c", "Corner Shop")
    # 10 + 10 against 11 in one piece: Erwin's own case, where the two screens used to
    # disagree -- the board said the 11 and the card said the 20.
    spread.terr_best_tiles, spread.terr_tiles, spread.terr_regions = 10, 20, 2
    tight.terr_best_tiles, tight.terr_tiles, tight.terr_regions = 11, 11, 1
    small.terr_best_tiles, small.terr_tiles, small.terr_regions = 3, 3, 1
    db.commit()

    top = crews.champions(db)["top"]
    assert [c["name"] for c in top] == ["Two Cities", "One Block", "Corner Shop"]
    assert top[0]["score"] == 15.0, (
        "the card is not carrying the standing the board sorts on")
    assert top[0]["regions"] == 2, "the card cannot say how many pieces the ground is in"


def test_champions_says_nothing_when_nobody_holds_anything(db):
    """Three crews on zero is a card about nothing. The client hides the deck on an empty
    top rather than printing a podium of noughts."""
    _on(db)
    _rider(db, "a", "Ann")
    _crew(db, "a", "Northern Lights")
    assert crews.champions(db)["top"] == []


def test_champions_leaves_out_a_crew_that_disbanded(db):
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    gone = _crew(db, "a", "Northern Lights")
    here = _crew(db, "b", "Fjord Few")
    gone.terr_best_tiles, gone.terr_tiles = 40, 40
    here.terr_best_tiles, here.terr_tiles = 5, 5
    db.commit()
    assert crews.champions(db)["top"][0]["name"] == "Northern Lights"
    gone.disbanded_at = models.utcnow()
    db.commit()
    assert [c["name"] for c in crews.champions(db)["top"]] == ["Fjord Few"]


# --- crew milestones -------------------------------------------------------
#
# All three are measured against a snapshot of the last redraw, because territory keeps no
# history: the map is rewritten wholesale every rebuild and the only record of what it used
# to say is the one these write. The behaviour that matters most is the SILENT first sight --
# switching one of these on must announce what happens next, not what already happened.

MILES = dict(CFG, crew_first=True, crew_squares=True, crew_flip=True,
             new_crew=False, tk_crew=False)


def test_the_first_sight_of_a_crew_is_recorded_and_not_announced(db, monkeypatch):
    sent = _capture(monkeypatch, MILES)
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    c.terr_best_tiles, c.terr_tiles = 400, 400
    db.commit()
    telegram.check_crew_events(db)
    assert sent == [], (
        "turning a milestone on announced a crew's whole history at once: " + repr(sent))
    # ...and now it is a baseline, so nothing fires again for standing still
    telegram.check_crew_events(db)
    assert sent == []


def test_a_crews_first_patch_is_announced_once(db, monkeypatch):
    sent = _capture(monkeypatch, MILES)
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    telegram.check_crew_events(db)            # baseline: on the board, holding nothing
    assert sent == []
    c.terr_best_tiles, c.terr_tiles = 4, 4
    db.commit()
    telegram.check_crew_events(db)
    assert len(sent) == 1 and "Northern Lights" in sent[0], sent
    assert "first patch" in sent[0], sent
    assert "/c/" + c.slug in sent[0], "the message does not link to the crew"
    telegram.check_crew_events(db)
    assert len(sent) == 1, "a crew got on the map twice: " + repr(sent)


def test_round_numbers_fire_upward_only(db, monkeypatch):
    """Squares go DOWN every week a crew does not ride. A ladder that fired on every
    crossing would turn ordinary decay into a drumbeat."""
    sent = _capture(monkeypatch, MILES)
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    c.terr_best_tiles, c.terr_tiles = 20, 49
    db.commit()
    telegram.check_crew_events(db)            # baseline, below the first rung
    assert sent == []
    c.terr_tiles = 51
    db.commit()
    telegram.check_crew_events(db)
    assert len(sent) == 1 and "50" in sent[0], sent
    c.terr_tiles = 44                         # faded back under it
    db.commit()
    telegram.check_crew_events(db)
    assert len(sent) == 1, "decaying past a rung announced it: " + repr(sent)
    c.terr_tiles = 52                         # and climbed again
    db.commit()
    telegram.check_crew_events(db)
    assert len(sent) == 1, "the same rung was announced twice: " + repr(sent)
    c.terr_tiles = 101
    db.commit()
    telegram.check_crew_events(db)
    assert len(sent) == 2 and "100" in sent[1], sent


def test_the_first_patch_and_a_rung_are_not_both_announced(db, monkeypatch):
    """A crew that appears on the map already holding sixty squares is one piece of news."""
    sent = _capture(monkeypatch, MILES)
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    telegram.check_crew_events(db)
    c.terr_best_tiles, c.terr_tiles = 55, 60
    db.commit()
    telegram.check_crew_events(db)
    assert len(sent) == 1 and "first patch" in sent[0], sent


def _two(db):
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    return _crew(db, "a", "Northern Lights"), _crew(db, "b", "Fjord Few")


def test_ground_changing_hands_names_both_crews(db, monkeypatch):
    sent = _capture(monkeypatch, MILES)
    loser, winner = _two(db)
    telegram.check_crew_events(db, {(loser.clan_id, winner.clan_id): 14})
    assert len(sent) == 1, sent
    assert "Fjord Few" in sent[0] and "Northern Lights" in sent[0], sent
    assert "14" in sent[0], sent


def test_a_small_shove_at_the_frontier_is_not_news(db, monkeypatch):
    """Below the floor it is the normal give and take where two patches meet, which happens
    somewhere on the map nearly every redraw."""
    sent = _capture(monkeypatch, MILES)
    loser, winner = _two(db)
    telegram.check_crew_events(db, {(loser.clan_id, winner.clan_id): telegram.FLIP_MIN - 1})
    assert sent == [], sent


def test_only_the_biggest_flip_is_announced(db, monkeypatch):
    """A redraw that moves a frontier moves it in several places at once, and a crew that has
    just lost a neighbourhood does not need five notifications about it."""
    sent = _capture(monkeypatch, MILES)
    loser, winner = _two(db)
    telegram.check_crew_events(db, {(loser.clan_id, winner.clan_id): 12,
                                    (winner.clan_id, loser.clan_id): 30})
    assert len(sent) == 1, sent
    assert sent[0].index("Northern Lights") < sent[0].index("Fjord Few"), (
        "the message has the winner and the loser the wrong way round: " + sent[0])


def test_ground_lost_by_a_crew_that_disbanded_is_not_a_takeover(db, monkeypatch):
    sent = _capture(monkeypatch, MILES)
    loser, winner = _two(db)
    loser.disbanded_at = models.utcnow()
    db.commit()
    telegram.check_crew_events(db, {(loser.clan_id, winner.clan_id): 40})
    assert sent == [], (
        "a crew walking off the map was announced as a conquest: " + repr(sent))


def test_the_toggles_are_separate(db, monkeypatch):
    sent = _capture(monkeypatch, dict(MILES, crew_first=False, crew_squares=True))
    _on(db)
    _rider(db, "a", "Ann")
    c = _crew(db, "a", "Northern Lights")
    telegram.check_crew_events(db)
    c.terr_best_tiles, c.terr_tiles = 4, 4
    db.commit()
    telegram.check_crew_events(db)
    assert sent == [], "first patch fired with its own toggle off: " + repr(sent)
