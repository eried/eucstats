"""Pluggable attestation verification.

`stub` mode accepts everything (local/dev until the app integrates Play
Integrity). `enforce` mode currently requires a non-empty token (so the 401
path is exercisable) via EnforceStubVerifier; swap in PlayIntegrityVerifier
once the GCP service account + Google-managed decode are set up.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AttestationResult:
    ok: bool
    reason: str = ""


class Verifier:
    def verify(self, envelope: dict) -> AttestationResult:  # pragma: no cover - interface
        raise NotImplementedError


class StubVerifier(Verifier):
    """Accepts everything. Default for local/dev."""

    def verify(self, envelope: dict) -> AttestationResult:
        return AttestationResult(True, "stub")


class EnforceStubVerifier(Verifier):
    """Requires a plausible token but does NOT cryptographically verify it yet."""

    def verify(self, envelope: dict) -> AttestationResult:
        att = (envelope or {}).get("attestation") or {}
        token = att.get("token")
        if isinstance(token, str) and len(token) > 10:
            return AttestationResult(True, "enforced-stub")
        return AttestationResult(False, "missing_or_invalid_token")


class PlayIntegrityVerifier(Verifier):
    """Real Play Integrity verification — wire once the app integrates the SDK
    and the GCP service account / decode is configured. Must check that the
    token decodes, requestPackageName == self.package, requestHash == sha256
    of the meta envelope, appRecognitionVerdict == PLAY_RECOGNIZED, and the
    device integrity verdict meets policy.
    """

    def __init__(self, package: str):
        self.package = package

    def verify(self, envelope: dict) -> AttestationResult:  # pragma: no cover - future
        raise NotImplementedError("Play Integrity verification not yet wired")


def get_verifier(mode: str, package: str = "com.eried.eucplanet") -> Verifier:
    if mode == "enforce":
        # Swap to PlayIntegrityVerifier(package) when real verification is ready.
        return EnforceStubVerifier()
    return StubVerifier()
