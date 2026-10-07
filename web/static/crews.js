/* Crews & Territory — the map mode and the management panel.
 *
 * Kept out of the page template on purpose: this is a static file, so it is cached by the
 * browser and can be served by nginx without the application being involved at all. The
 * server's whole contribution to drawing the map is one pre-built gzipped file.
 *
 * Everything geometric happens here:
 *   - tiles become rectangles (two multiplications each) rather than arriving as GeoJSON
 *   - adjacent tiles of one crew are dissolved into a single feature, so no seams
 *   - region outlines come from edge detection, which is one pass over the tiles
 *   - emblems are DOM markers sized from the zoom, so a logo always fills its square
 *
 * Rectangles are used ONLY in this mode. The heatmap's blobs are right for "people ride
 * here" and wrong for "this ground is held": a blob has no edge to hold.
 */
(function () {
  "use strict";

  var TERR = null;          // the payload as served
  var map = null;
  var H = {};               // helpers handed in by the page
  var markers = [];
  // Crew mode is on, which is not the same as the panel being open, and conflating the two
  // made "Details" on the map popup a no-op on a phone. Tapping a ride target closes the
  // panel (`H.closePanel`) and so does the header X, and neither runs `hide()` -- that is a
  // MODE change -- so `visible` stayed true with no panel on screen and `if (!visible) show()`
  // never fired. Two flags, because they are two questions.
  var visible = false;     // crew mode: the layers are on the map
  var panelOpen = false;   // the panel itself is on screen
  var KEEPTOP = 0;         // #pbody scrollTop carried across an in-place refresh
  var pairTimer = null, pairToken = null;
  var ME = null;

  var CFG = window.__CREWCFG__ || {};
  // From the server, which is the only place the set is decided -- `services.crews.PATTERNS`
  // validates every write against it. Typed out here as well, this went from four to twelve on
  // the server and stayed at four in the grid a founder actually picks from, which is the same
  // bug as the weekly cap that was a 6 in this file. The literal is a fallback for a page
  // served before the config carried the field, nothing more.
  var PATTERNS = (CFG.patterns && CFG.patterns.length)
    ? CFG.patterns.slice()
    : ["solid", "stripes", "dots", "hatch"];
  // `|| 7` turned a configured 0 into 7, so an admin who switched the cooldown off was still
  // telling riders to wait a week. Same class of bug as the hardcoded "7 days" it replaced.
  var COOLDOWN_DAYS = CFG.cooldown_days != null ? CFG.cooldown_days : 7;
  var SEED = CFG.seed || 2;
  var WINDOW_DAYS = CFG.window_days || 90;

  // `fadesIn` stood here: "fades in 3 days", the countdown on a cold square. It is gone
  // because the square no longer goes -- an uncontested holder is pinned at the floor rather
  // than dropped, so the only honest thing to say about a cold square is how little it costs
  // somebody else, which is what bands 1 and 2 already say.

  // With the cooldown switched off there is no waiting to describe, so the sentence changes
  // rather than the number. "No new crew for right now" is what came out before.
  // The last member out takes the crew with them. `services/crews.py` retires the clan when
  // nobody active is left, AND stamps the cooldown -- so on a solo crew the gentle-sounding
  // button ends the crew and benches you for a week, while Disband two rows down does the
  // same thing to the crew for nothing. Neither prompt said so.
  function leaveQuestion(name) {
    var solo = ME && ME.crew && (ME.crew.members || 0) <= 1;
    if (COOLDOWN_DAYS <= 0) return t("crew.mine.leaveq0", { name: name });
    if (solo) return t("crew.mine.leaveq.last", { name: name, n: days(COOLDOWN_DAYS) });
    // A leader leaving a crew that carries on. `leave()` hands the crew to nobody: it is left
    // with officers and no leader until one of them claims it. This prompt said only "No new
    // crew for 7 days" -- what a plain member reads -- to the one person who could hand it
    // over first. Only when there IS somebody left to inherit it, which is also the only case
    // the server lets a leader leave in at all.
    var lead = ME && ME.role === "leader";
    return t(lead ? "crew.mine.leaveq.lead" : "crew.mine.leaveq",
             { name: name, n: days(COOLDOWN_DAYS) });
  }

  function days(n) {
    if (n <= 0) return t("crew.now");
    return plural("crew.day1", "crew.days.few", "crew.days", n);
  }

  /* ---------- geometry ---------- */

  // Web Mercator tile -> lon/lat corners. The inverse of services/tiles.py, and the reason
  // territory is on this grid: these rectangles are square on screen at every latitude, so an
  // emblem dropped into one is not stretched.
  function tileLon(x, z) { return x / Math.pow(2, z) * 360 - 180; }
  function tileLat(y, z) {
    var n = Math.PI - 2 * Math.PI * y / Math.pow(2, z);
    return 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  }
  function tileRing(x, y, z) {
    var w = tileLon(x, z), e = tileLon(x + 1, z), n = tileLat(y, z), s = tileLat(y + 1, z);
    return [[[w, n], [e, n], [e, s], [w, s], [w, n]]];
  }

  // cells arrive as [crewIndex, x, y, band, tenths-of-a-km], and the band carries one extra
  // bit: +5 means the crew took this square within the last week. Folded in rather than sent
  // as a sixth integer, because this is the one payload every visitor downloads.
  // x:y -> [band, tenths of a km]. The fills are dissolved per crew per band so the map stays
  // cheap, which means a feature cannot carry a per-tile number: it used to hand over the
  // FIRST tile's, so every safe tile of a crew reported the same figure when the real spread
  // was 0.2 to 9.9 km. The popup reads the tile it was actually clicked on from here.
  var TILEINFO = {};

  function bandOf(v) { return v % 5; }
  function isFresh(v) { return v >= 5; }

  function cellsByCrew() {
    TILEINFO = {};
    var out = [];
    if (!TERR || !TERR.cells) return out;
    for (var i = 0; i < TERR.cells.length; i += 5) {
      var c = TERR.cells[i];
      if (!out[c]) out[c] = [];
      var x = TERR.cells[i + 1], y = TERR.cells[i + 2];
      var raw = TERR.cells[i + 3] || 0;
      out[c].push([x, y, bandOf(raw), TERR.cells[i + 4] || 0, isFresh(raw) ? 1 : 0]);
      TILEINFO[x + ":" + y] = [bandOf(raw), TERR.cells[i + 4] || 0, isFresh(raw) ? 1 : 0];
    }
    return out;
  }

  // Region outlines without any server help: a tile edge is a boundary when the tile on the
  // other side of it is not the same crew's. One pass, a set lookup per side.
  function outline(cells, z) {
    var own = Object.create(null), i, lines = [];
    for (i = 0; i < cells.length; i++) own[cells[i][0] + ":" + cells[i][1]] = 1;
    for (i = 0; i < cells.length; i++) {
      var x = cells[i][0], y = cells[i][1];
      var w = tileLon(x, z), e = tileLon(x + 1, z), n = tileLat(y, z), s = tileLat(y + 1, z);
      if (!own[x + ":" + (y - 1)]) lines.push([[w, n], [e, n]]);
      if (!own[x + ":" + (y + 1)]) lines.push([[w, s], [e, s]]);
      if (!own[(x - 1) + ":" + y]) lines.push([[w, n], [w, s]]);
      if (!own[(x + 1) + ":" + y]) lines.push([[e, n], [e, s]]);
    }
    return lines;
  }

  /* ---------- pattern sprites ---------- */

  // Four images, not one per crew. The fill layer paints the colour and a second layer paints
  // a translucent stencil over it, so adding a fifth pattern costs one more image and
  // ninety-six crews cost none.
  // One 16x16 tile per pattern, drawn so that it repeats seamlessly: every stroke that leaves
  // one edge has to arrive at the opposite one, which is why the diagonals run from -S to 2S
  // and the dots are placed on the diagonal rather than in the corners.
  function patternImage(kind) {
    var S = 16, cv = document.createElement("canvas");
    cv.width = cv.height = S;
    var g = cv.getContext("2d");
    var i, j;
    g.clearRect(0, 0, S, S);
    g.strokeStyle = "rgba(0,0,0,.42)";
    g.fillStyle = "rgba(0,0,0,.42)";
    g.lineWidth = 3;

    function diag(back) {
      for (i = -S; i < S * 2; i += 8) {
        g.beginPath();
        if (back) { g.moveTo(i + S, 0); g.lineTo(i, S); } else { g.moveTo(i, 0); g.lineTo(i + S, S); }
        g.stroke();
      }
    }
    function lines(horiz) {
      for (i = 2; i < S; i += 8) {
        g.beginPath();
        if (horiz) { g.moveTo(0, i); g.lineTo(S, i); } else { g.moveTo(i, 0); g.lineTo(i, S); }
        g.stroke();
      }
    }
    function discs(pts, r) {
      pts.forEach(function (p) {
        g.beginPath(); g.arc(p[0], p[1], r, 0, 6.2832); g.fill();
      });
    }

    if (kind === "stripes") {
      diag(false);
    } else if (kind === "backslash") {
      diag(true);
    } else if (kind === "hatch") {
      g.lineWidth = 2; diag(false); diag(true);
    } else if (kind === "vert") {
      lines(false);
    } else if (kind === "horiz") {
      lines(true);
    } else if (kind === "grid") {
      g.lineWidth = 2; lines(false); lines(true);
    } else if (kind === "dots") {
      discs([[4, 4], [12, 12]], 2.6);
    } else if (kind === "bigdots") {
      // One disc per tile, big enough to read as a spot rather than as texture -- this is the
      // pattern that has to stay apart from `dots` at fourteen pixels.
      discs([[8, 8]], 5);
    } else if (kind === "rings") {
      g.lineWidth = 2.4;
      g.beginPath(); g.arc(8, 8, 4.6, 0, 6.2832); g.stroke();
    } else if (kind === "checker") {
      g.fillRect(0, 0, S / 2, S / 2);
      g.fillRect(S / 2, S / 2, S / 2, S / 2);
    } else if (kind === "bricks") {
      g.lineWidth = 2;
      for (i = 0; i <= S; i += 8) {
        g.beginPath(); g.moveTo(0, i); g.lineTo(S, i); g.stroke();
      }
      // the cross joints, offset row to row, which is what makes it bricks and not `horiz`
      for (j = 0; j < 2; j++) {
        var x = j === 0 ? 4 : 12;
        g.beginPath(); g.moveTo(x, j * 8); g.lineTo(x, j * 8 + 8); g.stroke();
      }
    }
    return g.getImageData(0, 0, S, S);
  }

  function ensurePatterns() {
    PATTERNS.forEach(function (p) {
      var id = "crewpat-" + p;
      if (p !== "solid" && !map.hasImage(id)) {
        map.addImage(id, patternImage(p), { pixelRatio: 2 });
      }
    });
  }

  /* ---------- layers ---------- */

  // How far each pressure band is dimmed, and the single place it is written down. The legend
  // chips read from this too: when the two were typed out separately the stylesheet ended up
  // painting band 3 brighter than band 2, which is backwards, and nobody could see it without
  // holding both files open.
  var BAND_OP = { 0: 1, 1: 0.88, 2: 0.78, 3: 0.7, 4: 1 };

  // A ten per cent step reads across a city block and not across a sixteen-pixel chip, so the
  // ladder is stretched for the key while keeping the map's order exactly.
  function chipOp(band) {
    var v = BAND_OP[band] == null ? 1 : BAND_OP[band];
    return Math.round((0.3 + (v - 0.7) / 0.3 * 0.7) * 100) / 100;
  }

  // Kept by hand once, and a layer added without being added here took the whole mode down:
  // clearLayers dropped what it knew, then removing `crew-hot` threw because the forgotten
  // layer still used it, which aborted the rest of the teardown and left buildLayers to
  // throw on a source that already existed. Every id this file adds is recorded as it is
  // added now, so the two cannot drift again.
  var LAYERS = [];

  // Underneath the basemap's labels. Every layer this file adds is a fill or a line -- there
  // is not one symbol layer among the thirteen -- and they were all appended on top of the
  // style, which put a crew's colour over the place names. At the zooms the mode itself flies
  // you to, "OSLO" under Holmenkollen's tan and the neighbourhood name under Cykelslangen's
  // lilac were effectively unreadable while identical labels a hundred pixels outside the
  // fill read fine. That is the one screen whose entire job is answering "where do I ride",
  // and the answer is a place name.
  //
  // Lowering the opacity was the wrong lever -- it has been tuned twice already and the
  // territory is meant to read as solid ground. A choropleth belongs under the labels, which
  // is what `beforeId` is for. Recomputed per call rather than cached: a style switch wipes
  // the layer list and `buildLayers` runs again against a different basemap.
  function labelsStart() {
    try {
      var ls = map.getStyle().layers || [];
      for (var i = 0; i < ls.length; i++) {
        if (ls[i].type === "symbol") return ls[i].id;
      }
    } catch (e) {}
    return undefined;        // no labels in this style: append, as before
  }

  function addLayer(spec) {
    if (LAYERS.indexOf(spec.id) < 0) LAYERS.push(spec.id);
    map.addLayer(spec, labelsStart());
  }

  function cursorPointer() { map.getCanvas().style.cursor = "pointer"; }
  function cursorDefault() { map.getCanvas().style.cursor = ""; }

  // Coalesced to one pass per frame. A single wheel gesture fires about 27 zoom events, and
  // each one wrote three styles per marker.
  var sizePending = false;
  function onZoom() {
    if (sizePending) return;
    sizePending = true;
    requestAnimationFrame(function () { sizePending = false; sizeEmblems(); });
  }

  /* ---------- the slow breath ----------
     One property write every 1.6s; MapLibre eases between the two values on the GPU. A
     requestAnimationFrame loop would repaint the whole map sixty times a second to animate two
     numbers. The two layers run in opposite phase, so ground under strain dims as ground just
     taken brightens, and a glance tells the two apart without reading anything. */
  var pulseTimer = null, pulseUp = false;
  var CALM = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function beat() {
    if (!map.getLayer("crew-pulse-danger")) return;
    pulseUp = !pulseUp;
    // all the way down to nothing and back, so it reads as breathing rather than a tint
    map.setPaintProperty("crew-pulse-danger", "fill-opacity", pulseUp ? 0.26 : 0);
    map.setPaintProperty("crew-pulse-fresh", "fill-opacity", pulseUp ? 0 : 0.26);
  }

  function startPulse(any) {
    stopPulse();
    if (!any) return;                 // nothing happening
    // A reader who asked for stillness gets stillness, not blankness. Both layers are
    // declared at opacity 0 and only `beat()` ever gives them a value, so returning here
    // left them invisible for good -- and one of them is the only warning that somebody is
    // taking a square off your own crew. Lower than the pulse's peak for danger: a steady
    // white wash reads louder than a breathing one.
    if (CALM) {
      // Danger brighter than fresh. The calm note says "what is marked brighter is your
      // own", and both layers are now scoped to your own crew -- but a square being taken
      // OFF you is the louder of the two facts, so it is the louder wash.
      try {
        map.setPaintProperty("crew-pulse-danger", "fill-opacity", 0.3);
        map.setPaintProperty("crew-pulse-fresh", "fill-opacity", 0.2);
      } catch (e) {}
      return;
    }
    beat();
    pulseTimer = setInterval(function () {
      if (document.hidden) return;    // a hidden tab does not need a heartbeat
      beat();
    }, 2600);
  }

  function stopPulse() {
    if (pulseTimer) clearInterval(pulseTimer);
    pulseTimer = null;
    pulseUp = false;
  }

  // Your ground, when you have some. Applied here as well as at build time, because the layer
  // is created before the request that says who you are has come back.
  // Everyone else steps back so your own ground reads first and the unheld gaps come back.
  // 0.68 was picked by eye at street zoom: enough that two crews meeting at a border are two
  // things, little enough that a rival's block is still a block and not a suggestion.
  var THEIRS = 0.68;

  // How much of the ground shows through, by zoom. Flat colour is right for an overview --
  // that is the map answering "who holds what" -- and wrong once you are close enough to be
  // asking "which streets". A reviewer called the fly-to view the worst-looking screen in the
  // product: pastel blocks filling the viewport with no roads under them, reached by the one
  // control whose whole job is to send you somewhere to ride. Erwin had already said the same
  // thing about the layer in general ("But it is very opaque"), which is what set the current
  // 0.75; this is that note applied where it bites hardest rather than a second global cut.
  //
  // Nothing changes at or below z11, so the overview keeps the weight it was tuned at.
  // `interpolate` has to be the TOP of the expression, with the whole ladder rebuilt at each
  // stop -- `["*", ladder, ["interpolate", ["zoom"] ...]]` is the obvious shape and MapLibre
  // rejects it outright: "zoom expression may only be used as input to a top-level step or
  // interpolate expression". It throws from `setPaintProperty`, the paint property keeps its
  // previous value, and the map goes on looking exactly as it did -- no exception reaches the
  // page, nothing fails, and the only trace is a console line. I wrote it the wrong way first
  // and the screenshot looked plausible.
  // Ranged to the zooms a rider can actually reach. The map is built with `maxZoom: 13.23`
  // (public.py) and the targets card flies to `max(current, 11.8)`, so a ramp ending at 14.5
  // put its own floor 1.27 levels past the ceiling -- unreachable at every zoom -- and gave
  // the fly-to view, the one this exists for, about a tenth of the effect. A reviewer
  // measured 0.673 against the old 0.75 and called it a 10% lightening, which is exactly
  // what it was. Ending just inside the ceiling makes the bottom of the ramp a place you
  // can stand, and 11.8 now lands a fifth of the way down it rather than a twentieth.
  var ZOOM_LO = 11, ZOOM_HI = 13.2, FADE_HI = 0.45;

  // The two ladders, applied. Pulled out of `buildLayers` because they have to be applied
  // AGAIN once `/crews/me` lands: `bandOp` branches on `ME.crew` to dim every other crew's
  // ground to `THEIRS`, and `buildLayers` runs its `requestAnimationFrame` long before that
  // request resolves. So on every cold load the expression went up without its `case` branch
  // and your own territory was drawn exactly like everybody else's -- the single most
  // emotional thing this mode does, and it only worked if you left the panel and came back,
  // which rebuilt the layers after `ME` was set.
  //
  // `scopePulse()` two lines below the re-apply already carried a comment saying precisely
  // this about the pulse layers. The fill and the pattern needed the same treatment and never
  // got it. A reviewer pixel-diffed a cold load against a tab round trip: 39.3% of the frame
  // changed, a rival's square moved from rgb(142,109,71) to rgb(115,91,65), and the reader's
  // own squares were byte-identical in both.
  function paintBands() {
    if (!map || !map.getLayer || !map.getLayer("crew-fill")) return;
    var op = (window.__CREWCFG__ && window.__CREWCFG__.opacity) || 0.75;
    // 0.9 here is the FADING band's own multiplier, which is a different decision from how
    // heavy the stencil is -- I changed this one first by mistake, and it only ever touched
    // band 3.
    var pat = Math.min(op, 0.65);
    try {
      map.setPaintProperty("crew-fill", "fill-opacity", bandOp(op, 1));
      map.setPaintProperty("crew-pattern", "fill-opacity", bandOp(pat, 0.9));
    } catch (e) {}
  }

  function bandOp(op, b3) {
    function at(scale) {
      var o = op * scale;
      var ladder = ["match", ["get", "band"],
                    1, o * BAND_OP[1], 2, o * BAND_OP[2], 3, o * BAND_OP[3] * b3,
                    4, o, o];
      if (!ME || !ME.crew) return ladder;
      var theirs = ["match", ["get", "band"],
                    1, o * BAND_OP[1] * THEIRS, 2, o * BAND_OP[2] * THEIRS,
                    3, o * BAND_OP[3] * b3 * THEIRS, 4, o * THEIRS, o * THEIRS];
      return ["case", ["==", ["get", "slug"], ME.crew.slug], ladder, theirs];
    }
    return ["interpolate", ["linear"], ["zoom"],
            ZOOM_LO, at(1), ZOOM_HI, at(FADE_HI)];
  }

  // BOTH layers. The filters are written at build time, which happens before `/crews/me`
  // resolves, so each one is first applied in its signed-out form and this is what corrects
  // them. I scoped the fresh layer last round and left this function alone, so fifteen of
  // sixteen crews' fresh ground went on breathing under a legend that says -- in two
  // different wordings, one of them written for the stillness case -- that it does not.
  function scopePulse() {
    if (!map) return;
    ["danger", "fresh"].forEach(function (kind) {
      var id = "crew-pulse-" + kind;
      if (!map.getLayer(id)) return;
      map.setFilter(id, (ME && ME.crew)
        ? ["all", ["==", ["get", "kind"], kind], ["==", ["get", "slug"], ME.crew.slug]]
        : ["==", ["get", "kind"], kind]);
    });
  }

  function clearLayers() {
    map.off("zoom", onZoom);
    map.off("moveend", onZoom);
    map.off("click", "crew-fill", onCellClick);
    map.off("mouseenter", "crew-fill", cursorPointer);
    map.off("mouseleave", "crew-fill", cursorDefault);
    map.off("mousemove", "crew-fill", onCellHover);
    map.off("mousemove", "crew-target-hit", onTargetHover);
    map.off("click", "crew-target-hit", onTargetTap);
    map.off("click", dismissTip);
    map.off("mouseleave", "crew-target-hit", hideTip);
    map.off("mouseenter", "crew-target-hit", cursorPointer);
    map.off("mouseleave", "crew-target-hit", cursorDefault);
    map.off("mouseleave", "crew-fill", hideTip);
    map.off("movestart", hideTip);
    hideTip();
    LAYERS.forEach(function (l) { if (map.getLayer(l)) map.removeLayer(l); });
    if (map.getSource("crew-cells")) map.removeSource("crew-cells");
    if (map.getSource("crew-edges")) map.removeSource("crew-edges");
    if (map.getSource("crew-hot")) map.removeSource("crew-hot");
    if (map.getSource("crew-targets")) map.removeSource("crew-targets");
    if (map.getSource("crew-pulse")) map.removeSource("crew-pulse");
    if (map.getSource("crew-shine-src")) map.removeSource("crew-shine-src");
    stopPulse();
    markers.forEach(function (m) { m.remove(); });
    markers = [];
    if (POPUP) { POPUP.remove(); POPUP = null; }
  }

  function buildLayers() {
    if (!TERR || !TERR.crews || !TERR.crews.length) return;
    ensurePatterns();
    var z = TERR.z, groups = cellsByCrew();
    var fills = { type: "FeatureCollection", features: [] };
    var pulse = { type: "FeatureCollection", features: [] };
    var edges = { type: "FeatureCollection", features: [] };
    var hot = { type: "FeatureCollection", features: [] };

    TERR.crews.forEach(function (crew, idx) {
      var cells = groups[idx] || [];
      if (!cells.length) return;
      // One feature per crew per pressure band. Adjacent tiles share an edge exactly, so a
      // single feature renders as one solid shape with no antialiasing seam through it — and
      // splitting by band is what lets contested ground be drawn fainter without needing a
      // separate feature for every tile on the map.
      [0, 1, 2, 3, 4].forEach(function (band) {
        var inBand = cells.filter(function (t) { return (t[2] || 0) === band; });
        if (!inBand.length) return;
        fills.features.push({
          type: "Feature",
          properties: { c: crew.colour, p: "crewpat-" + crew.pattern, i: idx, band: band,
                        name: crew.name, slug: crew.slug, km2: crew.km2 },
          geometry: { type: "MultiPolygon",
                      coordinates: inBand.map(function (t) { return tileRing(t[0], t[1], z); }) }
        });
      });
      // under strain, and just taken: the two things on this map that are happening rather
      // than merely being the case
      // 1 as well as 2: an alarm that only rings once it is nearly too late is not an alarm.
      var danger = cells.filter(function (t) { return t[2] === 1 || t[2] === 2; });
      if (danger.length) {
        pulse.features.push({
          type: "Feature", properties: { kind: "danger", slug: crew.slug },
          geometry: { type: "MultiPolygon",
                      coordinates: danger.map(function (t) { return tileRing(t[0], t[1], z); }) }
        });
      }
      var fresh = cells.filter(function (t) { return t[4]; });
      if (fresh.length) {
        pulse.features.push({
          // The slug, so this layer can be scoped the way `crew-pulse-danger` is. Without
          // it the fresh wash breathed for every crew on the map -- fourteen of sixteen in
          // the current payload -- while the legend said "everyone else's sits still".
          type: "Feature",
          properties: { kind: "fresh", c: crew.colour, slug: crew.slug },
          geometry: { type: "MultiPolygon",
                      coordinates: fresh.map(function (t) { return tileRing(t[0], t[1], z); }) }
        });
      }
      edges.features.push({
        type: "Feature",
        properties: { c: crew.colour, i: idx },
        geometry: { type: "MultiLineString", coordinates: outline(cells, z) }
      });
      // One ring around the ground under pressure, not a box per tile -- and two rings, not
      // one: "about to flip" is the most urgent state there is and was sharing a mark with
      // "somebody is riding it", which is also why the key had to invent shapes for them.
      [[1, "pushed"], [2, "flipping"]].forEach(function (pair) {
        var band = pair[0];
        var pressed = cells.filter(function (t) { return (t[2] || 0) === band; });
        if (!pressed.length) return;        // fading ground is not under attack
        hot.features.push({
          type: "Feature",
          properties: { c: crew.colour, i: idx, kind: pair[1] },
          geometry: { type: "MultiLineString", coordinates: outline(pressed, z) }
        });
      });
    });

    map.addSource("crew-cells", { type: "geojson", data: fills });
    map.addSource("crew-edges", { type: "geojson", data: edges });
    map.addSource("crew-hot", { type: "geojson", data: hot });
    map.addSource("crew-pulse", { type: "geojson", data: pulse });

    var op = (window.__CREWCFG__ && window.__CREWCFG__.opacity) || 0.75;
    addLayer({
      id: "crew-pulse-danger", type: "fill", source: "crew-pulse",
      // Your ground, when you have some. "Somebody is taking this off you" animating exactly
      // like "a crew in Santiago is being leaned on" is ambience, not a warning. Signed out,
      // every crew's shows, because then none of it is yours and all of it is news.
      filter: (ME && ME.crew)
        ? ["all", ["==", ["get", "kind"], "danger"], ["==", ["get", "slug"], ME.crew.slug]]
        : ["==", ["get", "kind"], "danger"],
      paint: { "fill-color": "#ffffff", "fill-opacity": 0, "fill-antialias": false,
               "fill-opacity-transition": { duration: 1600 } }
    });
    addLayer({
      id: "crew-pulse-fresh", type: "fill", source: "crew-pulse",
      // Scoped like the danger layer above, and for the same reason its comment gives: a
      // square you have just taken breathing exactly like a square somebody in Santiago has
      // just taken is ambience, not news. Signed out, all of it is news again.
      filter: (ME && ME.crew)
        ? ["all", ["==", ["get", "kind"], "fresh"],
                  ["==", ["get", "slug"], ME.crew.slug]]
        : ["==", ["get", "kind"], "fresh"],
      paint: { "fill-color": ["get", "c"], "fill-opacity": 0, "fill-antialias": false,
               "fill-opacity-transition": { duration: 1600 } }
    });
    addLayer({
      id: "crew-fill", type: "fill", source: "crew-cells",
      paint: { "fill-color": ["get", "c"], "fill-opacity": 0,
               "fill-antialias": false,
               "fill-opacity-transition": { duration: 600 } }
    });
    addLayer({
      id: "crew-pattern", type: "fill", source: "crew-cells",
      filter: ["!=", ["get", "p"], "crewpat-solid"],
      paint: { "fill-pattern": ["get", "p"], "fill-opacity": 0,
               "fill-opacity-transition": { duration: 600 } }
    });
    // The Highlight wave. One layer over the fill, filtered to the ring currently lit, so
    // the whole effect is a handful of `setFilter` calls and nothing is added to the style
    // while it runs. It starts matching nothing. Registered through `addLayer` like every
    // other id here -- a layer this file adds without recording took the whole mode down
    // once, see the note on LAYERS.
    map.addSource("crew-shine-src",
      { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    addLayer({
      id: "crew-shine", type: "fill", source: "crew-shine-src",
      paint: { "fill-color": "#ffffff", "fill-opacity": 0.5, "fill-antialias": false,
               "fill-opacity-transition": { duration: 240 } }
    });
    // the glow sits under the hairline so a border reads at low zoom without being fat
    // Pressure needs a second channel. A shade on a dark map is something you notice
    // afterwards; a dashed edge is something you see.
    addLayer({
      id: "crew-contested", type: "line", source: "crew-hot",
      filter: ["==", ["get", "kind"], "pushed"],
      paint: { "line-color": "#ffffff",
               "line-dasharray": [2, 1.6],
               "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.4, 14, 2.4],
               "line-opacity": 0,
               "line-opacity-transition": { duration: 600 } }
    });
    addLayer({
      // About to flip: solid and thicker than the dashed ring beside it, because a square
      // changing hands this week is not the same news as one somebody is riding.
      id: "crew-flipping", type: "line", source: "crew-hot",
      filter: ["==", ["get", "kind"], "flipping"],
      paint: { "line-color": "#ffffff",
               "line-width": ["interpolate", ["linear"], ["zoom"], 8, 2, 14, 3.4],
               "line-opacity": 0,
               "line-opacity-transition": { duration: 600 } }
    });
    addLayer({
      id: "crew-edge-glow", type: "line", source: "crew-edges",
      paint: { "line-color": ["get", "c"], "line-width": 7, "line-blur": 7,
               "line-opacity": 0 , "line-opacity-transition": { duration: 600 } }
    });
    addLayer({
      id: "crew-edge", type: "line", source: "crew-edges",
      paint: { "line-color": ["get", "c"],
               "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 9, 1.8, 14, 3],
               "line-opacity": 0, "line-opacity-transition": { duration: 600 } }
    });

    requestAnimationFrame(function () {
      if (!map.getLayer("crew-fill")) return;
      // The dip is a warning, not a disappearance. At 38% a contested tile read as almost
      // unowned, which is the wrong story: it is still theirs right up until the moment it
      // flips, and the map should say "someone is leaning on this", not "this is nearly gone".
      // The floor was 0.55 x 0.5 = 0.275 composite, which is readable for cyan and gone for
      // navy, maroon and olive. Half the palette is dark. The ladder still descends, it just
      // stops bottoming out: 0.70 keeps the darkest crew on the map while it fades.
      // The stencil, capped at 0.65 and never heavier than the colour it sits on.
      //
      // This was `op + 0.15`, so with the admin default of 0.75 the PATTERN rendered at 0.90
      // over a 0.75 fill -- the hatch was the most opaque thing on the map and read louder
      // than the colour it is supposed to qualify. Erwin called it looking at the real map.
      //
      // `min`, not a constant: an admin who dials the crews layer down to 0.3 should not get
      // patterns at twice the weight of the ground they are printed on. The pattern is the
      // second channel for telling two crews apart; it is not the first.
      paintBands();
      map.setPaintProperty("crew-edge", "line-opacity", 0.95);
      map.setPaintProperty("crew-edge-glow", "line-opacity", 0.35);
      map.setPaintProperty("crew-contested", "line-opacity", 0.8);
      map.setPaintProperty("crew-flipping", "line-opacity", 0.95);
      scopePulse();
      // Here as well as in `show()`. On a cold load the territory payload has not landed when
      // the panel opens, so `buildLayers` runs LATER than `show` and there was no crew layer
      // to put the heat underneath yet -- the move was skipped and the heatmap stayed on top
      // of the ground it is meant to sit beneath. Measured 106 of 107 layers with the panel
      // open. Both call sites, because either one can be the last to run.
      heatUnderTerritory(true);
      startPulse(pulse.features.length);
      // The basemap picker calls setStyle, style.load fires, and this runs again from
      // scratch. Without this the gold rings were torn down and never came back, and a crew
      // write did the same thing racily through reloadTerritory.
      showTargets(TARGETS);
    });

    buildEmblems();
    // Named, so they can be removed again. The two cursor handlers used to be inline
    // functions, which meant clearLayers could not take them off and every open of the mode
    // left three more delegated listeners behind, each running its own hit test on every
    // mouse move.
    map.on("zoom", onZoom);
    // `zoom` fires throughout an ease and stops when the ease does, so the last one lands
    // somewhere short of the camera's resting position -- and `declutterEmblems` compares
    // SCREEN positions, which keep moving after it. A reviewer measured the consequence:
    // two 34px badges 8px and 24px apart at rest, well inside the 27.2px the overlap test
    // uses, both still visible, one covering the corner of the other. The computation was
    // right and was never run at the position it was judging. `moveend` fires once the
    // camera has actually stopped, for a wheel ease, a drag, a `flyTo` and the `fitBounds`
    // that Highlight and the standings rows use.
    map.on("moveend", onZoom);
    map.on("click", "crew-fill", onCellClick);
    map.on("mouseenter", "crew-fill", cursorPointer);
    map.on("mouseleave", "crew-fill", cursorDefault);
    map.on("mousemove", "crew-fill", onCellHover);
    // The rings mostly sit on ground nobody holds, which has no crew-fill feature under it, so
    // hover and click were dead on exactly the squares the card points at.
    map.on("mousemove", "crew-target-hit", onTargetHover);
    map.on("click", "crew-target-hit", onTargetTap);
    // a tap anywhere else puts it away; without this the sticky tip only left on a drag
    map.on("click", dismissTip);
    map.on("mouseleave", "crew-target-hit", hideTip);
    map.on("mouseenter", "crew-target-hit", cursorPointer);
    map.on("mouseleave", "crew-target-hit", cursorDefault);
    map.on("mouseleave", "crew-fill", hideTip);
    map.on("movestart", hideTip);              // dragging the map is not resting on a square
  }

  /* ---------- emblems ---------- */

  // DOM markers rather than a symbol layer. A symbol's icon-size would have to be driven by
  // both the zoom and the region's own size at once, and markers give exact control for the
  // price of a few dozen divs: the emblem is sized to the square it sits in, every frame, so
  // it fills a 2x2 block the same way it fills a 6x6 one.
  function buildEmblems() {
    if (!TERR.regions) return;
    TERR.regions.forEach(function (r) {
      var crew = TERR.crews[r.c];
      if (!crew) return;
      var x = r.e[0], y = r.e[1], s = r.e[2];
      var lon = (tileLon(x, TERR.z) + tileLon(x + s, TERR.z)) / 2;
      var lat = (tileLat(y, TERR.z) + tileLat(y + s, TERR.z)) / 2;
      var el = document.createElement("div");
      el.className = "crewemb";
      el.innerHTML = '<img alt="" src="' + crew.emblem + '"/>'
                   + '<span class="crewemb-n">' + esc(crew.name) + "</span>";
      // The biggest patch, like the board, the join list and the crew's own page. This was
      // `crew.km2` -- everything they hold anywhere -- so the marker printed 209 km2 beside a
      // join row reading 137 for the same crew at the same moment.
      // Both numbers, same order as the join list and for the same reason: the tooltip was
      // quoting km2 alone, which is not what the standings are built on.
      el.title = crew.name + " · " + tiles(crew.best_tiles || crew.tiles || 0)
        + " · " + fmtKm2(crew.best_km2 != null ? crew.best_km2 : crew.km2);
      el.dataset.s = s;
      el.dataset.n = r.n || 1;              // region size, which decides how long it survives
      // NOT a tab stop. `pressable` gives a div a role, a name, Enter and Space, which is
      // right for a row inside a card and wrong for a marker on a map: about thirty of these
      // put the map between the dock and the panel, so opening Crews with a keyboard took 39
      // stops to get inside the thing you had just opened. The crew rows already fly to them,
      // which is the keyboard route that makes sense.
      pressable(el, crew.name, function (ev) {
        if (ev) ev.stopPropagation();
        // `openCrewDetail`, not `openCrew`. The second opens the panel and flies the map --
        // and on a phone the panel is 76% of the screen, so the fly-to happens entirely
        // behind it and the crew you tapped is never named. You tapped a crew's emblem and
        // got an unrelated card scrolled to the top. The popup's own Details button has done
        // the right thing for rounds: open, scroll to that crew's row, flash it.
        openCrewDetail(crew.slug);
      }, false);
      var m = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([lon, lat]).addTo(map);
      markers.push(m);
    });
    sizeEmblems();
  }

  // A ceiling as well as a floor. Zoomed in, a big crew's emblem reached about 250px and
  // covered six squares of the territory it was labelling, including the pressure shades
  // underneath it, which is the one thing the reader came to look at.
  // A ceiling in pixels is not a ceiling on a phone: 96px is a quarter of a 390px screen,
  // and with a border and a drop shadow it reads as a dialog rather than a badge.
  var EMBLEM_MAX = 96;

  function emblemCap() {
    return Math.min(EMBLEM_MAX, Math.round(window.innerWidth * 0.16));
  }

  function sizeEmblems() {
    if (!TERR) return;
    var z = map.getZoom();
    // a tile of the territory grid spans this many screen pixels at the current zoom
    var tilePx = 256 * Math.pow(2, z - TERR.z);
    markers.forEach(function (m) {
      var el = m.getElement(), s = +el.dataset.s || 1;
      var tiles = +el.dataset.n || 1;
      var px = Math.round(s * tilePx * 0.76);        // inset, so the emblem sits inside its edge
      // A big crew keeps its badge legible as you zoom out. Scaling purely with the tiles
      // meant the largest territories on the map went anonymous first, which is backwards:
      // the ones worth recognising from a distance are exactly the big ones. The floor grows
      // with the size of the region, so a sprawling crew stays identifiable over a country
      // while a four-tile crew still disappears when it should.
      var floor = tiles >= 40 ? 34 : tiles >= 15 ? 28 : tiles >= 6 ? 22 : 0;
      px = Math.min(Math.max(px, floor), emblemCap());
      el.style.width = el.style.height = px + "px";
      el.style.opacity = px < 16 ? 0 : 1;
      // the name goes when the badge is too small to carry it, not when the crew is big: the
      // floor above means a large crew can sit at 34px and still want its name
      el.classList.toggle("tiny", px < Math.max(44, floor + 10));
      el.dataset.px = px;
    });
    declutterEmblems();
  }

  /* One badge per place on the screen.

     The floors above keep a big crew legible as you zoom out, and nothing stopped two of them
     landing on the same pixels. Measured at 390x844 at the default world view: Holmenkollen
     Climb (rank 1, 137 km2) at (193, 489) 34px with Nordlys Collective (rank 12, 23 km2) at
     (197, 495) 28px -- the smaller crew's badge sitting ENTIRELY inside the bigger one's,
     leaving a 4px ring of the crew underneath; two Nordlys regions 1px and 5px apart; and
     Peripherique dropping three badges within 5px of each other. Ten emblems inside the
     viewport, four of them legible.

     So the biggest region in a cluster keeps the spot and the rest stand down until a zoom
     separates them. Region size rather than rank, for the same reason the floors use it: the
     badge worth seeing from a distance is the one labelling the most ground, and it is also
     the one whose own shape is still visible underneath. Hidden rather than merely stacked --
     a badge drawn under another badge is not a badge, and leaving it pressable means a tap
     landing on a crew the reader cannot see.

     O(n^2) over a few dozen markers, every frame, which is a few thousand comparisons and far
     cheaper than the `project` calls it already needs. */
  function declutterEmblems() {
    var kept = [];
    var all = markers.map(function (m) {
      var el = m.getElement();
      var px = +el.dataset.px || 0;
      return { el: el, px: px, n: +el.dataset.n || 1, p: map.project(m.getLngLat()) };
    }).sort(function (a, b) { return (b.n - a.n) || (b.px - a.px); });
    all.forEach(function (e) {
      var hide = false;
      if (e.px >= 16) {
        for (var i = 0; i < kept.length; i++) {
          // How much of the smaller badge is actually covered, rather than how close the two
          // centres are. The first version of this compared centre distance against 0.8 of
          // the mean half-width, which sounds equivalent and is not: at 34px it tolerates
          // nearly 7px of real overlap in each axis before it fires. A reviewer found the
          // gap it leaves -- two 34px badges overlapping 25x6, one sitting on the corner of
          // the other, 28px apart vertically against a 27.2px threshold, so it missed by
          // eight tenths of a pixel. Area answers the question the eye is asking.
          //
          // 8%: the 25x6 case is 13% and goes; a genuine corner graze of a few pixels is
          // about 1% and stays, which is the tolerance the old factor was reaching for.
          var ox = Math.min(e.p.x + e.px / 2, kept[i].p.x + kept[i].px / 2)
                 - Math.max(e.p.x - e.px / 2, kept[i].p.x - kept[i].px / 2);
          var oy = Math.min(e.p.y + e.px / 2, kept[i].p.y + kept[i].px / 2)
                 - Math.max(e.p.y - e.px / 2, kept[i].p.y - kept[i].px / 2);
          if (ox <= 0 || oy <= 0) continue;
          var small = Math.min(e.px, kept[i].px);
          if ((ox * oy) / (small * small) > 0.08) { hide = true; break; }
        }
      }
      if (hide) {
        e.el.classList.add("crewembhid");
        e.el.setAttribute("aria-hidden", "true");
      } else {
        e.el.classList.remove("crewembhid");
        e.el.removeAttribute("aria-hidden");
        if (e.px >= 16) kept.push(e);
      }
    });
  }

  /* ---------- interaction ---------- */

  // What a square is, in words. Shared so the thing that appears under the pointer and the
  // thing that appears when you click can never drift apart.
  function tileAt(lngLat) {
    var tx = Math.floor((lngLat.lng + 180) / 360 * Math.pow(2, TERR.z));
    var lat = lngLat.lat * Math.PI / 180;
    var ty = Math.floor((1 - Math.log(Math.tan(lat) + 1 / Math.cos(lat)) / Math.PI)
                        / 2 * Math.pow(2, TERR.z));
    return [tx, ty];
  }

  // Holder-neutral on purpose. These fire on every crew's fill, not only your own, and the
  // crew name sits directly above them: "Yours, comfortably" over a rival's ground was simply
  // a lie, and "Surrounded" over your own read as a warning when it means the opposite.
  function tileWords(band, tenths, y) {
    var km = tenths / 10;
    return [
      [t("crew.tile.safe"), t("crew.tile.pushed"), t("crew.tile.slipping"),
       t("crew.tile.fading"), t("crew.tile.ringed")][band],
      band === 4 ? t("crew.tile.ringedp")
        // 3 joins 1 and 2 rather than counting down to a deadline that no longer arrives:
        // a cold square is pinned at its floor, so what the holder has IS what a rival needs.
        : band >= 1 && band <= 3 ? t("crew.tile.needw", { v: effort(km, y) })
        // the words version is already a whole clause; only the figures need a sentence
        : SHOW_NUMBERS ? t("crew.tile.clear", { v: fmtKm(km) }) : margin(km, y),
      // the third slot is the figure behind the phrase, the same one the two cards print.
      // With the setting on the phrase is already the figure, and 3 and 4 have no distance.
      SHOW_NUMBERS || band === 4 ? "" : fmtKm(km)
    ];
  }

  /* ---------- resting on a square ----------
     Slow on purpose. The delay is the whole design: anything quicker turns into a label that
     chases the pointer across the map while you are trying to look at the shapes. */
  var HOVER_MS = 650;
  var hoverTimer = null, hoverTip = null, hoverKey = "";

  // Where a tip sits, for both of them. Two call sites, one stylesheet, one 14px transform
  // offset -- and until now one right-edge guard copied into each with no left-edge
  // counterpart. A reviewer measured the tapped tip at a left edge of -14px from a tap in the
  // centre of a 390px map: the band stripe and the first character of every line off-screen.
  function placeTip(el, px) {
    el.style.left = px.x + "px";
    el.style.top = px.y + "px";
    // `offsetWidth` is only the box's real width because the stylesheet gives it
    // `width: max-content`. Without that, an absolutely positioned box with just a
    // `max-width` shrink-to-fits against the room to its right, so this read the COLLAPSED
    // width near an edge -- 36px instead of 210 -- and the whole function then reasoned about
    // a box that no longer existed. See the note on `.crewtip` in crews.css.
    var tw = el.offsetWidth, vw = window.innerWidth, gut = 8;
    // Flipping is a preference. Being on the map is not, so the far side has to have room for
    // it before the tip is sent over there.
    var flip = px.x + tw + 30 > vw;
    if (flip && px.x - tw - 14 < gut) flip = false;
    if (flip) el.classList.add("left");
    // `left` is the anchor and the transform supplies the offset, so the correction for a box
    // that fits on neither side belongs on `left`.
    var lead = flip ? px.x - tw - 14 : px.x + 14;
    var off = lead < gut ? gut - lead
            : lead + tw > vw - gut ? vw - gut - tw - lead : 0;
    if (off) el.style.left = (px.x + off) + "px";
    // Near the top of the map there is nothing above the pointer to hang it from. The tapped
    // tip did this and the resting one did not, which is the same drift as the missing guard.
    if (px.y < el.offsetHeight / 2 + 8) el.classList.add("below");
  }

  function hideTip() {
    clearTimeout(hoverTimer);
    hoverTimer = null;
    hoverKey = "";
    if (hoverTip) { hoverTip.remove(); hoverTip = null; }
  }

  function onCellHover(e) {
    if (!TERR || !e.features || !e.features[0]) return;
    var xy = tileAt(e.lngLat);
    var key = xy[0] + ":" + xy[1];
    if (key === hoverKey) return;               // same square, leave the timer alone
    hideTip();
    hoverKey = key;
    var p = e.features[0].properties;
    var info = TILEINFO[key];
    if (!info) return;
    var px = e.point;
    // the latest point inside the square, not the one the pointer first crossed into it at
    map.once("mousemove", function (ev) { px = ev.point; });
    hoverTimer = setTimeout(function () {
      if (hoverKey !== key || !map.getLayer("crew-fill")) return;
      var w = tileWords(info[0], info[1], xy[1]);
      var el = document.createElement("div");
      el.className = "crewtip b" + info[0];
      el.innerHTML = "<b>" + esc(p.name) + "</b><span>" + esc(w[0]) + "</span><span>"
        + esc(w[1]) + (w[2] ? " <i>" + esc(w[2]) + "</i>" : "") + "</span>"
        + (info[2] ? "<span><em>" + esc(t("crew.tile.fresh")) + "</em></span>" : "");
      map.getCanvasContainer().appendChild(el);
      placeTip(el, px);
      hoverTip = el;
    }, HOVER_MS);
  }

  var POPUP = null;

  // A ring answers for itself, out of the row that drew it.
  function onTargetHover(e) {
    if (!TERR || !e.features || !e.features[0]) return;
    var xy = tileAt(e.lngLat);
    var key = "t" + xy[0] + ":" + xy[1];
    if (key === hoverKey) return;
    hideTip();
    hoverKey = key;
    var row = null;
    TARGETS.concat(LOSING).forEach(function (x) {
      if (x.x === xy[0] && x.y === xy[1]) row = x;
    });
    if (!row) { hoverKey = ""; return; }
    var px = e.point;
    map.once("mousemove", function (ev) { px = ev.point; });
    hoverTimer = setTimeout(function () {
      if (hoverKey !== key) return;
      hoverTip = ringTip(row, px);
    }, HOVER_MS);
  }

  // A tap is not a hover: no delay, and it stays put until the next tap or a drag.
  // Named, so clearLayers can take it off again. See the note there.
  function dismissTip(e) {
    if (!hoverTip || hoverKey.indexOf("tap") !== 0) return;
    if (map.getLayer("crew-target-hit")
        && map.queryRenderedFeatures(e.point, { layers: ["crew-target-hit"] }).length) {
      return;
    }
    hideTip();
  }

  function onTargetTap(e) {
    if (!TERR) return;
    var xy = tileAt(e.lngLat);
    var row = null;
    TARGETS.concat(LOSING).forEach(function (x) {
      if (x.x === xy[0] && x.y === xy[1]) row = x;
    });
    if (!row) return;
    hideTip();
    hoverKey = "tap" + xy[0] + ":" + xy[1];
    hoverTip = ringTip(row, e.point, true);
  }

  function ringTip(row, px, sticky) {
      var el = document.createElement("div");
      var losing = row.band !== undefined;
      el.className = "crewtip " + (losing ? "bl" : "bt") + (sticky ? " tap" : "");
      el.innerHTML = (row.at ? '<b class="crewtipat">' + esc(row.at) + "</b>" : "")
        + "<b>" + esc(losing
            ? t("crew.lose.gap", { v: effort(row.need, row.y) })
            : row.blocked ? t(row.first ? "crew.targets.got" : "crew.targets.blocked")
            : effort(row.need, row.y)) + "</b>"
        + "<span>" + esc(losing
            ? t(row.band === 3 ? "crew.lose.cold"
                : row.band === 2 ? "crew.lose.now" : "crew.lose.soon")
            // The card calls this square the prize -- a block square the crew already
            // out-rides comes off its holder the moment the block lands -- and the tip
            // called it theirs. Same square, same sentence now.
            : row.first && row.blocked && row.held_by && row.held_name
            ? t("crew.targets.flips", { name: row.held_name })
            : row.held_by && row.held_name
            ? t("crew.targets.taken", { name: row.held_name })
            : row.held_by ? t("crew.targets.takenby")
            : t("crew.tile.free")) + "</span>"
        // with its count: this printed the literal "{n}" on the map, on the one square the
        // whole card is shouting about
        + (row.kills ? "<span><em>"
           + esc(t("crew.targets.kills", { v: tiles(row.lost || 0) })) + "</em></span>" : "")
        + (row.first ? "<span><em>" + esc(t("crew.targets.first")) + "</em></span>" : "");
      map.getCanvasContainer().appendChild(el);
      placeTip(el, px);
      return el;
  }

  function onCellClick(e) {
    // A target ring over a rival's ground is both a crew-fill feature and a crew-target-hit
    // feature, and both layers had a click handler, so the one tap the card is shouting
    // about opened two overlapping boxes. The ring is the more specific answer.
    if (map.getLayer("crew-target-hit")
        && map.queryRenderedFeatures(e.point, { layers: ["crew-target-hit"] }).length) {
      return;
    }
    var f = e.features && e.features[0];
    if (!f) return;
    var p = f.properties;
    // The band is the answer to "am I actually taking this off them?". Without it the only
    // signal is the shade, and a shade on its own is something you notice after the fact.
    // the clicked tile, not the shape it belongs to
    var xy = tileAt(e.lngLat), tx = xy[0], ty = xy[1];
    var info = TILEINFO[tx + ":" + ty] || [p.band || 0, 0];
    // the number is the whole point: "about to flip" without it is a warning with no content
    var words = tileWords(info[0], info[1], ty);
    var state = words[0], detail = words[1], fig = words[2];
    hideTip();
    if (POPUP) POPUP.remove();
    // How long they have held it. The server has computed this all along and nothing read it,
    // so the mode had no past tense at all: who holds what, never for how long.
    api("GET", "/api/v1/territory/at?lat=" + e.lngLat.lat + "&lon=" + e.lngLat.lng)
      .then(function (r) {
        if (!r.ok || !r.body || !r.body.since || !POPUP) return;
        var el = POPUP.getElement && POPUP.getElement();
        var slot = el && el.querySelector(".crewpop-since");
        if (slot) slot.textContent = t("crew.tile.since", { d: heldFor(r.body.since) });
      });
    // Anchored so it opens INTO the strip of map you can actually see.
    //
    // The panel is fixed over the bottom of the screen, and `#map` is `position: fixed` with
    // `z-index: 0` -- which is a stacking context, so a popup inside it can never be painted
    // above the panel however high its own z-index goes. I raised that z-index last round and
    // verified it by clicking one square, where the popup happened to anchor upward and
    // nothing overlapped, and called it fixed. A reviewer then measured a click that anchors
    // DOWNWARD: 44px of an 80px popup behind the panel, with the two lines worth reading --
    // how far it is to flip, and how long it has been held -- in the hidden half.
    //
    // z-index was never the lever. Where the popup opens is: `anchor: "bottom"` puts it above
    // the point you pressed, so it grows into the visible strip instead of under the panel.
    // Only when the panel is actually covering that point, so a click on open map keeps
    // MapLibre's own placement.
    var popOpts = { closeButton: false, className: "crewpop", offset: 10 };
    var panelEl = document.querySelector(".panel.open");
    if (panelEl) {
      var pr = panelEl.getBoundingClientRect();
      var pt = map.project(e.lngLat);
      // 170px is about the tallest this popup gets; below that line a downward popup would
      // run into the panel.
      if (pt.y + 170 > pr.top) popOpts.anchor = "bottom";
    }
    POPUP = new maplibregl.Popup(popOpts)
      .setLngLat(e.lngLat)
      .setHTML('<div class="crewpop-in"><img src="/api/v1/crews/' + encodeURIComponent(p.slug)
        + '/emblem" alt=""/><div><b>' + esc(p.name) + "</b><span>" + esc(state)
        + "</span><span>" + esc(detail) + (fig ? " <i>" + esc(fig) + "</i>" : "")
        + '</span><span class="crewpop-since"></span>'
        + "</div></div>"
        // The two things there are to do with a crew you just tapped. In the popup rather
        // than behind a hover delay or a long press: this is a phone-first mode, phones have
        // no hover, and a long press is both undiscoverable and the OS's own gesture. The
        // popup is already on screen and already names the crew, so it is where the actions
        // belong -- and it works identically with a thumb and with a mouse.
        + '<div class="crewpopacts">'
        + '<button type="button" class="crewpopb" data-hl="' + esc(p.slug) + '">'
        + esc(t("crew.pop.highlight")) + "</button>"
        + '<button type="button" class="crewpopb" data-det="' + esc(p.slug) + '">'
        + esc(t("crew.pop.details")) + "</button></div>")
      .addTo(map);
    bindPopActs();
  }

  // Wired after the popup is in the DOM; MapLibre builds its element on `addTo`.
  function bindPopActs() {
    var el = POPUP && POPUP.getElement && POPUP.getElement();
    if (!el) return;
    var hl = el.querySelector("[data-hl]"), det = el.querySelector("[data-det]");
    // The popup goes, same as its sibling below. Highlight means "show me this patch on the
    // map", and on a phone the popup is 188x161 parked directly over the ground it has just
    // lit -- so the one gesture whose entire output is an animation on the map covered the
    // animation with the control that started it. The asymmetry was two adjacent lines: one
    // handler dismissed the popup and the other did not.
    if (hl) hl.onclick = function () {
      var slug = hl.getAttribute("data-hl");
      if (POPUP) { POPUP.remove(); POPUP = null; }
      shine(slug);
    };
    if (det) det.onclick = function () {
      var slug = det.getAttribute("data-det");
      if (POPUP) { POPUP.remove(); POPUP = null; }
      openCrewDetail(slug);
    };
  }

  // The framing for Highlight, which has to know about the key.
  //
  // The padding was four constants, and the key panel is not a constant: open, it is 198x306
  // at (10,434) on a 390x844 screen -- about a quarter of the map area under the champions
  // card. A reviewer pixel-diffed the shine frames against a cleared frame and found that at
  // t=1500ms the ONLY changed pixels in the map region were x 18-46: the whole animation ran
  // behind the key. Closing the key made the same animation plainly visible. The one gesture
  // whose entire output is an animation was framing it underneath a panel.
  //
  // So the bottom padding grows to clear the key when the key is open, which puts the patch
  // in the band above it. Bottom only: padding the left as well would corner the patch into
  // whatever is left and zoom it out to nothing. Clamped to 45% of the viewport so a short
  // screen cannot end up with no framing area at all, and `fitBounds` throws if the padding
  // does not fit -- which is what the `try` around it was already there for.
  function shinePadding() {
    var pad = { top: 90, bottom: 120, left: 50, right: 50 };
    var key = document.querySelector(".crewkey");
    if (!key || !key.hasAttribute("open")) return pad;
    var r = key.getBoundingClientRect();
    if (!r.height) return pad;
    var want = Math.round(window.innerHeight - r.top + 12);
    pad.bottom = Math.max(pad.bottom, Math.min(want, Math.round(window.innerHeight * 0.45)));
    return pad;
  }

  /* ---------- Highlight ----------
     The crew's biggest patch lit from its middle outward. Scoped to the patch under the
     finger rather than every square the crew holds: a crew with ground in two cities would
     spend most of the animation off screen, lighting up places you cannot see.
     The wave is a per-square delay keyed on distance from the patch's centre, which is the
     same trick the sign-in QR uses on its modules. Nothing moves under prefers-reduced-motion;
     the CSS decides that, not this. */
  function shine(slug) {
    if (!TERR || !map.getLayer("crew-fill")) return;
    var idx = -1;
    TERR.crews.forEach(function (c, i) { if (c.slug === slug) idx = i; });
    if (idx < 0) return;
    var pts = [], i;
    for (i = 0; i < TERR.cells.length; i += 5) {
      if (TERR.cells[i] === idx) pts.push([TERR.cells[i + 1], TERR.cells[i + 2]]);
    }
    var patch = biggestPatch(pts);
    if (!patch.length) return;
    var cx = 0, cy = 0;
    patch.forEach(function (q) { cx += q[0]; cy += q[1]; });
    cx /= patch.length; cy /= patch.length;
    var far = 1;
    patch.forEach(function (q) {
      far = Math.max(far, Math.hypot(q[0] - cx, q[1] - cy));
    });
    // Rings outward from the middle: each square waits for the ring it is on.
    var rings = {};
    patch.forEach(function (q) {
      var r = Math.round(Math.hypot(q[0] - cx, q[1] - cy) / far * SHINE_RINGS);
      (rings[r] = rings[r] || []).push(q);
    });
    // Bring the patch into view first. Without this the button was a literal no-op whenever
    // the crew's biggest patch was not the one under your finger -- tap a Berlin square of a
    // crew whose biggest patch is in Oslo and the whole animation played off screen. And on a
    // phone the popup sits directly over the square you tapped, so even the right patch was
    // partly behind it.
    var b = new maplibregl.LngLatBounds();
    patch.forEach(function (q) {
      b.extend([tileLon(q[0], TERR.z), tileLat(q[1], TERR.z)]);
      b.extend([tileLon(q[0] + 1, TERR.z), tileLat(q[1] + 1, TERR.z)]);
    });
    try {
      map.fitBounds(b, { padding: shinePadding(), maxZoom: 12.5, duration: 700,
                         essential: true });
    } catch (e) {}

    // Each ring holds, rather than flashing past. The whole thing used to be over in 770ms
    // with the rings 70ms apart, which is below the threshold at which anybody registers a
    // deliberate animation: a reviewer screenshotted it at one second and got an identical
    // frame, and had to force a capture at 250ms to see anything at all. It starts after the
    // camera has moved, for the same reason.
    var step = 150, lead = 760;
    Object.keys(rings).forEach(function (r) {
      setTimeout(function () { paintShine(rings[r]); }, lead + Number(r) * step);
    });
    // and the last ring is left lit for a beat before it clears
    setTimeout(function () { paintShine(patch); }, lead + (SHINE_RINGS + 1) * step);
    setTimeout(function () { paintShine([]); }, lead + (SHINE_RINGS + 7) * step);
  }

  var SHINE_RINGS = 10;

  // The ring currently lit, as one polygon set on its own source. A filter on `crew-cells`
  // cannot do this: that source merges every square of a crew and band into a single
  // MultiPolygon, so there is no per-square feature there to match.
  function paintShine(cells) {
    var src = map.getSource && map.getSource("crew-shine-src");
    if (!src) return;
    src.setData(cells.length
      ? { type: "FeatureCollection", features: [{
            type: "Feature", properties: {},
            geometry: { type: "MultiPolygon",
                        coordinates: cells.map(function (q) {
                          return tileRing(q[0], q[1], TERR.z);
                        }) } }] }
      : { type: "FeatureCollection", features: [] });
  }

  // Every visible string goes through the page's translator. They live in web/i18n.py EN,
  // which is the source the translation workflow regenerates the other locales from.
  function t(key, vars) {
    var out = (H.t ? H.t(key, vars) : key);
    return out;
  }

  // The row's own columns, joined. `textContent` runs them together -- "a few streets 1.4
  // kmParis" -- which is the same jam the visible separators were given real text for; the
  // columns themselves carry no text between them, because the grid does that job on screen.
  function rowLabel(el) {
    return Array.prototype.map.call(el.children, function (c) {
      // Not the parts that are hidden from the accessibility tree. The board's rank cue is
      // `aria-hidden` because it re-states a number already in the row, and this function
      // builds its string out of `textContent` -- which does not care -- so a reader heard
      // "Cykelslangen▼5", the triangle as a character. `aria-hidden` keeps an element out of
      // the tree; it cannot keep its text out of a string somebody else assembles.
      if (c.getAttribute && c.getAttribute("aria-hidden") === "true") return "";
      var t = c.cloneNode(true);
      if (t.querySelectorAll) {
        Array.prototype.forEach.call(t.querySelectorAll('[aria-hidden="true"]'),
                                     function (h) { h.remove(); });
      }
      return t.textContent.replace(/\s+/g, " ").trim();
    }).filter(Boolean).join(" · ");
  }

  // A row that can be clicked can be reached. These are `<div>`s and `<tr>`s rather than
  // buttons -- they carry a grid of their own and a button would fight it -- so they get the
  // three things a button gets for free: a tab stop, a name for what pressing them does, and
  // Enter and Space. 35 of the 46 handlers in this panel had none of them, including every
  // row on the card that says "Pick one to find it".
  function pressable(el, label, fn, focusable) {
    if (!el) return;
    // `focusable === false` for things that are reachable another way and would otherwise
    // flood the tab order -- the map's emblem markers, thirty of them between the dock and
    // the panel.
    el.tabIndex = focusable === false ? -1 : 0;
    el.setAttribute("role", "button");
    if (label) el.setAttribute("aria-label", label);
    el.onclick = fn;
    el.onkeydown = function (ev) {
      if (ev.key !== "Enter" && ev.key !== " " && ev.key !== "Spacebar") return;
      // Space scrolls the panel otherwise, which moves the thing you were aiming at
      ev.preventDefault();
      fn.call(el, ev);
    };
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------- the panel ---------- */

  // What is already in flight, keyed by method and path. Nothing debounced a write, so a
  // double-click on Join sent two POSTs -- 200 then 400 `already_in_crew` -- and whichever
  // landed last decided what the rider saw. Every write in this feature goes through here,
  // so this one set covers join, create, leave, disband, promote, remove and approve.
  var inFlight = {};

  function api(method, path, body) {
    // The BODY is part of the key. Keyed on method and path alone, two different riders
    // decided through one endpoint were one key: Let in on row 1, then Let in on row 2 four
    // hundred milliseconds later, sent ONE request, and the second click got the first's
    // promise, saw `r.ok` and repainted as though it had worked -- rider two never decided,
    // nothing said so. `/decide` also carries `accept`, so Let in followed by No returned an
    // acceptance to a decline handler. A double-click on ONE button is still one write,
    // because its body is identical; two riders are two writes, because they are two
    // requests.
    var key = method + " " + path + " " + (body === undefined ? "" : JSON.stringify(body));
    if (method !== "GET" && inFlight[key]) return inFlight[key];
    var opt = { method: method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    var p = fetch(path, opt).catch(function () {
      // No catch at all before this: a dropped request rejected, the .then never ran, and
      // "Create crew" stayed disabled with nothing on screen to say why.
      return { ok: false, status: 0, json: function () { return Promise.resolve({}); } };
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        var detail = b && b.detail;
        if (typeof detail === "string") {
          try { detail = JSON.parse(detail); } catch (e) { detail = { detail: detail }; }
        }
        return { ok: r.ok, status: r.status, body: b, err: r.ok ? null : (detail || {}) };
      });
    });
    if (method !== "GET") {
      inFlight[key] = p;
      // Released however it settles, including the catch above, so one failed write cannot
      // wedge that endpoint for the rest of the session.
      p.then(function () { delete inFlight[key]; },
             function () { delete inFlight[key]; });
    }
    return p;
  }

  // The crew's own emblem at name size. Same source the map and the podium draw.
  function emb(slug, size) {
    var sz = size || 16;
    // Carries its slug so one delegated listener can open the sheet for whichever crew's
    // emblem was pressed -- the board, the browse list and the board's own footer line all
    // emit these, and none of them wants its own handler.
    return '<img class="crewembsm crewembgo" data-emb="' + esc(slug)
      + '" style="width:' + sz + "px;height:" + sz
      + 'px" alt="" src="/api/v1/crews/' + encodeURIComponent(slug) + '/emblem"/>';
  }

  // Only the identity picker, where the colour and the pattern are what you are choosing.
  function swatch(colour, pattern, size) {
    var sz = size || 18;
    return '<span class="crewsw" style="width:' + sz + "px;height:" + sz + "px;background:"
      + esc(colour) + '" data-p="' + esc(pattern) + '"></span>';
  }

  // "EUC Planet" wherever it appears in a translated line, turned into a link to it. The
  // brand name is the one part of these strings no locale changes, which is what makes one
  // replace enough.
  var APP_URL = "https://eucplanet.ried.no";
  function appLink(text) {
    return String(text).replace(
      "EUC Planet",
      '<a href="' + APP_URL + '" target="_blank" rel="noopener">EUC Planet</a>');
  }

  // One section of the manual: a heading and its rules. The substitutions are listed once,
  // here, so a rule added to any section gets them without anybody remembering to pass them.
  // `{v}` on the cooldown arrives pre-pluralised from `days()` -- a sentence using it must not
  // force a case on it, which is the trap this file has notes about elsewhere.
  function rulesSection(heading, keys) {
    return "<h5>" + t(heading) + "</h5>"
      + '<ol class="crewrules">'
      + keys.map(function (k) {
          // `{v}` means the cooldown on the one rule that is about the cooldown, and the
          // size of a square everywhere else. Two meanings for one placeholder is a trap, so
          // it is keyed on the rule rather than passed blind.
          var v = (k === "crew.how.cool") ? days(COOLDOWN_DAYS) : squareKm();
          return "<li>" + t(k, { n: k === "crew.how.size" ? MAX_MEMBERS : SEED,
                                 d: WINDOW_DAYS, c: fmtKm(capKm()), v: v }) + "</li>";
        }).join("")
      + "</ol>";
  }

  function explainer() {
    // Content only. This is what the (?) hands to the modal; it used to be a <details> in
    // the card stack, which is a disclosure pretending to be a dialog -- it still took its
    // place in the flow and the page behind it was still usable.
    return '<div class="crewhow" id="crewhow">'
      // What it IS, before any rule for playing it.
      + '<p class="crewintro">' + t("crew.how.intro") + "</p>"
      // Three sections, each a numbered list, because these are the rules of a game and eight
      // paragraphs at one weight is a wall however good the words are. Every number is read
      // here, at render time, from the config the server sent: change the window, the block,
      // the cooldown, the weekly cap or the member limit in admin and this changes with it.
      + rulesSection("crew.how.s1", ["crew.how.1", "crew.how.2", "crew.how.3", "crew.how.6"])
      + rulesSection("crew.how.s2", ["crew.how.4", "crew.how.7", "crew.how.8"])
      // "Your crew" was two lines and both were about leaving it. Nothing said who runs a
      // crew, what an officer is, or who can change what -- while the roster offers a
      // "Make officer" button, and the glyph it produces is explained nowhere.
      + rulesSection("crew.how.s3", ["crew.how.roles", "crew.how.5", "crew.how.cool"]
          // Only when there is one. With no cap there is no rule, and "unlimited" would be a
          // sentence about nothing.
          .concat(MAX_MEMBERS > 0 ? ["crew.how.size"] : []))
      // The five states, in the order a square moves through them. They were defined only in
      // the key below -- five chips of two words -- and two of those two words meant nothing
      // to anybody who had not read the model.
      // Six chips in the key, six entries here. There were five: "Taken this week" had a
      // swatch and no explanation, for the state that is the most interesting thing on the map.
      + rulesSection("crew.how.s4", ["crew.how.c0", "crew.how.c1", "crew.how.c2",
                                     "crew.how.c3", "crew.how.c4", "crew.how.c5"])
      // What the figures mean. `crew.board.sub` used to close the manual as a bare paragraph
      // with no heading and no antecedent -- it is the BOARD's subtitle, printed where
      // nothing had mentioned the board. Two reviewers read it as a non-sequitur, and
      // separately asked why a crew shows "91 squares", "2 patches", "23 km2" and "77 km2"
      // with nothing reconciling them. That is this section.
      // The swatches belong to the section that names them. They were emitted last, so the
      // reader got six colour descriptions with no colours, then two paragraphs of
      // arithmetic, then six unheadered chips 422px further down -- measured by a reviewer
      // as an orphan under the wrong heading.
      + legendHTML()
      + rulesSection("crew.how.s5", ["crew.how.n1", "crew.how.n2"])
      + "</div>";
  }

  // What changed, not only what is. The payload already marks ground taken inside FRESH_DAYS,
  // so this is one pass over an array the browser has had all along, and it is the difference
  // between a table and a race.
  function freshByCrew() {
    var out = {};
    if (!TERR || !TERR.cells) return out;
    for (var i = 0; i < TERR.cells.length; i += 5) {
      if (!isFresh(TERR.cells[i + 3] || 0)) continue;
      var c = TERR.crews[TERR.cells[i]];
      if (c) out[c.slug] = (out[c.slug] || 0) + 1;
    }
    return out;
  }

  var FRESH = {};

  // When the world was last drawn, so the panel can answer "I rode, where is it".
  var DRAWN = null;

  function drawnLine() {
    // The server does the arithmetic: the browser's clock is not the one the file was
    // stamped by, and on a phone it is frequently minutes out.
    if (!DRAWN || DRAWN.drawn_s_ago == null) return "";
    var every = DRAWN.every || 3600;
    var mins = Math.round((every - DRAWN.drawn_s_ago) / 60);
    // Overdue by MORE than an interval is not "any second", it is a map that has stopped.
    // A reviewer measured this instance at 7h14m against a one-hour cadence, printing "New
    // ground lands any second" -- which it had been saying for six hours and would say for
    // ever, because every value <= 1 collapsed into it. Saying how old the map is degrades
    // honestly whatever the rebuild is doing, and is the thing the panel never answered:
    // it said when the next one lands and never how old this one is.
    if (DRAWN.drawn_s_ago > every * 1.5) {
      // Whole hours, floored. `heldFor` works in days and would call a seven-hour-old map
      // "new", which is the opposite of the thing being said.
      var hrs = Math.max(1, Math.floor(DRAWN.drawn_s_ago / 3600));
      return '<p class="hint crewdrawn">' + esc(t("crew.drawn.old", { n: hrs })) + "</p>";
    }
    if (mins <= 1) return '<p class="hint crewdrawn">' + t("crew.drawn.soon") + "</p>";
    return '<p class="hint crewdrawn">' + t("crew.drawn.in", { n: mins }) + "</p>";
  }

  // The board's markup is chosen from the width when it is built -- "13 squares" and "1 new
  // this week" on a desktop, "13" and "+1" on a phone -- and nothing rebuilt it when the
  // window changed. Narrow a desktop window, or rotate a phone, and the desktop strings stayed
  // in a phone-width card: names wrapped to three lines and the last column printed past the
  // card's own border onto the panel background. Only on a crossing, so a drag does not
  // re-render on every pixel.
  // Seeded from the width at load, not from the first event. Starting at null meant the
  // first resize was swallowed AND recorded, and a phone rotation fires exactly one resize
  // event -- so the case this listener exists for was the one case it never handled. A
  // reviewer measured the board still holding its desktop strings and painting 41px outside
  // its own card after a single rotation.
  var LASTTIGHT = window.innerWidth <= 560;
  window.addEventListener("resize", function () {
    var now = window.innerWidth <= 560;
    if (now === LASTTIGHT) return;
    LASTTIGHT = now;
    if (document.querySelector(".crewboard")) show();
  });

  // Which way a crew is going, as a glyph and a number. Every reviewer this round said some
  // version of "nothing shows change" and named the board's stillness as what holds the fun
  // score down; this is the cheapest true answer to it.
  //
  // `aria-hidden`, and deliberately: the arrow is a second reading of the rank that is already
  // announced beside it, and "up two places" in nineteen languages is a sentence this did not
  // need. A sighted reader gets the movement, a screen reader gets the position, and neither
  // gets a translation written in a hurry.
  //
  // Nothing is drawn for a crew that has not moved, or that has no previous rank -- a crew
  // founded since the last rebuild, and every crew on the first rebuild after this shipped. An
  // arrow on every row is the badge-on-every-row mistake this file has made twice already, and
  // an arrow drawn from a missing number is a guess.
  function moved(e, i) {
    if (e == null || e.prev_rank == null) return "";
    var was = e.prev_rank, now = (typeof i === "number" ? i + 1 : null);
    if (now == null || was === now) return "";
    var up = now < was;
    return '<span class="crewmoved ' + (up ? "up" : "down") + '" aria-hidden="true">'
      + (up ? "\u25b2" : "\u25bc") + Math.abs(was - now) + "</span>";
  }

  function rankingHTML(rows) {
    if (!rows || !rows.length) {
      return '<div class="empty">' + t("crew.empty") + "</div>";
    }
    FRESH = freshByCrew();
    if (!H.podList) return plainRank(rows);
    // 560 is the phone breakpoint the stylesheet and the fly-to already use. The podium is
    // exempt: podList hands the same val and sub to the cards and to the rows below them.
    // The CARD this renders into, not the window. `window.innerWidth <= 560` meant that at
    // 1280 the board asked for the long labels -- "55 squares", "+55 squares this week" --
    // and got a 435px table to put them in, because every card in this panel is capped at a
    // 31rem measure however wide the window is. Rows wrapped to three and four lines and ran
    // 75px tall against a 22px line box. The question was never how big the window is.
    var col = Math.min(panel ? panel.clientWidth : window.innerWidth, 528) - 32;
    var tight = col <= 520;
    var top3 = rows.slice(0, 3);
    // below the podium, which draws its own emblem above the name
    var isRow = function (e) { return top3.indexOf(e) < 0; };
    var short = function (e) { return tight && isRow(e); };
    return H.podList(rows, {
      iconFn: function (e) {
        return '<img class="crewpodemb crewembgo" data-emb="' + esc(e.slug)
          + '" alt="" src="' + e.emblem + '"/>'; },
      // the swatch is the crew's identity on the map, so it belongs beside every name
      label: function (e) {
        // The position comes from `rows`, not from an argument: `podList` calls `label(e)`
        // with no index, in the podium and in the table both. Written as `label(e, i)` this
        // read `undefined` and `moved()` returned "" on every row forever -- a feature that
        // silently does nothing, which is the shape of bug this file keeps paying for.
        return (isRow(e) ? emb(e.slug, 16) + " " : "") + esc(e.name)
          + moved(e, rows.indexOf(e));
      },
      // squares, because that is what the board is sorted on and a square is the same amount
      // of riding everywhere. The area sits underneath, where it informs without ranking.
      val: function (e) {
        var n = e.best_tiles || e.tiles;
        if (short(e)) return n.toLocaleString();
        // The figure big, its unit quiet. One string from the translator, so the digits are
        // found wherever the locale puts them rather than split on a space.
        return esc(tiles(n)).replace(/(\d[\d.,  ]*)/,
                                     '<b class="crewpodn">$1</b>');
      },
      sub: function (e) {
        var gained = FRESH[e.slug] || 0;
        // No km2 here. The board ranks on squares, and area beside it inverted the ranking
        // two rows apart: 29 squares at 21 km2 above 20 squares at 120 km2. Area lives on the
        // crew's own card and in the popup, where nothing is being compared.
        // `tiles()`, so the noun carries the agreement. As a bare count this read
        // "1 новых на этой неделе" on five rows of the default board.
        // Counted inside the region the board RANKS on, not across the whole holding: the
        // row said "29 squares · +49 squares this week", because the figure is the biggest
        // contiguous patch and the delta was every fresh cell a crew held. Two quantities,
        // one noun, a plus between them. The note four lines above says area beside squares
        // inverted the ranking two rows apart, and the same reasoning was never applied here.
        // The rebuild counts it, because the components only exist there and a second flood
        // fill per crew per board render is not worth the bytes it would save. My first
        // attempt at this clamped the whole-holding count at the ranked figure, which is a
        // number true of nothing: 49 fresh cells over 3 patches printed "+29" beside a
        // 29-square patch, reading as though the entire block had been won this week.
        // The clamp stays as the fallback for a row from an older rebuild -- stale beats
        // wrong -- and `best_fresh` is used wherever the rebuild has supplied it.
        if (e.best_fresh != null) gained = e.best_fresh;
        else if (gained > (e.best_tiles || 0)) gained = e.best_tiles || 0;
        var full = t("crew.board.gained", { v: tiles(gained) });
        if (short(e)) {
          return gained ? '<span class="crewgain" title="' + esc(full) + '">+'
            + gained.toLocaleString() + "</span>" : "";
        }
        return '<span class="crewarea">'
          + (e.regions > 1 ? plural(null, "crew.patches.few", "crew.patches", e.regions) : "") + "</span>"
          + (gained ? ' <span class="crewgain">' + esc(full) + "</span>" : "");
      },
      click: true
    });
  }

  // Every mark the map draws gets a chip, and nothing else does. This list fell out of step
  // with the map four times, every time because a state was added to one and not the other,
  // and once because a chip described a mark the map has never drawn. The styles live in one
  // block in crews.css now rather than in a rule and a later override of that rule, which is
  // what made it so easy to add to the wrong half.
  // In the reader's own colours when they have some. The key was demonstrating the ladder
  // in the panel's pink, which is a colour no crew on the map is allowed to use, and flat,
  // while three crews in four carry a pattern.
  function myInk() {
    return ME && ME.crew ? [ME.crew.colour, ME.crew.pattern] : ["", ""];
  }

  function band(n, key) {
    var ink = myInk();
    return '<span class="b' + n + '"><i style="opacity:' + chipOp(n)
      + (ink[0] ? ";background:" + esc(ink[0]) : "") + '"'
      + (ink[1] ? ' data-p="' + esc(ink[1]) + '"' : "") + "></i>" + t(key) + "</span>";
  }

  /* ---------- the key, on the map ----------

     Both reviewers put this at the top of why the mode is not fun: the thing you are
     looking at means nothing. Gold outlines, white dashed outlines, a cyan halo, six shades
     of one colour and two hatch patterns, and the only explanation was behind the panel,
     behind a (?) and a scroll. "The game is illegible while you're playing it."

     So the key is on the map, and exactly when the map is what you can see: crew mode on and
     the panel closed. It is the same `legendHTML` the manual prints -- one definition, so the
     key and the manual cannot drift -- and it starts shut, because a permanent six-row
     overlay on a phone is worse than no key at all. Whether it is open is remembered per
     browser; it is a convenience, not state anybody else needs.
  */
  var KEYBOX = null;

  function keyOpenPref() {
    try { return localStorage.getItem("eucstats_crewkey") === "1"; } catch (e) { return false; }
  }

  function mountKey() {
    if (KEYBOX || !map) return;
    var host = map.getContainer && map.getContainer();
    if (!host) return;
    KEYBOX = document.createElement("details");
    KEYBOX.className = "crewkey";
    if (keyOpenPref()) KEYBOX.open = true;
    KEYBOX.innerHTML = "<summary>" + esc(t("crew.key")) + "</summary>"
      + '<div class="crewkeyb">' + legendHTML() + "</div>";
    KEYBOX.addEventListener("toggle", function () {
      try { localStorage.setItem("eucstats_crewkey", KEYBOX.open ? "1" : "0"); } catch (e) {}
    });
    host.appendChild(KEYBOX);
  }

  function unmountKey() {
    if (KEYBOX && KEYBOX.parentNode) KEYBOX.parentNode.removeChild(KEYBOX);
    KEYBOX = null;
  }

  // On when the map is the thing on screen. This doubles as the answer to "the panel is
  // closed and nothing says you are still in crew mode" -- the key being there is what says
  // so, and it is the only thing on screen that could.
  function syncKey() {
    if (visible && !panelOpen) mountKey();
    else unmountKey();
  }

  function legendHTML() {
    return '<div class="crewlegend">'
      + band(0, "crew.tile.safe") + band(1, "crew.tile.pushed")
      + band(2, "crew.tile.slipping") + band(3, "crew.tile.fading")
      + band(4, "crew.tile.ringed")
      // Both of these are keys to cards only a crew has. The breathing note below was gated
      // on `ME.crew` and these two were left, so a rider with no crew read a legend for
      // "where to ride next" and "what you could lose" -- neither of which is on their
      // screen. The earlier fix's letter was met and its reasoning stopped a line short.
      + (ME && ME.crew
         ? '<span class="bt"><i></i>' + t("crew.targets.h") + "</span>"
           + '<span class="bl"><i></i>' + t("crew.lose.h") + "</span>"
         : "")
      // the same ink and pattern as every other chip: this was the one square in the key
      // with no hatch on it, so a crew saw its own colour flat where the map shows it woven
      + '<span class="bf"><i'
      + (myInk()[0] ? ' style="background:' + esc(myInk()[0]) + '"' : "")
      + (myInk()[1] ? ' data-p="' + esc(myInk()[1]) + '"' : "") + "></i>"
      + t("crew.legend.fresh") + "</span>"
      // The note described the breathing, which is exactly what is not happening for a
      // reader who asked for stillness.
      // And only for a reader who has ground of their own. Both wordings say "your own", and
      // this legend also renders for a rider who is paired with no crew yet, where `ME.crew`
      // is null and `scopePulse()` therefore scopes nothing: sixteen crews' fresh ground
      // breathing under a sentence saying it belongs to the reader, who holds none of it. The
      // swatch above still reads "Taken this week", which is true of precisely what moves, so
      // the sentence goes and the motion stays for the rider who has nothing yet.
      + (ME && ME.crew
         ? '<p class="crewlegnote">'
           + t(CALM ? "crew.legend.note.calm" : "crew.legend.note") + "</p>"
         : "")
      + "</div>";
  }

  function plainRank(rows) {
    return '<table class="crewrank"><tbody>' + rows.map(function (r, i) {
      return '<tr class="sel" data-i="' + i + '" data-slug="' + esc(r.slug || "")
        + '"><td class=rk>' + (i + 1) + "</td>"
        + '<td><span class="celln">' + emb(r.slug, 16)
        + "<span>" + esc(r.name) + "</span></span></td>"
        + "<td class=val>" + tiles(r.best_tiles || r.tiles) + "</td>"
        // the sub column used to repeat the same unit with a different number beside it
        + '<td class="val sub">' + fmtKm2(r.best_km2) + "</td></tr>";
    }).join("") + "</tbody></table>";
  }

  // One, a few, many. English and most of the rest need only the first and last, and Russian,
  // Ukrainian and Polish need the middle one for 2, 3 and 4, which in this feature is nearly
  // every number anybody sees: a crew has two or three patches, not twenty-seven. The middle
  // branch can be run for everybody because the data agrees: every locale with no "few"
  // category has `.few` identical to its plural, so for them it decides nothing.
  //
  // The singular is not like that. Russian and Ukrainian take it back at 21, 31, 101 and 121
  // -- `21 клетка`, not `21 клеток` -- and Polish, which looks like the same rule, does not:
  // `21 pól` is correct there. Everything else wants the plural for every number above one.
  // Written as one arithmetic test for all nineteen languages, the board printed the genitive
  // plural for one Russian row in ten.
  var ONE_AT_X1 = { ru: 1, uk: 1 };

  // The page already keeps this current: it sets `lang` on the root element every time the
  // rider changes language, so there is nothing here to get out of step with it.
  function locale() {
    try { return document.documentElement.lang || "en"; } catch (e) { return "en"; }
  }

  function plural(one, few, many, n) {
    var d = n % 10, h = n % 100;
    if (one && (n === 1 || (ONE_AT_X1[locale()] && d === 1 && h !== 11))) {
      return t(one, { n: n });
    }
    if (few && d >= 2 && d <= 4 && (h < 12 || h > 14)) return t(few, { n: n });
    return t(many, { n: n });
  }

  function tiles(n) { return plural("crew.tile1", "crew.tiles.few", "crew.tiles", n); }

  function riders(n) { return plural("crew.rider1", "crew.riders.few", "crew.riders", n); }

  // Area follows the same metric/imperial switch as every other number on the site. A rider
  // who reads their rides in miles should not have one board quietly answering in km.
  var MI2_PER_KM2 = 0.3861021585;
  var MI_PER_KM = 0.6213711922;

  // "three weeks" rather than a date: the question is how entrenched they are, not the exact
  // afternoon it happened.
  function heldFor(iso) {
    var d = Math.max(0, Math.floor((Date.now() - new Date(iso)) / 86400000));
    if (d >= 364) return t("crew.ago.year");
    if (d >= 14) return plural(null, "crew.ago.weeks.few", "crew.ago.weeks",
                               Math.floor(d / 7));
    if (d >= 2) return plural(null, "crew.ago.days.few", "crew.ago.days", d);
    return t("crew.ago.new");
  }

  function daysUntil(iso) {
    var d = Math.ceil((new Date(iso) - Date.now()) / 86400000);
    return d > 0 ? d : 1;
  }

  // How long a knock has been standing.
  //
  // This was one string wrapped around `heldFor`, on the argument that a request that has
  // been waiting is the same question as ground that has been held. It is not, at the short
  // end: `heldFor`'s floor bucket is "a day or two", which is the right grain for territory
  // and makes a request that arrived 105 SECONDS ago read exactly like one from last
  // Tuesday -- wrong data in the one place a leader decides. A reviewer measured it against
  // the server clock.
  //
  // Hours below a day, and `heldFor` above it, so the long end still costs no new scale. The
  // hour form carries its number rather than spelling it, which keeps this to two strings
  // instead of a plural set in nineteen languages.
  function askedAgo(iso) {
    var h = Math.floor((Date.now() - new Date(iso)) / 3600000);
    if (h < 1) return t("crew.asked.now");
    if (h < 24) return t("crew.asked.h", { n: h });
    return t("crew.asked.ago", { d: heldFor(iso) });
  }

  // Same per-quantity unit switch as the rest of the site: somebody reading in miles gets
  // "0.4 mi", not a kilometre figure with a mile label on it.
  function fmtKm(v, coarse) {
    var n = (v == null ? 0 : v) * (H.mph && H.mph() ? MI_PER_KM : 1);
    // A non-breaking space. The join list broke `137 km²` across two lines on five of its
    // eight rows, and `nowrap` is the wrong tool twice over in this file: once it cut text in
    // eleven locales, once removing it made a measurement lie in CJK.
    var u = H.mph && H.mph() ? "\u00a0mi" : "\u00a0km";
    // One decimal while it matters, none once it does not: "0.4 mi" and "137 km".
    //
    // Under one unit the decimal count has to follow the unit, because the two are not
    // equally fine. A tenth of a kilometre is 100 m; a tenth of a mile is 160 m, which is
    // coarser than the figure being converted, so distinct targets collapse into one label --
    // a reviewer found 0.6 km and 0.7 km both printing "0.4 mi", three consecutive rows of a
    // list whose instruction is "Pick one to find it" all reading "a few streets 0.4 mi". The
    // rows exist to be chosen between, so they have to differ.
    //
    // The first version of this fix gave both units two decimals, which bought nothing in
    // kilometres and cost "0.60 km" where "0.6 km" had been right. Second decimal in miles
    // only: the point was matching the source precision, not printing more digits.
    //
    // `coarse` opts out of the second decimal. The rule above is about telling two rows of a
    // list apart; in prose it is noise, and it produced "A square is about 0.99 mi across" --
    // an exact conversion of 1.6 km that lands one hundredth short of a round number, under
    // the word "about", which reads as a bug whatever the arithmetic says.
    if (n < 1) return n.toFixed(!coarse && H.mph && H.mph() ? 2 : 1) + u;
    return (n < 10 ? n.toFixed(1) : Math.round(n).toLocaleString()) + u;
  }

  // How hard, not how far. A decimal is the model's answer handed over before anybody rides;
  // "a short ride" is the thing a rider is actually weighing up. The figures come back if the
  // admin switches them on.
  var SHOW_NUMBERS = !!(window.__CREWCFG__ && window.__CREWCFG__.numbers);

  // The same floor the server scores against: 0.42 of a square's own width, so one "lap" means
  // the same amount of riding in Tromso as in Singapore even though the squares differ
  // fourfold in area.
  var MIN_LEAD_EDGE = 0.42, EARTH_C_KM = 40075.016686;

  // How far it is across one square where this rider rides. Every number in the manual rests
  // on it -- the weekly cap, the 2x2, the km2 figures -- and the manual never stated it, so a
  // reader had "15 squares" and "23 km2" and no way to connect them. Derived from the floor,
  // which is a fixed fraction of the edge, at the rider's own latitude.
  // The measurement, so the cap can use the same one the sentence prints.
  function squareEdgeKm() {
    var lat = (ME && ME.home && ME.home.lat != null) ? ME.home.lat
            : (map && map.getCenter ? map.getCenter().lat : 59.9);
    var z = TERR ? TERR.z : 14;
    return 360 / Math.pow(2, z) / 360 * EARTH_C_KM * Math.cos(lat * Math.PI / 180);
  }

  function squareKm() {
    // Coarse: this is a sentence, not a row to pick between. See the note in `fmtKm`.
    return fmtKm(Math.round(squareEdgeKm() * 10) / 10, true);
  }

  function floorKm(y) {
    if (!TERR) return 0.5;
    var n = Math.pow(2, TERR.z);
    var mid = (tileLat(y, TERR.z) + tileLat(y + 1, TERR.z)) / 2;
    return 360 / n / 360 * EARTH_C_KM * Math.cos(mid * Math.PI / 180) * MIN_LEAD_EDGE;
  }

  // Six rungs, not four. The top one was open-ended, so a square 1.6 km out of reach and one
  // 5.0 km out of reach were both "the long way round": half the rows on a card said the same
  // thing about a Tuesday evening and a Saturday morning. The ratios are to the square's own
  // floor, so a word means the same amount of work in Tromso as in Singapore.
  // What one rider can put into one square in a week, and where that settles under decay.
  // Mirrors RIDER_TILE_WEEK_CAP_KM and HALF_LIFE_DAYS in services/territory.py; the panel
  // needs it to tell a crew which rows are arithmetic rather than a plan.
  // From the server, not from here. This used to be a 6 typed beside the sentence that
  // prints it, while `territory.RIDER_TILE_WEEK_CAP_KM` is what the model enforces.
  //
  // There is no single figure any more: the cap scales with the square, and a square is
  // 0.85 km across at Tromso against 2.45 at the equator. The page config carries a
  // mid-latitude default for a reader we know nothing about, and `/crews/me` carries the
  // rung for where THIS rider actually rides -- so `capKm()` and not a constant, because
  // the manual is usually opened after that has landed.
  // The rungs `services/territory.py` snaps to, and why: the cap is five crossings of a
  // square, and the square is not the same size everywhere. Mirrored here for the one case
  // the server cannot answer -- a reader with no session, whose square size comes from the
  // map they are looking at.
  var CAP_RUNGS = [5, 8, 10, 13, 16];

  function capKm() {
    if (ME && ME.rider_week_cap_km != null) return ME.rider_week_cap_km;
    // Signed out this fell back to a flat 8 while `squareKm()` two rules above printed the
    // square at the map's own latitude -- so the manual told a signed-out reader a 1.1 km
    // square has an 8 km cap, when 1.1 km snaps to 5. The same modal, 60% out, and only for
    // the readers who have not signed in. Same formula as the server, same ladder.
    var raw = Math.max(0.1, squareEdgeKm() * 5);
    var best = CAP_RUNGS[0];
    for (var i = 1; i < CAP_RUNGS.length; i++) {
      if (Math.abs(Math.log(CAP_RUNGS[i] / raw)) < Math.abs(Math.log(best / raw))) {
        best = CAP_RUNGS[i];
      }
    }
    return best;
  }
  var MAX_MEMBERS = CFG.max_members != null ? CFG.max_members : 0;   // 0 = no cap
  var RIDER_CEILING_KM = 29;

  // A square needing more than the crew can physically bank is not somewhere to ride, it is
  // a sum. Saying so is kinder than letting somebody grind at it for a month.
  function outOfReach(need) {
    var n = ME && ME.crew ? (ME.crew.members || 1) : 1;
    return need > n * RIDER_CEILING_KM;
  }

  function effort(km, y) {
    if (SHOW_NUMBERS) return fmtKm(km);
    var r = km / (floorKm(y) || 0.5);
    return t("crew.take." + (r <= 0.4 ? 1 : r <= 1 ? 2 : r <= 2.5 ? 3
                             : r <= 5 ? 4 : r <= 10 ? 5 : 6));
  }

  function margin(km, y) {
    if (SHOW_NUMBERS) return fmtKm(km);
    var r = km / (floorKm(y) || 0.5);
    // 0.35 because that is the gate _pressure uses for band 0; at 0.5 the tooltip could say
    // "Yours, comfortably" and "hanging on" in the same box
    return t("crew.hold." + (r <= 0.35 ? 1 : r <= 2 ? 2 : 3));
  }

  function fmtKm2(v) {
    var n = v == null ? 0 : v;
    if (H.mph && H.mph()) {
      return Math.round(n * MI2_PER_KM2).toLocaleString() + "\u00a0mi²";
    }
    return Math.round(n).toLocaleString() + "\u00a0km²";
  }

  // A function rather than a constant: this was built at load time, before the host hands
  // over its translator, so its titles could not have called `t()` even if anybody had
  // thought to -- and `crew.role.leader` has been translated in all eighteen locales the
  // whole time and is used correctly forty lines below.
  // `past` was U+00B7, the same middle dot that sits inside every `S13·Night Shift`
  // handle in the list, so "has left the crew" read as a typo next to a gold star and a mint
  // diamond. U+2716 is a mark, not punctuation.
  var ROLEGLYPH = { leader: "\u2605", officer: "\u25c6", member: "", past: "\u2716" };
  var ROLECLASS = { leader: "lead", officer: "off", member: "", past: "past" };

  function roleMark(role) {
    if (!ROLEGLYPH[role]) return "";
    return '<span class="crewrole ' + ROLECLASS[role] + '" title="'
      + esc(t("crew.role." + role)) + '">' + ROLEGLYPH[role] + "</span>";
  }

  // The marks, named in the open. `title=` was the only thing explaining them, which is no
  // explanation at all on a phone -- there is no hover -- and the manual names none of the
  // three. A reader met a gold star, a mint diamond and a cross beside their crewmates' names
  // with nothing anywhere saying what they meant, and the cross is the one that matters most:
  // it marks somebody who has left, whose kilometres stay on the list.
  //
  // Only the marks actually in the list it sits under, so a crew with no officers and nobody
  // gone is not handed a key to symbols it does not use.
  function roleKey(rows) {
    var bits = [];
    ["leader", "officer", "past"].forEach(function (role) {
      var present = (rows || []).some(function (c) { return c.role === role; });
      if (present) bits.push(ROLEGLYPH[role] + " " + t("crew.role." + role));
    });
    if (!bits.length) return "";
    return '<p class="hint crewrolekey">' + esc(bits.join(" · ")) + "</p>";
  }

  // The squares this crew could take next. Until this existed the mode could say a tile was
  // contested but never where to go, which is the one thing a map mode about choosing routes
  // has to do. A tile that joins two patches leads, because the board ranks on the biggest
  // single patch and welding two together beats widening either.
  // "same as the row above". Dimming the repeated words to the point where they read as a
  // repeat put them under three and a half to one against this background, which is below the
  // floor for body text, and there is no opacity that is both.

  var TARGETS = [];
  var TARGETSEL = -1;
  var LOSING = [];

  function bearing(d) { return d ? t("crew.targets." + d) : ""; }

  var COMPASS = ["n", "ne", "e", "se", "s", "sw", "w", "nw"];

  function compass(dx, dy) {
    if (!dx && !dy) return "";
    return COMPASS[Math.round(Math.atan2(dx, -dy) / (Math.PI / 4)) & 7];
  }

  // The squares this crew could take next. Until this existed the mode could say a tile was
  // contested but never where to go, which is the one thing a map mode about choosing routes
  // has to do.
  // Who the holder would fall behind if this square went. The board is sorted and carries
  // `best_tiles` on every entry, so this is arithmetic on what the browser already has.
  function myName() {
    return ME && ME.crew ? ME.crew.name : null;
  }

  // Would taking this square move the READER past somebody? `passes` answers the mirror
  // question -- whether the holder falls behind -- and returns nothing at all on a square
  // nobody holds, which is every row of seven crews' cards.
  function youPass(x) {
    if (!TERR || !TERR.crews || !ME || !ME.crew) return null;
    if (typeof x.grown !== "number" || !(x.grown > (x.own_now || 0))) return null;
    var board = TERR.crews.slice().sort(function (a, b) {
      return (b.best_tiles || 0) - (a.best_tiles || 0);
    });
    var mine = myName();
    for (var i = 0; i < board.length; i++) {
      if (board[i].name === mine) continue;
      var theirs = board[i].best_tiles || 0;
      // level or ahead now, behind after
      if ((x.own_now || 0) <= theirs && x.grown > theirs) return board[i].name;
    }
    return null;
  }

  function passes(x) {
    if (!TERR || !TERR.crews || !x.held_name || !(x.ranked_was > x.ranked_now)) return null;
    var board = TERR.crews.slice().sort(function (a, b) {
      return (b.best_tiles || 0) - (a.best_tiles || 0);
    });
    var i = -1;
    board.forEach(function (c, n) { if (c.name === x.held_name) i = n; });
    if (i < 0) return null;
    for (var k = i + 1; k < board.length; k++) {
      var below = board[k].best_tiles || 0;
      if (x.ranked_now < below) return board[k].name;   // they fall behind this one
    }
    return null;
  }

  function targetsHTML(rows) {
    TARGETS = rows || [];
    TARGETSEL = -1;
    var head = '<div style="--kmw:' + widest(TARGETS) + 'ch" class="crewtargets'
      + (SHOW_NUMBERS ? " nums" : "") + '"><h4>' + t("crew.targets.h") + "</h4>";
    // The "ride a block anywhere" line used to show only when there were no rows at all,
    // which is the one case where a crew cannot act on it. It is the hint above the rows now
    // whenever the crew holds nothing, which is who it was written for.
    var nothing = !ME || !ME.crew || !ME.crew.tiles;
    if (!TARGETS.length) {
      return head + '<p class=hint>' + t("crew.targets.none", { n: SEED }) + "</p>"
        + "</div>";
    }
    // When every row is the block, the hint above has already said so and the badge is on
    // all four, which makes it furniture rather than a mark.
    var allFirst = TARGETS.every(function (x) { return x.first; });
    // Four rows of one block are four squares of one neighbourhood, and printing its name
    // four times is the ditto problem in a card that bypasses the dedupe.
    // A ditto is a comparison with the line above. Written as a set of everything seen so
    // far, a blank row inherited the last DISTINCT place instead: the Oslo card printed Oslo,
    // then Kjenn on row 3, and rows 4-7 were Oslo sitting blank under "Kjenn" -- thirty rows
    // across twelve crews filed under the wrong neighbourhood. `onePlace` is gone with it: it
    // deleted the place from every row of a single-place card, and nothing else on the card
    // named it, so the second crew on the board got a card that said where to ride without
    // ever saying where.
    var prevAt = null;
    // Nothing on this card is held by anybody. Seven crews of fifteen are in that position,
    // and the honest thing to say about eight adjacent empty squares is the same sentence
    // eight times -- so it is said once, here, and the rows go back to being distances.
    // Every row, not most of them. This said "each of these adds one to your block" over a
    // card whose rows add nothing, and over another whose first four rows add seven.
    var noRival = TARGETS.every(function (x) {
      return !x.held_by && typeof x.grown === "number" && x.grown - (x.own_now || 0) === 1;
    });
    if (noRival && TARGETS.length > 1) {
      head += '<p class="hint crewclear">' + t("crew.targets.clear") + "</p>";
    }
    // Every branch of the chip chain gets a gate. Four had one and three did not, so the
    // loudest badge in the panel printed itself three times running on the top crew's card
    // and four times on another: the same class this chain has been closing for rounds,
    // reopened by the branches nobody had got to yet. `drops` shares `saidQuiet` with its own
    // quiet twin, which also stops one card saying "drops them to 22" in one weight and
    // "DROPS THEM TO 21" in another about the same crew.
    var seenWho = {};
    var saidQuiet = {};
    var saidPass = {};
    var saidGrow = {};
    var saidLink = {};
    var saidJoin = false;
    var saidStray = false;
    var body = TARGETS.map(function (x, i) {
          // Worked out before the tag chain, not after it. The chain asks whether this
          // rival's name has already appeared on the card, and `var` hoisting handed it
          // `undefined` every time, so the one-per-rival rule never fired and the quiet
          // reason printed on all seven rows of the sparse card.
          var who = x.first && x.blocked && x.held_by && x.held_name
            ? t("crew.targets.flips", { name: esc(x.held_name) })
            : x.held_by && x.held_name
            ? t("crew.targets.taken", { name: esc(x.held_name) })
            : x.held_by ? t("crew.targets.takenby")
            : t("crew.tile.free");
          // Nothing goes in the number column on a square whose shortfall is zero: riding it
          // again does nothing, and a word there wore the styling meant for a distance.
          // A square the crew already leads has nothing to ride, and printed an empty
          // column: on a first-block card that was three rows in four saying nothing.
          var km = x.blocked ? t(x.first ? "crew.targets.got" : "crew.targets.blocked")
                             : effort(x.need, x.y);
          // only the holder: rows are deduplicated on effort, place and holder -- not the
          // bearing, which differs without anything differing -- so two rows can share an
          // effort word and still be different places, and a ditto there would be a mistake.
          // The holder's standing, printed only where their name is new below: five rows
          // about one rival printed "1st, 91 squares" five times, which is the same ditto
          // problem the dimmed repeat beside it already solves.
          var stand = x.held_name ? holderStanding(x.held_name) : null;
          var rw = seenWho[who] ? " rpt" : "";
          seenWho[who] = 1;
          var tag = x.first && !allFirst
            ? '<span class="crewtag first">' + t("crew.targets.first") + "</span>"
            : x.kills ? '<span class="crewtag kills">'
              + t("crew.targets.kills", { v: tiles(x.lost || 0) }) + "</span>"
            // Above the link chips, because a gain of more than one only happens when
            // patches merge: the number says everything "joins two patches" says, and how
            // big. Four rows in the world take a crew up seven and two take one up
            // twenty-three, and the card printed neither.
            : typeof x.grown === "number" && x.grown - (x.own_now || 0) > 1
                && !saidGrow[x.grown]
              ? ((saidGrow[x.grown] = 1), '<span class="crewtag joins">'
                + t("crew.targets.grows", { n: x.grown }) + "</span>")
            // Below the stray mark, not above it. A square that links two patches but leaves
            // the ranked number where it was got "2 to link up", which implies the opposite
            // of the one true thing about it.
            // Gated like every other branch. I deleted the dead copy of this last round and
            // left the live one ungated, so the card went on printing it twice on adjacent
            // rows -- the duplicate was never the dead branch's doing.
            : typeof x.grown === "number" && x.grown <= (x.own_now || 0) && !saidStray
              ? ((saidStray = true),
                 '<span class="crewtquiet">' + t("crew.targets.stray") + "</span>")
            : x.links && !saidLink[x.links]
              ? ((saidLink[x.links] = 1), '<span class="crewtag joins">'
                + t("crew.targets.links", { n: x.links }) + "</span>")
            // Once too. It carries no number, so a second one is the same five words again.
            : x.joins && !saidJoin
              ? ((saidJoin = true),
                 '<span class="crewtag joins">' + t("crew.targets.joins") + "</span>")
            : x.blocked && !x.first
              ? '<span class="crewtag done">' + t("crew.targets.blocked") + "</span>"
            // Below the three-square bar, say what their number becomes. One square off the
            // patch the board ranks them on is true of every border square and is the only
            // thing on the card shaped like catching somebody.
            // Only when it changes something. Every border square costs a rival one, so
            // printing that on all eight rows is a badge nobody reads -- the mistake the
            // kill badge made before it. Worth a line when the square costs them more than
            // one, or when it drops them past somebody on the board.
            // Its own colour, not the hot pink reserved for breaking a crew's block, and
            // once per card: five rows saying the same thing about the same board is one
            // piece of news printed five times in the loudest ink in the panel.
            // Its own colour, not the hot pink reserved for breaking a crew's block, and
            // once per crew named: three rows saying "puts them behind Harbour Loop" is one
            // piece of news about one board, printed three times.
            : passes(x) && !saidPass[passes(x)]
              ? ((saidPass[passes(x)] = 1),
                 '<span class="crewtag ' + (passes(x) === myName() ? "youpass" : "drops")
                 + '">' + (passes(x) === myName()
                   ? t("crew.targets.youpass")
                   : t("crew.targets.passes", { name: esc(passes(x)) })) + "</span>")
            : x.ranked_was - x.ranked_now > 1 && !saidQuiet[who]
              ? ((saidQuiet[who] = 1), '<span class="crewtag drops">'
                + t("crew.targets.drops", { n: x.ranked_now }) + "</span>")
            // Worth one square. True of most border squares, which is why printing it as a
            // chip on every row turned it into furniture the round it was introduced -- but
            // deleting it left 36 rows naming a rival and saying nothing about them at all.
            // Same sentence, no box, no colour: a reason for the rows that have a small one,
            // and the chips keep meaning something.
            // Crossing somebody on the board by taking empty ground. Once per card, like the
            // mirror case below it.
            : youPass(x) && !saidPass[youPass(x)]
              ? ((saidPass[youPass(x)] = 1), '<span class="crewtag youpass">'
                + t("crew.targets.youpassname", { name: esc(youPass(x)) }) + "</span>")
            // A square that adds a tile the ranked number cannot see. Three rows in the world,
            // which is exactly why it is worth marking: riding one moves nothing.

            : x.held_by && x.ranked_was - x.ranked_now === 1 && !saidQuiet[who]
              ? ((saidQuiet[who] = 1), '<span class="crewtquiet">'
                + t("crew.targets.drops", { n: x.ranked_now }) + "</span>")
            : "";
          // Not part of the chain above: a square can be the best move in the game AND more
          // than the crew can physically bank, and being told only the first is how somebody
          // spends a month on arithmetic.
          // Not beside the same sentence in the effort column. A blocked row already reads
          // "ridden out, take the one next door" on the left; the chip repeated it in caps on
          // the right, twice on one row.
          if (x.blocked && tag.indexOf("crew.targets.blocked") < 0
              && tag.indexOf(t("crew.targets.blocked")) >= 0) {
            tag = "";
          }
          if (!x.blocked && outOfReach(x.need)) {
            tag += '<span class="crewtag done">' + t("crew.targets.far") + "</span>";
          }
          // A crew that folded between the rebuild and this view has no name to print, and
          // the row came out as " has it" with a leading space and nobody in it.
          // A block square the crew already out-rides: "done" over "they have it" reads as a
          return '<div class="crewtrow sel' + (x.blocked ? " done" : "") + '" data-t="' + i + '">'
            + '<span class="crewtkm">' + km
            + (x.blocked ? "" : ' <i>' + fmtKm(x.need) + "</i>") + "</span>"
            + '<span class="crewtdir">' + bearing(x.dir) + "</span>"
            // Where, not only which way. A compass bearing from the middle of your own
            // ground is not how anyone reads a map of the city they live in.
            + '<span class="crewtwho">'
            // The separator is TEXT, not `::before`. Generated content exists for the
            // renderer and nothing else, so the page read back `OsloHolmenkollen Climb hat
            // es` to a screen reader, a copy-paste and a find-in-page alike. CSS still
            // decides whether it is seen -- see `.crewtsep`.
            + (x.at && x.at !== prevAt
               ? ((prevAt = x.at), '<b class="crewtat">' + esc(x.at) + "</b>"
                  + '<span class="crewtsep"> &middot; </span>') : "")
            + '<i class="' + (rw ? "rpt" : "") + '">' + who
            // `tiles()` and not `t("crew.tiles")`, so Russian, Polish and Ukrainian get
            // their own plural forms; and "in one piece", because this number is the
            // biggest patch the board ranks on, while the hero two lines up uses the
            // bare phrase for the total. One card said "N squares" about two things.
            // `<span>`, not `<u>`: the underline is reset in the stylesheet anyway, so the
            // only thing the tag was still doing was telling a screen reader that a crew's
            // standing is underlined for emphasis, which is a layout choice.
            + (stand && !rw ? ' <span class="crewtst">' + esc(ordinal(stand.place)) + " · "
                        + esc(tiles(stand.tiles)) + " " + esc(t("crew.mine.ao"))
                        + "</span>" : "")
            + "</i></span>"
            + tag + "</div>";
        }).join("");
    // How close they are, which is the whole point of the card for a crew with no ground.
    var left = TARGETS.filter(function (x) { return x.first && !x.blocked; });
    var togo = left.reduce(function (a, x) { return a + x.need; }, 0);
    var lead = nothing && TARGETS.length && TARGETS[0].first
      ? (left.length === 0
         ? t("crew.targets.p0done")
         : t(left.length === 1 ? "crew.targets.p0one" : "crew.targets.p0n",
             // `tiles()`, not a bare count with the noun baked into the sentence: the
             // squares left in a block are 2, 3 or 4 far more often than anything else,
             // which is exactly where Russian, Ukrainian and Polish need their own form.
             { s: tiles(left.length), v: fmtKm(Math.round(togo * 10) / 10) }))
      : t(nothing ? "crew.targets.p0" : "crew.targets.p", { n: SEED });
    return head + '<p class=hint>' + lead + "</p>"
      // Not on this card any more: it is on the crew card above, where somebody looking for
      // "when does my ride count" actually looks, and printing it three times in one panel
      // made it furniture.
      + body + "</div>";
  }

  // How many `ch` a string actually occupies, which is not how many characters it has.
  //
  // An ideograph, a kana, a hangul syllable and a fullwidth form each take about two `ch`.
  // `.length` counted them as one, so sixteen characters of Japanese asked for 134px and
  // needed 194, and the row wrapped between `0.6 km` and the phrase qualifying it.
  //
  // A count rather than a measurement on purpose. Both measurements that have lied in this
  // file asked the browser something subtle at the wrong moment -- `scrollWidth` before the
  // font settled, `ch` against a fallback face -- and this is deterministic and font-free.
  // Ranges: Hangul Jamo, CJK radicals and punctuation, kana and Bopomofo, CJK Ext A, CJK
  // Unified, Yi, Hangul syllables, CJK compatibility, and the fullwidth forms.
  var WIDE = /[\u1100-\u115f\u2e80-\u303e\u3041-\u33ff\u3400-\u4dbf\u4e00-\u9fff\ua000-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe6f\uff00-\uff60\uffe0-\uffe6]/;
  function chWidth(str) {
    var s = String(str == null ? "" : str), n = 0;
    for (var i = 0; i < s.length; i++) n += WIDE.test(s.charAt(i)) ? 2 : 1;
    return n;
  }

  // The widest phrase decides the column, because the phrase is prose and the locales differ
  // by a factor of two. Including the number, which this measured without: `--kmw` came out
  // too narrow whenever a card's phrases were short, and six rows of eight wrapped to double
  // height.
  //
  // One function for both lists. They had two caps -- 38 here and 30 in `loseHTML` -- and the
  // tighter one sat on the longer family of strings, because the lose phrasing is the target
  // phrasing with "and it's theirs" appended. German wrapped and French missed by a pixel.
  // `--kmc` clamps the result to `min(--kmw, 46%)` anyway, so the cap is a safety net and
  // belongs in one place.
  // The `+1` used to be the whole allowance, and `ch` is the width of the ZERO glyph -- which
  // is wider than most lowercase letters and narrower than the spaces and `w`s and `g`s in a
  // phrase like "the long way round 1.4 km". Measured at 1280: that string is 25 characters,
  // so this returned 26ch = 143.2px, and the string needs 156.6px. Six rows of eight wrapped
  // to double height inside a column sized by the very phrase that was overflowing it.
  //
  // So the allowance is proportional rather than flat: a long phrase is short by more pixels
  // than a short one, because the error compounds per character. 15% covers the worst case I
  // could measure with room to spare, and the cap in the stylesheet still has the last word.
  function widestOf(rows, phrase) {
    var n = 7;
    (rows || []).forEach(function (x) {
      var w = chWidth(phrase(x));
      if (w > n) n = w;
    });
    return Math.min(Math.ceil(n * 1.15) + 1, 44);
  }

  function widest(rows) {
    return widestOf(rows, function (x) {
      // Blocked rows too. They render `crew.targets.blocked` into this very column, and
      // measuring them as empty made the column too narrow for the one string that is
      // longest: 36 characters in Danish, past the cap in eight locales, two rows of six
      // standing at double height on a list whose whole job is to be scannable.
      return x.blocked ? t(x.first ? "crew.targets.got" : "crew.targets.blocked")
                       : effort(x.need, x.y) + " " + fmtKm(x.need);
    });
  }

  // The other half of the game. Every band and every shortfall is already in TERR.cells, so
  // this costs one pass over an array the browser has had the whole time. Without it the mode
  // is offence only: the crew at the top of the board was being out-ridden in ten squares and
  // the panel was telling them to go paint empty fields.
  // cell ordinal -> the crew taking it. Sparse: only the squares somebody else is riding.
  var RIVALS = {};

  // cell ordinal -> neighbourhood, for the squares a crew can lose
  var PLACES = {};

  function indexRivals() {
    RIVALS = {};
    PLACES = {};
    var r = TERR && TERR.rivals;
    if (r) {
      for (var i = 0; i + 1 < r.length; i += 2) RIVALS[r[i]] = r[i + 1];
    }
    var p = TERR && TERR.places, names = (TERR && TERR.placenames) || [];
    if (p) {
      for (var j = 0; j + 1 < p.length; j += 2) PLACES[p[j]] = names[p[j + 1]];
    }
  }

  function loseHTML(slug) {
    if (!TERR || !TERR.crews) return "";
    indexRivals();
    var idx = -1;
    TERR.crews.forEach(function (c, i) { if (c.slug === slug) idx = i; });
    if (idx < 0) return "";
    var rows = [], all = [];
    for (var i = 0; i < TERR.cells.length; i += 5) {
      if (TERR.cells[i] !== idx) continue;
      all.push({ x: TERR.cells[i + 1], y: TERR.cells[i + 2] });
      var band = TERR.cells[i + 3];
      band = bandOf(band);
      // 3 belongs here too. Nobody has to beat you for fading ground, you only have to not
      // show up, and skipping it left ten crews out of twelve with an empty card while thirty
      // of their squares were quietly going cold.
      if (band !== 1 && band !== 2 && band !== 3) continue;   // 4 cannot be lost
      // The name comes from the payload, not from TARGETS: targets_for excludes every square
      // the crew holds and this list iterates only squares it holds, so the lookup that used
      // to scan TARGETS could never match anything.
      rows.push({ x: TERR.cells[i + 1], y: TERR.cells[i + 2], band: band,
                  at: PLACES[i / 5],
                  need: TERR.cells[i + 4] / 10, rival: RIVALS[i / 5] });
    }
    LOSING = [];
    if (!rows.length || !all.length) return "";
    // The grouping that used to live here -- a summary of the top three rivals printed above
    // the flat list -- is gone, because `groupedRows` below now groups the rows themselves and
    // heads each group with the same sentence. Keeping both would print every sentence twice,
    // and a summary that stopped at three rivals above a list that showed all of them was the
    // half-measure a reviewer named: it "names the villain without shortening the read".
    // about to flip first, then being ridden, then going cold on its own
    var urgency = { 2: 0, 1: 1, 3: 2 };
    rows.sort(function (a, b) {
      return (urgency[a.band] - urgency[b.band]) || (a.need - b.need);
    });
    // Never hide a square somebody is actually riding. At a flat cut of five, a crew with
    // seventeen losable squares had twelve hidden behind "and 12 more going quiet" -- and six
    // of those twelve had a named rival closing in on them. A leader who trusts that line
    // does not defend them. Everything in band 1 or 2 is shown; only ground going cold on its
    // own is ever folded into the count, which is the one thing that sentence can truthfully
    // describe.
    // No cap. This used to be `cold.slice(0, max(0, 5 - urgent.length))`, which with 37
    // urgent rows clamps to zero -- so the seven squares going cold on their own were never
    // rendered at all: no place, no distance, no bearing, not clickable, just a muted "and 7
    // squares more" where a fourth group heading would be. The cold group and
    // `crew.lose.cold.n` were unreachable for any crew with five or more squares under attack,
    // which is any crew under pressure.
    //
    // The cap was mercy on a flat list thirty-seven rows long. The GROUPING is what answers
    // length now -- 44px closed, 189px open -- so the cap had nothing left to do but hide
    // squares, and it made this function's own promise at the top ("Nothing is hidden from a
    // leader who opens this") false.
    var urgent = rows.filter(function (r) { return r.band !== 3; });
    var cold = rows.filter(function (r) { return r.band === 3; });
    LOSING = urgent.concat(cold);
    // bearings from the middle of everything the crew holds, not from the middle of the five
    // rows: with one row those are the same point and the direction comes out empty
    var cx = 0, cy = 0;
    all.forEach(function (x) { cx += x.x; cy += x.y; });
    cx /= all.length; cy /= all.length;
    // Same three columns as "where to ride next", so the two cards read as a pair: how hard,
    // which way, what about it.
    var cols = widestOf(LOSING, function (x) {
      return t("crew.lose.gap", { v: effort(x.need, x.y) });
    });
    var seenState = {};
    // A disclosure once the list would bury what is under it. Twenty-odd rows sat between
    // "where to ride next" and the roster, so the Leave / Disband / hand-back controls ended
    // up far below the fold at 390. Capping the list is the one thing this must not do: see
    // the note above `LOSING`, where a flat cut of five folded six squares with a named rival
    // closing in behind a sentence calling them quiet. Nothing is hidden from a leader who
    // opens this, and the summary names the count instead of characterising it -- "20 squares"
    // is true in the way "and 12 more going quiet" was not. `tiles()` carries the plural, so
    // no new string. Open while the list is short enough to be harmless.
    // OPEN by default, with the groups shut. The card-level fold existed to tame a flat list
    // thirty-seven rows long; the groups now do that, and folding at both levels hid the
    // villains behind two clicks for no gain -- a reviewer measured the default read as 44px
    // of "44 squares" with not one rival named, and said the ask was to shorten the read, not
    // to remove it. That was my overshoot. Open, with the groups shut, the default read is the
    // hint and four named threats in about 189px: the four entries that were asked for, at no
    // clicks. The rider's own choice still wins over this, and survives the panel closing
    // under them when they pick a square.
    var cardOpen = LOSECARDOPEN === null ? true : LOSECARDOPEN;
    return '<details id="crewlosecard" style="--kmw:' + cols + 'ch" class="crewtargets crewlose'
      + (SHOW_NUMBERS ? " nums" : "") + '"' + (cardOpen ? " open" : "") + "><summary><h4>"
      + t("crew.lose.h")
      // A space in the markup, not only a flex gap. The gap separates them on screen and
      // not in the text layer, which is how this file already read "6 off 8th4 riders" once.
      // Everything at risk, which is now also everything inside: with the cold cap gone
      // `LOSING` is all of `rows`, so this number and the card's contents are the same thing
      // for the first time. It read 44 over a box holding 37 until the cap went, which broke
      // the rule the previous version of this comment stated.
      // Always, not only when shut. It was gated on the fold that no longer exists, so with
      // the card open by default the total would never have been printed at all -- the number
      // a leader wants first, removed by the line above it.
      + ' <span class="crewlosen">' + tiles(rows.length) + "</span>"
      + "</h4></summary>"
      // eight crews in fourteen have nothing but fading ground, and telling them a rival is
      // closing in on it is simply untrue
      + '<p class=hint>'
      + t(LOSING.every(function (x) { return x.band === 3; }) ? "crew.lose.p3" : "crew.lose.p")
      + "</p>"
      // Grouped under the sentence that used to sit above them. A reviewer measured the
      // first version and said the summary "names the villain without shortening the read" --
      // thirty-seven rows were still thirty-seven rows. Closed, this is four lines; opened,
      // every square is still in it, which is the constraint the note above `LOSING` exists
      // for. The headings are the same two strings the summary used, so no new copy.
      + groupedRows(LOSING.map(function (x, i) {
          // their gap, not your effort, and the third column carries urgency rather than
          // restating the heading. Band 3 has no rival, so its number is days left.
          // The km goes INSIDE {v}, with the phrase it qualifies. Appended after the
          // whole string it landed past "and it's theirs", so eleven rows read "a few
          // streets and it's theirs 1.0 km" and the eye attached the number to "theirs".
          // The targets card above does it this way and the two are built as the same read.
          var reach = x.need < 0.05 ? null : fmtKm(x.need);
          var gap = t("crew.lose.gap", { v: effort(x.need, x.y)
                                            + (reach ? ' <i>' + reach + "</i>" : "") });
          // "creeping up" twice told you nothing about who. The attacking card has named its
          // victim since the first round; this one named nobody, so there was no grudge in a
          // game that runs on them.
          var who = x.rival == null ? null : (TERR.crews[x.rival] || {}).name;
          var state = x.band === 3 ? t("crew.lose.cold")
            : who ? t(x.band === 2 ? "crew.lose.nowwho" : "crew.lose.soonwho",
                      { name: esc(who) })
            : t(x.band === 2 ? "crew.lose.now" : "crew.lose.soon");
          // no ditto on the distance: see the note in the targets card above
          var rs = seenState[state] ? " rpt" : "";
          seenState[state] = 1;
          return { i: i, who: who, html: '<div class="crewtrow sel" data-l="' + i + '">'
            // Not "0.0 km". A gap under 50 m prints as 0.0 and that is a number saying
            // nothing, in the most urgent slot in the feature; the phrase ("one lap and it's
            // theirs") already carries it. See `reach` above, which is where it is decided.
            + '<span class="crewtkm">' + gap + "</span>"
            + '<span class="crewtdir">' + bearing(compass(x.x - cx, x.y - cy)) + "</span>"
            + '<span class="crewtwho">'
            + (x.at ? '<b class="crewtat">' + esc(x.at) + "</b>" + '<span class="crewtsep"> &middot; </span>' : "")
            + '<i class="' + (rs ? "rpt" : "") + '">' + state + "</i></span></div>" };
        }))
      // No footer: every row is in the card now, so there is nothing left over to admit to.
      // It used to read "and 7 squares more" and sat where a fourth group heading would, which
      // a reviewer read as a group that had failed to render. `crew.lose.more` is unused by
      // this card as a result -- left in the tables rather than pulled, since removing a
      // translated string to save a line is not worth a nineteen-locale diff.
      + "</details>";
  }

  // One disclosure per rival, holding that rival's squares, headed by the sentence the summary
  // used to print above the flat list.
  //
  // Each entry carries the index it had in `LOSING`, because `bindMine` finds these rows by
  // `data-l` and flies the map to `LOSING[i]`: renumbering inside groups would point every row
  // at the wrong square, which is a feature that works and lies.
  //
  // EVERY rival gets a group, not the three the summary named. A summary may stop at three; a
  // group list that stopped at three would hide the fourth rival's squares, which is the one
  // thing this card must never do.
  // Which losses groups the rider had open, and whether the card itself was open, across a
  // re-render. At phone widths picking a square CLOSES the panel -- otherwise you cannot see
  // the square -- and the rebuild used to come back entirely shut, so comparing two squares
  // cost four taps where the old flat list cost two.
  //
  // Keyed by the group's own heading, not its position: the groups re-sort as counts change,
  // and restoring by index would open the wrong rival. In the process rather than in storage,
  // because this is where you were a moment ago, not a preference you set.
  // `#cold` rather than a NUL sentinel: the key is written into `data-grp` and read back from
  // it by the toggle listener, and a NUL does not survive an HTML attribute -- it comes back as
  // U+FFFD, so the listener wrote one key while the renderer read another and the cold group's
  // state silently never persisted. `#` cannot appear in a crew name (letters, numbers, marks
  // and `_ -'&.`), so it cannot collide with a real group.
  var LOSEOPEN = {};
  var LOSECARDOPEN = null;

  // The group holding the square just flown to, so coming back from the map lands you where
  // you were rather than on a shut card.
  function rememberLoseGroup(x) {
    if (!x || !TERR) return;
    var who = x.rival == null ? null : (TERR.crews[x.rival] || {}).name;
    LOSEOPEN[who || "#cold"] = true;
    LOSECARDOPEN = true;
  }

  function groupedRows(entries) {
    var order = [], byWho = {};
    entries.forEach(function (e) {
      var key = e.who || "#cold";
      if (!byWho[key]) { byWho[key] = { who: e.who, rows: [] }; order.push(byWho[key]); }
      byWho[key].rows.push(e.html);
    });
    // Biggest threat first, ground nobody is taking last: it is the only group that is
    // nobody's fault but the crew's.
    order.sort(function (a, b) {
      if (!a.who !== !b.who) return a.who ? -1 : 1;
      return b.rows.length - a.rows.length;
    });
    return order.map(function (g) {
      // A group of one is a bare row. A one-row accordion is silly, and the counted headings
      // agree with a plural `{v}` in several locales -- "1 Feld werden kalt" -- which is the
      // trap a translator caught in the cold line and the reason it is gated on more than one.
      if (g.rows.length === 1) return g.rows[0];
      var head = g.who
        ? esc(t("crew.lose.threat", { name: g.who, v: tiles(g.rows.length) }))
        : esc(t("crew.lose.cold.n", { v: tiles(g.rows.length) }));
      // Small groups stay open: folding three rows behind a click buys nothing and costs a
      // click. Big ones fold, which is the whole point of doing this -- unless the rider had
      // this one open before the panel closed under them, in which case it is theirs.
      var key = g.who || "#cold";
      var open = (LOSEOPEN[key] || g.rows.length <= 3) ? " open" : "";
      return '<details class="crewlosegrp' + (g.who ? "" : " crewcold") + '"' + open
        + ' data-grp="' + esc(key) + '"><summary>' + head + "</summary>"
        + g.rows.join("") + "</details>";
    }).join("");
  }

  // Who actually rode for the crew, over the same window the territory is measured on, so the
  // list explains the shape on the map rather than ranking loyalty.
  function contributorsHTML(rows) {
    if (!rows || !rows.length) return "";
    // One denominator, because two of them is a chart that argues with its own label. The
    // bar was the share of the leader and the text beside it the share of the crew, so the
    // top rider drew a full-width bar reading "34%" and the rider 3 km behind them drew 97%
    // of that, reading "34%" as well. The share is what the card is asking -- who carries
    // this crew -- so the bar measures it too, and at a third of the width the track behind
    // it is finally visible.
    var total = rows.reduce(function (a, r) { return a + (r.km || 0); }, 0) || 1;
    // Largest remainder, not `Math.round` each. Rounding every row on its own let the column
    // add up to 101%, which on a card asking "who carries this crew" is the one number a
    // reader checks by adding it up. The floors always sum to 100 minus a whole number of
    // points, and those points go to the rows that were rounded down hardest.
    var exact = rows.map(function (c) { return ((c.km || 0) / total) * 100; });
    var shares = exact.map(Math.floor);
    var left = 100 - shares.reduce(function (a, b) { return a + b; }, 0);
    exact.map(function (v, i) { return [v - Math.floor(v), i]; })
      .sort(function (a, b) { return b[0] - a[0]; })
      .slice(0, Math.max(0, left))
      .forEach(function (pair) { shares[pair[1]] += 1; });
    return '<div class="crewcontrib"><h4>' + t("crew.mine.who") + "</h4>" + rows.map(function (c, i) {
      var share = shares[i];
      // The bar IS the number. It used to be `Math.max(2, share)`, so a row reading 1% drew
      // twice its own figure -- on exactly the rows where a rider is checking whether they
      // count at all. A tiny bar stays visible through `min-width` in the stylesheet, which
      // is pixels and does not claim to be a percentage.
      var pct = share;
      // Which row is you. This section is explicitly about personal contribution and every
      // row rendered identically, so the only way to find yourself was to remember your own
      // handle. The reader's handle is in the payload (`me.handle`) and every row carries the
      // same id, so this costs nothing but a class. `crew.how.s3` is "Your crew" and already
      // names the reader in nineteen languages; it is the board's marker too.
      var mine = !!(ME && ME.handle && c.id && c.id === ME.handle);
      return '<div class="crewcrow' + (mine ? " crewrowmine" : "") + '">'
        + (H.av ? H.av(c.id, c.has_avatar, c) : "")
        + (H.cc && c.flag ? H.cc(c.flag) : "")
        + '<span class="crewcname">' + esc(c.name) + roleMark(c.role)
        + (mine ? ' <b class="crewyou">' + esc(t("crew.you")) + "</b>" : "") + "</span>"
        + '<span class="crewcbar"><i style="width:' + pct + '%"></i></span>'
        + '<span class="crewckm">' + fmtKm(c.km)
        + ' <i>' + t("crew.who.share", { n: share }) + "</i></span></div>";
    }).join("") + roleKey(rows) + "</div>";
  }

  // Build an optional section, or nothing. A section that cannot render is a section
  // missing; it is not a reason for the rest of the card to disappear. The console still
  // gets the error, because a swallowed exception is how this stays broken quietly.
  function safely(build) {
    try {
      return build();
    } catch (e) {
      if (window.console && console.error) console.error("crews: section failed", e);
      return "";
    }
  }

  /* ---------- sign-in ---------- */

  // Why THIS reader is on the sign-in card: the crew whose poster sent them, named.
  function inviteNote() {
    if (!INVITE) return "";
    var nm = crewName(INVITE.slug);
    // Nothing rather than a slug. A crew holding no ground is off the board, so the name can
    // genuinely be unknown here, and "You came here for nordlys-collective-456c" is worse
    // than the generic card this is meant to improve on.
    if (!nm) return "";
    return '<p class="crewinvnote">' + esc(t("crew.signin.invited", { name: nm })) + "</p>";
  }

  function signInHTML() {
    // Heading, the code, and where to put it. Nothing else.
    //
    // This card used to open with one of six rotating hooks and a sentence explaining what
    // crews are -- added because three reviewers in a row said the screen never says why you
    // would pair a phone for this. On a signed-out screen the BOARD is directly underneath it
    // saying the same thing, so the pitch was being made twice, and the second copy was the
    // one with the podium and real crew names in it. Erwin's call, and it is right: the card
    // is the way in, not the argument for coming in.
    //
    // No disclosure around the QR either. I folded it away on phones on the grounds that a QR
    // cannot be scanned by the device showing it -- true, and beside the point: it is the
    // thing the card is for, a second phone or a laptop camera is the ordinary case, and
    // hiding the one object on screen behind a word is worse than showing it to somebody who
    // does not need it. "Why can the QR code be hidden to log in? It makes no sense."
    return '<div class="crewcard crewsign">'
      + "<h3>" + t("crew.signin.h") + "</h3>"
      // Why THIS reader is here, when they arrived from a crew's poster. Specific to them and
      // not a pitch, so it stays.
      + inviteNote()
      + '<a class="crewqr" id="crewqr" href="#"><div class="spin"></div></a>'
      + '<div class="crewcode" id="crewcode">······</div>'
      // Which app, which version, and where the screen is inside it. Not "scan this with" --
      // a code and a QR are self-evidently things you scan or type, and naming the app and
      // its version already says which thing does the scanning. The app's name is a link in
      // whatever language the sentence is written in: the brand is the one part no locale
      // translates, so one replace covers all nineteen.
      + '<p class=hint id="crewcodehint">'
      + appLink(t("crew.signin.scan", { v: (CFG && CFG.min_app) || "" })) + "</p>"
      // The one case the line above does not cover: you are reading this ON the phone that
      // has the app, where there is nothing to point a camera at. The QR is itself the deep
      // link, so tapping the code is the whole answer.
      + '<p class="hint crewsame">' + t("crew.signin.tap") + "</p>"
      + "</div>";
  }

  // The code drawn as its own modules, so they can land one after another. Returns "" when
  // the server did not send them, and the caller falls back to the PNG.
  //
  // One element per module and the delay carried on each: 33x33 is 1,089 nodes, built once per
  // sign-in and thrown away with the card. A CSS-only diagonal is not possible here -- the
  // delay depends on row PLUS column, which no selector can express -- and an inline style is
  // cheaper than 1,089 rules.
  /* ---------- clipboard ----------
     `navigator.clipboard` is absent on an insecure origin and in some embedded webviews, so
     there is a fallback, and if that fails too the caller is told nothing happened rather
     than being shown a tick over a clipboard that never changed. */
  function copyVia(text, ok, fail) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok, function () { viaTextarea(text, ok, fail); });
      return;
    }
    viaTextarea(text, ok, fail);
  }

  function viaTextarea(text, ok, fail) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var done = false;
    try { done = document.execCommand("copy"); } catch (e) { done = false; }
    document.body.removeChild(ta);
    if (done) ok(); else fail();
  }

  // Any crew the panel knows about, by slug. The browse list is the richer source -- it
  // carries the description and the join policy -- and the board is the fallback for a crew
  // ranked but not in the sixty nearest.
  function crewBySlug(slug) {
    var i, c;
    for (i = 0; ALL && i < ALL.length; i++) if (ALL[i].slug === slug) return ALL[i];
    for (i = 0; BOARD && i < BOARD.length; i++) {
      c = BOARD[i];
      if (c.slug === slug) {
        return { slug: c.slug, name: c.name, emblem: c.emblem, description: c.description,
                 tiles: c.tiles, km2: c.km2, members: c.members,
                 join_policy: c.join_policy };
      }
    }
    return null;
  }

  // Opens the sheet for any crew, from wherever its emblem was pressed.
  function openCrewSheet(slug) {
    var c = crewBySlug(slug);
    if (!c) return;
    var mine = !!(ME && ME.crew && ME.crew.slug === slug);
    window.openModal(c.name, crewSheetHTML(mine && ME.crew ? ME.crew : c, mine));
    // The action belongs to the row that already owns it: every refusal, every confirm and
    // every price sentence lives on that button. Pressing this one closes the sheet and
    // presses that one, rather than growing a second copy of the join flow in a dialog.
    var go = document.getElementById("crewsheetgo");
    if (go) go.onclick = function () {
      window.closeModal();
      var btn = document.querySelector('[data-join="' + cssEscape(slug) + '"]');
      if (!btn) return;
      btn.scrollIntoView({ block: "center", behavior: "smooth" });
      setTimeout(function () { btn.click(); }, 260);
    };
  }

  // What a click on the crew's emblem opens: the mark at a size worth looking at, what the
  // crew says about itself, what it holds, and the code to find it. Every string and every
  // helper here already existed -- this is an arrangement, not nineteen new translations.
  function crewSheetHTML(c, mine) {
    var held = c.tiles || 0;
    return '<div class="crewsheet">'
      + '<img class="crewsheetemb" alt="" src="' + esc(c.emblem) + '">'
      + (c.description ? "<p>" + esc(c.description) + "</p>" : "")
      + '<div class="crewsheetfig"><b>' + tiles(held) + "</b>"
      + (held ? " <span>" + t("crew.mine.ao") + "</span>" : "") + "</div>"
      + '<div class="crewsheetsub">' + fmtKm2(c.km2)
      + " · " + riders(c.members || 0) + "</div>"
      // The same code the share sheet prints, for the same reason: it points at the crew and
      // not at an invite code, so rotating the code does not kill a sticker already on a
      // backpack.
      + (c.share_qr
         ? '<div class="crewsheetqr">' + qrGrid(c.share_qr)
           + '<p class="hint">' + esc(t("crew.pub.scan")) + "</p></div>"
         : "")
      + (c.share_url
         ? '<p class="crewshareu"><a href="' + esc(c.share_url) + '" target="_blank"'
           + ' rel="noopener">' + esc(c.share_url.replace(/^https?:\/\//, "")) + "</a></p>"
         : "")
      // Somebody else's crew: the sheet is where you decide, so it carries the same label the
      // browse row carries. Not shown for your own crew, and not while you are in one -- the
      // row itself is shut in that state and this must not offer what it would refuse.
      + ((!mine && c.join_policy && ME && ME.paired && !ME.crew && !ME.cooldown_until)
         ? '<div class="crewsheetact"><button class="crewbtn" id="crewsheetgo">'
           + esc(t(c.join_policy === "open" ? "crew.join.btn"
                   : c.join_policy === "invite" ? "crew.join.code" : "crew.join.ask"))
           + "</button></div>"
         : "")
      + "</div>";
  }

  function qrGrid(rows) {
    if (!rows || !rows.length) return "";
    var n = rows[0].length, cells = [], y, x;
    for (y = 0; y < rows.length; y++) {
      for (x = 0; x < n; x++) {
        cells.push('<i' + (rows[y].charAt(x) === "1" ? "" : ' class="o"')
                   + ' style="animation-delay:' + ((x + y) * 0.012).toFixed(3) + 's"></i>');
      }
    }
    // `role="img"` with a name, because a thousand empty elements have neither.
    return '<div class="crewqrg" role="img" aria-label="' + esc(t("crew.signin.qralt")) + '"'
      + ' style="grid-template-columns:repeat(' + n + ',1fr)">' + cells.join("") + "</div>";
  }

  var pairRolls = 0;
  var PAIR_MAX_ROLLS = 1;        // the code itself now lasts the fifteen minutes

  // Both ways a code can die end up here. The error path used to stop the timer and return
  // without rendering anything, so a dead six-character code sat on screen looking live with
  // nothing polling, no message and no link, and the only way out was closing the panel.
  // `err` so this can tell the two refusals apart. An EXPIRED code is a retry -- press it and
  // you get a fresh one. A spent rate limit is not: a reviewer pressed "Still waiting? Get a
  // fresh code" against a live 429 and got the identical screen back, which is a control whose
  // only function is to fail. The wait is what that reader needs, and `setStatus` has it.
  function offerRetry(err) {
    stopPairing();
    var code = document.getElementById("crewcode");
    // Not dots. Six middot characters where a code goes read as LOADING, and the spinner
    // inside the QR frame kept spinning forever behind them, so a refusal -- an expired code
    // or a spent rate limit -- was dressed as a wait. An em dash is a state, not a promise.
    if (code) { code.textContent = "—"; code.classList.add("dead"); }
    // The spinner too: it is inside `#crewqr` and nothing ever stopped it.
    var spin = document.querySelector("#crewqr .spin");
    if (spin && spin.parentNode) spin.parentNode.removeChild(spin);
    // The code was blanked to dots -- which reads as LOADING -- and everything else on the
    // card stayed live: a full-size scannable QR, and both `eucplanet://pair?code=…` links
    // still carrying the dead code, so the biggest thing on screen and the brightest button
    // on it both went on offering a pairing that could not happen.
    var qr = document.getElementById("crewqr");
    if (qr) {
      qr.classList.add("dead");
      qr.removeAttribute("href");
      qr.setAttribute("aria-hidden", "true");
    }
    // The code is dead, so the thing it links to is dead with it. The QR IS the link now --
    // there is no separate button to disable -- so the href comes off and the caption under
    // it goes quiet. `aria-disabled` rather than `aria-hidden`: hiding it left a reader with
    // a card whose only announced content was its heading.
    var qrl = document.getElementById("crewqr");
    if (qrl) {
      qrl.classList.add("dead");
      qrl.removeAttribute("href");
      qrl.setAttribute("aria-disabled", "true");
    }
    var tapn = document.querySelector(".crewsame");
    if (tapn) tapn.remove();
    var el = document.getElementById("crewcodehint");
    if (!el) return;
    var ecode = (err && (err.code || err.detail)) || "";
    if (ecode.indexOf("rate_limited") === 0) {
      // Nothing to press. The message `startPairing` just set says how long, and offering a
      // button next to it would say the opposite.
      el.innerHTML = "";
      return;
    }
    // A bare `<a>` on this card computes to the browser default #0000EE, underlined, 13px, on
    // near-black -- and in this state it is the only control left.
    el.innerHTML = '<a href="#" class="crewbtn mini" id="crewagain">'
      + t("crew.signin.again") + "</a>";
    var again = document.getElementById("crewagain");
    if (again) again.onclick = function (ev) {
      ev.preventDefault();
      pairRolls = 0;
      startPairing();
    };
  }

  function startPairing() {
    stopPairing();
    api("POST", "/api/v1/pair/start").then(function (r) {
      if (!r.ok) { setStatus(errMsg(r.err), true); offerRetry(r.err); return; }
      pairToken = r.body.token;
      var qr = document.getElementById("crewqr");
      var code = document.getElementById("crewcode");
      // the app-scheme form of the same link, so a tap on this device hands the code to the
      // app without a round trip through the web page
      // The host goes in RAW. `encodeURIComponent` turns it into https%3A%2F%2F..., and the
      // app's parser splits on & and = and never decodes -- so `looksLikeOrigin()` saw a
      // string that does not begin with "http://", the whole link parsed to null, and the
      // pairing screen fell back to the camera. Tapping "Open EUC Planet" opened a SCANNER,
      // on the one device that cannot scan the code it is looking at. Erwin hit it on his
      // own phone.
      // An origin has no & and no = in it, so raw is unambiguous here, and it is also what a
      // decoding parser gets after decoding -- this works on the app already installed and on
      // the one that fixes its side.
      var deep = "eucplanet://pair?code=" + encodeURIComponent(r.body.code)
        + "&host=" + location.origin;
      if (qr) {
        qr.innerHTML = qrGrid(r.body.qr_rows)
          || ('<img alt="' + esc(t("crew.signin.qralt")) + '" src="data:image/png;base64,'
              + r.body.qr + '"/>');
        qr.href = deep;
      }
      if (code) code.textContent = r.body.code;
      // No handler on the app link any more: it is an ordinary outbound link now. Kept as a
      // lookup so the block below still finds its other controls.
      var get = null;
      if (get) get.onclick = function (ev) {
        ev.preventDefault();
        var tab = document.querySelector('.dock button[data-p=tech]');
        if (tab) tab.click();
      };
      var left = r.body.expires_in;
      pairTimer = setInterval(function () {
        // Nothing is going to happen while the tab is in the background, and a code that
        // rolls forever is a code that eats the hourly budget for everybody sharing the
        // address. One idle tab was making about 1,800 requests an hour.
        if (document.hidden) return;
        left -= 2;
        if (left <= 0) {
          if (pairRolls++ >= PAIR_MAX_ROLLS) { offerRetry(); return; }
          startPairing();
          return;
        }
        api("GET", "/api/v1/pair/poll?token=" + encodeURIComponent(pairToken))
          .then(function (p) {
            if (p.ok && p.body.status === "paired") {
              stopPairing();
              // the one step that spans two devices is the one that most needs a visible
              // result; every other action already reveals itself
              reveal(".crewcard:not(.crewboard)");
              // after the render, not before it: show() replaces the whole panel body,
              // including the node this writes into, so setting it first wrote the one
              // confirmation that matters into an element that was gone a line later
              pendingStatus = t("crew.signin.ok");
              show();
            }
            else if (!p.ok) {
              // counted like any other roll. This path used to restart pairing without
              // touching the counter, so the "stops after five codes" promise did not cover
              // the one case that can repeat on its own.
              if (pairRolls++ >= PAIR_MAX_ROLLS) { offerRetry(); return; }
              startPairing();
            }
          });
      }, 2000);
    });
  }

  function stopPairing() {
    if (pairTimer) clearInterval(pairTimer);
    pairTimer = null;
  }

  // Asks inside the panel instead of through the browser. `ok` runs on yes and nothing runs
  // on no. The native confirm() was the one moment this stopped looking like itself, and on a
  // phone it is a system sheet thrown over a custom surface.
  // `near` is the button that was pressed. The dialog opens beside it rather than at the top
  // of the panel, because a question about the thing under your thumb belongs under your thumb.
  function askHost(near) {
    var top = document.getElementById("crewstatus");
    // One prompt at a time. Pressing a confirm trigger twice -- which is what a keyboard user
    // does when the first press gives no signal at all -- used to insert a second prompt, and
    // then two elements shared the id `crewask-y` and `getElementById` was a coin toss.
    var open = document.querySelector(".crewaskslot");
    if (open && open.parentNode) open.parentNode.removeChild(open);
    if (!near || !near.parentNode) return top;
    var slot = document.createElement("div");
    // A class, because `near` is often a flex child and so is this: inserted bare into a
    // `.crewrow` it became a fourth column, squeezed `.crewrown` to 0px wide and rendered the
    // crew's own meta line one word per line down a 378px-tall row, clipped at the panel
    // edge. The CSS gives it the full width instead.
    slot.className = "crewaskslot";
    near.parentNode.insertBefore(slot, near.nextSibling);
    return slot;
  }

  // `danger` decides which button is bright. Every confirm in the feature used to put the
  // affirmative in a ghost and Cancel in pink -- right for Leave, Disband and a new invite
  // code, where a stray tap should not be the easy path, and wrong for everything else. A
  // reviewer measured it against "Let in", which IS pink, and read the emphasis as flipping
  // between screens. The rule is the act, not the dialog: irreversible asks quietly.
  function ask(message, confirmLabel, ok, near, danger) {
    var host = askHost(near);
    if (!host) { if (window.confirm(message)) ok(); return; }
    function done() { if (host.id === "crewstatus") host.innerHTML = ""; else host.remove(); }
    // A dialog, named by its own question. Without this the trigger did not change, focus
    // A dialog, named by its own question. Without this the trigger did not change, focus
    // did not move and nothing was announced, so pressing Disband gave a keyboard or screen
    // reader user no signal whatsoever -- on every irreversible action in the feature, and
    // the one for Leave states the seven-day cost in the text nobody heard.
    var qid = "crewask-q";
    host.innerHTML = '<div class="crewask" role="alertdialog" aria-labelledby="' + qid + '">'
      + '<p id="' + qid + '">' + esc(message) + "</p>"
      + '<button class="crewbtn mini' + (danger ? " ghost" : "")
      + '" id="crewask-y" aria-describedby="' + qid + '">'
      + esc(confirmLabel) + "</button>"
      + '<button class="crewbtn mini' + (danger ? "" : " ghost") + '" id="crewask-n">'
      + t("crew.cancel") + "</button>"
      + "</div>";
    // `center`, not `nearest`. `nearest` stops as soon as the top edge is in view, so a
    // prompt opened near the bottom of the scroll box had its own buttons below the fold --
    // measured at 718.5-762.5px against a box ending at 759.3 -- and the error line pushed
    // them further out. What has to be reachable is the buttons, not the first line of the
    // question.
    host.scrollIntoView({ block: "center", behavior: "smooth" });
    function shut() {
      done();
      // Back where they came from, so the keyboard does not land at the top of the panel.
      if (near && near.focus) { try { near.focus(); } catch (e) {} }
    }
    host.querySelector("#crewask-n").onclick = shut;
    host.querySelector("#crewask-y").onclick = function () { done(); ok(); };
    var yes = host.querySelector("#crewask-y");
    var no = host.querySelector("#crewask-n");
    var box = host.querySelector(".crewask");
    // `aria-modal`, so a reader is told it is modal -- and then a focus cycle, so it is. Tab
    // from the confirm button used to land on Disband, one press away from the irreversible
    // control next door.
    if (box) box.setAttribute("aria-modal", "true");
    function cycle(e) {
      if (e.key === "Escape") { e.preventDefault(); shut(); return; }
      if (e.key !== "Tab") return;
      e.preventDefault();
      var fwd = !e.shiftKey;
      (document.activeElement === yes ? (fwd ? no : no) : (fwd ? yes : yes)).focus();
    }
    if (yes) yes.addEventListener("keydown", cycle);
    if (no) no.addEventListener("keydown", cycle);
    // Cancel, not the acting button. Three of the four things this dialog asks about --
    // removing a rider, disbanding the crew, taking the leadership -- cannot be undone by the
    // person pressing, and this file notes eleven lines up that a keyboard user presses a
    // confirm trigger twice when the first press seems to do nothing. Opening on `yes` made
    // that second press the disband. The bright button is already the way out in the CSS;
    // focus now says the same thing. Tab reaches the acting button in one press.
    if (no && no.focus) { try { no.focus(); } catch (e) {} }
  }

  // One handler, wherever the button was rendered: inside the crew card's action row, or in
  // the panel footer for the five paired states that have no crew card to put it in.
  // The (?) in the panel chrome. Bound on every render because `setPanel` rewrites the body
  // and this button lives OUTSIDE it, so the handler survives but the block it opens does not.
  function bindHelp() {
    var btn = document.getElementById("phelp");
    if (!btn) return;
    // `aria-haspopup`, not `aria-expanded`: it opens a dialog somewhere else, it does not
    // expand a region of its own. `aria-controls` went with the disclosure for the same
    // reason -- the thing it named is built when the dialog opens and does not exist before.
    btn.removeAttribute("aria-expanded");
    btn.removeAttribute("aria-controls");
    btn.setAttribute("aria-haspopup", "dialog");
    btn.onclick = function () {
      if (typeof window.openModal !== "function") return;   // older shell, nothing to open
      window.openModal(t("crew.how.h"), explainer());
    };
  }

  // A button that confirms on itself: its own label for a moment, then back. Disabled while
  // it says so, because a second press has nothing new to do and a button that answers twice
  // reads as having failed the first time. Re-entrant: pressing again mid-flash restarts it
  // rather than leaving the label stuck on "Copied".
  var TICK = '<svg class="crewtick" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M5 13l4 4L19 7"/></svg>';

  function flash(btn, word) {
    if (!btn) return;
    if (btn._flash) { clearTimeout(btn._flash); }
    else {
      btn._was = btn.innerHTML;
      // Measured BEFORE anything is replaced. Read afterwards it measures a button that has
      // already shrunk around a tick, which is the trap this pattern sets. Pinned, because
      // "Copied" is wider than "Copy" in English and wider still in German and Russian, and a
      // button that resizes reflows the row it sits in.
      btn.style.minWidth = btn.offsetWidth + "px";
    }
    // A tick, not the word: nothing to translate, nothing to outgrow the button, and the
    // live region below still says it in words for anyone who cannot see the button change.
    btn.innerHTML = TICK;
    btn.setAttribute("aria-label", word);
    btn.disabled = true;
    btn.classList.add("crewdone");
    btn._flash = setTimeout(function () {
      btn.innerHTML = btn._was;
      btn.removeAttribute("aria-label");
      btn.style.minWidth = "";
      btn.disabled = false;
      btn.classList.remove("crewdone");
      btn._flash = null;
    }, 1400);
  }

  function bindSignOut() {
    var so = document.getElementById("cm-signout");
    if (!so) return;
    so.onclick = function () {
      ask(t("crew.mine.signoutq"), t("crew.mine.signout"), function () {
        api("POST", "/api/v1/crews/signout", {}).then(function () { ME = null; show(); });
      }, so, true);
    };
  }

  // An in-panel prompt, same reasoning.
  // `opts` carries what used to be this function's one caller's assumptions: `max` is the
  // input's maxlength (16, the invite code's length -- a crew name runs to 28, so disbanding
  // could not be confirmed by typing it while the box stopped four characters short of
  // "Harbour Bridge Bombers"), `confirm` is the acting button's label, which said "Join" over
  // a prompt about ending a crew, and `danger` swaps the weights so the quiet button acts and
  // the bright one is the way out, the same way `ask()` treats everything irreversible.
  function askFor(message, placeholder, ok, near, opts) {
    opts = opts || {};
    var host = askHost(near);
    if (!host) { var v = window.prompt(message); if (v) ok(v); return; }
    function done() { if (host.id === "crewstatus") host.innerHTML = ""; else host.remove(); }
    host.innerHTML = '<div class="crewask"><p>' + esc(message) + "</p>"
      + '<div class="crewmsg bad crewaskmsg" role="alert" aria-live="assertive"'
      + ' aria-atomic="true" hidden></div>'
      // `crewaskplain` when what is being typed is a NAME rather than a code. The box
      // renders its content uppercase with 2px tracking, which is right for a six-character
      // pairing code read off one screen and typed into another, and wrong for the disband
      // gate: it says "Type Holmenkollen Climb to end it", you type exactly that, and the
      // box shows you HOLMENKOLLEN CLIMB. The comparison has been case-folded for several
      // rounds, so it works -- it just looks like it has not, on the one control in the
      // feature where looking wrong means believing you mistyped your own crew's name.
      + '<input id="crewask-in"' + ((opts.max || 16) > 16 ? ' class="crewaskplain"' : "")
      + ' placeholder="' + esc(placeholder) + '" maxlength="'
      + (opts.max || 16) + '">'
      + '<button class="crewbtn mini' + (opts.danger ? " ghost" : "") + '" id="crewask-y">'
      + esc(opts.confirm || t("crew.join.btn")) + "</button>"
      + '<button class="crewbtn mini' + (opts.danger ? "" : " ghost") + '" id="crewask-n">'
      + t("crew.cancel") + "</button>"
      + "</div>";
    // `center`, not `nearest`. `nearest` stops as soon as the top edge is in view, so a
    // prompt opened near the bottom of the scroll box had its own buttons below the fold --
    // measured at 718.5-762.5px against a box ending at 759.3 -- and the error line pushed
    // them further out. What has to be reachable is the buttons, not the first line of the
    // question.
    host.scrollIntoView({ block: "center", behavior: "smooth" });
    var input = host.querySelector("#crewask-in");
    input.focus();
    host.querySelector("#crewask-n").onclick = done;
    // `done()` used to run BEFORE `ok(v)`, so one wrong character in an eight-character invite
    // code cost the prompt, the typing and 3.7 screens of scrolling back to the row it opened
    // beside. The caller decides now: `ctl.close()` on success, `ctl.fail(msg)` to keep it.
    // The box is built empty with the prompt and filled later. Creating a `role="alert"`
    // node and writing its text in the same task frequently announces nothing at all, which
    // is the pitfall `statusHost` was written around and this had reintroduced.
    function fail(msg) {
      var box = host.querySelector(".crewaskmsg");
      if (!box) return;
      box.textContent = msg;
      box.hidden = false;        // built empty and hidden with the prompt; see above
      input.focus();
      input.select();
    }
    function go() {
      var v = input.value.trim();
      // An empty box used to run `done()` -- the CLOSE path -- so pressing Join on a blank
      // invite code silently destroyed the prompt, the row it opened beside, and 4.5 screens
      // of scrolling back to it, with nothing said. The box two lines up exists for exactly
      // this and was never used for it. Pressing the affirmative on an empty field is a
      // mistake, not a cancellation; Cancel is the cancellation and it is right there.
      if (!v) { fail(t("crew.e.empty")); return; }
      ok(v, { close: done, fail: fail });
    }
    // Clearing the message the moment the field changes. Leaving a red box over a field the
    // rider has already fixed makes them read their own correct typing looking for the
    // mistake -- and this prompt is where a mistyped invite code lands.
    input.oninput = function () {
      var box = host.querySelector(".crewaskmsg");
      if (box && !box.hidden) { box.hidden = true; box.textContent = ""; }
    };
    host.querySelector("#crewask-y").onclick = go;
    input.onkeydown = function (e) { if (e.key === "Enter") go(); };
  }

  // Every failure used to arrive as the server's own string: a rider who tried to join a full
  // crew read "crew_full" in a pink box, and the fourteen locales all answered in English.
  var ERRS = {
    expired: "crew.e.expired", unknown: "crew.e.expired", used: "crew.e.expired",
    no_rider: "crew.e.norider", busy: "crew.e.busy",
    crew_full: "crew.e.full", not_yourself: "crew.e.not_yourself", creation_closed: "crew.e.closed", forbidden: "crew.e.forbidden",
    not_leader: "crew.e.forbidden", not_paired: "crew.e.pass",
    crews_disabled: "crew.e.off",
    // the server says which screen the code belongs to; without this the panel printed the
    // generic failure over the top of a sentence that answers the question
    wrong_screen: "crew.e.wrongscreen",
    bad_invite: "crew.e.invite", bad_name: "crew.e.name", name_taken: "crew.e.taken",
    already_in_crew: "crew.e.increw", no_trips: "crew.e.notrips", cooldown: "crew.e.cooldown",
    bad_identity: "crew.e.identity", identity_taken: "crew.e.identity",
    // The same sentence the form shows under the locked swatch, so a hand-made request is
    // answered with the reason rather than with the generic failure.
    identity_locked: "crew.mine.colourlock",
    no_crew: "crew.e.gone",
    not_member: "crew.e.left", not_in_crew: "crew.e.left",
    promote_first: "crew.e.promote", bad_name_word: "crew.e.name.word",
    name_reserved: "crew.e.name.reserved",
    // all three used to answer "only a leader or officer can do that", to somebody pressing a
    // button only shown to people who are neither
    leader_active: "crew.e.leaderback", not_eligible: "crew.e.notyou",
    no_request: "crew.e.norequest",
    // a 2 MB file can still come out over the 64 KB cap once it is re-encoded, and "make it
    // smaller" is the wrong advice for a photograph that is already small
    too_large: "crew.e.image", not_an_image: "crew.e.image",
    too_large_after_encode: "crew.e.image2"
  };

  function errMsg(err) {
    var code = (err && (err.code || err.detail)) || "";
    if (code.indexOf("rate_limited") === 0) {
      // The server knows the figure and now sends it. "Slow down a second." was understating
      // an hour-long window by three orders of magnitude -- and the limit is per IP, so a
      // household or a cafe behind one address can spend each other's allowance with nothing
      // on screen admitting it. Minutes up to an hour, then hours, in the same compact shapes
      // `crew.drawn.in` and `crew.drawn.old` already use for the two units.
      var secs = err && err.retry_after;
      if (typeof secs === "number" && secs > 0) {
        if (secs < 3600) {
          return t("crew.e.rate.in", { n: Math.max(1, Math.round(secs / 60)) });
        }
        return t("crew.e.rate.inh", { n: Math.max(1, Math.round(secs / 3600)) });
      }
      return t("crew.e.rate");
    }
    var k = ERRS[code];
    // One error names a button, and a string that restates another string's text cannot stay
    // true across nineteen files: it quoted "Lascia la squadra" where the button says "Esci
    // dalla squadra", and a different verb again in Polish. It takes the label now.
    if (k === "crew.e.not_yourself") return t(k, { v: t("crew.mine.leave") });
    // "That code is not it." names the problem and stops. Every surface that shows this error
    // gets the half that says what to do about it, which is why it is joined here rather than
    // at the one call site that happens to be an invite prompt.
    if (k === "crew.e.invite") return t(k) + " " + t("crew.e.invite.ask");
    // The server's own figure, where it sent one. `crew.e.cooldown` said "Still cooling off
    // from the last one." while `cooldown_until` sat in the payload and the join card already
    // formatted it -- so the error was vaguer than the data behind it.
    if (k === "crew.e.cooldown") {
      var until = ME && ME.cooldown_until;
      var n = until ? daysUntil(until) : null;
      if (n != null && n >= 0) return t("crew.join.wait.p", { n: days(n) });
    }
    return k ? t(k) : t("crew.err");
  }

  var statusTimer;

  // Any write that comes back unauthorised repaints to the sign-in card, because every
  // control still on screen belongs to a session that no longer exists.
  // `not_member` and its relatives mean exactly "the card you are looking at is wrong", and
  // they were the ones that left the wrong card up: two tabs, leave in one, press Leave in the
  // other, and you got "You are not in that crew." above a card still naming the crew.
  var STALE = { not_member: 1, not_in_crew: 1, no_crew: 1, no_request: 1 };

  function onWrite(r) {
    if (r && r.status === 401) { ME = null; show(); return true; }
    var code = r && r.err && (r.err.code || r.err.detail);
    if (code && STALE[code]) {
      // The server's sentence is written for the rider it is about -- "You are not in that
      // crew." -- and a leader removing somebody sees it addressed to themselves, while they
      // plainly ARE in the crew. The reader here is whoever pressed the button, and what is
      // true for them is that the card they were looking at was out of date.
      pendingStatus = t("crew.e.stale");
      show();
      return true;
    }
    return false;
  }

  // A slot sitting directly after the control that was pressed, for as long as its message
  // is up. Without one, every failure painted into `#crewstatus` above the board: pressing
  // Save in crew settings five screens down put the error five screens up and scrolled the
  // form off the screen. `askHost` has done this for the confirm prompts since round four.
  var statusSlot = null;

  function statusHost(near) {
    if (statusSlot && statusSlot.parentNode) {
      statusSlot.parentNode.removeChild(statusSlot);
    }
    statusSlot = null;
    // Null, not `#crewstatus`. Returning the polite region here collided with `setStatus`'s
    // own choice of region: for a BAD message `top` is `#crewalert`, so an un-anchored
    // failure wrote the bare announcement into `#crewalert` and the styled `.crewmsg bad`
    // copy into `#crewstatus` -- both of them visible regions, 42px apart. The first screen a
    // stranger sees printed "Slow down a second." twice, in two different styles. The caller
    // falls back to `top` itself, which is the one region that message belongs in.
    if (!near || !near.parentNode) return null;
    statusSlot = document.createElement("div");
    statusSlot.className = "crewstatusnear crewaskslot";
    // The live attributes go on BEFORE any text does. A region created and filled in the same
    // frame is frequently not announced at all, which is the whole point of this.
    statusSlot.setAttribute("role", "status");
    statusSlot.setAttribute("aria-live", "polite");
    statusSlot.setAttribute("aria-atomic", "true");
    near.parentNode.insertBefore(statusSlot, near.nextSibling);
    return statusSlot;
  }

  // Every red box this panel can be showing, gone. The forms call it the moment the field
  // changes: leaving "Give it a name." over a name the rider has already typed makes them
  // read their own correct work looking for the mistake, and three separate forms did it.
  // Both the permanent regions and any anchored copy, because `setStatus` writes to both.
  function clearStatus() {
    ["crewstatus", "crewalert"].forEach(function (id) {
      var r = document.getElementById(id);
      if (r) r.innerHTML = "";
    });
    // The anchored copy is the one the rider is actually looking at, and `statusHost` already
    // keeps the handle to it -- removed the same way it removes the previous one.
    if (statusSlot && statusSlot.parentNode) {
      statusSlot.parentNode.removeChild(statusSlot);
    }
    statusSlot = null;
    clearTimeout(statusTimer);
  }

  function setStatus(msg, bad, near) {
    // An error is worth interrupting for and a confirmation is not, so there are two regions
    // rather than one that is re-roled on the spot: both are registered when the panel is
    // built, and the text goes into the one that matches.
    var polite = document.getElementById("crewstatus");
    var alert_ = document.getElementById("crewalert");
    var top = bad ? (alert_ || polite) : polite;
    var other = bad ? polite : alert_;
    if (other) other.innerHTML = "";
    var el = statusHost(near) || top;
    if (!el) return;
    // The ANNOUNCEMENT goes into the permanent region, which has been registered since the
    // panel was built; the anchored slot is the visual copy and is hidden from the
    // accessibility tree. Both regions were dead code until now, because every in-card caller
    // passes `near` and a region inserted with its text already in it is frequently not
    // announced at all -- the pitfall `statusHost`'s own comment is about.
    if (top) {
      top.textContent = msg || "";
      if (other) other.textContent = "";
    }
    if (el !== top) el.setAttribute("aria-hidden", "true");
    el.innerHTML = msg ? '<div class="crewmsg' + (bad ? " bad" : "") + '">'
      + esc(msg) + "</div>" : "";
    // The success path got scrolled into view and the failure path did not, so an error from
    // deep inside the crew card painted at the top of a scrolled panel where nobody saw it.
    clearTimeout(statusTimer);
    if (!msg) return;
    // A bad status had no timer, so failures accumulated: two live red boxes 1124px apart,
    // one of them about a prompt that had already been cancelled. The next write clears the
    // last failure -- see `statusHost`, which removes the previous anchored slot.
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    // A success banner is a toast, not furniture. "You're in" was still the loudest thing on
    // the panel long after it stopped being news.
    if (!bad) statusTimer = setTimeout(function () { setStatus(""); }, 5000);
  }

  /* ---------- create / manage ---------- */

  // One builder for both forms. The create card and the crew settings card ask the same
  // question -- what does this crew look like on the map -- and settings never offered it, so
  // the single most visible choice a crew makes was the one choice it could not revisit.
  //
  // A roving tabindex, because 24 swatches were 24 tab stops: crossing this one control cost
  // 28 presses in a form with two text fields, and reaching Leave crew took 56. One stop in,
  // arrows to move, one stop out, which is what a grid of toggles is supposed to do.
  // `aria-pressed` is unchanged; it is the state, and this is only the focus order.
  /* ---------- which identities are still free ----------

     Every swatch used to look available. Forty of ninety-six pairs were not, so about two
     founders in five pressed Create crew and got "Another crew already flies those colours",
     with no indication of which ones were free and the error still sitting under the field
     while they guessed again. The palette is forty-eight by twelve now, which makes the same
     forty-four crews a far smaller share, but the grid says it either way: a pair that is
     gone is not something to discover by submitting a form. */

  // "colour|pattern" keys, from the same payload that suggests a free pair. An object, not a
  // scan: this is asked once per swatch per redraw, and the pattern grid re-asks on every
  // colour press.
  function takenPairs() {
    var list = (window.__CREWIDENT__ && window.__CREWIDENT__.taken) || [];
    var s = Object.create(null);
    list.forEach(function (p) { s[p[0] + "|" + p[1]] = 1; });
    return s;
  }

  // A colour is only really gone when every pattern on it is gone -- with twelve patterns
  // that takes twelve crews agreeing on one colour, which is the point of widening the grid.
  function colourGone(taken, c, own) {
    return PATTERNS.every(function (p) {
      return taken[c + "|" + p] && !(own && own === c + "|" + p);
    });
  }

  // The pattern to land on for a colour: keep the one they have if it survives the move,
  // otherwise the first that is free. Pressing a colour must never leave the form holding a
  // pair the server will refuse.
  function freePattern(taken, c, want, own) {
    function ok(p) { return !taken[c + "|" + p] || (own && own === c + "|" + p); }
    if (want && ok(want)) return want;
    for (var i = 0; i < PATTERNS.length; i++) {
      if (ok(PATTERNS[i])) return PATTERNS[i];
    }
    return want || PATTERNS[0];
  }

  /* ---------- naming a colour ----------

     Forty-eight swatches labelled "Colour 1" through "Colour 48", while the twelve patterns
     beside them say "stripes" and "dots" -- in the one grid where a rider who cannot see the
     difference needs the words most. A reviewer put it exactly there.

     Derived from the hex rather than written out: forty-eight names in nineteen languages is
     nine hundred strings, and they would be a second copy of the palette that nothing keeps
     in step with it. Eleven hue words and two qualifiers describe any of them -- "dark red",
     "pale green", "teal" -- and a colour added to the palette is named without anybody
     writing anything. */
  // Pink runs to 345, not 330: #ff4081 sits at 340 and came out "red", which is the one
  // name nobody would give it.
  var HUES = [[15, "red"], [45, "orange"], [70, "yellow"], [100, "lime"], [160, "green"],
              [200, "teal"], [250, "blue"], [290, "purple"], [345, "pink"], [361, "red"]];

  function colourName(hex) {
    var r = parseInt(hex.substr(1, 2), 16) / 255,
        g = parseInt(hex.substr(3, 2), 16) / 255,
        b = parseInt(hex.substr(5, 2), 16) / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    var l = (mx + mn) / 2;
    // 0.2, not 0.3: #4a4a4a sits at .29 and came out "black", which is a different colour
    if (d < 0.09) return t("crew.hue." + (l > 0.72 ? "white" : l < 0.2 ? "black" : "grey"));
    var h = 0;
    if (mx === r) h = 60 * (((g - b) / d) % 6);
    else if (mx === g) h = 60 * ((b - r) / d + 2);
    else h = 60 * ((r - g) / d + 4);
    if (h < 0) h += 360;
    var name = "red";
    for (var i = 0; i < HUES.length; i++) {
      if (h < HUES[i][0]) { name = HUES[i][1]; break; }
    }
    // Brown is a dark, dull orange OR red -- #5d4037 is 11 degrees and came out "red".
    // The saturation test is what separates it from a genuinely dark red like #800000,
    // which is vivid and should keep its name.
    var sat = d / (1 - Math.abs(2 * l - 1) || 1);
    // 0.68 catches #9a6324 (ochre, sat .62) while #c2410c (burnt orange, sat .88) and the
    // vivid oranges keep their name -- those are protected by the lightness test anyway.
    if ((name === "orange" || name === "red") && l < 0.42 && sat < 0.68) name = "brown";
    var word = t("crew.hue." + name);
    if (l > 0.76) return t("crew.hue.pale", { v: word });
    if (l < 0.26) return t("crew.hue.dark", { v: word });
    return word;
  }

  function identGrids(prefix, ident) {
    var cols = (window.__CREWCFG__ && window.__CREWCFG__.palette) || [];
    var chosenC = cols.indexOf(ident.colour);
    if (chosenC < 0) chosenC = 0;
    var chosenP = PATTERNS.indexOf(ident.pattern);
    if (chosenP < 0) chosenP = 0;
    var taken = takenPairs();
    // The pair this form opened with is always allowed. For the settings form that is the
    // crew's OWN colours, which the server excludes from the clash check -- greying them out
    // would tell a leader their own identity was unavailable to them.
    var own = ident.colour + "|" + ident.pattern;
    // The tab stop cannot sit on a disabled button: it is not focusable, so the grid would
    // have no tab stop at all and keyboard users could not reach it.
    if (colourGone(taken, cols[chosenC], own)) {
      for (var k = 0; k < cols.length; k++) {
        if (!colourGone(taken, cols[k], own)) { chosenC = k; break; }
      }
    }
    return {
      colours: '<div class="crewpick" id="' + prefix + '-colours" role="group" aria-label="'
        + esc(t("crew.new.colours")) + '">' + cols.map(function (c, i) {
          var on = c === ident.colour;
          var gone = colourGone(taken, c, own);
          // The colour's own name, with its index after it: the name is what a reader needs
          // and the number is what tells two near-identical pinks apart.
          var label = t("crew.new.colourn", { v: colourName(c), n: i + 1 })
            + (gone ? " — " + t("crew.new.gone") : "");
          return '<button type="button" class="crewpickc' + (on ? " on" : "")
            + (gone ? " gone" : "")
            + '" aria-pressed="' + (on ? "true" : "false")
            + (gone ? '" disabled aria-disabled="true' : "")
            + '" tabindex="' + (i === chosenC ? "0" : "-1")
            + '" data-c="' + c + '" style="background:' + c + '" title="'
            + esc(label) + '" aria-label="' + esc(label) + '"></button>';
        }).join("") + "</div>",
      // `data-own` so the live update in `bindIdent` knows which pair to keep allowed without
      // re-deriving it from a hidden input that by then has already moved.
      // Whether any pattern on the chosen colour is actually struck out, so the caption that
      // explains the strike only appears when there is a strike to explain.
      patternsGone: PATTERNS.some(function (pt) {
        return taken[ident.colour + "|" + pt] && own !== ident.colour + "|" + pt;
      }),
      patterns: '<div class="crewpick" id="' + prefix + '-patterns" role="group" data-own="'
        + esc(own) + '" aria-label="'
        + esc(t("crew.new.patterns")) + '">' + PATTERNS.map(function (pt, i) {
          var on = pt === ident.pattern;
          var gone = taken[ident.colour + "|" + pt] && own !== ident.colour + "|" + pt;
          var label = t("crew.pattern." + pt) + (gone ? " — " + t("crew.new.gone") : "");
          return '<button type="button" class="crewpickp' + (on ? " on" : "")
            + (gone ? " gone" : "")
            + '" aria-pressed="' + (on ? "true" : "false")
            + (gone ? '" disabled aria-disabled="true' : "")
            + '" tabindex="' + (i === chosenP ? "0" : "-1")
            + '" data-p="' + pt + '" title="' + esc(label)
            + '" aria-label="' + esc(label) + '">'
            + '<span class="crewsw" data-p="' + pt + '" style="background:' + ident.colour
            + '"></span></button>';
        }).join("") + "</div>"
    };
  }

  // The identity block, label and preview and both grids, for whichever form asks.
  // The invite link. `#crews` is the panel deep-link the host already understands -- it
  // opens this panel and skips the intro -- and the two parameters are read back by
  // `pendingInvite()` on arrival. The crew is carried as well as the code because joining is
  // per-crew: `/crews/{slug}/join` takes the code in the body, so a code alone names nothing.
  // Encoded, because a slug is whatever the crew's name slugified to and that is not always
  // ASCII -- "Zurich Night 21" with an umlaut slugs to one.
  function inviteLink(c) {
    var base = location.origin + location.pathname;
    return base + "?crew=" + encodeURIComponent(c.slug)
         + "&code=" + encodeURIComponent(c.invite_code) + "#crews";
  }

  // What this page was opened with, read once. Held rather than acted on immediately: a rider
  // arriving on an invite link is usually not signed in yet, and the code has to survive the
  // pairing and the re-render that follows it.
  // A slug ALONE is enough. This used to require both, so `/?crew=nordlys-collective#crews`
  // -- which is exactly where the public crew page's "Join this crew" button sends you, and
  // the whole point of a printed sticker that outlives its invite code -- was discarded, and
  // a rider who had just scanned a backpack landed on an undifferentiated list of thirty-four
  // crews with no mention of the one they came for. The code is optional: it is what
  // pre-fills the prompt for an invite-only crew, and there is nothing to pre-fill without it.
  var INVITE = (function () {
    try {
      var q = new URLSearchParams(location.search);
      var slug = q.get("crew"), code = q.get("code");
      return slug ? { slug: slug, code: code || null } : null;
    } catch (e) { return null; }
  })();

  // The crew's name from whatever the browser already holds. `reloadTerritory()` runs from
  // `init`, so this is populated on page load whether or not the panel has ever been opened.
  // Null when the slug is not on the map -- a crew holding nothing is off the board -- and
  // every caller treats that as "say nothing" rather than printing a slug at somebody.
  function crewName(slug) {
    var src = BOARD || (TERR && TERR.crews) || [];
    for (var i = 0; i < src.length; i++) {
      if (src[i].slug === slug) return src[i].name || null;
    }
    return null;
  }

  function identBlock(prefix, ident) {
    var g = identGrids(prefix, ident);
    return '<div class="crewidentrow">'
      + "<div class=crewidentl>" + t("crew.new.colours")
      + '<span class="crewpreview">' + swatch(ident.colour, ident.pattern, 34) + "</span>"
      + "</div>"
      + g.colours
      // A visible label of its own. Four pattern swatches sat under twenty-four colour
      // swatches with nothing but an `aria-label`, so a sighted reader got an unexplained
      // second grid in a form whose every other control is labelled.
      + '<div class=crewidentl>' + t("crew.new.patterns") + "</div>"
      + g.patterns
      // What the struck-through chips mean, for the readers who cannot hover a `title`.
      //
      // Under the PATTERN grid only, and only when a pattern on the chosen colour is actually
      // gone. A colour is crossed out solely when all twelve of its patterns are taken, which
      // thirty-four crews across five hundred and seventy-six pairs will not manage, so this
      // sentence sat under a grid of forty-eight swatches none of which had ever been crossed
      // out -- describing a state that grid is effectively never in. A reviewer checked all
      // forty-eight and found zero. True of one grid and never of the other is not true.
      + '<p class="hint crewdimmed"' + (g.patternsGone ? "" : " hidden") + ">"
      + esc(t("crew.new.dimmed")) + "</p>"
      + "</div>"
      + '<input type="hidden" id="' + prefix + '-colour" value="' + ident.colour + '">'
      + '<input type="hidden" id="' + prefix + '-pattern" value="' + ident.pattern + '">';
  }

  // Wire one form's grids: selection, the preview, the hidden inputs, and arrow keys.
  function bindIdent(prefix, onChange) {
    // After a colour press the pattern grid is a question about a different colour, so which
    // patterns are free changes with it. This lives here rather than in either form's own
    // `sync` because both forms need it and only one of them has a `sync` at all.
    function refreshPatterns() {
      var wrap = document.getElementById(prefix + "-patterns");
      var hidC = document.getElementById(prefix + "-colour");
      var hidP = document.getElementById(prefix + "-pattern");
      if (!wrap || !hidC || !hidP) return;
      var taken = takenPairs(), own = wrap.dataset.own || "", c = hidC.value;
      // Move off a pattern this colour has already lost BEFORE anything is drawn. The form
      // must never be left sitting on a pair the server is going to refuse.
      var keep = freePattern(taken, c, hidP.value, own);
      if (keep !== hidP.value) hidP.value = keep;
      var btns = [].slice.call(wrap.querySelectorAll("button")), stop = -1;
      btns.forEach(function (b, i) {
        var pt = b.dataset.p, key = c + "|" + pt;
        var gone = !!taken[key] && own !== key, on = pt === hidP.value;
        var label = t("crew.pattern." + pt) + (gone ? " — " + t("crew.new.gone") : "");
        b.disabled = gone;
        b.classList.toggle("gone", gone);
        b.classList.toggle("on", on);
        if (gone) b.setAttribute("aria-disabled", "true");
        else b.removeAttribute("aria-disabled");
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.title = label;
        b.setAttribute("aria-label", label);
        if (on) stop = i;
      });
      // A roving tab stop has to land on something focusable, or the grid has no tab stop
      // and a keyboard cannot reach it at all.
      btns.forEach(function (b, i) { b.tabIndex = (i === stop && !b.disabled) ? 0 : -1; });
      // The caption that explains the strike follows the strike. It is built from the colour
      // the form opens on -- which the picker deliberately chooses to be a FREE one, so
      // nothing is struck and the caption is correctly absent -- and the grid redraws here
      // without it when you pick a colour somebody else flies. Shown and hidden with the
      // thing it describes.
      var cap = document.querySelector(".crewdimmed");
      if (cap) cap.hidden = !btns.some(function (b) { return b.disabled; });
      if (stop < 0 || btns[stop].disabled) {
        for (var j = 0; j < btns.length; j++) {
          if (!btns[j].disabled) { btns[j].tabIndex = 0; break; }
        }
      }
    }

    function wire(sel, attr, hidden, isColour) {
      var all = [].slice.call(document.querySelectorAll("#" + prefix + sel + " button"));
      if (!all.length) return;
      function mark(chosen) {
        all.forEach(function (o) {
          var on = o === chosen;
          o.classList.toggle("on", on);
          o.setAttribute("aria-pressed", on ? "true" : "false");
          o.tabIndex = on ? 0 : -1;
        });
      }
      all.forEach(function (b, i) {
        b.onclick = function () {
          if (b.disabled) return;
          var h = document.getElementById(prefix + hidden);
          if (h) h.value = b.dataset[attr];
          mark(b);
          // order matters: the pattern may have to move before the preview is repainted,
          // or the preview shows the pair the form just stopped holding
          if (isColour) refreshPatterns();
          if (onChange) onChange();
        };
        b.onkeydown = function (e) {
          var dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1
                  : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1
                  : e.key === "Home" ? 1 : e.key === "End" ? -1 : 0;
          if (!dir) return;
          e.preventDefault();
          // Step over the ones that are gone. They are `disabled`, so focusing one is a
          // no-op that leaves the tab stop on an unfocusable button -- the grid would look
          // like it had simply stopped responding to the arrow keys.
          var j = e.key === "Home" ? -1 : e.key === "End" ? all.length : i;
          do { j += dir; } while (j >= 0 && j < all.length && all[j].disabled);
          if (j < 0 || j >= all.length) return;
          // Focus only. Clicking here too meant a swatch could never be focused WITHOUT
          // being selected, so the `:focus-visible` rule written for exactly this case could
          // never match -- an unreachable rule is not a fixed one. Arrows move, Space or
          // Enter chooses, and the tab stop follows the focus so leaving and coming back
          // returns to where you were.
          all.forEach(function (o) { o.tabIndex = o === all[j] ? 0 : -1; });
          all[j].focus();
        };
      });
    }
    wire("-colours", "c", "-colour", true);
    wire("-patterns", "p", "-pattern", false);
  }

  // The same shape the server checks, so a typo costs a keystroke instead of a round trip
  // and a red line in the console. `maxlength="28"` already proved the client knew the rule;
  // what it did not do was say WHICH half failed -- one sentence answered an empty box, three
  // spaces, two emoji, "ab" and `<b>hi</b>` alike, and led with the length every time.
  //
  // `\p{L}\p{N}_` and NOT `\w`. The server's pattern is `[\w \-'&.]` with `re.UNICODE`, where
  // `\w` matches any Unicode word character -- Кланы, 戦隊兵, Zürich and كلان all pass it. In
  // JavaScript `\w` is ASCII-only no matter what flags it carries, even `/u`, so my first
  // version of this check refused four of the five non-Latin names the server accepts. A
  // client check stricter than the server is worse than no client check at all: it turns a
  // working name into an error nobody can get past.
  //
  // Verified against the server for Nordlys, Кланы, 戦隊兵, Zürich Crew, كلان (all accepted)
  // and `Rev B 🛞 Crew`, `<b>hi</b>` (both refused). It does not cover emoji, and nor
  // does the server.
  //
  // The line that used to be here said Python's `\w` "also covers combining marks, hence
  // `\p{M}`". It does not: a mark is category Mn/Mc and `str.isalnum()` is false for it. So
  // every DECOMPOSED name passed here and was refused by the server -- and decomposed is what
  // iOS and macOS text input commonly hand you. A reviewer measured `Zürich Crew` accepted
  // by this regex and refused with "3-28 characters." about a twelve-character name. The
  // server is written by category now and normalises first; see `crews.name_ok`.
  var NAME_OK = /^[\p{L}\p{N}\p{M}_ \-'&.]{3,28}$/u;
  // And something to read it by. The set above is all that was required, so "..." -- three
  // permitted characters -- was a legal crew name, and the server's slugger turned it into
  // eight random hex digits because there was nothing left to slug. The server mirrors this.
  var NAME_HAS_WORD = /[\p{L}\p{N}]/u;

  function nameProblem(v) {
    // NFC first, and for the same reason the server does it: otherwise the two sides measure
    // different strings. `u` + `U+0308` is two characters here and one after normalising, so a
    // 28-character name with accents could be refused as too long by the browser and accepted
    // by the server, which is the same disagreement the other way round.
    var raw = (v || "").trim();
    var name = raw.normalize ? raw.normalize("NFC") : raw;
    if (!name) return "crew.e.name.empty";
    if (name.length < 3) return "crew.e.name.short";
    if (name.length > 28) return "crew.e.name.long";
    if (!NAME_OK.test(name)) return "crew.e.name.chars";
    // Its own message. `crew.e.name.chars` lists "- ' & ." as permitted, which is a strange
    // thing to tell somebody who typed "...".
    if (!NAME_HAS_WORD.test(name)) return "crew.e.name.word";
    return null;
  }

  // Write a refusal under the field it is about, and mark the field. Both forms do this on
  // submit; the live `oninput` path writes the same span while you type.
  function nameWhy(id, box, key) {
    var el = document.getElementById(id);
    if (el) el.textContent = key ? t(key) : "";
    if (box) {
      if (key) box.setAttribute("aria-invalid", "true");
      else box.removeAttribute("aria-invalid");
    }
  }

  function createHTML(ident) {
    // No invented fallback. Nothing is preselected until the server says what is free, which
    // is a form with no colour chosen rather than a form lying about one.
    ident = ident || { colour: null, pattern: null };
    // Colours are swatches, not a dropdown of hex codes. Nobody picks a crew identity by
    // reading "#000075", and the thing being chosen is the thing you will see on the map, so
    // the picker shows it with the pattern already on it. `identBlock` builds both grids.

    return '<div class="crewcard">'
      + "<h3>" + t("crew.new.h") + "</h3>"
      + '<p class=hint>' + t("crew.new.p") + "</p>"
      // No ghost text. It was "Nordlys Collective" and "Oslo, mostly after dark." --
      // hardcoded English in all eighteen locales, and the name and description of
      // crew #8 on the board, visible in the join list directly under this form. A
      // rider who took the hint got `name_taken`.
      // A counter, because `maxlength` clips in silence: 200 characters typed in became a
      // crew called `AAAAAAAAAAAAAAAAAAAAAAAAAAAA` with nothing on screen saying the rest had
      // gone. The description has had one since round seventeen.
      + "<label>" + t("crew.new.name")
      // No `maxlength`. It clipped in silence, which is the one thing every other refusal
      // in this form refuses to do: 36 characters pasted in became a crew named after the
      // first 28 with nothing said, and a 42-character paste CREATED one. The only signal was
      // a counter reading 28/28, which looks like a field that is merely full. `nameProblem`
      // has carried `crew.e.name.long` since it was written and could never reach it, because
      // the attribute made the state unreachable. The counter turns over now and the rule
      // appears under the field while there is still something to do about it.
      + '<input id="cf-name" aria-describedby="cf-namewhy">'
      + '<span class="crewcount" id="cf-namecount">0/28</span>'
      // Not a live region. The same sentence announced on every keystroke is how a screen
      // reader is made unusable; `aria-invalid` on the field carries the state instead, and
      // the spoken version still happens once, on submit, where it always did.
      + '<span class="crewwhy" id="cf-namewhy"></span>' + "</label>"
      // A textarea. 275 characters in the old `<input maxlength="280">` measured
      // scrollWidth 1639 against clientWidth 291, so you read back the last 35 with the
      // leading glyph cut in half -- for a string the join list renders as two lines.
      + "<label>" + t("crew.new.desc")
      + '<textarea id="cf-desc" rows="2" maxlength="280"></textarea>'
      + '<span class="crewcount" id="cf-desccount">0/280</span>' + "</label>"
      + identBlock("cf", ident)
      + "<label>" + t("crew.new.who")
      + '<select id="cf-policy">'
      + '<option value="approval">' + t("crew.new.approval") + "</option>"
      + '<option value="open">' + t("crew.new.open") + "</option>"
      + '<option value="invite">' + t("crew.new.invite") + "</option>"
      + "</select></label>"
      + '<button class="crewbtn" id="cf-go">' + t("crew.new.go") + "</button>"
      + "</div>";
  }

  function bindCreate() {
    var preview = document.querySelector(".crewpreview .crewsw");
    var hidC = document.getElementById("cf-colour");
    var hidP = document.getElementById("cf-pattern");
    if (!hidC || !hidP) return;

    function sync() {
      if (preview) {
        preview.style.background = hidC.value;
        preview.dataset.p = hidP.value;
      }
      // the pattern swatches show the chosen colour, so the two choices are seen together
      document.querySelectorAll("#cf-patterns .crewsw").forEach(function (el) {
        el.style.background = hidC.value;
      });
    }

    // One wiring for both forms: selection, `aria-pressed`, the roving tabindex and the
    // arrow keys all live in `bindIdent`, which the settings form calls as well.
    bindIdent("cf", sync);
    // You cannot read back what you cannot see, so say how much of it there is.
    [["cf-desc", "cf-desccount", 280], ["cf-name", "cf-namecount", 28]]
      .forEach(function (pair) {
        var box = document.getElementById(pair[0]);
        var out = document.getElementById(pair[1]);
        if (!box || !out) return;
        box.oninput = function () {
          clearStatus();          // the submit error is about text that has just changed
          out.textContent = box.value.length + "/" + pair[2];
          // The counter is the only thing on screen while you are still typing, so it has to
          // be able to say "too long" and not just "full". 28/28 and 36/28 looked identical
          // before, because the field could not hold 36.
          out.classList.toggle("over", box.value.length > pair[2]);
          // The name's rule, while there is still something to do about it. Silent: see the
          // note on `#cf-namewhy`. Nothing is said about an empty field -- a form that
          // objects before you have typed is worse than one that answers when you ask.
          if (pair[0] !== "cf-name") return;
          var why = document.getElementById("cf-namewhy");
          if (!why) return;
          var problem = box.value.trim() ? nameProblem(box.value) : null;
          why.textContent = problem ? t(problem) : "";
          if (problem) box.setAttribute("aria-invalid", "true");
          else box.removeAttribute("aria-invalid");
        };
      });
    sync();

    var go = document.getElementById("cf-go");
    if (go) go.onclick = function () {
      var nmf = document.getElementById("cf-name");
      var why = nameProblem(nmf && nmf.value);
      if (why) {
        // Under the field, and ONLY there. The live hint stays quiet while the box is empty
        // -- a form that objects before you have typed is worse than one that answers when
        // you ask -- so on submit "Give it a name." was reaching the anchored status line and
        // nothing else, while every other name rule filled the hint as you typed.
        //
        // Writing both put the identical sentence on screen twice, twenty pixels apart. The
        // field is focused and `aria-describedby` points at the hint, so moving focus is what
        // announces it; a second copy announced nothing new and read as a stutter.
        clearStatus();
        nameWhy("cf-namewhy", nmf, why);
        if (nmf) { nmf.focus(); nmf.select(); }
        return;
      }
      go.disabled = true;
      api("POST", "/api/v1/crews", {
        name: document.getElementById("cf-name").value,
        description: document.getElementById("cf-desc").value,
        colour: hidC.value,
        pattern: hidP.value,
        join_policy: document.getElementById("cf-policy").value
      }).then(function (r) {
        go.disabled = false;
        if (r.ok) { reveal(".crewmine-wrap"); show(); reloadTerritory(); }
        else if (!onWrite(r)) {
          var ecode = (r.err && (r.err.code || r.err.detail)) || "";
          if (ecode === "identity_taken" || ecode === "bad_identity") {
            // `taken` goes out of date the moment another crew is founded -- the endpoint
            // says so in its own comment -- so this refusal means the browser's copy is
            // stale, and nothing was re-fetching it. The form went on holding the pair the
            // server had just refused, the grid went on drawing that pattern as free, and
            // Create failed identically on every press after. One GET fixes all of it,
            // because the picker already moves the selection off a taken pair before it
            // draws the grid.
            //
            // And the message goes under the COLOUR, not under the name. Every server error
            // in this form was anchored to `#cf-name` and then focused and selected it, so
            // "Another crew already flies those colours." arrived under the one field that
            // was fine, with its text highlighted ready to be typed over.
            // Everything they typed, carried across the rebuild. Only the identity is
            // allowed to change, because the identity is the thing that was refused.
            var draft = {};
            ["cf-name", "cf-desc", "cf-policy"].forEach(function (id) {
              var el = document.getElementById(id);
              if (el) draft[id] = el.value;
            });
            api("GET", "/api/v1/crews/identity").then(function (ir) {
              if (ir.ok) window.__CREWIDENT__ = ir.body;
              pendingDraft = draft;
              pendingStatus = errMsg(r.err);
              pendingBad = true;
              pendingNear = "cf-colours";
              show();
            });
            return;
          }
          // Anchored to the FIELD, not the button. Anchoring to the button and then focusing
          // the field is two scrolls, and `focus()` runs last and wins, so the message ended
          // up at top 794 of a 492px viewport -- a focused empty box and no words.
          var nm = document.getElementById("cf-name");
          setStatus(errMsg(r.err), true, nm || go);
          if (nm) { nm.focus(); nm.select(); }
        }
      });
    };
  }

  // Where this crew sits and how far off the one above, from the board the browser already
  // holds. Nothing on the crew card said either.
  // The board the panel is showing, so the rank on your card and the table under it cannot
  // disagree. `TERR.crews` is the cached territory payload and the board is live: the cache
  // had sixteen crews and the board thirteen, so a card read "16th" over a thirteen-row
  // table, and a crew the board does not list got a rank of its own anyway.
  var BOARD = null;
  // The browse list, kept so the emblem sheet can answer about any crew and not only
  // your own. `_crew_brief` carries the description, the figures and the policy already.
  var ALL = null;

  /* ---------- what changed since you last looked ----------

     Every reviewer who scored FUN below 9 said a version of the same thing: the mode is
     entirely pull. A square about to flip, a rival creeping up, a rank change -- all of it
     exists, all of it is already on the client, and none of it is told to you. You have to
     open the panel and go looking, which means the panel never pays you for opening it.

     So the crew card leads with the diff. Per browser, in localStorage, because this is one
     reader's "last time I looked" and nobody else's -- it is not state the server should hold
     and not something another device should inherit. Written as it is read, so a line you
     have seen does not greet you twice.
  */
  function seenKey(slug) { return "eucstats_crewseen_" + slug; }

  function lastSeen(slug) {
    try { return JSON.parse(localStorage.getItem(seenKey(slug)) || "null"); } catch (e) { return null; }
  }

  function markSeen(slug, snap) {
    try { localStorage.setItem(seenKey(slug), JSON.stringify(snap)); } catch (e) {}
  }

  function rankOf(slug) {
    var src = BOARD || (TERR && TERR.crews);
    if (!src || !src.length) return null;
    var rows = src.slice().sort(function (a, b) {
      return (b.best_tiles || 0) - (a.best_tiles || 0);
    });
    for (var i = 0; i < rows.length; i++) if (rows[i].slug === slug) return i + 1;
    return null;
  }

  // Null when there is no figure to take a snapshot OF. This returned `{t: 0}` for a missing
  // territory object, and zero is a number: compared against a stored 15 it is not "I don't
  // know", it is "you lost fifteen squares". That is exactly what happened -- see the note on
  // `territory` in `/crews/me` -- and it lit the dock badge permanently for every member.
  // An absent reading has to be absent, or every consumer has to remember to check, and one
  // of them will not.
  function sinceSnap(slug, terr) {
    if (!terr || typeof terr.best_tiles !== "number") return null;
    return { t: terr.best_tiles, r: rankOf(slug) };
  }

  // The phrases, from two snapshots and nothing else. Split out of `sinceLine` because the
  // dock dot has to ask the same question WITHOUT answering it: `sinceLine` marks as it
  // reads, which is right for a line you have now seen and fatal for a badge -- the first
  // peek would consume the news it exists to announce.
  function sinceBits(was, now) {
    var bits = [];
    // Either side missing is no comparison: a first visit, or a reading we do not have.
    if (!was || !now) return bits;
    var dt = now.t - (was.t || 0);
    if (dt) bits.push(t(dt > 0 ? "crew.since.up" : "crew.since.down", { v: tiles(Math.abs(dt)) }));
    // Climbing is a smaller number, which is the one place in this panel where down is good.
    if (now.r && was.r && now.r !== was.r) {
      bits.push(t(now.r < was.r ? "crew.since.rose" : "crew.since.fell",
                  { a: ordinal(was.r), b: ordinal(now.r) }));
    }
    return bits;
  }

  function sinceLine(c, terr) {
    if (!c || !c.slug) return "";
    var now = sinceSnap(c.slug, terr);
    // Read once and held: `markSeen` below overwrites it, and the card needs to know which
    // way the change went, not only that there was one.
    var lastSeen_ = lastSeen(c.slug);
    var bits = sinceBits(lastSeen_, now);
    // Never record a reading we do not have, or the next visit compares against nothing.
    if (now) markSeen(c.slug, now);
    // Read, so the badge that sent them here has nothing left to announce.
    dockNews = false;
    dockDot(dockKnocks, false);
    if (!bits.length) return "";
    var joined = bits.join(" &middot; ");
    // Gained squares, or climbed the board. Every one-shot card in this mode announces
    // something going wrong -- turned down, removed, folded under you -- and taking ground
    // arrived as a grey line in a corner of a card you had to scroll to. A reviewer named it:
    // nothing ever congratulates you. Good news gets the same shape and the same prominence
    // the bad news has always had, and only when it is true, which is what keeps it worth
    // reading. Losing ground keeps the quiet line: a crew that is shrinking does not need a
    // banner about it, it needs the number.
    var was = lastSeen_;
    var up = !!was && ((now.t - (was.t || 0)) > 0
                       || (now.r && was.r && now.r < was.r));
    if (!up) return '<p class="crewsince">' + t("crew.since.h") + " " + joined + "</p>";
    return '<div class="crewgood"><h4>' + esc(t("crew.good.h")) + "</h4>"
      + "<p>" + t("crew.good.p", { v: joined }) + "</p></div>";
  }

  // One of the three one-shot notices has just been built into the HTML about to go on
  // screen, so tell the server it has been said. `/crews/me` used to retire the notice as it
  // reported it, which only works if exactly one GET is ever made per page -- and the panel
  // makes three, two of them 3ms apart. The first spent the news and the render saw nothing,
  // so being turned down, being removed and having your crew fold under you were all
  // written, translated nineteen times, and never shown to anybody.
  //
  // Fired once per notice per panel build; the endpoint is idempotent, so a repaint costs
  // nothing and a dropped request only means the card appears again, which is the safe way
  // round for news this reader has to act on.
  var noticeAcked = null;
  // And held, for the life of the panel. Making the GET idempotent fixed the server half and
  // left a client half: the panel fires four requests in `render()` and `primeDock` fires its
  // own, so a fetch ISSUED after the acknowledgement comes back without the notice and the
  // render it drives blanks the card the previous render just put up. Measured: the ack went
  // out, and the finished page showed the plain join list with no mention of the refusal.
  // So once a notice has been seen it stays on screen until the rider is in a crew or the
  // panel is closed, whatever later responses say.
  var heldNotice = null;

  function ackNotice(kind) {
    if (!kind || noticeAcked === kind) return;
    noticeAcked = kind;
    api("POST", "/api/v1/crews/notices/seen", { kind: kind });
  }

  // Remember whichever notice a response carried, and put it back into one that has stopped
  // carrying it. Order matches the render chain below, so the card that wins here is the card
  // that would have won there.
  function holdNotice(me) {
    if (!me || !me.paired) return;
    if (me.crew) { heldNotice = null; noticeAcked = null; return; }
    if (me.removed_by) heldNotice = { k: "removed_by", v: me.removed_by };
    else if (me.folded) heldNotice = { k: "folded", v: me.folded };
    else if (me.declined_by) heldNotice = { k: "declined_by", v: me.declined_by };
    else if (heldNotice) me[heldNotice.k] = heldNotice.v;
  }

  // The same comparison, read-only, for the dock badge.
  function sinceNews(c, terr) {
    if (!c || !c.slug) return false;
    return sinceBits(lastSeen(c.slug), sinceSnap(c.slug, terr)).length > 0;
  }

  function standing(slug) {
    var src = BOARD || (TERR && TERR.crews);
    if (!src || !src.length) return "";
    var rows = src.slice().sort(function (a, b) {
      return (b.best_tiles || 0) - (a.best_tiles || 0);
    });
    var i = -1;
    rows.forEach(function (c, n) { if (c.slug === slug) i = n; });
    if (i < 0) return "";
    if (i === 0) return '<span class="crewgap top">' + t("crew.rank.top") + "</span>";
    var gap = (rows[i - 1].best_tiles || 0) - (rows[i].best_tiles || 0);
    // Your own place first. `crew.rank.off` names the crew ABOVE you -- "1 off 7th" when you
    // are eighth -- and it was the only ordinal on your own card, so it read as your rank.
    return '<span class="crewgap">' + esc(ordinal(i + 1)) + " &middot; "
      // `tiles()`, so the noun agrees. As a bare count this was the one number on the
      // card's most prominent line with nothing after it for a locale to inflect: ja read
      // `2位まで5`, a numeral with no counter, and fr and nl read "5 of 2nd".
      + t(gap === 0 ? "crew.rank.level" : "crew.rank.off",
          { n: tiles(gap), v: ordinal(i) }) + "</span>";
  }

  // 1st, 2nd, 3rd from the host's own podium words, and the bare suffix past that. This
  // returned "{n}th" for every number, so second, third and fourth place read "1 off 1th".
  function ordinal(n) {
    if (n >= 1 && n <= 3 && H.t) {
      var pod = H.t("pod." + n);
      if (pod && pod.indexOf("pod.") !== 0) return pod.toLowerCase();
    }
    var s = t("crew.rank.nth", { n: n });
    // English, Swedish and Ukrainian are the three locales whose suffix is irregular;
    // English is the only one whose template
    // ends in "th" -- every other table carries its own correct form ("{n}.", "{n}e",
    // "{n}位"). Without this the board read "21th", "22th", "31th" past the podium.
    if (/th$/.test(s) && !(n % 100 >= 11 && n % 100 <= 13)) {
      var tail = { 1: "st", 2: "nd", 3: "rd" }[n % 10];
      if (tail) return s.slice(0, -2) + tail;
    }
    // Swedish is the second, and the comment above used to say there was only one. `{n}:a`
    // is right for 1, 2, 21 and 22 and wrong for everything else -- 3:e, 4:e, 11:e, 13:e --
    // so the board printed "8:a" for eighth while this locale's own `pod.3` showed "3:e".
    if (/:a$/.test(s)) {
      var d = n % 10, h = n % 100;
      if (!((d === 1 || d === 2) && h !== 11 && h !== 12)) return s.slice(0, -1) + "e";
    }
    // Ukrainian is the third, and this comment said there were two until a reviewer found
    // `1-ше` and `13-е` in the same list. The suffix follows the last word of the spelled-out
    // ordinal -- перше, друге, третє, сьоме, восьме, and -те for everything else, with the
    // teens taking -те. Russian uses the same `{n}-е` template and is genuinely correct with
    // it (перше after a vowel), which is why this is keyed on the locale and not the shape.
    if (locale() === "uk" && /-\u0435$/.test(s)) {
      var ud = n % 10, uh = n % 100;
      var tail = (uh >= 11 && uh <= 19) ? "\u0442\u0435"
        : ud === 1 ? "\u0448\u0435"
        : ud === 2 ? "\u0433\u0435"
        : ud === 3 ? "\u0442\u0454"
        : (ud === 7 || ud === 8) ? "\u043c\u0435"
        : "\u0442\u0435";
      return s.slice(0, -1) + tail;
    }
    return s;
  }

  // Where the crew holding a square stands, so a row says what the prize is worth. Six rows
  // naming one rival and nothing about them is a list of errands.
  // Worked out once per board rather than once per row: this sorted the whole crew list on
  // every row it rendered to answer the same question eight times.
  var STAND = null, STAND_FOR = null;

  function holderStanding(name) {
    if (!TERR || !TERR.crews || !name) return null;
    if (STAND_FOR !== TERR.crews) {
      STAND = {};
      TERR.crews.slice().sort(function (a, b) {
        return (b.best_tiles || 0) - (a.best_tiles || 0);
      }).forEach(function (c, i) {
        if (c.name && !STAND[c.name]) STAND[c.name] = { place: i + 1, tiles: c.best_tiles || 0 };
      });
      STAND_FOR = TERR.crews;
    }
    return STAND[name] || null;
  }

  function myCrewHTML(me) {
    var c = me.crew, lead = me.role === "leader" || me.role === "officer";
    // No head block. It held the emblem and the line with the standing, the members and your
    // role; all of that is in the accordion's title now, which is eighteen pixels above and is
    // what names the crew. What was left was two nested divs with nothing between them.
    var h = '<div class="crewcard crewmine">';
    if (me.status === "pending") {
      // The payload knew `leader_gone` all along and the card said "Waiting on a leader to
      // let you in" regardless, to a rider whose crew has nobody who could ever answer them.
      h += '<div id="crewwhywait" class="crewmsg' + (me.leader_gone ? " bad" : "") + '">'
        + t(me.leader_gone ? "crew.join.pending.none" : "crew.join.pending") + "</div>";
      // An id, because the browse list below this has a row of shut buttons whose reason is
      // this sentence, and a button has to carry its own reason -- see `whyShut` in joinHTML.
    }
    // First inside the card, not last. This used to sit below the description, the territory
    // figures, the invite code and the refusal list -- 4.6 screenfuls down at 390x844 -- and
    // it is the only thing on the card that is waiting on the reader to do something.
    if (me.pending && me.pending.length) {
      h += '<div class="crewpend crewknock"><h4>' + t("crew.pending.h")
        + ' <span class="crewknockn">' + me.pending.length + "</span></h4>"
        + me.pending.map(function (p) {
            // No separating space: the stylesheet gives the flag a 7px right margin, so a
            // literal one on top of it put the knock rows at 10.17px against the roster's
            // 7.00px -- the two lists in the same card starting their names 3.17px apart. I
            // claimed in a commit that both were fixed and had measured only the first
            // `.crewpendr`, which is a roster row.
            // What they have ridden, and when they asked. The two buttons under this are a
            // judgement about a person, and the row used to carry a flag and a handle --
            // less than the contributors list two blocks down says about everybody already
            // in. A rider who has ridden nothing in the window gets no figure rather than a
            // "0 km" that reads as an accusation; the date they asked still stands.
            var facts = [p.km ? fmtKm(p.km) : "", p.asked ? askedAgo(p.asked) : ""]
                          .filter(Boolean).join(" · ");
            return '<div class="crewpendr"><span>'
              + (p.flag ? cc(p.flag) : "") + esc(p.name)
              + (facts ? '<small class="crewpendf">' + facts + "</small>" : "") + "</span>"
              + '<button class="crewbtn mini" data-ok="' + esc(p.store_id) + '"'
              + ' data-name="' + esc(p.name) + '">' + t("crew.accept") + "</button>"
              + '<button class="crewbtn mini ghost" data-no="' + esc(p.store_id) + '"'
              + ' data-name="' + esc(p.name) + '">' + t("crew.decline") + "</button>"
              + "</div>"; }).join("")
        + "</div>";
    }
    // Classed so a rename can find it. See the save handler: this card is deliberately NOT
    // rebuilt after an edit, so whatever changed has to be written into the DOM by hand.
    if (c.description) h += '<p class="crewdesc">' + esc(c.description) + "</p>";
    h += '<div class="crewterr" id="crewterr"><div class=spin></div></div>';
    // The clock, on the card a rider opens to look at their own ground. It lived only at the
    // bottom of two cards you reach by scrolling, phrased as a question about the list above
    // it. Four reviewers said the same thing in four different words: the mode states what is
    // at stake and never says WHEN anything happens, so there is nothing to come back to.
    //
    // Not while the request is still with a leader. It reads "Ride now and it lands on the
    // map in about 12 min", which is a promise to somebody who is not in the crew and whose
    // riding will not count for it -- two taps after a confirm that said, correctly, that
    // nothing changes until a leader says yes. The clock is about your ground; a rider
    // waiting has none.
    if (me.status !== "pending") h += drawnLine();
    if (c.invite_code) {
      // A button, because this is the one act a new leader has to perform and it used to be
      // eight hex characters to select by hand inside a panel that scrolls under your finger.
      h += '<p class="hint crewinvite">'
        + t(c.join_policy === "invite" ? "crew.mine.invite" : "crew.mine.invite2")
        + ': <code id="cm-invite">' + esc(c.invite_code) + "</code>"
        // The link first: it is what you paste into a chat for somebody to tap. The code
        // stays because it is what survives being read out, photographed, or typed on a
        // phone that reached the site some other way, which is what "Enter code" is for.
        // One group, so the pair wraps together instead of the second one falling alone onto
        // a line of its own: at 390 the label, the code and the first button exactly fill the
        // row, and the break landed between the two things that belong side by side.
        + '<span class="crewinvbtns">'
        + '<button class="crewbtn mini" id="cm-copylink" title="'
        + esc(t("crew.tip.copylink")) + '" data-link="'
        + esc(inviteLink(c)) + '">' + t("crew.mine.copylink") + "</button>"
        + '<button class="crewbtn mini ghost" id="cm-copy" title="'
        + esc(t("crew.tip.copycode")) + '" data-code="'
        + esc(c.invite_code) + '">' + t("crew.mine.copy") + "</button>"
        // Leader only: an officer lets riders in one at a time, which somebody reviews.
        // Turning the code over changes who can get in with nobody reviewing anything.
        + (me.role === "leader"
           ? '<button class="crewbtn mini ghost" id="cm-newcode" title="'
             + esc(t("crew.tip.newcode")) + '">' + t("crew.mine.newcode") + "</button>"
           : "")
        + "</span></p>";
    }
    if (me.declined && me.declined.length) {
      h += '<div class="crewpend"><h4>' + t("crew.decl.h") + "</h4>"
        + me.declined.map(function (x) {
            return '<div class="crewpendr"><span>' + esc(x.name) + "</span>"
              + (x.free
                 ? '<button class="crewbtn mini ghost" data-undecline="'
                   + esc(x.store_id || "") + '">' + t("crew.decl.undo") + "</button>"
                 : '<span class="crewgone">'
                   // All three reasons, in this column's own third-person register.
                   // `crew.join.wait.h` used to stand in for the cooldown, which addresses
                   // the rider it is shown to -- next to somebody else's name it told the
                   // leader to cool down, in Title case among lowercase fragments.
                   + t(x.why === "cooldown" ? "crew.decl.cooling"
                       : x.why === "full" ? "crew.decl.full" : "crew.decl.gone")
                   + "</span>")
              + "</div>";
          }).join("") + "</div>";
    }
    // Leaving is blocked for a leader with members until somebody else can run the crew, and
    // there was no control anywhere to make that somebody. The endpoint existed; the button
    // did not, so a two-person crew's leader was stuck for good.
    // Officers hold these powers on the server and were shown none of them, so an officer's
    // only way to use a power they have was to craft the request by hand. Promoting to leader
    // stays a leader's call, and the server enforces that.
    // A plain member gets the names, read-only. The whole roster used to be behind the same
    // gate as the invite code and the knock list, so a member's page said "5 riders" in its
    // header and the only list on it was WHO RODE FOR IT -- four names, with the reader's own
    // nowhere on their own crew's page, because they had just joined and had not ridden for
    // it yet. Knowing who you ride with is not a power.
    if (me.roster && me.roster.length > 1
        && me.role !== "leader" && me.role !== "officer" && me.status !== "pending") {
      h += '<div class="crewpend"><h4>' + t("crew.roles.h") + "</h4>"
        + me.roster.map(function (x) {
            var mark = x.role === "leader" || x.role === "officer"
              ? " " + roleMark(x.role)
                + '<b class="crewrolew">' + esc(t("crew.role." + x.role)) + "</b>"
              : "";
            var flag = (H.cc && x.flag) ? H.cc(x.flag) : "";
            var isMe = !!(ME && ME.handle && x.store_id && x.store_id === ME.handle);
            return '<div class="crewpendr' + (isMe ? " crewrowmine" : "") + '"><span>'
              + flag + esc(x.name) + mark
              + (isMe ? ' <b class="crewyou">' + esc(t("crew.you")) + "</b>" : "")
              + "</span></div>";
          }).join("")
        + "</div>";
    }
    if (me.roster && me.roster.length > 1
        && (me.role === "leader" || me.role === "officer")) {
      // Folded at six, like the join list directly above it. Six members ran 83px each --
      // 529px of a 590px screen for twelve buttons and no information -- while the list above
      // folds at six and says so. The leader's row is always inside the fold.
      var ROSTER_SHOWN = 6;
      h += '<div class="crewpend"><h4>' + t("crew.roles.h") + "</h4>"
        + me.roster.map(function (x, ri) {
            // The ROLE, in words, not only a glyph with a `title`. A phone has no hover, so
            // the star beside a name was unreadable on the device this is built for -- and
            // "Make officer" is a button whose result was explained nowhere on screen.
            var mark = x.role === "leader" || x.role === "officer"
              ? " " + roleMark(x.role)
                + '<b class="crewrolew">' + esc(t("crew.role." + x.role)) + "</b>"
              : "";
            // The flag the payload has carried all along. A list of riders that says nothing
            // about any rider is a list of buttons.
            var flag = (H.cc && x.flag) ? H.cc(x.flag) : "";
            // the leader is listed, because a section called "The crew" that leaves them out
            // is a section header telling a lie
            // Your own row. Remove answered 400 `not_yourself` on every press, and Stand
            // down worked -- it stripped the clicker's own powers and took the roster panel
            // with it. There is a way to step back further down the card; it is not this.
            // Nothing an officer presses on another officer can work: the server
            // refuses both, and the refusal it renders reads "Only a leader or officer
            // can do that" at somebody who is one. Only the leader outranks an officer.
            var btn = x.store_id && x.store_id === me.handle ? ""
              : x.role === "leader" ? ""
              : x.role === "officer" && me.role !== "leader" ? ""
              : '<button class="crewbtn mini ghost" data-role="'
                + (x.role === "officer" ? "member" : "officer") + '" data-sid="'
                + esc(x.store_id || "") + '">'
                + t(x.role === "officer" ? "crew.roles.demote" : "crew.roles.promote")
                + "</button>"
                + '<button class="crewbtn mini ghost" data-kick="'
                + esc(x.store_id || "") + '" data-name="' + esc(x.name) + '"'
                + ' title="' + esc(t("crew.tip.remove")) + '">'
                + t("crew.roles.remove") + "</button>";
            // `hidden` and `crewrest`, the same two the join list folds with: one pattern
            // for the two folding lists in this card rather than two.
            return '<div class="crewpendr' + (ri >= ROSTER_SHOWN ? ' crewrest" hidden' : '"')
              + '><span>' + flag + esc(x.name) + mark + "</span>" + btn
              + "</div>";
          }).join("")
        + (me.roster.length > ROSTER_SHOWN
           ? '<button class="crewbtn mini ghost crewshowall" id="cr-more"'
             + ' aria-expanded="false">' + t("crew.roles.all", { n: me.roster.length })
             + "</button>"
           : "")
        + "</div>";
    }
    if (lead) {
      h += '<details class="crewedit"><summary>' + t("crew.mine.settings") + "</summary>"
        // The same two fields as the create form, with the same affordances. These were an
        // `<input>` apiece with no counter: a 280-character description in a single-line box
        // 294px wide shows about thirty-five characters at a time, so the field a leader uses
        // to EDIT what they wrote was the one they could not read back. Create had a counted
        // textarea for the identical value.
        // Same as create: see the note there about clipping a paste in silence. Worse here,
        // because the value starts full of a name the leader did not just type.
        + "<label>" + t("crew.new.name") + '<input id="ce-name" value="'
        + esc(c.name) + '" aria-describedby="ce-namewhy">'
        + '<span class="crewcount" id="ce-namecount">' + (c.name || "").length + "/28</span>"
        + '<span class="crewwhy" id="ce-namewhy"></span></label>'
        + "<label>" + t("crew.new.desc")
        + '<textarea id="ce-desc" maxlength="280" rows="2">'
        + esc(c.description || "") + "</textarea>"
        + '<span class="crewcount" id="ce-desccount">'
        + (c.description || "").length + "/280</span></label>"
        // The colours, while they are still a guess. The picker went in last round because
        // choosing before meeting a single rival's colour made it "an irreversible guess" --
        // true, right up until the crew is on the map. After that the rectangles out there are
        // in this colour and other riders have learned them, so what was a guess is a fact and
        // stays one. The swatch remains either way: a leader should always be able to see what
        // their crew flies.
        + ((c.tiles || 0) > 0
           ? '<div class="crewidentrow"><div class=crewidentl>' + t("crew.new.colours")
             + '<span class="crewpreview">' + swatch(c.colour, c.pattern, 34) + "</span>"
             + "</div></div>"
             + '<p class=hint>' + t("crew.mine.colourlock") + "</p>"
           : identBlock("ce", { colour: c.colour, pattern: c.pattern }))
        + "<label>" + t("crew.new.who") + '<select id="ce-policy">'
        + ["approval", "open", "invite"].map(function (p) {
            return '<option value="' + p + '"' + (p === c.join_policy ? " selected" : "")
              + ">" + t("crew.new." + p) + "</option>"; }).join("")
        + "</select></label>"
        + '<label class="crewfile">' + t("crew.mine.emblem")
        + '<input type="file" id="ce-logo" accept="image/*"></label>'
        // The mark itself, with an X on it when there is an upload to remove. Clearing used
        // to be a ghost button beside Save reading "Use the drawn one" -- which Erwin called
        // weird, and it is: it names the thing you get rather than the thing you do, it sits
        // next to Save as if it were a second way to save, and it was shown whether or not
        // there was anything to clear, because nothing told the client. `has_logo` does now.
        + '<div class="crewlogo">' + emb(c.slug, 52)
        + (c.has_logo
           ? '<button type="button" class="crewlogox" id="ce-clearlogo" title="'
             + esc(t("crew.mine.generated")) + '" aria-label="'
             + esc(t("crew.mine.generated")) + '">✕</button>'
           : "")
        + "</div>"
        + '<p class=hint>' + t("crew.mine.emblemp") + "</p>" 
        // `.crewacts`, like every other button row in this card. Emitted as bare
        // siblings these two had no horizontal spacing at all -- measured 0.00px apart --
        // so Save and "Use the drawn one" read as a single merged control.
        + '<div class="crewacts">'
        + '<button class="crewbtn" id="ce-save">' + t("crew.mine.save") + "</button>"
        + "</div>"
        + "</details>";
    }
    h += '<div class="crewacts">'
      // Every member gets this, not only the two roles that can invite: handing out where
      // the crew lives is not the same act as letting somebody in, and a rider who cannot
      // approve anybody can still put a code on their backpack.
      + (c.share_url
         ? '<button class="crewbtn ghost" id="cm-share" title="'
           + esc(t("crew.share.p")) + '">' + t("crew.share") + "</button>"
         : "")
      // `title` on each of these: the labels are in this feature's voice and the voice is only
      // free when the plain meaning is one hover away. Pulling a request and leaving a crew
      // are different acts, so they do not share an explanation.
      + '<button class="crewbtn ghost" id="cm-leave" title="'
      + esc(t(me.status === "pending" ? "crew.tip.cancel" : "crew.tip.leave")) + '">'
      // pulling a request you never got an answer to is not leaving a crew, and it does not
      // cost a cooldown any more either
      + t(me.status === "pending" ? "crew.mine.cancel" : "crew.mine.leave") + "</button>"
      // disband and claim-leadership were endpoints with no buttons. A solo leader who walks
      // out used to leave a crew with no riders on the board that nobody could clear up.
      + (me.role === "leader"
         ? '<button class="crewbtn ghost danger" id="cm-disband" title="'
           + esc(t("crew.tip.disband")) + '">' + t("crew.mine.disband")
           + "</button>"
         : "")
      + (me.role !== "leader" && me.leader_stale && me.can_claim
         ? '<button class="crewbtn ghost" id="cm-claim" title="' + esc(t("crew.tip.claim"))
           + '">' + t("crew.mine.claim") + "</button>"
         : "")
      + "</div>"
      // Its own bar, like every crewless state already has. In this row it was a plain ghost
      // button identical to "Leave crew" and two along from "Disband", so the control that
      // ends your session looked exactly like the one that leaves your crew, beside the
      // irreversible one.
      + '<div class="crewfoot"><button class="crewbtn ghost" id="cm-signout" title="'
      + esc(t("crew.tip.signout")) + '">'
      + t("crew.mine.signout") + "</button></div>"
      + "</div>";
    return h;
  }

  function bindMine(me) {
    var c = me.crew;
    // `.then(show)` and nothing else: no `r.ok`, no `onWrite`, no `setStatus`. With
    // `/decide` answering 400 these two did NOTHING visible -- the knock stayed on the card
    // and the only trace was a red line in devtools -- and they are the two buttons a leader
    // presses most. The un-decline handler below has had the right body all along.
    function decide(b, sid, accept) {
      var who = b.dataset.name || "";
      b.onclick = function () {
        // Turning somebody away asks first. It is the one act in this feature that affects
        // another person and cannot be undone by the leader, and it was the only consequential
        // control with no guard at all -- while removing an existing member, rotating the
        // code, leaving and disbanding all confirm, and "Let in" and "No" sit 8px apart. A
        // reviewer tapped No and the request was simply gone.
        //
        // Only the refusal. "Let in" is what the knock is asking for and is undone by the
        // Remove control two blocks down, so a confirm there would be a dialog in front of
        // saying yes.
        if (!accept) {
          ask(t("crew.roles.declineq", { name: who }), t("crew.decline"),
              function () { send(); }, b, true);
          return;
        }
        send();
      };
      function send() {
        api("POST", "/api/v1/crews/" + c.slug + "/decide",
            { store_id: sid, accept: accept }).then(function (r) {
          if (r.ok) {
            // The knock row vanishing was the ONLY evidence that a person had joined your
            // crew, on the button a leader presses more than any other.
            pendingStatus = t(accept ? "crew.roles.letin" : "crew.roles.turned",
                              { name: who });
            reveal(".crewmine-wrap");
            show();
          } else if (!onWrite(r)) setStatus(errMsg(r.err), true, b);
        });
      }
    }
    document.querySelectorAll("[data-ok]").forEach(function (b) {
      decide(b, b.dataset.ok, true);
    });
    document.querySelectorAll("[data-no]").forEach(function (b) {
      decide(b, b.dataset.no, false);
    });
    document.querySelectorAll("[data-undecline]").forEach(function (b) {
      b.onclick = function () {
        api("POST", "/api/v1/crews/" + c.slug + "/decide",
            { store_id: b.dataset.undecline, accept: true }).then(function (r) {
          if (r.ok) { reveal(".crewmine-wrap"); show(); }
          else if (!onWrite(r)) setStatus(errMsg(r.err), true, b);
        });
      };
    });
    document.querySelectorAll("[data-kick]").forEach(function (b) {
      b.onclick = function () {
        ask(t("crew.roles.removeq", { name: b.dataset.name }), t("crew.roles.remove"),
            function () {
              api("POST", "/api/v1/crews/" + c.slug + "/remove",
                  { store_id: b.dataset.kick }).then(function (r) {
                if (r.ok) { reveal(".crewmine-wrap"); show(); }
                else if (!onWrite(r)) setStatus(errMsg(r.err), true, b);
              });
            }, b, true);
      };
    });
    document.querySelectorAll("[data-role]").forEach(function (b) {
      b.onclick = function () {
        api("POST", "/api/v1/crews/" + c.slug + "/role",
            { store_id: b.dataset.sid, role: b.dataset.role }).then(function (r) {
          if (r.ok) { reveal(".crewmine-wrap"); show(); }
          else if (!onWrite(r)) setStatus(errMsg(r.err), true, b);
        });
      };
    });
    var leave = document.getElementById("cm-leave");
    if (leave) leave.onclick = function () {
      var pending = me.status === "pending";
      // The blocker BEFORE the question, not after the answer. A leader with members and no
      // officer used to read the confirm, weigh the seven-day cooldown, press Leave crew --
      // and only then be told "Make somebody an officer first." The server is still the one
      // that enforces it; this is the same rule read off the roster the panel already has,
      // so the one thing standing between them and leaving is said while they are still
      // deciding. `errMsg` below keeps the server's answer for the race where somebody else
      // demotes the last officer in between.
      if (!pending && me.role === "leader" && me.roster && me.roster.length > 1
          && !me.roster.some(function (x) { return x.role === "officer"; })) {
        setStatus(t("crew.e.promote"), true, leave);
        return;
      }
      ask(pending ? t("crew.mine.cancelq", { name: c.name }) : leaveQuestion(c.name),
          t(pending ? "crew.mine.cancel" : "crew.mine.leave"), function () {
        api("POST", "/api/v1/crews/leave", {}).then(function (r) {
          // The card that explains what just happened, not the leaderboard. Flashing
          // `.crewboard` scrolled 0 -> 405 and put the "next crew in 7 days" card at
          // viewTop -57: off the top of the screen at the moment it was written.
          // The TOP of the panel: after disbanding there is no crew card to go to, the
          // first `.crewcard` is the create form, and that is where a leader who has just
          // ended their crew was landing -- in a pattern picker. `#crewstatus` is where the
          // "X is gone" line lands.
          if (r.ok) { reveal("#crewstatus"); show(); reloadTerritory(); }
          else if (!onWrite(r)) setStatus(errMsg(r.err), true, leave);
        });
      }, leave, true);
    };
    var dis = document.getElementById("cm-disband");
    if (dis) dis.onclick = function () {
      // The name, typed. Every other control in this card can be undone by doing it again;
      // this one ends a crew other people rode for, and it used to be two taps where the
      // second one opened under the cursor.
      // Whether anybody else is in it changes what this costs, and only one of the two
      // sentences says so. A reviewer disbanded the top crew on the board -- five riders, 91
      // squares -- and got the question written for a crew of one, word for word.
      // `members` is active-only on the server, so a rider still knocking is not counted as
      // somebody who loses a crew they were never let into.
      var others = Math.max(0, (c.members || 1) - 1);
      askFor((others
              ? t("crew.mine.disbandq.others", { name: c.name, r: riders(others) })
              : t("crew.mine.disbandq", { name: c.name }))
             + " " + t("crew.mine.disbandtype", { name: c.name }),
             c.name, function (typed, ctl) {
        // Composed on both sides, like `name_ok` does on the server: an accented name can be
        // typed decomposed and stored composed, and the leader would be told that their own
        // crew's name is not its name.
        // Case-folded as well as normalised. The input renders what you type in UPPERCASE
        // (`text-transform` plus 2px tracking) and its placeholder is the crew's real name,
        // so a leader who types exactly what the screen shows them was refused by a
        // difference they could not see -- the two strings are identical on screen. The gate
        // is there to make you stop and read the name, not to test your shift key.
        // Internal whitespace too, and for exactly the reason above. HTML collapses runs of
        // spaces, so a crew called "Cykel  slangen" is rendered "Cykel slangen" in the
        // sentence asking for it AND in the input's own placeholder -- the leader types the
        // only thing they can see, and is told it is not the name. The two strings are
        // identical on screen. A reviewer founded that crew and could not disband it: with
        // no other way to end a crew, its leader is stuck for good.
        var norm = function (x) {
          x = (x || "").trim().replace(/\s+/g, " ");
          if (x.normalize) x = x.normalize("NFC");
          return x.toLowerCase();
        };
        if (norm(typed) !== norm(c.name)) { ctl.fail(t("crew.e.nomatch")); return; }
        ctl.close();
        api("POST", "/api/v1/crews/" + c.slug + "/disband", {}).then(function (r) {
          if (r.ok) {
            // It landed at scrollTop 183 -- partway down somebody else's join list -- with no
            // message at any poll from 250ms to six seconds. Leaving has said what happened
            // since round eighteen; disbanding is the bigger act and said nothing.
            pendingStatus = t("crew.mine.disbanded", { name: c.name });
            reveal(".crewcard:not(.crewboard)");
            show();
            reloadTerritory();
          } else if (!onWrite(r)) setStatus(errMsg(r.err), true, dis);
        });
      }, dis, { max: 28, confirm: t("crew.mine.disband"), danger: true });
    };
    var claim = document.getElementById("cm-claim");
    if (claim) claim.onclick = function () {
      ask(t(ME && ME.leader_gone ? "crew.mine.claimq.none" : "crew.mine.claimq"),
          t("crew.mine.claim"), function () {
        api("POST", "/api/v1/crews/" + c.slug + "/claim", {}).then(function (r) {
          if (r.ok) { reveal(".crewmine-wrap"); show(); }
          else if (!onWrite(r)) setStatus(errMsg(r.err), true, claim);
        });
      }, claim);
    };
    bindSignOut();
    // Same wiring as the create form: the counters, the name rule while there is still
    // something to do about it, and clearing the submit error the moment the text changes.
    [["ce-desc", "ce-desccount", 280], ["ce-name", "ce-namecount", 28]]
      .forEach(function (pair) {
        var box = document.getElementById(pair[0]);
        var out = document.getElementById(pair[1]);
        if (!box || !out) return;
        box.oninput = function () {
          clearStatus();
          out.textContent = box.value.length + "/" + pair[2];
          // The counter is the only thing on screen while you are still typing, so it has to
          // be able to say "too long" and not just "full". 28/28 and 36/28 looked identical
          // before, because the field could not hold 36.
          out.classList.toggle("over", box.value.length > pair[2]);
          if (pair[0] !== "ce-name") return;
          var why = document.getElementById("ce-namewhy");
          if (!why) return;
          var problem = box.value.trim() ? nameProblem(box.value) : null;
          why.textContent = problem ? t(problem) : "";
          if (problem) box.setAttribute("aria-invalid", "true");
          else box.removeAttribute("aria-invalid");
        };
      });
    bindIdent("ce", function () {
      var hid = document.getElementById("ce-colour");
      var pat = document.getElementById("ce-pattern");
      if (!hid || !pat) return;
      var prev = document.querySelector(".crewedit .crewpreview .crewsw");
      if (prev) { prev.style.background = hid.value; prev.dataset.p = pat.value; }
      document.querySelectorAll("#ce-patterns .crewsw").forEach(function (el) {
        el.style.background = hid.value;
      });
    });

    var save = document.getElementById("ce-save");

    // The emblem, properly. It used to scale 3.5x on hover, growing down and right from its
    // own top-left corner -- which is where the crew's NAME is, so the one gesture for
    // looking at your crew's mark covered the crew's name while you did it, and sat flush
    // against the card's top edge. Erwin's words: it should zoom without being obfuscated.
    //
    // So hovering only lifts it a little, as the affordance, and a click opens the thing you
    // actually wanted: the emblem large, with the crew's description, its figures and the
    // code somebody else can scan to find it. On a phone the sheet is 96vw by 92dvh, which
    // is as close to full screen as a dialog with a close button gets.
    var emb = document.querySelector(".crewsumemb");
    if (emb) {
      pressable(emb, c.name, function (ev) {
        // Inside a <summary>, so without both of these the click toggles the accordion shut
        // underneath the dialog it just opened.
        if (ev) { ev.preventDefault(); ev.stopPropagation(); }
        window.openModal(c.name, crewSheetHTML(c));
      });
    }

    // Share crew: the address that goes on a sticker, with its code big enough to scan off
    // a phone held up to somebody. Deliberately NOT the invite link -- an invite code can be
    // rotated and a printed one cannot, so what leaves this card carries the slug.
    var sh = document.getElementById("cm-share");
    if (sh) sh.onclick = function () {
      var c = (ME && ME.crew) || {};
      if (!c.share_url) return;
      window.openModal(t("crew.share"),
        '<div class="crewshare">'
        + qrGrid(c.share_qr)
        + '<p class="crewshareu"><a href="' + esc(c.share_url) + '" target="_blank" rel="noopener">'
        + esc(c.share_url.replace(/^https?:\/\//, "")) + "</a></p>"
        + '<p class="hint">' + esc(t("crew.share.p")) + "</p>"
        + '<div class="crewacts">'
        + '<button class="crewbtn" id="cs-copy">' + t("crew.mine.copylink") + "</button>"
        + '<a class="crewbtn ghost" href="' + esc(c.share_url) + '" target="_blank"'
        + ' rel="noopener">' + t("crew.pub.print") + "</a>"
        + "</div></div>");
      var b = document.getElementById("cs-copy");
      if (b) b.onclick = function () {
        copyVia(c.share_url,
          function () { flash(b, t("crew.mine.copied")); },
          function () { setStatus(t("crew.err"), true, b); });
      };
    };

    var cl = document.getElementById("cm-copylink");
    if (cl) cl.onclick = function () {
      // No `<code>` holding the URL to select, so the offscreen textarea in `copyVia` is the
      // only fallback route; if that fails too the status line says nothing happened rather
      // than claiming it did.
      copyVia(cl.dataset.link || "",
        function () { setStatus(t("crew.mine.copied"), false, cl); flash(cl, t("crew.mine.copied")); },
        function () { setStatus(t("crew.err"), true, cl); });
    };

    // A new invite code, retiring the old one. Confirmed, because it breaks every link and
    // every screenshot already handed out -- and says so, rather than asking "are you sure".
    // The roster's fold. Six rows and a count, like the join list above it.
    var rmore = document.getElementById("cr-more");
    if (rmore) rmore.onclick = function () {
      panel.querySelectorAll(".crewpendr.crewrest").forEach(function (r) { r.hidden = false; });
      rmore.setAttribute("aria-expanded", "true");
      rmore.hidden = true;
    };

    var nc = document.getElementById("cm-newcode");
    if (nc) nc.onclick = function () {
      ask(t("crew.mine.newcodeq"), t("crew.mine.newcode"), function () {
        api("POST", "/api/v1/crews/" + encodeURIComponent(ME.crew.slug) + "/newcode")
          .then(function (r) {
            if (!r.ok) { setStatus(errMsg(r.err), true, nc); return; }
            // Through `pendingStatus`, not `setStatus`: the `render()` on the next line
            // rebuilds the panel and takes the message with it, so the confirmation was
            // written into DOM that was replaced microseconds later. The string has existed
            // and been translated into nineteen languages the whole time -- a reviewer
            // reported the rotation as having NO acknowledgement, because what reaches the
            // leader is eight characters silently changing. Same race as the invite link.
            pendingStatus = t("crew.mine.newcoded");
            render();
          });
      }, nc, true);
    };

    var cp = document.getElementById("cm-copy");
    if (cp) cp.onclick = function () {
      var code = cp.dataset.code || "";
      // `navigator.clipboard` is absent on an insecure origin and in some embedded webviews,
      // so there are two fallbacks and the last one is "select it for you", which is still
      // better than the hand-selection this replaces.
      // On the button, not in a strip elsewhere on the card. `setStatus` still runs because
      // it is the live region and a reader who cannot see the button needs telling; what
      // changes is that the eye is answered where the thumb was.
      function done() {
        setStatus(t("crew.mine.copied"), false, cp);
        flash(cp, t("crew.mine.copied"));
      }
      function pick() {
        var node = document.getElementById("cm-invite");
        if (!node || !window.getSelection) return;
        var r = document.createRange();
        r.selectNodeContents(node);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(code).then(done, function () { pick(); done(); });
        return;
      }
      var ta = document.createElement("textarea");
      ta.value = code;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      if (!ok) pick();
      done();
    };
    if (save) save.onclick = function () {
      var nm = document.getElementById("ce-name");
      var bad = nameProblem(nm && nm.value);
      if (bad) {
        // One copy, under the field. See the note in the create form.
        clearStatus();
        nameWhy("ce-namewhy", nm, bad);
        if (nm) { nm.focus(); nm.select(); }
        return;
      }
      save.disabled = true;
      api("POST", "/api/v1/crews/" + c.slug + "/edit", {
        name: document.getElementById("ce-name").value,
        description: document.getElementById("ce-desc").value,
        join_policy: document.getElementById("ce-policy").value,
        colour: (document.getElementById("ce-colour") || {}).value,
        pattern: (document.getElementById("ce-pattern") || {}).value
      }).then(function (r) {
        save.disabled = false;
        if (r.ok) {
          // Beside Save, and the panel stays where it is. `pendingStatus` plus `show()` threw
          // the leader to scrollTop 0 and shut the `<details>` they were working in, which put
          // Save 1,052px -- 2.1 screens -- from where their finger had been.
          // `pendingStatus`, not `setStatus`: the `show()` below rebuilds the panel and would
          // take the confirmation with it. Exactly the race the "New code" button lost.
          pendingStatus = t("crew.mine.saved");
          reloadTerritory();
          // A refresh that keeps your place, rather than patching the card by hand.
          //
          // This used to write the new name and description straight into the DOM, because
          // the comment above says `show()` threw the leader to scrollTop 0. That is no longer
          // true -- `show()` carries `#pbody`'s scroll across an in-place refresh through
          // KEEPTOP -- and patching only reached the card. A reviewer found the standings
          // further down the SAME scroll still showing the old name: "Saved / ZZ Review
          // Holmen" above, "1ST Holmenkollen Climb" below, two names for one crew on one
          // screen immediately after a save, which makes you doubt the save landed.
          //
          // The board is drawn from a different response, so nothing short of re-rendering
          // could have agreed with itself.
          show();
        } else if (!onWrite(r)) {
          // A name-shaped refusal belongs beside the name, not beside Save: anchored to Save,
          // `scrollIntoView` dragged the field to y-235 and the rider typed into a box they
          // could not see.
          var code = (r.err && (r.err.code || r.err.detail)) || "";
          var nmf = document.getElementById("ce-name");
          var near = (code === "name_taken" || code === "bad_name"
                  || code === "name_reserved" || code === "bad_name_word")
                 && nmf ? nmf : save;
          setStatus(errMsg(r.err), true, near);
          if (near === nmf) { nmf.focus(); nmf.select(); }
        }
      });
    };
    var logo = document.getElementById("ce-logo");
    if (logo) logo.onchange = function () {
      if (!logo.files || !logo.files[0]) return;
      var fd = new FormData();
      fd.append("file", logo.files[0]);
      // through the same reader as everything else, so "too big" says how big and the three
      // image codes in ERRS stop being dead letters
      fetch("/api/v1/crews/" + c.slug + "/emblem",
            { method: "POST", body: fd, credentials: "same-origin" })
        .catch(function () { return { ok: false, json: function () { return {}; } }; })
        .then(function (r) {
          if (r.ok) { show(); reloadTerritory(); return; }
          Promise.resolve(r.json ? r.json() : {}).catch(function () { return {}; })
            .then(function (b) {
              setStatus(errMsg({ detail: b && b.detail }), true, logo);
            });
        });
    };
    var clr = document.getElementById("ce-clearlogo");
    if (clr) clr.onclick = function () {
      fetch("/api/v1/crews/" + c.slug + "/emblem",
            { method: "DELETE", credentials: "same-origin" })
        .catch(function () { return { ok: false }; })
        .then(function () { show(); reloadTerritory(); });
    };
    // the crew's own ground, from the ranking it is already in
    api("GET", "/api/v1/crews/" + c.slug).then(function (r) {
      var el = document.getElementById("crewterr");
      if (!el) return;
      if (!r.ok) {            // the card shipped with a spinner in it and nothing replaced it
        el.innerHTML = '<div class="crewmsg bad">' + esc(errMsg(r.err)) + "</div>";
        return;
      }
      var terr = r.body.territory || {};     // not `t`: that is the translator
      // Built before the hero line, because the hero line asks whether the first target is a
      // first-block square -- and `TARGETS` is assigned inside targetsHTML, which used to be
      // concatenated on the next line. It was answering from the previous card's array, and
      // from an empty one the first time the panel opened.
      var tgt = { html: "", first: false };
      if (me.status !== "pending") {
        tgt.html = targetsHTML(r.body.targets);
        tgt.first = !!(TARGETS.length && TARGETS[0].first);
      }
      // "0 squares in one piece" is the first line of the first card a new leader sees, and
      // "in one piece" is a brag about a shape that does not exist yet. The line under it
      // already says the useful thing.
      var held = terr.best_tiles || terr.tiles || 0;
      // Before the figure, because it is the reason to have opened this at all.
      el.innerHTML = sinceLine(c, terr)
        // The figure is a map link when there is ground behind it. Every row on the board and
        // every row in the targets list flies the map; the one number that is about YOUR
        // ground was the only thing in the panel that did not, so a member of a Copenhagen
        // crew could tap their own headline figure and stay looking at Norway. `frameTerritory`
        // puts the camera there on open -- this is how you get back after panning away.
        + '<div class="crewbig"' + (held ? ' data-mine="' + esc(c.slug) + '"' : "") + ">"
        + tiles(held)
        + (held ? " <span>" + t("crew.mine.ao") + "</span>" : "") + "</div>"
        + '<div class="crewsub">' + fmtKm2(terr.best_km2)
        + (terr.regions > 1 ? " · " + plural(null, "crew.patches.few", "crew.patches", terr.regions) : "")
        + (terr.tiles && terr.tiles !== (terr.best_tiles || terr.tiles)
            ? " · " + t("crew.inall", { v: tiles(terr.tiles) }) : "")
        // `||`, not a separator with an empty string after it. A crew that holds nothing
        // and whose first target completes its first block took the inner branch and printed
        // `0 km2 · ` with nothing following the dot -- on the first card every founder
        // sees, which is the third time this shape has shipped.
        + (terr.tiles || tgt.first ? "" : " · " + t("crew.mine.start", { n: SEED })) + "</div>"
        + (me.status === "pending" ? "" : tgt.html + safely(function () {
            return loseHTML(c.slug);
          }))
        // Behind a guard, and so is the losing list above it. This whole body is one
        // assignment, so when `contributorsHTML` threw -- `av()` called an `esc` that does
        // not exist in public.py's scope -- NOTHING was assigned: the squares, the area, the
        // ride targets and the losing list all vanished with it, `showTargets` never ran, and
        // the card showed a 21px gap. The figures never depended on the contributor list;
        // they were sharing a `+`.
        + safely(function () { return contributorsHTML(r.body.contributors); });
      el.querySelectorAll("[data-mine]").forEach(function (fig) {
        pressable(fig, t("crew.mine.onmap"), function () {
          flyToCrew(fig.dataset.mine);
          // Same act as tapping a board row, so it gets the same answer about whether the
          // panel is standing in front of the thing it just moved.
          closeIfCovering();
        });
      });
      el.querySelectorAll("[data-t]").forEach(function (row) {
        pressable(row, rowLabel(row), function () {
          flyToTile(TARGETS[+row.dataset.t], +row.dataset.t);
        });
      });
      // A rider opening or shutting a group by hand is also telling us where they want to
      // be; without this only the fly-to path was remembered and a hand-opened group still
      // vanished on the next render.
      el.querySelectorAll(".crewlosegrp").forEach(function (g) {
        g.addEventListener("toggle", function () {
          var k = g.dataset.grp;
          if (k) LOSEOPEN[k] = g.open;
        });
      });
      var card = el.querySelector("#crewlosecard");
      if (card) {
        card.addEventListener("toggle", function () { LOSECARDOPEN = card.open; });
      }
      el.querySelectorAll("[data-l]").forEach(function (row) {
        // no second argument: ground you are losing is already breathing on the map, and
        // redrawing the target rings here only cleared whichever one was marked
        pressable(row, rowLabel(row), function () {
          // Before the flight, because at phone widths this closes the panel and the next
          // render has to put the rider back where they were.
          rememberLoseGroup(LOSING[+row.dataset.l]);
          flyToTile(LOSING[+row.dataset.l]);
        });
      });
      showTargets(TARGETS);
    });
  }

  // Kilometres from the middle of the view to the nearest square a crew holds, or null when
  // the crew holds nothing yet. Straight-line, which is all this has to be: the question is
  // "is this my city" and the answer is off by a factor of ten thousand when it is not.
  // How far this crew's nearest ground is FROM THE RIDER -- the place they last rode, which
  // `/crews/me` carries. It used to be from `map.getCenter()`, so "74 km from here" meant
  // "from wherever you last panned", and the join list is sorted by it: pan to Copenhagen and
  // the recommendation of which crew to join silently reshuffles. Falls back to the camera
  // when we have never seen a ride, which is the only honest answer then.
  function groundAway(slug) {
    if (!TERR || !map) return null;
    var idx = -1;
    TERR.crews.forEach(function (c, i) { if (c.slug === slug) idx = i; });
    if (idx < 0) return null;
    var home = ME && ME.home;
    var c = home ? { lat: home.lat, lng: home.lon } : map.getCenter();
    var best = null;
    for (var i = 0; i < TERR.cells.length; i += 5) {
      if (TERR.cells[i] !== idx) continue;
      var lon = (tileLon(TERR.cells[i + 1], TERR.z) + tileLon(TERR.cells[i + 1] + 1, TERR.z)) / 2;
      var lat = (tileLat(TERR.cells[i + 2], TERR.z) + tileLat(TERR.cells[i + 2] + 1, TERR.z)) / 2;
      var dy = (lat - c.lat) * 111.32;
      var dx = (lon - c.lng) * 111.32 * Math.cos(c.lat * Math.PI / 180);
      var d = Math.sqrt(dx * dx + dy * dy);
      if (best == null || d < best) best = d;
    }
    return best;
  }

  var MAXMEM = 0;

  // The exact moment the cooldown lifts, in the reader's own locale and with no string to
  // translate. `toLocaleString` is given the locale the panel is already running in, and falls
  // back to the browser's own if that is not a tag it knows.
  function whenAgain(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";          // never print "Invalid Date" at a rider
    var out;
    try {
      // `locale()` reads the root element's lang, which the page updates every time the rider
      // changes language -- the same source `plural()` uses, so the date and the nouns around
      // it cannot disagree. I first wrote `LANG`, which does not exist in this file and would
      // have thrown on the one card it was written for.
      // Day and month, no clock. A seven-day cooldown does not turn over at 02:07, and the
      // minute invited a precision the figure does not have -- printed with no timezone, next
      // to a sentence that says "7 days". A reviewer read "Oct 13, 02:07 AM" and asked what
      // the minute was for. `toLocaleDateString`, so a locale that writes the day first still
      // writes the day first.
      out = d.toLocaleDateString(locale() || undefined,
                                 { day: "numeric", month: "short" });
    } catch (e) {
      try { out = d.toLocaleDateString(); } catch (e2) { return ""; }
    }
    return ' <span class="crewwhen">' + esc(out) + "</span>";
  }

  function joinHTML(crews, me) {
    // The countdown used to be returned INSTEAD of the list, so twenty-one crews existed
    // and a rider could not look at one of them for a week. The stated reason for hiding the
    // create form while cooling off was "a form whose only possible outcome is the error" --
    // and a list is not a form. Window-shopping for the crew you will join on day eight is
    // the only thing a cooldown leaves you.
    // Three different reasons this list might be look-only, and they were one boolean.
    // `cooling` is the seven days after walking out. `pending` is a request sitting with a
    // leader -- `join()` refuses while ANY membership exists, this one included, so every row
    // has to be shut even though nothing is wrong with the rider or the crew. A member cap is
    // the third, and it is per row rather than per rider.
    var cooling = !!me.cooldown_until;
    var pending = !cooling && me.status === "pending";
    // Where the reason for a shut button lives. A reviewer scrolled past the cooldown banner
    // and found six disabled buttons with no `title`, no `aria-disabled` and nothing pointing
    // at the explanation -- which is what a list does: it scrolls, and the banner leaves.
    // `crewwhycool` is this card's own message; `crewwhywait` is the crew card's waiting line
    // above it, which is why that one is not repeated here.
    var whyId = cooling ? "crewwhycool" : pending ? "crewwhypend" : null;
    // The same sentence as plain text, for `title`. `t()` returns the string the card prints,
    // so the tooltip and the banner cannot drift apart.
    var whyReason = cooling
      ? t("crew.join.wait.p", { n: days(daysUntil(me.cooldown_until)) })
      : pending ? t("crew.join.pending") : "";
    var msg = cooling
      ? '<div id="crewwhycool" class="crewmsg">'
        + t("crew.join.wait.p", { n: days(daysUntil(me.cooldown_until)) })
        // The date itself, which the server has known all along: a join attempt during the
        // cooldown is refused with "You can join a crew after 11 Oct 12:14." and the card
        // printed only "next crew in 7 days", so a rider coming back on day four could not
        // tell what was left. A formatted date needs no translation -- the browser renders it
        // in the reader's own locale -- which is why it is a span beside the sentence rather
        // than a sentence of its own in nineteen tables.
        + whenAgain(me.cooldown_until)
        + "</div>"
      // `pending` prints it too now. The argument against was that the crew card directly
      // above already says "Waiting on a leader to let you in", so this would be the same
      // sentence twice on one screen -- true, and it assumed both are on the screen at once.
      // A reviewer on a phone scrolled to the list, found six dead buttons, and reported the
      // reason as unavailable: `title` does not render on touch at all, and the card's line
      // had gone off the top. "Two lines down" is a desktop measurement. The duplicate costs
      // a sentence; the alternative costs the explanation.
      : pending
      ? '<div id="crewwhypend" class="crewmsg">' + t("crew.join.pending") + "</div>"
      : "";
    if (!crews.length) {
      return cooling
        ? '<div class="crewcard"><h3>' + t("crew.join.wait.h") + "</h3>" + msg + "</div>"
        : "";
    }
    var rows = crews.slice();
    rows.forEach(function (c) { c._km = groundAway(c.slug); });
    // Nearest first. Rider count is a fine tiebreak and a terrible sort: it put a crew
    // fifteen hundred kilometres away at the top of the list of crews you might join.
    rows.sort(function (a, b) {
      if (a._km == null) return b._km == null ? 0 : 1;
      if (b._km == null) return -1;
      return a._km - b._km;
    });
    // Six, with the rest behind a toggle. Nearest-first means the six are the ones a rider
    // can actually reach, and the whole set stays in the DOM so the filter below works on all
    // of it and the toggle costs no round trip.
    var SHOWN = 6;
    return '<div class="crewcard crewjoin"><h3>'
      // "Cooling off" only when that is what it is. A rider waiting on a leader is not
      // cooling off, and the list they are reading is still a list of crews to join.
      + t(cooling ? "crew.join.wait.h" : "crew.join.h") + "</h3>" + msg
      + (rows.length > SHOWN
         ? '<input class="crewfilter" id="cj-filter" type="search" autocomplete="off"'
           + ' placeholder="' + esc(t("crew.join.filter")) + '"'
           + ' aria-controls="cj-list" aria-describedby="cj-count"'
           + ' aria-label="' + esc(t("crew.join.filter")) + '">'
           // The list changed under a search box and nothing was said, not even "no results".
           + '<div class="crewcount" id="cj-count" role="status" aria-live="polite"'
           + ' aria-atomic="true"></div>'
         : "")
      + '<div class="crewlist" id="cj-list">'
      + rows.map(function (c, i) {
          var open = c.join_policy === "open";
          var label = open ? t("crew.join.btn")
            : c.join_policy === "invite" ? t("crew.join.code") : t("crew.join.ask");
          var policy = t("crew.policy." + c.join_policy);
          // what the crew holds and what it says about itself, so the choice is not a
          // blind name-pick that costs a cooldown if it is wrong
          // Cooling off, so the list is for looking at. A button that cannot work must say
          // so before it is pressed, not after: the countdown above the list is the answer
          // and the button is the question.
          // Per row, and only for the cap. The other two reasons are about the rider.
          var capped = !!(MAXMEM && c.members >= MAXMEM);
          var locked = capped || cooling || pending;
          // Each fact in its own `nowrap` span, so the line breaks BETWEEN facts. The
          // non-breaking space stopped `78 km` splitting inside itself; the phrase around it
          // still broke anywhere, so a row read `… 78 km` / `from here` and the one below
          // began `· 222 km from here` -- a separator reading as a list bullet.
          // No separator character. Glued backwards, a line could END with a dangling dot;
          // glued forwards, every wrapped line BEGAN with one -- measured on 6 of 6 rows at
          // 390 and 6 of 6 at 360. With a glyph between two facts and the break between two
          // facts, there is no third position for it. The facts are flex items with a gap
          // instead, which separates them at every width and has nothing to strand. The
          // single space in the markup keeps the text layer from reading `6 off 8th4 riders`,
          // which is why a separator was put here in the first place.
          // Squares BEFORE km2, because squares is what the board ranks on and this list was
          // printing only the other one. A rider choosing a crew here read "Five Borough Crew
          // 103 km2" next to "Ringbahn Runners 67 km2" and had no way to see that the board
          // has them level on 30 squares each -- or that Equator Express, third-largest in
          // km2 on screen, sits 13th. `_crew_brief` has carried `tiles` all along; the row
          // simply never rendered it. The board, the crew's own card and its public page all
          // lead with squares; the two surfaces a stranger actually chooses from did not.
          var sub = [riders(c.members), policy]
            .concat(c.tiles ? [tiles(c.tiles)] : [])
            .concat(c.km2 ? [fmtKm2(c.km2)] : [])
            .concat(c._km == null ? [] : [t("crew.join.away", { v: fmtKm(c._km) })])
            .map(function (f) { return '<span class="crewfact">' + f + "</span>"; })
            .join(" ");
          return '<div class="crewrow' + (i >= SHOWN ? " crewrest" : "") + '"'
            + ' data-name="' + esc((c.name || "").toLowerCase()) + '">' + emb(c.slug, 26)
            + '<div class="crewrown"><b>' + esc(c.name) + "</b><span>" + sub + "</span>"
            + (c.description ? '<span class="crewmeta2">' + esc(c.description) + "</span>" : "")
            + "</div>"
            + (locked
               // No `</div>` here. The row's own closer is appended to the whole expression
               // below, so this branch closed it twice: one `.crewrow` opened and two closed,
               // and the surplus closer walked the rest of the list out of the card. `locked`
               // covers the cooling-off window, so that was EVERY row for any rider who had
               // just left a crew -- the counter read "1 of 1 crews" over twenty-one rows,
               // the filter governed one of them, and the card's border stopped mid-list.
               //
               // A waiting rider keeps the row's OWN label, disabled. The reason is stated
               // once above the list instead of twenty-one times inside it, and "Cooling off"
               // on a row would be false -- they are not cooling off, they are queued.
               // The row keeps its own label while cooling off too. "Cooling off" on every
               // button hid the join policy -- the one thing a rider reads this list FOR --
               // exactly while they were reading it, and the card above already says what the
               // wait is, once. Same argument I made for the waiting state two commits ago;
               // it applies here and I only applied it there. A member cap is different: that
               // IS a fact about the crew in the row, so it still replaces the label.
               // Its own reason, not just the banner's. `aria-describedby` is how a reader
               // is told and `title` is how a pointer is; both name the sentence this card
               // already shows, so there is nothing new to translate.
               ? '<button class="crewbtn mini ghost" disabled'
                 + (whyId && !capped ? ' aria-describedby="' + whyId + '"' : "")
                 + (whyId && !capped ? ' title="' + esc(whyReason) + '"' : "")
                 + ">"
                 + (capped ? t("crew.join.full") : label)
                 + "</button>"
               : '<button class="crewbtn mini' + (open ? "" : " ghost") + '" data-join="'
                 + esc(c.slug) + '" data-pol="' + esc(c.join_policy) + '" data-name="'
                 + esc(c.name) + '">' + label + "</button>") + "</div>";
        }).join("")
      + "</div>"
      + (rows.length > SHOWN
         // `crewshowall`, not `crewmore`: that class already belongs to the lose card's
         // "and 3 squares more", two hundred lines away in this file and a hundred and
         // fifty in the stylesheet. Borrowing it put a `width: 100%` and a `margin-top`
         // on a hint paragraph in a different card.
         ? '<button class="crewbtn mini ghost crewshowall" id="cj-more"'
           + ' aria-expanded="false">' + t("crew.join.all", { n: rows.length })
           + "</button>"
         : "")
      + "</div>";
  }

  // The filter and the toggle, over rows already in the DOM: the list is at most sixty and
  // the whole point is that neither costs a request.
  // Whether the join list is unfolded, and the current list's repaint. Both used to live
  // inside `bindList`'s closure, which is rebuilt on every render -- so an unfolded list
  // silently re-folded itself whenever anything repainted the panel, and `offerInvite`'s
  // `more.click()` was undone a few milliseconds after it ran. A reviewer scanned the sticker
  // of an invite-only crew -- the only discovery path those crews have -- and landed in a
  // list of six crews that did not contain it, with the row present at 0x0 inside `[hidden]`
  // and nothing on screen naming the crew. That is the whole arrival, silently dropped, for
  // any crew outside the nearest six.
  var listOpen = false;
  var paintList = null;

  function bindList() {
    var list = document.getElementById("cj-list");
    if (!list) { paintList = null; return; }
    var rows = [].slice.call(list.querySelectorAll(".crewrow"));
    var more = document.getElementById("cj-more");
    var box = document.getElementById("cj-filter");

    function paint() {
      var q = box ? box.value.trim().toLowerCase() : "";
      var shown = 0;
      rows.forEach(function (r, i) {
        var hit = !q || (r.dataset.name || "").indexOf(q) >= 0;
        // Typing searches the whole set; without a query the six nearest stand alone.
        r.hidden = !hit || (!q && !listOpen && r.classList.contains("crewrest"));
        if (hit) shown++;
      });
      // Nothing to expand while a query is narrowing the list.
      if (more) more.hidden = !!q;
      var count = document.getElementById("cj-count");
      // Only while something is being searched. "21 of 21 crews" over six visible rows reads
      // as a bug at a glance -- the other fifteen are behind the toggle, whose own label
      // already carries the total. This is a `role="status"` for answering "what did my
      // search do", so with no search it has nothing to answer.
      if (count) count.textContent = q
        ? t("crew.join.count", { n: shown, v: rows.length }) : "";
      // A sentence naming the query and a way back, rather than the single em dash a
      // stylesheet rule used to draw into an otherwise empty card.
      var note = document.getElementById("cj-none");
      if (!shown && box) {
        list.setAttribute("data-empty", "1");
        if (!note) {
          note = document.createElement("div");
          note.className = "crewempty";
          note.id = "cj-none";
          list.appendChild(note);
        }
        note.innerHTML = '<span>' + esc(t("crew.join.nomatch", { v: q })) + "</span>"
          + '<button class="crewbtn mini ghost" id="cj-clear">'
          + esc(t("crew.join.showall")) + "</button>";
        // The empty state carries its own way back, so the list's toggle would be a second
        // control with the same label six pixels below it.
        if (more) more.hidden = true;
        note.hidden = false;
        var clear = document.getElementById("cj-clear");
        if (clear) clear.onclick = function () {
          box.value = "";
          // Expanded, not merely unfiltered. Clearing alone dropped the list back to its six
          // nearest with a second "Show all 22" immediately below it -- two presses for what
          // one button names, and the second is the very duplicate this card suppresses.
          listOpen = true;
          if (more) {
            more.setAttribute("aria-expanded", "true");
            more.textContent = t("crew.join.fewer");
          }
          paint();
          box.focus();
        };
      } else {
        list.removeAttribute("data-empty");
        if (note) note.hidden = true;
      }
    }

    // Reachable from `offerInvite`, which must not depend on a click handler being bound
    // yet, and must not be undone by the next repaint.
    paintList = paint;
    // The button is rebuilt with its default label, so the remembered state has to be put
    // back onto it or the list and its own control disagree.
    if (more && listOpen) {
      more.setAttribute("aria-expanded", "true");
      more.textContent = t("crew.join.fewer");
    }
    if (box) box.oninput = paint;
    if (more) more.onclick = function () {
      listOpen = !listOpen;
      more.setAttribute("aria-expanded", listOpen ? "true" : "false");
      more.textContent = listOpen ? t("crew.join.fewer")
                              : t("crew.join.all", { n: rows.length });
      paint();
      // Expanding pushed the button 1,225px down the panel, so "Show fewer" was below the
      // fold the instant it existed and the thumb was left pointing at a row.
      more.scrollIntoView({ block: "nearest" });
    };
    paint();
  }

  // An invite link, once the rider is signed in and has no crew of their own. It brings the
  // crew on screen and fills the prompt; it does not press Join. Clicking a link somebody sent
  // you is not the same as deciding to join their crew.
  function offerInvite(me) {
    if (!INVITE || !me || !me.paired || me.crew) return;
    var btn = document.querySelector('[data-join="' + cssEscape(INVITE.slug) + '"]');
    if (!btn) return;                      // that crew is not in the list; leave it alone
    // Spent, whether or not the rider goes through with it: a refresh should not reopen this.
    var inv = INVITE;
    INVITE = null;
    // The row may be one of the twenty-eight the list folds away, in which case the button
    // exists and cannot be pressed. Unfold by setting the state and repainting, rather than
    // by synthesising a click on a control whose handler belongs to a closure that the next
    // render replaces: `more.click()` unfolded the list and the following repaint folded it
    // straight back, so the row stayed 0x0 and hidden and the arrival went nowhere.
    if (btn.closest("[hidden]")) {
      listOpen = true;
      if (paintList) paintList();
      var more = document.getElementById("cj-more");
      if (more) {
        more.setAttribute("aria-expanded", "true");
        more.textContent = t("crew.join.fewer");
      }
    }
    btn.scrollIntoView({ block: "center" });
    // Say which row. Arriving from a sticker and being dropped at a scroll position in a list
    // of thirty-four is the same as being dropped at the top of it.
    //
    // Not `flashRow(row)` directly: a `render()` pass replaces `#crewpanel` within about
    // fifty milliseconds of this running, and `INVITE` is already spent, so the lit row was
    // destroyed before a single frame of it was drawn and never re-lit. A reviewer watched
    // for `.crewlit` in the live document every 25ms for six seconds and found it zero
    // times. The scroll survived, so you landed on the right row with nothing saying so.
    //
    // So the flash outlives the renders instead: it is re-applied after each one until the
    // panel stops repainting, and the last application is the one the reader sees.
    flashSlug = inv.slug;
    flashUntil = Date.now() + 3000;
    applyFlash();
    // Only with a code to put in it. A slug-only link to an invite-only crew opens the
    // prompt with nothing to type, which is worse than leaving the button alone.
    //
    // And re-applied like the flash above, for exactly the reason stated there. This did the
    // click and filled the box once, and the `render()` that lands about fifty milliseconds
    // later threw the prompt away with everything in it: a reviewer's MutationObserver caught
    // the filled input exactly once at t=370ms and an 8ms poll caught it zero times -- created
    // and destroyed inside one task, never drawn. So "Copy link", whose whole advantage over
    // "Copy code" is that the recipient does not have to type eight characters, delivered
    // exactly what "Copy code" delivers. The row scrolled and lit, so it looked like it had
    // worked. The comment above fixed this for the flash and stopped one line short.
    if (btn.dataset.pol === "invite" && inv.code) {
      askSlug = inv.slug;
      askCode = inv.code;
      askUntil = Date.now() + 3000;
      applyAsk();
    }
  }

  // The invite prompt, re-opened and re-filled after each render inside its window. Paired
  // with `applyFlash`: same problem, same shape of answer.
  var askSlug = null;
  var askCode = null;
  var askUntil = 0;

  function applyAsk() {
    if (!askSlug) return;
    if (Date.now() > askUntil) { askSlug = askCode = null; return; }
    // Already open and filled: leave it alone rather than stealing the caret back from
    // somebody who has started typing.
    var open = document.getElementById("crewask-in");
    if (open) { if (!open.value) { open.value = askCode; open.select(); } return; }
    var btn = document.querySelector('[data-join="' + cssEscape(askSlug) + '"]');
    if (!btn) return;
    btn.click();
    var box = document.getElementById("crewask-in");
    if (box) { box.value = askCode; box.focus(); box.select(); }
  }

  // `CSS.escape` is absent in older engines and this runs on whatever a rider has. A slug is
  // letters, numbers and hyphens after `slugify`, plus whatever non-ASCII the name carried, so
  // the only characters worth refusing are the ones that would end the attribute selector.
  function cssEscape(v) {
    return String(v).replace(/["\\\]]/g, "");
  }

  function bindJoin() {
    document.querySelectorAll("[data-join]").forEach(function (b) {
      b.onclick = function () {
        function send(body, ctl) {
          api("POST", "/api/v1/crews/" + b.dataset.join + "/join", body).then(function (r) {
            if (r.ok) {
              if (ctl) ctl.close();
              reveal(".crewmine-wrap"); show(); reloadTerritory();
            } else if (!onWrite(r)) {
              if (ctl) ctl.fail(errMsg(r.err));
              else setStatus(errMsg(r.err), true, b);
            }
          });
        }
        if (b.dataset.pol === "invite") {
          // beside the row, and naming the crew: the prompt used to open at the top of the
          // panel, so by the time you read it you could no longer see which crew you tapped
          askFor(t("crew.join.codeask", { name: b.dataset.name || "" }), "ABC12345",
                 function (code, ctl) {
                   // An invite code is `uuid4().hex[:8].upper()` -- eight hex characters and
                   // nothing else -- so "AB", "ab!!@@" and "....." cannot be one, and each of
                   // them was costing a round trip to be told so. The name field three cards
                   // up has rejected length and character-set mistakes locally since it was
                   // written; the code field sent everything. Same message either way, since
                   // the answer is the same: this is not a code. The server still decides
                   // whether a well-formed code is the RIGHT one.
                   if (!/^[0-9a-f]{8}$/i.test((code || "").trim())) {
                     ctl.fail(t("crew.e.invite") + " " + t("crew.e.invite.ask"));
                     return;
                   }
                   send({ invite_code: code }, ctl);
                 }, b);
        } else if (COOLDOWN_DAYS > 0) {
          // Joining is the act with a price on it: walk out again and you wait, and the
          // manual says so. It took one unguarded tap, while PULLING a request -- which the
          // dialog itself says "costs you nothing" -- got a confirmation. The budget was
          // being spent on the wrong one. Not `danger`: this is a thing you want to do, so
          // the affirmative is the bright button.
          // Asking queues you and costs nothing; joining commits you to the cooldown.
          var approval = b.dataset.pol === "approval";
          ask(t(approval ? "crew.join.askconfirm" : "crew.join.confirm",
                { name: b.dataset.name || "", v: days(COOLDOWN_DAYS) }),
              t(approval ? "crew.join.ask" : "crew.join.btn"),
              function () { send({}); }, b);
        } else {
          send({});
        }
      };
    });
  }

  /* ---------- the mode ---------- */

  // The heatmap and territory answer different questions and look terrible together: the glow
  // bleeds across the rectangles' edges, which are the whole point of them. So the two modes
  // are exclusive — entering crews fades the heat out, leaving brings it back.
  // Under the territory, not over it. The host adds `heat` last, so it sits on top of every
  // layer in the style -- including the crew fills and, since the fills moved down, including
  // the place names as well. A reviewer sampled the same crew's colour inside and outside the
  // heat blob: saturation 73% below it and 19% inside it, with the crew's own magenta reading
  // as grey-pink exactly over the ground that crew holds. Hiding both crew layers moved those
  // pixels by a contrast ratio of 1.3, which says the heat was setting the colour there, not
  // the territory. The whole mode is about whose colour is on the ground.
  //
  // Moved only while the mode is on, and put back on the way out, because `heat` belongs to
  // the host and every other panel expects it where it was.
  function heatUnderTerritory(under) {
    if (!map.getLayer || !map.getLayer("heat")) return;
    try {
      if (under && map.getLayer("crew-pulse-danger")) map.moveLayer("heat", "crew-pulse-danger");
      else if (!under) map.moveLayer("heat");
    } catch (e) {}
  }

  function setHeat(on) {
    if (!map.getLayer("heat")) return;
    var full = (window.__HEAT__ && window.__HEAT__.opacity) || 0.62;
    // Not off, just faint. Territory answers "who holds this" and the heatmap answers "does
    // anybody actually ride here", and the second is useful context under the first as long
    // as it is quiet enough not to blur the edges that are the whole point.
    var ghost = CFG.heat_ghost != null ? CFG.heat_ghost : 0.30;
    var want = on ? full : full * ghost;
    try { map.setPaintProperty("heat", "heatmap-opacity", want); } catch (e) {}
    // Again on the next frame. The host sets the full opacity from a `requestAnimationFrame`
    // of its own, so opening the mode during that window wrote the ghost and then had it
    // written straight back: a reviewer measured `heatmap-opacity` at 0.62 -- the full value
    // -- with the panel open, which is the value this function exists to replace.
    requestAnimationFrame(function () {
      try { map.setPaintProperty("heat", "heatmap-opacity", want); } catch (e) {}
    });
  }

  // Re-rendering resets the panel's scroll, so an action that changes your standing left you
  // staring at the top of the board with no sign it worked. Whatever is new gets scrolled to.
  var revealNext = null;
  var pendingStatus = null;
  // A pending message that is a FAILURE, and the id of the control it belongs under.
  // `pendingStatus` alone goes through `setStatus(msg)` with no `bad` flag, so it paints as a
  // confirmation and auto-clears after five seconds -- which is wrong for an error that
  // survived a repaint, and wrong twice over for one the reader has to act on.
  var pendingBad = false;
  var pendingNear = null;
  // What was typed into the create form, to be put back after a rebuild. The identity
  // re-fetch below goes through `show()`, which rebuilds the card from scratch -- so the fix
  // for "Create fails identically forever" arrived holding a second annoyance: the name and
  // description you had just written were gone, and a rider who had thought about the name
  // had to think of it again because of a colour clash they did not cause.
  var pendingDraft = null;
  // A `show()` that arrived before the helpers did; see `show()` and `init`.
  var pendingShow = false;

  // The dock is outside the panel and survives every re-render, so the count is written to
  // it rather than built with the panel HTML.
  // Asked once on load, so the count is there before anybody opens the panel. `dockDot` was
  // only reachable from `render()`, which only runs from `show()`, so the badge could only
  // ever tell a leader something they were already looking at.
  function primeDock() {
    api("GET", "/api/v1/crews/me").then(function (r) {
      if (!r.ok || !r.body) return;
      // Both signals on load, so the dot is already right before anybody touches the dock.
      // `rankOf` has no board yet at this point and returns null, so the rank half of the
      // comparison simply does not fire on a cold load -- the squares half, which is the
      // bigger news anyway, does.
      dockHasCrew = !!r.body.crew;
      dockDot(r.body.pending ? r.body.pending.length : 0,
              sinceNews(r.body.crew, r.body.territory));
      // And keep asking. Once was the fix for a badge that could only ever tell a leader
      // something they were already looking at; it left the badge unable to tell them
      // anything NEW.
      watchKnocks();
    });
  }

  // How often a leader's queue is re-checked while they have the page open. `primeDock`
  // asks once on load and never again, so a request arriving a minute later was invisible
  // until something re-rendered the panel -- a reviewer had the panel open when two requests
  // landed and had to close and reopen it to find them. Approval is the default join policy,
  // so this is the path most crews use.
  var KNOCK_POLL_MS = 30000;
  var knockTimer = null;
  // Whether there is a crew to poll about, learned from `/crews/me` on load rather than from
  // `ME`, which nothing sets until the panel is rendered. See `watchKnocks`.
  var dockHasCrew = false;

  function watchKnocks() {
    if (knockTimer) return;
    knockTimer = setInterval(function () {
      // Nothing to answer in a background tab.
      if (document.hidden) return;
      // Every member, not only the ranks that can answer a knock. The poll used to return
      // here for anybody who was not a leader or an officer, which was right while a knock
      // was the only thing it could report -- and is what kept the news badge below from
      // ever reaching the riders it is for.
      // NOT `ME`. `ME` is assigned only inside `render()`, which only runs when the panel is
      // opened -- so this timer returned on its first tick and every tick after for exactly
      // the rider it was written for: somebody who has not opened the panel. A reviewer
      // measured one `/crews/me` call in 39 seconds with the panel untouched, against three
      // in 36 seconds after opening it once, and the badge never lit for a change that had
      // genuinely happened. `primeDock` learns on load whether there is a crew to poll for,
      // and the poll reads its own response rather than a global somebody else owns.
      if (!dockHasCrew) return;
      api("GET", "/api/v1/crews/me").then(function (r) {
        if (!r.ok || !r.body) return;
        dockHasCrew = !!r.body.crew;
        var role = r.body.role;
        var lead = role === "leader" || role === "officer";
        var n = (lead && r.body.pending) ? r.body.pending.length : 0;
        // The one thing every reviewer who scored FUN below 9 asked for and the one thing
        // still missing after five rounds: with the panel shut, nothing reached you. The diff
        // was already computed, already on the client, and only ever shown to somebody who
        // had decided to go and look. `sinceNews` asks without consuming.
        dockDot(n, sinceNews(r.body.crew, r.body.territory));
        // The counts only. Re-rendering on a timer would pull the form out from under a
        // leader halfway through typing a crew name, which is a worse bug than the one this
        // fixes -- so the badge and the dot are updated in place and nothing else moves.
        var dot = document.querySelector(".crewsumdot");
        if (dot) {
          dot.textContent = String(n);
          dot.title = n ? knockText(n) : "";
          dot.hidden = !n;
        }
        if (ME) ME.pending = r.body.pending || [];
      });
    }, KNOCK_POLL_MS);
  }

  // "2 riders want to join your crew", in the one/few/many shape every counted phrase in
  // this file uses -- the verb changes with the number and a `{v}` insertion cannot carry that.
  function knockText(n) { return plural("crew.knock1", "crew.knocks.few", "crew.knocks", n); }

  // Remembered so either signal can be refreshed without the other being recomputed: the
  // render path clears the news and knows nothing about knocks, the poll finds knocks and
  // must not drop the news.
  var dockKnocks = 0;
  var dockNews = false;

  // Two signals, one badge. A knock is a COUNT and the reader can act on each one, so it
  // keeps the digit. "Your crew moved" is not a count of anything -- it is one fact, and a
  // digit on it would be a lie about how many -- so it shows as a bare dot. A bare dot is
  // also the weaker of the two claims on attention, which is the right way round: a number
  // means somebody is waiting on you.
  function dockDot(n, news) {
    if (typeof n === "number") dockKnocks = n;
    if (news !== undefined) dockNews = !!news;
    n = dockKnocks;
    news = dockNews;
    var dot = document.getElementById("crewsdot");
    if (!dot) return;
    dot.textContent = n ? (n > 9 ? "9+" : String(n)) : "";
    // Without this a news dot inherits the 17px box a two-digit count needs and renders as a
    // pink lozenge with nothing in it.
    dot.classList.toggle("bare", !n && news);
    dot.hidden = !n && !news;
    var btn = dot.parentNode;
    if (btn && btn.setAttribute) {
      // The label is hidden at phone widths, so the count has to reach a screen reader
      // through the button's own name.
      var base = t("dock.crews");
      var why = n ? knockText(n) : (news ? t("crew.since.dot") : "");
      // The same sentence the hover gives, rather than a heading and a digit side by side:
      // "Crews - Waiting 2" was two labels touching, and said nothing about who was waiting.
      btn.setAttribute("aria-label", why ? base + " · " + why : base);
      // A badge capped at "9+" is a number you cannot read; the title always has the real one.
      if (why) btn.setAttribute("title", why);
      else btn.removeAttribute("title");
    }
  }

  function reveal(sel) {
    revealNext = sel;
  }

  // The arrival flash, re-applied after every render inside its window. `revealNext` and
  // `doReveal` do the same dance for the scroll position, for the same reason: anything this
  // function does to the DOM is thrown away by the next repaint unless something re-does it.
  var flashSlug = null;
  var flashUntil = 0;

  function applyFlash() {
    if (!flashSlug) return;
    if (Date.now() > flashUntil) { flashSlug = null; return; }
    var btn = document.querySelector('[data-join="' + cssEscape(flashSlug) + '"]');
    var row = btn && btn.closest(".crewrow");
    if (row) flashRow(row);
  }

  function doReveal() {
    if (!revealNext) return;
    var el = document.querySelector(revealNext);
    revealNext = null;
    if (!el) return;
    // Open every collapsed ancestor, not just the node itself. Revealing the crew card of a
    // returning member scrolled to a node with no box, because the card lives inside a
    // <details> that is shut by default, so the one step that spans two devices still ended
    // in nothing visibly happening.
    var up = el;
    while (up) {
      if (up.tagName === "DETAILS") up.open = true;
      up = up.parentElement;
    }
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("crewflash");
    setTimeout(function () { el.classList.remove("crewflash"); }, 1400);
  }

  function show() {
    // `EUCCrews.init` runs from inside `map.on("load")`, after a fetch, so `H` is `{}` until
    // then -- and the dock button was reachable by keyboard during that window even though it
    // was invisible to the mouse. Pressing Enter threw `H.setPanel is not a function`,
    // uncaught, three times out of three. The activation is held and drained by `init`.
    if (!H || typeof H.setPanel !== "function") { pendingShow = true; return; }
    // Before `setPanel` replaces the panel body, and only when this panel was already the one
    // on screen -- a fresh open belongs at the top. `render()` applies it.
    var pb0 = document.getElementById("pbody");
    // NOT when something is waiting to be revealed. `reveal()` is how an action says "the
    // thing I just did is over there"; `doReveal()` scrolls to it synchronously after the
    // render, and this restore runs in a requestAnimationFrame AFTER that -- so keeping the
    // old position silently undid every one of them. Reviewer D measured Ask landing at
    // scrollTop 1352, Create at 1114 and Disband at 1443, each about two screens away from
    // the card it had just produced, with the confirmation pinned off-screen at the top.
    // That was this line, introduced with the Refresh fix. A refresh has nothing to reveal,
    // which is exactly when carrying the position is the right answer.
    KEEPTOP = (panelOpen && pb0 && !revealNext) ? pb0.scrollTop : 0;
    visible = true;
    panelOpen = true;
    syncKey();
    H.setPanel("crews", (H.t ? H.t("title.crews") : "Crews & Territory"),
      // TWO regions, not one whose role flips. Changing `role` and `aria-live` on an
      // already-registered region in the same mutation as the text is the registration
      // pitfall `statusHost` was written around -- the same pitfall one line further on.
      // Both exist from the start and the message goes into whichever one matches.
      '<div id="crewstatus" role="status" aria-live="polite" aria-atomic="true"></div>'
      + '<div id="crewalert" role="alert" aria-live="assertive" aria-atomic="true"></div>'
      + '<div id="crewpanel"><div class="spin"></div></div>');
    render();
    if (TERR && !map.getLayer("crew-fill")) buildLayers();
    setHeat(false);
    heatUnderTerritory(true);
    // Signed out, or on a reopen, this is everything needed. Signed in it fires again from
    // `render()`, because which crew is yours arrives with that request and not before --
    // the same ordering that painted your own ground like a stranger's for nine passes.
    frameTerritory();
  }

  function render() {
    var panel = document.getElementById("crewpanel");
    if (!panel) return;
    Promise.all([
      api("GET", "/api/v1/crews/me"),
      api("GET", "/api/v1/crews/ranking/all?limit=25"),
      api("GET", "/api/v1/crews?limit=60"),
      api("GET", "/api/v1/crews/drawn")
    ]).then(function (res) {
      DRAWN = res[3] && res[3].ok ? res[3].body : null;
      // A failure is not the same as not being signed in. The gate returns crews_disabled,
      // and mapping that onto {paired:false} put a signed-in rider in front of a sign-in
      // form, a pairing spinner and a dead QR before anything mentioned the real reason.
      // A dead pass is not a failed request: the panel went on showing a crew card and a
      // form that could not work, under a pink line telling you to grab a new pass with
      // nothing on screen to grab one with.
      if (res[0].status === 401 && ME) { ME = null; }
      var off = !res[0].ok && (res[0].err || {}).detail === "crews_disabled";
      if (off) {
        clearLayers();
        panel.innerHTML = '<div class="crewcard"><h3>' + t("crew.off.h") + "</h3>"
          + '<p class=hint>' + t("crew.e.off") + "</p></div>";
        ME = null;
        return;
      }
      var me = res[0].ok ? res[0].body : { paired: false };
      holdNotice(me);
      ME = me;
      // The pulse layer is built before this resolves, so without re-applying the filter it
      // always took the "every crew" branch and your own ground never stood out.
      scopePulse();
      // And the fill and the pattern, for exactly the same reason and in the same breath.
      // This line is the whole of the fix for a cold load drawing your ground like a
      // stranger's; see `paintBands`.
      paintBands();
      var rank = res[1].ok ? res[1].body.crews || [] : [];
      if (res[1].ok) BOARD = rank;
      // Both halves of the choice have landed now: which crew is yours, and who leads if you
      // have none. `show()` already tried with whichever it had.
      frameTerritory();
      var all = res[2].ok ? res[2].body.crews || [] : [];
      ALL = all;
      MAXMEM = res[2].ok ? (res[2].body.max_members || 0) : 0;
      // What the number under each name IS. `crew.board.sub` has existed since the board was
      // written and was rendered nowhere -- a reviewer found it by grep. So the podium prints
      // "Holmenkollen Climb / 91 / squares" with nothing saying 91 is the biggest patch held
      // in one piece rather than the 119 they hold in total. The crew's own card says "in one
      // piece"; the board, which is where strangers read it, never did.
      var board = '<div class="crewcard crewboard"><h3>' + t("crew.board") + "</h3>"
        + '<p class="hint crewboardsub">' + esc(t("crew.board.sub")) + "</p>"
        // The definition of the metric used to live here, permanently, above the board it
        // defines. It is a manual entry and it is in the manual now; see `explainer()`.
        
        + firstRunNote()
        + (TERR && TERR.pending && !rank.length ? "" : rankingHTML(rank))
        // A crew holding nothing has no rank, so it has no row, so the board a rider reads to
        // answer "how are we doing" simply did not mention their crew -- it ran 1 to 15 and
        // stopped, with no sign that anything was missing. The card above says "0 squares,
        // ride a 2x2 block and you're on the map"; the board, which is the part people scroll,
        // said nothing at all. One line under the table, built from strings that already
        // exist in all nineteen tables.
        + (me.crew && me.crew.slug
           && !rank.some(function (r) { return r.slug === me.crew.slug; })
           ? '<div class="crewboardmine">' + emb(me.crew.slug, 16) + " "
             + "<b>" + esc(me.crew.name) + "</b> · "
             + esc(t("crew.mine.start", { n: SEED })) + "</div>"
           : "")
        + "</div>";
      // What this rider can DO goes first and the standings go under it. The board used to
      // lead for everyone, and it put your own crew 1.7 screenfuls down at 1440x900 and 2.3
      // at 390x844 -- and START A CREW 2.1 screens down with JOIN A CREW at 3.7, for a rider
      // who had just paired and had nothing to stand in. Signed out already worked this way
      // and was the one screen a reviewer called the best in the feature, for that reason.
      var own = "";
      if (!me.paired) {
        /* the sign-in card is the whole of it */
      } else if (me.crew) {
        // BELOW the crew card. It went above for a while so a new member would not have to
        // scroll past the Leave / Disband row to find the rules -- but that put a shut
        // accordion of rules in front of the crew of every rider who already knows them,
        // which is the opposite of what this round did for the crewless state.
        own += "";
        // Folded by default put the only actionable thing in the feature behind a
        // disclosure triangle, under a 25-row board.
        own += '<details class="crewmine-wrap" open'
          + '><summary>' + '<img class="crewsumemb" alt="" src="' + me.crew.emblem + '"/>'

          // The name and the standing live in one block BESIDE the emblem, rather than as

          // loose children of the summary. As siblings of the image they could only wrap to a

          // full-width line that began at the summary's own left edge -- under the disclosure

          // triangle, outdented from the name it belongs to, which is what Erwin saw. In a

          // block of their own they wrap against the name instead: side by side when the row

          // is wide enough, stacked and still aligned when it is not.

          + '<span class="crewsumtxt">'

          + "<span>" + esc(me.crew.name) + "</span>"
          // read back as "Harbour Bridge Bombersleader" without this
          + '<span class="crewsumsep"> &middot; </span>'
          // What the card used to open with, moved up beside the name: where the crew stands,
          // how many ride for it, and what you are to it. "LEADER" alone spent the width on
          // the least interesting true thing on the line.
          // `/crews/me` answers `role: "member"` with `status: "pending"`, so a rider still
          // knocking reads "waiting" here rather than being badged a member of a crew that
          // has not answered them.
          + '<span class="crewsumrole">'
          + (me.status === "pending"
             ? t("crew.role.waiting")
             : [standing(me.crew.slug), riders(me.crew.members),
                t(me.role === "leader" ? "crew.mine.youare"
                  : me.role === "officer" ? "crew.mine.youofficer" : "crew.mine.youmember")]
               .filter(Boolean).join(" \u00b7 "))
          + "</span>"
          + "</span>"
          // The accordion can be shut, and a leader who shut it had no way at all to learn
          // that somebody was waiting.
          + (me.pending && me.pending.length
             // Its own class, not `.dockdot` as well: crews.css is linked BEFORE public.py's
             // inline <style>, so the inline `.dockdot { position: absolute }` would win at
             // equal specificity and this would be pinned to the summary's top-right corner.
             ? '<span class="crewsumdot" title="' + esc(knockText(me.pending.length)) + '">'
               + me.pending.length + "</span>" : "")
          + "</summary>"
          + myCrewHTML(me) + "</details>"
          // The one state that can last for days was the only one with nothing to move on to.
          // Removed, folded and declined all show the list under their notice; a rider waiting
          // on a leader got the crew page and nothing else, while the decline notice two
          // branches down says "No waiting, pick another one" -- advice pointing at a list
          // that this state had taken away. Look-only, because `join()` refuses while the
          // request stands: the rider reads what is out there and the card above says how to
          // free themselves to act on it.
          + (me.status === "pending" ? joinHTML(all, me) : "");
      } else if (me.removed_by) {
        ackNotice("removed");
        own += '<div class="crewcard"><h3>' + t("crew.removed.h") + "</h3>"
          + '<p class=hint>' + t("crew.removed.p", { name: esc(me.removed_by) })
          + "</p></div>"
          // Joining first. A reviewer's tap-count table puts "newcomer to in a crew" as the
          // journey that matters and it ends in Join, while founding is the rarer and bigger
          // act -- and the create form's colour grid is 200px of scroll in front of it.
          + joinHTML(all, me)
          + (me.can_found && me.creation_open && !me.cooldown_until
             ? createHTML(window.__CREWIDENT__ || null) : "")
      } else if (me.folded) {
        ackNotice("folded");
        own += '<div class="crewcard"><h3>' + t("crew.folded.h") + "</h3>"
          + '<p class=hint>' + t("crew.folded.p", { name: esc(me.folded) }) + "</p></div>"
          // Joining first. A reviewer's tap-count table puts "newcomer to in a crew" as the
          // journey that matters and it ends in Join, while founding is the rarer and bigger
          // act -- and the create form's colour grid is 200px of scroll in front of it.
          + joinHTML(all, me)
          + (me.can_found && me.creation_open && !me.cooldown_until
             ? createHTML(window.__CREWIDENT__ || null) : "")
      } else if (me.declined_by && !me.cooldown_until) {
        // Not while a cooldown is running. `crew.declined.p` ends "No waiting, pick another
        // one." -- advice that is false during the seven days, and it sat directly above a
        // card saying "Next crew in 7 days" with every row below it disabled. Two cards on one
        // screen answering "can I join now?" in opposite directions.
        //
        // Reachable in ordinary use: ask crew B, get turned down, then quit crew A. Both facts
        // are then true, and only one of them is actionable. The cooldown card is the one that
        // says what the rider can do, so it is the one that stays; the decline was already
        // announced when it happened. Splitting the sentence to keep the half that is still
        // true would be a new string in nineteen tables for a card nobody can act on.
        ackNotice("declined");
        own += '<div class="crewcard"><h3>' + t("crew.declined.h") + "</h3>"
          + '<p class=hint>' + t("crew.declined.p", { name: esc(me.declined_by) })
          + "</p></div>"
          // Joining first. A reviewer's tap-count table puts "newcomer to in a crew" as the
          // journey that matters and it ends in Join, while founding is the rarer and bigger
          // act -- and the create form's colour grid is 200px of scroll in front of it.
          + joinHTML(all, me)
          + (me.can_found && me.creation_open && !me.cooldown_until
             ? createHTML(window.__CREWIDENT__ || null) : "")
      } else if (!me.can_found && !me.cooldown_until) {
        // Both gates can be shut at once -- no validated ride AND just left a crew -- and
        // the panel printed "Joining one works right now" directly above "Next crew in 5
        // days". The cooldown is the nearer answer, so joinHTML's own card carries it.
        own += '<div class="crewcard"><h3>' + t("crew.first.h") + "</h3>"
          + '<p class=hint>' + t("crew.first.p") + "</p></div>" + joinHTML(all, me);
      } else {
        // Cooling off: joinHTML already swaps the list for the countdown, but the create form
        // was rendered regardless, so the panel offered a full form whose only possible
        // outcome is the error in the card directly below it.
        own += joinHTML(all, me)
          + (me.cooldown_until ? ""
             : me.creation_open
               ? createHTML(window.__CREWIDENT__ || null)
               : '<div class="crewcard"><h3>' + t("crew.closed.h") + "</h3>"
                 + '<p class=hint>' + t("crew.closed.p") + "</p></div>");
      }
      // Act, then the rules, then the standings. In a crew the rules are already above the
      // crew card; without one they go under the two things you can actually press.

      // Signed out is the one case with nothing of your own to put first. The rules come
      // with it, shut: deciding whether to bother is exactly when somebody wants to read what
      // the mode is, and until now they were only rendered to people who had already signed
      // in. `<details>` with no `open`, so the card you land on is still the sign-in alone.
      // Two wrappers, so a wide screen can put the board beside what you can DO rather than
      // a kilometre below it. They are `display: contents` under the desktop breakpoint, so
      // at phone width the cards flow exactly as they always have and nothing here changes.
      // Wrappers rather than grid placement on the cards themselves: with the board pinned to
      // column 2 row 1, row 1's height becomes the height of the whole board and a gap opens
      // under the first card on the left.
      // Signed out there is nothing to put in a left column: the sign-in card is one short
      // block and the board is the rest of the panel, so splitting them left the whole left
      // half of a 1100px panel blank -- a reviewer measured 515x471, 27% of the split area,
      // on the first screen a stranger ever sees. `crewcolsolo` tells the stylesheet to stay
      // in one column and keep the narrow panel for that case.
      var h = '<div class="crewcol crewcolmain' + (me.paired ? "" : " crewcolsolo") + '">'
        + (me.paired ? own : signInHTML())
      // The only sign-out button in the feature was emitted by `myCrewHTML`, which this
      // function calls on the `me.crew` branch alone -- so cooling off, removed, folded,
      // declined and no-ride-yet had no control of ANY kind on them. A reviewer pressed
      // Leave and found an empty `querySelectorAll` while the endpoint answered 200.
        + ((me.paired && !me.crew)
           ? '<div class="crewfoot"><button class="crewbtn ghost" id="cm-signout">'
             + t("crew.mine.signout") + "</button></div>"
           : "")
        + "</div>"
        + '<div class="crewcol crewcolside">' + board + "</div>";
      panel.innerHTML = h;
      // Put the rider back where they were. Pressing Refresh threw the scroll away: a
      // reviewer measured 2038px -- three and a half screens into an expanded join list --
      // gone, with nothing saying anything had happened.
      //
      // `panel` is `#crewpanel`, which lives INSIDE the scroller and is replaced wholesale by
      // `setPanel`; the scroll belongs to `#pbody`. My first attempt read and wrote it on this
      // element, which never scrolls, and the restore was a no-op on the wrong node. Captured
      // in `show()` before `setPanel` wipes it, applied here once the content it has to scroll
      // through exists.
      if (KEEPTOP) {
        var pb = document.getElementById("pbody"), want = KEEPTOP;
        KEEPTOP = 0;
        if (pb) {
          requestAnimationFrame(function () {
            pb.scrollTop = Math.min(want, Math.max(0, pb.scrollHeight - pb.clientHeight));
          });
        }
      }
      dockDot(me.pending ? me.pending.length : 0);

      if (!me.paired) startPairing();
      else stopPairing();
      if (me.crew) {
        bindMine(me);
        // The waiting state renders the browse list UNDER the crew card, and a pending rider
        // has `me.crew`, so this branch used to leave that list unbound: the filter and the
        // toggle were both inert, and with no `paint()` every row rendered unfolded, so
        // "Show all 22" was false the moment it appeared. Binding what was rendered.
        if (me.status === "pending") { bindJoin(); bindList(); }
      } else { bindCreate(); bindJoin(); bindList(); }
      bindSignOut();
      bindHelp();
      offerInvite(me);
      doReveal();
      // The arrival flash, if one is still owed; see `applyFlash`.
      applyFlash();
      // And the invite prompt it arrived with; see `applyAsk`.
      applyAsk();
      // Before the message, so the fields are back in place by the time anything is anchored
      // to one of them and scrolled into view.
      if (pendingDraft) {
        Object.keys(pendingDraft).forEach(function (id) {
          var el = document.getElementById(id);
          if (el) el.value = pendingDraft[id];
        });
        pendingDraft = null;
        // The counters under the name and description are written by their own `input`
        // handlers, so a value restored behind their back leaves "0/28" over a filled field.
        var nm = document.getElementById("cf-name");
        if (nm) nm.dispatchEvent(new Event("input", { bubbles: true }));
        var ds = document.getElementById("cf-desc");
        if (ds) ds.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (pendingStatus) {
        setStatus(pendingStatus, pendingBad,
                  pendingNear ? document.getElementById(pendingNear) : null);
        pendingStatus = null; pendingBad = false; pendingNear = null;
      }
      // One listener for every crew emblem the panel draws, wherever it is. Delegated on the
      // panel rather than bound per element: the board is rendered by the host's shared
      // `podList` and the browse list is rebuilt on every filter keystroke, so there is no
      // one place to bind and no moment at which they are all present.
      panel.querySelectorAll(".crewembgo").forEach(function (im) {
        // Named for the crew, not for its slug, and reachable by keyboard. I shipped these
        // announced as a control, unreachable by one, and labelled with the slug -- a URL
        // fragment read out where a crew's name belongs. The `focusable: false` argument
        // exists for the map's thirty emblem markers, which are reachable another way; these
        // are the only route to the crew sheet, so they belong in the tab order.
        var c = crewBySlug(im.dataset.emb);
        pressable(im, (c && c.name) || im.dataset.emb, function (ev) {
          // The emblem sits inside a row that flies the map and inside a <summary> that
          // folds; neither should happen when the thing you pressed was the picture.
          if (ev) { ev.preventDefault(); ev.stopPropagation(); }
          openCrewSheet(im.dataset.emb);
        });
      });
      panel.querySelectorAll(".crewboard [data-i]").forEach(function (el) {
        var i = +el.dataset.i, r = rank[i];
        // `aria-label` REPLACES the element's own text, so naming the rank and the crew did
        // not merely leave out the 91 squares, the 2 patches and the +13 this week: it
        // suppressed them. A reader on a card headed "Biggest patch a crew holds in one
        // piece" heard sixteen names and not one number. `rowLabel` joins what is actually
        // in the row, which is what the target rows have always done.
        // No ordinal prefix: the row's first child IS the rank, so `rowLabel` already has
        // it and prefixing produced "2nd · 2ND · Polar Night Riders".
        // Stamped here rather than in the markup: the board is drawn by the host's shared
        // `podList`, the same renderer every other panel's table uses, so there is nowhere in
        // this file to put an attribute on its rows. `plainRank` below carries it inline for
        // the fallback path. `openCrewDetail` finds a row by it.
        if (r && r.slug) el.dataset.slug = r.slug;
        // Which line is you. The board is the one screen that answers "how am I doing
        // against everybody", and a reviewer measured the signed-in leader's own row as
        // byte-identical to every other: same background, same colour, same weight. Your own
        // card says "12th · 2 squares off 11th" and the board, which is the thing people
        // scroll, said nothing. `crew.how.s3` is already "Your crew", so the name for it
        // exists and nothing new has to be written in nineteen languages.
        var mine = !!(r && r.slug && ME && ME.crew && r.slug === ME.crew.slug);
        el.classList.toggle("crewrowmine", mine);
        pressable(el, (r && rowLabel(el)) + (mine ? " · " + t("crew.how.s3") : ""), function () {
          if (!r) return;
          flyToCrew(r.slug);
          closeIfCovering();
        });
      });
    });
    if (!window.__CREWIDENT__) {
      api("GET", "/api/v1/crews/identity").then(function (r) {
        if (!r.ok) return;
        window.__CREWIDENT__ = r.body;
        // Nothing redrew when this landed, so the first create form of a session was painted
        // with a hard-coded blue under a heading that says we picked something free.
        if (document.getElementById("cf-colours")) show();
      });
    }
  }

  // Tapping a row flies the map, and on a phone the map is entirely behind the panel: the
  // whole thing happens where you cannot see it, so it reads as nothing having happened.
  // `flyToRider` and `flyToCountry` in the host have closed the panel on a tap since they
  // were written -- this is the same act in a different panel, and it did not.
  //
  // Measured rather than a width: the question is whether the panel is covering the map, and
  // `flyToTile` beside it hardcoded 560px for the same decision. At 390x844 the panel is 367
  // x641, which is 71% of the screen; at 1280 it is 720 of 1280 wide and leaves the map in
  // view, so it stays open and the row keeps its highlight.
  function panelCovers() {
    var el = panel && panel.closest ? panel.closest(".panel") : null;
    if (!el || !window.innerWidth) return window.innerWidth <= 560;
    var r = el.getBoundingClientRect();
    return (r.width * r.height) / (window.innerWidth * window.innerHeight) > 0.55;
  }

  function closeIfCovering() {
    if (panelCovers() && H.closePanel) H.closePanel();
  }

  /* ---------- lighting a row ----------
     The host writes `style="animation:rowin .5s both;animation-delay:45ms"` onto every
     standings row and podium card as it builds them (web/public.py:815 and :817). An inline
     declaration beats any stylesheet rule without `!important`, so `.crewlit` NEVER applied:
     a reviewer sampled `getAnimations()` on the target row every 180ms for 2.9 seconds and
     found only `rowin`, with the background flat `rgba(0,0,0,0)` throughout. The class was
     added and removed exactly as intended and nothing could be seen.

     The inline animation is a half-second entry effect that finished long before anything
     asks for a flash, so the property is removed rather than fought with `!important` --
     which would have to win against two shorthands and would also kill the entry effect for
     every future row. */
  function flashRow(el) {
    if (!el) return;
    el.style.removeProperty("animation");
    el.style.removeProperty("animation-delay");
    // restart cleanly if the row is lit twice in a row
    el.classList.remove("crewlit");
    void el.offsetWidth;
    el.classList.add("crewlit");
    setTimeout(function () { el.classList.remove("crewlit"); }, 2400);
  }

  function openCrew(slug) {
    if (!panelOpen) show();
    flyToCrew(slug);
  }

  // What "Details" on a tapped square opens: the panel, scrolled to that crew's row on the
  // board and lit for a moment. The board row is everything the panel knows about a crew
  // that is not yours -- where it stands, how much it holds, how it is moving -- and before
  // this there was no way to get from a colour on the map to it except by reading sixteen
  // names. The flash matters: the panel can be a long scroll, and landing silently in the
  // middle of a table leaves you looking for what just happened.
  function openCrewDetail(slug) {
    if (!panelOpen) show();
    setTimeout(function () {
      var row = panel.querySelector('.crewboard [data-slug="' + slug.replace(/"/g, "") + '"]');
      if (!row) return;
      try { row.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e) {
        row.scrollIntoView();
      }
      // Flash when the row ARRIVES, not when it is asked for. A smooth scroll of 2801px takes
      // about 1.37s, and the flash is a 2.2s decay from full strength -- so starting both at
      // once spent the brightest half of it on a row that was still off screen. A reviewer
      // sampled it: lit at alpha 0.937 at t=323ms, row first visible at t=1022ms by which
      // point it was 0.494 and falling, with only 20 of 53 visible-and-lit samples above 0.2.
      // The point of the flash is to say "this one", to somebody who can see it.
      //
      // `scrollend` is the event for exactly this and is not everywhere yet, so there is a
      // timer behind it; whichever happens first wins, and `flashRow` is safe to call twice.
      var pb = document.getElementById("pbody");
      var fired = false;
      var go = function () {
        if (fired) return;
        fired = true;
        if (pb) pb.removeEventListener("scrollend", go);
        flashRow(row);
      };
      if (pb && "onscrollend" in pb) pb.addEventListener("scrollend", go, { once: true });
      setTimeout(go, 900);
    }, 280);
  }

  // The crew's BIGGEST patch, not every square it holds.
  //
  // This used to fit bounds over all of them, which for a crew with ground in two cities
  // framed both and showed neither: Nordlys hold squares in Oslo and Tromso, and clicking
  // them on the board flew you to a view of Norway. The board ranks crews on their biggest
  // single connected patch, so that patch is both the thing the row is about and the thing
  // worth looking at.
  function flyToCrew(slug) {
    if (!TERR) return;
    var idx = -1;
    TERR.crews.forEach(function (c, i) { if (c.slug === slug) idx = i; });
    if (idx < 0) return;
    var pts = [], i;
    for (i = 0; i < TERR.cells.length; i += 5) {
      if (TERR.cells[i] === idx) pts.push([TERR.cells[i + 1], TERR.cells[i + 2]]);
    }
    if (!pts.length) return;
    var patch = biggestPatch(pts);
    var b = new maplibregl.LngLatBounds();
    patch.forEach(function (q) {
      b.extend([tileLon(q[0], TERR.z), tileLat(q[1], TERR.z)]);
      b.extend([tileLon(q[0] + 1, TERR.z), tileLat(q[1] + 1, TERR.z)]);
    });
    try {
      map.fitBounds(b, { padding: { top: 90, bottom: 320, left: 50, right: 50 },
                         maxZoom: 11.5, duration: 1800, essential: true });
    } catch (e) {}
  }

  // Open the mode looking at some actual ground.
  //
  // The map's default camera is lng 10 / lat 62 at zoom 3.8 -- the Norwegian Sea, from high
  // enough that a 1.2km square is well under a pixel. A reviewer opened Crews cold and got a
  // black rectangle with four emblem tiles on it, then signed in as a member of a COPENHAGEN
  // crew and watched the panel list Copenhagen targets beside a map still sitting on central
  // Norway. The territory renders correctly -- at z9 the same view is the best screen in the
  // feature -- so every frame of that was a camera problem, and the one thing the mode exists
  // to show was the one thing nobody saw until they clicked something.
  //
  // Your own crew if you are in one, because that is the ground the panel is talking about;
  // the top of the board otherwise, because a stranger should meet the game rather than the
  // sea. Once per page: a second framing would fight the reader, and reopening the panel after
  // deliberately panning somewhere is not a request to be moved back.
  var framed = false;
  function frameTerritory() {
    if (framed || !visible || !TERR || !TERR.crews || !TERR.crews.length) return;
    // Already looking at something at territory scale -- panned there, or arrived by a link.
    // Below this zoom nothing this mode draws is legible, which is the same threshold the
    // fill fade is built on.
    if (map.getZoom() >= ZOOM_LO) { framed = true; return; }
    var mine = ME && ME.crew && ME.crew.slug;
    var lead = BOARD && BOARD.length ? BOARD[0].slug : null;
    var slug = mine || lead;
    if (!slug) return;                  // no crew and no board yet: try again next render
    framed = true;
    flyToCrew(slug);
  }

  // Connected components over a list of squares, four-way, returning the largest. The server
  // computes the same thing for the board (`territory.regions`); doing it again here is a
  // flood fill over one crew's squares rather than another field on every payload.
  function biggestPatch(pts) {
    var own = Object.create(null), i;
    for (i = 0; i < pts.length; i++) own[pts[i][0] + ":" + pts[i][1]] = pts[i];
    var seen = Object.create(null), best = [];
    for (i = 0; i < pts.length; i++) {
      var k0 = pts[i][0] + ":" + pts[i][1];
      if (seen[k0]) continue;
      var stack = [pts[i]], comp = [];
      seen[k0] = 1;
      while (stack.length) {
        var q = stack.pop();
        comp.push(q);
        var nbs = [[q[0] + 1, q[1]], [q[0] - 1, q[1]], [q[0], q[1] + 1], [q[0], q[1] - 1]];
        for (var j = 0; j < 4; j++) {
          var k = nbs[j][0] + ":" + nbs[j][1];
          if (own[k] && !seen[k]) { seen[k] = 1; stack.push(own[k]); }
        }
      }
      if (comp.length > best.length) best = comp;
    }
    return best;
  }

  // Where on screen there is room to put the marked square.
  //
  // Three versions of this aimed at a named direction and each was defeated by a size nobody
  // had measured: the panel's own centre (identically zero, the panel is centred), the wider
  // margin (there is none between 561 and 1080), and the region above the panel (which is
  // where the topbar lives -- a reviewer measured the ring completely hidden under it at
  // 600x800 and back behind the panel at phone-landscape). The obstacles are on screen and
  // measurable, so this asks where there is room instead of assuming.
  // `skip` leaves one obstacle out. It exists because the only thing the caller can do when
  // there is no room is close the panel, and it cannot measure that having happened: see
  // offsetForFlight().
  function freeOffset(skip) {
    var W = window.innerWidth, H = window.innerHeight;
    var boxes = [];
    [".panel", ".topbar", ".dock"].forEach(function (sel) {
      if (sel === skip) return;
      var el = document.querySelector(sel);
      if (!el) return;
      var r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) boxes.push(r);
    });
    if (!boxes.length) return [0, 0];

    // How far a point sits from the nearest obstacle, and from the edges of the window.
    function clearance(x, y) {
      var best = Math.min(x, y, W - x, H - y);
      for (var i = 0; i < boxes.length; i++) {
        var b = boxes[i];
        var dx = Math.max(b.left - x, 0, x - b.right);
        var dy = Math.max(b.top - y, 0, y - b.bottom);
        var d = (dx === 0 && dy === 0) ? -1 : Math.sqrt(dx * dx + dy * dy);
        if (d < best) best = d;
      }
      return best;
    }

    // Half the ring the map draws, plus a little air. The first version scored a POINT and
    // accepted 40px of clearance, so a "clear" centre still hung 56px of gold square over the
    // panel or off the edge of the window -- four viewports in twelve, which the reviewer
    // measured in Chrome while my own harness called them all clean.
    var RING = 56, need = RING + 12;
    var step = 24, bx = W / 2, by = H / 2, bs = clearance(bx, by);
    for (var x = step; x < W; x += step) {
      for (var y = step; y < H; y += step) {
        var c = clearance(x, y);
        if (c > bs) { bs = c; bx = x; by = y; }
      }
    }
    // Nowhere with elbow room at all. A phone held sideways is the real case: the panel is
    // 94vw by 76dvh and there is no corner of the map left to fly to. Saying so lets the
    // caller do what the portrait phone already does and move the panel out of the way,
    // which is the only honest answer when the screen is full.
    if (bs < need) return null;
    return [bx - W / 2, by - H / 2];
  }

  // The whole decision, in one place a test can reach. It lived inside flyToTile, so a
  // harness could only re-implement it -- and a re-implementation agrees with itself by
  // construction, which is how the harness went on passing while the shipped caller threw
  // freeOffset()'s answer away and flew to dead centre.
  function offsetForFlight() {
    var off = window.innerWidth <= 560 ? [0, 0] : freeOffset();
    if (off !== null) return off;
    // Closing the panel changes the obstacle set, which is the entire premise of
    // freeOffset(), so ask again rather than hardcode the centre: measured at 844x390 the
    // ring came down 11% under the topbar with the panel already gone and half the map free.
    // Closing it changes the obstacle set, which is the premise -- but it cannot be MEASURED
    // afterwards. `closePanel()` starts a 280ms slide and removes the class on a timeout, so
    // a second look at the DOM sees the panel exactly where it was, returns null again, and
    // the `|| [0, 0]` below sends the square to dead centre. A reviewer measured the box as
    // bit-identical immediately after the call, and my harness had been hiding it by deleting
    // the panel synchronously -- so the test asserted a fallback that could not run. The panel
    // comes out by argument instead, which is knowable now rather than in 280ms.
    if (H.closePanel) H.closePanel();
    return freeOffset(".panel") || [0, 0];
  }

  function flyToTile(x, i) {
    if (!x || !TERR) return;
    var lon = (tileLon(x.x, TERR.z) + tileLon(x.x + 1, TERR.z)) / 2;
    var lat = (tileLat(x.y, TERR.z) + tileLat(x.y + 1, TERR.z)) / 2;
    // Closing the panel threw the list away to show eight identical outlines, so comparing two
    // squares cost two full round trips. On a phone the panel covers the map and has to go.
    TARGETSEL = i == null ? -1 : i;
    // a losing square is drawn too, in the colour its card uses, because flying there and
    // marking nothing left you on a map with no way to tell which square you were sent to
    showTargets(TARGETS, i == null ? x : null);
    closeIfCovering();
    // On a phone the panel is gone by now and the centre is the centre. On a desktop it is
    // still there and the square was flying to dead centre, behind the card that had just
    // said "Tap it to find it".
    // The first attempt at this shifted the camera by the panel's own centre, which is
    // identically zero: `.panel` is `left: 50%` with `translateX(-50%)`, so its centre is the
    // window's centre at every width. What is actually free is the margin beside it, so the
    // square is put in the middle of whichever margin is wider.
    var off = offsetForFlight();
    // Close enough to find the street, far enough to still see it against the crew's own
    // ground. Flying to 13.2 put one square across the whole screen, which answers "where is
    // it" with a picture of nowhere. A reader already zoomed in keeps their zoom.
    map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 11.8),
                offset: off, duration: 1600, essential: true });
  }

  // The same squares, marked on the ground. A list of distances is a table; a ring around the
  // block two streets over is a route. Not in the white dashed line the contested ring already
  // uses: that one means somebody is taking ground off you, which is the opposite thing.
  function showTargets(rows, losing) {
    // not on crew-cells: buildLayers returns early when no crew holds anything, so on a fresh
    // install the "puts you on the map" squares were listed and never drawn for anybody
    if (!map || !TERR || !map.isStyleLoaded()) return;
    var feats = (rows || []).map(function (x, i) {
      return { type: "Feature",
               properties: { sel: i === TARGETSEL ? 1 : 0, dim: x.blocked ? 1 : 0, lose: 0 },
               geometry: { type: "Polygon", coordinates: tileRing(x.x, x.y, TERR.z) } };
    });
    if (losing) {
      feats.push({ type: "Feature", properties: { sel: 1, dim: 0, lose: 1 },
                   geometry: { type: "Polygon",
                               coordinates: tileRing(losing.x, losing.y, TERR.z) } });
    }
    var data = { type: "FeatureCollection", features: feats };
    if (map.getSource("crew-targets")) { map.getSource("crew-targets").setData(data); return; }
    map.addSource("crew-targets", { type: "geojson", data: data });
    // a dark casing first, or a thin gold line disappears over the pale half of the palette
    addLayer({
      id: "crew-target-case", type: "line", source: "crew-targets",
      paint: { "line-color": "rgba(0,0,0,.85)",
               "line-width": ["interpolate", ["linear"], ["zoom"], 8, 4, 14, 8] }
    });
    // an invisible fill, because the line it used to be bound to is three pixels wide
    addLayer({
      id: "crew-target-hit", type: "fill", source: "crew-targets",
      paint: { "fill-color": "#000", "fill-opacity": 0.01 }
    });
    addLayer({
      // Ground to take: gold and solid. Ground to defend is a separate layer because
      // line-dasharray takes no data expression, which is just as well: solid against dashed
      // is the real difference. Seven of the twenty-four crew colours sit close enough to one
      // of these two hues to erase it, and a crew can pick any of them, so the colour is a
      // nicety and the shape is the signal.
      id: "crew-target-line", type: "line", source: "crew-targets",
      filter: ["!=", ["get", "lose"], 1],
      paint: {
        "line-color": "#ffd24a",
        // zoom has to be the input to the interpolate, not buried inside a case, so the
        // selected-or-not test moves into the stop values
        "line-width": ["interpolate", ["linear"], ["zoom"],
                       8, ["case", ["==", ["get", "sel"], 1], 3.2, 1.6],
                       14, ["case", ["==", ["get", "sel"], 1], 5.5, 3]],
        "line-opacity": ["case", ["==", ["get", "dim"], 1], 0.45, 0.95]
      }
    });
    addLayer({
      id: "crew-lose-line", type: "line", source: "crew-targets",
      filter: ["==", ["get", "lose"], 1],
      paint: {
        "line-color": "#ff9f6b",
        // dotted, not dashed: crew-contested is already dashed at the same rhythm
        "line-dasharray": [1, 1.6],
        "line-width": ["interpolate", ["linear"], ["zoom"],
                       8, ["case", ["==", ["get", "sel"], 1], 3.2, 1.6],
                       14, ["case", ["==", ["get", "sel"], 1], 5.5, 3]],
        "line-opacity": 0.95
      }
    });
  }

  // A fresh install has no territory file until the first rebuild runs. The payload says so
  // and nothing read it, so the map was blank and the board said "nobody holds anything yet",
  // which is exactly what a broken feature looks like.
  function firstRunNote() {
    if (!TERR || !TERR.pending) return "";
    return '<div class="crewmsg">' + esc(t("crew.first.building")) + "</div>";
  }

  function reloadTerritory() {
    return fetch("/api/v1/territory", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (t) {
        if (!t) return;
        TERR = t;
        clearLayers();
        if (visible) { buildLayers(); frameTerritory(); }
      }).catch(function () {});
  }

  /* ---------- wiring ---------- */

  window.EUCCrews = {
    // What the Crew Champions card calls when one of its rows is pressed. The panel may not be
    // open or even rendered yet, so this opens it and hands the slug to the same sheet the
    // board's own emblems open -- after a render, because `ALL` and `BOARD` are what
    // `crewBySlug` reads and they arrive with it.
    openCrew: function (slug) {
      show();
      var tries = 0;
      (function wait() {
        if (crewBySlug(slug)) { openCrewSheet(slug); return; }
        if (++tries > 40) return;      // ~6s, then give up quietly
        setTimeout(wait, 150);
      })();
    },
    init: function (theMap, helpers) {
      map = theMap;
      H = helpers || {};
      reloadTerritory();
      // Somebody pressed the dock button with a keyboard while this was still loading.
      if (pendingShow) { pendingShow = false; show(); }
      // The one call this needed and never had. Without it the dock count only ever appeared
      // for a leader who had already opened the panel, which is the one person who does not
      // need telling.
      primeDock();
      map.on("style.load", function () {
        // a style switch wipes every layer; the payload is already in memory
        if (TERR && visible) { clearLayers(); buildLayers(); }
      });
    },
    show: show,
    // closing the panel leaves the territory drawn, but there is nothing to poll for once
    // nobody is looking at the code
    // The panel slid away; the mode is still on and the map is still painted. Recording it
    // is what lets anything reopen the panel -- see the two flags at the top.
    // `heldNotice` goes with it: the notice has been read and acknowledged, so holding it
    // past the close of the panel would resurrect a spent card on the next visit. Held for
    // this reading, not for ever.
    panelClosed: function () {
      panelOpen = false; heldNotice = null; noticeAcked = null;
      stopPairing(); syncKey();
    },
    hide: function () {
      if (!visible) return;
      visible = false;
      panelOpen = false;
      unmountKey();
      stopPairing();
      clearLayers();          // rectangles belong to this mode and nowhere else
      heatUnderTerritory(false);
      setHeat(true);
    },
    reload: reloadTerritory
  };
})();
