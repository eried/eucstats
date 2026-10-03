# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""Record which English string each translation was made from.

Run it: python scripts/stamp_i18n.py   (after a localisation pass, never before one)

A reviewer found `crew.targets.p0` telling eighteen locales to do the opposite of what the
English said. The English had been rewritten from "Any {n}x{n} of these and you are" to "these
ARE one block, ride all of them"; the translations still said pick any one. It is the first
sentence a brand-new crew reads, and a rider who followed it rode four scattered squares and
never appeared on the map.

The cause is not that somebody forgot. It is that nothing could notice: a regen pass adds keys
that are missing and has no way to see a key whose English CHANGED underneath a translation
that is still present, still non-empty, and now wrong. Three keys had drifted that way, eight
commits apart, and only an audit caught them.

So each key carries a fingerprint of the English it was translated from. `tests/test_i18n_stamp.py`
compares the two and fails when they diverge, which turns "the translations are stale" from
something a human has to notice into something the suite says.

Changing an English string therefore means one of two things, both deliberate:
  * the meaning moved  -> retranslate, then re-run this;
  * only the wording moved and every translation is still true -> re-run this alone, and say so.
"""
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

OUT = ROOT / "web" / "i18n_stamp.py"


def fingerprint(text: str) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:12]


def main() -> int:
    import i18n

    keys = sorted(k for k in i18n.EN if k.startswith("crew.") or k.startswith("pod."))
    lines = [
        '"""Auto-generated. The English each translation in i18n_data.py was made from.',
        "",
        "Regenerate with scripts/stamp_i18n.py, and only after a localisation pass: the whole",
        "point is that it goes stale when the English moves and the translations do not.",
        'See tests/test_i18n_stamp.py.',
        '"""',
        "",
        "EN_FINGERPRINT = {",
    ]
    for k in keys:
        lines.append('    %r: %r,' % (k, fingerprint(i18n.EN[k])))
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"stamped {len(keys)} keys -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
