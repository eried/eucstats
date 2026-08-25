// Runs the public page's init() under stub browser APIs and reports whether the chrome
// would appear. A block-scoped const that doIntro() cannot see is a ReferenceError at
// runtime, not a syntax error, so `node --check` passes it and the whole UI dies. This
// executes the code instead. Driven by tests/test_intro_runtime.py.
//
//   node intro_harness.js <app.js> <scenario>     scenario: natural | skip | off
const fs = require("fs");
const [, , scriptPath, scenario] = process.argv;
let src = fs.readFileSync(scriptPath, "utf8");

const nodes = new Map();
function el(id) {
  if (nodes.has(id)) return nodes.get(id);
  const e = {
    id, dataset: {}, innerHTML: "", textContent: "", offsetWidth: 0, offsetParent: null,
    duration: 6.4, currentTime: 0, readyState: 4, paused: false,
    style: { setProperty() {}, removeProperty() {}, getPropertyValue: () => "" },
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
                 toggle(c) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); },
                 contains(c) { return this._s.has(c); } },
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {}, remove() { nodes.delete(id); },
    appendChild() {}, setAttribute() {}, getAttribute: () => null,
    play: () => ({ catch() {} }), load() {},
  };
  nodes.set(id, e);
  return e;
}

globalThis.window = globalThis;
globalThis.innerWidth = 1280; globalThis.innerHeight = 800; globalThis.devicePixelRatio = 1;
globalThis.document = {
  getElementById: id => el(id),
  querySelector: sel => el("sel:" + sel),
  querySelectorAll: () => [],
  addEventListener() {}, createElement: () => el("new" + Math.random()),
  body: el("body"), documentElement: el("html"),
};
const store = { eucstats_intro_off: scenario === "off" ? "1" : null };
globalThis.localStorage = { getItem: k => (k in store ? store[k] : null),
                            setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
globalThis.sessionStorage = globalThis.localStorage;
globalThis.navigator = { language: "en-GB" };
const winListeners = {};
globalThis.addEventListener = (t, f) => { (winListeners[t] = winListeners[t] || []).push(f); };
globalThis.removeEventListener = (t, f) => {
  if (winListeners[t]) winListeners[t] = winListeners[t].filter(x => x !== f);
};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
globalThis.requestAnimationFrame = cb => setTimeout(cb, 0);
globalThis.visualViewport = null;
globalThis.IntersectionObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };
globalThis.ResizeObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };
globalThis.fetch = u => {
  const url = String(u);
  const body = /map\/cells|records|champions\b/.test(url)
    ? [] : { entries: [], riders: 0, trips: 0, total_km: 0, countries: 0, day: null, week: null, month: null };
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
globalThis.Intl = {
  DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: "Europe/Oslo" }) }),
  DisplayNames: function () { this.of = c => c; },
};
globalThis.maplibregl = {
  Map: function () {
    this.on = (ev, cb) => { if (ev === "load") setTimeout(cb, 10); };
    this.addControl = () => {}; this.flyTo = () => { globalThis.__flew = true; };
    this.getZoom = () => 2; this.setPaintProperty = () => {}; this.addSource = () => {};
    this.addLayer = () => {}; this.getSource = () => null; this.setStyle = () => {}; this.resize = () => {};
  },
  NavigationControl: function () {}, LngLatBounds: function () { this.extend = () => {}; },
};

src = src.replace("function runIntro(){", "function runIntro(){ globalThis.__revealed = true;");

let thrown = null;
try { new Function(src)(); } catch (e) { thrown = e; }

setTimeout(() => {
  const vid = nodes.get("intro");
  if (scenario === "skip" && vid) {
    (winListeners.pointerdown || []).forEach(f => f({ type: "pointerdown" }));
  }
  if (scenario !== "off" && vid && vid.onended) vid.onended();     // the video finishes
  setTimeout(() => {
    console.log(JSON.stringify({
      scenario,
      threw: thrown ? thrown.constructor.name + ": " + thrown.message : null,
      revealed: globalThis.__revealed === true,
      seekedTo: vid ? +vid.currentTime.toFixed(2) : null,
    }));
    process.exit(0);
  }, 60);
}, 120);
