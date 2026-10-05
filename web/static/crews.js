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
  var visible = false;
  var pairTimer = null, pairToken = null;
  var ME = null;

  var PATTERNS = ["solid", "stripes", "dots", "hatch"];
  var CFG = window.__CREWCFG__ || {};
  // `|| 7` turned a configured 0 into 7, so an admin who switched the cooldown off was still
  // telling riders to wait a week. Same class of bug as the hardcoded "7 days" it replaced.
  var COOLDOWN_DAYS = CFG.cooldown_days != null ? CFG.cooldown_days : 7;
  var SEED = CFG.seed || 2;
  var WINDOW_DAYS = CFG.window_days || 90;

  // "1 days" is not a thing
  function fadesIn(n) {
    // The `ago.days` family, not `days`: this reads "fades in …", a duration, and German
    // wants the dative there (`in 3 Tagen`, not `in 3 Tage`) -- which is the whole
    // difference between the two families. `heldFor()` uses the same one. n is never 1
    // here, so the absent singular cannot be reached.
    return n <= 1 ? t("crew.tile.day1")
      : t("crew.tile.days", { v: plural(null, "crew.ago.days.few", "crew.ago.days", n) });
  }

  // With the cooldown switched off there is no waiting to describe, so the sentence changes
  // rather than the number. "No new crew for right now" is what came out before.
  function leaveQuestion(name) {
    return COOLDOWN_DAYS > 0
      ? t("crew.mine.leaveq", { name: name, n: days(COOLDOWN_DAYS) })
      : t("crew.mine.leaveq0", { name: name });
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
  function patternImage(kind) {
    var S = 16, cv = document.createElement("canvas");
    cv.width = cv.height = S;
    var g = cv.getContext("2d");
    g.clearRect(0, 0, S, S);
    g.strokeStyle = "rgba(0,0,0,.42)";
    g.fillStyle = "rgba(0,0,0,.42)";
    g.lineWidth = 3;
    if (kind === "stripes") {
      for (var i = -S; i < S * 2; i += 8) {
        g.beginPath(); g.moveTo(i, 0); g.lineTo(i + S, S); g.stroke();
      }
    } else if (kind === "hatch") {
      g.lineWidth = 2;
      for (var j = -S; j < S * 2; j += 8) {
        g.beginPath(); g.moveTo(j, 0); g.lineTo(j + S, S); g.stroke();
        g.beginPath(); g.moveTo(j + S, 0); g.lineTo(j, S); g.stroke();
      }
    } else if (kind === "dots") {
      [[4, 4], [12, 12]].forEach(function (p) {
        g.beginPath(); g.arc(p[0], p[1], 2.6, 0, 6.2832); g.fill();
      });
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

  function addLayer(spec) {
    if (LAYERS.indexOf(spec.id) < 0) LAYERS.push(spec.id);
    map.addLayer(spec);
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

  function bandOp(op, b3) {
    var ladder = ["match", ["get", "band"],
                  1, op * BAND_OP[1], 2, op * BAND_OP[2], 3, op * BAND_OP[3] * b3,
                  4, op, op];
    if (!ME || !ME.crew) return ladder;
    var mine = ["match", ["get", "band"],
                1, op * BAND_OP[1], 2, op * BAND_OP[2], 3, op * BAND_OP[3] * b3,
                4, op, op];
    var theirs = ["match", ["get", "band"],
                  1, op * BAND_OP[1] * THEIRS, 2, op * BAND_OP[2] * THEIRS,
                  3, op * BAND_OP[3] * b3 * THEIRS, 4, op * THEIRS, op * THEIRS];
    return ["case", ["==", ["get", "slug"], ME.crew.slug], mine, theirs];
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
      var pat = Math.min(1, op + 0.15);
      map.setPaintProperty("crew-fill", "fill-opacity", bandOp(op, 1));
      map.setPaintProperty("crew-pattern", "fill-opacity", bandOp(pat, 0.9));
      map.setPaintProperty("crew-edge", "line-opacity", 0.95);
      map.setPaintProperty("crew-edge-glow", "line-opacity", 0.35);
      map.setPaintProperty("crew-contested", "line-opacity", 0.8);
      map.setPaintProperty("crew-flipping", "line-opacity", 0.95);
      scopePulse();
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
      el.title = crew.name + " · " + fmtKm2(crew.km2);
      el.dataset.s = s;
      el.dataset.n = r.n || 1;              // region size, which decides how long it survives
      // NOT a tab stop. `pressable` gives a div a role, a name, Enter and Space, which is
      // right for a row inside a card and wrong for a marker on a map: about thirty of these
      // put the map between the dock and the panel, so opening Crews with a keyboard took 39
      // stops to get inside the thing you had just opened. The crew rows already fly to them,
      // which is the keyboard route that makes sense.
      pressable(el, crew.name, function (ev) {
        if (ev) ev.stopPropagation();
        openCrew(crew.slug);
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
        : band === 3 ? fadesIn(tenths)
        : band === 1 || band === 2 ? t("crew.tile.needw", { v: effort(km, y) })
        // the words version is already a whole clause; only the figures need a sentence
        : SHOW_NUMBERS ? t("crew.tile.clear", { v: fmtKm(km) }) : margin(km, y),
      // the third slot is the figure behind the phrase, the same one the two cards print.
      // With the setting on the phrase is already the figure, and 3 and 4 have no distance.
      SHOW_NUMBERS || band === 3 || band === 4 ? "" : fmtKm(km)
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
            ? (row.band === 3 ? fadesIn(Math.round(row.need * 10))
               : t("crew.lose.gap", { v: effort(row.need, row.y) }))
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
    POPUP = new maplibregl.Popup({ closeButton: false, className: "crewpop", offset: 10 })
      .setLngLat(e.lngLat)
      .setHTML('<div class="crewpop-in"><img src="/api/v1/crews/' + encodeURIComponent(p.slug)
        + '/emblem" alt=""/><div><b>' + esc(p.name) + "</b><span>" + esc(state)
        + "</span><span>" + esc(detail) + (fig ? " <i>" + esc(fig) + "</i>" : "")
        + '</span><span class="crewpop-since"></span>'
        + "</div></div>")
      .addTo(map);
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
    return '<img class="crewembsm" style="width:' + sz + "px;height:" + sz
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
          return "<li>" + t(k, { n: k === "crew.how.size" ? MAX_MEMBERS : SEED,
                                 d: WINDOW_DAYS, c: RIDER_WEEK_CAP,
                                 v: days(COOLDOWN_DAYS) }) + "</li>";
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
      + rulesSection("crew.how.s3", ["crew.how.5", "crew.how.cool"]
          // Only when there is one. With no cap there is no rule, and "unlimited" would be a
          // sentence about nothing.
          .concat(MAX_MEMBERS > 0 ? ["crew.how.size"] : []))
      // Outside the list: this one is not a rule, it is what the standings MEAN, so it closes
      // the section rather than becoming a ninth instruction.
      + '<p class="crewrulesend">' + t("crew.board.sub") + "</p>"
      // The five shades belong here rather than under the board. It is a key, and a key is
      // something you look up once, not a row of swatches on screen every time you visit.
      + legendHTML()
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
    var mins = Math.round((DRAWN.every - DRAWN.drawn_s_ago) / 60);
    // Overdue: the rebuild shares the retention loop, so it lands on that loop's next pass.
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
    var tight = window.innerWidth <= 560;
    var top3 = rows.slice(0, 3);
    // below the podium, which draws its own emblem above the name
    var isRow = function (e) { return top3.indexOf(e) < 0; };
    var short = function (e) { return tight && isRow(e); };
    return H.podList(rows, {
      iconFn: function (e) { return '<img class="crewpodemb" alt="" src="' + e.emblem + '"/>'; },
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
      return '<tr class="sel" data-i="' + i + '"><td class=rk>' + (i + 1) + "</td>"
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

  // Same per-quantity unit switch as the rest of the site: somebody reading in miles gets
  // "0.4 mi", not a kilometre figure with a mile label on it.
  function fmtKm(v) {
    var n = (v == null ? 0 : v) * (H.mph && H.mph() ? MI_PER_KM : 1);
    // A non-breaking space. The join list broke `137 km²` across two lines on five of its
    // eight rows, and `nowrap` is the wrong tool twice over in this file: once it cut text in
    // eleven locales, once removing it made a measurement lie in CJK.
    var u = H.mph && H.mph() ? "\u00a0mi" : "\u00a0km";
    // one decimal while it matters, none once it does not: "0.4 mi" and "137 km"
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
  var RIDER_WEEK_CAP = CFG.rider_week_cap_km != null ? CFG.rider_week_cap_km : 6;
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
        + drawnLine() + "</div>";
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
      + body + drawnLine() + "</div>";
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
  function widestOf(rows, phrase) {
    var n = 7;
    (rows || []).forEach(function (x) {
      var w = chWidth(phrase(x));
      if (w > n) n = w;
    });
    return Math.min(n + 1, 38);
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
      return x.band === 3 ? fadesIn(Math.round(x.need * 10))
                          : t("crew.lose.gap", { v: effort(x.need, x.y) });
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
          var reach = x.band === 3 || x.need < 0.05 ? null : fmtKm(x.need);
          var gap = x.band === 3
            ? fadesIn(Math.round(x.need * 10))
            : t("crew.lose.gap", { v: effort(x.need, x.y)
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
    return '<div class="crewcontrib"><h4>' + t("crew.mine.who") + "</h4>" + rows.map(function (c) {
      var share = Math.round((c.km / total) * 100);
      var pct = Math.max(2, share);
      return '<div class="crewcrow">'
        + (H.av ? H.av(c.id, c.has_avatar, c) : "")
        + (H.cc && c.flag ? H.cc(c.flag) : "")
        + '<span class="crewcname">' + esc(c.name) + roleMark(c.role) + "</span>"
        + '<span class="crewcbar"><i style="width:' + pct + '%"></i></span>'
        + '<span class="crewckm">' + fmtKm(c.km)
        + ' <i>' + t("crew.who.share", { n: share }) + "</i></span></div>";
    }).join("") + "</div>";
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

  function signInHTML() {
    // Same phone as the browser? Then there is nothing to point a camera at — you cannot scan
    // your own screen. The deep link opens the app directly and it comes straight back, so
    // the one awkward case in the whole flow is a tap. The QR itself is the same link, so on
    // a phone the image is tappable too.
    return '<div class="crewcard crewsign">'
      // The test warning used to be here, which meant a rider only ever saw it before they
      // had anything to lose. It is at the top of the panel now, for everybody.
      + "<h3>" + t("crew.signin.h") + "</h3>"
      + '<a class="crewqr" id="crewqr" href="#"><div class="spin"></div></a>'
      + '<div class="crewcode" id="crewcode">······</div>'
      // One line: what to point at it, which app, and which version. The version used to be a
      // paragraph of its own and the app's name is a link in whatever language the sentence is
      // written in -- the brand is the one part no locale translates, so one replace covers
      // all nineteen. `crew.signin.p` and `crew.signin.ride` are gone: the first was a sentence
      // about what the app does for you, and the second stated a precondition to everybody in
      // order to reach the few who do not meet it, who find out by trying and are told why.
      + '<p class=hint id="crewcodehint">'
      + appLink(t("crew.signin.scan", { v: (CFG && CFG.min_app) || "" })) + "</p>"
      // No second link to the same place. The version line above says "EUC Planet" and links
      // it; this line existed because that one did not, and two links three lines apart is one
      // link and a repetition.

      + '<p class="hint crewsame">' + t("crew.signin.same") + "</p>"
      + '<a class="crewbtn crewopen" id="crewopen" href="#">' + t("crew.signin.open") + "</a>"
      + "</div>";
  }

  // The code drawn as its own modules, so they can land one after another. Returns "" when
  // the server did not send them, and the caller falls back to the PNG.
  //
  // One element per module and the delay carried on each: 33x33 is 1,089 nodes, built once per
  // sign-in and thrown away with the card. A CSS-only diagonal is not possible here -- the
  // delay depends on row PLUS column, which no selector can express -- and an inline style is
  // cheaper than 1,089 rules.
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
  function offerRetry() {
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
    var open = document.getElementById("crewopen");
    if (open) {
      open.classList.add("dead");
      open.removeAttribute("href");
      // `aria-hidden` on the QR is right -- it is decoration once it cannot be scanned -- but
      // this is a labelled button, and hiding it left a reader with a card whose only
      // announced content was the heading. Disabled says the same thing and stays readable.
      open.setAttribute("aria-disabled", "true");
    }
    var el = document.getElementById("crewcodehint");
    if (!el) return;
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
      if (!r.ok) { setStatus(errMsg(r.err), true); offerRetry(); return; }
      pairToken = r.body.token;
      var qr = document.getElementById("crewqr");
      var code = document.getElementById("crewcode");
      var open = document.getElementById("crewopen");
      // the app-scheme form of the same link, so a tap on this device hands the code to the
      // app without a round trip through the web page
      var deep = "eucplanet://pair?code=" + encodeURIComponent(r.body.code)
        + "&host=" + encodeURIComponent(location.origin);
      if (qr) {
        qr.innerHTML = qrGrid(r.body.qr_rows)
          || ('<img alt="' + esc(t("crew.signin.qralt")) + '" src="data:image/png;base64,'
              + r.body.qr + '"/>');
        qr.href = deep;
      }
      if (open) open.href = deep;
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

  function ask(message, confirmLabel, ok, near) {
    var host = askHost(near);
    if (!host) { if (window.confirm(message)) ok(); return; }
    function done() { if (host.id === "crewstatus") host.innerHTML = ""; else host.remove(); }
    // The quiet button is the one that acts and the bright one is the way out. Leaving costs
    // a crew and a cooldown, and a stray tap should not be the easy path.
    // A dialog, named by its own question. Without this the trigger did not change, focus
    // did not move and nothing was announced, so pressing Disband gave a keyboard or screen
    // reader user no signal whatsoever -- on every irreversible action in the feature, and
    // the one for Leave states the seven-day cost in the text nobody heard.
    var qid = "crewask-q";
    host.innerHTML = '<div class="crewask" role="alertdialog" aria-labelledby="' + qid + '">'
      + '<p id="' + qid + '">' + esc(message) + "</p>"
      + '<button class="crewbtn mini ghost" id="crewask-y" aria-describedby="' + qid + '">'
      + esc(confirmLabel) + "</button>"
      + '<button class="crewbtn mini" id="crewask-n">' + t("crew.cancel") + "</button>"
      + "</div>";
    host.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
  function flash(btn, word) {
    if (!btn) return;
    if (btn._flash) { clearTimeout(btn._flash); }
    else { btn._was = btn.textContent; }
    btn.textContent = word;
    btn.disabled = true;
    btn.classList.add("crewdone");
    btn._flash = setTimeout(function () {
      btn.textContent = btn._was;
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
      }, so);
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
      + '<input id="crewask-in" placeholder="' + esc(placeholder) + '" maxlength="'
      + (opts.max || 16) + '">'
      + '<button class="crewbtn mini' + (opts.danger ? " ghost" : "") + '" id="crewask-y">'
      + esc(opts.confirm || t("crew.join.btn")) + "</button>"
      + '<button class="crewbtn mini' + (opts.danger ? "" : " ghost") + '" id="crewask-n">'
      + t("crew.cancel") + "</button>"
      + "</div>";
    host.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
      if (!v) { done(); return; }
      ok(v, { close: done, fail: fail });
    }
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
    promote_first: "crew.e.promote",
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
    if (code.indexOf("rate_limited") === 0) return t("crew.e.rate");
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
    var top = document.getElementById("crewstatus");
    if (!near || !near.parentNode) return top;
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
  function identGrids(prefix, ident) {
    var cols = (window.__CREWCFG__ && window.__CREWCFG__.palette) || [];
    var chosenC = cols.indexOf(ident.colour);
    if (chosenC < 0) chosenC = 0;
    var chosenP = PATTERNS.indexOf(ident.pattern);
    if (chosenP < 0) chosenP = 0;
    return {
      colours: '<div class="crewpick" id="' + prefix + '-colours" role="group" aria-label="'
        + esc(t("crew.new.colours")) + '">' + cols.map(function (c, i) {
          var on = c === ident.colour;
          return '<button type="button" class="crewpickc' + (on ? " on" : "")
            + '" aria-pressed="' + (on ? "true" : "false")
            + '" tabindex="' + (i === chosenC ? "0" : "-1")
            + '" data-c="' + c + '" style="background:' + c + '" title="'
            + esc(t("crew.new.colourn", { n: i + 1 })) + '" aria-label="'
            + esc(t("crew.new.colourn", { n: i + 1 })) + '"></button>';
        }).join("") + "</div>",
      patterns: '<div class="crewpick" id="' + prefix + '-patterns" role="group" aria-label="'
        + esc(t("crew.new.patterns")) + '">' + PATTERNS.map(function (pt, i) {
          var on = pt === ident.pattern;
          return '<button type="button" class="crewpickp' + (on ? " on" : "")
            + '" aria-pressed="' + (on ? "true" : "false")
            + '" tabindex="' + (i === chosenP ? "0" : "-1")
            + '" data-p="' + pt + '" title="' + esc(t("crew.pattern." + pt))
            + '" aria-label="' + esc(t("crew.pattern." + pt)) + '">'
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
  var INVITE = (function () {
    try {
      var q = new URLSearchParams(location.search);
      var slug = q.get("crew"), code = q.get("code");
      return (slug && code) ? { slug: slug, code: code } : null;
    } catch (e) { return null; }
  })();

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
      + g.patterns + "</div>"
      + '<input type="hidden" id="' + prefix + '-colour" value="' + ident.colour + '">'
      + '<input type="hidden" id="' + prefix + '-pattern" value="' + ident.pattern + '">';
  }

  // Wire one form's grids: selection, the preview, the hidden inputs, and arrow keys.
  function bindIdent(prefix, onChange) {
    function wire(sel, attr, hidden) {
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
          var h = document.getElementById(prefix + hidden);
          if (h) h.value = b.dataset[attr];
          mark(b);
          if (onChange) onChange();
        };
        b.onkeydown = function (e) {
          var step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1
                   : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1
                   : e.key === "Home" ? -all.length
                   : e.key === "End" ? all.length : 0;
          if (!step) return;
          e.preventDefault();
          var j = Math.max(0, Math.min(all.length - 1, i + step));
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
    wire("-colours", "c", "-colour");
    wire("-patterns", "p", "-pattern");
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
    return null;
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
      + '<input id="cf-name" maxlength="28" aria-describedby="cf-namewhy">'
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
          out.textContent = box.value.length + "/" + pair[2];
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
        setStatus(t(why), true, nmf);
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
            return '<div class="crewpendr"><span>'
              + (p.flag ? cc(p.flag) + " " : "") + esc(p.name) + "</span>"
              + '<button class="crewbtn mini" data-ok="' + esc(p.store_id) + '"'
              + ' data-name="' + esc(p.name) + '">' + t("crew.accept") + "</button>"
              + '<button class="crewbtn mini ghost" data-no="' + esc(p.store_id) + '"'
              + ' data-name="' + esc(p.name) + '">' + t("crew.decline") + "</button>"
              + "</div>"; }).join("")
        + "</div>";
    }
    if (c.description) h += "<p>" + esc(c.description) + "</p>";
    h += '<div class="crewterr" id="crewterr"><div class=spin></div></div>';
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
        + '<button class="crewbtn mini" id="cm-copylink" data-link="'
        + esc(inviteLink(c)) + '">' + t("crew.mine.copylink") + "</button>"
        + '<button class="crewbtn mini ghost" id="cm-copy" data-code="'
        + esc(c.invite_code) + '">' + t("crew.mine.copy") + "</button></span></p>";
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
    if (me.roster && me.roster.length > 1
        && (me.role === "leader" || me.role === "officer")) {
      h += '<div class="crewpend"><h4>' + t("crew.roles.h") + "</h4>"
        + me.roster.map(function (x) {
            var mark = x.role === "leader" || x.role === "officer"
              ? " " + roleMark(x.role) : "";
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
            return '<div class="crewpendr"><span>' + esc(x.name) + mark + "</span>" + btn
              + "</div>";
          }).join("") + "</div>";
    }
    if (lead) {
      h += '<details class="crewedit"><summary>' + t("crew.mine.settings") + "</summary>"
        + "<label>" + t("crew.new.name") + '<input id="ce-name" maxlength="28" value="'
        + esc(c.name) + '"></label>'
        + "<label>" + t("crew.new.desc") + '<input id="ce-desc" maxlength="280" value="'
        + esc(c.description || "") + '"></label>'
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
        + '<p class=hint>' + t("crew.mine.emblemp") + "</p>" 
        // `.crewacts`, like every other button row in this card. Emitted as bare
        // siblings these two had no horizontal spacing at all -- measured 0.00px apart --
        // so Save and "Use the drawn one" read as a single merged control.
        + '<div class="crewacts">'
        + '<button class="crewbtn" id="ce-save">' + t("crew.mine.save") + "</button>"
        + '<button class="crewbtn ghost" id="ce-clearlogo">' + t("crew.mine.generated")
        + "</button></div>"
        + "</details>";
    }
    h += '<div class="crewacts">'
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
      };
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
            }, b);
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
      ask(pending ? t("crew.mine.cancelq", { name: c.name }) : leaveQuestion(c.name),
          t(pending ? "crew.mine.cancel" : "crew.mine.leave"), function () {
        api("POST", "/api/v1/crews/leave", {}).then(function (r) {
          // The card that explains what just happened, not the leaderboard. Flashing
          // `.crewboard` scrolled 0 -> 405 and put the "next crew in 7 days" card at
          // viewTop -57: off the top of the screen at the moment it was written.
          if (r.ok) { reveal(".crewcard:not(.crewboard)"); show(); reloadTerritory(); }
          else if (!onWrite(r)) setStatus(errMsg(r.err), true, leave);
        });
      }, leave);
    };
    var dis = document.getElementById("cm-disband");
    if (dis) dis.onclick = function () {
      // The name, typed. Every other control in this card can be undone by doing it again;
      // this one ends a crew other people rode for, and it used to be two taps where the
      // second one opened under the cursor.
      askFor(t("crew.mine.disbandq", { name: c.name }) + " " + t("crew.mine.disbandtype", { name: c.name }),
             c.name, function (typed, ctl) {
        // Composed on both sides, like `name_ok` does on the server: an accented name can be
        // typed decomposed and stored composed, and the leader would be told that their own
        // crew's name is not its name.
        var norm = function (x) { x = (x || "").trim(); return x.normalize ? x.normalize("NFC") : x; };
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

    var cl = document.getElementById("cm-copylink");
    if (cl) cl.onclick = function () {
      var link = cl.dataset.link || "";
      function said() { setStatus(t("crew.mine.copied"), false, cl); flash(cl, t("crew.mine.copied")); }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(link).then(said, function () { fallback(); });
        return;
      }
      fallback();
      function fallback() {
        // No `<code>` holding the URL to select, so the offscreen textarea is the only route;
        // if that fails too the status line says nothing happened rather than claiming it did.
        var ta = document.createElement("textarea");
        ta.value = link;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        var ok = false;
        try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
        document.body.removeChild(ta);
        if (ok) said();
        else setStatus(t("crew.err"), true, cl);
      }
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
        setStatus(t(bad), true, nm);
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
          setStatus(t("crew.mine.saved"), false, save);
          reloadTerritory();
          // The colour may have changed, so the swatch beside the summary is refreshed in
          // place rather than by rebuilding the panel around it.
          var sw = document.querySelector(".crewsumemb");
          if (sw) sw.src = sw.src.split("?")[0] + "?v=" + Date.now();
        } else if (!onWrite(r)) {
          // A name-shaped refusal belongs beside the name, not beside Save: anchored to Save,
          // `scrollIntoView` dragged the field to y-235 and the rider typed into a box they
          // could not see.
          var code = (r.err && (r.err.code || r.err.detail)) || "";
          var nmf = document.getElementById("ce-name");
          var near = (code === "name_taken" || code === "bad_name") && nmf ? nmf : save;
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
      el.innerHTML = '<div class="crewbig">' + tiles(held)
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
  function groundAway(slug) {
    if (!TERR || !map) return null;
    var idx = -1;
    TERR.crews.forEach(function (c, i) { if (c.slug === slug) idx = i; });
    if (idx < 0) return null;
    var c = map.getCenter(), best = null;
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
      out = d.toLocaleString(locale() || undefined,
                             { day: "numeric", month: "short", hour: "2-digit",
                               minute: "2-digit" });
    } catch (e) {
      try { out = d.toLocaleString(); } catch (e2) { return ""; }
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
    var whyId = cooling ? "crewwhycool" : pending ? "crewwhywait" : null;
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
      // Nothing for `pending`: the crew card directly above already says "Waiting on a
      // leader to let you in", and printing it again two lines down was the same sentence
      // twice on one screen. The shut buttons point at that line instead, so the reason is
      // still one `aria-describedby` away from every control it governs.
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
          var sub = [riders(c.members), policy]
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
  function bindList() {
    var list = document.getElementById("cj-list");
    if (!list) return;
    var rows = [].slice.call(list.querySelectorAll(".crewrow"));
    var more = document.getElementById("cj-more");
    var box = document.getElementById("cj-filter");
    var open = false;

    function paint() {
      var q = box ? box.value.trim().toLowerCase() : "";
      var shown = 0;
      rows.forEach(function (r, i) {
        var hit = !q || (r.dataset.name || "").indexOf(q) >= 0;
        // Typing searches the whole set; without a query the six nearest stand alone.
        r.hidden = !hit || (!q && !open && r.classList.contains("crewrest"));
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
          open = true;
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

    if (box) box.oninput = paint;
    if (more) more.onclick = function () {
      open = !open;
      more.setAttribute("aria-expanded", open ? "true" : "false");
      more.textContent = open ? t("crew.join.fewer")
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
    // The row may be one of the fifteen the list folds away, in which case the button exists
    // and cannot be pressed. Unfold first.
    var more = document.getElementById("cj-more");
    if (more && btn.closest("[hidden]")) more.click();
    btn.scrollIntoView({ block: "center" });
    if (btn.dataset.pol === "invite") {
      btn.click();                          // opens the code prompt beside the row
      var box = document.getElementById("crewask-in");
      if (box) { box.value = inv.code; box.focus(); box.select(); }
    }
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
                 function (code, ctl) { send({ invite_code: code }, ctl); }, b);
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
  function setHeat(on) {
    if (!map.getLayer("heat")) return;
    var full = (window.__HEAT__ && window.__HEAT__.opacity) || 0.62;
    // Not off, just faint. Territory answers "who holds this" and the heatmap answers "does
    // anybody actually ride here", and the second is useful context under the first as long
    // as it is quiet enough not to blur the edges that are the whole point.
    var ghost = CFG.heat_ghost != null ? CFG.heat_ghost : 0.30;
    var want = on ? full : full * ghost;
    try { map.setPaintProperty("heat", "heatmap-opacity", want); } catch (e) {}
  }

  // Re-rendering resets the panel's scroll, so an action that changes your standing left you
  // staring at the top of the board with no sign it worked. Whatever is new gets scrolled to.
  var revealNext = null;
  var pendingStatus = null;
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
      dockDot(r.body.pending ? r.body.pending.length : 0);
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

  function watchKnocks() {
    if (knockTimer) return;
    knockTimer = setInterval(function () {
      // Nothing to answer in a background tab, and nothing to ask for somebody who cannot
      // receive a knock in the first place.
      if (document.hidden) return;
      if (!ME || !ME.crew) return;
      if (ME.role !== "leader" && ME.role !== "officer") return;
      api("GET", "/api/v1/crews/me").then(function (r) {
        if (!r.ok || !r.body) return;
        var n = r.body.pending ? r.body.pending.length : 0;
        // The counts only. Re-rendering on a timer would pull the form out from under a
        // leader halfway through typing a crew name, which is a worse bug than the one this
        // fixes -- so the badge and the dot are updated in place and nothing else moves.
        dockDot(n);
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

  function dockDot(n) {
    var dot = document.getElementById("crewsdot");
    if (!dot) return;
    dot.textContent = n > 9 ? "9+" : String(n);
    dot.hidden = !n;
    var btn = dot.parentNode;
    if (btn && btn.setAttribute) {
      // The label is hidden at phone widths, so the count has to reach a screen reader
      // through the button's own name.
      var base = t("dock.crews");
      // The same sentence the hover gives, rather than a heading and a digit side by side:
      // "Crews - Waiting 2" was two labels touching, and said nothing about who was waiting.
      btn.setAttribute("aria-label", n ? base + " · " + knockText(n) : base);
      // A badge capped at "9+" is a number you cannot read; the title always has the real one.
      if (n) btn.setAttribute("title", knockText(n));
      else btn.removeAttribute("title");
    }
  }

  function reveal(sel) {
    revealNext = sel;
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
    visible = true;
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
      ME = me;
      // The pulse layer is built before this resolves, so without re-applying the filter it
      // always took the "every crew" branch and your own ground never stood out.
      scopePulse();
      var rank = res[1].ok ? res[1].body.crews || [] : [];
      if (res[1].ok) BOARD = rank;
      var all = res[2].ok ? res[2].body.crews || [] : [];
      MAXMEM = res[2].ok ? (res[2].body.max_members || 0) : 0;
      var board = '<div class="crewcard crewboard"><h3>' + t("crew.board") + "</h3>"
        // The definition of the metric used to live here, permanently, above the board it
        // defines. It is a manual entry and it is in the manual now; see `explainer()`.
        
        + firstRunNote()
        + (TERR && TERR.pending && !rank.length ? "" : rankingHTML(rank)) + "</div>";
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
      var h = me.paired ? own + board : signInHTML() + board;
      // The only sign-out button in the feature was emitted by `myCrewHTML`, which this
      // function calls on the `me.crew` branch alone -- so cooling off, removed, folded,
      // declined and no-ride-yet had no control of ANY kind on them. A reviewer pressed
      // Leave and found an empty `querySelectorAll` while the endpoint answered 200.
      if (me.paired && !me.crew) {
        h += '<div class="crewfoot"><button class="crewbtn ghost" id="cm-signout">'
          + t("crew.mine.signout") + "</button></div>";
      }
      panel.innerHTML = h;
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
      if (pendingStatus) { setStatus(pendingStatus); pendingStatus = null; }
      panel.querySelectorAll(".crewboard [data-i]").forEach(function (el) {
        var i = +el.dataset.i, r = rank[i];
        // `aria-label` REPLACES the element's own text, so naming the rank and the crew did
        // not merely leave out the 91 squares, the 2 patches and the +13 this week: it
        // suppressed them. A reader on a card headed "Biggest patch a crew holds in one
        // piece" heard sixteen names and not one number. `rowLabel` joins what is actually
        // in the row, which is what the target rows have always done.
        // No ordinal prefix: the row's first child IS the rank, so `rowLabel` already has
        // it and prefixing produced "2nd · 2ND · Polar Night Riders".
        pressable(el, r && rowLabel(el), function () {
          if (r) flyToCrew(r.slug);
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

  function openCrew(slug) {
    if (!visible) show();
    flyToCrew(slug);
  }

  function flyToCrew(slug) {
    if (!TERR) return;
    var idx = -1;
    TERR.crews.forEach(function (c, i) { if (c.slug === slug) idx = i; });
    if (idx < 0) return;
    var b = new maplibregl.LngLatBounds(), any = false;
    for (var i = 0; i < TERR.cells.length; i += 5) {
      if (TERR.cells[i] !== idx) continue;
      var x = TERR.cells[i + 1], y = TERR.cells[i + 2];
      b.extend([tileLon(x, TERR.z), tileLat(y, TERR.z)]);
      b.extend([tileLon(x + 1, TERR.z), tileLat(y + 1, TERR.z)]);
      any = true;
    }
    if (any) {
      try {
        map.fitBounds(b, { padding: { top: 90, bottom: 320, left: 50, right: 50 },
                           maxZoom: 11.5, duration: 1800, essential: true });
      } catch (e) {}
    }
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
    if (window.innerWidth <= 560) H.closePanel && H.closePanel();
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
        if (visible) buildLayers();
      }).catch(function () {});
  }

  /* ---------- wiring ---------- */

  window.EUCCrews = {
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
    panelClosed: function () { stopPairing(); },
    hide: function () {
      if (!visible) return;
      visible = false;
      stopPairing();
      clearLayers();          // rectangles belong to this mode and nowhere else
      setHeat(true);
    },
    reload: reloadTerritory
  };
})();
