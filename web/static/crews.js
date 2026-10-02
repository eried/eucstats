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

  function cellsByCrew() {
    var out = [];
    if (!TERR || !TERR.cells) return out;
    for (var i = 0; i < TERR.cells.length; i += 3) {
      var c = TERR.cells[i];
      if (!out[c]) out[c] = [];
      out[c].push([TERR.cells[i + 1], TERR.cells[i + 2]]);
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

  var LAYERS = ["crew-fill", "crew-pattern", "crew-edge", "crew-edge-glow"];

  function clearLayers() {
    LAYERS.forEach(function (l) { if (map.getLayer(l)) map.removeLayer(l); });
    if (map.getSource("crew-cells")) map.removeSource("crew-cells");
    if (map.getSource("crew-edges")) map.removeSource("crew-edges");
    markers.forEach(function (m) { m.remove(); });
    markers = [];
  }

  function buildLayers() {
    if (!TERR || !TERR.crews || !TERR.crews.length) return;
    ensurePatterns();
    var z = TERR.z, groups = cellsByCrew();
    var fills = { type: "FeatureCollection", features: [] };
    var edges = { type: "FeatureCollection", features: [] };

    TERR.crews.forEach(function (crew, idx) {
      var cells = groups[idx] || [];
      if (!cells.length) return;
      // one MultiPolygon per crew: adjacent tiles share an edge exactly, so a single feature
      // renders as one solid shape with no antialiasing seam running through it
      fills.features.push({
        type: "Feature",
        properties: { c: crew.colour, p: "crewpat-" + crew.pattern, i: idx,
                      name: crew.name, slug: crew.slug, km2: crew.km2 },
        geometry: { type: "MultiPolygon",
                    coordinates: cells.map(function (t) { return tileRing(t[0], t[1], z); }) }
      });
      edges.features.push({
        type: "Feature",
        properties: { c: crew.colour, i: idx },
        geometry: { type: "MultiLineString", coordinates: outline(cells, z) }
      });
    });

    map.addSource("crew-cells", { type: "geojson", data: fills });
    map.addSource("crew-edges", { type: "geojson", data: edges });

    var op = (window.__CREWCFG__ && window.__CREWCFG__.opacity) || 0.55;
    map.addLayer({
      id: "crew-fill", type: "fill", source: "crew-cells",
      paint: { "fill-color": ["get", "c"], "fill-opacity": 0,
               "fill-opacity-transition": { duration: 600 } }
    });
    map.addLayer({
      id: "crew-pattern", type: "fill", source: "crew-cells",
      filter: ["!=", ["get", "p"], "crewpat-solid"],
      paint: { "fill-pattern": ["get", "p"], "fill-opacity": 0,
               "fill-opacity-transition": { duration: 600 } }
    });
    // the glow sits under the hairline so a border reads at low zoom without being fat
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
      map.setPaintProperty("crew-fill", "fill-opacity", op);
      map.setPaintProperty("crew-pattern", "fill-opacity", Math.min(1, op + 0.15));
      map.setPaintProperty("crew-edge", "line-opacity", 0.95);
      map.setPaintProperty("crew-edge-glow", "line-opacity", 0.35);
    });

    buildEmblems();
    map.on("zoom", sizeEmblems);
    map.on("click", "crew-fill", onCellClick);
    map.on("mouseenter", "crew-fill", function () { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "crew-fill", function () { map.getCanvas().style.cursor = ""; });
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
      var px = Math.round(s * tilePx * 0.76);        // inset, so the emblem sits inside its edge
      el.style.width = el.style.height = px + "px";
      // below about 22px an emblem is a smudge, and the name under it is unreadable
      el.style.opacity = px < 22 ? 0 : 1;
      el.classList.toggle("tiny", px < 64);
    });
  }

  /* ---------- interaction ---------- */

  function onCellClick(e) {
    var f = e.features && e.features[0];
    if (!f) return;
    var p = f.properties;
    new maplibregl.Popup({ closeButton: false, className: "crewpop", offset: 10 })
      .setLngLat(e.lngLat)
      .setHTML('<div class="crewpop-in"><img src="/api/v1/crews/' + encodeURIComponent(p.slug)
        + '/emblem" alt=""/><div><b>' + esc(p.name) + "</b><span>" + p.km2
        + " km² held</span></div></div>")
      .addTo(map);
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

  var EXPLAINER =
    '<details class="crewhow"><summary>How territory works</summary>' +
    '<p>Ground is won by <b>riding it</b>. Every validated ride is credited to the crew you ' +
    'were in when you uploaded it, and the kilometres are spread across the map tiles the ' +
    'ride passed through. In each tile, the crew with the most kilometres <b>over the last ' +
    '90 days</b> holds it.</p>' +
    '<p>A crew has to <b>plant a 2×2 block</b> before it holds anything at all. One ride ' +
    'down one street paints nothing — four tiles won together is a deliberate claim. From ' +
    'there, territory <b>grows by contact</b>: any tile you win that touches your existing ' +
    'ground joins it. Tiles that touch nothing are not shown.</p>' +
    '<p>Nothing is permanent. The window rolls, so a crew that stops riding fades, and ' +
    '<b>more kilometres takes a tile</b> from whoever holds it. Ties stay with whoever got ' +
    'there first, so borders do not flicker.</p>' +
    '<p>Your rides keep the crew that earned them. Switching crews does not redraw the ' +
    'map — and leaving one does not take its ground away from the people still riding for ' +
    'it.</p></details>';

  function rankingHTML(rows) {
    if (!rows || !rows.length) {
      return '<div class="empty">No crew holds any ground yet.</div>';
    }
    return '<table class="crewrank"><tbody>' + rows.map(function (r, i) {
      return '<tr class="sel" data-slug="' + esc(r.slug) + '"><td class=rk>' + (i + 1) + "</td>"
        + '<td><span class="celln">' + swatch(r.colour, r.pattern)
        + "<span>" + esc(r.name) + "</span></span></td>"
        + "<td class=val>" + r.km2 + " km²</td>"
        + '<td class="val sub">' + r.tiles + " tiles</td></tr>";
    }).join("") + "</tbody></table>";
  }

  /* ---------- sign-in ---------- */

  function signInHTML() {
    return '<div class="crewcard crewsign">'
      + "<h3>Sign in with your phone</h3>"
      + "<p class=hint>Crews has no password. Open <b>EUC Planet</b> on your phone, "
      + "tap <b>Scan</b>, and point it at this code — the app tells us who you are. "
      + "Nothing is typed, and your rider id never leaves the phone.</p>"
      + '<div class="crewqr" id="crewqr"><div class="spin"></div></div>'
      + '<div class="crewcode" id="crewcode">······</div>'
      + '<p class=hint id="crewcodehint">The code expires in three minutes.</p>'
      + "</div>";
  }

  function startPairing() {
    stopPairing();
    api("POST", "/api/v1/pair/start").then(function (r) {
      if (!r.ok) { setStatus("Pairing is unavailable right now."); return; }
      pairToken = r.body.token;
      var qr = document.getElementById("crewqr");
      var code = document.getElementById("crewcode");
      if (qr) qr.innerHTML = '<img alt="Pairing QR code" src="data:image/png;base64,'
        + r.body.qr + '"/>';
      if (code) code.textContent = r.body.code;
      var left = r.body.expires_in;
      pairTimer = setInterval(function () {
        left -= 2;
        if (left <= 0) { startPairing(); return; }          // quietly roll a fresh code
        api("GET", "/api/v1/pair/poll?token=" + encodeURIComponent(pairToken))
          .then(function (p) {
            if (p.ok && p.body.status === "paired") { stopPairing(); show(); }
            else if (!p.ok) startPairing();
          });
      }, 2000);
    });
  }

  function stopPairing() {
    if (pairTimer) clearInterval(pairTimer);
    pairTimer = null;
  }

  function setStatus(msg, bad) {
    var el = document.getElementById("crewstatus");
    if (el) el.innerHTML = msg ? '<div class="crewmsg' + (bad ? " bad" : "") + '">'
      + esc(msg) + "</div>" : "";
  }

  /* ---------- create / manage ---------- */

  function createHTML(ident) {
    var cols = (window.__CREWCFG__ && window.__CREWCFG__.palette) || [];
    return '<div class="crewcard">'
      + "<h3>Start a crew</h3>"
      + "<p class=hint>Your colour and pattern are picked from the least-used combination "
      + "so the map stays readable — change them if you like. No two crews fly the same "
      + "pair.</p>"
      + '<label>Name<input id="cf-name" maxlength="28" placeholder="Nordlys Collective"></label>'
      + '<label>Description<input id="cf-desc" maxlength="280" placeholder="Oslo, mostly after dark."></label>'
      + '<div class="crewident">' + swatch(ident.colour, ident.pattern, 40)
      + '<div><div class="crewidentl">Colours</div>'
      + '<select id="cf-colour">' + cols.map(function (c) {
          return '<option value="' + c + '"' + (c === ident.colour ? " selected" : "") + ">"
            + c + "</option>"; }).join("") + "</select>"
      + '<select id="cf-pattern">' + PATTERNS.map(function (p) {
          return '<option value="' + p + '"' + (p === ident.pattern ? " selected" : "") + ">"
            + p + "</option>"; }).join("") + "</select></div></div>"
      + "<label>Who can join"
      + '<select id="cf-policy">'
      + '<option value="approval">A leader approves each request</option>'
      + '<option value="open">Anyone can join</option>'
      + '<option value="invite">Only with an invite code</option>'
      + "</select></label>"
      + '<button class="crewbtn" id="cf-go">Create crew</button>'
      + "</div>";
  }

  function bindCreate() {
    var sw = document.querySelector(".crewident .crewsw");
    function sync() {
      var c = document.getElementById("cf-colour").value;
      var p = document.getElementById("cf-pattern").value;
      if (sw) { sw.style.background = c; sw.dataset.p = p; }
    }
    ["cf-colour", "cf-pattern"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.onchange = sync;
    });
    var go = document.getElementById("cf-go");
    if (go) go.onclick = function () {
      go.disabled = true;
      api("POST", "/api/v1/crews", {
        name: document.getElementById("cf-name").value,
        description: document.getElementById("cf-desc").value,
        colour: document.getElementById("cf-colour").value,
        pattern: document.getElementById("cf-pattern").value,
        join_policy: document.getElementById("cf-policy").value
      }).then(function (r) {
        go.disabled = false;
        if (r.ok) { show(); reloadTerritory(); }
        else setStatus((r.err && r.err.detail) || "That did not work.", true);
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
      + esc(c.pattern) + " · " + c.members + (c.members === 1 ? " rider" : " riders")
      + " · you are the " + esc(me.role) + "</div></div></div>";
    if (me.status === "pending") {
      h += '<div class="crewmsg">Your request is waiting for a leader to approve it.</div>';
    }
    if (c.description) h += "<p>" + esc(c.description) + "</p>";
    h += '<div class="crewterr" id="crewterr">—</div>';
    if (c.invite_code) {
      h += '<p class=hint>Invite code: <code>' + esc(c.invite_code) + "</code></p>";
    }
    if (me.pending && me.pending.length) {
      h += '<div class="crewpend"><h4>Waiting to join</h4>'
        + me.pending.map(function (p) {
            return '<div class="crewpendr"><span>' + esc(p.name) + "</span>"
              + '<button class="crewbtn mini" data-ok="' + esc(p.store_id) + '">Accept</button>'
              + '<button class="crewbtn mini ghost" data-no="' + esc(p.store_id) + '">Decline</button>'
              + "</div>"; }).join("")
        + "</div>";
    }
    if (lead) {
      h += '<details class="crewedit"><summary>Crew settings</summary>'
        + '<label>Name<input id="ce-name" maxlength="28" value="' + esc(c.name) + '"></label>'
        + '<label>Description<input id="ce-desc" maxlength="280" value="'
        + esc(c.description || "") + '"></label>'
        + "<label>Who can join<select id=\"ce-policy\">"
        + ["approval", "open", "invite"].map(function (p) {
            return '<option value="' + p + '"' + (p === c.join_policy ? " selected" : "")
              + ">" + p + "</option>"; }).join("")
        + "</select></label>"
        + '<label class="crewfile">Emblem (a small square image)'
        + '<input type="file" id="ce-logo" accept="image/*"></label>'
        + '<p class=hint>Leave it empty and we draw one from your crew name and colour.</p>'
        + '<button class="crewbtn" id="ce-save">Save</button>'
        + '<button class="crewbtn ghost" id="ce-clearlogo">Use the generated emblem</button>'
        + "</details>";
    }
    h += '<div class="crewacts">'
      + '<button class="crewbtn ghost" id="cm-leave">Leave crew</button>'
      + '<button class="crewbtn ghost" id="cm-signout">Sign out of this browser</button>'
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
    var leave = document.getElementById("cm-leave");
    if (leave) leave.onclick = function () {
      if (!confirm("Leave " + c.name + "? There is a 7-day wait before you can join another."))
        return;
      api("POST", "/api/v1/crews/leave", {}).then(function (r) {
        if (r.ok) { show(); reloadTerritory(); }
        else setStatus((r.err && r.err.detail) || "That did not work.", true);
      });
    };
    var so = document.getElementById("cm-signout");
    if (so) so.onclick = function () {
      api("POST", "/api/v1/crews/signout", {}).then(function () { ME = null; show(); });
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
        else setStatus((r.err && r.err.detail) || "That did not work.", true);
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
          else setStatus("That image was not accepted.", true);
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
      var t = r.body.territory || {};
      el.innerHTML = '<div class="crewbig">' + (t.km2 || 0) + ' <span>km² held</span></div>'
        + '<div class="crewsub">' + (t.tiles || 0) + " tiles"
        + (t.tiles ? "" : " — ride a 2×2 block to plant your first claim") + "</div>";
    });
  }

  function joinHTML(crews, me) {
    if (me.cooldown_until) {
      return '<div class="crewcard"><h3>Joining a crew</h3>'
        + '<div class="crewmsg">You left a crew recently. You can join another after '
        + esc(new Date(me.cooldown_until).toLocaleString()) + "."
        + "</div><p class=hint>The wait stops crew-hopping to chase territory. It is short "
        + "enough not to sting and long enough not to be worth it.</p></div>";
    }
    if (!crews.length) return "";
    return '<div class="crewcard"><h3>Join a crew</h3><div class="crewlist">'
      + crews.map(function (c) {
          var label = c.join_policy === "open" ? "Join"
            : c.join_policy === "invite" ? "Use code" : "Request";
          return '<div class="crewrow">' + swatch(c.colour, c.pattern, 26)
            + '<div class="crewrown"><b>' + esc(c.name) + "</b><span>" + c.members
            + (c.members === 1 ? " rider" : " riders") + " · " + esc(c.join_policy)
            + "</span></div>"
            + '<button class="crewbtn mini" data-join="' + esc(c.slug) + '" data-pol="'
            + esc(c.join_policy) + '">' + label + "</button></div>";
        }).join("")
      + "</div></div>";
  }

  function bindJoin() {
    document.querySelectorAll("[data-join]").forEach(function (b) {
      b.onclick = function () {
        var body = {};
        if (b.dataset.pol === "invite") {
          var code = prompt("Invite code for this crew:");
          if (!code) return;
          body.invite_code = code;
        }
        api("POST", "/api/v1/crews/" + b.dataset.join + "/join", body).then(function (r) {
          if (r.ok) { show(); reloadTerritory(); }
          else setStatus((r.err && r.err.detail) || "That did not work.", true);
        });
      };
    });
  }

  /* ---------- the mode ---------- */

  // The heatmap and territory answer different questions and look terrible together: the glow
  // bleeds across the rectangles' edges, which are the whole point of them. So the two modes
  // are exclusive — entering crews fades the heat out, leaving brings it back.
  function setHeat(on) {
    if (!map.getLayer("heat")) return;
    var want = on ? ((window.__HEAT__ && window.__HEAT__.opacity) || 0.62) : 0;
    try { map.setPaintProperty("heat", "heatmap-opacity", want); } catch (e) {}
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
      var h = "";
      if (!me.paired) {
        h = signInHTML();
      } else if (me.crew) {
        h = myCrewHTML(me);
      } else if (!me.can_found) {
        h = '<div class="crewcard"><h3>Ride once first</h3><p class=hint>Crews are for '
          + "riders. Upload one validated ride from the app and you can found or join "
          + "one.</p></div>" + joinHTML(all, me);
      } else {
        h = (me.creation_open ? createHTML(window.__CREWIDENT__ || { colour: "#4363d8", pattern: "solid" }) : "")
          + joinHTML(all, me);
      }
      h += '<div class="crewcard"><h3>Most ground held</h3>' + rankingHTML(rank) + "</div>";
      h += EXPLAINER;
      panel.innerHTML = h;

      if (!me.paired) startPairing();
      else stopPairing();
      if (me.crew) bindMine(me);
      else { bindCreate(); bindJoin(); }
      panel.querySelectorAll("[data-slug]").forEach(function (tr) {
        tr.onclick = function () { flyToCrew(tr.dataset.slug); };
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
    for (var i = 0; i < TERR.cells.length; i += 3) {
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
