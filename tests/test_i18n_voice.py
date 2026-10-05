"""Two things a locale must not do: change its speech level, or rename the pass.

Both were reported as single wrong strings and both turned out to be one locale disagreeing
with itself across a dozen of them. A list of corrected strings closes neither; the suite went
green on three such fixes in one round and a reviewer was right to call them instance fixes
wearing a class fix's clothes.

**Speech level.** Korean and Japanese mark it; Chinese cannot; the European locales here use
one informal register throughout and have no second level to slip into. Japanese is uniform
plain across all 217 strings. Korean was 68 plain sentences against 28 in `해요체`, and the
legend card carried both -- `식어간다, 아무도 다시 안 왔다` nine pixels from `다른 클랜이 타고
있어요`, one of them politely addressing the reader and the other not. A reviewer found it on
the legend card and then in five more.

Nominal labels (`-기`, `-음`, `-중`) are not a speech level and are not checked: Japanese puts
`保持、競合なし` beside a plain sentence on the same card and it reads as one voice.

**The pass.** Four strings name the thing you sign in with. Fifteen locales used one noun for
it. Four reached for a cognate of the English "pass" in `crew.e.pass` while their own other
three strings had settled on a key -- so a Danish rider fetched `din nøgle` and was later told
`Dit pas er udløbet`, a different object and mostly a passport. In each of the four it was that
one string against three siblings.
"""
import pathlib
import re
import sys

import pytest

NL = chr(10)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))
from i18n_data import TRANSLATIONS            # noqa: E402

# A sentence-final polite ending. The lookbehind is a Hangul syllable so that nouns which
# simply end in the syllable 요 -- 필요, 중요, 내용 -- are not mistaken for politeness; the
# first pass of this flagged `초대 코드 필요` ("invite code") and would have had me rewrite it.
POLITE = {
    "ko": re.compile(r"[가-힣](?:요|예요|에요)[.!?]?$"),
    "ja": re.compile(r"(?:です|ます|ません|でした|ました|ですか|ますか)[。！？]?$"),
}
NOT_POLITE = {"ko": re.compile(r"(?:필|중|주|내)요[.!?]?$"), "ja": re.compile(r"(?!)")}


def sentences(value, loc):
    sep = r"(?<=[。！？])" if loc == "ja" else r"(?<=[.!?])\s+"
    return [s.strip() for s in re.split(sep, value) if s.strip()]


@pytest.mark.parametrize("loc", sorted(POLITE))
def test_one_speech_level_per_locale(loc):
    bad = []
    for key, value in TRANSLATIONS[loc].items():
        if not key.startswith("crew.") or not isinstance(value, str):
            continue
        for s in sentences(value, loc):
            if POLITE[loc].search(s) and not NOT_POLITE[loc].search(s):
                bad.append((key, s))
                break
    assert not bad, (
        f"{loc}: {len(bad)} strings switch to the polite level while the rest of the locale "
        f"is plain. A map key cannot be politely addressing the reader next to a chip that "
        f"is not." + NL + NL.join(f"  {k}: {s}" for k, s in bad[:12]))


# The noun each locale has settled on, taken from that locale's own strings rather than chosen
# here. Fifteen of the eighteen already agreed with themselves.
PASS_NOUN = {
    "da": "nøgl", "de": "schlüssel", "es": "pase", "es-419": "pase", "fr": "clé",
    "it": "pass", "ja": "パス", "ko": "패스", "nl": "sleutel", "no": "nøkkel",
    "pl": "przepust", "pt-BR": "passe", "ru": "пропуск", "sv": "nyckel", "tr": "izn",
    "uk": "пропуск", "zh": "通行证", "zh-Hant": "通行證",
}
# `crew.mine.signout` and its question were in here while the control was called "Hand
# the pass back". It is "Sign out" now -- Erwin asked twice what the old label meant,
# which is the answer to whether a metaphor is carrying -- so neither string mentions a
# pass and neither should be asked to. What is left are the two that really do fetch and
# return one: the sign-in heading, and the error for acting without one.
PASS_KEYS = ["crew.e.pass", "crew.signin.qralt"]


@pytest.mark.parametrize("loc", sorted(PASS_NOUN))
def test_the_pass_has_one_name(loc):
    missing = [k for k in PASS_KEYS
               if PASS_NOUN[loc] not in (TRANSLATIONS[loc].get(k) or "").lower()]
    assert not missing, (
        f"{loc} calls the pass '{PASS_NOUN[loc]}' in the strings that fetch and return it, "
        f"but not in {missing}: "
        + "; ".join(f"{k} = {TRANSLATIONS[loc].get(k)!r}" for k in missing))
