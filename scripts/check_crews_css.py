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


def parse(src):
    """(order, context, selector, property, value) for every declaration, in source order."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    ctx, i, n, buf = [], 0, len(src), ""
    decls, order = [], 0
    while i < n:
        ch = src[i]
        if ch == "{":
            head, buf = buf.strip(), ""
            if head.startswith("@"):
                ctx.append(head)
                i += 1
                continue
            depth, j = 1, i + 1
            while j < n and depth:
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                j += 1
            body, where = src[i + 1:j - 1], " ".join(ctx)
            for sel in expand(head):
                for decl in body.split(";"):
                    if ":" not in decl:
                        continue
                    prop, val = decl.split(":", 1)
                    prop = prop.strip()
                    if not prop or prop.startswith("--"):
                        continue
                    order += 1
                    decls.append((order, where, sel, prop, val.strip()))
            i = j
            continue
        if ch == "}":
            if ctx:
                ctx.pop()
            buf = ""
            i += 1
            continue
        buf += ch
        i += 1
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
    "inset-inline-start": "inset", "inset-inline-end": "inset",
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


IS_RE = re.compile(r":(?:is|where)\(([^()]*)\)")


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
            m = IS_RE.search(cur)
            if not m:
                out.append(cur)
                continue
            for alt in split_top(m.group(1), ","):
                todo.append(cur[:m.start()] + alt + cur[m.end():])
    return out


def family(prop):
    return FAMILY.get(prop, prop)


def bare(val):
    """The value without its priority, so `none` and `none !important` are not a cancellation."""
    return val.replace("!important", "").strip()


def alts(where):
    """A media context as its set of alternatives. `@media a, b` matches a OR b.

    Normalised, because `(max-width:560px)` and `(max-width: 560px)` are the same query and
    comparing them as raw strings let a reviewer re-create the bug this file's docstring
    names by deleting one space.
    """
    body = (where or "").replace("@media", " ").lower()
    out = set()
    for part in split_top(body, ","):
        part = re.sub(r"\s+", "", part)
        if part:
            out.add(part)
    return frozenset(out)


def covers(outer, inner):
    """True when `outer` applies everywhere `inner` does.

    Raw string equality missed a reviewer's bypass: they re-created a bug this docstring names
    by appending `@media (max-width: 560px), (hover: none) and (pointer: coarse)`, a different
    string matching the same phone. Comparing every narrow-screen block to every other was the
    wrong correction though -- this file deliberately carries a wide touch block and a narrower
    phone block, and the phone one legitimately refines it ("thumbs, not cursors"). A later
    rule only cancels an earlier one where it applies everywhere the earlier one does.
    """
    if not inner:
        return not outer
    return alts(inner) <= alts(outer) if outer else False


def specificity(sel):
    """(ids, classes+attrs+pseudo-classes, elements). Good enough for this file's selectors."""
    s = re.sub(r"::[\w-]+", " ", sel)                  # pseudo-elements count as elements
    ids = len(re.findall(r"#[\w-]+", s))
    cls = len(re.findall(r"\.[\w-]+", s)) + len(re.findall(r"\[[^\]]*\]", s)) \
        + len(re.findall(r":(?!not\b)[\w-]+", s))
    els = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", s))
    return (ids, cls, els)


def compounds(sel):
    """Split a selector into its compounds, dropping combinators."""
    return [c for c in re.split(r"[\s>+~]+", sel.strip()) if c]


def classes_of(compound):
    return frozenset(re.findall(r"\.[\w-]+", compound))


def base_of(compound):
    """The compound with its classes removed, so `a.b.c` and `a.b` share a base."""
    return re.sub(r"\.[\w-]+", "", compound)


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
        return classes_of(ca[-1]) < classes_of(cb[-1])
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
    return classes_of(outer) <= classes_of(inner) and bool(classes_of(outer))


# Values that mean "take the restriction off". An ancestor set to one of these, against a
# descendant set to something else, is somebody trying to undo a child's rule from the parent
# -- which the cascade never does. That is the whole of the bug a reviewer found: a phone rule
# relaxing `.crewtwho` while `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`
# sat on `.crewtwho i`, so the text ran 62px outside its own card and nothing noticed.
RELAXERS = {"none", "normal", "visible", "clip", "auto", "initial", "unset", "revert", "0"}


def relaxes(val):
    return val.strip().lower() in RELAXERS


def main():
    decls = parse(CSS.read_text(encoding="utf-8"))
    bad = []

    # --- pass 1: same selector, a later rule beating an earlier one that it does not narrow
    # Keyed on the literal property, not the family: pass 1 is about one rule restating what
    # an earlier one said, and `padding-left: 0` after `padding: 2px 6px` is a narrowing, not
    # a cancellation. The family grouping belongs to pass 2, which is about specificity.
    by_key = collections.defaultdict(list)
    for d in decls:
        by_key[(d[2], d[3])].append(d)
    for (sel, prop), rows in sorted(by_key.items()):
        for a, b in zip(rows, rows[1:]):
            if bare(a[4]) == bare(b[4]):
                continue
            if a[1] and not b[1]:
                bad.append(f"OVERRIDDEN  {sel} | {prop}: {a[4]}  [{a[1]}]"
                           f"  ->  {b[4]}  [top level]")
            elif covers(b[1], a[1]):
                bad.append(f"SELF-CANCEL {sel} | {prop}: {a[4]} -> {b[4]}"
                           f"  [{a[1] or 'top level'}]")

    # --- pass 2: a rule that cannot win on the elements it shares with a more specific one.
    # Grouped by property, because that is the granularity at which one rule beats another.
    by_prop = collections.defaultdict(list)
    for d in decls:
        by_prop[family(d[3])].append(d)
    seen = set()
    for prop, rows in sorted(by_prop.items()):
        for a in rows:
            for b in rows:
                if a is b or a[4] == b[4]:
                    continue
                # `!important` beats specificity outright, so a rule carrying it is not losing
                # to anything here. The script reported six problems against a stylesheet that
                # was correct.
                if "!important" in a[4]:
                    continue
                if not subset_pair(a[2], b[2]):
                    continue
                # An ancestor/descendant pair only matters when the ancestor is trying to lift
                # a restriction the descendant sets; anything else is a parent and a child
                # legitimately holding different values.
                if len(compounds(b[2])) > len(compounds(a[2])) and not relaxes(a[4]):
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
                if a[0] < b[0]:
                    continue
                # Unless something inside a's own context restates it at b's specificity --
                # which is exactly what the fix for this looks like.
                # Anything in a's own context that reaches b's elements at or above b's
                # specificity is the fix, however it is written. Requiring it to look like a
                # subset_pair reported five problems against a stylesheet corrected with a
                # descendant selector (`.crewtrow .crewtag`), which is a perfectly good fix.
                if any(c[1] and covers(c[1], a[1]) and family(c[3]) == prop
                       and "!important" in c[4] for c in rows):
                    continue
                covered = any(
                    covers(c[1], a[1]) and family(c[3]) == prop and c[2] != a[2]
                    and specificity(c[2]) >= specificity(b[2])
                    and (subset_pair(c[2], b[2]) or subset_pair(a[2], c[2])
                         or c[2].endswith(a[2]) or b[2].endswith(c[2].split()[-1]))
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
