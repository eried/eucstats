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
<title>EUC Stats | Leaderboards & Heatmap</title>
<meta name="description" content="Live leaderboards, records and a world activity heatmap for electric unicycle riders, powered by EUC Planet."/>
<meta property="og:title" content="EUC Stats · EUC rider leaderboards & world heatmap"/>
<meta property="og:description" content="Live leaderboards, records and a world activity heatmap for electric unicycle riders, powered by EUC Planet."/>
<meta property="og:image" content="https://eucstats.ried.no/static/logo.png"/>
<meta property="og:type" content="website"/>
<meta name="twitter:card" content="summary_large_image"/>
<link rel="icon" type="image/png" href="/static/favicon.png"/>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=Orbitron:wght@600;700;800&display=swap" rel="stylesheet"/>
<style>
:root{--ink:#eef1fb;--mut:#9aa6c8;--acc:#2ea8ff;--gold:#ffd24a;--line:#33457a;--surf:linear-gradient(158deg,rgba(34,52,100,.92),rgba(9,14,30,.95));--glass:rgba(13,17,32,.82);--shadow:0 12px 34px rgba(0,0,0,.6),inset 0 1px 0 rgba(130,170,255,.14)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font:14px/1.45 "Chakra Petch",ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;color:var(--ink);background:#070a16;overflow:hidden}
#ptitle,.champ,.chead,.chip b,.val,.rk,.pod .km,.pod .rkn,.cscore,.clab,.tab span,.dock .lbl{font-family:"Orbitron",ui-sans-serif,sans-serif;letter-spacing:.5px}
#map{position:fixed;inset:0;z-index:0}
#veil{position:fixed;inset:0;z-index:1;pointer-events:none;transition:opacity .25s linear;background:linear-gradient(to bottom,rgba(0,0,0,.5) 0%,rgba(0,0,0,.12) 28%,rgba(0,0,0,0) 50%),radial-gradient(ellipse 80% 84% at 50% 48%,rgba(0,0,0,0) 40%,rgba(0,0,0,.74) 100%)}
#dots{position:fixed;inset:0;z-index:1;pointer-events:none;mix-blend-mode:multiply;opacity:.22;background-image:radial-gradient(circle at center,rgba(0,0,0,.8) 0,rgba(0,0,0,.8) .7px,transparent 1.2px);background-size:4px 4px}
#intro{position:fixed;inset:0;z-index:3000;width:100%;height:100%;object-fit:cover;background:#000;filter:blur(.5px) saturate(1.08) contrast(1.04);clip-path:circle(150% at 50% 50%);transition:clip-path 1.6s cubic-bezier(.65,0,.35,1)}#intro.done{clip-path:circle(0% at 50% 50%);pointer-events:none}
#introfx{position:fixed;inset:0;z-index:3001;pointer-events:none;overflow:hidden;clip-path:circle(150% at 50% 50%);transition:clip-path 1.6s cubic-bezier(.65,0,.35,1);background:radial-gradient(ellipse 80% 84% at 50% 47%,rgba(0,0,0,0) 38%,rgba(0,0,0,.34) 72%,rgba(0,0,0,.7) 100%),linear-gradient(to bottom,rgba(0,0,0,.34),rgba(0,0,0,0) 20%,rgba(0,0,0,0) 80%,rgba(0,0,0,.42))}#introfx.done{clip-path:circle(0% at 50% 50%)}
#introfx::after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(0,0,0,.45) 0,rgba(0,0,0,.45) 2px,rgba(130,180,255,.06) 2px,rgba(130,180,255,.06) 4px);background-size:100% 4px;mix-blend-mode:multiply;opacity:.72;animation:scan 2.5s linear infinite}
#introfx::before{content:"";position:absolute;inset:-12px;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='110' height='110'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");background-size:130px 130px;mix-blend-mode:overlay;opacity:.32;animation:grain .9s steps(4) infinite}
@keyframes scan{from{background-position:0 0}to{background-position:0 3px}}
@keyframes grain{0%{transform:translate(0,0)}25%{transform:translate(-4px,3px)}50%{transform:translate(3px,-4px)}75%{transform:translate(-3px,-2px)}100%{transform:translate(0,0)}}
.cbtn{background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:8px;color:var(--mut);font-size:11px;padding:6px 11px;cursor:pointer;letter-spacing:.4px;transition:color .2s,border-color .2s,background .2s}
.cbtn:hover{color:var(--acc);border-color:var(--acc)}
.cbtn:disabled,.cbtn.on{color:var(--gold);border-color:rgba(255,210,74,.55);background:rgba(255,210,74,.12);cursor:default}
tr.gold1{background-image:linear-gradient(100deg,rgba(255,210,74,.10) 0,rgba(255,222,120,.14) 30%,rgba(255,240,175,.42) 50%,rgba(255,222,120,.14) 70%,rgba(255,210,74,.10) 100%)!important;background-size:300% 100%;background-repeat:no-repeat}
.pod.gold1{position:relative;overflow:hidden;background:linear-gradient(158deg,rgba(74,60,22,.95),rgba(28,21,6,.96))!important;border-top-color:var(--gold)}
.pod.gold1::after{content:"";position:absolute;top:0;left:-130%;width:120%;height:100%;background:linear-gradient(100deg,transparent 0,rgba(255,238,170,0) 30%,rgba(255,238,170,.42) 50%,rgba(255,238,170,0) 70%,transparent 100%);transform:skewX(-16deg);animation:shinesweep 5.5s ease-in-out infinite;pointer-events:none}
@keyframes shinebg{0%{background-position:160% 0}16%{background-position:-60% 0}100%{background-position:-60% 0}}
@keyframes shinesweep{0%{left:-130%}20%{left:140%}100%{left:140%}}
.pod.silv{position:relative;overflow:hidden;background:linear-gradient(158deg,rgba(62,66,78,.95),rgba(20,23,30,.96))!important;border-top-color:#cdd3e0}
.pod.silv::after{content:"";position:absolute;top:0;left:-130%;width:120%;height:100%;background:linear-gradient(100deg,transparent 0,rgba(228,234,246,0) 30%,rgba(228,234,246,.4) 50%,rgba(228,234,246,0) 70%,transparent 100%);transform:skewX(-16deg);animation:shinesweep 5.5s ease-in-out .9s infinite;pointer-events:none}
.pod.brnz{position:relative;overflow:hidden;background:linear-gradient(158deg,rgba(78,54,30,.95),rgba(28,18,9,.96))!important;border-top-color:#b07a4a}
.pod.brnz::after{content:"";position:absolute;top:0;left:-130%;width:120%;height:100%;background:linear-gradient(100deg,transparent 0,rgba(230,170,112,0) 30%,rgba(230,170,112,.38) 50%,rgba(230,170,112,0) 70%,transparent 100%);transform:skewX(-16deg);animation:shinesweep 5.5s ease-in-out 1.8s infinite;pointer-events:none}
.maplibregl-ctrl-attrib{background:none!important;box-shadow:none!important;font-size:9px;opacity:.4}.maplibregl-ctrl-attrib a{color:#7a86ad;text-shadow:0 1px 2px #000}.maplibregl-ctrl-attrib-button{display:none!important}.maplibregl-ctrl-group{background:var(--glass)!important;border:1px solid var(--line)!important}
svg.ic{width:18px;height:18px;display:block}
.intro{opacity:0}
.intro.show{opacity:1;transition:opacity 1s ease}
@keyframes rowin{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
.topbar{position:fixed;top:16px;left:16px;z-index:500;max-width:min(92vw,380px);background:var(--surf);backdrop-filter:blur(10px);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}
.champ{display:block;padding:10px 14px 11px;font-size:13px;border-bottom:1px solid var(--line);background:rgba(255,210,74,.06)}
.champ svg{width:16px;height:16px;color:var(--gold)}.champ b{font-weight:700;color:var(--gold)}
.chead{display:flex;align-items:center;gap:7px;font-size:10.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--gold);margin-bottom:5px}
.chead>span{flex:1}
.cflag{width:17px;height:17px;overflow:visible}
.cflagwave{transform-box:fill-box;transform-origin:left center;animation:wave 2.6s ease-in-out infinite}
@keyframes wave{0%,100%{transform:skewY(0)}25%{transform:skewY(-5deg)}50%{transform:skewY(0)}75%{transform:skewY(5deg)}}
.cinfo{background:none;border:0;color:var(--mut);cursor:pointer;font-size:13px;line-height:1;padding:0}
.cinfo:hover{color:var(--gold)}
.cline{display:flex;align-items:center;gap:6px;font-size:12.5px;padding:2px 0}
.cline .clab{width:42px;min-width:42px;font-size:9.5px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut)}
.cline b{color:var(--gold);font-weight:700;flex:1;min-width:0;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cscore{margin-left:auto;color:var(--acc);font-weight:700;font-size:12px}
.cformula{margin-top:7px;font-size:10.5px;line-height:1.45;color:var(--ink);background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:7px;padding:7px 9px}
.cformula b{color:var(--gold)}
.chead>span{flex:1;background:linear-gradient(90deg,#caa12f,#fff3c0,#ffd24a,#fff3c0,#caa12f);background-size:220% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;animation:goldflow 4.5s linear infinite}
@keyframes goldflow{0%{background-position:0 0}100%{background-position:220% 0}}
#tip{position:fixed;z-index:600;max-width:240px;background:linear-gradient(158deg,rgba(26,40,78,.98),rgba(8,12,26,.99));border:1px solid var(--line);border-radius:9px;box-shadow:0 16px 50px rgba(0,0,0,.6);padding:8px 11px;font-size:11.5px;line-height:1.45;color:var(--ink);pointer-events:none;opacity:0;transform:translateY(4px);transition:opacity .15s,transform .15s}
#tip.on{opacity:1;transform:translateY(0)}#tip b{color:var(--gold)}
.tab.on{animation:tabpop .4s ease}
@keyframes tabpop{0%{transform:scale(1)}40%{transform:scale(1.1)}100%{transform:scale(1)}}
.chips{display:flex;flex-wrap:wrap}
.chip{flex:1 1 auto;padding:9px 14px;font-size:11px;color:var(--mut);letter-spacing:.3px;border-right:1px solid var(--line);white-space:nowrap}
.chip:last-child{border-right:0}.chip b{display:block;color:var(--acc);font-weight:700;font-size:16px;letter-spacing:0}
.rfoot{position:fixed;right:8px;top:50%;transform:translateY(-50%);z-index:500;writing-mode:vertical-rl;display:flex;flex-direction:row;align-items:center;gap:22px;white-space:nowrap;color:var(--mut);font-size:12px;letter-spacing:.6px}
.rfoot b{color:var(--ink);font-weight:700}
.rfoot a{display:inline-flex;flex-direction:row;align-items:center;gap:7px;color:var(--mut);text-decoration:none;transition:color .2s}
.rfoot a:hover{color:var(--acc)}.rfoot a:hover b{color:var(--acc)}
.rfoot.show{opacity:.4;transition:opacity .35s ease}.rfoot.show:hover{opacity:1}
.rfoot img{width:18px;height:18px}.rfoot svg{width:14px;height:14px}
.rfoot .ver{font-family:ui-monospace,monospace;font-size:11px}
.dock{position:fixed;left:50%;transform:translateX(-50%);bottom:18px;z-index:600;display:flex;gap:4px;background:var(--surf);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:10px;padding:7px;box-shadow:var(--shadow),0 18px 54px rgba(0,0,0,.55)}
.dock button{display:flex;align-items:center;gap:8px;background:transparent;color:var(--ink);border:0;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:600;letter-spacing:.3px;cursor:pointer;transition:background .15s,color .15s}
.dock button:hover{background:rgba(255,255,255,.06)}.dock button.on{background:rgba(46,168,255,.16);color:var(--acc)}
.dock button.on svg{color:var(--acc)}
.panel{position:fixed;left:50%;bottom:84px;transform:translateX(-50%) translateY(150%);opacity:0;visibility:hidden;z-index:550;width:min(94vw,720px);max-height:60vh;overflow:auto;background:linear-gradient(158deg,rgba(26,40,78,.96),rgba(8,12,26,.97));backdrop-filter:blur(18px);border:1px solid var(--line);border-radius:12px;box-shadow:0 30px 90px rgba(0,0,0,.65);transition:transform .32s cubic-bezier(.2,.8,.2,1),opacity .26s}
.panel.open{transform:translateX(-50%) translateY(0);opacity:1;visibility:visible}
.phead{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(11,15,28,.97)}
.phead b{font-size:14px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut)}.phead button{background:transparent;border:0;color:var(--mut);cursor:pointer}
.pbody{padding:12px 18px}.hint{color:var(--mut);font-size:11.5px;margin:2px 0 12px;letter-spacing:.3px;border-left:2px solid var(--acc);padding-left:8px}
table{width:100%;border-collapse:collapse}td,th{padding:7px 8px;text-align:left}
tr+tr{border-top:1px solid #1b2240}.rk{color:var(--acc);width:26px;font-weight:700;font-variant-numeric:tabular-nums}
.val{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.mut{color:var(--mut)}.rider{display:flex;align-items:center;gap:9px}
.av{width:24px;height:24px;border-radius:50%;background:#1b2240;object-fit:cover;flex:0 0 auto;box-shadow:0 0 0 2px rgba(255,255,255,.85)}
.avph{background:linear-gradient(135deg,#2a3566,#141a30)}
.flag{width:20px;height:15px;border-radius:2px;object-fit:cover;vertical-align:middle;box-shadow:0 0 0 1px rgba(0,0,0,.45);flex:0 0 auto}
tr.sel{cursor:pointer}tr.sel:hover{background:rgba(46,168,255,.08)}
.tabs{display:grid;grid-template-columns:repeat(auto-fit,minmax(116px,1fr));gap:6px;margin-bottom:2px}
.tab{display:flex;align-items:center;justify-content:flex-start;gap:7px;background:transparent;border:1px solid var(--line);color:var(--mut);border-radius:7px;padding:7px 10px;font-size:12px;cursor:pointer;letter-spacing:.3px}
.tab svg{flex:0 0 auto}
.tab svg{width:14px;height:14px}
.tab.on{background:rgba(46,168,255,.16);border-color:var(--acc);color:var(--acc)}
.podium{display:flex;gap:12px;justify-content:center;align-items:flex-end;padding:8px 0}
.pod{background:var(--surf);border:1px solid var(--line);border-top-width:3px;border-radius:9px;padding:14px 12px;text-align:center;width:148px;cursor:pointer;transition:transform .15s;box-shadow:var(--shadow)}
.pod:hover{transform:translateY(-3px)}.pod .av{width:54px;height:54px;margin:0 auto 8px;display:block}
.pod.p1{border-top-color:var(--gold);margin-bottom:18px}.pod.p2{border-top-color:#cdd3e0}.pod.p3{border-top-color:#b07a4a;margin-bottom:0}
.pod .km{color:var(--acc);font-weight:700;margin-top:3px}.pod .rkn{color:var(--mut);font:700 12px/1 ui-monospace,monospace;letter-spacing:1px}
.winpin{width:36px;height:36px;border-radius:50%;border:2px solid var(--acc);background:#0e1326 center/cover;box-shadow:0 0 0 4px rgba(46,168,255,.22),0 0 20px rgba(46,168,255,.65)}
#gear{position:fixed;left:14px;bottom:14px;z-index:560;width:38px;height:38px;display:flex;align-items:center;justify-content:center;background:var(--surf);border:1px solid var(--line);border-radius:9px;color:var(--mut);cursor:pointer;box-shadow:var(--shadow);transition:color .2s}
#gear:hover{color:var(--acc)}#gear svg{width:18px;height:18px}
#gear.show{opacity:.45;transition:opacity .3s ease,color .2s}#gear.show:hover{opacity:1}
.cfgpop{position:fixed;left:14px;bottom:60px;z-index:560;display:none;flex-direction:column;gap:12px;min-width:240px;background:linear-gradient(158deg,rgba(26,40,78,.96),rgba(8,12,26,.97));backdrop-filter:blur(16px);border:1px solid var(--line);border-radius:12px;box-shadow:0 24px 70px rgba(0,0,0,.6);padding:14px}
.cfgpop.open{display:flex}
.crow{display:flex;align-items:center;justify-content:space-between;gap:14px;font-size:12px;color:var(--mut);letter-spacing:.4px}
.seg{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:4px;background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:8px;padding:3px}
.seg button{background:transparent;border:0;color:var(--mut);border-radius:6px;padding:5px 9px;font-size:11px;cursor:pointer}
.seg button.on{background:var(--acc);color:#04101f;font-weight:700}
@media(max-width:560px){.dock button .lbl{display:none}.dock button{padding:11px}}
</style></head><body>
<div id="map"></div>
<div id="veil"></div>
<div id="dots"></div>
<video id="intro" autoplay muted playsinline preload="auto"><source src="/static/intro.mp4" type="video/mp4"></video>
<div id="introfx"></div>
<div class="topbar intro">
  <div id="champ" class="champ" style="display:none"></div>
  <div id="chips" class="chips"></div>
</div>
<div class="rfoot intro">
  <a href="https://eucplanet.ried.no" target="_blank" rel="noopener"><img src="/static/euc-planet.svg" alt=""/><span>powered by <b>EUC&nbsp;Planet</b></span></a>
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
<button id="gear" class="intro" title="Settings"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14 12.94a7.49 7.49 0 0 0 .05-.94 7.49 7.49 0 0 0-.05-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.61-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54a7 7 0 0 0-1.62.94l-2.39-.96a.5.5 0 0 0-.61.22L2.74 8.84a.5.5 0 0 0 .12.64l2.03 1.58a7.49 7.49 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32a.5.5 0 0 0 .61.22l2.39-.96a7 7 0 0 0 1.62.94l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54a7 7 0 0 0 1.62-.94l2.39.96a.5.5 0 0 0 .61-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58ZM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7Z"/></svg></button>
<div id="cfg" class="cfgpop"></div>
<div id="tip"></div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
const API="/api/v1";
const j=p=>fetch(API+p).then(r=>r.json());
const cc=c=>c?`<img class="flag" src="https://flagcdn.com/24x18/${(""+c).toLowerCase()}.png" alt="${c}" loading="lazy"/>`:"";
const av=(id,has)=>has===false?'<span class="av avph"></span>':`<img class="av" alt="" src="${API}/riders/${encodeURIComponent(id)}/avatar" onerror="this.style.visibility='hidden'"/>`;
const rider=e=>`<span class="rider">${av(e.store_id,e.has_avatar)}${cc(e.flag)}<span>${e.name||e.store_id}</span></span>`;
const CROWN='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 7l4.5 4L12 4l4.5 7L21 7l-1.8 12H4.8L3 7Z"/></svg>';
const FLAG='<svg class="cflag" viewBox="0 0 24 24"><path d="M5 21V3" stroke="#caa12f" stroke-width="2" fill="none" stroke-linecap="round"/><path class="cflagwave" d="M6 4h11l-2.4 3.3L17 10.6H6z" fill="#ffd24a"/></svg>';
const IC={
 mileage:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 5h8a3 3 0 0 1 0 6H8a3 3 0 0 0 0 6h11"/></svg>',
 daily:'<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="4.5"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/></g></svg>',
 week:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/><rect x="6" y="13" width="4" height="4" fill="currentColor" stroke="none"/></svg>',
 month:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M7 14h2M11 14h2M15 14h2M7 17h2M11 17h2"/></svg>',
 speed:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 19a8 8 0 1 1 16 0"/><path d="M12 13l4-3.5"/></svg>',
 accel:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 3c4 .5 6.5 3.5 7 7-3 .5-4.6 2.2-6 5l-3-3c1.4-3.6 1.2-6.6 2-9zM8 16l-4 4m6-2l-4 4m0-6l-2 2"/></svg>',
 gforce:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.6 6.6L21 11l-6.4 2.4L12 20l-2.6-6.6L3 11l6.4-2.4z"/></svg>',
 power:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>',
 current:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12c2-6 4-6 6 0s4 6 6 0 4-6 6 0"/></svg>',
 voltage:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="7" width="15" height="10" rx="2"/><path d="M21 10v4"/><path d="M10 9l-2 3.5h3L9 16" stroke-linejoin="round"/></svg>',
 streak:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2c1 4-2 5-2 8a2 2 0 0 0 4 0c2 2 3 4 3 6a5 5 0 0 1-10 0c0-4 4-6 5-14z"/></svg>'};
const BOARDS=[
 {k:"mileage",t:"Total dist",c:"total_km",u:" km",conv:"dist",d:"Total distance ever ridden"},
 {k:"daily",t:"Biggest day",c:"best_day_km",u:" km",conv:"dist",d:"Most distance in a single day"},
 {k:"week",t:"Biggest week",c:"best_week_km",u:" km",conv:"dist",d:"Most distance in one ISO week"},
 {k:"month",t:"Biggest month",c:"best_month_km",u:" km",conv:"dist",d:"Most distance in one calendar month"},
 {k:"speed",t:"Top speed",c:"best_speed",u:" km/h",conv:"spd",d:"Highest speed reached on any ride"},
 {k:"accel",t:"0→40",c:"accel_s",u:" s",d:"Fastest launch from a stop to 40 km/h · lower is better"},
 {k:"gforce",t:"Max G",c:"best_gforce",u:" g",d:"Strongest g-force spike · hard accel, brake or bump"},
 {k:"power",t:"Sustained W",c:"sustained_w",u:" W",d:"Highest power held for 2 seconds straight"},
 {k:"current",t:"Sustained A",c:"sustained_a",u:" A",d:"Highest current (amps) held for 2 seconds"},
 {k:"voltage",t:"Volt peak",c:"peak_voltage",u:" V",d:"Highest battery voltage observed"},
 {k:"streak",t:"Streak",c:"longest_streak",u:" d",d:"Longest run of consecutive days ridden"}];
const RECLABEL={mileage_king:"Mileage King",top_speed:"Top Speed",longest_trip:"Longest Trip",max_gforce:"Max G-Force",sustained_w:"Sustained Power",sustained_a:"Sustained Current",peak_voltage:"Voltage Peak"};
// --- units (km/h <-> mph), remembered + smart default by locale; + map style ---
const MI=0.621371, MPH_REGIONS=["US","GB","LR","MM"];
function defaultUnit(){try{const r=((navigator.language||"").split("-")[1]||"").toUpperCase();return MPH_REGIONS.includes(r)?"mph":"kmh";}catch(e){return "kmh";}}
let UNIT=localStorage.getItem("eucstats_unit")||defaultUnit();
const RASTER=(t,a)=>({version:8,sources:{r:{type:"raster",tiles:[t],tileSize:256,attribution:a,maxzoom:19}},layers:[{id:"r",type:"raster",source:"r"}]});
const STYLES={dark:"https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",light:"https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",voyager:"https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",satellite:RASTER("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}","© Esri, Maxar"),terrain:RASTER("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}","© Esri")};
let MAPSTYLE=localStorage.getItem("eucstats_style")||"dark";
const mph=()=>UNIT==="mph";
const r1=n=>Math.round((+n||0)*10)/10, r2=n=>Math.round((+n||0)*100)/100;
const dnum=km=>mph()?(""+r1(km*MI)):(""+r1(km)), dunit=()=>mph()?"mi":"km";
const snum=kmh=>mph()?(""+r1(kmh*MI)):(""+r1(kmh)), sunit=()=>mph()?"mph":"km/h";
function bval(b,v){if(v==null)v=0;if(b.conv==="dist")return dnum(v)+" "+dunit();if(b.conv==="spd")return snum(v)+" "+sunit();return r2(v)+b.u;}
const RECCONV={mileage_king:"dist",longest_trip:"dist",top_speed:"spd"};
function recval(k,v){const c=RECCONV[k];if(c==="dist")return dnum(v)+" "+dunit();if(c==="spd")return snum(v)+" "+sunit();return Math.round(v*100)/100;}
let S=null,WC=null;
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
  const dxKm=((hashStr(key+"x")%2000)/100)-10, dyKm=((hashStr(key+"y")%2000)/100)-10;  // ~ -10..+10 km each axis
  const olat=e.lat+dyKm/111, olon=e.lon+dxKm/(111*(Math.cos(e.lat*Math.PI/180)||1e-6));
  showArea(olon,olat,11);                                              // dotted ring marking the noised area
  map.flyTo({center:[olon,olat],zoom:9.6,curve:1.9,duration:2800,easing:easeInOutCubic,essential:true});
}

const pbody=document.getElementById("pbody"),panel=document.getElementById("panel"),ptitle=document.getElementById("ptitle");
let openPanel=null;
function RA(i){return i===0?'animation:rowin .5s both, shinebg 5s ease-in-out 1.1s infinite':'animation:rowin .5s both;animation-delay:'+(i*55)+'ms';}
function _tipEl(){return document.getElementById("tip");}
function showTip(html,x,y){const T=_tipEl();if(!T)return;T.innerHTML=html;T.classList.add("on");const r=T.getBoundingClientRect();let nx=x-r.width/2,ny=y-r.height-10;nx=Math.max(8,Math.min(innerWidth-r.width-8,nx));if(ny<8)ny=y+22;T.style.left=nx+"px";T.style.top=ny+"px";}
function hideTip(){const T=_tipEl();if(T)T.classList.remove("on");}
function bindTips(root){(root||document).querySelectorAll("[data-tip]").forEach(el=>{const show=()=>{const b=el.getBoundingClientRect();showTip(el.dataset.tip,b.left+b.width/2,b.top);};el.onmouseenter=show;el.onmouseleave=hideTip;el.addEventListener("click",e=>{e.stopPropagation();const T=_tipEl();(T&&T.classList.contains("on"))?hideTip():show();});});}
document.addEventListener("click",hideTip);
function countUp(el,target,dur,dec){const t0=+target||0;let s=null;function step(now){if(s===null)s=now;let p=Math.min(1,(now-s)/dur);p=1-Math.pow(1-p,3);const v=t0*p;el.textContent=dec?v.toFixed(dec):Math.round(v).toLocaleString();if(p<1)requestAnimationFrame(step);else el.textContent=dec?t0.toFixed(dec):Math.round(t0).toLocaleString();}requestAnimationFrame(step);}
function setPanel(name,title,html){
  if(openPanel===name){closePanel();return;}
  openPanel=name;ptitle.textContent=title;pbody.innerHTML=html;panel.classList.add("open");
  document.querySelectorAll(".dock button").forEach(b=>b.classList.toggle("on",b.dataset.p===name));
}
function closePanel(){openPanel=null;panel.classList.remove("open");document.querySelectorAll(".dock button").forEach(b=>b.classList.remove("on"));}
document.getElementById("pclose").onclick=closePanel;

function showBoards(){
  setPanel("boards","Leaderboards",`<div class="tabs">${BOARDS.map((b,i)=>`<button class="tab${i?'':' on'}" data-b="${b.k}" data-tip="${(b.d||'').replace(/"/g,'&quot;')}">${IC[b.k]||''}<span>${b.t}</span></button>`).join("")}</div><table><tbody id="lb"></tbody></table>`);
  pbody.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{pbody.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));t.classList.add("on");loadBoard(t.dataset.b);});
  bindTips(pbody);
  loadBoard("mileage");
}
async function loadBoard(k){
  const b=BOARDS.find(x=>x.k===k);
  const rows=(await j(`/leaderboards/${k}?limit=15`)).entries,tb=document.getElementById("lb");
  tb.innerHTML=rows.length?rows.map((e,i)=>`<tr class="sel${i===0?' gold1':''}" data-i="${i}" style="${RA(i)}"><td class=rk>${i+1}</td><td>${rider(e)}</td><td class=val>${bval(b,e[b.c])}</td></tr>`).join(""):`<tr><td colspan=3 class=mut>no data yet</td></tr>`;
  tb.querySelectorAll("tr.sel").forEach(tr=>tr.onclick=()=>flyToRider(rows[+tr.dataset.i]));
}
async function showPodium(){
  const m=(await j("/leaderboards/mileage?limit=3")).entries,o=[1,0,2],rkn=["1ST","2ND","3RD"];
  setPanel("podium","Mileage Kings",`<div class="podium" id="pod"></div>`);
  document.getElementById("pod").innerHTML=o.filter(i=>m[i]).map(i=>`<div class="pod p${i+1}${i===0?' gold1':i===1?' silv':i===2?' brnz':''}" data-i="${i}" style="animation:rowin .55s both;animation-delay:${i*90}ms"><div class="rkn">${rkn[i]}</div>${av(m[i].store_id,m[i].has_avatar)}<div>${cc(m[i].flag)} ${m[i].name||m[i].store_id}</div><div class="km">${dnum(m[i].total_km)} ${dunit()}</div></div>`).join("")||"<span class=mut>no riders yet</span>";
  document.querySelectorAll("#pod .pod").forEach(p=>p.onclick=()=>flyToRider(m[+p.dataset.i]));
}
async function showCountries(){
  const cs=await j("/countries");
  setPanel("countries","By country",`<table><tr><th>#</th><th>Country</th><th class=val>Total ${dunit()}</th><th class=val>Riders</th><th class=val>Avg</th></tr>${cs.map((c,i)=>`<tr class="${i===0?'gold1':''}" style="${RA(i)}"><td class=rk>${i+1}</td><td>${cc(c.country)}</td><td class=val>${dnum(c.total_km)}</td><td class=val>${c.riders}</td><td class=val>${dnum(c.avg_km_per_rider)}</td></tr>`).join("")||"<tr><td colspan=5 class=mut>no data yet</td></tr>"}</table>`);
}
async function showRecords(){
  const recs=(await j("/records")).filter(r=>r.value!=null);
  setPanel("records","Records",`<table id="rec"></table>`);
  document.getElementById("rec").innerHTML=recs.map((r,i)=>`<tr class="sel${i===0?' gold1':''}" data-i="${i}" style="${RA(i)}"><td>${RECLABEL[r.key]||r.key}</td><td>${rider(r.rider)}</td><td class=val>${recval(r.key,r.value)}</td></tr>`).join("")||"<tr><td class=mut>no records yet</td></tr>";
  document.querySelectorAll("#rec tr.sel").forEach(tr=>tr.onclick=()=>flyToRider(recs[+tr.dataset.i].rider));
}
const HANDLERS={boards:showBoards,podium:showPodium,countries:showCountries,records:showRecords};
document.querySelectorAll(".dock button").forEach(b=>b.onclick=()=>HANDLERS[b.dataset.p]());

function reveal(el,d){ if(el) setTimeout(()=>el.classList.add("show"),d); }
function runIntro(){
  reveal(document.querySelector(".topbar"),1100);
  reveal(document.querySelector(".dock"),2300);
  document.querySelectorAll(".dock button").forEach((b,i)=>reveal(b,2700+i*320));
  reveal(document.querySelector(".rfoot"),4100);
  reveal(document.querySelector("#gear"),4450);
}

function renderHeader(){
  const chips=[["Riders",S.riders,0],["Trips",S.trips,0],["Total "+dunit(),mph()?r1(S.total_km*MI):r1(S.total_km),1],["Countries",S.countries,0]];
  document.getElementById("chips").innerHTML=chips.map(([l,v,dec])=>`<span class="chip"><b data-cv="${v}" data-dec="${dec}">0</b> ${l}</span>`).join("");
  document.querySelectorAll("#chips b[data-cv]").forEach(b=>countUp(b,+b.dataset.cv,1000,+b.dataset.dec));
  const ch=document.getElementById("champ");
  const C=WC||{};
  if(!(C.day||C.week||C.month)){ch.style.display="none";return;}
  ch.style.display="block";ch.style.cursor="default";ch.onclick=null;
  const line=(lab,c)=>c?`<div class="cline" data-sid="${c.store_id}"><span class="clab">${lab}</span>${cc(c.flag)}<b>${c.name||c.store_id}</b><span class="cscore">${c.score} pts</span></div>`:`<div class="cline"><span class="clab">${lab}</span><span class="mut">no rides yet</span></div>`;
  const tip=((C.formula?`<b>${C.formula}</b><br>`:"")+"Our secret recipe: distance is king, lifted by your top speed and time in the saddle.").replace(/"/g,"&quot;");
  ch.innerHTML=`<div class="chead">${FLAG}<span>EUC Planet Champions</span><button class="cinfo" data-tip="${tip}">&#9432;</button></div>`+
    line("Day",C.day)+line("Week",C.week)+line("Month",C.month);
  ch.querySelectorAll(".cline[data-sid]").forEach(el=>{el.style.cursor="pointer";el.onclick=()=>{const c=[C.day,C.week,C.month].find(x=>x&&x.store_id===el.dataset.sid);if(c)flyToRider(c);};});
  bindTips(ch);
}
let CELLS=null;
function addHeat(){
  if(!CELLS||map.getSource("activity")) return;
  map.addSource("activity",{type:"geojson",data:{type:"FeatureCollection",
    features:CELLS.map(c=>({type:"Feature",geometry:{type:"Point",coordinates:[c.lon,c.lat]},properties:{r:c.rider_count||0}}))}});
  map.addLayer({id:"heat",type:"heatmap",source:"activity",paint:{
    "heatmap-weight":["min",1,["+",0.25,["/",["ln",["+",1,["get","r"]]],["ln",7]]]],
    "heatmap-intensity":["interpolate",["linear"],["zoom"],0,1.0,9,3.2],
    "heatmap-radius":["interpolate",["linear"],["zoom"],0,34,5,62,12,98],
    "heatmap-opacity":0,
    "heatmap-color":["interpolate",["linear"],["heatmap-density"],
      0,"rgba(0,0,0,0)",0.1,"rgba(22,35,94,0.6)",0.3,"#2b6fd6",0.5,"#1fb6c9",0.7,"#3fe6a8",0.86,"#c8f7e6",1,"#ffffff"]}});
  map.setPaintProperty("heat","heatmap-opacity-transition",{duration:1500});
  requestAnimationFrame(()=>map.setPaintProperty("heat","heatmap-opacity",0.62));   // softer, more organic
}
function setupCfg(){
  const gear=document.getElementById("gear"),cfg=document.getElementById("cfg");
  function render(){
    cfg.innerHTML=`<div class="crow"><span>Units</span><div class="seg"><button data-u="kmh" class="${UNIT==='kmh'?'on':''}">km/h</button><button data-u="mph" class="${UNIT==='mph'?'on':''}">mph</button></div></div>`+
      `<div class="crow"><span>Map</span><div class="seg"><button data-s="dark" class="${MAPSTYLE==='dark'?'on':''}">Dark</button><button data-s="light" class="${MAPSTYLE==='light'?'on':''}">Light</button><button data-s="voyager" class="${MAPSTYLE==='voyager'?'on':''}">Voyager</button><button data-s="satellite" class="${MAPSTYLE==='satellite'?'on':''}">Satellite</button><button data-s="terrain" class="${MAPSTYLE==='terrain'?'on':''}">Topo</button></div></div>`+
      `<div class="crow"><span>Intro</span><button id="introbtn" class="cbtn">Replay intro</button></div>`;
    cfg.querySelectorAll("[data-u]").forEach(b=>b.onclick=()=>{UNIT=b.dataset.u;localStorage.setItem("eucstats_unit",UNIT);render();renderHeader();const p=openPanel;if(p){openPanel=null;HANDLERS[p]();}});
    cfg.querySelectorAll("[data-s]").forEach(b=>b.onclick=()=>{MAPSTYLE=b.dataset.s;localStorage.setItem("eucstats_style",MAPSTYLE);render();map.setStyle(STYLES[MAPSTYLE]);});
    const ib=cfg.querySelector("#introbtn");
    if(ib) ib.onclick=()=>{ try{localStorage.removeItem("eucstats_intro_seen");}catch(e){} ib.disabled=true; ib.classList.add("on"); ib.textContent="Replaying…"; setTimeout(()=>location.reload(),260); };
  }
  render(); gear.onclick=()=>cfg.classList.toggle("open");
}

async function init(){
  S=await j("/stats/summary"); WC=await j("/champions"); renderHeader();
  const tzFull=Intl.DateTimeFormat().resolvedOptions().timeZone||"";
  const [rlon,rlat,rz]=TZMAP[tzFull]||REGION[tzFull.split("/")[0]]||[10,30,3];
  map=new maplibregl.Map({container:"map",style:STYLES[MAPSTYLE],center:[rlon,rlat],zoom:1.5,attributionControl:true});
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),"top-right");
  const veil=document.getElementById("veil");
  const setVeil=()=>{const z=map.getZoom();veil.style.opacity=Math.max(0,Math.min(1,(6-z)/4.5));};
  map.on("zoom",setVeil); setVeil();
  map.on("style.load",addHeat);   // re-add heat after a style switch
  setupCfg();
  let mapReady=false,videoDone=false,introRan=false;
  function pickTarget(){   // don't zoom to empty space: if the visitor's region has no data, go to the busiest area
    let t=[rlon,rlat,rz];
    if(CELLS&&CELLS.length){
      const near=CELLS.some(c=>Math.abs(c.lon-rlon)<8&&Math.abs(c.lat-rlat)<6);
      if(!near){const top=CELLS.slice().sort((a,b)=>(b.total_km||0)-(a.total_km||0))[0]; t=[top.lon,top.lat,rz];}
    }
    return t;
  }
  function doIntro(){
    if(introRan||!mapReady||!videoDone) return; introRan=true;
    const [tlon,tlat,tz]=pickTarget();
    map.flyTo({center:[tlon,tlat],zoom:tz,duration:5000,curve:1.5,easing:easeInOutCubic,essential:true});
    runIntro();
  }
  const vid=document.getElementById("intro"),fx=document.getElementById("introfx");
  const introSeen=localStorage.getItem("eucstats_intro_seen");
  const endVideo=()=>{ if(videoDone) return; videoDone=true; try{localStorage.setItem("eucstats_intro_seen","1");}catch(e){}
    if(vid){vid.classList.add("done"); setTimeout(()=>{vid&&vid.remove();},2000);}
    if(fx){fx.classList.add("done"); setTimeout(()=>{fx&&fx.remove();},2000);} doIntro(); };
  if(vid){
    vid.onended=endVideo; vid.onerror=endVideo;
    if(introSeen){   // repeat visit: hide frame 0, seek to the final second, then reveal
      vid.style.filter="brightness(0)";
      const reveal1=()=>{vid.style.filter="";};
      vid.onseeked=reveal1;
      const seekEnd=()=>{try{if(isFinite(vid.duration)&&vid.duration>1.3){vid.currentTime=vid.duration-0.9;}else{reveal1();}}catch(e){reveal1();}};
      if(vid.readyState>=2)seekEnd(); else vid.onloadeddata=seekEnd; setTimeout(endVideo,4500);
    } else { setTimeout(endVideo,14000); }   // first visit: full intro (with a safety timeout)
    const pp=vid.play&&vid.play(); if(pp&&pp.catch)pp.catch(()=>{});
  } else { videoDone=true; }
  map.on("load",async ()=>{
    CELLS=await j("/map/cells?zoom=0.1"); addHeat();
    mapReady=true; doIntro();
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
