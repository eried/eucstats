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
    return n <= 1 ? t("crew.tile.day1") : t("crew.tile.days", { n: n });
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

  var LAYERS = ["crew-fill", "crew-pattern", "crew-contested", "crew-edge",
                "crew-edge-glow", "crew-target-case", "crew-target-line",
                "crew-pulse-danger", "crew-pulse-fresh", "crew-target-hit",
                "crew-lose-line"];

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
    if (CALM || !any) return;         // nothing happening, or a reader who asked for stillness
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
  function scopePulse() {
    if (!map || !map.getLayer("crew-pulse-danger")) return;
    map.setFilter("crew-pulse-danger", (ME && ME.crew)
      ? ["all", ["==", ["get", "kind"], "danger"], ["==", ["get", "slug"], ME.crew.slug]]
      : ["==", ["get", "kind"], "danger"]);
  }

  function clearLayers() {
    map.off("zoom", onZoom);
    map.off("click", "crew-fill", onCellClick);
    map.off("mouseenter", "crew-fill", cursorPointer);
    map.off("mouseleave", "crew-fill", cursorDefault);
    map.off("mousemove", "crew-fill", onCellHover);
    map.off("mousemove", "crew-target-hit", onTargetHover);
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
          type: "Feature", properties: { kind: "fresh", c: crew.colour },
          geometry: { type: "MultiPolygon",
                      coordinates: fresh.map(function (t) { return tileRing(t[0], t[1], z); }) }
        });
      }
      edges.features.push({
        type: "Feature",
        properties: { c: crew.colour, i: idx },
        geometry: { type: "MultiLineString", coordinates: outline(cells, z) }
      });
      // one dashed ring around the ground under pressure, not a box per tile
      var pressed = cells.filter(function (t) {
        var b = t[2] || 0;
        return b === 1 || b === 2;        // a rival; fading ground is not under attack
      });
      if (pressed.length) {
        hot.features.push({
          type: "Feature",
          properties: { c: crew.colour, i: idx },
          geometry: { type: "MultiLineString", coordinates: outline(pressed, z) }
        });
      }
    });

    map.addSource("crew-cells", { type: "geojson", data: fills });
    map.addSource("crew-edges", { type: "geojson", data: edges });
    map.addSource("crew-hot", { type: "geojson", data: hot });
    map.addSource("crew-pulse", { type: "geojson", data: pulse });

    var op = (window.__CREWCFG__ && window.__CREWCFG__.opacity) || 0.55;
    map.addLayer({
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
    map.addLayer({
      id: "crew-pulse-fresh", type: "fill", source: "crew-pulse",
      filter: ["==", ["get", "kind"], "fresh"],
      paint: { "fill-color": ["get", "c"], "fill-opacity": 0, "fill-antialias": false,
               "fill-opacity-transition": { duration: 1600 } }
    });
    map.addLayer({
      id: "crew-fill", type: "fill", source: "crew-cells",
      paint: { "fill-color": ["get", "c"], "fill-opacity": 0,
               "fill-antialias": false,
               "fill-opacity-transition": { duration: 600 } }
    });
    map.addLayer({
      id: "crew-pattern", type: "fill", source: "crew-cells",
      filter: ["!=", ["get", "p"], "crewpat-solid"],
      paint: { "fill-pattern": ["get", "p"], "fill-opacity": 0,
               "fill-opacity-transition": { duration: 600 } }
    });
    // the glow sits under the hairline so a border reads at low zoom without being fat
    // Pressure needs a second channel. A shade on a dark map is something you notice
    // afterwards; a dashed edge is something you see.
    map.addLayer({
      id: "crew-contested", type: "line", source: "crew-hot",
      paint: { "line-color": "#ffffff",
               "line-dasharray": [2, 1.6],
               "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.4, 14, 2.4],
               "line-opacity": 0,
               "line-opacity-transition": { duration: 600 } }
    });
    map.addLayer({
      id: "crew-edge-glow", type: "line", source: "crew-edges",
      paint: { "line-color": ["get", "c"], "line-width": 7, "line-blur": 7,
               "line-opacity": 0 , "line-opacity-transition": { duration: 600 } }
    });
    map.addLayer({
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
      map.setPaintProperty("crew-fill", "fill-opacity",
        ["match", ["get", "band"],
         1, op * BAND_OP[1], 2, op * BAND_OP[2], 3, op * BAND_OP[3], 4, op, op]);
      map.setPaintProperty("crew-pattern", "fill-opacity",
        ["match", ["get", "band"],
         1, pat * BAND_OP[1], 2, pat * BAND_OP[2], 3, pat * BAND_OP[3] * 0.9, 4, pat, pat]);
      map.setPaintProperty("crew-edge", "line-opacity", 0.95);
      map.setPaintProperty("crew-edge-glow", "line-opacity", 0.35);
      map.setPaintProperty("crew-contested", "line-opacity", 0.8);
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
      el.onclick = function (ev) { ev.stopPropagation(); openCrew(crew.slug); };
      var m = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([lon, lat]).addTo(map);
      markers.push(m);
    });
    sizeEmblems();
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
      px = Math.max(px, floor);
      el.style.width = el.style.height = px + "px";
      el.style.opacity = px < 16 ? 0 : 1;
      el.classList.toggle("tiny", px < 64);
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
        : SHOW_NUMBERS ? t("crew.tile.clear", { v: fmtKm(km) }) : margin(km, y)
    ];
  }

  /* ---------- resting on a square ----------
     Slow on purpose. The delay is the whole design: anything quicker turns into a label that
     chases the pointer across the map while you are trying to look at the shapes. */
  var HOVER_MS = 650;
  var hoverTimer = null, hoverTip = null, hoverKey = "";

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
        + esc(w[1]) + "</span>"
        + (info[2] ? "<span><em>" + esc(t("crew.tile.fresh")) + "</em></span>" : "");
      map.getCanvasContainer().appendChild(el);
      el.style.left = px.x + "px";
      el.style.top = px.y + "px";
      // it is nowrap, so max-width cannot save it: flip to the other side near the edge
      if (px.x + el.offsetWidth + 30 > window.innerWidth) el.classList.add("left");
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
      var el = document.createElement("div");
      var losing = row.band !== undefined;
      el.className = "crewtip " + (losing ? "bl" : "bt");
      el.innerHTML = "<b>" + esc(losing
            ? (row.band === 3 ? fadesIn(Math.round(row.need * 10))
               : t("crew.lose.gap", { v: effort(row.need, row.y) }))
            : row.blocked ? t("crew.targets.blocked") : effort(row.need, row.y)) + "</b>"
        + "<span>" + esc(losing
            ? t(row.band === 3 ? "crew.lose.cold"
                : row.band === 2 ? "crew.lose.now" : "crew.lose.soon")
            : row.held_by
            ? t("crew.targets.taken", { name: row.held_name || "" })
            : t("crew.tile.free")) + "</span>"
        + (row.kills ? "<span><em>" + esc(t("crew.targets.kills")) + "</em></span>" : "")
        + (row.first ? "<span><em>" + esc(t("crew.targets.first")) + "</em></span>" : "");
      map.getCanvasContainer().appendChild(el);
      el.style.left = px.x + "px";
      el.style.top = px.y + "px";
      if (px.x + el.offsetWidth + 30 > window.innerWidth) el.classList.add("left");
      if (px.y < el.offsetHeight / 2 + 8) el.classList.add("below");
      hoverTip = el;
    }, HOVER_MS);
  }

  function onCellClick(e) {
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
    var state = words[0], detail = words[1];
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
        + "</span><span>" + esc(detail) + '</span><span class="crewpop-since"></span>'
        + "</div></div>")
      .addTo(map);
  }

  // Every visible string goes through the page's translator. They live in web/i18n.py EN,
  // which is the source the translation workflow regenerates the other locales from.
  function t(key, vars) {
    var out = (H.t ? H.t(key, vars) : key);
    return out;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------- the panel ---------- */

  function api(method, path, body) {
    var opt = { method: method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    return fetch(path, opt).catch(function () {
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
  }

  function swatch(colour, pattern, size) {
    var sz = size || 18;
    return '<span class="crewsw" style="width:' + sz + "px;height:" + sz + "px;background:"
      + esc(colour) + '" data-p="' + esc(pattern) + '"></span>';
  }

  function explainer() {
    return '<details class="crewhow"><summary>' + t("crew.how.h") + "</summary>"
      + ["crew.how.1", "crew.how.2", "crew.how.3", "crew.how.7", "crew.how.4", "crew.how.5",
         "crew.how.6"]
        .map(function (k) { return "<p>" + t(k, { n: SEED, d: WINDOW_DAYS }) + "</p>"; }).join("")
      // The five shades belong here rather than under the board. It is a key, and a key is
      // something you look up once, not a row of swatches on screen every time you visit.
      + legendHTML()
      + "</details>";
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

  function rankingHTML(rows) {
    if (!rows || !rows.length) {
      return '<div class="empty">' + t("crew.empty") + "</div>";
    }
    FRESH = freshByCrew();
    if (!H.podList) return plainRank(rows);
    return H.podList(rows, {
      iconFn: function (e) { return '<img class="crewpodemb" alt="" src="' + e.emblem + '"/>'; },
      // the swatch is the crew's identity on the map, so it belongs beside every name
      label: function (e) { return swatch(e.colour, e.pattern, 14) + " " + esc(e.name); },
      // squares, because that is what the board is sorted on and a square is the same amount
      // of riding everywhere. The area sits underneath, where it informs without ranking.
      val: function (e) { return tiles(e.best_tiles || e.tiles); },
      sub: function (e) {
        var gained = FRESH[e.slug] || 0;
        // No km2 here. The board ranks on squares, and area beside it inverted the ranking
        // two rows apart: 29 squares at 21 km2 above 20 squares at 120 km2. Area lives on the
        // crew's own card and in the popup, where nothing is being compared.
        return '<span class="crewarea">'
          + (e.regions > 1 ? plural(null, "crew.patches.few", "crew.patches", e.regions) : "") + "</span>"
          + (gained ? ' <span class="crewgain">'
             + esc(t("crew.board.gained", { n: gained })) + "</span>" : "");
      },
      click: true
    });
  }

  // Every mark the map draws gets a chip, and nothing else does. This list fell out of step
  // with the map four times, every time because a state was added to one and not the other,
  // and once because a chip described a mark the map has never drawn. The styles live in one
  // block in crews.css now rather than in a rule and a later override of that rule, which is
  // what made it so easy to add to the wrong half.
  function band(n, key) {
    return '<span class="b' + n + '"><i style="opacity:' + chipOp(n) + '"></i>'
      + t(key) + "</span>";
  }

  function legendHTML() {
    return '<div class="crewlegend">'
      + band(0, "crew.tile.safe") + band(1, "crew.tile.pushed")
      + band(2, "crew.tile.slipping") + band(3, "crew.tile.fading")
      + '<span class="bt"><i></i>' + t("crew.targets.h") + "</span>"
      + '<span class="bl"><i></i>' + t("crew.lose.h") + "</span>"
      + '<span class="bf"><i></i>' + t("crew.legend.fresh") + "</span>"
      + "</div>";
  }

  function plainRank(rows) {
    return '<table class="crewrank"><tbody>' + rows.map(function (r, i) {
      return '<tr class="sel" data-i="' + i + '"><td class=rk>' + (i + 1) + "</td>"
        + '<td><span class="celln">' + swatch(r.colour, r.pattern)
        + "<span>" + esc(r.name) + "</span></span></td>"
        + "<td class=val>" + tiles(r.best_tiles || r.tiles) + "</td>"
        + '<td class="val sub">' + tiles(r.tiles) + "</td></tr>";
    }).join("") + "</tbody></table>";
  }

  // One, a few, many. English and most of the rest need only the first and last, and Russian,
  // Ukrainian and Polish need the middle one for 2, 3 and 4, which in this feature is nearly
  // every number anybody sees: a crew has two or three patches, not twenty-seven.
  function plural(one, few, many, n) {
    if (n === 1 && one) return t(one, { n: n });
    var d = n % 10, h = n % 100;
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
    var u = H.mph && H.mph() ? " mi" : " km";
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

  function effort(km, y) {
    if (SHOW_NUMBERS) return fmtKm(km);
    var r = km / (floorKm(y) || 0.5);
    return t("crew.take." + (r <= 0.4 ? 1 : r <= 1 ? 2 : r <= 2.5 ? 3 : 4));
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
      return Math.round(n * MI2_PER_KM2).toLocaleString() + " mi²";
    }
    return Math.round(n).toLocaleString() + " km²";
  }

  var ROLEIC = {
    leader: '<span class="crewrole lead" title="Leader">★</span>',
    officer: '<span class="crewrole off" title="Officer">◆</span>',
    member: "",
    past: '<span class="crewrole past" title="No longer in the crew">·</span>'
  };

  // The squares this crew could take next. Until this existed the mode could say a tile was
  // contested but never where to go, which is the one thing a map mode about choosing routes
  // has to do. A tile that joins two patches leads, because the board ranks on the biggest
  // single patch and welding two together beats widening either.
  // "same as the row above". Dimming the repeated words to the point where they read as a
  // repeat put them under three and a half to one against this background, which is below the
  // floor for body text, and there is no opacity that is both.
  var DITTO = "\u3003";

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
      return head + '<p class=hint>' + t("crew.targets.none", { n: SEED }) + "</p></div>";
    }
    var seenKm = {}, seenWho = {};
    var body = TARGETS.map(function (x, i) {
          var tag = x.first ? '<span class="crewtag first">' + t("crew.targets.first") + "</span>"
            : x.kills ? '<span class="crewtag kills">'
              + t("crew.targets.kills", { n: x.lost || 0 }) + "</span>"
            : x.joins ? '<span class="crewtag joins">' + t("crew.targets.joins") + "</span>"
            : x.blocked ? '<span class="crewtag done">' + t("crew.targets.blocked") + "</span>"
            : "";
          var who = x.held_by
            ? t("crew.targets.taken", { name: esc(x.held_name || "") })
            : t("crew.tile.free");
          // Nothing goes in the number column on a square whose shortfall is zero: riding it
          // again does nothing, and a word there wore the styling meant for a distance.
          var km = x.blocked ? "" : effort(x.need, x.y);
          // only the holder: since the rows are deduplicated on effort, bearing and holder
          // together, two rows can share an effort word and still be different places, and a
          // ditto there would read as a mistake.
          var rk = "";
          var rw = seenWho[who] ? " rpt" : "";
          seenWho[who] = 1;
          return '<div class="crewtrow sel' + (x.blocked ? " done" : "") + '" data-t="' + i + '">'
            + '<span class="crewtkm' + rk + '">' + (rk ? DITTO : km) + "</span>"
            + '<span class="crewtdir">' + bearing(x.dir) + "</span>"
            + '<span class="crewtwho' + rw + '">' + (rw ? DITTO : who) + "</span>"
            + tag + "</div>";
        }).join("");
    return head + '<p class=hint>'
      + t(nothing ? "crew.targets.p0" : "crew.targets.p", { n: SEED }) + "</p>"
      + body + "</div>";
  }

  // The widest phrase decides the column, because the phrase is prose and the locales differ
  // by a factor of two. ch is close enough for a proportional face and needs no measuring.
  function widest(rows) {
    var n = 7;
    (rows || []).forEach(function (x) {
      var w = (x.blocked ? "" : effort(x.need, x.y)).length;
      if (w > n) n = w;
    });
    return Math.min(n + 1, 22);
  }

  // The other half of the game. Every band and every shortfall is already in TERR.cells, so
  // this costs one pass over an array the browser has had the whole time. Without it the mode
  // is offence only: the crew at the top of the board was being out-ridden in ten squares and
  // the panel was telling them to go paint empty fields.
  function loseHTML(slug) {
    if (!TERR || !TERR.crews) return "";
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
      rows.push({ x: TERR.cells[i + 1], y: TERR.cells[i + 2], band: band,
                  need: TERR.cells[i + 4] / 10 });
    }
    LOSING = [];
    if (!rows.length || !all.length) return "";
    // about to flip first, then being ridden, then going cold on its own
    var urgency = { 2: 0, 1: 1, 3: 2 };
    rows.sort(function (a, b) {
      return (urgency[a.band] - urgency[b.band]) || (a.need - b.need);
    });
    var hidden = Math.max(0, rows.length - 5);
    LOSING = rows.slice(0, 5);
    // bearings from the middle of everything the crew holds, not from the middle of the five
    // rows: with one row those are the same point and the direction comes out empty
    var cx = 0, cy = 0;
    all.forEach(function (x) { cx += x.x; cy += x.y; });
    cx /= all.length; cy /= all.length;
    // Same three columns as "where to ride next", so the two cards read as a pair: how hard,
    // which way, what about it.
    var cols = 7;
    LOSING.forEach(function (x) {
      var w = (x.band === 3 ? fadesIn(Math.round(x.need * 10))
               : t("crew.lose.gap", { v: effort(x.need, x.y) })).length;
      if (w > cols) cols = w;
    });
    var seenGap = {}, seenState = {};
    return '<div style="--kmw:' + Math.min(cols + 1, 30) + 'ch" class="crewtargets crewlose'
      + (SHOW_NUMBERS ? " nums" : "") + '"><h4>' + t("crew.lose.h") + "</h4>"
      // eight crews in fourteen have nothing but fading ground, and telling them a rival is
      // closing in on it is simply untrue
      + '<p class=hint>'
      + t(LOSING.every(function (x) { return x.band === 3; }) ? "crew.lose.p3" : "crew.lose.p")
      + "</p>"
      + LOSING.map(function (x, i) {
          // their gap, not your effort, and the third column carries urgency rather than
          // restating the heading. Band 3 has no rival, so its number is days left.
          var gap = x.band === 3 ? fadesIn(Math.round(x.need * 10))
                                 : t("crew.lose.gap", { v: effort(x.need, x.y) });
          var state = t(x.band === 3 ? "crew.lose.cold"
                        : x.band === 2 ? "crew.lose.now" : "crew.lose.soon");
          var rg = seenGap[gap] ? " rpt" : "", rs = seenState[state] ? " rpt" : "";
          seenGap[gap] = 1; seenState[state] = 1;
          return '<div class="crewtrow sel" data-l="' + i + '">'
            + '<span class="crewtkm' + rg + '">' + (rg ? DITTO : gap) + "</span>"
            + '<span class="crewtdir">' + bearing(compass(x.x - cx, x.y - cy)) + "</span>"
            + '<span class="crewtwho' + rs + '">' + (rs ? DITTO : state) + "</span></div>";
        }).join("")
      // 85 squares are losable across the world and 45 were shown, with nothing saying so
      + (hidden ? '<p class="hint crewmore">' + t("crew.lose.more", { n: hidden }) + "</p>" : "")
      + "</div>";
  }

  // Who actually rode for the crew, over the same window the territory is measured on, so the
  // list explains the shape on the map rather than ranking loyalty.
  function contributorsHTML(rows) {
    if (!rows || !rows.length) return "";
    var top = rows[0].km || 1;
    return '<div class="crewcontrib"><h4>' + t("crew.mine.who") + "</h4>" + rows.map(function (c) {
      var pct = Math.max(3, Math.round((c.km / top) * 100));
      return '<div class="crewcrow">'
        + (H.av ? H.av(c.id, c.has_avatar, c) : "")
        + (H.cc && c.flag ? H.cc(c.flag) : "")
        + '<span class="crewcname">' + esc(c.name) + (ROLEIC[c.role] || "") + "</span>"
        + '<span class="crewcbar"><i style="width:' + pct + '%"></i></span>'
        + '<span class="crewckm">' + fmtKm(c.km) + "</span></div>";
    }).join("") + "</div>";
  }

  /* ---------- sign-in ---------- */

  function signInHTML() {
    // Same phone as the browser? Then there is nothing to point a camera at — you cannot scan
    // your own screen. The deep link opens the app directly and it comes straight back, so
    // the one awkward case in the whole flow is a tap. The QR itself is the same link, so on
    // a phone the image is tappable too.
    return '<div class="crewcard crewsign">'
      + "<h3>" + t("crew.signin.h") + "</h3>"
      + '<p class=hint>' + t("crew.signin.p") + "</p>"
      + '<a class="crewqr" id="crewqr" href="#"><div class="spin"></div></a>'
      + '<div class="crewcode" id="crewcode">······</div>'
      + '<p class=hint id="crewcodehint">' + t("crew.signin.scan") + "</p>"
      + '<p class="hint crewnoapp"><a href="#" id="crewgetapp">'
      + t("crew.signin.noapp") + "</a></p>"
      + '<p class="hint crewsame">' + t("crew.signin.same") + "</p>"
      + '<a class="crewbtn crewopen" id="crewopen" href="#">' + t("crew.signin.open") + "</a>"
      + "</div>";
  }

  var pairRolls = 0;
  var PAIR_MAX_ROLLS = 5;        // about fifteen minutes of waiting, then it asks

  // Both ways a code can die end up here. The error path used to stop the timer and return
  // without rendering anything, so a dead six-character code sat on screen looking live with
  // nothing polling, no message and no link, and the only way out was closing the panel.
  function offerRetry() {
    stopPairing();
    var code = document.getElementById("crewcode");
    if (code) code.textContent = "······";
    var el = document.getElementById("crewcodehint");
    if (!el) return;
    el.innerHTML = '<a href="#" id="crewagain">' + t("crew.signin.again") + "</a>";
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
        qr.innerHTML = '<img alt="Crew pass code" src="data:image/png;base64,'
          + r.body.qr + '"/>';
        qr.href = deep;
      }
      if (open) open.href = deep;
      if (code) code.textContent = r.body.code;
      var get = document.getElementById("crewgetapp");
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
    if (!near || !near.parentNode) return top;
    var slot = document.createElement("div");
    near.parentNode.insertBefore(slot, near.nextSibling);
    return slot;
  }

  function ask(message, confirmLabel, ok, near) {
    var host = askHost(near);
    if (!host) { if (window.confirm(message)) ok(); return; }
    function done() { if (host.id === "crewstatus") host.innerHTML = ""; else host.remove(); }
    // The quiet button is the one that acts and the bright one is the way out. Leaving costs
    // a crew and a cooldown, and a stray tap should not be the easy path.
    host.innerHTML = '<div class="crewask"><p>' + esc(message) + "</p>"
      + '<button class="crewbtn mini ghost" id="crewask-y">' + esc(confirmLabel) + "</button>"
      + '<button class="crewbtn mini" id="crewask-n">' + t("crew.cancel") + "</button>"
      + "</div>";
    host.scrollIntoView({ block: "nearest", behavior: "smooth" });
    host.querySelector("#crewask-n").onclick = done;
    host.querySelector("#crewask-y").onclick = function () { done(); ok(); };
  }

  // An in-panel prompt, same reasoning.
  function askFor(message, placeholder, ok, near) {
    var host = askHost(near);
    if (!host) { var v = window.prompt(message); if (v) ok(v); return; }
    function done() { if (host.id === "crewstatus") host.innerHTML = ""; else host.remove(); }
    host.innerHTML = '<div class="crewask"><p>' + esc(message) + "</p>"
      + '<input id="crewask-in" placeholder="' + esc(placeholder) + '" maxlength="16">'
      + '<button class="crewbtn mini" id="crewask-y">' + t("crew.join.btn") + "</button>"
      + '<button class="crewbtn mini ghost" id="crewask-n">' + t("crew.cancel") + "</button>"
      + "</div>";
    host.scrollIntoView({ block: "nearest", behavior: "smooth" });
    var input = host.querySelector("#crewask-in");
    input.focus();
    host.querySelector("#crewask-n").onclick = done;
    function go() {
      var v = input.value.trim();
      done();
      if (v) ok(v);
    }
    host.querySelector("#crewask-y").onclick = go;
    input.onkeydown = function (e) { if (e.key === "Enter") go(); };
  }

  // Every failure used to arrive as the server's own string: a rider who tried to join a full
  // crew read "crew_full" in a pink box, and the fourteen locales all answered in English.
  var ERRS = {
    crew_full: "crew.e.full", creation_closed: "crew.e.closed", forbidden: "crew.e.forbidden",
    not_leader: "crew.e.forbidden", not_paired: "crew.e.pass",
    crews_disabled: "crew.e.off",
    bad_invite: "crew.e.invite", bad_name: "crew.e.name", name_taken: "crew.e.taken",
    already_in_crew: "crew.e.increw", no_trips: "crew.e.notrips", cooldown: "crew.e.cooldown",
    bad_identity: "crew.e.identity", identity_taken: "crew.e.identity",
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
    return k ? t(k) : t("crew.err");
  }

  var statusTimer;

  function setStatus(msg, bad) {
    var el = document.getElementById("crewstatus");
    if (!el) return;
    el.innerHTML = msg ? '<div class="crewmsg' + (bad ? " bad" : "") + '">'
      + esc(msg) + "</div>" : "";
    // The success path got scrolled into view and the failure path did not, so an error from
    // deep inside the crew card painted at the top of a scrolled panel where nobody saw it.
    clearTimeout(statusTimer);
    if (!msg) return;
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    // A success banner is a toast, not furniture. "You're in" was still the loudest thing on
    // the panel long after it stopped being news.
    if (!bad) statusTimer = setTimeout(function () { setStatus(""); }, 5000);
  }

  /* ---------- create / manage ---------- */

  function createHTML(ident) {
    var cols = (window.__CREWCFG__ && window.__CREWCFG__.palette) || [];
    // Colours are swatches, not a dropdown of hex codes. Nobody picks a crew identity by
    // reading "#000075", and the thing being chosen is the thing you will see on the map, so
    // the picker shows it with the pattern already on it.
    var colourGrid = '<div class="crewpick" id="cf-colours">' + cols.map(function (c) {
      return '<button type="button" class="crewpickc' + (c === ident.colour ? " on" : "")
        + '" data-c="' + c + '" style="background:' + c + '" title="' + c + '"></button>';
    }).join("") + "</div>";
    var patternGrid = '<div class="crewpick" id="cf-patterns">' + PATTERNS.map(function (pt) {
      return '<button type="button" class="crewpickp' + (pt === ident.pattern ? " on" : "")
        + '" data-p="' + pt + '" title="' + pt + '">'
        + '<span class="crewsw" data-p="' + pt + '" style="background:' + ident.colour
        + '"></span></button>';
    }).join("") + "</div>";
    return '<div class="crewcard">'
      + "<h3>" + t("crew.new.h") + "</h3>"
      + '<p class=hint>' + t("crew.new.p") + "</p>"
      + "<label>" + t("crew.new.name") + '<input id="cf-name" maxlength="28" placeholder="Nordlys Collective"></label>'
      + "<label>" + t("crew.new.desc") + '<input id="cf-desc" maxlength="280" placeholder="Oslo, mostly after dark."></label>'
      + '<div class="crewidentrow">'
      + '<div class="crewpreview">' + swatch(ident.colour, ident.pattern, 62) + "</div>"
      + "<div class=crewpickwrap><div class=crewidentl>" + t("crew.new.colours") + "</div>"
      + colourGrid + patternGrid + "</div></div>"
      + '<input type="hidden" id="cf-colour" value="' + ident.colour + '">'
      + '<input type="hidden" id="cf-pattern" value="' + ident.pattern + '">'
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

    document.querySelectorAll("#cf-colours .crewpickc").forEach(function (b) {
      b.onclick = function () {
        hidC.value = b.dataset.c;
        document.querySelectorAll("#cf-colours .crewpickc").forEach(function (o) {
          o.classList.toggle("on", o === b);
        });
        sync();
      };
    });
    document.querySelectorAll("#cf-patterns .crewpickp").forEach(function (b) {
      b.onclick = function () {
        hidP.value = b.dataset.p;
        document.querySelectorAll("#cf-patterns .crewpickp").forEach(function (o) {
          o.classList.toggle("on", o === b);
        });
        sync();
      };
    });
    sync();

    var go = document.getElementById("cf-go");
    if (go) go.onclick = function () {
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
        else setStatus(errMsg(r.err), true);
      });
    };
  }

  function myCrewHTML(me) {
    var c = me.crew, lead = me.role === "leader" || me.role === "officer";
    var h = '<div class="crewcard crewmine">'
      + '<div class="crewhead">'
      + '<img class="crewlogo" src="' + c.emblem + '" alt=""/>'
      + "<div><h3>" + esc(c.name) + "</h3>"
      + '<div class="crewmeta">' + swatch(c.colour, c.pattern) + " "
      + riders(c.members) + " · "
      + t(me.role === "leader" ? "crew.mine.youare"
          : me.role === "officer" ? "crew.mine.youofficer" : "crew.mine.youmember")
      + "</div></div></div>";
    if (me.status === "pending") {
      h += '<div class="crewmsg">' + t("crew.join.pending") + "</div>";
    }
    if (c.description) h += "<p>" + esc(c.description) + "</p>";
    h += '<div class="crewterr" id="crewterr"><div class=spin></div></div>';
    if (c.invite_code) {
      h += '<p class=hint>'
        + t(c.join_policy === "invite" ? "crew.mine.invite" : "crew.mine.invite2")
        + ': <code>' + esc(c.invite_code) + "</code></p>";
    }
    // Leaving is blocked for a leader with members until somebody else can run the crew, and
    // there was no control anywhere to make that somebody. The endpoint existed; the button
    // did not, so a two-person crew's leader was stuck for good.
    if (me.roster && me.roster.length > 1 && me.role === "leader") {
      h += '<div class="crewpend"><h4>' + t("crew.roles.h") + "</h4>"
        + me.roster.map(function (x) {
            var mark = x.role === "leader" ? " " + ROLEIC.leader
              : x.role === "officer" ? " " + ROLEIC.officer : "";
            // the leader is listed, because a section called "The crew" that leaves them out
            // is a section header telling a lie
            var btn = x.role === "leader" ? ""
              : '<button class="crewbtn mini ghost" data-role="'
                + (x.role === "officer" ? "member" : "officer") + '" data-sid="'
                + esc(x.store_id || "") + '">'
                + t(x.role === "officer" ? "crew.roles.demote" : "crew.roles.promote")
                + "</button>";
            return '<div class="crewpendr"><span>' + esc(x.name) + mark + "</span>" + btn
              + "</div>";
          }).join("") + "</div>";
    }
    if (me.pending && me.pending.length) {
      h += '<div class="crewpend"><h4>' + t("crew.pending.h") + "</h4>"
        + me.pending.map(function (p) {
            return '<div class="crewpendr"><span>' + esc(p.name) + "</span>"
              + '<button class="crewbtn mini" data-ok="' + esc(p.store_id) + '">' + t("crew.accept") + "</button>"
              + '<button class="crewbtn mini ghost" data-no="' + esc(p.store_id) + '">' + t("crew.decline") + "</button>"
              + "</div>"; }).join("")
        + "</div>";
    }
    if (lead) {
      h += '<details class="crewedit"><summary>' + t("crew.mine.settings") + "</summary>"
        + "<label>" + t("crew.new.name") + '<input id="ce-name" maxlength="28" value="'
        + esc(c.name) + '"></label>'
        + "<label>" + t("crew.new.desc") + '<input id="ce-desc" maxlength="280" value="'
        + esc(c.description || "") + '"></label>'
        + "<label>" + t("crew.new.who") + '<select id="ce-policy">'
        + ["approval", "open", "invite"].map(function (p) {
            return '<option value="' + p + '"' + (p === c.join_policy ? " selected" : "")
              + ">" + t("crew.new." + p) + "</option>"; }).join("")
        + "</select></label>"
        + '<label class="crewfile">' + t("crew.mine.emblem")
        + '<input type="file" id="ce-logo" accept="image/*"></label>'
        + '<p class=hint>' + t("crew.mine.emblemp") + "</p>" 
        + '<button class="crewbtn" id="ce-save">' + t("crew.mine.save") + "</button>" 
        + '<button class="crewbtn ghost" id="ce-clearlogo">' + t("crew.mine.generated") + "</button>"
        + "</details>";
    }
    h += '<div class="crewacts">'
      + '<button class="crewbtn ghost" id="cm-leave">'
      // pulling a request you never got an answer to is not leaving a crew, and it does not
      // cost a cooldown any more either
      + t(me.status === "pending" ? "crew.mine.cancel" : "crew.mine.leave") + "</button>"
      // disband and claim-leadership were endpoints with no buttons. A solo leader who walks
      // out used to leave a crew with no riders on the board that nobody could clear up.
      + (me.role === "leader"
         ? '<button class="crewbtn ghost" id="cm-disband">' + t("crew.mine.disband") + "</button>"
         : "")
      + (me.role !== "leader" && me.leader_stale
         ? '<button class="crewbtn ghost" id="cm-claim">' + t("crew.mine.claim") + "</button>"
         : "")
      + '<button class="crewbtn ghost" id="cm-signout">' + t("crew.mine.signout") + "</button>"
      + "</div></div>";
    return h;
  }

  function bindMine(me) {
    var c = me.crew;
    document.querySelectorAll("[data-ok]").forEach(function (b) {
      b.onclick = function () {
        api("POST", "/api/v1/crews/" + c.slug + "/decide",
            { store_id: b.dataset.ok, accept: true }).then(show);
      };
    });
    document.querySelectorAll("[data-no]").forEach(function (b) {
      b.onclick = function () {
        api("POST", "/api/v1/crews/" + c.slug + "/decide",
            { store_id: b.dataset.no, accept: false }).then(show);
      };
    });
    document.querySelectorAll("[data-role]").forEach(function (b) {
      b.onclick = function () {
        api("POST", "/api/v1/crews/" + c.slug + "/role",
            { store_id: b.dataset.sid, role: b.dataset.role }).then(function (r) {
          if (r.ok) { reveal(".crewmine-wrap"); show(); }
          else setStatus(errMsg(r.err), true);
        });
      };
    });
    var leave = document.getElementById("cm-leave");
    if (leave) leave.onclick = function () {
      var pending = me.status === "pending";
      ask(pending ? t("crew.mine.cancelq", { name: c.name }) : leaveQuestion(c.name),
          t(pending ? "crew.mine.cancel" : "crew.mine.leave"), function () {
        api("POST", "/api/v1/crews/leave", {}).then(function (r) {
          if (r.ok) { reveal(".crewboard"); show(); reloadTerritory(); }
          else setStatus(errMsg(r.err), true);
        });
      }, leave);
    };
    var dis = document.getElementById("cm-disband");
    if (dis) dis.onclick = function () {
      ask(t("crew.mine.disbandq", { name: c.name }), t("crew.mine.disband"), function () {
        api("POST", "/api/v1/crews/" + c.slug + "/disband", {}).then(function (r) {
          if (r.ok) { reveal(".crewboard"); show(); reloadTerritory(); }
          else setStatus(errMsg(r.err), true);
        });
      }, dis);
    };
    var claim = document.getElementById("cm-claim");
    if (claim) claim.onclick = function () {
      ask(t("crew.mine.claimq"), t("crew.mine.claim"), function () {
        api("POST", "/api/v1/crews/" + c.slug + "/claim", {}).then(function (r) {
          if (r.ok) { reveal(".crewmine-wrap"); show(); }
          else setStatus(errMsg(r.err), true);
        });
      }, claim);
    };
    var so = document.getElementById("cm-signout");
    if (so) so.onclick = function () {
      ask(t("crew.mine.signoutq"), t("crew.mine.signout"), function () {
        api("POST", "/api/v1/crews/signout", {}).then(function () { ME = null; show(); });
      }, so);
    };
    var save = document.getElementById("ce-save");
    if (save) save.onclick = function () {
      save.disabled = true;
      api("POST", "/api/v1/crews/" + c.slug + "/edit", {
        name: document.getElementById("ce-name").value,
        description: document.getElementById("ce-desc").value,
        join_policy: document.getElementById("ce-policy").value
      }).then(function (r) {
        save.disabled = false;
        if (r.ok) { show(); reloadTerritory(); }
        else setStatus(errMsg(r.err), true);
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
            .then(function (b) { setStatus(errMsg({ detail: b && b.detail }), true); });
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
      el.innerHTML = '<div class="crewbig">' + tiles(terr.best_tiles || terr.tiles || 0)
        + " <span>" + t("crew.mine.ao") + "</span></div>"
        + '<div class="crewsub">' + fmtKm2(terr.best_km2)
        + (terr.regions > 1 ? " · " + plural(null, "crew.patches.few", "crew.patches", terr.regions) : "")
        + (terr.tiles && terr.tiles !== (terr.best_tiles || terr.tiles)
            ? " · " + t("crew.inall", { v: tiles(terr.tiles) }) : "")
        + (terr.tiles ? "" : " · " + t("crew.mine.start", { n: SEED })) + "</div>"
        + (me.status === "pending" ? "" : targetsHTML(r.body.targets) + loseHTML(c.slug))
        + contributorsHTML(r.body.contributors);
      el.querySelectorAll("[data-t]").forEach(function (row) {
        row.onclick = function () { flyToTile(TARGETS[+row.dataset.t], +row.dataset.t); };
      });
      el.querySelectorAll("[data-l]").forEach(function (row) {
        // no second argument: ground you are losing is already breathing on the map, and
        // redrawing the target rings here only cleared whichever one was marked
        row.onclick = function () { flyToTile(LOSING[+row.dataset.l]); };
      });
      showTargets(TARGETS);
    });
  }

  function joinHTML(crews, me) {
    if (me.cooldown_until) {
      return '<div class="crewcard"><h3>' + t("crew.join.wait.h") + "</h3>"
        + '<div class="crewmsg">'
        + t("crew.join.wait.p", { n: days(daysUntil(me.cooldown_until)) })
        + "</div></div>";
    }
    if (!crews.length) return "";
    return '<div class="crewcard"><h3>' + t("crew.join.h") + '</h3><div class="crewlist">'
      + crews.map(function (c) {
          var open = c.join_policy === "open";
          var label = open ? t("crew.join.btn")
            : c.join_policy === "invite" ? t("crew.join.code") : t("crew.join.ask");
          var policy = t("crew.policy." + c.join_policy);
          // what the crew holds and what it says about itself, so the choice is not a
          // blind name-pick that costs a cooldown if it is wrong
          var sub = riders(c.members) + " · " + policy
            + (c.km2 ? " · " + fmtKm2(c.km2) : "");
          return '<div class="crewrow">' + swatch(c.colour, c.pattern, 26)
            + '<div class="crewrown"><b>' + esc(c.name) + "</b><span>" + sub + "</span>"
            + (c.description ? '<span class="crewmeta2">' + esc(c.description) + "</span>" : "")
            + "</div>"
            + '<button class="crewbtn mini' + (open ? "" : " ghost") + '" data-join="'
            + esc(c.slug) + '" data-pol="' + esc(c.join_policy) + '" data-name="'
            + esc(c.name) + '">' + label + "</button></div>";
        }).join("")
      + "</div></div>";
  }

  function bindJoin() {
    document.querySelectorAll("[data-join]").forEach(function (b) {
      b.onclick = function () {
        function send(body) {
          api("POST", "/api/v1/crews/" + b.dataset.join + "/join", body).then(function (r) {
            if (r.ok) { reveal(".crewmine-wrap"); show(); reloadTerritory(); }
            else setStatus(errMsg(r.err), true);
          });
        }
        if (b.dataset.pol === "invite") {
          // beside the row, and naming the crew: the prompt used to open at the top of the
          // panel, so by the time you read it you could no longer see which crew you tapped
          askFor(t("crew.join.codeask", { name: b.dataset.name || "" }), "ABC12345",
                 function (code) { send({ invite_code: code }); }, b);
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
    var ghost = CFG.heat_ghost != null ? CFG.heat_ghost : 0.14;
    var want = on ? full : full * ghost;
    try { map.setPaintProperty("heat", "heatmap-opacity", want); } catch (e) {}
  }

  // Re-rendering resets the panel's scroll, so an action that changes your standing left you
  // staring at the top of the board with no sign it worked. Whatever is new gets scrolled to.
  var revealNext = null;
  var pendingStatus = null;

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
    visible = true;
    H.setPanel("crews", (H.t ? H.t("title.crews") : "Crews & Territory"),
      '<div id="crewstatus"></div><div id="crewpanel"><div class="spin"></div></div>');
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
      api("GET", "/api/v1/crews?limit=60")
    ]).then(function (res) {
      var me = res[0].ok ? res[0].body : { paired: false };
      ME = me;
      // The pulse layer is built before this resolves, so without re-applying the filter it
      // always took the "every crew" branch and your own ground never stood out.
      scopePulse();
      var rank = res[1].ok ? res[1].body.crews || [] : [];
      var all = res[2].ok ? res[2].body.crews || [] : [];
      // Standings first and always: the mode is a competition, and a visitor who is not in a
      // crew should land on the board rather than on a sign-in form. The rider's own crew
      // sits under it, folded away once they have one — they already know what it is.
      var board = '<div class="crewcard crewboard"><h3>' + t("crew.board") + "</h3>"
        + '<p class="hint crewboardsub">' + t("crew.board.sub") + "</p>"
        + firstRunNote() + rankingHTML(rank) + "</div>";
      // Signed out, the only thing you can act on goes first and the board follows. Signed
      // in, the board leads because that is what you came back to look at.
      var h = me.paired ? board : signInHTML() + board;
      if (!me.paired) {
        /* the sign-in card is already at the top */
      } else if (me.crew) {
        // Folded by default put the only actionable thing in the feature behind a
        // disclosure triangle, under a 25-row board.
        h += '<details class="crewmine-wrap" open'
          + '><summary>' + '<img class="crewsumemb" alt="" src="' + me.crew.emblem + '"/>'
          + "<span>" + esc(me.crew.name) + "</span>"
          + '<span class="crewsumrole">' + t("crew.role." + me.role) + "</span></summary>"
          + myCrewHTML(me) + "</details>";
      } else if (!me.can_found) {
        h += '<div class="crewcard"><h3>' + t("crew.first.h") + "</h3>"
          + '<p class=hint>' + t("crew.first.p") + "</p></div>" + joinHTML(all, me);
      } else {
        // Cooling off: joinHTML already swaps the list for the countdown, but the create form
        // was rendered regardless, so the panel offered a full form whose only possible
        // outcome is the error in the card directly below it.
        h += (me.cooldown_until ? ""
              : me.creation_open
                ? createHTML(window.__CREWIDENT__ || { colour: "#4363d8", pattern: "solid" })
                : '<div class="crewcard"><h3>' + t("crew.closed.h") + "</h3>"
                  + '<p class=hint>' + t("crew.closed.p") + "</p></div>")
          + joinHTML(all, me);
      }
      h += explainer();
      panel.innerHTML = h;

      if (!me.paired) startPairing();
      else stopPairing();
      if (me.crew) bindMine(me);
      else { bindCreate(); bindJoin(); }
      doReveal();
      if (pendingStatus) { setStatus(pendingStatus); pendingStatus = null; }
      panel.querySelectorAll(".crewboard [data-i]").forEach(function (el) {
        el.onclick = function () {
          var r = rank[+el.dataset.i];
          if (r) flyToCrew(r.slug);
        };
      });
    });
    if (!window.__CREWIDENT__) {
      api("GET", "/api/v1/crews/identity").then(function (r) {
        if (r.ok) { window.__CREWIDENT__ = r.body; }
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
    // Close enough to find the street, far enough to still see it against the crew's own
    // ground. Flying to 13.2 put one square across the whole screen, which answers "where is
    // it" with a picture of nowhere. A reader already zoomed in keeps their zoom.
    map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 11.8),
                duration: 1600, essential: true });
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
    map.addLayer({
      id: "crew-target-case", type: "line", source: "crew-targets",
      paint: { "line-color": "rgba(0,0,0,.85)",
               "line-width": ["interpolate", ["linear"], ["zoom"], 8, 4, 14, 8] }
    });
    // an invisible fill, because the line it used to be bound to is three pixels wide
    map.addLayer({
      id: "crew-target-hit", type: "fill", source: "crew-targets",
      paint: { "fill-color": "#000", "fill-opacity": 0.01 }
    });
    map.addLayer({
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
    map.addLayer({
      id: "crew-lose-line", type: "line", source: "crew-targets",
      filter: ["==", ["get", "lose"], 1],
      paint: {
        "line-color": "#ff9f6b",
        "line-dasharray": [2, 1.6],
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
