"""One card, one person -- the person it says it is, carried by every word in it.

A reviewer traced three rounds of my patching back to one cause: the English never decided
whether "you" was the crew or the rider, so eighteen translators decided eighteen times and the
seam fell inside cards and inside single sentences. They measured 29 split cards across 13
locales. The English decides now, the split cards are gone, and this file is what holds it --
which it did not really do, on three counts reviewers found in the round after:

  * **It never enforced the declaration.** `CARDS[c]` was read only inside the failure
    message, so the test rejected MIXING and nothing else: a locale could render the whole
    targets card in the singular, addressing one rider about a plan that belongs to a crew,
    and this stayed green.
  * **It covered 96 keys of 217.** `crew.e.`, `crew.tile.`, `crew.declined.`, `crew.new.`,
    `crew.first.` and the rest were outside it entirely -- which is how `crew.e.leaderback`
    ("Your leader turned up again", about the reader's own crew) came to be second-person
    PLURAL in 16 of 18 locales with nothing noticing.
  * **It was pronouns and possessives only.** Person is carried by the verb in most of these
    languages, and the round-eleven fix changed the pronouns on the targets card to plural and
    left the imperatives singular. A reviewer counted 19 live violations: de `Fahr {v} darin
    und ihr seid auf der Karte` -- singular imperative, plural verb, one sentence -- with
    `Fahrt sie alle` on the same card.

So: every key is declared, the declaration is enforced, and person is looked for in three
places rather than one.

**Pronouns and possessives.** As before, and still only where they cannot be a third-person
possessive: German bare `ihr` is also "its", Spanish `su` is also "its", Norwegian `deres` is
also "theirs", Danish `De/Deres` is also formal "they". All four produced false alarms on a
first pass, and a detector that reports correct strings is the same false assurance pointed the
other way.

**Closed-class verb forms.** The auxiliaries and modals -- `seid`/`bist`, `êtes`/`es`,
`siete`/`sei`, `jesteście`/`jesteś` -- mark person unambiguously and there are a fixed dozen of
them per language, so this is a list that cannot quietly fall behind the copy. Open-class verbs
are deliberately not listed: `Fahrt` is a 2PL imperative and also a noun, and German 3SG shares
the `-t` ending, so a list of ordinary verbs produces exactly the false alarms that get a
detector narrowed until it catches nothing.

**Imperatives disagreeing with each other.** Which needs no vocabulary at all, and is the half
that closes the open class: on one card, two strings whose first word shares a stem but differs
by a plural ending are the defect itself -- `Fahr` beside `Fahrt`, `Roule` beside `Roulez`,
`Проедь` beside `Проедьте`. Imperatives are clause-initial in all of these languages, and
requiring a stem-matched PAIR inside one card means no noun can trigger it.

The last test is about usage rather than wording, and comes from a fourth finding: the data was
right and the rendering was not. `crew.join.wait.h` -- the join card's heading, which addresses
the rider reading it -- was used as the label on a refused rider's row, so a Norwegian leader
was told `Kjøl deg ned` about somebody else. Pinning strings by key prefix cannot see that, so
the shipped JS is scanned for where each key is actually rendered.
"""
import collections
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
from i18n import EN                            # noqa: E402
from i18n_data import TRANSLATIONS             # noqa: E402

NL = chr(10)
CREWS_JS = ROOT / "web" / "static" / "crews.js"

# Who each group of keys is talking to.
#   "rider" -- the reader, as one person. Singular.
#   "crew"  -- the reader's crew, as a group. Plural. The backend decides this, not the copy:
#              `crews_api` serves `clan.targets_json`, one plan per crew, the same for every
#              member, so these cards cannot be addressing an individual.
#   "none"  -- no second person at all. Either a bare label, or deliberately holder-neutral:
#              the tile words fire on every crew's squares, not only your own, so "yours" over
#              a rival's ground would simply be false.
CARDS = {
    "crew.targets.": "crew",
    "crew.lose.": "crew",
    "crew.legend.": "crew",
    "crew.signin.": "rider",
    "crew.mine.": "rider",
    "crew.join.": "rider",
    "crew.how.": "rider",
    "crew.e.": "rider",
    "crew.err": "rider",
    "crew.empty": "rider",
    "crew.declined.": "rider",
    "crew.folded.": "rider",
    "crew.removed.": "rider",
    "crew.first.": "rider",
    "crew.new.": "rider",
    "crew.closed.": "rider",
    "crew.drawn.": "rider",
    "crew.board": "rider",
    "crew.board.": "rider",
    "crew.roles.": "rider",
    "crew.decl.": "none",
    "crew.tile.": "none",
    "crew.role.": "none",
    "crew.policy.": "none",
    "crew.take.": "none",
    "crew.hold.": "none",
    "crew.rank.": "none",
    "crew.ago.": "none",
    "crew.who.": "none",
    "crew.off.": "none",
    "crew.pending.": "none",
    "crew.accept": "none",
    "crew.cancel": "none",
    "crew.decline": "none",
    "crew.now": "none",
    "crew.inall": "none",
    "crew.tile1": "none",
    "crew.tiles": "none",
    "crew.tiles.": "none",
    "crew.rider1": "none",
    "crew.riders": "none",
    "crew.riders.": "none",
    "crew.day1": "none",
    "crew.days": "none",
    "crew.days.": "none",
    "crew.patches": "none",
    "crew.patches.": "none",
}

SINGULAR = {
    "de": r"\b(du|dein|deine|deinem|deinen|deiner|dir|dich"
          r"|bist|hast|kannst|musst|darfst|sollst|willst|wirst|würdest|hättest)\b",
    "fr": r"\b(tu|ton|ta|tes|toi"
          r"|es|as|peux|dois|veux|vas|seras|auras|pourras)\b",
    "es": r"\b(tú|tu|tus"
          r"|eres|estás|tienes|puedes|debes|vas|serás|tendrás|podrás)\b",
    "es-419": r"\b(tú|tu|tus"
              r"|eres|estás|tienes|tenés|puedes|podés|debes|vas|serás|tendrás)\b",
    "it": r"\b(tu|tuo|tua|tuoi|tue"
          r"|sei|hai|puoi|devi|vuoi|vai|sarai|avrai|potrai)\b",
    "nl": r"\b(je|jouw|jij)\b",
    "pl": r"\b(twój|twoje|twoja|twoim|twojego|ty"
          r"|jesteś|masz|możesz|musisz|chcesz|będziesz)\b",
    "pt-BR": r"\b(você|seu|sua|seus|suas)\b",
    "ru": r"\b(ты|твой|твоя|твои|твоего|тебя|тебе"
          r"|можешь|хочешь|должен|будешь|имеешь)\b",
    "uk": r"\b(ти|твій|твоя|твої|тебе|тобі"
          r"|можеш|хочеш|мусиш|будеш)\b",
    "sv": r"\b(du|din|ditt|dina|dig)\b",
    "da": r"\b(du|din|dit|dine|dig)\b",
    "no": r"\b(du|din|ditt|dine|deg)\b",
}

# Plural markers, only where they cannot be a third person.
PLURAL = {
    # not bare `ihr`: also "its". `seid`/`habt`/`könnt` cannot be anything else.
    "de": r"\b(euch|euer|eure|eurem|euren|eurer"
          r"|seid|habt|werdet|könnt|müsst|dürft|sollt|wollt|möchtet|hättet)\b",
    "fr": r"\b(vous|votre|vos"
          r"|êtes|avez|pouvez|devez|voulez|allez|serez|aurez|pourrez)\b",
    "es": r"\b(vosotros|vuestro|vuestra|vuestros|vuestras"
          r"|sois|estáis|tenéis|podéis|debéis|habéis|vais|seréis|tendréis)\b",
    # not `su`/`sus`: also "its". `tienen`/`pueden` are 3PL too, and were already here.
    "es-419": r"\b(ustedes|tienen|pueden)\b",
    "it": r"\b(voi|vostro|vostra|vostri|vostre"
          r"|siete|avete|potete|dovete|volete|andate|sarete|avrete|potrete)\b",
    "nl": r"\b(jullie)\b",
    "pl": r"\b(wasz|wasze|wasza|waszego|was|wam"
          r"|jesteście|macie|możecie|musicie|chcecie|będziecie)\b",
    "pt-BR": r"\b(vocês)\b",
    "ru": r"\b(вы|ваш|ваша|ваши|вашего|вас|вам"
          r"|можете|хотите|должны|будете|имеете)\b",
    "uk": r"\b(ви|ваш|ваша|ваші|вас|вам"
          r"|можете|хочете|мусите|будете)\b",
    "sv": r"\b(ni|ert|era)\b",                            # not `er`: also "their"
    "da": r"\b(jer|jeres)\b",                             # not `De/Deres`: also formal "they"
    "no": r"\b(dere)\b",                                  # not `deres`: also "theirs"
}

def card_of(key):
    """The longest declared prefix that matches, so `crew.board.sub` beats `crew.board`."""
    best = None
    for prefix in CARDS:
        if key == prefix or (prefix.endswith(".") and key.startswith(prefix)):
            if best is None or len(prefix) > len(best):
                best = prefix
    return best


def test_every_key_is_declared():
    """A key nobody declared is a key nobody checks, which is how 121 of them went unchecked."""
    undeclared = sorted(k for k in EN if k.startswith("crew.") and not card_of(k))
    assert not undeclared, (
        f"{len(undeclared)} keys belong to no card, so no locale's person is checked for "
        f"them. Add them to CARDS:\n  " + "\n  ".join(undeclared))


@pytest.mark.parametrize("loc", sorted(SINGULAR))
def test_each_card_addresses_the_person_it_declares(loc):
    """Not merely consistent -- correct.

    `CARDS[c]` used to appear only in the failure message, so a card rendered entirely in the
    wrong person passed. Both halves are checked now: a crew's plan is never explained to one
    rider, a rider's own key is never handed to a group, and the holder-neutral tile words
    address nobody, because they describe other crews' squares as often as your own.
    """
    sg_re, pl_re = re.compile(SINGULAR[loc], re.I), re.compile(PLURAL[loc], re.I)
    wrong = collections.defaultdict(list)
    for key, value in TRANSLATIONS[loc].items():
        card = card_of(key)
        if not card or not isinstance(value, str):
            continue
        want = CARDS[card]
        if want in ("crew", "none") and sg_re.search(value):
            wrong[card + " (" + want + ")"].append(("singular", key, value))
        if want in ("rider", "none") and pl_re.search(value):
            wrong[card + " (" + want + ")"].append(("plural", key, value))

    assert not wrong, (
        f"{loc}: {sum(len(v) for v in wrong.values())} strings address the wrong person.\n"
        + "\n".join(
            f"  {c}: " + "; ".join(f"{kind} in {k} -- {v}" for kind, k, v in rows[:3])
            for c, rows in sorted(wrong.items())))


# Singular orders that must not come back to a card that belongs to the crew.
#
# A list, which is the honest shape for this one. Person lives in open-class verbs here, and
# they cannot be recognised in general without reporting correct strings: German `Fahrt` is a
# 2PL imperative and also a noun, and 3SG takes the same `-t`. I tried a stem-matched rule
# first and it found three false alarms (Spanish `queda`/`quedan`, Portuguese `falta`/`faltam`
# -- agreement with the number of SQUARES, not with the reader) and not one real defect, which
# is the same false assurance pointed the other way.
#
# So it is a floor and not a ceiling: it holds the 28 forms two audits found, and genuinely new
# copy on this card still needs a translator. Every replacement was a form the same card
# already used elsewhere -- Spanish said `Rodad` in one string and `Rueda` in another.
# An imperative opens its clause in every one of these languages but one, and that is what
# tells it apart from the third person it is spelled like: `Nadie más rueda por aquí` means
# nobody rides here, while `. Rueda un bloque` is an order. Matching the bare verb reported
# all three of those correct strings.
#
# Turkish is verb-final, so its forms are matched at the END of a clause instead -- `blok
# sür,` against `blok sürün,`. It was already the language whose two defects an audit by
# hand missed, and a start-of-clause rule would have made it silent here as well.
START_OF_CLAUSE = r"(?:^|[.,;:!?]\s+)"
END_OF_CLAUSE = r"(?=[.,;:!?]|$)"

SINGULAR_ORDERS = {
    "de": START_OF_CLAUSE + r"(fahr|nimm)\b",
    "es": START_OF_CLAUSE + r"(rueda|coge)\b",
    "es-419": START_OF_CLAUSE + r"(rueda|agarra)\b",
    "fr": START_OF_CLAUSE + r"(roule|prends)\b",
    "it": START_OF_CLAUSE + r"(fai|gira|prendi)\b",
    "pl": START_OF_CLAUSE + r"(przejedź|weź|dotknij)\b",
    "pt-BR": START_OF_CLAUSE + r"(roda|pega|toca)\b",
    "ru": START_OF_CLAUSE + r"(проедь|бери|нажми)\b",
    "tr": r"\b(sür|al)" + END_OF_CLAUSE,
    "uk": START_OF_CLAUSE + r"(проїдь|бери|натисни)\b",
}


@pytest.mark.parametrize("loc", sorted(SINGULAR_ORDERS))
def test_the_crews_plan_is_not_ordered_about_one_rider(loc):
    """The imperatives on the targets card, which the pronouns were fixed without.

    Round eleven made this card's pronouns plural and left its orders singular, so one
    sentence read `Fahr {v} darin und ihr seid auf der Karte` -- told to one person, said
    about a group -- while `Fahrt sie alle` sat four strings away.
    """
    rx = re.compile(SINGULAR_ORDERS[loc], re.I)
    bad = [(k, v) for k, v in TRANSLATIONS[loc].items()
           if k.startswith("crew.targets.") and isinstance(v, str) and rx.search(v)]
    assert not bad, (
        f"{loc}: {len(bad)} strings on the crew's own card give a singular order:" + NL
        + NL.join(f"  {k}: {v}" for k, v in bad))


# Where a key may be rendered. A key's card is a claim about who is being spoken to, and the
# claim is only true if the string lands on that card -- which `crew.join.wait.h` did not: it
# was the label on a REFUSED RIDER'S row inside the crew card, so a Norwegian leader read
# "Kjøl deg ned" about somebody else.
#
# Several keys are legitimately shared, and each one is named rather than waved through, so the
# next piece of reuse has to be looked at.
SHARED = {
    "crew.mine.ao": {"targetsHTML"},            # "all told" -- a fragment with no person in it
    "crew.join.pending": {"myCrewHTML"},        # about the reader, who is the one waiting
    "crew.join.pending.none": {"myCrewHTML"},
    "crew.lose.h": {"legendHTML"},              # the card's own name, used as a key's label
    "crew.targets.h": {"legendHTML"},
    "crew.tiles": {"targetsHTML"},              # a count
    "crew.tile.fresh": {"legendHTML", "targetsHTML"},
}

# The functions that build each card. Anything rendering a key from another card has to appear
# in SHARED above.
HOME = {
    "crew.signin.": {"signInHTML", "startPairing", "offerRetry"},
    "crew.mine.": {"myCrewHTML", "bindMine", "leaveQuestion", "contributorsHTML", "errMsg"},
    "crew.join.": {"joinHTML", "bindJoin", "askFor"},
    "crew.how.": {"explainer"},
    "crew.targets.": {"targetsHTML", "ringTip", "bearing", "widest"},
    "crew.lose.": {"loseHTML", "ringTip"},
    "crew.decl.": {"myCrewHTML"},
    "crew.tile.": {"tileWords", "legendHTML", "fadesIn", "onCellClick", "onCellHover",
                   "ringTip", "targetsHTML"},
    "crew.legend.": {"legendHTML"},
    "crew.new.": {"createHTML", "myCrewHTML"},
    "crew.roles.": {"myCrewHTML", "bindMine"},
}


def _rendered_by():
    """Every `"crew.*"` literal in the shipped JS, against the function that renders it."""
    lines = CREWS_JS.read_text(encoding="utf-8").split("\n")
    out, cur = collections.defaultdict(set), "(top)"
    for line in lines:
        m = re.match(r"  function ([A-Za-z_]\w*)\(", line)
        if m:
            cur = m.group(1)
        for key in re.findall(r"""["'](crew\.[a-zA-Z0-9_.]+)["']""", line):
            out[key].add(cur)
    return out


def test_no_card_borrows_another_cards_words():
    """Usage, not wording -- the finding this file could not see.

    The translations were right and the rendering was not: a rider-addressed heading was used
    as a third party's label. Pinning strings by key prefix is blind to that by construction,
    so the shipped JS is read for where each key actually goes.
    """
    used = _rendered_by()
    strays = []
    for key, fns in sorted(used.items()):
        home = None
        for prefix, allowed in HOME.items():
            if key.startswith(prefix) and (home is None or len(prefix) > len(home[0])):
                home = (prefix, allowed)
        if not home:
            continue
        for fn in sorted(fns):
            if fn in home[1] or fn in SHARED.get(key, ()):
                continue
            strays.append(f"  {key} ({home[0]}) rendered in {fn}()")
    assert not strays, (
        f"{len(strays)} keys are rendered on a card other than the one whose person they "
        f"were written for. Either give that card its own key, or name the reuse in SHARED "
        f"with the reason it is safe:\n" + "\n".join(strays))
