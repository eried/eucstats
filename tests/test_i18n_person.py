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

**An inventory of the words a crew card actually opens its clauses with.** Which needs no
vocabulary of any language, and is the half that closes the open class. A stem-matched rule
was tried first and found three false alarms and no real defects; the list of known-wrong
forms that replaced it let 14 of 18 one-word regressions through, because a list of mistakes
cannot be finished. The list of words that are THERE can: 39 to 54 per locale. New copy fails
until somebody adds its word, which is the review this file wants.

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
    # the test notice: it is at the top of the panel rather than on a card, and it
    # tells the one reader that what they build may be wiped
    "crew.wip": "rider",
    "crew.mine.": "rider",
    # The hover explanations, which say what a button does TO THE READER: "You stop riding
    # for this crew", "Signs this browser out".
    "crew.tip.": "rider",
    # "{n} riders want to join your crew" -- addressed to the leader reading the badge.
    "crew.knock1": "rider",
    "crew.knocks": "rider",
    "crew.knocks.few": "rider",
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
    # One word each, naming a fill pattern. No verb, no person, nothing to address.
    "crew.pattern.": "none",
    "crew.closed.": "rider",
    "crew.drawn.": "rider",
    "crew.board": "rider",
    "crew.board.": "rider",
    # The public crew page at /c/<slug>, and the card that hands out its address. The page is
    # read by somebody who has just pointed a camera at a sticker and may not have an account
    # at all, so it addresses them the way the sign-in card does -- "Scan to find this crew",
    # "Open in EUC Stats". `crew.share.p` is on a button in the crew card and tells the reader
    # what printing it will get them, which is the same person `crew.tip.` speaks to.
    # Two buttons on the popup over a square, naming what pressing them does. No person in
    # either: "Highlight", "Details".
    # One word on the collapsed strip over the map. Names a thing; addresses nobody.
    "crew.key": "none",
    "crew.pop.": "none",
    "crew.pub.": "rider",
    "crew.share": "rider",
    "crew.share.": "rider",
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
    "es": r"\b(tú|tu|tus|eres|estás|estés|tienes|tengas|puedes|puedas|debes|vas"
          r"|aguantas|serás|tendrás|podrás)\b",
    "es-419": r"\b(tú|tu|tus"
              r"|eres|estás|tienes|tenés|puedes|podés|debes|vas|serás|tendrás)\b",
    "it": r"\b(tu|tuo|tua|tuoi|tue"
          r"|sei|sia|hai|abbia|puoi|devi|vuoi|tieni|vai|sarai|avrai|potrai)\b",
    "nl": r"\b(je|jouw|jij)\b",
    "pl": r"\b(twój|twoje|twoja|twoim|twojego|ty"
          r"|jesteś|masz|możesz|musisz|chcesz|będziesz|przejechałeś|zatrzymasz)\b",
    "pt-BR": r"\b(você|seu|sua|seus|suas)\b",
    "ru": r"\b(ты|твой|твоя|твоё|твои|твоего|твою|твоим|твоих|тебя|тебе"
          r"|можешь|хочешь|должен|будешь|имеешь)\b",
    "uk": r"\b(ти|твій|твоя|твоє|твої|твого|твою|твоїм|твоїх|тебе|тобі"
          r"|можеш|хочеш|мусиш|будеш)\b",
    "sv": r"\b(du|din|ditt|dina|dig)\b",
    "da": r"\b(du|din|dit|dine|dig)\b",
    "no": r"\b(du|din|ditt|dine|deg)\b",
    # Chinese marks it with one character, and was absent entirely.
    "zh": r"你(?!们)|您",
    "zh-Hant": r"你(?!們)|您",
    # Turkish marks person by suffix, so this does too. Only the verb endings: the bare
    # possessive `-in` is two letters and would match half the language.
    "tr": r"\b(sen|seni|sana|senin)\b|\b\w{3,}(?:sin|sın|sun|sün)\b",
}

# Plural markers, only where they cannot be a third person.
#
# Endings where an ending is genuinely unambiguous, and a closed list where it is not. Every
# candidate below was run over all 219 keys in all 18 locales and kept only when everything it
# matched on a card declared "crew" really was second-person plural and it reported nothing
# correct elsewhere. What that test REJECTED is the useful half:
#
#   * es-419 `(?:en|an)` and pt-BR `(?:em|am)` collide with the third person plural -- 16
#     correct strings reported between them;
#   * German `-t` collides with the third person singular (`geht`, `holt`) and with the noun
#     `Fahrt`;
#   * Polish `-cie` collides with ordinary nouns -- `Wyjście`, `szczycie`.
#
# Those four keep lists. The rest earn an ending.
PLURAL = {
    # not bare `ihr`: also "its". `seid`/`habt`/`könnt` cannot be anything else.
    "de": r"\b(euch|euer|eure|eurem|euren|eurer"
          r"|seid|habt|werdet|könnt|müsst|dürft|sollt|wollt|möchtet|hättet)\b",
    "fr": r"\b(vous|votre|vos"
          r"|êtes|avez|pouvez|devez|voulez|allez|serez|aurez|pourrez)\b",
    "es": r"\b(vosotros|vuestro|vuestra|vuestros|vuestras|os)\b"
          r"|\b\w{3,}(?:áis|éis|ís)\b",
    # `tienen`/`pueden` are third person too, and were already here; `-en`/`-an` as an ending
    # reported sixteen correct strings, so this one stays a list.
    "es-419": r"\b(ustedes|tienen|pueden)\b",
    "it": r"\b(voi|vostro|vostra|vostri|vostre)\b|\b\w{3,}(?:ate|ete|ite)\b",
    "nl": r"\b(jullie)\b",
    "pl": r"\b(wasz|wasze|wasza|waszego|was|wam|jesteście|macie|możecie|musicie|chcecie"
          r"|będziecie|zatrzymacie|przejechaliście|wybierzcie|przejedźcie|dotknijcie"
          r"|weźcie)\b",
    "pt-BR": r"\b(vocês)\b",
    # `(?:ете|ите|йте|ьте)` and not a bare `те`, which also matched `карте`, the dative of
    # "map". Ukrainian is safe from its own third person `вицвіте`/`росте` because Ukrainian
    # spells that vowel `і` and the ending here is the Russian `и`.
    "ru": r"\b(вы|ваш|ваша|ваше|ваши|вашу|вашего|вашим|ваших|вас|вам)\b"
          r"|\b\w{3,}(?:ете|ите|йте|ьте)\b",
    "uk": r"\b(ви|ваш|ваша|ваше|ваші|вашу|вашого|вашим|ваших|вас|вам)\b"
          r"|\b\w{3,}(?:ете|ите|йте|ьте)\b",
    # Swedish "their" is `deras`. `er/ert/era` is unambiguously 2PL and excluding it as
    # ambiguous hid two strings -- the exclusion was wrong, not cautious.
    "sv": r"\b(ni|er|ert|era)\b",
    # `I` is the pronoun and `i` the preposition. It was missing only because every pattern
    # here compiles with re.I, which a scoped case-sensitive group settles.
    "da": r"\b(jer|jeres)\b|(?-i:(?<![\wæøå])I(?![\wæøå]))",
    "no": r"\b(dere)\b",                                 # not `deres`: also "theirs"
    "zh": r"你们",
    "zh-Hant": r"你們",
    "tr": r"\b(siz|sizi|size|sizin)\b"
          r"|\b\w{3,}(?:iniz|ınız|unuz|ünüz|siniz|sınız|sunuz|sünüz)\b",
}


# Japanese and Korean mark person by register and by verb ending rather than by pronoun, and
# their crew cards use number-neutral plain forms (`走れ`, `달려라`) that carry no number to
# check. Both were audited by hand and neither has a live defect -- `crew.legend.note` uses
# `自分たち` and `너희` correctly. They are exempt by name rather than by omission, so that a
# nineteenth locale cannot join the set and be skipped in silence.
EXEMPT = {"ja", "ko"}


def test_every_locale_is_either_checked_or_exempt():
    assert set(SINGULAR) | EXEMPT == set(TRANSLATIONS), (
        "a locale is neither checked for person nor named as exempt: "
        + repr(sorted(set(TRANSLATIONS) - set(SINGULAR) - EXEMPT)))


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
# Every word that currently opens a clause on a card belonging to the crew -- the inventory,
# not a list of the mistakes two audits happened to find. A reviewer mutated one word per
# string and 14 of 18 survived the blacklist that used to be here: `Wählt eines aus` ->
# `Wähl eines aus`, `Elegid` -> `Elige`, `Выберите` -> `Выбери`. "Pick one" had never been
# audited in any language, and a list of what is WRONG can never be finished.
#
# This one can: 39 to 54 words per locale. New copy fails until its word is added, which is
# the moment to read it -- and no vocabulary of any language is needed to maintain it.
#
# Turkish freezes the clause TAIL rather than its head, because Turkish puts the verb last.
# `tr` gained "üzere" with `crew.lose.threat` ("{name} {v} almak üzere." -- "{name} is about
# to take {v}"). Read before adding: `almak üzere` is a verbal construction meaning "about to",
# a third-person statement about the RIVAL, so it is not the failure this guard watches for --
# nobody is addressing the crew as one rider. Added deliberately.
# `crew.lose.cold.n` added seven clause-openers, each read before being listed. "keiner" (de)
# and "без" (ru, uk) are an indefinite pronoun and a preposition introducing the second half --
# "nobody rides there any more", "without riders"; "uğramıyor" (tr) is the verb of that same
# clause, "nobody drops by". The bare "v" in ja, zh and zh-Hant is the `{v}` placeholder itself
# opening the sentence, which no string in those locales had done before. None of the seven
# addresses the crew as one rider, which is the failure this guard exists to catch.
CREW_WORDS = {
    "da": {"alle", "binder", "bliver", "bryder", "de", "del", "den", "det", "enhver", "er", "giver", "hvad", "hver", "hvor", "i", "ikke", "ingen", "jeres", "klaret", "kold", "kryber", "kør", "kørt", "men", "n", "name", "nogen", "nv", "nø", "og", "ryger", "s", "sender", "sv", "så", "sætter", "sø", "tag", "taget", "uden", "v", "vælg", "ét", "ø"},
    "de": {"aber", "alle", "ausgefahren", "bricht", "bringt", "dann", "den", "der", "die", "diese", "drückt", "eine", "er", "erledigt", "es", "euer", "fahrt", "geht", "ihr", "ist", "jedes", "jemand", "kalt", "keiner", "kommt", "n", "name", "nehmt", "niemand", "no", "noch", "nw", "o", "s", "setzt", "sie", "so", "sobald", "sonst", "sw", "teil", "um", "und", "v", "verbindet", "vorerst", "w", "was", "wie", "wird", "wohin", "wählt"},
    "es": {"alguien", "aparece", "cada", "caen", "coged", "cualquiera", "cuánto", "dónde", "e", "el", "elegid", "esta", "están", "frío", "fuera", "hecho", "la", "las", "les", "lo", "los", "n", "nada", "nadie", "name", "ne", "no", "o", "os", "parte", "pero", "queda", "quedan", "recorredlas", "rodad", "s", "se", "simplemente", "so", "suma", "todavía", "tomado", "une", "v", "volved", "vuestro", "y", "ya"},
    "es-419": {"agarren", "alguien", "aparece", "cada", "caen", "cualquiera", "cuánto", "dónde", "e", "el", "elijan", "esta", "están", "frío", "fuera", "la", "las", "les", "listo", "lo", "los", "n", "nada", "nadie", "name", "ne", "no", "o", "parte", "pero", "queda", "quedan", "recórranlas", "rueden", "s", "se", "simplemente", "so", "su", "suma", "todavía", "tomado", "une", "v", "vuelvan", "y", "ya"},
    "fr": {"ajoute", "casse", "ce", "celui", "cette", "chacune", "choisissez-en", "combien", "déjà", "e", "encore", "et", "fait", "froid", "hors", "il", "ils", "le", "les", "mais", "n", "name", "ne", "no", "o", "où", "personne", "plus", "prenez", "pris", "quelqu", "refroidit", "relie", "repassez", "rien", "roulez", "roulez-les", "s", "se", "so", "une", "v", "votre", "vous", "ça", "échappe"},
    "it": {"aggiunge", "avete", "cadono", "ce", "chiunque", "compare", "cosa", "dove", "e", "fate", "fatto", "freddo", "fuori", "girate", "già", "gli", "il", "le", "li", "ma", "manca", "mancano", "n", "name", "ne", "nessuno", "niente", "no", "non", "o", "ognuna", "parte", "passa", "passatele", "prendete", "preso", "qualcuno", "quanto", "quello", "questa", "ripassateci", "s", "scegliete", "se", "si", "so", "sono", "unisce", "v", "vi"},
    "ja": {"1マス増えるが、ブロックは増えない", "2つのエリアをつなぐ", "n", "name", "v", "あと", "あと一マス。印のついたマスを", "じわじわ来ている", "それぞれのマスであと何キロ走ればいいか。選ぶと場所が出る。", "だれかが持っている", "つなぐまであと", "ほかに", "まだ地図に載っていない。印のついたマスで", "まだ狙う先がない。どこかで", "ブロックが", "ブロックが決まれば", "ブロックは走り終えた。次の描き直しで出てくる。", "今は手が届かない", "今週", "今週取った", "冷えていて、誰にでも安い", "北", "北東", "北西", "南", "南東", "南西", "取られる寸前。もう一度流しておこう。", "失いそうなところ", "息をしているのは自分たちの土地。ほかのクランのものは動かない。", "放っておくと冷める", "明るく示されているのは自分たちの土地。ほかのクランのものはそのまま。", "最初のブロックの一部", "東", "次はここを走ろう", "済み", "相手のブロックを割る、", "相手を抜く", "西", "誰かに取られているのではない。ただ来なくなっただけ。", "走り切った、隣を取れ", "近くを走っているクランは他にいない。どれを取ってもブロックが1マス増える。"},
    "ko": {"n", "name", "s", "v", "가장", "각", "고르면", "그", "그냥", "그들을", "근처에", "남", "남동", "남서", "남의", "누가", "누구에게나", "누군가", "다", "다가오는", "다시", "다음", "다음은", "더", "동", "두", "북", "북동", "북서", "블록은", "블록이", "뺏기기", "상대", "서", "숨", "식었고", "아무", "아직", "어느", "옆", "완료", "이번", "잃을", "잇기까지", "전부", "지금은", "첫", "표시된", "한"},
    "nl": {"breekt", "dat", "de", "deel", "deze", "drukt", "elk", "en", "er", "ga", "gaat", "het", "hoeveel", "iedereen", "iemand", "is", "jullie", "kies", "klaar", "koelt", "komt", "koud", "levert", "maar", "n", "name", "niemand", "no", "nog", "nw", "o", "pak", "rijd", "uitgereden", "v", "verbindt", "voorlopig", "w", "waar", "wat", "z", "ze", "zet", "zo", "zw"},
    "no": {"alle", "binder", "blir", "blokka", "bryter", "de", "del", "den", "denne", "dere", "det", "ferdig", "gir", "hva", "hvem", "hver", "hvor", "ingen", "ingenting", "kald", "kjør", "kjørt", "men", "n", "name", "noen", "nv", "nærmer", "nø", "og", "ryker", "s", "sender", "setter", "sv", "så", "sø", "ta", "tatt", "utenfor", "v", "velg", "én", "ø"},
    "pl": {"a", "ale", "co", "cudze", "część", "daje", "e", "gdy", "gdzie", "i", "ile", "jaki", "jest", "każde", "każdy", "ktoś", "leci", "ma", "n", "na", "najtańszy", "name", "ne", "nie", "nikt", "nw", "odchodzi", "po", "pojawi", "przejechane", "przejedźcie", "razem", "rozbija", "s", "samo", "se", "spycha", "sw", "są", "to", "v", "w", "wasz", "weźcie", "wybierzcie", "wysuwa", "wzięte", "zaznaczone", "zbliża", "zimny", "zostało", "zrobione", "zrzuca", "łączy", "żeby"},
    "pt-BR": {"alguém", "aparece", "cada", "caem", "chegando", "derruba", "e", "escolham", "esfria", "esta", "estão", "falta", "faltam", "feito", "fora", "frio", "joga", "junta", "já", "l", "mas", "n", "nada", "name", "ne", "ninguém", "no", "o", "onde", "os", "parte", "passem", "peguem", "põe", "qualquer", "quanto", "quebra", "rodem", "s", "sai", "se", "so", "soma", "tomado", "v", "vocês"},
    "ru": {"n", "name", "v", "без", "берите", "в", "вас", "ваш", "взято", "вы", "выберите", "выводит", "готово", "даст", "дышит", "её", "з", "и", "их", "каждая", "как", "куда", "ломает", "любой", "на", "наезжено", "но", "они", "осталась", "осталось", "отбрасывает", "отмеченные", "подбираются", "пока", "появится", "проедьте", "проедьтесь", "рядом", "с", "самый", "сбивает", "св", "сз", "сколько", "соединяет", "ставит", "стынет", "уйдёт", "уходит", "холодный", "часть", "что", "чтобы", "чужое", "ю", "юв", "юз", "ярче"},
    "sv": {"alla", "binder", "bryter", "de", "del", "den", "det", "en", "ert", "färdigkörd", "ger", "går", "hur", "ingen", "inget", "kall", "kallnar", "klart", "kör", "men", "n", "name", "ni", "no", "nv", "närmar", "någon", "o", "och", "pressar", "s", "so", "sv", "sätter", "så", "ta", "taget", "utom", "v", "vad", "var", "vart", "vem", "välj"},
    "tr": {"alabilir", "almıyor", "alın", "alındı", "alıyor", "b", "birinde", "birleştirir", "bitmiş", "blok", "bıraktınız", "d", "daha", "değil", "değilsiniz", "dolar", "duruyor", "düşürür", "ekler", "g", "gb", "gd", "gerektiği", "geçin", "geçirir", "gider", "girersiniz", "hafta", "haritadasınız", "k", "kaldı", "kalıyor", "kare", "katar", "kaybedebilecekleriniz", "kb", "kd", "kırar", "olur", "parçası", "seçin", "soğudu", "soğuk", "soğuyor", "sürmüyor", "sürüldü", "sürün", "tamam", "topraklarınız", "tutuyor", "ucuz", "ucuzu", "ulaşılmaz", "uğramıyor", "v", "var", "yaklaşıyor", "yok", "çıkar", "üzere"},
    "uk": {"n", "name", "v", "але", "без", "беріть", "будь-хто", "вас", "ваш", "взято", "ви", "виберіть", "виводить", "вони", "відкидає", "готово", "дасть", "дихає", "з", "зʼявиться", "збиває", "кожна", "куди", "ламає", "лишилась", "лишилось", "найдешевший", "наїжджено", "пд", "пдз", "пдс", "пн", "пнз", "пнс", "позначені", "поки", "поряд", "проїдьте", "проїдьтесь", "підбираються", "піде", "с", "скільки", "ставить", "холодний", "холоне", "цього", "частина", "чуже", "що", "щоб", "щойно", "яскравіше", "і", "іде", "їх", "її"},
    "zh": {"name", "v", "下一趟骑哪里", "东", "东北", "东南", "他们马上就要拿走了。再过去压一遍。", "会呼吸的是你们自己的地盘。别人的不动。", "你们的方块升到", "你们的方块已经骑完了。下次重画就会出现。", "你们第一个方块的一部分", "你们还没上地图。标记的格子正好是一个", "再", "凉了，谁都便宜", "北", "南", "可能会丢的", "多一格，但不算进方块", "已完成", "打断他们的方块，掉", "把他们压到", "把他们挤到", "方块一成就从", "暂时够不着", "暂时没什么可盯的。随便找地方骑出一个", "有人占着", "本周", "本周拿下的", "标得更亮的是你们自己的地盘。别人的保持原样。", "正在逼近", "每个格子里还得再骑多少。选一个就能找到。", "没人在抢这些。是你们自己不去了。", "自己在变凉", "西", "西北", "西南", "让你们超过", "让你们超过他们", "还差", "还差一格。在标记的那格里骑", "还有", "连起两块地盘", "附近没有别的战队在骑。每一格都给你们的方块加一格。", "骑够了，去拿旁边那个"},
    "zh-Hant": {"name", "v", "下一趟騎哪裡", "他們馬上就要拿走了。再過去壓一遍。", "你們的方塊升到", "你們的方塊已經騎完了。下次重畫就會出現。", "你們第一個方塊的一部分", "你們還沒上地圖。標記的格子正好是一個", "再", "北", "南", "可能會丟的", "多一格，但不算進方塊", "已完成", "打斷他們的方塊，掉", "把他們壓到", "把他們擠到", "方塊一成就從", "暫時搆不著", "暫時沒什麼可盯的。隨便找地方騎出一個", "會呼吸的是你們自己的地盤。別人的不動。", "有人占著", "本週", "本週拿下的", "東", "東北", "東南", "標得更亮的是你們自己的地盤。別人的保持原樣。", "正在逼近", "每個格子裡還得再騎多少。選一個就能找到。", "沒人在搶這些。是你們自己不去了。", "涼了，誰都便宜", "自己在變涼", "西", "西北", "西南", "讓你們超過", "讓你們超過他們", "連起兩塊地盤", "還差", "還差一格。在標記的那格裡騎", "還有", "附近沒有別的戰隊在騎。每一格都給你們的方塊加一格。", "騎夠了，去拿旁邊那個"},
}

TAIL_FIRST = {"tr"}        # verb-final: the word that carries person is the last one


def clause_heads(value, loc):
    """The word that opens each clause, or closes it where the verb goes last."""
    out = []
    for clause in re.split(r"[.,;:!?]+", value):
        words = re.findall(r"[^\s.,;:!?\"'()\[\]{}\u00b7]+", clause)
        if words:
            out.append((words[-1] if loc in TAIL_FIRST else words[0]).lower())
    return out


@pytest.mark.parametrize("loc", sorted(CREW_WORDS))
def test_a_crew_card_opens_its_clauses_with_words_somebody_has_read(loc):
    """The crew's own cards, held by an inventory rather than by a list of known mistakes.

    Person lives in open-class verbs in most of these languages and they cannot be recognised
    in general -- German `Fahrt` is a 2PL imperative and also a noun, and 3SG takes the same
    `-t`. A stem-matched rule found three false alarms and no real defects, and a blacklist
    let 14 of 18 one-word regressions through. What is left is the words that are there.
    """
    crew_cards = tuple(c for c, who in CARDS.items() if who == "crew")
    unknown = []
    for key, value in sorted(TRANSLATIONS[loc].items()):
        if not key.startswith(crew_cards) or not isinstance(value, str):
            continue
        for word in clause_heads(value, loc):
            if word not in CREW_WORDS[loc]:
                unknown.append(f"  {key}: {word!r}")
    assert not unknown, (
        f"{loc}: {len(unknown)} clauses on a crew card open with a word this locale has not "
        f"used there before. If the copy changed on purpose, read it and add the word to "
        f"CREW_WORDS; if it did not, somebody has just addressed the crew as one rider." + NL
        + NL.join(sorted(set(unknown))[:12]))


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
    # Handing the pass back moved out of the crew card, because the crew card is the one
    # paired state that ALREADY had a way to do it: cooling off, removed, folded, declined and
    # no-ride-yet had no control of any kind on them. Both strings are about the reader's own
    # pass and say the same thing on every one of those cards, which is why the move is safe.
    "crew.mine.signout": {"render", "bindSignOut"},
    # The standing, the member count and what you are to the crew moved out of the
    # card's meta line and into the accordion summary that titles it, which `render`
    # builds. Same card, same reader, one line higher.
    "crew.mine.youare": {"render"},
    "crew.mine.youofficer": {"render"},
    "crew.mine.youmember": {"render"},
    # The line under the locked swatch, reused as the refusal if a hand-made request
    # tries to repaint a crew that is already on the map. Same reader, same fact, and
    # the `ERRS` table it is named in sits inside `askFor`'s span, which is why it is
    # reported there rather than where it is shown.
    "crew.mine.colourlock": {"askFor", "myCrewHTML"},
    "crew.mine.signoutq": {"bindSignOut"},
    # The dated cooldown sentence, reused as the cooldown ERROR: `crew.e.cooldown` said
    # "Still cooling off from the last one." while `cooldown_until` was in the payload all
    # along. Same reader, same fact, and the dated wording already existed.
    "crew.join.wait.p": {"errMsg"},
    # The pattern names and the "another crew flies this" clause, relabelled by `bindIdent`
    # after a colour press. `identGrids` draws the grid once with the pattern names and the
    # availability of the pair the form opened with; pressing a colour changes which pairs are
    # free, so the same buttons have to be relabelled in place. Same grid, same reader, same
    # words -- the alternative is re-rendering the form under the founder's cursor.
    "crew.pattern.": {"bindIdent"},
    "crew.new.gone": {"bindIdent"},
}

# The functions that build each card. Anything rendering a key from another card has to appear
# in SHARED above.
HOME = {
    "crew.signin.": {"signInHTML", "startPairing", "offerRetry", "qrGrid"},
    "crew.mine.": {"myCrewHTML", "bindMine", "leaveQuestion", "contributorsHTML", "errMsg"},
    "crew.join.": {"joinHTML", "bindJoin", "bindList", "askFor"},
    # `bindHelp` renders `crew.how.h` as the modal's title; `explainer` builds the body
    # it titles. One card, split across the control that opens it and the content it
    # opens, which is what a dialog is.
    "crew.how.": {"explainer", "bindHelp", "rulesSection"},
    "crew.targets.": {"targetsHTML", "ringTip", "bearing", "widest"},
    # `groupedRows` is part of the losses card, not another card: `loseHTML` hands it that
    # card's own rows and it heads each group with that card's own sentences. It is listed here
    # rather than in SHARED because this is not a reuse across cards -- it is the same card,
    # split into a second function when the flat list became a set of groups.
    "crew.lose.": {"loseHTML", "groupedRows", "ringTip"},
    "crew.decl.": {"myCrewHTML"},
    "crew.tile.": {"tileWords", "legendHTML", "fadesIn", "onCellClick", "onCellHover",
                   "ringTip", "targetsHTML"},
    "crew.legend.": {"legendHTML"},
    "crew.new.": {"createHTML", "myCrewHTML", "identGrids", "identBlock"},
    "crew.pattern.": {"createHTML", "identGrids"},
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
