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
            for sel in [x.strip() for x in head.split(",") if x.strip()]:
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
    """True when every element matching b also matches a, by the common pattern in this file:
    identical selector structure, and b's final compound carries all of a's classes and more."""
    ca, cb = compounds(a), compounds(b)
    if len(ca) != len(cb) or not ca:
        return False
    if ca[:-1] != cb[:-1]:
        return False
    if base_of(ca[-1]) != base_of(cb[-1]):
        return False
    sa, sb = classes_of(ca[-1]), classes_of(cb[-1])
    return sa < sb


def main():
    decls = parse(CSS.read_text(encoding="utf-8"))
    bad = []

    # --- pass 1: same selector, a later rule beating an earlier one that it does not narrow
    by_key = collections.defaultdict(list)
    for d in decls:
        by_key[(d[2], d[3])].append(d)
    for (sel, prop), rows in sorted(by_key.items()):
        for a, b in zip(rows, rows[1:]):
            if a[4] == b[4]:
                continue
            if a[1] and not b[1]:
                bad.append(f"OVERRIDDEN  {sel} | {prop}: {a[4]}  [{a[1]}]"
                           f"  ->  {b[4]}  [top level]")
            elif a[1] == b[1]:
                bad.append(f"SELF-CANCEL {sel} | {prop}: {a[4]} -> {b[4]}"
                           f"  [{a[1] or 'top level'}]")

    # --- pass 2: a rule that cannot win on the elements it shares with a more specific one.
    # Grouped by property, because that is the granularity at which one rule beats another.
    by_prop = collections.defaultdict(list)
    for d in decls:
        by_prop[d[3]].append(d)
    seen = set()
    for prop, rows in sorted(by_prop.items()):
        for a in rows:
            for b in rows:
                if a is b or a[4] == b[4]:
                    continue
                if not subset_pair(a[2], b[2]):
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
                covered = any(
                    c[1] == a[1] and c[3] == prop and c[2] != a[2]
                    and subset_pair(a[2], c[2]) and classes_of(compounds(c[2])[-1])
                    >= classes_of(compounds(b[2])[-1])
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
