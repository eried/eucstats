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
  function days(n) {
    if (n <= 0) return t("crew.now");
    return n === 1 ? t("crew.day1") : t("crew.days", { n: n });
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

  // cells arrive as [crewIndex, x, y, band, tenths-of-a-km]
  function cellsByCrew() {
    var out = [];
    if (!TERR || !TERR.cells) return out;
    for (var i = 0; i < TERR.cells.length; i += 5) {
      var c = TERR.cells[i];
      if (!out[c]) out[c] = [];
      out[c].push([TERR.cells[i + 1], TERR.cells[i + 2], TERR.cells[i + 3] || 0,
                   TERR.cells[i + 4] || 0]);
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

  var LAYERS = ["crew-fill", "crew-pattern", "crew-contested", "crew-edge",
                "crew-edge-glow"];

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

  function clearLayers() {
    map.off("zoom", onZoom);
    map.off("click", "crew-fill", onCellClick);
    map.off("mouseenter", "crew-fill", cursorPointer);
    map.off("mouseleave", "crew-fill", cursorDefault);
    LAYERS.forEach(function (l) { if (map.getLayer(l)) map.removeLayer(l); });
    if (map.getSource("crew-cells")) map.removeSource("crew-cells");
    if (map.getSource("crew-edges")) map.removeSource("crew-edges");
    if (map.getSource("crew-hot")) map.removeSource("crew-hot");
    markers.forEach(function (m) { m.remove(); });
    markers = [];
  }

  function buildLayers() {
    if (!TERR || !TERR.crews || !TERR.crews.length) return;
    ensurePatterns();
    var z = TERR.z, groups = cellsByCrew();
    var fills = { type: "FeatureCollection", features: [] };
    var edges = { type: "FeatureCollection", features: [] };
    var hot = { type: "FeatureCollection", features: [] };

    TERR.crews.forEach(function (crew, idx) {
      var cells = groups[idx] || [];
      if (!cells.length) return;
      // One feature per crew per pressure band. Adjacent tiles share an edge exactly, so a
      // single feature renders as one solid shape with no antialiasing seam through it — and
      // splitting by band is what lets contested ground be drawn fainter without needing a
      // separate feature for every tile on the map.
      [0, 1, 2, 3].forEach(function (band) {
        var inBand = cells.filter(function (t) { return (t[2] || 0) === band; });
        if (!inBand.length) return;
        fills.features.push({
          type: "Feature",
          properties: { c: crew.colour, p: "crewpat-" + crew.pattern, i: idx, band: band,
                        need: inBand[0][3] || 0,
                        name: crew.name, slug: crew.slug, km2: crew.km2 },
          geometry: { type: "MultiPolygon",
                      coordinates: inBand.map(function (t) { return tileRing(t[0], t[1], z); }) }
        });
      });
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

    var op = (window.__CREWCFG__ && window.__CREWCFG__.opacity) || 0.55;
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
      var pat = Math.min(1, op + 0.15);
      map.setPaintProperty("crew-fill", "fill-opacity",
        ["match", ["get", "band"], 1, op * 0.84, 2, op * 0.66, 3, op * 0.5, op]);
      map.setPaintProperty("crew-pattern", "fill-opacity",
        ["match", ["get", "band"], 1, pat * 0.84, 2, pat * 0.66, 3, pat * 0.4, pat]);
      map.setPaintProperty("crew-edge", "line-opacity", 0.95);
      map.setPaintProperty("crew-edge-glow", "line-opacity", 0.35);
      map.setPaintProperty("crew-contested", "line-opacity", 0.8);
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
      el.title = crew.name + " · " + crew.km2 + " km²";
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

  function onCellClick(e) {
    var f = e.features && e.features[0];
    if (!f) return;
    var p = f.properties;
    // The band is the answer to "am I actually taking this off them?". Without it the only
    // signal is the shade, and a shade on its own is something you notice after the fact.
    var band = p.band || 0;
    var km = (p.need || 0) / 10;
    var state = [t("crew.tile.safe"), t("crew.tile.pushed"), t("crew.tile.slipping"),
                 t("crew.tile.fading")][band];
    // the number is the whole point: "about to flip" without it is a warning with no content
    var detail = band === 1 || band === 2
      ? t("crew.tile.need", { v: km.toFixed(1) })
      : t("crew.tile.clear", { v: km.toFixed(1) });
    new maplibregl.Popup({ closeButton: false, className: "crewpop", offset: 10 })
      .setLngLat(e.lngLat)
      .setHTML('<div class="crewpop-in"><img src="/api/v1/crews/' + encodeURIComponent(p.slug)
        + '/emblem" alt=""/><div><b>' + esc(p.name) + "</b><span>" + esc(state)
        + "</span><span>" + esc(detail) + "</span></div></div>")
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
    return fetch(path, opt).then(function (r) {
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
      + ["crew.how.1", "crew.how.2", "crew.how.3", "crew.how.4", "crew.how.5"]
        .map(function (k) { return "<p>" + t(k, { n: SEED, d: WINDOW_DAYS }) + "</p>"; }).join("")
      + "</details>";
  }

  function rankingHTML(rows) {
    if (!rows || !rows.length) {
      return '<div class="empty">' + t("crew.empty") + "</div>";
    }
    if (!H.podList) return plainRank(rows);
    return H.podList(rows, {
      iconFn: function (e) { return '<img class="crewpodemb" alt="" src="' + e.emblem + '"/>'; },
      // the swatch is the crew's identity on the map, so it belongs beside every name
      label: function (e) { return swatch(e.colour, e.pattern, 14) + " " + esc(e.name); },
      val: function (e) { return fmtKm2(e.best_km2); },
      sub: function (e) {
        return tiles(e.tiles)
          + (e.regions > 1 ? " · " + t("crew.patches", { n: e.regions }) : "");
      },
      click: true
    });
  }

  // The three states exist on the map whether or not anyone taps a tile, so they get named
  // under the board rather than hiding in a popup.
  function legendHTML() {
    return '<div class="crewlegend">'
      + '<span class="b0"><i></i>' + t("crew.tile.safe") + "</span>"
      + '<span class="b1"><i></i>' + t("crew.tile.pushed") + "</span>"
      + '<span class="b2"><i></i>' + t("crew.tile.slipping") + "</span>"
      + '<span class="b3"><i></i>' + t("crew.tile.fading") + "</span>"
      + "</div>";
  }

  function plainRank(rows) {
    return '<table class="crewrank"><tbody>' + rows.map(function (r, i) {
      return '<tr class="sel" data-i="' + i + '"><td class=rk>' + (i + 1) + "</td>"
        + '<td><span class="celln">' + swatch(r.colour, r.pattern)
        + "<span>" + esc(r.name) + "</span></span></td>"
        + "<td class=val>" + fmtKm2(r.best_km2) + "</td>"
        + '<td class="val sub">' + tiles(r.tiles) + "</td></tr>";
    }).join("") + "</tbody></table>";
  }

  function tiles(n) {
    return n === 1 ? t("crew.tile1") : t("crew.tiles", { n: n });
  }

  function riders(n) {
    return n === 1 ? t("crew.rider1") : t("crew.riders", { n: n });
  }

  // Area follows the same metric/imperial switch as every other number on the site. A rider
  // who reads their rides in miles should not have one board quietly answering in km.
  var MI2_PER_KM2 = 0.3861021585;

  function daysUntil(iso) {
    var d = Math.ceil((new Date(iso) - Date.now()) / 86400000);
    return d > 0 ? d : 1;
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

  // Who actually rode for the crew, over the same window the territory is measured on — so
  // the list explains the shape on the map rather than ranking loyalty.
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
        + '<span class="crewckm">' + Math.round(c.km) + " km</span></div>";
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
      + '<p class="hint crewsame">' + t("crew.signin.same") + "</p>"
      + '<a class="crewbtn crewopen" id="crewopen" href="#">' + t("crew.signin.open") + "</a>"
      + "</div>";
  }

  var pairRolls = 0;
  var PAIR_MAX_ROLLS = 5;        // about fifteen minutes of waiting, then it asks

  function startPairing() {
    stopPairing();
    api("POST", "/api/v1/pair/start").then(function (r) {
      if (!r.ok) { setStatus(t("crew.err"), true); return; }
      pairToken = r.body.token;
      var qr = document.getElementById("crewqr");
      var code = document.getElementById("crewcode");
      var open = document.getElementById("crewopen");
      // the app-scheme form of the same link, so a tap on this device hands the code to the
      // app without a round trip through the web page
      var deep = "eucplanet://pair?code=" + encodeURIComponent(r.body.code)
        + "&host=" + encodeURIComponent(location.origin);
      if (qr) {
        qr.innerHTML = '<img alt="Crew Pass code" src="data:image/png;base64,'
          + r.body.qr + '"/>';
        qr.href = deep;
      }
      if (open) open.href = deep;
      if (code) code.textContent = r.body.code;
      var left = r.body.expires_in;
      pairTimer = setInterval(function () {
        // Nothing is going to happen while the tab is in the background, and a code that
        // rolls forever is a code that eats the hourly budget for everybody sharing the
        // address. One idle tab was making about 1,800 requests an hour.
        if (document.hidden) return;
        left -= 2;
        if (left <= 0) {
          if (pairRolls++ >= PAIR_MAX_ROLLS) {
            stopPairing();
            var el = document.getElementById("crewcodehint");
            if (el) el.innerHTML = '<a href="#" id="crewagain">' + t("crew.signin.again")
              + "</a>";
            var again = document.getElementById("crewagain");
            if (again) again.onclick = function (ev) {
              ev.preventDefault();
              pairRolls = 0;
              startPairing();
            };
            return;
          }
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
              setStatus(t("crew.signin.ok"));
              show();
            }
            else if (!p.ok) startPairing();
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
  function ask(message, confirmLabel, ok) {
    var host = document.getElementById("crewstatus");
    if (!host) { if (window.confirm(message)) ok(); return; }
    host.innerHTML = '<div class="crewask"><p>' + esc(message) + "</p>"
      + '<button class="crewbtn mini" id="crewask-y">' + esc(confirmLabel) + "</button>"
      + '<button class="crewbtn mini ghost" id="crewask-n">' + t("crew.cancel") + "</button>"
      + "</div>";
    host.scrollIntoView({ block: "nearest", behavior: "smooth" });
    document.getElementById("crewask-n").onclick = function () { host.innerHTML = ""; };
    document.getElementById("crewask-y").onclick = function () {
      host.innerHTML = "";
      ok();
    };
  }

  // An in-panel prompt, same reasoning.
  function askFor(message, placeholder, ok) {
    var host = document.getElementById("crewstatus");
    if (!host) { var v = window.prompt(message); if (v) ok(v); return; }
    host.innerHTML = '<div class="crewask"><p>' + esc(message) + "</p>"
      + '<input id="crewask-in" placeholder="' + esc(placeholder) + '" maxlength="16">'
      + '<button class="crewbtn mini" id="crewask-y">' + t("crew.join.btn") + "</button>"
      + '<button class="crewbtn mini ghost" id="crewask-n">' + t("crew.cancel") + "</button>"
      + "</div>";
    host.scrollIntoView({ block: "nearest", behavior: "smooth" });
    var input = document.getElementById("crewask-in");
    input.focus();
    document.getElementById("crewask-n").onclick = function () { host.innerHTML = ""; };
    function go() {
      var v = input.value.trim();
      host.innerHTML = "";
      if (v) ok(v);
    }
    document.getElementById("crewask-y").onclick = go;
    input.onkeydown = function (e) { if (e.key === "Enter") go(); };
  }

  function setStatus(msg, bad) {
    var el = document.getElementById("crewstatus");
    if (!el) return;
    el.innerHTML = msg ? '<div class="crewmsg' + (bad ? " bad" : "") + '">'
      + esc(msg) + "</div>" : "";
    // The success path got scrolled into view and the failure path did not, so an error from
    // deep inside the crew card painted at the top of a scrolled panel where nobody saw it.
    if (msg) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
        else setStatus((r.err && r.err.detail) || t("crew.err"), true);
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
    if (c.invite_code && c.join_policy === "invite") {
      h += '<p class=hint>' + t("crew.mine.invite") + ': <code>' + esc(c.invite_code) + "</code></p>";
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
      + '<button class="crewbtn ghost" id="cm-leave">' + t("crew.mine.leave") + "</button>"
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
          else setStatus((r.err && r.err.detail) || t("crew.err"), true);
        });
      };
    });
    var leave = document.getElementById("cm-leave");
    if (leave) leave.onclick = function () {
      ask(t("crew.mine.leaveq", { name: c.name, n: days(COOLDOWN_DAYS) }),
          t("crew.mine.leave"), function () {
        api("POST", "/api/v1/crews/leave", {}).then(function (r) {
          if (r.ok) { reveal(".crewboard"); show(); reloadTerritory(); }
          else setStatus((r.err && r.err.detail) || t("crew.err"), true);
        });
      });
    };
    var so = document.getElementById("cm-signout");
    if (so) so.onclick = function () {
      ask(t("crew.mine.signoutq"), t("crew.mine.signout"), function () {
        api("POST", "/api/v1/crews/signout", {}).then(function () { ME = null; show(); });
      });
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
        else setStatus((r.err && r.err.detail) || t("crew.err"), true);
      });
    };
    var logo = document.getElementById("ce-logo");
    if (logo) logo.onchange = function () {
      if (!logo.files || !logo.files[0]) return;
      var fd = new FormData();
      fd.append("file", logo.files[0]);
      fetch("/api/v1/crews/" + c.slug + "/emblem",
            { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (r) {
          if (r.ok) { show(); reloadTerritory(); }
          else setStatus(t("crew.mine.emblembad"), true);
        });
    };
    var clr = document.getElementById("ce-clearlogo");
    if (clr) clr.onclick = function () {
      fetch("/api/v1/crews/" + c.slug + "/emblem",
            { method: "DELETE", credentials: "same-origin" })
        .then(function () { show(); reloadTerritory(); });
    };
    // the crew's own ground, from the ranking it is already in
    api("GET", "/api/v1/crews/" + c.slug).then(function (r) {
      var el = document.getElementById("crewterr");
      if (!el || !r.ok) return;
      var terr = r.body.territory || {};     // not `t`: that is the translator
      el.innerHTML = '<div class="crewbig">' + fmtKm2(terr.best_km2)
        + " <span>" + t("crew.mine.ao") + "</span></div>"
        + '<div class="crewsub">' + tiles(terr.tiles || 0)
        + (terr.regions > 1 ? " · " + t("crew.patches", { n: terr.regions }) : "")
        + (terr.km2 && terr.km2 !== terr.best_km2
            ? " · " + t("crew.inall", { v: fmtKm2(terr.km2) }) : "")
        + (terr.tiles ? "" : " · " + t("crew.mine.start", { n: SEED })) + "</div>"
        + contributorsHTML(r.body.contributors);
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
            + esc(c.slug) + '" data-pol="' + esc(c.join_policy) + '">' + label + "</button></div>";
        }).join("")
      + "</div></div>";
  }

  function bindJoin() {
    document.querySelectorAll("[data-join]").forEach(function (b) {
      b.onclick = function () {
        function send(body) {
          api("POST", "/api/v1/crews/" + b.dataset.join + "/join", body).then(function (r) {
            if (r.ok) { reveal(".crewmine-wrap"); show(); reloadTerritory(); }
            else setStatus((r.err && r.err.detail) || t("crew.err"), true);
          });
        }
        if (b.dataset.pol === "invite") {
          askFor(t("crew.join.codeask"), "ABC12345", function (code) {
            send({ invite_code: code });
          });
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
    var ghost = CFG.heat_ghost != null ? CFG.heat_ghost : 0.2;
    var want = on ? full : full * ghost;
    try { map.setPaintProperty("heat", "heatmap-opacity", want); } catch (e) {}
  }

  // Re-rendering resets the panel's scroll, so an action that changes your standing left you
  // staring at the top of the board with no sign it worked. Whatever is new gets scrolled to.
  var revealNext = null;

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
      var rank = res[1].ok ? res[1].body.crews || [] : [];
      var all = res[2].ok ? res[2].body.crews || [] : [];
      // Standings first and always: the mode is a competition, and a visitor who is not in a
      // crew should land on the board rather than on a sign-in form. The rider's own crew
      // sits under it, folded away once they have one — they already know what it is.
      var board = '<div class="crewcard crewboard"><h3>' + t("crew.board") + "</h3>"
        + rankingHTML(rank) + legendHTML() + "</div>";
      // Signed out, the only thing you can act on goes first and the board follows. Signed
      // in, the board leads because that is what you came back to look at.
      var h = me.paired ? board : signInHTML() + board;
      if (!me.paired) {
        /* the sign-in card is already at the top */
      } else if (me.crew) {
        h += '<details class="crewmine-wrap" ' + (me.status === "pending" ? "open" : "")
          + '><summary>' + '<img class="crewsumemb" alt="" src="' + me.crew.emblem + '"/>'
          + "<span>" + esc(me.crew.name) + "</span>"
          + '<span class="crewsumrole">' + t("crew.role." + me.role) + "</span></summary>"
          + myCrewHTML(me) + "</details>";
      } else if (!me.can_found) {
        h += '<div class="crewcard"><h3>' + t("crew.first.h") + "</h3>"
          + '<p class=hint>' + t("crew.first.p") + "</p></div>" + joinHTML(all, me);
      } else {
        h += (me.creation_open
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
