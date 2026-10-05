"""Two classes that have each been fixed three times at the place somebody pointed at.

A reviewer, after the third round of it: *"the fix has gone where the finding pointed and the
class has stayed open one step to the side; a checker that fails on the class is the thing that
ends that."* So these read the shipped JS and the shipped page rather than a list of strings.

**A count with an agreeing word beside it.** Five strings have now been found passing a bare
number into a sentence whose next word has to agree with it: `crew.targets.kills`
(`уходит {n} клеток`), `crew.targets.p0n`, `crew.tile.days`, and then `crew.board.gained`
(`1 новых на этой неделе`, on five consecutive rows of the default board) and `crew.lose.more`
(`и ещё 1 затихают`). Each round closed the ones it was handed. A call passing `n:` is fine
only when the key has no word that agrees, and that is a judgement somebody has to make and
write down -- so the ones that are fine are named here, and anything else has to go through
the plural machinery.

**An attribute whose only text is English.** `title`, `alt` and `aria-label` are the strings
that never appear on screen, so they are the ones that stay untranslated: `panel.peek` was
found in round fourteen and fixed, and `ROLEIC`'s three `title="Leader"` badges and the pairing
QR's `alt` were sitting in the same file the whole time. An attribute carrying words has to
come from the i18n hook, or be named here with the reason it cannot.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
from i18n import EN                            # noqa: E402

NL = chr(10)
JS = (ROOT / "web" / "static" / "crews.js").read_text(encoding="utf-8")
PUBLIC = (ROOT / "web" / "public.py").read_text(encoding="utf-8")
# The pairing page a scanned QR lands on lives here, and was hard-coded English in all
# nineteen locales for as long as this scan read only the two files above.
API = (ROOT / "web" / "crews_api.py").read_text(encoding="utf-8")

# Keys that may take a bare `{n}`, because nothing in them agrees with it. Each one was read
# in the locales that inflect -- Russian, Polish, Ukrainian -- before being written down.
NO_AGREEMENT = {
    "crew.targets.none": "`{n}x{n} block` -- a dimension, and the noun after it is fixed",
    "crew.mine.start": "the same dimension",
    "crew.targets.grows": "`your block goes to {n}` -- the number ends the clause",
    "crew.targets.links": "`{n} to link up` -- no noun follows it in any locale",
    "crew.targets.drops": "`drops them to {n}` -- ends the clause",
    "crew.drawn.in": "`{n} min` -- every locale abbreviates the unit, so nothing inflects",
    "crew.drawn.old": "`over {n} h ago` -- the same abbreviated unit, same reason",
    # The rate-limit wait, in the same two units and for the same reason. ru and uk first had
    # the idiomatic inversion -- `минут через {n}`, unit BEFORE the number -- which is the one
    # shape this claim cannot be made about, so they were rewritten to `через {n} мин.` to
    # match the other sixteen. ja/zh put 分/分钟 after it and neither pluralises after a
    # numeral; ko 분; tr dk/sa; the rest abbreviate.
    "crew.e.rate.in": "`{n} min` -- an abbreviated unit after the number in every locale",
    "crew.e.rate.inh": "`{n} h` -- the same",
    "crew.who.share": "`{n}%`",
    # The roster's fold. Read in all three: ru `Показать всех {n}`, pl `Pokaż wszystkich {n}`,
    # uk `Показати всіх {n}` -- the number ENDS the clause in every one, and the word
    # before it is a fixed form that does not agree with it. Turkish, Japanese and Chinese
    # put a counter after it (`kişi`, `人`) which never pluralises after a numeral.
    "crew.roles.all": "the number ends the clause; nothing after it inflects",
    "crew.rank.nth": "the ordinal template itself, which is what `ordinal()` formats",
    # the two a ternary key hid from this scan until it stopped requiring a literal
    "crew.rank.off": "`{n} off {v}` -- read in ru, uk and pl: `на {n} меньше`, `{n} do`, "
                     "`{n} hinter`. The number is followed by a preposition in every one.",
    "crew.rank.level": "`level with {v}` -- the count is not in this string at all; it is "
                       "passed alongside its sibling by one ternary call.",
    "crew.targets.p": "`{n}x{n}` again, the seed dimension",
    # Not a count at all: an index, so a screen reader has something to hold a position with
    # instead of twenty-four hex codes. "Colour 3" never becomes "Colours 3".
    "crew.new.colourn": "`Colour {n}` -- an index naming one swatch, not a quantity of them",
    # Written so that nothing can agree with it. Read in all nineteen: the number ends the
    # clause in en, de, da, no, sv, nl, it, fr, es, es-419, pt-BR, pl, ru, uk and ko; tr puts
    # a noun after it (`{n} ekibin`) and Turkish does not pluralise after a numeral; zh and
    # zh-Hant follow it with the measure word 个/個, which is invariant.
    "crew.join.all": "`Show all {n}` -- the number ends the clause, or is followed by "
                     "something that does not inflect after a numeral",
    # `{n} of {v} crews`: the noun agrees with {v}, the total, and nothing follows {n}. In
    # ja/zh/ko the counter sits on the total too (`{v}件中{n}件`, `{v} 中的 {n} 个`), and ru
    # and uk drop the noun entirely (`{n} из {v}`).
    "crew.join.count": "`{n} of {v} crews` -- the noun belongs to the total, not to {n}",
    "crew.targets.p0": "the same dimension",
}

# A formatted count, rather than a bare one: these produce the agreeing noun themselves.
FORMATTERS = ("tiles(", "riders(", "days(", "plural(", "fmtKm", "heldFor(", "effort(",
              "fadesIn(", "ordinal(", "daysUntil(")

# The whole first argument, not just a literal key: `t(cond ? "a" : "b", {...})` is an
# ordinary call and sixteen of them were invisible to a pattern that required a quoted string.
CALL = re.compile(r"""t\(([^(){}]*?),\s*\{([^{}]*)\}""")
KEY = re.compile(r"""["'](crew\.[a-zA-Z0-9_.]+)["']""")


def test_a_count_only_goes_in_bare_where_nothing_agrees_with_it():
    # `plural()` is where a bare count is correct, by construction
    a = JS.index("  function plural(one, few, many, n) {")
    b = JS.index(NL + "  }", a)

    loose = []
    for m in CALL.finditer(JS):
        if a <= m.start() <= b:
            continue
        keys, args = KEY.findall(m.group(1)), m.group(2)
        if not keys:
            continue                       # not a crews string
        n_arg = re.search(r"\bn\s*:\s*([^,}]+)", args)
        if not n_arg:
            continue
        value = n_arg.group(1).strip()
        if any(f in value for f in FORMATTERS):
            continue                       # already a formatted phrase
        line = JS[:m.start()].count(NL) + 1
        for key in keys:                   # a ternary offers two, and both get the count
            if key not in NO_AGREEMENT:
                loose.append(f"  crews.js:{line}  {key}  n: {value}")

    assert not loose, (
        f"{len(loose)} call(s) pass a bare count into a string whose next word may have to "
        f"agree with it. Read the key in Russian, Polish and Ukrainian: if something agrees, "
        f"take the count from the plural machinery (`tiles()` and friends); if nothing does, "
        f"add the key to NO_AGREEMENT with the reason." + NL + NL.join(loose))


def test_the_agreement_free_list_describes_keys_that_exist():
    gone = sorted(k for k in NO_AGREEMENT if k not in EN)
    assert not gone, "NO_AGREEMENT names keys that no longer exist: " + repr(gone)


# Attributes whose text cannot come from the i18n tables, with the reason.
RAW_ATTRS = {
    "eucstats on GitHub": "a product name and a service name; neither is translated anywhere",
    "HTML last-modified date (auto-updated on deploy)":
        "a build stamp for whoever is debugging a deploy, not rider-facing copy",
}
ATTR = re.compile(r"""(title|alt|aria-label)\s*=\s*(["'])([^"']*)\2""")
WORDS = re.compile(r"[A-Za-z]{3,}")
HOOK = {"title": "data-i18n-title", "aria-label": "data-i18n-aria", "alt": "data-i18n-alt"}


def test_no_attribute_carries_english_that_the_tables_cannot_reach():
    bare = []
    for name, src in (("crews.js", JS), ("public.py", PUBLIC), ("crews_api.py", API)):
        for m in ATTR.finditer(src):
            attr, value = m.group(1), m.group(3)
            if not value.strip() or not WORDS.search(value):
                continue
            if '" + ' in value or "esc(" in value:
                continue                   # interpolated, so it came from somewhere else
            if value in RAW_ATTRS:
                continue
            window = src[max(0, m.start() - 240):m.end() + 80]
            if HOOK[attr] in window:
                continue
            line = src[:m.start()].count(NL) + 1
            bare.append(f"  {name}:{line}  {attr}={value!r}")

    assert not bare, (
        f"{len(bare)} attribute(s) carry English with no way for a translation to reach them. "
        f"These are the strings that never appear on screen, which is exactly why they stay "
        f"English: give them a `data-i18n-*` hook and a key, or name them in RAW_ATTRS with "
        f"the reason they cannot have one." + NL + NL.join(bare))


def test_no_i18n_hook_is_written_where_nothing_will_read_it():
    """A `data-i18n-*` attribute inside a script is a promise `applyI18n()` cannot keep.

    `applyI18n()` walks the served document with `querySelectorAll` at boot and on a language
    change. A hook written inside a JS string literal is interpolated into panel HTML long
    after that pass has run, so the node is never visited -- which is how the anonymous-country
    globe carried `data-i18n-aria="flag.hidden"`, shipped eighteen translations, and went on
    saying "Country hidden" in every one of them. The attribute test above cannot catch it,
    because a static check cannot tell whether the pass ever reaches the node.

    Markup that is built at render time names itself at render time: call `t()` there, the way
    `roleMark()` and `globeSvg()` do.
    """
    inside = []
    for name, src in (("public.py", PUBLIC), ("crews_api.py", API)):
        for m in re.finditer(r"<script[^>]*>", src):
            end = src.find("</script>", m.end())
            body = src[m.end():end if end > 0 else len(src)]
            for h in re.finditer(r"data-i18n(?:-title|-aria|-alt)?\s*=", body):
                line = src[:m.end() + h.start()].count(NL) + 1
                inside.append(f"  {name}:{line}  {body[h.start():h.start() + 40].strip()!r}")
    assert not inside, (
        f"{len(inside)} i18n hook(s) are written inside a script, where `applyI18n()`'s pass "
        f"over the served document will never reach them. Build the markup with a `t()` call "
        f"instead:" + NL + NL.join(inside))
