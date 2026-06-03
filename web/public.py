"""Public website: world activity map + leaderboards + podium. Single page that
consumes the /api/v1 endpoints. Served at /."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

public_router = APIRouter()

_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>eucstats — EUC rider leaderboards & map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
:root{--bg:#0b1020;--card:#151c33;--ink:#e8ecf8;--mut:#8a93b2;--acc:#5cc8ff;--gold:#ffd24a;--sil:#cdd3e0;--brz:#e0915a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
a{color:var(--acc)}.wrap{max-width:1100px;margin:0 auto;padding:24px}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;border-bottom:1px solid #243056;padding-bottom:14px}
h1{margin:0;font-size:28px;letter-spacing:.5px}.tag{color:var(--mut)}
.summary{display:flex;gap:18px;flex-wrap:wrap;margin:18px 0}
.stat{background:var(--card);border:1px solid #243056;border-radius:12px;padding:12px 18px;min-width:120px}
.stat b{display:block;font-size:24px;color:var(--acc)}.stat span{color:var(--mut);font-size:13px}
h2{font-size:18px;margin:28px 0 12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border:1px solid #243056;border-radius:12px;padding:14px}
.card h3{margin:0 0 10px;font-size:15px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;text-align:left}
tr+tr{border-top:1px solid #20294a}.rk{color:var(--mut);width:26px}.val{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.rider{display:flex;align-items:center;gap:8px}.av{width:26px;height:26px;border-radius:50%;background:#2a3358;object-fit:cover}
.podium{display:flex;gap:14px;justify-content:center;align-items:flex-end;margin:14px 0 6px}
.pod{background:var(--card);border:1px solid #243056;border-radius:12px;padding:14px;text-align:center;width:170px}
.pod .av{width:64px;height:64px;margin:0 auto 8px;display:block}
.pod .nm{font-weight:600}.pod .km{color:var(--acc);font-size:20px}.pod.p1{border-color:var(--gold)}
.pod.p1 .medal{color:var(--gold)}.pod.p2{border-color:var(--sil)}.pod.p2 .medal{color:var(--sil)}
.pod.p3{border-color:var(--brz);margin-bottom:0}.pod.p3 .medal{color:var(--brz)}.pod.p1{margin-bottom:18px}
.medal{font-size:22px}#map{height:420px;border-radius:12px;border:1px solid #243056;margin-top:6px}
.foot{color:var(--mut);font-size:12px;margin:30px 0 10px;text-align:center}
</style></head><body><div class="wrap">
<header><h1>🛞 eucstats</h1><span class="tag">Electric unicycle rider leaderboards &amp; world activity — powered by eucplanet</span></header>
<div class="summary" id="summary"></div>

<h2>🏆 Mileage Kings</h2>
<div class="podium" id="podium"></div>

<h2>Leaderboards</h2>
<div class="grid" id="boards"></div>

<h2>🌍 World activity</h2>
<div id="map"></div>

<h2>By country</h2>
<div class="card"><table id="countries"><thead><tr><th>#</th><th>Country</th><th class="val">Total km</th><th class="val">Riders</th><th class="val">Avg/rider</th></tr></thead><tbody></tbody></table></div>

<h2>Records</h2>
<div class="card"><table id="records"><tbody></tbody></table></div>

<div class="foot">eucstats · clustered to ~10&nbsp;km for privacy · raw GPS never shown</div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const API="/api/v1";
const flag=cc=>cc?cc.toUpperCase().replace(/./g,c=>String.fromCodePoint(127397+c.charCodeAt())):"";
const av=id=>`<img class="av" src="${API}/riders/${encodeURIComponent(id)}/avatar" onerror="this.style.visibility='hidden'"/>`;
const rider=e=>`<span class="rider">${av(e.store_id)}<span>${flag(e.flag)} ${e.name||e.store_id}</span></span>`;
const j=p=>fetch(API+p).then(r=>r.json());

const BOARDS=[
 {k:"mileage",t:"Mileage (km)",c:"total_km",u:" km"},
 {k:"speed",t:"Top speed",c:"best_speed",u:" km/h"},
 {k:"daily",t:"Biggest day",c:"best_day_km",u:" km"},
 {k:"streak",t:"Longest streak",c:"longest_streak",u:" days"},
 {k:"gforce",t:"Max G-force",c:"best_gforce",u:" g"},
];
const RECLABEL={mileage_king:"👑 Mileage King",top_speed:"⚡ Top Speed",longest_trip:"📏 Longest Trip",max_gforce:"💥 Max G-Force"};

async function main(){
  const s=await j("/stats/summary");
  document.getElementById("summary").innerHTML=
    [["Riders",s.riders],["Trips",s.trips],["Total km",s.total_km],["Countries",s.countries]]
    .map(([l,v])=>`<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");

  const mil=(await j("/leaderboards/mileage?limit=3")).entries;
  const order=[1,0,2];
  document.getElementById("podium").innerHTML=order.filter(i=>mil[i]).map(i=>{
    const e=mil[i];const cls=["p1","p2","p3"][i];const m=["🥇","🥈","🥉"][i];
    return `<div class="pod ${cls}"><div class="medal">${m}</div>${av(e.store_id)}<div class="nm">${flag(e.flag)} ${e.name||e.store_id}</div><div class="km">${e.total_km} km</div></div>`;
  }).join("");

  document.getElementById("boards").innerHTML=BOARDS.map(b=>`<div class="card" id="b_${b.k}"><h3>${b.t}</h3><table><tbody></tbody></table></div>`).join("");
  for(const b of BOARDS){
    const rows=(await j(`/leaderboards/${b.k}?limit=10`)).entries;
    document.querySelector(`#b_${b.k} tbody`).innerHTML=rows.length?rows.map((e,i)=>
      `<tr><td class="rk">${i+1}</td><td>${rider(e)}</td><td class="val">${e[b.c]??0}${b.u}</td></tr>`).join("")
      :'<tr><td class="rk"></td><td style="color:var(--mut)">no data yet</td></tr>';
  }

  const cs=await j("/countries");
  document.querySelector("#countries tbody").innerHTML=cs.map((c,i)=>
    `<tr><td class="rk">${i+1}</td><td>${flag(c.country)} ${c.country}</td><td class="val">${c.total_km}</td><td class="val">${c.riders}</td><td class="val">${c.avg_km_per_rider}</td></tr>`).join("");

  const recs=await j("/records");
  document.querySelector("#records tbody").innerHTML=recs.filter(r=>r.value!=null).map(r=>
    `<tr><td>${RECLABEL[r.key]||r.key}</td><td>${rider(r.rider)}</td><td class="val">${Math.round(r.value*100)/100}</td></tr>`).join("")||'<tr><td style="color:var(--mut)">no records yet</td></tr>';

  const map=L.map("map",{worldCopyJump:true}).setView([30,5],2);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    {attribution:"&copy; OpenStreetMap &copy; CARTO",subdomains:"abcd",maxZoom:12}).addTo(map);
  const cells=await j("/map/cells?zoom=0.5");
  const pts=[];
  for(const c of cells){
    pts.push([c.lat,c.lon]);
    L.circleMarker([c.lat,c.lon],{radius:6+Math.min(14,Math.log2(1+c.total_km)*2),
      color:"#5cc8ff",weight:1,fillColor:"#5cc8ff",fillOpacity:.45})
     .bindPopup(`${c.rider_count} rider(s) · ${c.total_km} km`).addTo(map);
  }
  if(pts.length) map.fitBounds(pts,{padding:[40,40],maxZoom:9});
}
main().catch(e=>document.getElementById("summary").innerHTML="<div class='stat'>API error</div>");
</script></body></html>"""


@public_router.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(_PAGE)
