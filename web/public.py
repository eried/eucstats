"""Public website: GPU-accelerated (MapLibre GL) world heatmap of aggregated
activity, with openable leaderboard/tool panels docked at the bottom, fly-to
animations when a winner is selected, and an eucplanet badge. Consumes /api/v1."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

public_router = APIRouter()

_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>eucstats</title>
<link rel="icon" type="image/png" href="/static/eucplanet.png"/>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;color:#e8ecf8;background:#0b1020;overflow:hidden}
#map{position:fixed;inset:0;z-index:0}
.maplibregl-ctrl-attrib{font-size:9px;opacity:.5}
.topbar{position:fixed;top:14px;left:14px;z-index:500;display:flex;flex-direction:column;gap:8px;max-width:min(92vw,440px)}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{background:rgba(21,28,51,.74);backdrop-filter:blur(8px);border:1px solid #2a3358;border-radius:999px;padding:5px 11px;font-size:12px}
.chip b{color:#5cc8ff}
.champ{background:rgba(21,28,51,.78);backdrop-filter:blur(8px);border:1px solid #4a3a12;border-radius:12px;padding:8px 12px;font-size:13px}
.champ b{color:#ffd24a}
.badge{position:fixed;left:14px;bottom:16px;z-index:500;display:flex;align-items:center;gap:8px;text-decoration:none;color:#cdd3e0;background:rgba(15,20,36,.8);backdrop-filter:blur(10px);border:1px solid #2a3358;border-radius:999px;padding:6px 12px 6px 6px;font-size:12px}
.badge img{width:26px;height:26px;border-radius:7px}.badge b{color:#e8ecf8}.badge:hover{border-color:#5cc8ff}
.dock{position:fixed;left:50%;transform:translateX(-50%);bottom:16px;z-index:600;display:flex;gap:6px;background:rgba(15,20,36,.82);backdrop-filter:blur(12px);border:1px solid #2a3358;border-radius:16px;padding:8px;box-shadow:0 12px 40px rgba(0,0,0,.45)}
.dock button{background:transparent;color:#e8ecf8;border:0;border-radius:11px;padding:10px 14px;font-size:14px;cursor:pointer;transition:background .15s}
.dock button:hover{background:#1d2647}.dock button.on{background:#2a3a6e}
.panel{position:fixed;left:50%;bottom:78px;transform:translateX(-50%) translateY(140%);opacity:0;visibility:hidden;z-index:550;width:min(94vw,740px);max-height:62vh;overflow:auto;background:rgba(17,23,43,.94);backdrop-filter:blur(16px);border:1px solid #2a3358;border-radius:18px;box-shadow:0 24px 70px rgba(0,0,0,.55);transition:transform .3s cubic-bezier(.2,.8,.2,1),opacity .25s}
.panel.open{transform:translateX(-50%) translateY(0);opacity:1;visibility:visible}
.phead{display:flex;justify-content:space-between;align-items:center;padding:13px 17px;border-bottom:1px solid #20294a;position:sticky;top:0;background:rgba(17,23,43,.96)}
.phead b{font-size:15px}.phead button{background:transparent;border:0;color:#8a93b2;font-size:20px;cursor:pointer;line-height:1}
.pbody{padding:13px 17px}.hint{color:#8a93b2;font-size:11px;margin-bottom:8px}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;text-align:left}
tr+tr{border-top:1px solid #20294a}.rk{color:#8a93b2;width:24px}.val{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.mut{color:#8a93b2}.rider{display:flex;align-items:center;gap:8px}
.av{width:24px;height:24px;border-radius:50%;background:#2a3358;object-fit:cover;vertical-align:middle}
tr.sel{cursor:pointer}tr.sel:hover{background:#1d2647}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tab{background:#1a2240;border:1px solid #2a3358;color:#cdd3e0;border-radius:999px;padding:5px 11px;font-size:12px;cursor:pointer}
.tab.on{background:#2a3a6e;color:#fff}
.podium{display:flex;gap:12px;justify-content:center;align-items:flex-end;padding:6px 0}
.pod{background:#151c33;border:1px solid #2a3358;border-radius:12px;padding:12px;text-align:center;width:150px;cursor:pointer}
.pod:hover{border-color:#5cc8ff}.pod .av{width:56px;height:56px;margin:0 auto 6px;display:block}
.pod.p1{border-color:#ffd24a;margin-bottom:16px}.pod.p2{border-color:#cdd3e0}.pod.p3{border-color:#e0915a;margin-bottom:0}
.pod .km{color:#5cc8ff;font-weight:600}.pod .medal{font-size:20px}
.winpin{width:34px;height:34px;border-radius:50%;border:2px solid #ffd24a;background:#151c33 center/cover;box-shadow:0 0 0 4px rgba(255,210,74,.25),0 0 18px rgba(255,210,74,.6)}
@media(max-width:560px){.dock button .lbl{display:none}.dock button{padding:10px 12px;font-size:18px}.badge span{display:none}}
</style></head><body>
<div id="map"></div>
<div class="topbar">
  <div id="champ" class="champ" style="display:none"></div>
  <div id="chips" class="chips"></div>
</div>
<a class="badge" href="https://github.com/eried/eucstats" target="_blank" rel="noopener">
  <img src="/static/eucplanet.png" alt="eucplanet"/><span>powered by <b>eucplanet</b> · source</span>
</a>
<div class="panel" id="panel"><div class="phead"><b id="ptitle"></b><button id="pclose">✕</button></div><div class="pbody" id="pbody"></div></div>
<div class="dock">
  <button data-p="boards">🏆 <span class="lbl">Leaderboards</span></button>
  <button data-p="podium">🥇 <span class="lbl">Podium</span></button>
  <button data-p="countries">🌍 <span class="lbl">Countries</span></button>
  <button data-p="records">💥 <span class="lbl">Records</span></button>
</div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
const API="/api/v1";
const j=p=>fetch(API+p).then(r=>r.json());
const flag=cc=>cc?cc.toUpperCase().replace(/./g,c=>String.fromCodePoint(127397+c.charCodeAt())):"";
const av=id=>`<img class="av" src="${API}/riders/${encodeURIComponent(id)}/avatar" onerror="this.style.visibility='hidden'"/>`;
const rider=e=>`<span class="rider">${av(e.store_id)}<span>${flag(e.flag)} ${e.name||e.store_id}</span></span>`;
const BOARDS=[
 {k:"mileage",t:"Mileage",c:"total_km",u:" km"},{k:"speed",t:"Top speed",c:"best_speed",u:" km/h"},
 {k:"daily",t:"Biggest day",c:"best_day_km",u:" km"},{k:"streak",t:"Streak",c:"longest_streak",u:" d"},
 {k:"gforce",t:"Max G",c:"best_gforce",u:" g"}];
const RECLABEL={mileage_king:"👑 Mileage King",top_speed:"⚡ Top Speed",longest_trip:"📏 Longest Trip",max_gforce:"💥 Max G-Force"};
const REGION={America:[-98,39,3.4],Europe:[12,52,3.6],Asia:[100,34,2.8],Africa:[21,3,2.8],Australia:[134,-25,3.4],Pacific:[-150,5,2.6],Atlantic:[-30,35,2.6],Indian:[75,-15,2.6],Antarctica:[0,-72,2.5]};

let map, winMarker=null;
function flyToRider(e){
  if(e==null||e.lat==null||e.lon==null||!map) return;
  closePanel();
  if(winMarker) winMarker.remove();
  const el=document.createElement("div"); el.className="winpin";
  el.style.backgroundImage=`url(${API}/riders/${encodeURIComponent(e.store_id)}/avatar)`;
  winMarker=new maplibregl.Marker({element:el}).setLngLat([e.lon,e.lat]).addTo(map);
  map.flyTo({center:[e.lon,e.lat],zoom:9,speed:1.3,curve:1.6,essential:true});
}

const pbody=document.getElementById("pbody"),panel=document.getElementById("panel"),ptitle=document.getElementById("ptitle");
let openPanel=null;
function setPanel(name,title,html){
  if(openPanel===name){closePanel();return;}
  openPanel=name;ptitle.textContent=title;pbody.innerHTML=html;panel.classList.add("open");
  document.querySelectorAll(".dock button").forEach(b=>b.classList.toggle("on",b.dataset.p===name));
}
function closePanel(){openPanel=null;panel.classList.remove("open");document.querySelectorAll(".dock button").forEach(b=>b.classList.remove("on"));}
document.getElementById("pclose").onclick=closePanel;

function showBoards(){
  setPanel("boards","Leaderboards",`<div class="hint">tap a rider to fly there →</div><div class="tabs">${BOARDS.map((b,i)=>`<button class="tab${i?'':' on'}" data-b="${b.k}">${b.t}</button>`).join("")}</div><table><tbody id="lb"></tbody></table>`);
  pbody.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{pbody.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));t.classList.add("on");loadBoard(t.dataset.b);});
  loadBoard("mileage");
}
async function loadBoard(k){
  const b=BOARDS.find(x=>x.k===k),rows=(await j(`/leaderboards/${k}?limit=15`)).entries;
  const tb=document.getElementById("lb");
  tb.innerHTML=rows.length?rows.map((e,i)=>`<tr class="sel" data-i="${i}"><td class=rk>${i+1}</td><td>${rider(e)}</td><td class=val>${e[b.c]??0}${b.u}</td></tr>`).join(""):`<tr><td colspan=3 class=mut>no data yet</td></tr>`;
  tb.querySelectorAll("tr.sel").forEach(tr=>tr.onclick=()=>flyToRider(rows[+tr.dataset.i]));
}
async function showPodium(){
  const m=(await j("/leaderboards/mileage?limit=3")).entries,o=[1,0,2],med=["🥇","🥈","🥉"];
  setPanel("podium","Mileage Kings",`<div class="hint">tap a winner to fly there →</div><div class="podium" id="pod"></div>`);
  document.getElementById("pod").innerHTML=o.filter(i=>m[i]).map(i=>`<div class="pod p${i+1}" data-i="${i}"><div class="medal">${med[i]}</div>${av(m[i].store_id)}<div>${flag(m[i].flag)} ${m[i].name||m[i].store_id}</div><div class="km">${m[i].total_km} km</div></div>`).join("")||"<span class=mut>no riders yet</span>";
  document.querySelectorAll("#pod .pod").forEach(p=>p.onclick=()=>flyToRider(m[+p.dataset.i]));
}
async function showCountries(){
  const cs=await j("/countries");
  setPanel("countries","By country",`<table><tr><th>#</th><th>Country</th><th class=val>Total km</th><th class=val>Riders</th><th class=val>Avg</th></tr>${cs.map((c,i)=>`<tr><td class=rk>${i+1}</td><td>${flag(c.country)} ${c.country}</td><td class=val>${c.total_km}</td><td class=val>${c.riders}</td><td class=val>${c.avg_km_per_rider}</td></tr>`).join("")||"<tr><td colspan=5 class=mut>no data yet</td></tr>"}</table>`);
}
async function showRecords(){
  const recs=await j("/records");
  setPanel("records","Records",`<div class="hint">tap a holder to fly there →</div><table id="rec"></table>`);
  const real=recs.filter(r=>r.value!=null);
  document.getElementById("rec").innerHTML=real.map((r,i)=>`<tr class="sel" data-i="${i}"><td>${RECLABEL[r.key]||r.key}</td><td>${rider(r.rider)}</td><td class=val>${Math.round(r.value*100)/100}</td></tr>`).join("")||"<tr><td class=mut>no records yet</td></tr>";
  document.querySelectorAll("#rec tr.sel").forEach(tr=>tr.onclick=()=>flyToRider(real[+tr.dataset.i].rider));
}
const HANDLERS={boards:showBoards,podium:showPodium,countries:showCountries,records:showRecords};
document.querySelectorAll(".dock button").forEach(b=>b.onclick=()=>HANDLERS[b.dataset.p]());

async function init(){
  const s=await j("/stats/summary");
  document.getElementById("chips").innerHTML=[["Riders",s.riders],["Trips",s.trips],["Total km",s.total_km],["Countries",s.countries]]
    .map(([l,v])=>`<span class="chip"><b>${v}</b> ${l}</span>`).join("");
  const wc=await j("/champions/weekly");
  if(wc&&wc.champion){const c=wc.champion;const ch=document.getElementById("champ");ch.style.display="block";
    ch.innerHTML=`👑 Champion of the week — <b>${flag(c.flag)} ${c.name||c.store_id}</b> · ${c.km} km`;
    ch.style.cursor="pointer";ch.onclick=()=>flyToRider(c);}

  const tz=(Intl.DateTimeFormat().resolvedOptions().timeZone||"").split("/")[0];
  const [clon,clat,cz]=REGION[tz]||[10,30,2.4];
  map=new maplibregl.Map({container:"map",style:"https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    center:[clon,clat],zoom:cz,attributionControl:true});
  map.addControl(new maplibregl.NavigationControl(),"top-right");
  map.on("load",async ()=>{
    const cells=await j("/map/cells?zoom=0.1");
    const maxKm=Math.max(1,...cells.map(c=>c.total_km));
    map.addSource("activity",{type:"geojson",data:{type:"FeatureCollection",
      features:cells.map(c=>({type:"Feature",geometry:{type:"Point",coordinates:[c.lon,c.lat]},properties:{w:c.total_km}}))}});
    map.addLayer({id:"heat",type:"heatmap",source:"activity",paint:{
      "heatmap-weight":["interpolate",["linear"],["get","w"],0,0.05,maxKm,1],
      "heatmap-intensity":["interpolate",["linear"],["zoom"],0,1,9,3],
      "heatmap-radius":["interpolate",["linear"],["zoom"],0,12,9,32],
      "heatmap-opacity":0.85,
      "heatmap-color":["interpolate",["linear"],["heatmap-density"],
        0,"rgba(0,0,0,0)",0.2,"#1f6feb",0.45,"#5cc8ff",0.75,"#ffd24a",1,"#ff5d5d"]}});
    map.addLayer({id:"dots",type:"circle",source:"activity",minzoom:7,paint:{
      "circle-radius":["interpolate",["linear"],["zoom"],7,2,12,7],
      "circle-color":"#5cc8ff","circle-opacity":0.6}});
  });
}
init().catch(()=>{document.getElementById("chips").innerHTML='<span class="chip">API error</span>';});
</script></body></html>"""


@public_router.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(_PAGE)
