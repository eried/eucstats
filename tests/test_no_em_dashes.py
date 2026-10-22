"""No em dash reaches a rider, in any language or on any channel.

Erwin has asked for this twice. The first time it was the nineteen translation tables, and
the fix was to write it into the style note and clean them out. The second time was the
Telegram channel, which nobody had thought of as rider-facing copy at all:

    "[21/10/2026 17:00] EUCPlanet: 📍 Arctic Wheelers just passed 50 squares — 50 squares
    held."
    "in the telegram bot there is a lot of emdashes, why? I though I said I dont like them"

Ten posted strings had one. So the rule is not a note any more: every string literal that a
rider can end up reading is checked here, and the check is on the SOURCE rather than on a
rendered message, because a message is only built when its event fires and most of these fire
once in the life of a crew.

Docstrings and comments are deliberately not checked. They are prose for whoever is reading
the code, this repo's own writing uses the dash freely in them, and no rider sees one.
"""
import ast
import pathlib

EM = chr(8212)
EN = chr(8211)
NL = chr(10)
ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every file whose string literals are read by a rider. `i18n_data.py` is generated from
# `i18n.py`, and both are listed: a hand-edit to the generated table is already against the
# rules and this would not be the test to let it through.
FACING = ["services/telegram.py", "web/i18n.py", "web/i18n_data.py"]


def _literals(path):
    """Every string constant in the file that is not a docstring, with its line number."""
    src = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docs.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_nothing_a_rider_reads_contains_an_em_dash():
    bad = []
    for path in FACING:
        for line, value in _literals(path):
            if EM in value:
                where = value.replace(NL, " ")
                at = where.index(EM)
                bad.append(f"  {path}:{line}  ...{where[max(0, at - 40):at + 40]}...")
    assert not bad, (
        f"{len(bad)} string(s) a rider reads contain an em dash. Use a comma, a colon, a full "
        f"stop, or the middot this code already separates facts with:" + NL + NL.join(bad))


def test_the_en_dash_is_still_allowed_where_it_is_punctuation():
    """The one legitimate long dash, kept deliberately so the rule above cannot be widened
    into a blanket ban by somebody tidying up.

    `22:00–05:00` is a range, which is what an en dash is for, and it is written that way in
    all nineteen locales including the three that do not use Latin script. Banning it would
    mean changing a typographically correct string in nineteen tables to fix nothing.
    """
    ranges = [(p, line) for p in FACING for line, v in _literals(p) if EN in v]
    assert ranges, (
        "the time-range strings no longer use an en dash. If that was deliberate, delete this "
        "test; if it was a search-and-replace over all long dashes, it went too far")
    assert all("UTC" in v for p in FACING for _l, v in _literals(p) if EN in v), (
        "an en dash turned up somewhere other than a time range. It is allowed as a range "
        "separator and nowhere else -- in prose it reads as the dash Erwin has twice asked "
        "not to see")
