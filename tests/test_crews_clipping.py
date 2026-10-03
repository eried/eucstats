"""Every rule that can cut text off has to have been looked at in more than one language.

Three rounds running, the same bug: a declaration that clips text, added after measuring
English, cutting the locales whose words are longer.

  * `.crewgain { white-space: nowrap }` -- the podium cards are `overflow: hidden`, so it did
    not overflow, it CUT. Eleven of eighteen locales lost their last word; Russian lost
    "неделе", German cut mid-word. English is 72px in a 78px box, which is why it passed.
  * `.dock .lbl { text-overflow: ellipsis }` -- added so the Turkish dock would stop running
    off a 760px screen. Result: all seven labels truncated in ENGLISH at 700px, the active tab
    reading "Cre…", and sixteen of nineteen locales affected somewhere in 561-812px.
  * `.crewpendr > span { white-space: nowrap }` -- added so a role mark would not wrap under
    the name. It made the NAME the thing that shortens, and the buttons beside it are
    `flex: 0 0 auto`: in German the name gets 45px of a 292px row, so three roster rows read
    `S14·Fj…`, `SIM·V…`, `S14·K…` -- two of them indistinguishable, beside a button that
    removes somebody from the crew.

All three were caught by a reviewer with a browser, and all three were introduced by me adding
one declaration. **This checks the moment of introduction, which is the moment the author can
still answer the question.** It is not a measurement: measuring would mean laying out the panel
in nineteen languages at several widths, which needs a real browser, and there is none in this
repo's dev environment. What it does instead is refuse to let a text-cutting declaration into
the stylesheet silently -- each one has to be named here, with what happens to the longest
locale written beside it.

So the honest statement of what this catches and what it does not: it catches a NEW clipping
rule nobody has thought about. It does not catch an existing one becoming wrong because the
text around it grew. For that, the note beside each entry is the record of what was measured
and when.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from check_crews_css import CSS, canon, parse        # noqa: E402

NL = chr(10)

# A declaration that can take text away rather than wrap it.
CLIPPING = {
    ("white-space", "nowrap"), ("white-space", "pre"),
    ("text-overflow", "ellipsis"),
    ("overflow", "hidden"), ("overflow-x", "hidden"), ("overflow-y", "hidden"),
}

# Every selector allowed to clip, and what was measured in the locale that needs the most
# room. Adding a row here is the review; the measurement belongs in the note.
REVIEWED = {
    ".crewboard table .val":
        "a figure and its unit -- `13 squares`, `91 клетка`. Measured at 390 in all 19: the "
        "column is sized from its widest row, and the longest (uk `клітинок`) fits.",
    ".crewrank .val":
        "same column in the plain-table fallback, same measurement.",
    ".crewcname":
        "a rider's display name on the contributors list, which is user-supplied and has no "
        "longest form to measure. Ellipsis is the deliberate answer: the avatar and the "
        "distance beside it carry the row, and the full name is in the title attribute.",
    ".crewrown b":
        "a crew's name in the join list, same reasoning -- user-supplied, unbounded.",
    ".crewpendr > span:first-child":
        "a rider's name on the roster. It wraps to its own full-width line below 560px "
        "(see the phone block) so the ellipsis only applies where there is room; above that "
        "width German's `Zum Offizier machen` + `Entfernen` still leave 99px and the name "
        "fits. This clipped the name to 45px on a phone for one round.",
    ".crewpendr > span .crewrole":
        "the one-character role mark, which is what the nowrap was ever for. It was on the "
        "whole span and took the name with it.",
    ".crewemb-n":
        "the two-letter emblem monogram. Two characters, no locale.",
    ".crewtkm i":
        "a distance, `1.4 km`. No locale translates the unit and the number is bounded.",
    ".crewpop-in span i":
        "a distance in the map popup, same.",
    ".crewtip span i":
        "a distance in the hover tip, same.",
    ".crewsumsep":
        "the screen-reader-only separator. It is 1px by 1px on purpose and holds one "
        "character; the clipping is what hides it.",
}


def _clipping_selectors():
    out = {}
    for _order, _ctx, sel, prop, val in parse(CSS.read_text(encoding="utf-8")):
        if (prop, val.strip().lower()) in CLIPPING:
            out.setdefault(canon(sel), set()).add(f"{prop}: {val.strip()}")
    return out


def test_every_rule_that_can_cut_text_has_been_read_in_more_than_one_language():
    found = _clipping_selectors()
    unreviewed = sorted(set(found) - set(REVIEWED))
    assert not unreviewed, (
        f"{len(unreviewed)} selectors can cut text off and are not in REVIEWED. Open the "
        f"panel in the locale with the longest words for that string -- German, Turkish and "
        f"Ukrainian are usually the ones -- and either write what you measured beside the "
        f"selector, or let the text wrap instead:" + NL
        + NL.join(f"  {sel}  ({', '.join(sorted(found[sel]))})" for sel in unreviewed))


def test_the_review_list_has_no_entries_for_rules_that_are_gone():
    """A note about a rule that no longer exists is a note nobody will ever check."""
    found = _clipping_selectors()
    stale = sorted(set(REVIEWED) - set(found))
    assert not stale, (
        "REVIEWED names selectors the stylesheet no longer clips; delete them so the list "
        "stays a description of the file rather than of its history:" + NL
        + NL.join("  " + sel for sel in stale))
