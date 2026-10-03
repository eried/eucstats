"""One card, one person. No locale may address the crew and the rider on the same card.

A reviewer traced three rounds of my patching back to a single cause: the English never
decides whether "you" is the crew or the rider, so eighteen translators decided eighteen
times and the seam fell inside cards and inside single sentences. They measured 29 split
cards across 13 locales, including a German rules list that changed who it was talking to
five times in eight bullets.

The backend has already decided, so the English can:

  * TARGETS and LOSE are the crew's. `crews_api` serves `clan.targets_json` -- one plan per
    crew, identical for every member -- so these cards address a crew. Plural.
  * SIGNIN, MINE, JOIN and HOW are one rider's: "get your key", "you run it", "you just
    walked out of one". Singular.

Where the English means the crew inside an otherwise singular card it now says so -- "what
your crew already holds", "how many of the crew ride it" -- rather than leaving a "you" for
each language to guess at.

This test is the part that makes it stick. The reviewer's sharpest criticism was that three
fixes in one round were instance fixes dressed as class fixes and the suite went green on all
three; a class is only closed when something checks it.

Only languages that mark the distinction unambiguously are checked. Markers that double as a
third-person possessive are deliberately absent -- German bare `ihr` is also "its", Spanish
`su` is also "its", Norwegian `deres` is also "theirs" -- because a detector that reports
correct strings is the same false assurance pointed the other way, and all three of those
produced false alarms on the first pass.
"""
import collections
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "web"))
from i18n_data import TRANSLATIONS            # noqa: E402

SINGULAR = {
    "de": r"\b(du|dein|deine|deinem|deinen|deiner|dir|dich)\b",
    "fr": r"\b(tu|ton|ta|tes|toi)\b",
    "es": r"\b(tú|tu|tus|tienes)\b",
    "es-419": r"\b(tú|tu|tus|tienes|tenés)\b",
    "it": r"\b(tu|tuo|tua|tuoi|tue)\b",
    "nl": r"\b(je|jouw|jij)\b",
    "pl": r"\b(twój|twoje|twoja|twoim|twojego|ty)\b",
    "pt-BR": r"\b(você|seu|sua|seus|suas)\b",
    "ru": r"\b(ты|твой|твоя|твои|твоего|тебя|тебе)\b",
    "uk": r"\b(ти|твій|твоя|твої|тебе|тобі)\b",
    "sv": r"\b(du|din|ditt|dina|dig)\b",
    "da": r"\b(du|din|dit|dine|dig)\b",
    "no": r"\b(du|din|ditt|dine|deg)\b",
}

# Plural markers only where they cannot be a third-person possessive.
PLURAL = {
    "de": r"\b(euch|euer|eure|eurem|euren|eurer)\b",      # not bare `ihr`: also "its"
    "fr": r"\b(vous|votre|vos)\b",
    "es": r"\b(vosotros|vuestro|vuestra|vuestros|vuestras|tenéis|estáis|podéis)\b",
    "es-419": r"\b(ustedes|tienen|pueden)\b",             # not `su`/`sus`: also "its"
    "it": r"\b(voi|vostro|vostra|vostri|vostre)\b",
    "nl": r"\b(jullie)\b",
    "pl": r"\b(wasz|wasze|wasza|waszego|was|wam|musicie|możecie)\b",
    "pt-BR": r"\b(vocês)\b",
    "ru": r"\b(вы|ваш|ваша|ваши|вашего|вас|вам)\b",
    "uk": r"\b(ви|ваш|ваша|ваші|вас|вам)\b",
    "sv": r"\b(ni|ert|era)\b",                            # not `er`: also "their"
    "da": r"\b(jer|jeres)\b",
    "no": r"\b(dere)\b",                                  # not `deres`: also "theirs"
}

# Who each card is talking to. The value is documentation as much as data.
CARDS = {
    "crew.targets.": "crew",
    "crew.lose.": "crew",
    "crew.signin.": "rider",
    "crew.mine.": "rider",
    "crew.join.": "rider",
    "crew.how.": "rider",
}


def card_of(key):
    for prefix in CARDS:
        if key.startswith(prefix):
            return prefix
    return None


@pytest.mark.parametrize("loc", sorted(SINGULAR))
def test_no_card_addresses_both_the_crew_and_the_rider(loc):
    sg_re, pl_re = re.compile(SINGULAR[loc], re.I), re.compile(PLURAL[loc], re.I)
    per_card = collections.defaultdict(lambda: {"singular": [], "plural": []})
    for key, value in TRANSLATIONS[loc].items():
        card = card_of(key)
        if not card or not isinstance(value, str):
            continue
        if sg_re.search(value):
            per_card[card]["singular"].append(key)
        if pl_re.search(value):
            per_card[card]["plural"].append(key)

    split = {c: d for c, d in per_card.items() if d["singular"] and d["plural"]}
    assert not split, (
        f"{loc}: a card cannot address the crew and the rider at once.\n"
        + "\n".join(
            f"  {c} wants {CARDS[c]}: singular in {d['singular'][:4]}, "
            f"plural in {d['plural'][:4]}" for c, d in split.items()))
