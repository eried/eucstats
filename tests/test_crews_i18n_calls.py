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

# Keys that may take a bare `{n}`, because nothing in them agrees with it. Each one was read
# in the locales that inflect -- Russian, Polish, Ukrainian -- before being written down.
NO_AGREEMENT = {
    "crew.targets.none": "`{n}x{n} block` -- a dimension, and the noun after it is fixed",
    "crew.mine.start": "the same dimension",
    "crew.targets.grows": "`your block goes to {n}` -- the number ends the clause",
    "crew.targets.links": "`{n} to link up` -- no noun follows it in any locale",
    "crew.targets.drops": "`drops them to {n}` -- ends the clause",
    "crew.drawn.in": "`{n} min` -- every locale abbreviates the unit, so nothing inflects",
    "crew.who.share": "`{n}%`",
    "crew.rank.nth": "the ordinal template itself, which is what `ordinal()` formats",
}

# A formatted count, rather than a bare one: these produce the agreeing noun themselves.
FORMATTERS = ("tiles(", "riders(", "days(", "plural(", "fmtKm", "heldFor(", "effort(",
              "fadesIn(", "ordinal(", "daysUntil(")

CALL = re.compile(r"""t\(\s*["'](crew\.[a-zA-Z0-9_.]+)["']\s*,\s*\{([^{}]*)\}""")


def test_a_count_only_goes_in_bare_where_nothing_agrees_with_it():
    # `plural()` is where a bare count is correct, by construction
    a = JS.index("  function plural(one, few, many, n) {")
    b = JS.index(NL + "  }", a)

    loose = []
    for m in CALL.finditer(JS):
        if a <= m.start() <= b:
            continue
        key, args = m.group(1), m.group(2)
        n_arg = re.search(r"\bn\s*:\s*([^,}]+)", args)
        if not n_arg:
            continue
        value = n_arg.group(1).strip()
        if any(f in value for f in FORMATTERS):
            continue                       # already a formatted phrase
        if key in NO_AGREEMENT:
            continue
        line = JS[:m.start()].count(NL) + 1
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
    for name, src in (("crews.js", JS), ("public.py", PUBLIC)):
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
