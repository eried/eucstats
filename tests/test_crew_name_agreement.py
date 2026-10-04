"""The browser's name rule and the server's, compared directly over the same corpus.

Two rules for one question is the bug. The client was
`/^[\\p{L}\\p{N}\\p{M}_ \\-'&.]{3,28}$/u` and the server `^[\\w \\-'&.]{3,28}$`, and a comment in
crews.js asserted they agreed because "Python's `\\w` also covers combining marks". It does
not: a mark is category Mn or Mc and `str.isalnum()` is False for it.

So every DECOMPOSED name passed in the browser and was refused by the server. Decomposed is
not exotic -- it is what iOS and macOS text input commonly produce. A reviewer measured
`Zu\\u0308rich Crew` (u followed by U+0308) accepted by the client, refused 400 by the server,
with the message "3-28 characters." about a twelve-character name. An earlier round had
"verified" the pair against eight names and found zero mismatches, because every one of the
eight was precomposed.

The guard that was missing is this one: take a corpus that deliberately includes both
spellings of the same name, run the SHIPPED client regex under node and the server's own
`name_ok` in process, and require that they reach the same verdict on every string. A rule
written twice needs a test that compares them, not two tests that each check one.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from services import crews

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

# Anchored on text: if `nameProblem` moves or is renamed, this fails loudly instead of
# silently testing nothing.
START = "  var NAME_OK ="
END = "  function createHTML(ident) {"

# Deliberately includes the precomposed AND decomposed spelling of the same two names, which
# is the pair the old rules disagreed on.
CORPUS = [
    "Nordlys Collective",
    "Night Riders",
    "Кланы",
    "戦隊兵",
    "كلان",
    "Zürich Crew",              # precomposed U+00FC
    "Zu\u0308rich Crew",        # decomposed: u + U+0308 -- the measured failure
    "Ångström Crew",            # precomposed
    "A\u030angstro\u0308m Crew",  # decomposed
    "हिन्दी क्रू",                  # Devanagari with matras (category Mc)
    "Ron's Crew #1",            # a `#`: refused by both
    "Rev B 🛞 Crew",            # emoji: refused by both
    "<b>hi</b>",
    "ab",                       # too short
    "a" * 29,                   # too long
    "a" * 28,                   # exactly the limit
    "abc",                      # exactly the floor
    "   Night Riders   ",       # trimmed by both
    "Crew_One",                 # underscore
    "Harbour & Bridge",
    "Crew-One.Two",
    "",
    "   ",
    # 28 code units raw, 14 after NFC: the length asymmetry. Without normalising on the client
    # this is refused by the browser for length and accepted by the server.
    "Z\u0308" * 14,
]


def _region():
    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(START)
    j = src.index(END, i)
    region = src[i:j]
    for needed in ("NAME_OK", "function nameProblem", "normalize"):
        assert needed in region, f"{needed} is not in the lifted region; this tests nothing"
    return region


HARNESS = """
const t = (k) => k;
%(region)s

const CORPUS = %(corpus)s;
console.log(JSON.stringify(CORPUS.map(n => ({ name: n, problem: nameProblem(n) }))));
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node to run the shipped js")


def _client_verdicts():
    d = pathlib.Path(tempfile.mkdtemp(prefix="nameagree-"))
    try:
        f = d / "t.mjs"
        f.write_text(HARNESS % {"region": _region(),
                                "corpus": json.dumps(CORPUS)}, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return {row["name"]: row["problem"] for row in json.loads(r.stdout)}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_two_rules_agree_on_every_name_in_the_corpus():
    """The whole point. One disagreement here is a name a rider can type and have refused."""
    client = _client_verdicts()
    mismatches = []
    for name in CORPUS:
        client_ok = client[name] is None
        server_ok = crews.name_ok(name)
        if client_ok != server_ok:
            mismatches.append({
                "name": name,
                "codepoints": " ".join(f"U+{ord(c):04X}" for c in name[:12]),
                "browser": "accepts" if client_ok else f"refuses ({client[name]})",
                "server": "accepts" if server_ok else "refuses",
            })
    assert not mismatches, (
        "the browser and the server disagree about these names. Whichever way round it is, a "
        "rider meets it as a form that either refuses what is legal or accepts what will be "
        f"thrown away by the server:\n{json.dumps(mismatches, indent=2, ensure_ascii=False)}")


def test_both_spellings_of_an_accented_name_are_accepted():
    """The measured regression, named directly rather than left to the corpus loop."""
    client = _client_verdicts()
    for name in ("Zürich Crew", "Zu\u0308rich Crew"):
        assert crews.name_ok(name), f"the server refuses {name!r}"
        assert client[name] is None, f"the browser refuses {name!r} ({client[name]})"


def test_both_spellings_normalise_to_one_stored_name():
    """Uniqueness has to mean what a reader thinks it means: two rows that look identical in a
    list are a worse outcome than a refusal."""
    assert crews.clean_name("Zu\u0308rich Crew") == crews.clean_name("Zürich Crew")
    assert crews.clean_name("A\u030angstro\u0308m Crew") == crews.clean_name("Ångström Crew")


def test_marks_are_accepted_and_emoji_are_not():
    """The two halves of the rule that the old `\\w` got wrong in opposite directions."""
    assert crews.name_ok("हिन्दी क्रू"), "a script written with combining marks is refused"
    assert not crews.name_ok("Rev B 🛞 Crew"), "emoji should not be a crew name"
    assert not crews.name_ok("Ron's Crew #1"), "`#` is not in the five allowed characters"


def test_the_length_limits_are_counted_after_normalising():
    """Fourteen decomposed pairs are 28 code units raw and 14 after NFC, so the two sides have
    to measure the same string or they disagree on length alone."""
    client = _client_verdicts()
    name = "Z\u0308" * 14
    assert (client[name] is None) == crews.name_ok(name), (
        f"browser says {client[name]!r}, server says {crews.name_ok(name)} -- the length is "
        "being counted on different strings")
