"""Public website: GPU (MapLibre GL) world heatmap with a cinematic intro,
monochrome/silhouette UI, openable tool panels, and fly-to-winner. /api/v1."""
import datetime
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

public_router = APIRouter()

_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>EUC Stats — Electric Unicycle rider leaderboards & world heatmap</title>
<meta name="description" content="Live leaderboards, records and a world activity heatmap for electric unicycle riders, powered by EUC Planet."/>
<meta property="og:title" content="EUC Stats — EUC rider leaderboards & world heatmap"/>
<meta property="og:description" content="Live leaderboards, records and a world activity heatmap for electric unicycle riders, powered by EUC Planet."/>
<meta property="og:image" content="https://eucstats.ried.no/static/logo.png"/>
<meta property="og:type" content="website"/>
<meta name="twitter:card" content="summary_large_image"/>
<link rel="icon" type="image/svg+xml" href="/static/euc-favicon.svg"/>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
<style>
:root{--ink:#eef1fb;--mut:#9aa6c8;--acc:#2ea8ff;--gold:#ffd24a;--line:#33457a;--surf:linear-gradient(158deg,rgba(34,52,100,.92),rgba(9,14,30,.95));--glass:rgba(13,17,32,.82);--shadow:0 12px 34px rgba(0,0,0,.6),inset 0 1px 0 rgba(130,170,255,.14)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font:14px/1.45 ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;color:var(--ink);background:#070a16;overflow:hidden}
#map{position:fixed;inset:0;z-index:0}
.maplibregl-ctrl-attrib{font-size:9px;opacity:.4}.maplibregl-ctrl-group{background:var(--glass)!important;border:1px solid var(--line)!important}
svg.ic{width:18px;height:18px;display:block}
.intro{opacity:0}
.intro.show{opacity:1;transition:opacity 1s ease}
.topbar{position:fixed;top:16px;left:16px;z-index:500;display:flex;flex-direction:column;gap:9px;max-width:min(92vw,460px)}
.champ{display:inline-flex;align-items:center;gap:8px;align-self:flex-start;background:var(--surf);backdrop-filter:blur(10px);border:1px solid rgba(255,210,74,.42);border-radius:8px;padding:9px 13px;font-size:13px;letter-spacing:.2px;box-shadow:var(--shadow),0 0 18px rgba(255,210,74,.12)}
.champ svg{width:16px;height:16px;color:var(--gold)}.champ b{font-weight:700;color:var(--gold)}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{background:var(--surf);backdrop-filter:blur(10px);border:1px solid var(--line);border-radius:7px;padding:6px 12px;font-size:12px;color:var(--mut);letter-spacing:.3px;box-shadow:var(--shadow)}
.chip b{color:var(--acc);font-weight:700}
.pside,.gside{position:fixed;bottom:22px;z-index:500;display:flex;align-items:center;gap:9px;white-space:nowrap;color:var(--mut);font-size:12px;letter-spacing:.6px;text-decoration:none}
.pside:hover{color:var(--ink)}.pside:hover b{color:var(--acc)}
.pside b,.gside b{color:var(--ink);font-weight:700}
.pside{left:22px;transform-origin:left bottom;transform:rotate(-90deg)}
.pside img{width:18px;height:18px;transform:rotate(90deg)}
.gside{right:22px;transform-origin:right bottom;transform:rotate(90deg)}
.gside a{display:flex;align-items:center;gap:6px;color:var(--mut);text-decoration:none;transition:color .2s}
.gside a:hover{color:var(--acc)}.gside svg{width:14px;height:14px;transform:rotate(-90deg)}
.gside .ver{font-family:ui-monospace,monospace;font-size:11px}
.dock{position:fixed;left:50%;transform:translateX(-50%);bottom:18px;z-index:600;display:flex;gap:4px;background:var(--surf);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:10px;padding:7px;box-shadow:var(--shadow),0 18px 54px rgba(0,0,0,.55)}
.dock button{display:flex;align-items:center;gap:8px;background:transparent;color:var(--ink);border:0;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:600;letter-spacing:.3px;cursor:pointer;transition:background .15s,color .15s}
.dock button:hover{background:rgba(255,255,255,.06)}.dock button.on{background:rgba(46,168,255,.16);color:var(--acc)}
.dock button.on svg{color:var(--acc)}
.panel{position:fixed;left:50%;bottom:84px;transform:translateX(-50%) translateY(150%);opacity:0;visibility:hidden;z-index:550;width:min(94vw,720px);max-height:60vh;overflow:auto;background:linear-gradient(158deg,rgba(26,40,78,.96),rgba(8,12,26,.97));backdrop-filter:blur(18px);border:1px solid var(--line);border-radius:12px;box-shadow:0 30px 90px rgba(0,0,0,.65);transition:transform .32s cubic-bezier(.2,.8,.2,1),opacity .26s}
.panel.open{transform:translateX(-50%) translateY(0);opacity:1;visibility:visible}
.phead{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(11,15,28,.97)}
.phead b{font-size:14px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut)}.phead button{background:transparent;border:0;color:var(--mut);cursor:pointer}
.pbody{padding:12px 18px}.hint{color:var(--mut);font-size:11px;margin-bottom:9px;letter-spacing:.3px}
table{width:100%;border-collapse:collapse}td,th{padding:7px 8px;text-align:left}
tr+tr{border-top:1px solid #1b2240}.rk{color:var(--acc);width:26px;font-weight:700;font-variant-numeric:tabular-nums}
.val{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.mut{color:var(--mut)}.rider{display:flex;align-items:center;gap:9px}
.av{width:24px;height:24px;border-radius:50%;background:#1b2240;object-fit:cover;flex:0 0 auto}
.cc{font:600 10px/1.6 ui-monospace,monospace;color:var(--mut);border:1px solid var(--line);border-radius:4px;padding:0 4px;letter-spacing:.5px}
tr.sel{cursor:pointer}tr.sel:hover{background:rgba(46,168,255,.08)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:11px}
.tab{background:transparent;border:1px solid var(--line);color:var(--mut);border-radius:7px;padding:5px 12px;font-size:12px;cursor:pointer;letter-spacing:.3px}
.tab.on{background:rgba(46,168,255,.16);border-color:var(--acc);color:var(--acc)}
.podium{display:flex;gap:12px;justify-content:center;align-items:flex-end;padding:8px 0}
.pod{background:var(--surf);border:1px solid var(--line);border-top-width:3px;border-radius:9px;padding:14px 12px;text-align:center;width:148px;cursor:pointer;transition:transform .15s;box-shadow:var(--shadow)}
.pod:hover{transform:translateY(-3px)}.pod .av{width:54px;height:54px;margin:0 auto 8px;display:block}
.pod.p1{border-top-color:var(--gold);margin-bottom:18px}.pod.p2{border-top-color:#cdd3e0}.pod.p3{border-top-color:#b07a4a;margin-bottom:0}
.pod .km{color:var(--acc);font-weight:700;margin-top:3px}.pod .rkn{color:var(--mut);font:700 12px/1 ui-monospace,monospace;letter-spacing:1px}
.winpin{width:36px;height:36px;border-radius:50%;border:2px solid var(--acc);background:#0e1326 center/cover;box-shadow:0 0 0 4px rgba(46,168,255,.22),0 0 20px rgba(46,168,255,.65)}
@media(max-width:560px){.dock button .lbl{display:none}.dock button{padding:11px}.badge b~span,.badge span{}}
</style></head><body>
<div id="map"></div>
<div class="topbar">
  <div id="champ" class="champ intro" style="display:none"></div>
  <div id="chips" class="chips intro"></div>
</div>
<a class="pside intro" href="https://eucplanet.ried.no" target="_blank" rel="noopener"><img src="/static/euc-planet.svg" alt=""/><span>powered by <b>EUC&nbsp;Planet</b></span></a>
<div class="gside intro">
  <a href="https://github.com/eried/eucstats" target="_blank" rel="noopener" title="View / contribute on GitHub"><svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg><span>GitHub</span></a>
  <span class="ver" title="HTML last-modified date (auto-updated on deploy)">build __BUILD__</span>
</div>
<div class="panel" id="panel"><div class="phead"><b id="ptitle"></b><button id="pclose"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div><div class="pbody" id="pbody"></div></div>
<div class="dock intro">
  <button class="intro" data-p="boards" title="Leaderboards"><svg class="ic" viewBox="0 0 24 24" fill="currentColor"><rect x="3" y="10" width="4" height="11" rx="1"/><rect x="10" y="3" width="4" height="18" rx="1"/><rect x="17" y="13" width="4" height="8" rx="1"/></svg><span class="lbl">Leaderboards</span></button>
  <button class="intro" data-p="podium" title="Podium"><svg class="ic" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h12v3a4 4 0 0 1-3 3.87V13h2v2H7v-2h2v-2.13A4 4 0 0 1 6 7V4Zm-3 2h2v2a2 2 0 0 1-2-2Zm16 0a2 2 0 0 1-2 2V6h2ZM7 17h10v3H7z"/></svg><span class="lbl">Podium</span></button>
  <button class="intro" data-p="countries" title="Countries"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18"/></svg><span class="lbl">Countries</span></button>
  <button class="intro" data-p="records" title="Records"><svg class="ic" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/></svg><span class="lbl">Records</span></button>
</div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
const API="/api/v1";
const j=p=>fetch(API+p).then(r=>r.json());
const cc=c=>c?`<span class="cc">${c}</span>`:"";
const av=id=>`<img class="av" src="${API}/riders/${encodeURIComponent(id)}/avatar" onerror="this.style.visibility='hidden'"/>`;
const rider=e=>`<span class="rider">${av(e.store_id)}${cc(e.flag)}<span>${e.name||e.store_id}</span></span>`;
const CROWN='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 7l4.5 4L12 4l4.5 7L21 7l-1.8 12H4.8L3 7Z"/></svg>';
const BOARDS=[
 {k:"mileage",t:"Mileage",c:"total_km",u:" km"},{k:"speed",t:"Top speed",c:"best_speed",u:" km/h"},
 {k:"daily",t:"Biggest day",c:"best_day_km",u:" km"},{k:"streak",t:"Streak",c:"longest_streak",u:" d"},
 {k:"gforce",t:"Max G",c:"best_gforce",u:" g"}];
const RECLABEL={mileage_king:"Mileage King",top_speed:"Top Speed",longest_trip:"Longest Trip",max_gforce:"Max G-Force"};
const REGION={America:[-98,39,4],Europe:[12,52,4.2],Asia:[100,34,3.2],Africa:[21,3,3.2],Australia:[134,-25,4],Pacific:[-150,5,3],Atlantic:[-30,35,3],Indian:[75,-15,3],Antarctica:[0,-72,2.6]};
// city/country-level centers so we land near the visitor (e.g. Oslo, not central Europe)
const TZMAP={
 "Europe/Oslo":[10,62,4.3],"Europe/Stockholm":[16,62,4.3],"Europe/Helsinki":[25,63,4.2],"Europe/Copenhagen":[11,56,5],
 "Europe/London":[-2,54,4.7],"Europe/Dublin":[-8,53,5],"Europe/Berlin":[10,51,4.7],"Europe/Paris":[2,47,4.7],
 "Europe/Madrid":[-4,40,4.7],"Europe/Lisbon":[-8,40,5],"Europe/Rome":[12,42,4.7],"Europe/Amsterdam":[5,52,5.2],
 "Europe/Brussels":[4,50,5.2],"Europe/Zurich":[8,47,5.4],"Europe/Vienna":[15,48,5],"Europe/Warsaw":[20,52,4.7],
 "Europe/Prague":[15,50,5.2],"Europe/Athens":[23,39,4.8],"Europe/Moscow":[38,56,3.6],"Europe/Kyiv":[31,49,4.4],
 "Europe/Istanbul":[35,39,4.6],"Asia/Istanbul":[35,39,4.6],
 "America/New_York":[-75,40,4.7],"America/Toronto":[-79,44,4.7],"America/Chicago":[-90,40,4.4],
 "America/Denver":[-105,39,4.7],"America/Los_Angeles":[-119,37,4.7],"America/Vancouver":[-123,49,4.7],
 "America/Mexico_City":[-99,20,4.7],"America/Sao_Paulo":[-47,-22,4.4],"America/Bogota":[-74,4,4.6],
 "America/Argentina/Buenos_Aires":[-58,-35,4.2],"America/Santiago":[-71,-34,4.4],
 "Asia/Tokyo":[138,37,4.5],"Asia/Seoul":[127,36,5],"Asia/Shanghai":[113,32,3.8],"Asia/Hong_Kong":[114,22,5.2],
 "Asia/Singapore":[104,1.3,5.4],"Asia/Bangkok":[101,14,4.6],"Asia/Kolkata":[78,22,3.9],"Asia/Dubai":[54,24,5],
 "Asia/Jerusalem":[35,31,5.4],"Australia/Sydney":[151,-33,4.7],"Australia/Melbourne":[145,-37,4.7],
 "Australia/Perth":[116,-32,4.7],"Pacific/Auckland":[174,-41,4.6],"Africa/Johannesburg":[26,-29,4.6],
 "Africa/Cairo":[31,27,4.4],"Africa/Lagos":[6,8,4.6],"Africa/Nairobi":[37,-1,4.6]};
const easeInOutCubic=t=>t<0.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;

let map;
// FNV-1a string hash -> deterministic noise per (today + rider), so a winner's
// area is stable for the day but never their exact location (privacy).
function hashStr(s){let h=2166136261>>>0;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)>>>0;}return h>>>0;}
function ringCoords(lon,lat,km,n=72){const c=[],k=Math.cos(lat*Math.PI/180)||1e-6;for(let i=0;i<=n;i++){const a=2*Math.PI*i/n;c.push([lon+(km*Math.cos(a))/(111*k),lat+(km*Math.sin(a))/111]);}return c;}
const DASH=[[0,4,3],[0.5,4,2.5],[1,4,2],[1.5,4,1.5],[2,4,1],[2.5,4,0.5],[3,4,0],[0,0.5,3,3.5],[0,1,3,3],[0,1.5,3,2.5],[0,2,3,2],[0,2.5,3,1.5],[0,3,3,1],[0,3.5,3,0.5]];
let dashStep=-1,dashRunning=false;
function animateDash(){ if(map&&map.getLayer("area-line")){const s=Math.floor((performance.now()/45)%DASH.length); if(s!==dashStep){map.setPaintProperty("area-line","line-dasharray",DASH[s]);dashStep=s;}} requestAnimationFrame(animateDash); }
function showArea(lon,lat,km){
  const ring={type:"Feature",geometry:{type:"LineString",coordinates:ringCoords(lon,lat,km)}};
  if(map.getSource("area")){map.getSource("area").setData(ring);}
  else{
    map.addSource("area",{type:"geojson",data:ring});
    map.addLayer({id:"area-glow",type:"line",source:"area",paint:{"line-color":"#ffd24a","line-width":9,"line-blur":9,"line-opacity":0.3}});
    map.addLayer({id:"area-line",type:"line",source:"area",paint:{"line-color":"#ffd24a","line-width":2.5,"line-dasharray":[0,4,3]}});
  }
  if(!dashRunning){dashRunning=true;animateDash();}
}
function flyToRider(e){
  if(!e||e.lat==null||e.lon==null||!map) return;
  closePanel();
  const key=new Date().toISOString().slice(0,10)+"|"+e.store_id;       // changes daily, stable within a day
  const dxKm=((hashStr(key+"x")%8000)/100)-40, dyKm=((hashStr(key+"y")%8000)/100)-40;  // ~ -40..+40 km each axis
  const olat=e.lat+dyKm/111, olon=e.lon+dxKm/(111*(Math.cos(e.lat*Math.PI/180)||1e-6));
  showArea(olon,olat,14);                                              // dotted ring marking the noised area
  map.flyTo({center:[olon,olat],zoom:9.2,curve:1.9,duration:2800,easing:easeInOutCubic,essential:true});
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
  setPanel("boards","Leaderboards",`<div class="hint">TAP A RIDER TO FLY THERE</div><div class="tabs">${BOARDS.map((b,i)=>`<button class="tab${i?'':' on'}" data-b="${b.k}">${b.t}</button>`).join("")}</div><table><tbody id="lb"></tbody></table>`);
  pbody.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{pbody.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));t.classList.add("on");loadBoard(t.dataset.b);});
  loadBoard("mileage");
}
async function loadBoard(k){
  const b=BOARDS.find(x=>x.k===k),rows=(await j(`/leaderboards/${k}?limit=15`)).entries,tb=document.getElementById("lb");
  tb.innerHTML=rows.length?rows.map((e,i)=>`<tr class="sel" data-i="${i}"><td class=rk>${i+1}</td><td>${rider(e)}</td><td class=val>${e[b.c]??0}${b.u}</td></tr>`).join(""):`<tr><td colspan=3 class=mut>no data yet</td></tr>`;
  tb.querySelectorAll("tr.sel").forEach(tr=>tr.onclick=()=>flyToRider(rows[+tr.dataset.i]));
}
async function showPodium(){
  const m=(await j("/leaderboards/mileage?limit=3")).entries,o=[1,0,2],rkn=["1ST","2ND","3RD"];
  setPanel("podium","Mileage Kings",`<div class="hint">TAP A WINNER TO FLY THERE</div><div class="podium" id="pod"></div>`);
  document.getElementById("pod").innerHTML=o.filter(i=>m[i]).map(i=>`<div class="pod p${i+1}" data-i="${i}"><div class="rkn">${rkn[i]}</div>${av(m[i].store_id)}<div>${cc(m[i].flag)} ${m[i].name||m[i].store_id}</div><div class="km">${m[i].total_km} km</div></div>`).join("")||"<span class=mut>no riders yet</span>";
  document.querySelectorAll("#pod .pod").forEach(p=>p.onclick=()=>flyToRider(m[+p.dataset.i]));
}
async function showCountries(){
  const cs=await j("/countries");
  setPanel("countries","By country",`<table><tr><th>#</th><th>Country</th><th class=val>Total km</th><th class=val>Riders</th><th class=val>Avg</th></tr>${cs.map((c,i)=>`<tr><td class=rk>${i+1}</td><td>${cc(c.country)}</td><td class=val>${c.total_km}</td><td class=val>${c.riders}</td><td class=val>${c.avg_km_per_rider}</td></tr>`).join("")||"<tr><td colspan=5 class=mut>no data yet</td></tr>"}</table>`);
}
async function showRecords(){
  const recs=(await j("/records")).filter(r=>r.value!=null);
  setPanel("records","Records",`<div class="hint">TAP A HOLDER TO FLY THERE</div><table id="rec"></table>`);
  document.getElementById("rec").innerHTML=recs.map((r,i)=>`<tr class="sel" data-i="${i}"><td>${RECLABEL[r.key]||r.key}</td><td>${rider(r.rider)}</td><td class=val>${Math.round(r.value*100)/100}</td></tr>`).join("")||"<tr><td class=mut>no records yet</td></tr>";
  document.querySelectorAll("#rec tr.sel").forEach(tr=>tr.onclick=()=>flyToRider(recs[+tr.dataset.i].rider));
}
const HANDLERS={boards:showBoards,podium:showPodium,countries:showCountries,records:showRecords};
document.querySelectorAll(".dock button").forEach(b=>b.onclick=()=>HANDLERS[b.dataset.p]());

function reveal(el,d){ if(el) setTimeout(()=>el.classList.add("show"),d); }
function runIntro(){
  reveal(document.getElementById("champ"),1100);
  reveal(document.getElementById("chips"),1700);
  reveal(document.querySelector(".dock"),2300);
  document.querySelectorAll(".dock button").forEach((b,i)=>reveal(b,2700+i*320));
  reveal(document.querySelector(".pside"),4100);
  reveal(document.querySelector(".gside"),4450);
}

async function init(){
  const s=await j("/stats/summary");
  document.getElementById("chips").innerHTML=[["Riders",s.riders],["Trips",s.trips],["Total km",s.total_km],["Countries",s.countries]]
    .map(([l,v])=>`<span class="chip"><b>${v}</b> ${l}</span>`).join("");
  const wc=await j("/champions/weekly");
  if(wc&&wc.champion){const c=wc.champion,ch=document.getElementById("champ");ch.style.display="inline-flex";ch.style.cursor="pointer";
    ch.innerHTML=`${CROWN}<span>Champion of the week — <b>${c.name||c.store_id}</b> ${cc(c.flag)} · ${c.km} km</span>`;
    ch.onclick=()=>flyToRider(c);}

  const tzFull=Intl.DateTimeFormat().resolvedOptions().timeZone||"";
  const [rlon,rlat,rz]=TZMAP[tzFull]||REGION[tzFull.split("/")[0]]||[10,30,3];
  map=new maplibregl.Map({container:"map",style:"https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    center:[rlon,rlat],zoom:1.5,attributionControl:true});   // start zoomed out over the visitor's region
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),"top-right");
  map.on("load",async ()=>{
    const cells=await j("/map/cells?zoom=0.5");   // coarser, smoother grid
    map.addSource("activity",{type:"geojson",data:{type:"FeatureCollection",
      features:cells.map(c=>({type:"Feature",geometry:{type:"Point",coordinates:[c.lon,c.lat]},properties:{r:c.rider_count||0}}))}});
    map.addLayer({id:"heat",type:"heatmap",source:"activity",paint:{
      // logarithmic: ~10 riders in an area -> full intensity; a lone rider stays faint
      "heatmap-weight":["min",1,["/",["ln",["+",1,["get","r"]]],["ln",11]]],
      "heatmap-intensity":["interpolate",["linear"],["zoom"],0,0.6,9,1.8],
      "heatmap-radius":["interpolate",["linear"],["zoom"],0,24,5,46,12,76],
      "heatmap-opacity":0.82,
      "heatmap-color":["interpolate",["linear"],["heatmap-density"],
        0,"rgba(0,0,0,0)",0.12,"#11317a",0.32,"#1f6feb",0.55,"#2ec5ff",0.78,"#ffd24a",1,"#ff5d5d"]}});
    setTimeout(()=>map.flyTo({center:[rlon,rlat],zoom:rz,duration:5000,curve:1.5,easing:easeInOutCubic,essential:true}),450);
    runIntro();
  });
}
init().catch(()=>{const c=document.getElementById("chips");c.classList.add("show");c.innerHTML='<span class="chip">API error</span>';});
</script></body></html>"""


def _build_date():
    try:
        return datetime.datetime.utcfromtimestamp(os.path.getmtime(__file__)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


@public_router.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(_PAGE.replace("__BUILD__", _build_date()))
