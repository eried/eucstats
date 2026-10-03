"""A translation has to know which English it was made from.

A reviewer found `crew.targets.p0` telling eighteen locales to do the opposite of what the
English said. The English had been rewritten -- from "Any {n}x{n} of these and you are" to
"these ARE one block, ride all of them" -- and the translations still said pick any one. It is
the first sentence a brand-new crew reads, and a rider who followed it rode four scattered
squares and never appeared on the map. `crew.targets.first` had drifted the same way eight
commits earlier: the English moved off "puts you on the map" precisely because that was false
of one square, and every locale kept saying it.

Nothing could have caught either. A regen adds keys that are MISSING; a key whose English
changed under a translation that is still present and still non-empty looks finished from
every angle. Both were found by a human reading nineteen files side by side.

So the English is fingerprinted per key, and this test fails when a fingerprint and its string
disagree. Changing an English string is then a choice with a visible cost: either retranslate
and re-stamp, or, when only the wording moved and every translation is still true, re-stamp
alone and say so in the commit.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

import i18n                                   # noqa: E402
from i18n_data import TRANSLATIONS            # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from stamp_i18n import fingerprint            # noqa: E402

try:
    from i18n_stamp import EN_FINGERPRINT
except ImportError:                            # pragma: no cover
    EN_FINGERPRINT = None


def _keys():
    """Every key, not two prefixes.

    This filtered to `crew.` and `pod.`, so the two keys added last round to serve a Crews
    control -- `panel.close` and `panel.peek` -- fell outside the Crews guard and shipped in
    English only, the only two untranslated keys out of 442. A guard scoped to a prefix is a
    guard somebody can step around by naming a key well.
    """
    return sorted(i18n.EN)


def test_the_stamp_exists():
    assert EN_FINGERPRINT, "run scripts/stamp_i18n.py"


def test_no_english_string_has_moved_under_its_translations():
    """The whole point. A key here means the English changed and nobody retranslated."""
    assert EN_FINGERPRINT
    drifted = []
    for k in _keys():
        want = EN_FINGERPRINT.get(k)
        if want is None:
            continue                            # a brand-new key; the next test covers it
        if fingerprint(i18n.EN[k]) != want:
            drifted.append(k)
    assert not drifted, (
        "English changed but the translations were not regenerated:\n  "
        + "\n  ".join(drifted)
        + "\n\nRetranslate these in web/i18n_data.py, then run scripts/stamp_i18n.py."
        + "\nIf only the wording moved and every translation is still true, re-run the"
        + " stamp alone and say so in the commit.")


def test_every_key_is_stamped_and_translated():
    assert EN_FINGERPRINT
    missing_stamp = [k for k in _keys() if k not in EN_FINGERPRINT]
    assert not missing_stamp, (
        "new keys with no stamp (run scripts/stamp_i18n.py):\n  " + "\n  ".join(missing_stamp))

    gaps = [(loc, k) for loc in TRANSLATIONS for k in _keys() if not TRANSLATIONS[loc].get(k)]
    assert not gaps, f"{len(gaps)} untranslated: {gaps[:8]}"


@pytest.mark.parametrize("loc", sorted(TRANSLATIONS))
def test_placeholders_survive_translation(loc):
    """A dropped {n} prints nothing; an invented one prints a brace at a rider."""
    import re
    bad = []
    for k in _keys():
        want = set(re.findall(r"\{(\w+)\}", str(i18n.EN[k])))
        got = set(re.findall(r"\{(\w+)\}", str(TRANSLATIONS[loc].get(k, ""))))
        if want != got:
            bad.append((k, sorted(want), sorted(got)))
    assert not bad, bad[:6]
