"""Site-level test mode (global, not per-dataset) + customizable banner text."""
from services import settings


def test_defaults_to_enabled_with_default_text(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "SITE_STATE_FILE", tmp_path / "site.json")
    tm = settings.get_test_mode()
    assert tm["enabled"] is True            # fail-safe default
    assert tm["text"] == "TEST DATA"
    assert settings.is_test_mode() is True


def test_set_and_get_roundtrip(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "SITE_STATE_FILE", tmp_path / "site.json")
    settings.set_test_mode(False, "PREVIEW")
    tm = settings.get_test_mode()
    assert tm["enabled"] is False and tm["text"] == "PREVIEW"
    assert settings.is_test_mode() is False


def test_blank_text_falls_back(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "SITE_STATE_FILE", tmp_path / "site.json")
    settings.set_test_mode(True, "   ")
    assert settings.get_test_mode()["text"] == "TEST DATA"


def test_mode_independent_of_db(tmp_path, monkeypatch):
    # no Session is needed — proves test mode is global, not stored in app_meta
    import config
    monkeypatch.setattr(config, "SITE_STATE_FILE", tmp_path / "site.json")
    settings.set_test_mode(False, "LIVE-ish")
    assert settings.is_test_mode() is False
