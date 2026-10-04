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
PUBLIC = ROOT / "web" / "public.py"

# A declaration that takes text away rather than wrapping it.
#
# A bare `overflow: hidden` is NOT one: it is on `html`, `body`, `.panel`, `.topbar` and the
# three podium shine masks, and asking an author to justify those says nothing about words.
# Where `overflow: hidden` does cut text it is because a `nowrap` on the same element stopped
# the wrap first, and that is the declaration named here.
CLIPPING = {
    ("white-space", "nowrap"), ("white-space", "pre"),
    ("text-overflow", "ellipsis"),
}


def _inline_stylesheet():
    """The `<style>` block in public.py.

    The first version of this file read only crews.css -- and two of the three instances it
    was written for, both of them the dock, were in here.
    """
    src = PUBLIC.read_text(encoding="utf-8")
    a = src.index("<style>") + len("<style>")
    return src[a:src.index("</style>", a)]

# Every selector allowed to clip, and what was measured in the locale that needs the most
# room. Adding a row here is the review; the measurement belongs in the note.
REVIEWED = {
    ".crewfact":
        "one fact out of a join row's meta line -- `4 riders`, `leader says yes`, `137 km²`, "
        "`223 km from here`. The nowrap is here so the line breaks BETWEEN facts: without it a "
        "row read `… 78 km` / `from here` and the next began `· 222 km from here`, a "
        "separator reading as a list bullet. The container still wraps, and it wraps at the "
        "separators, so nothing is cut -- the longest single fact is uk "
        "`за 223 км відсюди` at "
        "about 120px in a 292px row at 390.",
    ".crewsep":
        "the ` · ` between two of those facts, four characters wide. It may not start or "
        "end a line, which is the whole reason it is a span.",
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
    # --- the page's own stylesheet, which this file could not see for one commit
    ".dock .lbl":
        "the nav labels. The nowrap is here so `fitDock()` measures the natural single-line "
        "width: without it CJK wraps between characters, `scrollWidth` returns min-content "
        "(427px for a Japanese dock that needs 587) and the fit test never fires. There is "
        "deliberately no ellipsis -- the words are shown whole or the row drops to icons. "
        "See test_the_dock_can_still_measure_itself.",
    ".chip":
        "the topbar statistics -- `9 Countries`, `9 Länder`. They sit in a `flex-wrap` strip "
        "of four with `flex: 1 1 0`, so a long locale wraps the strip to two rows rather than "
        "cutting; measured at 390 in all 19 with the longest (`Gesamt km`) intact.",
    ".pname":
        "a rider or crew name on the podium. User-supplied and unbounded, so the ellipsis is "
        "the deliberate answer -- and the phone block already overrides it to wrap, because "
        "truncating a crew's name in a feature about naming your crew is indefensible.",
    ".cline b":
        "a champion's display name. User-supplied, same reasoning.",
    ".crow > span":
        "a country's name beside its flag. From the host's own country table, not translated "
        "here, and the flag carries the row if it ellipsises.",
    ".celln > span":
        "a rider's name in a board cell. User-supplied.",
    ".recrider span":
        "a record holder's name. User-supplied.",
    ".brow .blab":
        "a wheel brand's name, which comes from the trip data rather than from a translation.",
    ".cck":
        "the two map checkboxes. Two or three words, and they sit on their own row with the "
        "map controls -- nothing shares the line to squeeze them.",
    ".introctl .cbtn":
        "the intro's two buttons, `flex: 1` in a two-item row, so each gets half the panel "
        "and the longest translation measured (de `Überspringen`) fits.",
    ".rfoot":
        "the vertical credits rail, which is rotated and sized by its own content.",
    "#testwm":
        "the TEST DATA watermark, which is deliberately never translated -- it is a marker "
        "for whoever is looking at a staging box.",
    ".crewsumsep":
        "the screen-reader-only separator. It is 1px by 1px on purpose and holds one "
        "character; the clipping is what hides it.",
}


def _clipping_selectors():
    out = {}
    for where, text in (("crews.css", CSS.read_text(encoding="utf-8")),
                        ("public.py", _inline_stylesheet())):
        for _order, _ctx, sel, prop, val in parse(text):
            if (prop, val.strip().lower()) in CLIPPING:
                out.setdefault(canon(sel), set()).add(f"{where} {prop}: {val.strip()}")
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


def test_the_dock_can_still_measure_itself():
    """`fitDock()` is only correct while its labels cannot wrap.

    This is the half a review list cannot carry. The guard above fires on a declaration that
    CUTS text, and the bug this pins was a DELETION: I removed `white-space: nowrap` from the
    dock labels along with an ellipsis that deserved to go, and `scrollWidth` -- which
    `fitDock()` uses to decide whether the words fit -- silently started returning min-content
    instead of the natural width. In Latin and Cyrillic those are the same number, so five
    locales measured clean; in Japanese and Chinese a line breaks between any two characters,
    so the dock reported 427px, never dropped to icons, and rendered seven vertical stacks of
    one character each from 561px to about 1340px.

    No entry in REVIEWED can fail on a line that is not there, so the assumption is asserted
    directly: the measurement means what it says only while min-content equals max-content.
    """
    inline = _inline_stylesheet()
    labels = {(prop, val.strip().lower())
              for _o, _c, sel, prop, val in parse(inline) if canon(sel) == ".dock .lbl"}
    assert ("white-space", "nowrap") in labels, (
        "`.dock .lbl` no longer sets `white-space: nowrap`, so `fitDock()`'s `scrollWidth` "
        "reads the min-content width rather than the natural one. In a script that breaks "
        "between characters that is one glyph per line, the fit test never fires, and the "
        "dock renders as a block of vertical characters at every width above 560px.")
    assert ("text-overflow", "ellipsis") not in labels, (
        "`.dock .lbl` ellipsises again. A seven-item primary nav should show its words or "
        "its icons; truncating them cut all seven in English at 700px for one round.")
