import pytest

from ingest.attestation import get_verifier, PlayIntegrityVerifier


def test_stub_accepts_everything():
    assert get_verifier("stub").verify({}).ok is True


def test_enforce_requires_token():
    v = get_verifier("enforce")
    assert v.verify({"attestation": {"token": "x" * 20}}).ok is True
    assert v.verify({"attestation": {"token": ""}}).ok is False
    assert v.verify({}).ok is False


def test_play_integrity_not_wired_yet():
    with pytest.raises(NotImplementedError):
        PlayIntegrityVerifier("com.eried.eucplanet").verify({})
