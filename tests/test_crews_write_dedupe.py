"""`api()`'s in-flight map, which deduplicated more than it was asked to.

Round 18 found that a double-click on Join sent two POSTs and whichever landed last decided
what the rider saw. I keyed an in-flight map on `method + " " + path` and that fixed it. Round
19 found what it broke: `store_id` is in the BODY, so two different riders decided through one
endpoint were ONE key. With latency on `/decide`, Let in on row 1 and Let in on row 2 four
hundred milliseconds later sent one request, and the second click received the first's promise,
saw `r.ok` and repainted as though it had worked — measured `members 3, pending [one name]`,
with nothing on screen saying a rider had been skipped. `/decide` also carries `accept`, so Let
in followed by No handed an acceptance to a decline handler.

Clearing a queue of knocks is the leader's whole job, and it is the exact shape that triggers
it. The replacement was worse than the bug.

The class is "a cache key narrower than the thing it identifies", and the reviewer who found it
named the guard: fire two writes with different bodies at one path inside one tick and assert
two requests reach the server. That is what this does, by lifting the real `api()` out of
crews.js and running it against a counting `fetch` — the same technique `test_crews_plural.py`
uses for the plural machinery and `test_public_render.py` for the row helpers.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CREWS_JS = ROOT / "web" / "static" / "crews.js"

# The lifted region: the in-flight map and `api()` itself. Anchored on text rather than line
# numbers, so moving the function fails loudly instead of testing nothing.
START = "  var inFlight = {};"
END = "  // The crew's own emblem at name size."

HARNESS = """
let sent = [];
globalThis.fetch = function (path, opt) {
  sent.push({ path: path, method: (opt && opt.method) || "GET",
              body: opt && opt.body ? JSON.parse(opt.body) : null });
  // Deliberately slow, so every call in a burst is still in flight when the next one starts.
  return new Promise(function (resolve) {
    setTimeout(function () {
      resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve({ ok: true, seq: sent.length });
      } });
    }, 40);
  });
};

%(region)s

const P = "/api/v1/crews/cordillera-sur/decide";

async function run() {
  const out = {};

  // Two different riders, one endpoint, one tick. This is the leader clearing a queue.
  sent = [];
  const two = await Promise.all([
    api("POST", P, { store_id: "rider-one", accept: true }),
    api("POST", P, { store_id: "rider-two", accept: true }),
  ]);
  out.twoRiders = { requests: sent.length, bodies: sent.map(s => s.body.store_id) };

  // Accept one, decline another. Same path, same shape, opposite meaning.
  sent = [];
  await Promise.all([
    api("POST", P, { store_id: "rider-one", accept: true }),
    api("POST", P, { store_id: "rider-two", accept: false }),
  ]);
  out.acceptAndDecline = { requests: sent.length,
                           accepts: sent.map(s => s.body.accept) };

  // The same button pressed twice: one write, which is what the in-flight map is FOR.
  sent = [];
  const dup = await Promise.all([
    api("POST", P, { store_id: "rider-one", accept: true }),
    api("POST", P, { store_id: "rider-one", accept: true }),
  ]);
  out.doubleClick = { requests: sent.length, sameAnswer: dup[0] === dup[1] };

  // And once it has settled the endpoint is free again, so a second decision still goes.
  sent = [];
  await api("POST", P, { store_id: "rider-one", accept: true });
  await api("POST", P, { store_id: "rider-one", accept: true });
  out.sequential = { requests: sent.length };

  // A GET is never deduplicated: two readers of the same path are two reads.
  sent = [];
  await Promise.all([api("GET", "/api/v1/crews/me"), api("GET", "/api/v1/crews/me")]);
  out.gets = { requests: sent.length };

  console.log(JSON.stringify(out));
}

run();
"""


def _region():
    src = CREWS_JS.read_text(encoding="utf-8")
    i = src.index(START)
    j = src.index(END, i)
    region = src[i:j]
    for needed in ("function api(", "inFlight[key]", "var key = method"):
        assert needed in region, f"{needed} is not in the lifted region; this tests nothing"
    return region


pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node to run the shipped js")


def _run():
    d = pathlib.Path(tempfile.mkdtemp(prefix="dedupe-"))
    try:
        f = d / "t.mjs"
        f.write_text(HARNESS % {"region": _region()}, encoding="utf-8")
        r = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_two_riders_decided_at_once_are_two_writes():
    """The regression. One key for both meant the second rider was never decided."""
    out = _run()
    assert out["twoRiders"]["requests"] == 2, (
        "two riders decided through one endpoint collapsed into "
        f"{out['twoRiders']['requests']} request(s). A leader clearing a queue of knocks "
        "silently skips everyone after the first, and the skipped click reports the first "
        f"one's success. Sent: {out['twoRiders']['bodies']}")
    assert sorted(out["twoRiders"]["bodies"]) == ["rider-one", "rider-two"]


def test_an_acceptance_and_a_refusal_are_not_the_same_write():
    """`/decide` carries `accept`, so collapsing these returns a yes to a no."""
    out = _run()
    assert out["acceptAndDecline"]["requests"] == 2, (
        "Let in on one rider and No on another became one write, so one of them got the "
        "other's answer")
    assert sorted(out["acceptAndDecline"]["accepts"]) == [False, True]


def test_the_same_button_twice_is_still_one_write():
    """What the in-flight map exists for. Round 18: a double-click sent two POSTs."""
    out = _run()
    assert out["doubleClick"]["requests"] == 1, (
        f"a double-click sent {out['doubleClick']['requests']} writes")
    assert out["doubleClick"]["sameAnswer"], "both clicks should share one answer"


def test_a_settled_write_frees_its_endpoint():
    """Otherwise one decision per session per rider, which is worse again."""
    out = _run()
    assert out["sequential"]["requests"] == 2, (
        "the in-flight entry was not released, so the endpoint is wedged for the session")


def test_reads_are_never_deduplicated():
    """Two readers of one path are two reads; only writes are at risk here."""
    out = _run()
    assert out["gets"]["requests"] == 2
