# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""Catch a CSS rule that silently cancels an earlier one, or never wins at all.

Run it: python scripts/check_crews_css.py   (exit 1 when it finds something)

Four versions of this existed before it earned a place in the tree, and each missed the bug
the next one was written for.

Version one compared whole selector groups and missed a phone-board bug. Version two split the
groups and caught self-cancelling declarations inside one context, which is why it then missed
this: a top-level rule LATER in the file beats a media-query rule EARLIER in it at the same
specificity, whatever the media query says. That is how `.crewtat { display: inline }` inside
the phone block was quietly overridden by `.crewtat { display: block }` sixty lines further
down, and every target row on a phone grew a third line.

Version three walked the file in source order and compared, for each (selector, property), a
later declaration against an earlier one in a context that does not narrow it. A reviewer then
proved it blind to the exact bug it had been written for the round before: it keys on the
literal selector string, so `.crewtag` and `.crewtag.kills` are two different keys and are
never compared at all. `.crewtag { background: none }` inside the phone block is one class;
`.crewtag.kills { background: rgba(...) }` at top level is two. Media queries contribute
nothing to specificity, so the phone rule could not win no matter where it sat, and every
coloured chip kept the tinted box the rule existed to remove. They deleted the fix, re-ran
this script, and got `problems 0`.

So this now does two passes:

  1. the order pass, as before: same selector, same property, a later declaration beating an
     earlier one from a context that does not narrow it.
  2. a specificity pass: where one selector's elements are a subset of another's (the same
     compound plus extra classes), the less specific rule is reported when it cannot win on
     the elements they share -- which is what a media query full of single-class overrides
     looks like from the outside.
"""
import collections
import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "crews.css"


# The priority, not the letters. `"!important" in value` is a substring test: it misses
# `! important` and `!IMPORTANT`, which are both valid and which Chrome honours -- reporting
# correct CSS -- and it counts `url("hero!important.png")` as a priority, which silences every
# report for that property family.
IMPORTANT_RE = re.compile(r"!\s*important\s*$", re.I)


def _strip_comments(src):
    """Comments out, strings left alone.

    This was `re.sub(r"/\\*.*?\\*/", "", src, flags=re.S)` over the whole file, so a rule
    saying `content: "/*"` opened a comment that swallowed everything up to the next `*/` --
    which a reviewer used to silence this script on a stylesheet with a real bug in it.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            j = i + 1
            while j < n and src[j] != ch:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:min(j + 1, n)])
            i = j + 1
            continue
        if ch == "/" and src.startswith("/*", i):
            k = src.find("*/", i + 2)
            i = n if k < 0 else k + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _blocks(src, i=0):
    """Parse from `i` to the matching `}`; return (nodes, position after it).

    A node is `("decl", text)` or `("rule", prelude, children)`. One scanner for every level,
    because the versions that handled one level each were got past one level down, twice in the
    same round. Quotes are skipped whole, so a brace or a semicolon inside a string is text;
    parentheses suspend both, so `url(data:…;base64,…)` stays one value.
    """
    nodes, buf, depth, n = [], "", 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            j = i + 1
            while j < n and src[j] != ch:
                j += 2 if src[j] == "\\" else 1
            buf += src[i:min(j + 1, n)]
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            if ch == "{":
                kids, i = _blocks(src, i + 1)
                nodes.append(("rule", buf.strip(), kids))
                buf = ""
                continue
            if ch == "}":
                if buf.strip():
                    nodes.append(("decl", buf.strip()))
                return nodes, i + 1
            if ch == ";":
                if buf.strip():
                    nodes.append(("decl", buf.strip()))
                buf = ""
                i += 1
                continue
        buf += ch
        i += 1
    if buf.strip():
        nodes.append(("decl", buf.strip()))
    return nodes, i


def _join(sels, head):
    """The selectors a nested rule head stands for, given the ones it is nested inside."""
    out = []
    for raw in split_top(head, ","):
        for parent in (sels or [""]):
            if "&" in raw:
                joined = raw.replace("&", parent)
            elif parent:
                joined = parent + " " + raw
            else:
                joined = raw
            out.extend(expand(joined))
    return out


def _walk(nodes, sels, ctx, decls, order):
    for node in nodes:
        if node[0] == "decl":
            text = node[1]
            # `@charset "utf-8";` and `@import url(…);` carry no block, so they arrive here.
            # Filed as contexts they were pushed on a stack and never popped, and every later
            # declaration was attributed to a context that does not exist.
            if text.startswith("@") or ":" not in text:
                continue
            prop, val = text.split(":", 1)
            prop = prop.strip().lower()       # CSS property names are ASCII case-insensitive
            if not prop or prop.startswith("--"):
                continue
            order[0] += 1
            # " && ", not " ": nesting is conjunction, and the boundary between two
            # at-rules is the whole of what `clauses()` needs to compare them.
            where = " && ".join(ctx)
            for sel in (sels or [""]):
                decls.append((order[0], where, sel, prop, val.strip()))
            continue
        _, head, kids = node
        # An at-rule is a CONTEXT for what it contains, wherever it is written. Joined on as
        # part of the selector it produced `.crewtwho @media (max-width: 560px)` -- a selector
        # matching nothing, scored for specificity, filed at top level.
        if head.startswith("@"):
            _walk(kids, sels, ctx + [head], decls, order)
        else:
            _walk(kids, _join(sels, head), ctx, decls, order)


def parse(src):
    """(order, context, selector, property, value) for every declaration, in source order."""
    # Escapes first, before anything is tokenised, so the tokenizer, `canon`, `specificity`,
    # `_find_is` and the priority test all read the same resolved text. `_resolved` keeps an
    # escaped brace or space from becoming a brace or a space.
    nodes, _ = _blocks(_strip_comments(unescape(src)))
    decls, order = [], [0]
    _walk(nodes, [], [], decls, order)
    return decls


# A shorthand and the longhand it sets are the same declaration as far as the cascade is
# concerned, and keying on the literal property name meant `background: rgba(...)` and
# `background-color: transparent` were never compared. Only the families this stylesheet
# actually uses; a general table would be mostly dead weight.
FAMILY = {
    "background-color": "background", "background-image": "background",
    "padding-left": "padding", "padding-right": "padding",
    "padding-top": "padding", "padding-bottom": "padding",
    "margin-left": "margin", "margin-right": "margin",
    "margin-top": "margin", "margin-bottom": "margin",
    "border-color": "border", "border-width": "border", "border-style": "border",
    "border-left": "border", "border-right": "border",
    "border-top": "border", "border-bottom": "border",
    "font-size": "font", "font-weight": "font", "font-family": "font",
    "overflow-x": "overflow", "overflow-y": "overflow",
    # Logical properties set the same thing as their physical twins, so a rule written one way
    # competes with a rule written the other and the two were never compared.
    "padding-inline": "padding", "padding-inline-start": "padding",
    "padding-inline-end": "padding", "padding-block": "padding",
    "padding-block-start": "padding", "padding-block-end": "padding",
    "margin-inline": "margin", "margin-inline-start": "margin",
    "margin-inline-end": "margin", "margin-block": "margin",
    "margin-block-start": "margin", "margin-block-end": "margin",
    "border-inline-start": "border", "border-inline-end": "border",
    "flex-grow": "flex", "flex-shrink": "flex", "flex-basis": "flex",
}


def split_top(text, sep):
    """Split on `sep`, ignoring any that sit inside brackets.

    The selector head was split on every comma, so `:is(.a, .b)` became two selectors matching
    nothing at all and the rule was invisible to both passes.
    """
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x for x in (p.strip() for p in out) if x]


def _find_is(sel):
    """(start, end, inner) for the first `:is(…)`, with parens balanced.

    `:where()` is NOT expanded, because `:where()` contributes zero specificity and writing it
    out hands its contents' weight to `specificity()`. Left whole it is one opaque qualifier,
    which is what it is for matching, and `specificity()` drops it.

    This was a regex, `:(?:is|where)\\(([^()]*)\\)`, and `:is(.a, .b:not(.x))` has a paren
    inside it -- so the rule matched nothing, expanded to itself, and both passes walked past
    it. One nested paren reopened a hole that had already been closed once.
    """
    for m in re.finditer(r":is\(", sel, re.I):
        i = m.end() - 1
        depth, j = 0, i
        while j < len(sel):
            if sel[j] == "(":
                depth += 1
            elif sel[j] == ")":
                depth -= 1
                if depth == 0:
                    return m.start(), j + 1, sel[i + 1:j]
            j += 1
    return None


# A pseudo-element belongs to the element, not to the qualifiers: `specificity` has always
# counted it as one, and `.crewtag` does not style `.crewtag::after`'s box.
PSEUDO_EL_RE = re.compile(r"::[\w-]+")
# A class, an id, an attribute test, or a pseudo-class with its argument kept whole. The
# argument is never looked inside, which is the bug: `canon` used to sort the classes of the
# entire compound and hoisted `.nokills` out of `:not(.nokills)` into a requirement.
QUAL_RE = re.compile(r"\#[\w-]+|\.[\w-]+|\[[^\]]*\]|:[\w-]+(?:\((?:[^()]|\([^()]*\))*\))?")


def split_compound(part):
    """(element, [qualifiers]) for one compound selector."""
    quals, el, i, n = [], "", 0, len(part)
    while i < n:
        m = PSEUDO_EL_RE.match(part, i)
        if m:
            el += m.group(0)
            i = m.end()
            continue
        m = QUAL_RE.match(part, i)
        if m:
            quals.append(m.group(0))
            i = m.end()
            continue
        el += part[i]
        i += 1
    return el, quals


def expand(head):
    """Every selector a rule head stands for, with `:is()` and `:where()` written out.

    `:is(.a, .b) { … }` is two rules as far as the cascade is concerned. Left folded up, the
    whole rule became one selector matching nothing and both passes walked past it.
    """
    out = []
    for sel in split_top(head, ","):
        todo = [sel]
        while todo:
            cur = todo.pop()
            found = _find_is(cur)
            if not found:
                out.append(cur)
                continue
            a, b, inner = found
            for alt in split_top(inner, ","):
                todo.append(cur[:a] + alt + cur[b:])
    return out


# A logical property and its physical twin are the SAME declaration, not two. `padding-left`
# and `padding-inline-start` on one selector are one rule written twice; `padding` followed by
# `padding-left` is a shorthand then a refinement of it, which is ordinary CSS. Pass 1 keys on
# this so the first pair is compared and the second is not.
PHYSICAL = {
    "padding-inline-start": "padding-left", "padding-inline-end": "padding-right",
    "padding-block-start": "padding-top", "padding-block-end": "padding-bottom",
    "margin-inline-start": "margin-left", "margin-inline-end": "margin-right",
    "margin-block-start": "margin-top", "margin-block-end": "margin-bottom",
    "inset-inline-start": "left", "inset-inline-end": "right",
    "inset-block-start": "top", "inset-block-end": "bottom",
    "border-inline-start": "border-left", "border-inline-end": "border-right",
}


def physical(prop):
    return PHYSICAL.get(prop, prop)


ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.))", re.S)


def _resolved(ch):
    r"""One escaped character, as something that can be compared but never read as structure.

    An identifier character comes back as itself: `.crewtag.k\ills` is `.crewtag.kills`, and
    the browser agrees. Anything else -- a space, a brace, a comma, a backslash -- becomes a
    token, because resolving it to the character was a hole of its own: `\ ` became a real
    space, so `.crewtrow\ .crewtag` (ONE class whose name contains a space, which no element
    can ever carry) was read as a descendant selector, which is the exact shape `fixes()`
    accepts as a fix.
    """
    return ch if (ch.isalnum() or ch in "_-") else "\ue000%04x\ue001" % ord(ch)


def unescape(text):
    r"""Resolve every CSS escape in `text`.

    Called on the whole source before anything is tokenised, rather than inside `canon()`,
    because `specificity()`, `_find_is()` and the priority test all read this text too and a
    resolver that only the comparison sees leaves three readers looking at raw letters.
    """
    return ESCAPE_RE.sub(
        lambda m: _resolved(chr(int(m.group(1), 16)) if m.group(1) else m.group(2)), text)


def canon(sel):
    """One selector, written one way.

    `.crewtrow .crewtag` and `.crewtrow  .crewtag` are the same rule and were two keys, so the
    later of them was never compared with the earlier -- a reviewer got past this script with
    one extra space. Swapping the class order (`.kills.crewtag` for `.crewtag.kills`) did it
    too. Whitespace is collapsed, combinators are spaced consistently, and the classes inside
    each compound are sorted.
    """
    t = re.sub(r"\s*([>+~])\s*", r" \1 ", (sel or "").strip())
    t = re.sub(r"\s+", " ", t)
    out = []
    for part in t.split(" "):
        if part in (">", "+", "~") or not part:
            out.append(part)
            continue
        el, quals = split_compound(part)
        out.append(el + "".join(sorted(quals)))
    return " ".join(x for x in out if x)


def family(prop):
    """What this property belongs to, after the logical name has been resolved.

    `PHYSICAL` already knew that `inset-inline-start` is `left` and `border-inline-start` is
    `border-left`, and `family()` did not ask it -- so pass 2 grouped the padding and margin
    pairs and let the inset and border ones straight through. Two tables disagreeing about
    the same facts.
    """
    p = physical(prop)
    return FAMILY.get(p, p)


def bare(val):
    """The value without its priority, so `none` and `none !important` are not a cancellation."""
    return IMPORTANT_RE.sub("", val).strip()


# All three spellings the range syntax allows, not just the first one. `(560px >= width)` and
# `(0px <= width <= 560px)` are the same feature with the same 2023 baseline, and both mean
# `(max-width: 560px)`.
# A length is a sign, a number and a unit. Both of these required a bare `px`, so
# `(max-width: 35rem)` -- the same breakpoint -- was a different string from `(max-width:
# 560px)` and related to nothing.
LEN = r"([+-]?[\d.]+)(px|rem|em)"
ROOT_PX = 16.0        # the page sets no root font-size, so rem and em are 16px here


def _px(num, unit):
    return float(num) * (1.0 if unit == "px" else ROOT_PX)


RANGE_RE = re.compile(r"\(\s*(width|height)\s*(<=|>=|<|>)\s*" + LEN + r"\s*\)")
RANGE_REV_RE = re.compile(r"\(\s*" + LEN + r"\s*(<=|>=|<|>)\s*(width|height)\s*\)")
RANGE_SPAN_RE = re.compile(
    r"\(\s*" + LEN + r"\s*(?:<=|<)\s*(width|height)\s*(?:<=|<)\s*" + LEN + r"\s*\)")


def _range_syntax(body):
    """`(width <= 560px)` is `(max-width: 560px)`. Baseline in every browser since 2023.

    Invisible here until now, because conditions were compared as strings: the pinned case
    "a media query differing by one space" went quiet when written this way.
    """
    def one(m):
        feat, op = m.group(1), m.group(2)
        side = "max" if op in ("<=", "<") else "min"
        return "(%s-%s:%gpx)" % (side, feat, _px(m.group(3), m.group(4)))

    def rev(m):
        # `(560px >= width)` is `(width <= 560px)`: the comparison, read the other way round
        op, feat = m.group(3), m.group(4)
        side = "max" if op in (">=", ">") else "min"
        return "(%s-%s:%gpx)" % (side, feat, _px(m.group(1), m.group(2)))

    def span(m):
        # an interval is two conditions; the upper bound is the one that bounds a phone
        return "(max-%s:%gpx)" % (m.group(3), _px(m.group(4), m.group(5)))

    body = RANGE_SPAN_RE.sub(span, body)
    body = RANGE_REV_RE.sub(rev, body)
    return RANGE_RE.sub(one, body)


PX_RE = re.compile(r"\((max|min)-(width|height)\s*:\s*" + LEN + r"\)")


def _implies(inner, outer):
    """True when `inner` holding guarantees `outer` holds.

    String equality had no notion of one query containing another, so a later rule in
    `(max-width: 1200px)` beating an earlier one in `(max-width: 560px)` -- which it does, on
    every phone -- was not a cancellation as far as this file was concerned. That is the
    round-six bug with a tablet breakpoint in place of top level.
    """
    if inner == outer:
        return True
    a, b = PX_RE.fullmatch(inner), PX_RE.fullmatch(outer)
    if not a or not b or a.group(1) != b.group(1) or a.group(2) != b.group(2):
        return False
    lo, hi = _px(a.group(3), a.group(4)), _px(b.group(3), b.group(4))
    # a narrower max- is contained by a wider one; min- runs the other way
    return lo <= hi if a.group(1) == "max" else lo >= hi


def clauses(where):
    """A context as one set of alternatives per at-rule it is nested inside.

    `@media a, b` matches a OR b, so one at-rule is a set. Nesting is AND, so a context is a
    list of them. Flattened into a single string and stripped of whitespace, a context nested
    two deep became one meaningless alternative and a correct fix wrapped in `@supports` was
    reported five times.

    The conditions are normalised, because `(max-width:560px)` and `(max-width: 560px)` are
    the same query and comparing them as raw strings let a reviewer re-create the bug this
    file's docstring names by deleting one space. `@media` is dropped as noise; `@supports` is
    kept, so that a support condition is never mistaken for a media one.
    """
    out = []
    for rule in (where or "").split(" && "):
        if not rule.strip():
            continue
        # lower BEFORE replacing: at-rule names are ASCII case-insensitive, so
        # `@Media (max-width: 560px)` never normalised and a real bug inside one
        # exited 0. Range syntax is folded onto the min-/max- form it means.
        # The media TYPE goes too. `@media screen and (max-width: 560px)` is the commonest
        # way to write a phone query and it normalised to the literal
        # `screenand(max-width:560px)`, which relates to nothing -- four spellings of the same
        # query slipped past pass 1. `print` is deliberately NOT stripped: a print context
        # really is different, and folding it into screen would be a false alarm.
        body = rule.lower().replace("@media", " ")
        body = re.sub(r"\bonly\b", " ", body)
        body = re.sub(r"\b(?:screen|all)\s+and\b", " ", body)
        body = re.sub(r"^\s*(?:screen|all)\s*$", " ", body)
        body = _range_syntax(body)
        alt = frozenset(re.sub(r"\s+", "", part) for part in split_top(body, ",")
                        if part.strip())
        if alt:
            out.append(alt)
    return out


def covers(outer, inner):
    """True when `outer` applies everywhere `inner` does.

    Raw string equality missed a reviewer's bypass: they re-created a bug this docstring names
    by appending `@media (max-width: 560px), (hover: none) and (pointer: coarse)`, a different
    string matching the same phone. Comparing every narrow-screen block to every other was the
    wrong correction though -- this file deliberately carries a wide touch block and a narrower
    phone block, and the phone one legitimately refines it ("thumbs, not cursors"). A later
    rule only cancels an earlier one where it applies everywhere the earlier one does.
    """
    co, ci = clauses(outer), clauses(inner)
    if not ci:
        return not co
    if not co:
        return False
    # Every condition the outer rule imposes has to be imposed at least as tightly by the
    # inner one. A clause is a set of alternatives, so "at least as tightly" means every
    # alternative the inner one allows implies one the outer one allows -- by NUMBER where
    # both are lengths, not by spelling.
    def covered_clause(a, b):
        return all(any(_implies(x, y) for y in a) for x in b)
    return all(any(covered_clause(a, b) for b in ci) for a in co)


def specificity(sel):
    """(ids, classes+attrs+pseudo-classes, elements). Good enough for this file's selectors."""
    s = re.sub(r"::[\w-]+", " ", sel)                  # pseudo-elements count as elements
    ids = len(re.findall(r"#[\w-]+", s))
    # `:not(.a, .b)` contributes its MOST SPECIFIC argument, not the sum of them. Counting
    # all of them over-scores, which pushes toward false alarms rather than silence -- but it
    # is still wrong, and a multi-argument `:not()` is ordinary CSS.
    # `:where()` contributes zero. It is a qualifier for matching and a nothing for weight,
    # so it comes out before anything is counted.
    s = re.sub(r":where\((?:[^()]|\([^()]*\))*\)", " ", s, flags=re.I)

    def one_not(m):
        alts = split_top(m.group(1), ",")
        return max(alts, key=lambda x: len(re.findall(r"[.\[#:]", x))) if alts else ""
    s = re.sub(r":not\(([^()]*)\)", one_not, s, flags=re.I)
    cls = len(re.findall(r"\.[\w-]+", s)) + len(re.findall(r"\[[^\]]*\]", s)) \
        + len(re.findall(r":(?!not\b)[\w-]+", s))
    els = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", s))
    return (ids, cls, els)


def compounds(sel):
    """Split a selector into its compounds, dropping combinators.

    Canonical first, so two spellings of one selector give one answer.
    """
    return [c for c in re.split(r"[\s>+~]+", canon(sel)) if c]


def steps(sel):
    """(combinator, compound) for each step, keeping what joins them.

    `compounds()` throws the combinator away, so `.crewtrow .crewtag` and `.crewtrow +
    .crewtag` looked identical -- and the second one is a chip that is the next SIBLING of a
    row, which reaches no chip at all and silenced all six reports.
    """
    t = canon(sel)
    out, comb = [], " "
    for part in t.split(" "):
        if part in (">", "+", "~"):
            comb = part
        elif part:
            out.append((comb, part))
            comb = " "
    return out


# What the markup actually nests inside what. Declared, not inferred: a stylesheet does not
# say which elements contain which, and the three narrowings that tried to guess it from the
# CSS each failed -- one broke the legitimate descendant fix, one reported the shipped file,
# one dropped the branch the hatch exists for.
#
# `.crewtag` is built at crews.js:1232-1311 and concatenated into the `<div class="crewtrow
# …">` returned at crews.js:1316. That is the whole of what this entry claims, and it can be
# checked against the markup in half a minute -- which a heuristic cannot.
#
# Only an ancestor somebody actually writes a fix with ever needs an entry, so this stays
# small. Without one, a descendant rule is not accepted as a fix.
CONTAINS = {
    ".crewtag": {".crewtrow"},
}


def quals_of(compound):
    """Everything that narrows the element: classes, ids, attribute tests, pseudo-classes.

    Named for classes when it counted only `.class` substrings, which is why `.crewtag` and
    `.crewtag[data-fam=kills]` looked like the same set and `.crewtag:not(.nokills)` looked
    like it REQUIRED `.nokills`.
    """
    return frozenset(split_compound(compound)[1])


def base_of(compound):
    """The element the compound selects, with every qualifier taken off."""
    return split_compound(compound)[0]


def subset_pair(a, b):
    """True when every element matching b also matches a.

    Two shapes, both of which this stylesheet uses:
      1. the same structure, where b's final compound carries all of a's classes and more
         (`.crewtag` vs `.crewtag.kills`);
      2. b is a's selector plus further descendant steps (`.crewtwho` vs `.crewtwho i`) --
         which was not compared at all, and is why a rule painting 62px outside its own card
         passed this script clean.
    """
    ca, cb = compounds(a), compounds(b)
    if not ca or not cb:
        return False
    if len(ca) == len(cb):
        if ca[:-1] != cb[:-1]:
            return False
        if base_of(ca[-1]) != base_of(cb[-1]):
            return False
        return quals_of(ca[-1]) < quals_of(cb[-1])
    # b is longer: a describes an ancestor of what b describes. That alone is not a defect --
    # a parent carrying `font-size` and a child carrying its own is ordinary CSS, and flagging
    # it produced five complaints about correct rules. The caller decides, using `relaxes()`.
    #
    # `a` counts as describing an ancestor when its compounds appear IN ORDER inside b's, which
    # is neither a prefix nor a tail. An exact prefix was the first attempt and one extra
    # ancestor step defeated it -- `.crewcard .crewtwho i` against a rule on `.crewtwho` is
    # the ellipsis bug that shipped, invisible to the script that exists to catch it.
    if len(cb) > len(ca):
        i = 0
        for comp in cb:
            if i < len(ca) and compound_matches(ca[i], comp):
                i += 1
        if i == len(ca):
            return True
    return False


def compound_matches(outer, inner):
    """Would `outer` select an element that `inner` also selects?"""
    if outer == inner:
        return True
    if base_of(outer) and base_of(outer) != base_of(inner):
        return False
    return quals_of(outer) <= quals_of(inner) and bool(quals_of(outer))


# Values that mean "take the restriction off". An ancestor set to one of these, against a
# descendant set to something else, is somebody trying to undo a child's rule from the parent
# -- which the cascade never does. That is the whole of the bug a reviewer found: a phone rule
# relaxing `.crewtwho` while `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`
# sat on `.crewtwho i`, so the text ran 62px outside its own card and nothing noticed.
RELAXERS = {
    "none", "normal", "visible", "clip", "auto", "initial", "unset", "revert", "0",
    # the other ways to say "let it wrap" and "let it show" -- a reviewer rewrote the shipped
    # ellipsis bug with `pre-wrap` and it went quiet
    "pre-wrap", "pre-line", "break-spaces", "scroll", "overlay", "100%",
}


# Properties whose value on an ANCESTOR actually governs a descendant, and so can be written
# to lift something the descendant sets. Inheritance is half of it and containment the other:
# a parent's `white-space` reaches the child's text, a parent's `overflow` clips the child's
# box, a parent's `display: none` means the child is never laid out at all.
#
# What is deliberately absent is box geometry -- margin, padding, border, width, inset, gap.
# A descendant cannot defeat an ancestor's margin, because they are two different boxes, and
# the `relaxes()` docstring already argues exactly this for padding before the code forgot to
# ask which property it was holding. Without this, `margin-inline: auto` on three containers
# -- the ordinary way to centre a capped column -- was reported three times against correct
# CSS, each time "losing" to a rule on a DESCENDANT it shares no element with.
GOVERNS_DESCENDANTS = frozenset({
    "white-space", "word-break", "overflow-wrap", "text-overflow", "hyphens",
    "overflow", "display", "visibility", "opacity", "position", "contain", "clip-path",
    "color", "font", "line-height", "letter-spacing", "text-align", "text-transform",
    "direction", "cursor", "pointer-events",
})


# How many distinct selectors each compound appears in. A planted ancestor appears in exactly
# one -- its own rule -- while the fix this hatch exists for, `.crewtrow .crewtag`, names a
# compound the stylesheet uses nine times. See fixes().
SEEN = collections.Counter()


def layered(fix_ctx, broad_ctx):
    """True when the candidate fix sits in a cascade layer and the rule it answers does not.

    An unlayered declaration beats a layered one whatever its specificity or position, so a
    fix written inside `@layer` does not win -- the unlayered rule it was meant to beat keeps
    its value. Taken as the fix it silenced every report for that property. Nothing here uses
    layers yet, which is the right moment to say so.
    """
    return "@layer" in (fix_ctx or "") and "@layer" not in (broad_ctx or "")


def supported(fix_ctx, broad_ctx):
    """True when the candidate fix sits behind a feature query the broad rule is not behind.

    Such a fix only applies where that query passes, and nothing here can know whether it
    does: a reviewer silenced every report with `@supports (-webkit-touch-callout: none)`,
    the standard iOS-only hack, which is false in Chrome. Two rounds earlier the same reviewer
    reported the opposite -- a correct fix wrapped in `@supports (display: flex)` being
    flagged -- and both complaints are fair. They cannot both be met, so correctness decides:
    a fix that holds only when a feature query passes is not one the stylesheet can rely on,
    which is how `layered()` already treats `@layer`.
    """
    return "@supports" in (fix_ctx or "") and "@supports" not in (broad_ctx or "")


def fixes(a, b, c):
    """True when rule `c` could be the fix for `a` losing to `b`.

    Two questions, and both hatches used to accept either answer on its own:

      * does `c` REACH b's elements? b's subject is its last compound, so `c` reaches it when
        c's own subject demands nothing b's subject lacks.
      * is `c` a refinement of `a` -- the same rule written more specifically?

    The descendant fix this hatch exists for (`.crewtrow .crewtag`) answers both. A rule
    merely narrower than `a` (`.crewtag.zzcompact`) answers only the second and touches no
    `.crewtag.kills` at all; a rule merely ending in the right class (`.zlegend .kills`)
    answers only the first. A reviewer silenced all six chip reports with one planted rule of
    each shape, five ways in total, on a stylesheet with the live bug still in it.
    """
    cc, cb = compounds(c), compounds(b)
    if not cc or not cb:
        return False
    if canon(c) == canon(b) or subset_pair(c, b):
        return True
    if not (subset_pair(a, c) and compound_matches(cc[-1], cb[-1])):
        return False
    # An extra ancestor satisfies both halves on its own -- `.zzfoo .crewtag` reaches the chips
    # AND refines `.crewtag` -- so one planted rule silenced all six reports. What separates a
    # real fix from a decoy is whether the markup puts the subject inside that ancestor, and
    # only CONTAINS knows that; the stylesheet does not, and three attempts to infer it from
    # the stylesheet each broke something real.
    subject = cc[-1]
    allowed = set()
    for key, parents in CONTAINS.items():
        if compound_matches(key, subject):
            allowed |= parents
    walk = steps(c)
    # A step's combinator says how it joins the one before it, so the join that matters for
    # the SUBJECT is recorded on the subject's own step -- which is why checking only the
    # ancestors let `.crewtrow + .crewtag` through: a chip NEXT TO a row, reaching no chip
    # inside one.
    for comb, _ in walk[1:]:
        if comb in ("+", "~"):
            return False
    for _comb, anc in walk[:-1]:
        if compound_matches(subject, anc) and compound_matches(anc, subject):
            return False        # a chip inside a chip
        if not any(compound_matches(a2, anc) for a2 in allowed):
            return False
    return True


def plain_variant(broad, narrow):
    """True when `narrow` is `broad` plus one or more ordinary CLASSES and nothing else.

    A state (`:hover`, `:focus-within`) or an attribute test is a different question -- those
    are conditions, not members of a family -- and treating them as members is what made
    dropping the position guard below cry wolf four ways.
    """
    cb, cn = compounds(broad), compounds(narrow)
    if not cb or not cn or len(cb) != len(cn) or cb[:-1] != cn[:-1]:
        return False
    if base_of(cb[-1]) != base_of(cn[-1]):
        return False
    qb, qn = quals_of(cb[-1]), quals_of(cn[-1])
    # A proper superset, which is what `subset_pair()` asks and this did not. A set
    # DIFFERENCE is non-empty whenever the two differ at all, so `.crewtag` counted as a
    # variant of `.crewcard` -- every one-class selector in the file a variant of every other
    # -- and the enumeration below answered True for a card with a single modifier class.
    # A variant ADDS to its base; a sibling swaps one class for another.
    if not qb < qn:
        return False
    return all(q.startswith(".") for q in qn - qb)


def enumerated_family(a, rows):
    """Does `a`'s own context already list sibling variants of it for this property?

    Two or more is an enumeration -- an author writing "these all need this". One is a single
    exception, which is ordinary CSS and not a list somebody forgot to extend.
    """
    seen = {c[2] for c in rows
            if c[1] == a[1] and c[2] != a[2] and plain_variant(a[2], c[2])}
    return len(seen) >= 2


# A class that means "this one is in that state" and a pseudo-class that means "the pointer or
# the keyboard is on it right now". The first is information; the second is transient.
STATE_CLASSES = {"on", "sel", "active", "open", "current", "checked", "selected"}
USER_PSEUDOS = {"hover", "focus", "focus-visible", "focus-within", "active"}


def state_of(compound):
    """The state classes this compound requires, if any."""
    return {q[1:] for q in quals_of(compound)
            if q.startswith(".") and q[1:] in STATE_CLASSES}


def pseudo_of(compound):
    """The user pseudo-classes this compound requires, if any."""
    return {q[1:] for q in quals_of(compound)
            if q.startswith(":") and q[1:].split("(")[0] in USER_PSEUDOS}


def same_base(a, b):
    """The two selectors address the same element, ignoring states and pseudos.

    `.crewpickc.on` and `.crewpickc:hover` are the same cell in two conditions;
    `.crewpickc.on` and `.crewpickp:hover` are two different grids and never conflict.
    """
    ca, cb = compounds(a), compounds(b)
    if len(ca) != len(cb) or not ca:
        return False
    if ca[:-1] != cb[:-1]:
        return False
    if base_of(ca[-1]) != base_of(cb[-1]):
        return False
    strip = lambda cmp: frozenset(
        q for q in quals_of(cmp)
        if not (q.startswith(".") and q[1:] in STATE_CLASSES)
        and not (q.startswith(":") and q[1:].split("(")[0] in USER_PSEUDOS))
    return strip(ca[-1]) == strip(cb[-1])


def relaxes(val, against=None, prop=None):
    """Whether this value lifts a restriction the descendant sets.

    A reviewer wrote the shipped ellipsis bug with `white-space: pre-wrap` instead of `normal`
    and it went invisible -- same rule, same parent, same child still carrying `nowrap;
    overflow: hidden`, same 62px of text outside the card. So the list carries the other
    spellings of "let it wrap" and "let it show".

    It stays a LIST and not "any different value": every value that differs was the obvious
    generalisation and it reports the shipped stylesheet eight times, because a card with
    `padding: 12px` and a file input inside it with `padding: 7px 10px` is a parent and a
    child legitimately disagreeing, which is most of CSS.
    """
    # The one value a descendant cannot answer. `none` belongs in the list for `background`,
    # `overflow` and `border`, but a box with `display: none` generates no box at all, so it has
    # no children in the layout tree and a descendant's `display: block` is never consulted --
    # not outranked, never read. I hid a podium's sub-line on a short screen and this reported
    # the two descendant rules it was hiding: correct CSS, two complaints.
    #
    # `visibility: hidden` is deliberately NOT here: a descendant really can set
    # `visibility: visible` and come back, so an ancestor setting it IS relaxing something.
    if prop is not None and physical(prop) == "display" and bare(val).strip().lower() == "none":
        return False
    # And only for a property an ancestor's value reaches the descendant through. `auto` is in
    # the list above because `overflow: auto` lifts a child's clipping; it is also the value of
    # every centred container in the file, and without this question those were reported as
    # beaten by a `margin` rule on a child three levels down. See GOVERNS_DESCENDANTS.
    if prop is not None and family(prop) not in GOVERNS_DESCENDANTS:
        return False
    return val.strip().lower() in RELAXERS


def main():
    decls = parse(CSS.read_text(encoding="utf-8"))
    bad = []
    # one count per compound, over distinct selectors, for fixes()
    SEEN.clear()
    for sel in {d[2] for d in decls}:
        for comp in set(compounds(sel)):
            SEEN[comp] += 1

    # --- pass 1: same selector, a later rule beating an earlier one that it does not narrow
    # Keyed on the literal property, not the family: pass 1 is about one rule restating what
    # an earlier one said, and `padding-left: 0` after `padding: 2px 6px` is a narrowing, not
    # a cancellation. The family grouping belongs to pass 2, which is about specificity.
    by_key = collections.defaultdict(list)
    for d in decls:
        by_key[(canon(d[2]), physical(d[3]))].append(d)
    # A second index on the FAMILY, for the OVERRIDDEN branch only. Keyed on the literal
    # property, `.crewtag { background: none }` in a media query and a later top-level
    # `.crewtag { background-color: … }` were two keys and never met -- the round-six bug
    # spelled with a longhand. The SELF-CANCEL branch keeps the literal key, because
    # `padding-left` after `padding` is a narrowing rather than a cancellation.
    by_fam = collections.defaultdict(list)
    for d in decls:
        by_fam[(canon(d[2]), family(d[3]))].append(d)
    # Every earlier declaration against every later one, not just the one before it. With
    # adjacent pairs, a single innocuous line asserting the same value sat between a rule and
    # its cancellation and the pair that mattered was never formed.
    for (sel, fam), rows in sorted(by_fam.items()):
        for i, b in enumerate(rows):
            for a in rows[:i]:
                if bare(a[4]) == bare(b[4]) or physical(a[3]) == physical(b[3]):
                    continue    # same spelling: the index above already judged it
                if IMPORTANT_RE.search(a[4]) and not IMPORTANT_RE.search(b[4]):
                    continue
                # `clauses()`, not the raw string. `@media screen and (max-width: 560px)` and
                # a bare `@media screen` both normalise to nothing, and reading the text
                # instead let a `@media screen {}` wrapper round the winning rule silence
                # every case in this branch.
                if clauses(a[1]) and not clauses(b[1]):
                    bad.append(f"OVERRIDDEN  {sel} | {fam}: {a[3]}: {a[4]}  [{a[1]}]"
                               f"  ->  {b[3]}: {b[4]}  [top level]")
                    break
    # A longhand wiped by a later shorthand on the same selector. Directional on purpose: a
    # shorthand sets every component it has, so one standing AFTER a longhand resets it --
    # somebody wrote a specific value and then a general one over the top. The reverse,
    # `padding-left: 0` after `padding: 2px 6px`, is a deliberate exception and is why
    # `by_key` is keyed on the literal property; reporting that would be crying wolf on most
    # of CSS.
    #
    # It took `.crewmore { margin-top: 8px }` meeting a `.crewmore { margin: 6px 0 0 }` two
    # hundred lines away -- a different element sharing a class name -- for this to show up,
    # with pass 1's two indexes between them seeing nothing: one keys on the literal property
    # so they never met, and the other only reports a conditional rule beaten by a top-level
    # one.
    for (sel, fam), rows in sorted(by_fam.items()):
        for i, b in enumerate(rows):
            if physical(b[3]) != fam:
                continue                        # `b` has to be the shorthand
            for a in rows[:i]:
                if physical(a[3]) == fam:
                    continue                    # two shorthands: the index above has it
                if bare(a[4]) == bare(b[4]):
                    continue
                if IMPORTANT_RE.search(a[4]) and not IMPORTANT_RE.search(b[4]):
                    continue                    # a priority is not overridden by a later rule
                if not (covers(b[1], a[1]) or covers(a[1], b[1])):
                    continue                    # different, unrelated conditions
                bad.append(f"SHORTHAND   {sel} | {a[3]}: {a[4]} is reset by the later"
                           f" {b[3]}: {b[4]}  [{a[1] or 'top level'}]")
                break

    for (sel, prop), rows in sorted(by_key.items()):
        for i, b in enumerate(rows):
            for a in rows[:i]:
                if bare(a[4]) == bare(b[4]):
                    continue
                # `!important` beats the cascade, so the earlier rule is not being overridden
                # by anything that lacks it. `bare()` takes the priority off for the equality
                # test above and nothing read it here, so a correct stylesheet was reported.
                if IMPORTANT_RE.search(a[4]) and not IMPORTANT_RE.search(b[4]):
                    continue
                if clauses(a[1]) and not clauses(b[1]):
                    bad.append(f"OVERRIDDEN  {sel} | {prop}: {a[4]}  [{a[1]}]"
                               f"  ->  {b[4]}  [top level]")
                    break
                elif covers(b[1], a[1]):
                    bad.append(f"SELF-CANCEL {sel} | {prop}: {a[4]} -> {b[4]}"
                               f"  [{a[1] or 'top level'}]")
                    break

    # --- pass 2: a rule that cannot win on the elements it shares with a more specific one.
    # Grouped by property, because that is the granularity at which one rule beats another.
    by_prop = collections.defaultdict(list)
    for d in decls:
        by_prop[family(d[3])].append(d)
    seen = set()

    # --- pass 2a: a transient pseudo-class that erases a state nothing puts back.
    #
    # `.crewpickc.on { outline: 2px solid #fff }` then `.crewpickc:hover { outline: 1px … }`:
    # both 0-2-0, neither a subset of the other, so the later wins and a selected swatch under
    # the pointer looks exactly like an unselected one. `subset_pair()` models extra CLASSES,
    # so the pair is invisible to the whole of pass 2.
    #
    # Narrow on purpose. A hover tint over `.pod.gold1`'s plate is ordinary CSS and reporting
    # it is how this script gets narrowed until it catches nothing. The defect is that nothing
    # puts the state BACK: no rule anywhere reaches `X.<state>:<pseudo>` for that property, so
    # for as long as the pointer rests there the state has no channel left.
    for prop, rows in sorted(by_prop.items()):
        for a in rows:
            st = state_of(a[2])
            if not st or IMPORTANT_RE.search(a[4]):
                continue
            for b in rows:
                if b is a or bare(a[4]) == bare(b[4]) or b[0] < a[0]:
                    continue
                ps = pseudo_of(b[2])
                if not ps or state_of(b[2]) or not same_base(a[2], b[2]):
                    continue
                if specificity(b[2]) < specificity(a[2]):
                    continue
                if not covers(b[1], a[1]) and not covers(a[1], b[1]):
                    continue
                # Anything that restores the state under that pseudo-class, however written:
                # `X.on:hover`, or a `:not(.on)` on the pseudo rule itself (which is the fix).
                #
                # `same_base` here as well. Without it the restoring rule did not have to be
                # about the same element -- ANY rule anywhere carrying a `.sel` and a `:hover`
                # for that property counted, and this file has one, so the planted case that
                # was supposed to prove the pass fires came back silent.
                if any(c is not b and family(c[3]) == prop
                       and state_of(c[2]) & st and pseudo_of(c[2]) & ps
                       and same_base(a[2], c[2])
                       for c in rows):
                    continue
                if any(q == ":not(." + name + ")" for q in quals_of(compounds(b[2])[-1])
                       for name in st):
                    continue
                key = ("state", prop, a[2], b[2])
                if key in seen:
                    continue
                seen.add(key)
                bad.append(
                    f"STATE LOST  {a[2]} | {prop}: {a[4]}  is erased by  {b[2]}: {b[4]}"
                    f"  and nothing restores it")

    for prop, rows in sorted(by_prop.items()):
        for a in rows:
            for b in rows:
                if a is b or a[4] == b[4]:
                    continue
                # `!important` beats specificity outright, so a rule carrying it is not losing
                # to anything here. The script reported six problems against a stylesheet that
                # was correct.
                if IMPORTANT_RE.search(a[4]):
                    continue
                if not subset_pair(a[2], b[2]):
                    continue
                # An ancestor/descendant pair only matters when the ancestor is trying to lift
                # a restriction the descendant sets; anything else is a parent and a child
                # legitimately holding different values.
                if len(compounds(b[2])) > len(compounds(a[2])) \
                        and not relaxes(a[4], b[4], a[3]):
                    continue
                # `a` is the broad rule, `b` the narrow one. `a` only wins on b's elements
                # if it is at least as specific, which (media queries adding nothing) it is
                # not.
                if specificity(a[2]) >= specificity(b[2]):
                    continue
                # And only when `a` was written to override: a broad rule standing EARLIER in
                # the file than a narrow one is ordinary CSS -- the narrow one is the
                # exception to it, which is the whole point of writing it. The defect is a
                # rule placed later, plainly meant to win, that cannot.
                #
                # Except where `a`'s own context already ENUMERATES variants of it for this
                # property. Then position says nothing: the enumeration is the author stating
                # that these variants all need the rule, and a further variant is one they
                # missed, wherever in the file it was added. A seventh chip family appended at
                # the end of the file was invisible while the same declaration beside the
                # other six was reported -- the wrong way round for the likeliest edit here.
                if a[0] < b[0] and not (
                        # and only for the shape where position really is irrelevant: the
                        # broad rule conditional, the narrow one not. A top-level base beaten
                        # by a rule inside a media query is how this stylesheet is MEANT to
                        # work, and reporting it is five complaints about the shipped file.
                        clauses(a[1]) and not clauses(b[1])
                        and plain_variant(a[2], b[2])
                        and enumerated_family(a, rows)):
                    continue
                # Unless something inside a's own context restates it at b's specificity --
                # which is exactly what the fix for this looks like.
                # Anything in a's own context that reaches b's elements at or above b's
                # specificity is the fix, however it is written. Requiring it to look like a
                # subset_pair reported five problems against a stylesheet corrected with a
                # descendant selector (`.crewtrow .crewtag`), which is a perfectly good fix.
                if any(c[1] and covers(c[1], a[1]) and family(c[3]) == prop
                       and IMPORTANT_RE.search(c[4]) and not layered(c[1], a[1])
                       and not supported(c[1], a[1])
                       and fixes(a[2], b[2], c[2]) for c in rows):
                    continue
                # Anything that reaches b's elements at or above b's specificity, from a
                # context compatible with a's, is the fix -- however it is written.
                #
                # `endswith` used to be in here, twice, and a reviewer silenced all six real
                # reports with one unrelated rule whose last compound happened to be a string
                # suffix of the narrow selector. Reaching an element is a question about
                # compounds; `.crewtag.kills".endswith(".kills")` is a question about letters.
                #
                # Either direction on the context, because one direction rejected a correct
                # fix wrapped in `@supports` and five complaints about correct CSS is how a
                # detector gets narrowed until it catches nothing.
                covered = any(
                    # One direction. `or covers(a[1], c[1])` accepted a fix from a context
                    # NARROWER than the rule it answers: the chip strip moved into `@media
                    # (max-width: 380px)` silenced the whole 381-560 band, and this file's own
                    # two phone blocks did it to each other. It was added in round twelve for
                    # the `@supports` false alarm, which `supported()` now handles.
                    covers(c[1], a[1])
                    and family(c[3]) == prop and c[2] != a[2]
                    and specificity(c[2]) >= specificity(b[2])
                    and not layered(c[1], a[1]) and not supported(c[1], a[1])
                    and fixes(a[2], b[2], c[2])
                    for c in rows)
                if covered:
                    continue
                key = (prop, a[2], b[2], a[1])
                if key in seen:
                    continue
                seen.add(key)
                bad.append(
                    f"NEVER WINS  {a[2]} | {prop}: {a[4]}  [{a[1] or 'top level'}]"
                    f"  loses to  {b[2]}: {b[4]}  [{b[1] or 'top level'}]")

    for line in bad:
        print(line)
    print(f"declarations {len(decls)} | selectors {len({d[2] for d in decls})} "
          f"| problems {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
