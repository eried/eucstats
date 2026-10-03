"""The stylesheet may not contradict itself, and the checker has to be able to tell.

A later rule beating an earlier one at the same specificity, from a context that does not
narrow it, is how this feature shipped a phone board whose week column was hidden and shown by
two rules in one block, and a two-line phone row that was dead code because a base rule sixty
lines further down overrode it. Neither was visible in review; both were visible to a script.

The rest of this file exists because reviewers twice proved the script blind to the exact bug
it had been written for. The first time it keyed on the literal selector string, so `.crewtag`
and `.crewtag.kills` were never compared. The second time they got past it four separate ways
in one sitting -- and made it fail two stylesheets that were perfectly correct -- while the
shipped file contained a live instance of the class it guards: a phone rule relaxing
`.crewtwho` while `white-space: nowrap; overflow: hidden` sat on `.crewtwho i`, so the card
painted 62px outside its own border and the script reported no problems.

A checker that passes a file containing the bug it was written for is worse than no checker,
because it gets quoted as evidence. So every bypass and every false alarm is pinned here.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_crews_css.py"
CSS = ROOT / "web" / "static" / "crews.css"

# The rule that makes every coloured chip lose its box on a phone. Removing it reintroduces
# the round-six bug exactly.
PHONE_STRIP = """  .crewtag.first, .crewtag.joins, .crewtag.kills, .crewtag.drops,
  .crewtag.done, .crewtag.youpass { background: none; padding: 0; }"""

# The shipped fix for the state/pseudo case, quoted so a case can put the bug back.
PICKED = (".crewpickc:not(.on):hover, .crewpickp:not(.on):hover {" + chr(10)
          + "  outline: 1px solid rgba(255,255,255,.6); outline-offset: 1px;" + chr(10) + "}")

TRUNCATE = (".crewtwho i { display: block; font-style: normal; font-size: 11px;"
            " line-height: 1.25; }")
KILLS = ".crewtag.kills { color: #ff4fa3; background: rgba(255,79,163,.16); }"
NL = chr(10)
BS = chr(92)        # CSS identifier escapes, which several cases turn on

# All six, because rewriting one is how the attribute-selector case passed for the wrong
# reason: the checker was exiting 1 on the five chips the case had not touched, and its output
# never mentioned the one it had.
CHIPS = ("first", "joins", "kills", "drops", "done", "youpass")


def _chips_as(css, shape):
    """The six chip rules rewritten into another way of saying `.crewtag.<chip>`."""
    out = css
    for chip in CHIPS:
        before = ".crewtag." + chip + " {"
        after = shape(chip) + " {"
        assert before in out, before
        out = out.replace(before, after, 1)
    return out


def _run(checker):
    return subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)


def _point_at(tmp_path, css_text):
    """A copy of the checker aimed at a copy of the stylesheet."""
    broken = tmp_path / "crews.css"
    broken.write_text(css_text, encoding="utf-8")
    src, n = re.subn(r"^CSS = pathlib\.Path\(__file__\).*$",
                     "CSS = pathlib.Path(r" + repr(str(broken)) + ")",
                     CHECKER.read_text(encoding="utf-8"), count=1, flags=re.M)
    assert n == 1, "could not point the checker at a copy; its CSS path line changed"
    probe = tmp_path / "check.py"
    probe.write_text(src, encoding="utf-8")
    return probe


def test_no_css_rule_silently_cancels_an_earlier_one():
    r = _run(CHECKER)
    assert r.returncode == 0, r.stdout + r.stderr


def _cases():
    """(name, mutated stylesheet, expected exit code)."""
    css = CSS.read_text(encoding="utf-8")
    second_block = (NL + "@media (max-width: 560px), (hover: none) and (pointer: coarse) {" + NL
                    + "  .crewtat, .crewtwho i { display: block; }" + NL + "}" + NL)
    truncating = (".crewtwho i { display: block; font-style: normal; font-size: 11px;" + NL
                  + "  line-height: 1.25; white-space: nowrap; overflow: hidden;" + NL
                  + "  text-overflow: ellipsis; }")
    return [
        # --- four ways a reviewer got past it. Each must now exit 1.
        ("the phone chip-strip deleted",
         css.replace(PHONE_STRIP, "", 1), 1),
        ("the strip written as a longhand against a shorthand",
         css.replace(PHONE_STRIP, "  .crewtag { background-color: transparent; }", 1), 1),
        ("the chips painted through an attribute selector",
         css.replace(KILLS, KILLS.replace(".crewtag.kills", ".crewtag[data-fam=kills]"), 1)
            .replace(PHONE_STRIP, "", 1), 1),
        ("a second phone block worded differently",
         css + second_block, 1),
        # Both halves of the defect that shipped: a child that truncates, and a phone rule
        # relaxing the PARENT in the belief that it reaches the child. Injected together,
        # because the stylesheet no longer contains either half on its own.
        ("a parent trying to relax a child's overflow",
         css.replace(TRUNCATE, truncating, 1)
            + NL + "@media (max-width: 560px) {" + NL
            + "  .crewtwho { white-space: normal; overflow: visible; text-overflow: clip; }"
            + NL + "}" + NL, 1),
        # --- four more a reviewer got past it with, in the third round running
        ("a media query differing by one space",
         css + NL + "@media (max-width:560px) { .crewtat { display: block; } }" + NL, 1),
        ("one ancestor step on the narrow selector",
         css.replace(TRUNCATE,
                     ".crewcard .crewtwho i { display: block; white-space: nowrap;" + NL
                     + "  overflow: hidden; text-overflow: ellipsis; }", 1)
            + NL + "@media (max-width: 560px) {" + NL
            + "  .crewtwho { white-space: normal; overflow: visible; }" + NL + "}" + NL, 1),
        ("a logical property against its physical twin",
         css + NL + ".zzrow.wide { padding-left: 9px; }" + NL
             + "@media (max-width: 560px) { .zzrow { padding-inline-start: 0; } }" + NL, 1),
        (":is() hiding a selector from the parser",
         css + NL + ".zztag.hot { background: #f00; }" + NL
             + "@media (max-width: 560px) { :is(.zztag, .zzother) { background: none; } }" + NL,
         1),
        # --- round ten: one extra space, a swapped class order, and CSS nesting
        ("the same selector written with two spaces",
         css + NL + "@media (max-width: 560px) { .crewtrow .crewtag { display: inline; } }"
             + NL + ".crewtrow  .crewtag { display: block; }" + NL, 1),
        ("the same compound with its classes swapped",
         css + NL + "@media (max-width: 560px) { .crewtag.kills { display: inline; } }"
             + NL + ".kills.crewtag { display: block; }" + NL, 1),
        # The nested forms have to be the genuine equivalents of the flat bugs: a phone rule
        # that is LESS specific than the rule it is trying to beat. Nesting `&.wide` instead
        # makes it equally specific and later, which wins, and is correct CSS the checker
        # should stay quiet about.
        ("a logical property under a nested block",
         css + NL + ".zzrowN.wide { padding-left: 9px; }" + NL
             + "@media (max-width: 560px) { .zzrowN { padding-inline-start: 0;"
             + " &.other { color: red; } } }" + NL, 1),
        ("an :is() rule under a nested block",
         css + NL + ".crewtrow .zztagW.hot { background: #f00; }" + NL
             + "@media (max-width: 560px) { .crewtrow { :is(.zztagW, .zzother)"
             + " { background: none; } } }" + NL, 1),
        # --- round twelve: both reviewers found these in one round
        # the nested block comes FIRST and a later top-level rule beats it everywhere --
        # the other order is a phone rule legitimately refining a base rule, which is correct
        # CSS and the checker should stay quiet about it
        ("a nested at-rule, which parsed as part of the selector",
         css + NL
             + ".crewzzA { color: red; @media (max-width: 560px) { display: inline; } }" + NL
             + ".crewzzA { display: block; }" + NL, 1),
        ("a property name with one capital letter",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { .crewtag { BACKGROUND: none; } }" + NL, 1),
        ("a statement at-rule that was pushed and never popped",
         '@charset "utf-8";' + NL + css.replace(PHONE_STRIP, "", 1), 1),
        # --- round thirteen. Three reviewers, nine ways past and four correct files failed.
        # The decisive shape: ALL SIX chips, not one. With one rewritten the checker was
        # exiting 1 on the other five and the case passed without ever mentioning the chip it
        # had changed.
        ("all six chips painted through attribute selectors",
         _chips_as(css, lambda c: ".crewtag[data-fam=" + c + "]")
            .replace(PHONE_STRIP, "", 1), 1),
        ("all six chips painted through :not()",
         _chips_as(css, lambda c: ".crewtag:not(.no" + c + ")")
            .replace(PHONE_STRIP, "", 1), 1),
        # A rule inside a nested at-rule: the branch read the at-rule's body as declarations
        # and never recursed, so this parsed as a property literally named `.zzn { background`.
        ("a rule nested inside a nested at-rule",
         css + NL + ".crewtrow .zzn.hot { background: #f00; }" + NL
             + ".crewtrow { @media (max-width: 560px) { .zzn { background: none; } } }" + NL, 1),
        ("an & rule nested inside a nested at-rule",
         css + NL + ".zzb.hot { background: #f00; }" + NL
             + ".zzb { @media (max-width: 560px) { & { background: none; } } }" + NL, 1),
        # One nested paren defeated the :is() regex, which reopened a hole closed in round nine.
        (":is() with a paren inside it",
         css + NL + ".zztagZ.hot { background: #f00; }" + NL
             + "@media (max-width: 560px) { :is(.zztagZ, .zzo:not(.x))"
             + " { background: none; } }" + NL, 1),
        # A brace in a string ran the block matcher past the rule's real closing brace, and
        # the declaration count went UP, so the counter gave no warning either.
        ('a brace inside a quoted value',
         css.replace(PHONE_STRIP, "", 1) + NL
             + '.zzq::before { content: "{"; }' + NL
             + "@media (max-width: 560px) { .crewtag { background: none; } }" + NL, 1),
        # A comment opener in a string fed the comment stripper, which was one regex over the
        # whole file, so everything to the next `*/` disappeared.
        ('a comment opener inside a quoted value',
         css.replace(PHONE_STRIP, "", 1) + NL
             + '.zzc::before { content: "/*"; }' + NL
             + "@media (max-width: 560px) { .crewtag { background: none; } }" + NL
             + '.zzc::after { content: "*/"; }' + NL, 1),
        # The `covered` hatch matched a string suffix, so one unrelated plausible rule whose
        # last compound ends in `.kills` silenced all six real reports.
        ("an unrelated rule whose last compound looks like the narrow one",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { .zlegend .first, .zlegend .joins," + NL
             + "  .zlegend .kills, .zlegend .drops, .zlegend .done, .zlegend .youpass" + NL
             + "  { background: #123; padding: 1px; } }" + NL, 1),
        # --- round thirteen. Two reviewers, six more ways past and two correct files
        # reported. Four of the six are one assumption: CSS is case-insensitive about
        # at-rule names, pseudo-class names and `!important`, and round twelve lowercased
        # property names and stopped there.
        ("an at-rule name in another case",
         css + NL + "@Media (max-width: 560px) { .crewtat { display: block; } }" + NL, 1),
        ("a pseudo-class name in another case",
         css + NL + ".zztagU.hot { background: #f00; }" + NL
             + "@media (max-width: 560px) { :IS(.zztagU, .zzother)"
             + " { background: none; } }" + NL, 1),
        # the round-six bug with a tablet breakpoint instead of top level, which is the most
        # likely next thing to be added to this stylesheet
        ("a wider media query beating a narrower one",
         css + NL + "@media (max-width: 560px) { .zzw { display: inline; } }" + NL
             + "@media (max-width: 1200px) { .zzw { display: block; } }" + NL, 1),
        # baseline in every browser since 2023, and invisible while conditions were strings
        ("a media query written in range syntax",
         css + NL + "@media (width <= 560px) { .crewtat { display: block; } }" + NL, 1),
        # a backslash before a non-hex character is that character: same class as the
        # attribute-selector and `:not()` cases above, one keystroke away
        ("all six chips written with identifier escapes",
         _chips_as(css, lambda c: ".crewtag." + c[0] + chr(92) + c[1:])
            .replace(PHONE_STRIP, "", 1), 1),
        # Both escape hatches in pass 2 asked "is there some rule nearby?" and never "does it
        # reach the elements that are winning?", so ONE planted rule silenced all six reports.
        ("a planted !important that reaches nothing",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { .zzfoot { background: #000 !important; } }"
             + NL, 1),
        ("a planted rule narrower than the broad one but reaching no chip",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { .crewtag.zzcompact { background: #123; } }"
             + NL, 1),
        # `"!important" in value` is a substring test, and a filename is a string
        ("!important inside a url()",
         css.replace(PHONE_STRIP, "", 1) + NL
             + '@media (max-width: 560px) { .zzlogo { background-image:'
             + ' url("hero!important.png"); } }' + NL, 1),
        # --- round fourteen. Two reviewers, ten more ways past.
        # The media TYPE was left in place, so the commonest spelling of a phone query
        # normalised to the literal `screenand(max-width:560px)` and related to nothing.
        ("the media type kept in the query",
         css + NL + "@media screen and (max-width: 560px)"
             + " { .crewtat { display: block; } }" + NL, 1),
        ("only screen",
         css + NL + "@media only screen and (max-width: 560px)"
             + " { .crewtat { display: block; } }" + NL, 1),
        # the other two spellings of the range syntax, same feature, same 2023 baseline
        ("a range with its operands reversed",
         css + NL + "@media (560px >= width) { .crewtat { display: block; } }" + NL, 1),
        ("a range written as an interval",
         css + NL + "@media (0px <= width <= 560px) { .crewtat { display: block; } }" + NL, 1),
        # a rem breakpoint is the same 560px, and is the most likely next thing added here
        ("a breakpoint in rem",
         css + NL + "@media (max-width: 35rem) { .crewtat { display: block; } }" + NL, 1),
        ("a length carrying a sign",
         css + NL + "@media (max-width: +560px) { .crewtat { display: block; } }" + NL, 1),
        # One extra ancestor satisfies BOTH halves of `fixes()` on its own, so a single
        # planted rule silenced all six chip reports again. A real fix names ancestors the
        # stylesheet already uses; `.zzfoo` appears in its own rule and nowhere else.
        ("a planted ancestor that exists nowhere else",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { .zzfoo .crewtag"
             + " { background: none; padding: 0; } }" + NL, 1),
        ("an id ancestor that exists nowhere else",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { #zznothing .crewtag"
             + " { background: none; padding: 0; } }" + NL, 1),
        # a chip inside a chip, which no DOM produces
        ("an ancestor that is the subject itself",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { .crewtag .crewtag"
             + " { background: none; padding: 0; } }" + NL, 1),
        # An unlayered declaration beats a layered one whatever its specificity, so a fix
        # written inside `@layer` does not win -- and the checker took it as the fix.
        ("a correct-looking fix wrapped in @layer",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@layer zz { @media (max-width: 560px) { .crewtrow .crewtag"
             + " { background: none; padding: 0; } } }" + NL, 1),
        # --- round fourteen again: `unescape` lived inside `canon`, so the comparison saw
        # resolved text and `specificity`, `_find_is` and the priority test did not.
        ("a hex escape that halves the specificity",
         _chips_as(css, lambda c: ".crewtag." + BS + "%06x" % ord(c[0]) + c[1:])
            .replace(PHONE_STRIP, "", 1), 1),
        # ONE class whose name contains a space -- which no element can carry, so it matches
        # nothing -- resolved to a real space and read as the descendant selector that
        # `fixes()` accepts
        ("an escaped space read as a combinator",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 560px) { .crewtrow" + BS + " .crewtag"
             + " { background: #123; } }" + NL, 1),
        # `PHYSICAL` knew these pairs and `family()` never asked it
        ("a logical inset against its physical twin",
         css + NL + ".zzp.wide { left: 9px; }" + NL
             + "@media (max-width: 560px) { .zzp { inset-inline-start: 0; } }" + NL, 1),
        ("a logical border against its physical twin",
         css + NL + ".zzb2.wide { border-left: 2px solid #f00; }" + NL
             + "@media (max-width: 560px) { .zzb2 { border-inline-start: 0; } }" + NL, 1),
        (":is() spelled with an escape",
         css + NL + ".zztagI.hot { background: #f00; }" + NL
             + "@media (max-width: 560px) { :i" + BS + "s(.zztagI, .zzother)"
             + " { background: none; } }" + NL, 1),
        # --- round fifteen. `:where()` contributes ZERO specificity, and `expand()` wrote it
        # out so `specificity()` scored its contents. All three of these look like a fix and
        # none of them can win.
        ("the chip strip rewritten with :where()",
         css.replace(PHONE_STRIP,
                     "  .crewtag:where(.first, .joins, .kills, .drops, .done, .youpass)"
                     " { background: none; padding: 0; }", 1), 1),
        ("a descendant fix with :where() on the subject",
         css.replace(PHONE_STRIP, "  .crewtrow :where(.crewtag)"
                     " { background: none; padding: 0; }", 1), 1),
        ("a descendant fix with :where() on the ancestor",
         css.replace(PHONE_STRIP, "  :where(.crewtrow) .crewtag"
                     " { background: none; padding: 0; }", 1), 1),
        # the round-six bug spelled with a longhand: pass 1 keyed on the literal property, so
        # `background` and `background-color` were two keys and never met
        ("a longhand cancelling a shorthand from top level",
         css + NL + ".crewtag { background-color: rgba(255,79,163,.16); }" + NL, 1),
        # --- round sixteen
        # The OVERRIDDEN branch read the raw context while `clauses()` already knew that
        # `screen`, `all` and `only screen` normalise away -- my own round-14 fix, not asked
        # a second time, so a `@media screen {}` wrapper silenced every case in it.
        ("@media screen around the winning rule",
         css + NL + "@media screen { .crewtag { background-color: rgba(255,79,163,.16); } }"
             + NL, 1),
        ("@media all around the winning rule",
         css + NL + "@media all { .crewtat { display: block; } }" + NL, 1),
        # Position is irrelevant to the cascade when the broad rule is conditional and the
        # narrow one is not -- and a seventh chip family is the likeliest edit this file will
        # ever see.
        ("a seventh chip family appended at the end",
         css + NL + ".crewtag.late { color: #b48cff; background: rgba(180,140,255,.14); }"
             + NL, 1),
        # a fix that only applies below 380px does not answer a rule that applies below 560
        ("a fix in a context narrower than the rule it answers",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@media (max-width: 380px) { .crewtag.first, .crewtag.joins, .crewtag.kills,"
             + " .crewtag.drops, .crewtag.done, .crewtag.youpass"
             + " { background: none; padding: 0; } }" + NL, 1),
        # pass 1 compared adjacent rows, so one same-value line hid the pair that mattered
        ("a same-value line standing between a rule and its cancellation",
         css + NL + ".crewtag { background-image: none; }" + NL
             + ".crewtag { background-color: rgba(255,79,163,.16); }" + NL, 1),
        # `RELAXERS` was a whitelist, and this is the shipped ellipsis bug in another spelling
        ("a parent relaxing a child with pre-wrap rather than normal",
         css.replace(TRUNCATE,
                     ".crewtwho i { display: block; font-style: normal; font-size: 11px;" + NL
                     + "  line-height: 1.25; white-space: nowrap; overflow: hidden;" + NL
                     + "  text-overflow: ellipsis; }", 1)
            + NL + "@media (max-width: 560px) {" + NL
            + "  .crewtwho { white-space: pre-wrap; }" + NL + "}" + NL, 1),
        # --- two correct stylesheets it used to fail. Each must exit 0.
        ("!important, which genuinely wins",
         css.replace(PHONE_STRIP,
                     "  .crewtag { background: none !important; padding: 0 !important; }", 1), 0),
        ("a fix written as a descendant selector",
         css.replace(PHONE_STRIP, "  .crewtrow .crewtag { background: none; padding: 0; }", 1), 0),
        # --- and four more correct stylesheets round twelve failed
        # REVERSED in round fifteen, deliberately. This was pinned as must-exit-0 in round
        # twelve, when a reviewer reported a correct fix wrapped in `@supports (display:
        # flex)` being flagged five times. In round fifteen the same reviewer silenced every
        # report with `@supports (-webkit-touch-callout: none)` -- the standard iOS-only hack,
        # which is false in Chrome -- and the bug was live behind it.
        #
        # Both complaints are fair and they cannot both be satisfied: nothing here can know
        # which feature queries hold. A fix that applies only when one passes is not a fix the
        # stylesheet can rely on, which is how `@layer` is already treated, so it is loud now.
        ("a fix wrapped in @supports, which may not hold",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@supports (display: flex) { @media (max-width: 560px) {" + NL
             + "  .crewtrow .crewtag { background: none; padding: 0; } } }" + NL, 1),
        ("a fix behind the iOS-only feature query",
         css.replace(PHONE_STRIP, "", 1) + NL
             + "@supports (-webkit-touch-callout: none) { @media (max-width: 560px) {" + NL
             + "  .crewtag.kills { background: none; padding: 0; } } }" + NL, 1),
        # The same fix written with nesting, which the un-recursed branch swallowed whole and
        # then reported five times.
        ("a correct fix written as a nested rule",
         css.replace(PHONE_STRIP, "", 1) + NL
             + ".crewtrow { @media (max-width: 560px) {" + NL
             + "  .crewtag { background: none; padding: 0; } } }" + NL, 0),
        # An earlier !important genuinely wins, and pass 1 was not reading the priority it
        # strips for its own equality test.
        ("an earlier !important beaten by a later plain rule",
         css + NL + "@media (max-width: 560px) { .zzimp { display: inline !important; } }" + NL
             + ".zzimp { display: block; }" + NL, 0),
        # A string holding a comment opener, on a file with nothing wrong in it.
        ("a comment opener in a string, on a correct file",
         css + NL + '.zzok::before { content: "/*"; }' + NL
             + '.zzok::after { content: "*/"; }' + NL, 0),
        # `!important` is case-insensitive and may carry a space. Both of these genuinely win,
        # and reporting them is the cry-wolf half of this file's own thesis.
        ("!important spelled in another case",
         css.replace(PHONE_STRIP,
                     "  .crewtag { background: none !IMPORTANT; padding: 0 !IMPORTANT; }",
                     1), 0),
        ("!important with a space after the bang",
         css.replace(PHONE_STRIP,
                     "  .crewtag { background: none ! important;"
                     " padding: 0 ! important; }", 1), 0),
        # The reverse of the :where() cases above: written this way the six chip rules drop
        # to (0,1,0), so the later phone rule wins and the stylesheet is CORRECT. Scoring
        # `:where()` as if it carried its contents reported five problems against it.
        ("the chips themselves written with :where()",
         _chips_as(css, lambda c: ".crewtag:where(." + c + ")"), 0),
        # The four shapes that would cry wolf if the position guard were simply dropped.
        # Each is a narrow rule that legitimately refines a broad one, and each must stay
        # silent: a state is a condition, not a member of a family.
        ("a hover state on a chip",
         css + NL + ".crewtag:hover { background: rgba(255,255,255,.06); }" + NL, 0),
        ("a focus-within state on a row",
         css + NL + ".crewtrow:focus-within { background: rgba(255,255,255,.04); }" + NL, 0),
        ("a selected variant of a row",
         css + NL + ".crewtrow.sel { background: rgba(255,138,216,.08); }" + NL, 0),
        ("a modifier class on a card",
         css + NL + ".crewcard.tight { padding: 8px 10px; }" + NL, 0),
        # --- round seventeen: a transient pseudo-class erasing a state nothing puts back
        # Both rules are 0-2-0 and neither selects a subset of the other, so the later one
        # wins and a selected swatch under the pointer looks unselected. `subset_pair()`
        # models extra CLASSES, which is why five rounds of hardening never saw it.
        ("hover erasing the ring that says which swatch is chosen",
         css.replace(PICKED, ".crewpickc:hover, .crewpickp:hover"
                             " { outline: 1px solid rgba(255,255,255,.6); }", 1), 1),
        # Planted names, because `.crewtrow:hover` and `.crewpickc.on` are real rules in this
        # file: my first draft of these appended its own version of them, self-cancelled the
        # shipped one, and two cases failed on a report that had nothing to do with what they
        # were testing while a third PASSED on that report rather than on the STATE LOST it
        # was written for.
        ("hover erasing a selected row and nothing putting it back",
         css + NL + ".zzrow.sel { background: rgba(255,138,216,.3); }" + NL
             + ".zzrow:hover { background: rgba(255,255,255,.04); }" + NL, 1),
        # The four shapes that make this pass narrow rather than loud. Each must stay silent,
        # and each for its own reason.
        # 1. the state IS put back, which is the other correct fix
        ("a hover rule with the selected case written out beside it",
         css + NL + ".zzrow.sel { background: rgba(255,138,216,.3); }" + NL
             + ".zzrow:hover { background: rgba(255,255,255,.04); }" + NL
             + ".zzrow.sel:hover { background: rgba(255,138,216,.38); }" + NL, 0),
        # 2. `.gold1` is a fact about the row, not a state somebody selected. A hover tint
        #    over a podium plate is ordinary CSS and reporting it is how this script gets
        #    narrowed until it catches nothing.
        ("a hover tint over a podium plate",
         css + NL + ".zzpod.gold1 { background: rgba(104,84,30,.96); }" + NL
             + ".zzpod:hover { background: rgba(255,255,255,.06); }" + NL, 0),
        # 3. two different grids: the pointer on a pattern cell cannot erase a colour cell
        ("a hover on one grid and a state on the other",
         css + NL + ".zzgrida.on { outline: 3px solid #fff; }" + NL
             + ".zzgridb:hover { outline: 1px solid rgba(255,255,255,.6); }" + NL, 0),
        # 4. the state rule written LAST already wins, which is most of this stylesheet
        ("a state rule standing after the hover rule it answers",
         css + NL + ".zzrow:hover { background: rgba(255,255,255,.04); }" + NL
             + ".zzrow.open { background: rgba(255,138,216,.3); }" + NL, 0),
        # Chrome honours this as a priority, so reporting it is the cry-wolf half again
        ("!important spelled with an escape",
         css.replace(PHONE_STRIP,
                     "  .crewtag { background: none !im" + BS + "portant;"
                     " padding: 0 !im" + BS + "portant; }", 1), 0),
    ]


# len(), not a literal: a nineteenth case added to a `range(18)` never runs, in the one
# file whose thesis is that a checker which passes is worse than no checker.
@pytest.mark.parametrize("idx", range(len(_cases())))
def test_the_checker_is_not_fooled_and_does_not_cry_wolf(idx, tmp_path):
    name, mutated, want = _cases()[idx]
    assert mutated != CSS.read_text(encoding="utf-8"), (
        name + ": the mutation changed nothing, so its anchor has moved")

    r = _run(_point_at(tmp_path, mutated))
    if want:
        assert r.returncode == 1, name + ": slipped past the checker" + NL + r.stdout
    else:
        assert r.returncode == 0, name + ": correct CSS reported as broken" + NL + r.stdout


def test_a_planted_ancestor_that_can_never_contain_the_subject(tmp_path):
    """`.crewpick .crewtag` looks exactly like the fix this hatch exists for, and reaches
    nothing: `.crewpick` only ever holds the two swatch grids.

    This was a strict xfail for a round, with three narrowings tried and rejected on evidence.
    All three inferred containment FROM THE STYLESHEET, which does not contain it: requiring
    the ancestor to appear in another selector is defeated by writing the decoy twice, and
    `.crewtrow .crewtag` is not attested in crews.css either, so it failed the legitimate case;
    requiring the fix to restate the broad rule's value reported the shipped file and broke ten
    cases; dropping the refinement branch fails the descendant fix the hatch is for.

    A reviewer's fourth way was to stop inferring. The REPO has the answer even though the
    stylesheet does not -- crews.js emits both halves -- so `CONTAINS` in the checker declares
    it, one entry, auditable against the markup in half a minute. A wrong entry is a sentence
    somebody can be wrong about out loud, which a heuristic never is.

    The same table rejects `.crewtrow + .crewtag`, a chip that is the next SIBLING of a row:
    `compounds()` threw the combinator away, so a sibling and a descendant were the same
    string.
    """
    css = CSS.read_text(encoding="utf-8")
    for name, planted in (
            ("an ancestor that holds no chips",
             "@media (max-width: 560px) { .crewpick .crewtag"
             " { background: #10131a; padding: 0; } }"),
            ("a sibling rather than an ancestor",
             "@media (max-width: 560px) { .crewtrow + .crewtag"
             " { background: none; padding: 0; } }"),
            ("a general sibling",
             "@media (max-width: 560px) { .crewtrow ~ .crewtag"
             " { background: none; padding: 0; } }")):
        bug = css.replace(PHONE_STRIP, "", 1) + NL + planted + NL
        r = _run(_point_at(tmp_path, bug))
        assert r.returncode == 1, name + ": still hides the bug" + NL + r.stdout
