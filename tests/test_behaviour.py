"""Page-behaviour settings, incl. the intro replay-on-return-visit seconds."""
from services import settings


def test_intro_replay_secs_default_zero(db):
    assert settings.get_behaviour(db)["intro_replay_secs"] == 0


def test_intro_replay_secs_roundtrip_and_clamp(db):
    settings.set_behaviour(db, poll_secs=30, intro_enabled=True, intro_src="/x.mp4",
                           map_style="dark", glitch_enabled=False, intro_replay_secs=5)
    assert settings.get_behaviour(db)["intro_replay_secs"] == 5

    settings.set_behaviour(db, poll_secs=30, intro_enabled=True, intro_src="/x.mp4",
                           map_style="dark", glitch_enabled=False, intro_replay_secs=999)
    assert settings.get_behaviour(db)["intro_replay_secs"] == 60   # clamped to max
