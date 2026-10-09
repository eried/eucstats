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
    assert crews.counts(db) == {"crews": 2, "riders": 2}

    # a disbanded crew is not a crew, and its member is not in one
    a.disbanded_at = models.utcnow()
    db.commit()
    assert crews.counts(db) == {"crews": 1, "riders": 1}, (
        "a disbanded crew is still being counted, so the recap overstates the feature "
        "every morning")


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


def test_the_all_time_line_carries_crews_and_their_riders(db):
    _on(db)
    _rider(db, "a", "Ann")
    _rider(db, "b", "Bo")
    c = _crew(db, "a", "Northern Lights")
    db.add(models.ClanMember(clan_id=c.clan_id, store_id="b", role="member"))
    db.commit()
    text = _summary(db)
    assert text, "the recap went silent on a day with a ride in it"
    assert "2 crews (2 riders)" in text or "1 crew (2 riders)" in text, (
        "the all-time line does not carry the crew counts:" + NL + text)


def test_no_crews_means_no_crew_line(db):
    _on(db)
    _rider(db, "a", "Ann")
    text = _summary(db)
    assert text
    assert "crew" not in text.lower(), (
        "the recap advertises crews on a site that has none:" + NL + text)


def test_crews_switched_off_keeps_the_line_out(db):
    _on(db)
    _rider(db, "a", "Ann")
    _crew(db, "a", "Northern Lights")
    _on(db, enabled=False)
    text = _summary(db)
    assert text
    assert "crew" not in text.lower(), (
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
