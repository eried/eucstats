# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""Catch a CSS rule that silently cancels an earlier one.

Run it: python scripts/check_crews_css.py   (exit 1 when it finds something)

Three versions of this existed in a scratchpad before it earned a place in the tree, and
each missed the bug the next one was written for.

Version one compared whole selector groups and missed a phone-board bug. Version two split
the groups and caught self-cancelling declarations inside one context, which is why it then
missed this: a top-level rule LATER in the file beats a media-query rule EARLIER in it at the
same specificity, whatever the media query says. That is how `.crewtat { display: inline }`
inside the phone block was quietly overridden by `.crewtat { display: block }` sixty lines
further down, and every target row on a phone grew a third line.

So this walks the file in source order and, for each (selector, property), reports when a
later declaration overrides an earlier one in a context that does not narrow it.
"""
import re, pathlib, collections

CSS = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "crews.css"
src = CSS.read_text(encoding="utf-8")
src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)

ctx, i, n, buf = [], 0, len(src), ""
decls = []          # (order, context, selector, property, value)
order = 0
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
            if src[j] == "{": depth += 1
            elif src[j] == "}": depth -= 1
            j += 1
        body, where = src[i + 1:j - 1], " ".join(ctx)
        for sel in [x.strip() for x in head.split(",") if x.strip()]:
            for decl in body.split(";"):
                if ":" not in decl: continue
                prop, val = decl.split(":", 1)
                prop = prop.strip()
                if not prop or prop.startswith("--"): continue
                order += 1
                decls.append((order, where, sel, prop, val.strip()))
        i = j
        continue
    if ch == "}":
        if ctx: ctx.pop()
        buf = ""
        i += 1
        continue
    buf += ch
    i += 1

by_key = collections.defaultdict(list)
for d in decls:
    by_key[(d[2], d[3])].append(d)

bad = 0
for (sel, prop), rows in sorted(by_key.items()):
    for a, b in zip(rows, rows[1:]):
        if a[4] == b[4]:
            continue
        # b wins. It is a real cancellation when b is no more specific in context than a:
        # either both are top level, or b is top level and a is behind a media query.
        if a[1] and not b[1]:
            print(f"OVERRIDDEN  {sel} | {prop}: {a[4]}  [{a[1]}]  ->  {b[4]}  [top level]")
            bad += 1
        elif a[1] == b[1]:
            print(f"SELF-CANCEL {sel} | {prop}: {a[4]} -> {b[4]}  [{a[1] or 'top level'}]")
            bad += 1
print(f"declarations {len(decls)} | selectors {len({(d[2]) for d in decls})} | problems {bad}")
raise SystemExit(1 if bad else 0)
