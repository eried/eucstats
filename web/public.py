"""Public website: GPU (MapLibre GL) world heatmap with a cinematic intro,
monochrome/silhouette UI, openable tool panels, and fly-to-winner. /api/v1."""
import datetime
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

import config
from database import get_db
from services import settings

public_router = APIRouter()

_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"/>
<title>EUC Stats | Leaderboards & Heatmap</title>
__CLARITY__
__HIDECFG__
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
:root{--ink:#eef1fb;--mut:#9aa6c8;--acc:#2ea8ff;--gold:#ffd24a;--line:#33457a;--surf:linear-gradient(158deg,rgba(34,52,100,.82),rgba(9,14,30,.85));--glass:rgba(13,17,32,.72);--shadow:0 12px 34px rgba(0,0,0,.6),inset 0 1px 0 rgba(130,170,255,.14)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;height:100dvh;font:14px/1.45 "Chakra Petch",ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;color:var(--ink);background:#070a16;overflow:hidden}
body{position:fixed;inset:0;width:100%}
#ptitle,.champ,.chead,.chip b,.val,.rk,.pod .km,.pod .rkn,.cscore,.clab,.tab span,.dock .lbl,.recval{font-family:"Orbitron",ui-sans-serif,sans-serif;letter-spacing:.5px}
#map{position:fixed;top:0;left:0;width:100vw;height:var(--appvh,100dvh);z-index:0}
#veil{position:fixed;top:0;left:0;width:100vw;height:var(--appvh,100dvh);z-index:1;pointer-events:none;transition:opacity .25s linear;background:linear-gradient(to bottom,rgba(0,0,0,.5) 0%,rgba(0,0,0,.12) 28%,rgba(0,0,0,0) 50%),radial-gradient(ellipse 80% 84% at 50% 48%,rgba(0,0,0,0) 40%,rgba(0,0,0,.74) 100%)}
#dots{position:fixed;top:0;left:0;width:100vw;height:var(--appvh,100dvh);z-index:1;pointer-events:none;mix-blend-mode:multiply;opacity:.22;background-image:radial-gradient(circle at center,rgba(0,0,0,.8) 0,rgba(0,0,0,.8) .7px,transparent 1.2px);background-size:4px 4px}
#testwm{position:fixed;inset:0;z-index:2000;display:flex;align-items:center;justify-content:center;pointer-events:none;font:900 clamp(46px,14vw,210px)/1 "Orbitron",sans-serif;letter-spacing:.08em;text-transform:uppercase;color:rgba(255,42,42,.2);transform:rotate(-22deg);text-shadow:0 0 32px rgba(255,0,0,.22);white-space:nowrap;-webkit-text-stroke:2px rgba(255,60,60,.18)}
#intro{position:fixed;top:0;left:0;width:100vw;height:var(--appvh,100dvh);z-index:3000;object-fit:cover;background:#000;filter:blur(.5px) saturate(1.08) contrast(1.04);clip-path:circle(150% at 50% 50%);transition:clip-path 1.6s cubic-bezier(.65,0,.35,1)}#intro.done{clip-path:circle(0% at 50% 50%);pointer-events:none}
#introfx{position:fixed;top:0;left:0;width:100vw;height:var(--appvh,100dvh);z-index:3001;pointer-events:none;overflow:hidden;clip-path:circle(150% at 50% 50%);transition:clip-path 1.6s cubic-bezier(.65,0,.35,1);background:radial-gradient(ellipse 80% 84% at 50% 47%,rgba(0,0,0,0) 38%,rgba(0,0,0,.34) 72%,rgba(0,0,0,.7) 100%),linear-gradient(to bottom,rgba(0,0,0,.34),rgba(0,0,0,0) 20%,rgba(0,0,0,0) 80%,rgba(0,0,0,.42))}#introfx.done{clip-path:circle(0% at 50% 50%)}
#introfx::after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(0,0,0,.45) 0,rgba(0,0,0,.45) 2px,rgba(130,180,255,.06) 2px,rgba(130,180,255,.06) 4px);background-size:100% 4px;mix-blend-mode:multiply;opacity:.72;animation:scan 2.5s linear infinite}
#introfx::before{content:"";position:absolute;inset:-12px;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='110' height='110'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");background-size:130px 130px;mix-blend-mode:overlay;opacity:.32;animation:grain .9s steps(4) infinite}
@keyframes scan{from{background-position:0 0}to{background-position:0 3px}}
@keyframes grain{0%{transform:translate(0,0)}25%{transform:translate(-4px,3px)}50%{transform:translate(3px,-4px)}75%{transform:translate(-3px,-2px)}100%{transform:translate(0,0)}}
.cbtn{background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:8px;color:var(--mut);font-size:11px;padding:7px 11px;cursor:pointer;letter-spacing:.4px;width:100%;text-align:center;transition:color .2s,border-color .2s,background .2s}
.cbtn:hover{color:var(--acc);border-color:var(--acc)}
.cbtn:disabled{color:var(--mut);border-color:var(--line);background:rgba(0,0,0,.18);opacity:.5;cursor:not-allowed}
.cbtn.on{color:var(--gold);border-color:rgba(255,210,74,.55);background:rgba(255,210,74,.12);cursor:default;opacity:1}
.introctl{display:flex;gap:8px;align-items:center}
.introctl .cbtn{width:auto;flex:1;white-space:nowrap}
.cck{display:inline-flex;gap:5px;align-items:center;font-size:11px;color:var(--mut);cursor:pointer;white-space:nowrap}
.cck input{accent-color:var(--acc);cursor:pointer;margin:0}
.cck.dis{opacity:.45;cursor:not-allowed}
tr.gold1,tr.silv,tr.brnz{background-size:300% 100%;background-repeat:no-repeat}
tr.gold1{background-image:linear-gradient(100deg,rgba(255,213,80,.14) 0,rgba(255,226,130,.20) 30%,rgba(255,243,185,.58) 50%,rgba(255,226,130,.20) 70%,rgba(255,213,80,.14) 100%)!important;box-shadow:inset 0 0 22px rgba(255,214,90,.30)}
tr.silv{background-image:linear-gradient(100deg,rgba(205,211,224,.07) 0,rgba(220,226,238,.11) 30%,rgba(238,242,250,.38) 50%,rgba(220,226,238,.11) 70%,rgba(205,211,224,.07) 100%)!important;box-shadow:inset 0 0 20px rgba(220,226,238,.17)}
tr.brnz{background-image:linear-gradient(100deg,rgba(150,104,64,.05) 0,rgba(168,124,82,.08) 30%,rgba(198,150,108,.22) 50%,rgba(168,124,82,.08) 70%,rgba(150,104,64,.05) 100%)!important;box-shadow:inset 0 0 18px rgba(170,120,84,.10)}
.pod.gold1{position:relative;overflow:hidden;background:linear-gradient(158deg,rgba(104,84,30,.96),rgba(42,31,8,.97))!important;border-top-color:var(--gold);box-shadow:inset 0 0 26px rgba(255,214,90,.26),0 0 16px rgba(255,200,70,.18),var(--shadow)}
.pod.gold1::after{content:"";position:absolute;top:0;left:-130%;width:120%;height:100%;background:linear-gradient(100deg,transparent 0,rgba(255,242,180,0) 30%,rgba(255,242,180,.58) 50%,rgba(255,242,180,0) 70%,transparent 100%);transform:skewX(-16deg);animation:shinesweep 5.5s ease-in-out infinite;pointer-events:none}
@keyframes shinebg{0%{background-position:160% 0}16%{background-position:-60% 0}100%{background-position:-60% 0}}
@keyframes shinesweep{0%{left:-130%}20%{left:140%}100%{left:140%}}
.pod.silv{position:relative;overflow:hidden;background:linear-gradient(158deg,rgba(62,66,78,.95),rgba(20,23,30,.96))!important;border-top-color:#cdd3e0}
.pod.silv::after{content:"";position:absolute;top:0;left:-130%;width:120%;height:100%;background:linear-gradient(100deg,transparent 0,rgba(228,234,246,0) 30%,rgba(228,234,246,.4) 50%,rgba(228,234,246,0) 70%,transparent 100%);transform:skewX(-16deg);animation:shinesweep 5.5s ease-in-out .9s infinite;pointer-events:none}
.pod.brnz{position:relative;overflow:hidden;background:linear-gradient(158deg,rgba(56,40,24,.95),rgba(22,15,8,.96))!important;border-top-color:#8a6038}
.pod.brnz::after{content:"";position:absolute;top:0;left:-130%;width:120%;height:100%;background:linear-gradient(100deg,transparent 0,rgba(206,150,100,0) 30%,rgba(206,150,100,.22) 50%,rgba(206,150,100,0) 70%,transparent 100%);transform:skewX(-16deg);animation:shinesweep 5.5s ease-in-out 1.8s infinite;pointer-events:none}
.maplibregl-ctrl-attrib{background:none!important;box-shadow:none!important;font-size:9px;opacity:.4}.maplibregl-ctrl-attrib a{color:#7a86ad;text-shadow:0 1px 2px #000}.maplibregl-ctrl-attrib-button{display:none!important}.maplibregl-ctrl-group{background:var(--glass)!important;border:1px solid var(--line)!important}
svg.ic{width:18px;height:18px;display:block}
.intro{opacity:0;pointer-events:none}
.intro.show{opacity:1;pointer-events:auto;transition:opacity 1s ease}
@keyframes rowin{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
.topbar{position:fixed;top:16px;left:16px;z-index:500;max-width:min(92vw,380px);background:var(--surf);backdrop-filter:blur(10px);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}
.champ{display:block;padding:10px 14px 11px;font-size:13px;border-bottom:1px solid var(--line);background:rgba(255,210,74,.06);position:relative;overflow:hidden;animation:champvhs 10s infinite}
.champ svg{width:16px;height:16px;color:var(--gold)}.champ b{font-weight:700;color:var(--gold)}
.chead{display:flex;align-items:center;gap:7px;font-size:10.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--gold);margin-bottom:5px}
.chead>span{flex:1}
.cflag{width:17px;height:17px;overflow:visible}
.cflagwave{transform-box:fill-box;transform-origin:left center;animation:wave 2.6s ease-in-out infinite}
@keyframes wave{0%,100%{transform:skewY(0)}25%{transform:skewY(-5deg)}50%{transform:skewY(0)}75%{transform:skewY(5deg)}}
.cinfo{background:none;border:0;color:var(--mut);cursor:pointer;font-size:13px;line-height:1;padding:0}
.cinfo:hover{color:var(--gold)}
.ccol{background:none;border:0;color:var(--mut);cursor:pointer;padding:0;display:flex;align-items:center}.ccol:hover{color:var(--gold)}.ccol svg{width:15px;height:15px;transition:transform .25s}
.topbar.collapsed{max-width:none;width:auto;background:transparent;border:0;box-shadow:none;backdrop-filter:none;overflow:visible}
.topbar.collapsed .chips{display:none}
.topbar.collapsed .champ{display:inline-flex;border-bottom:0;padding:8px 10px;border-radius:11px;background:var(--surf);border:1px solid var(--line);backdrop-filter:blur(10px);box-shadow:var(--shadow);opacity:.45;cursor:pointer;transition:opacity .25s;animation:none}
.topbar.collapsed .champ:hover{opacity:1}
.topbar.collapsed .chead{margin:0}
.topbar.collapsed .chead>span,.topbar.collapsed .cinfo,.topbar.collapsed .ccol,.topbar.collapsed .cline{display:none}
.topbar.collapsed .champ::after{display:none}
.topbar.collapsed .cflag{width:24px;height:24px}
.cline{display:flex;align-items:center;gap:6px;font-size:12.5px;padding:2px 0}
.cline .clab{width:42px;min-width:42px;font-size:9.5px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut)}
.cline b{color:var(--gold);font-weight:700;flex:1;min-width:0;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cscore{margin-left:auto;color:var(--acc);font-weight:700;font-size:12px}
.cformula{margin-top:7px;font-size:10.5px;line-height:1.45;color:var(--ink);background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:7px;padding:7px 9px}
.cformula b{color:var(--gold)}
.chead>span{flex:1;background:linear-gradient(90deg,#caa12f,#fff3c0,#ffd24a,#fff3c0,#caa12f);background-size:220% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;animation:goldflow 4.5s linear infinite}
@keyframes goldflow{0%{background-position:0 0}100%{background-position:220% 0}}
@keyframes champvhs{0%,90%,100%{text-shadow:none}92%{text-shadow:1.6px 0 rgba(255,40,90,.5),-1.6px 0 rgba(0,200,255,.5)}94%{text-shadow:-1.6px 0 rgba(255,40,90,.5),1.6px 0 rgba(0,200,255,.5)}96%{text-shadow:none}}
.champ::after{content:"";position:absolute;left:0;right:0;height:36%;top:-36%;background:linear-gradient(rgba(255,255,255,0),rgba(180,220,255,.16),rgba(255,255,255,0));pointer-events:none;animation:champroll 10s linear infinite}
@keyframes champroll{0%,82%{top:-36%}100%{top:136%}}
.rgbglitch{animation:rgbg .5s linear}
@keyframes rgbg{0%,100%{text-shadow:none}20%{text-shadow:-1.6px 0 #ff2bd0,1.6px 0 #00ffe7}45%{text-shadow:1.6px 0 #ff2bd0,-1.6px 0 #00ffe7}70%{text-shadow:-1px 0 #ff2bd0,1px 0 #00ffe7}}
#tip{position:fixed;z-index:600;max-width:240px;background:linear-gradient(158deg,rgba(26,40,78,.88),rgba(8,12,26,.89));border:1px solid var(--line);border-radius:9px;box-shadow:0 16px 50px rgba(0,0,0,.6);padding:8px 11px;font-size:11.5px;line-height:1.45;color:var(--ink);pointer-events:none;opacity:0;transform:translateY(4px);transition:opacity .15s,transform .15s}
#tip.on{opacity:1;transform:translateY(0)}#tip b{color:var(--gold)}
.tab.on{animation:tabglow .7s ease}
@keyframes tabglow{0%{box-shadow:0 0 0 0 rgba(46,168,255,.55)}100%{box-shadow:0 0 16px 3px rgba(46,168,255,0)}}
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
.dock button:hover{background:rgba(255,255,255,.06)}.dock button.on{background:color-mix(in srgb,var(--sec,var(--acc)) 16%,transparent);color:var(--sec,var(--acc))}
.dock button.on svg{color:var(--sec,var(--acc))}
.dock button[data-p=riders]{--sec:#2ea8ff}.dock button[data-p=countries]{--sec:#ff6b6b}.dock button[data-p=wheels]{--sec:#ffd24a}.dock button[data-p=brands]{--sec:#ff9f43}.dock button[data-p=records]{--sec:#39d98a}.dock button[data-p=tech]{--sec:#a78bfa}
.panel{position:fixed;left:50%;bottom:84px;transform:translateX(-50%) translateY(150%);opacity:0;visibility:hidden;z-index:550;width:min(94vw,720px);height:60dvh;max-height:580px;overflow:hidden;display:flex;flex-direction:column;background:linear-gradient(158deg,rgba(26,40,78,.86),rgba(8,12,26,.87));backdrop-filter:blur(18px);border:1px solid var(--line);border-radius:12px;box-shadow:0 30px 90px rgba(0,0,0,.65);transition:transform .32s cubic-bezier(.2,.8,.2,1),opacity .26s}
.panel.open{transform:translateX(-50%) translateY(0);opacity:1;visibility:visible}
.panel{transform-origin:50% 100%;border-top-width:2px;border-top-color:color-mix(in srgb,var(--sec,var(--acc)) 62%,transparent);box-shadow:0 30px 90px rgba(0,0,0,.65),inset 0 0 70px -52px var(--sec,transparent)}
.panel[data-sec=riders]{--sec:#2ea8ff}.panel[data-sec=countries]{--sec:#ff6b6b}.panel[data-sec=wheels]{--sec:#ffd24a}.panel[data-sec=brands]{--sec:#ff9f43}.panel[data-sec=records]{--sec:#39d98a}.panel[data-sec=tech]{--sec:#a78bfa}
@keyframes panUp{from{opacity:0;transform:translate(-50%,46px)}to{opacity:1;transform:translate(-50%,0)}}
@keyframes panLeft{from{opacity:0;transform:translate(calc(-50% - 70px),0)}to{opacity:1;transform:translate(-50%,0)}}
@keyframes panRight{from{opacity:0;transform:translate(calc(-50% + 70px),0)}to{opacity:1;transform:translate(-50%,0)}}
@keyframes panPop{from{opacity:0;transform:translate(-50%,12px) scale(.92)}to{opacity:1;transform:translate(-50%,0) scale(1)}}
@keyframes panFlip{from{opacity:0;transform:translate(-50%,22px) perspective(800px) rotateX(20deg)}to{opacity:1;transform:translate(-50%,0) perspective(800px) rotateX(0)}}
@keyframes panBlur{from{opacity:0;filter:blur(10px);transform:translate(-50%,8px)}to{opacity:1;filter:blur(0);transform:translate(-50%,0)}}
@keyframes panDown{from{opacity:1;transform:translate(-50%,0)}to{opacity:0;transform:translate(-50%,58px)}}
.phead{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line);flex:0 0 auto;z-index:5;background:rgba(11,15,28,.96)}
.phead b{font-size:14px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut)}.phead button{background:transparent;border:0;color:var(--mut);cursor:pointer}
.pacts{display:flex;gap:12px;align-items:center}.phead button:hover{color:var(--acc)}#ppeek.on{color:var(--gold)}.phead button svg{width:18px;height:18px;display:block}#prefresh.spin svg{animation:spin .6s linear}@keyframes spin{to{transform:rotate(360deg)}}
.pbody{padding:12px 18px;flex:1;min-height:0;overflow-y:auto;scrollbar-width:thin;scrollbar-color:rgba(130,170,255,.3) transparent}.hint{color:var(--mut);font-size:11.5px;margin:2px 0 12px;letter-spacing:.3px;border-left:2px solid var(--acc);padding-left:8px}
table{width:100%;border-collapse:collapse}td,th{padding:7px 8px;text-align:left}
tr+tr{border-top:1px solid #1b2240}.rk{color:var(--acc);width:26px;font-weight:700;font-variant-numeric:tabular-nums}
.val{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.mut{color:var(--mut)}.rider{display:flex;align-items:center;gap:9px}
.av{width:24px;height:24px;border-radius:50%;background:#1b2240;object-fit:cover;flex:0 0 auto;vertical-align:middle;box-shadow:0 0 0 1.5px rgba(255,255,255,.55),0 1px 5px rgba(0,0,0,.5)}
.avph{background:linear-gradient(135deg,#2a3566,#141a30)}
.flag{width:20px;height:15px;border-radius:2px;object-fit:cover;vertical-align:middle;box-shadow:0 0 0 1px rgba(0,0,0,.45);flex:0 0 auto}
tr.sel{cursor:pointer}tr.sel:hover{background:rgba(46,168,255,.08)}
.tabs{display:grid;grid-auto-flow:column;grid-template-rows:repeat(2,auto);grid-auto-columns:minmax(176px,max-content);gap:6px;margin-bottom:6px;overflow-x:auto;overflow-y:hidden;scroll-snap-type:x proximity;padding-bottom:6px;scrollbar-width:thin}
.tabs.onerow{grid-template-rows:auto}   /* few tabs: one line + side-scroll instead of a half-empty second row */
.tabs .tab{scroll-snap-align:start}
.tabcap{display:flex;align-items:center;gap:7px;min-height:16px;margin:-1px 2px 9px;font-size:12px;color:var(--mut);line-height:1.3}
.tabcap svg{width:14px;height:14px;flex:0 0 auto;opacity:.9}
.tabcap:empty{display:none}
.tabs::-webkit-scrollbar{height:6px}.tabs::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}
.pbody::-webkit-scrollbar{width:7px}.pbody::-webkit-scrollbar-track{background:transparent}.pbody::-webkit-scrollbar-thumb{background:rgba(130,170,255,.28);border-radius:4px;border:2px solid transparent;background-clip:padding-box}.pbody::-webkit-scrollbar-thumb:hover{background:rgba(130,170,255,.5)}
.tab{display:flex;align-items:center;justify-content:flex-start;gap:7px;background:transparent;border:1px solid var(--line);color:var(--mut);border-radius:7px;padding:0 12px;height:36px;font-size:12px;cursor:pointer;letter-spacing:.3px;overflow:visible;white-space:nowrap}
.tab>span{white-space:nowrap;text-align:left}
.tab svg{flex:0 0 auto}
.tab svg{width:14px;height:14px}
.tab.on{background:rgba(46,168,255,.16);border-color:var(--acc);color:var(--acc)}
.peek{opacity:.5!important}   /* admin preview: hidden-from-public metric, shown dimmed */
.podium{display:flex;gap:12px;justify-content:center;align-items:flex-end;padding:8px 0}
.pod{background:var(--surf);border:1px solid var(--line);border-top-width:3px;border-radius:9px;padding:14px 12px;text-align:center;flex:1 1 0;min-width:0;max-width:158px;cursor:pointer;transition:transform .15s;box-shadow:var(--shadow)}
.pod:hover{transform:translateY(-3px)}.pod .av{width:54px;height:54px;margin:0 auto 8px;display:block}
.pod.p1{border-top-color:var(--gold);margin-bottom:18px}.pod.p2{border-top-color:#cdd3e0}.pod.p3{border-top-color:#8a6038;margin-bottom:0}
.pod .km{color:var(--acc);font-weight:700;margin-top:3px}.pod .rkn{color:var(--mut);font:700 12px/1 ui-monospace,monospace;letter-spacing:1px}
.pname{margin-top:2px;font-size:12px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.psub{color:var(--mut);font-size:10.5px;margin-top:3px}
.blogo{position:relative;width:46px;height:46px;display:inline-flex;align-items:center;justify-content:center}
.blogo img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
.bmono{font-family:Orbitron,sans-serif;font-weight:800;font-size:17px;letter-spacing:.5px;color:var(--gold);background:rgba(255,210,74,.1);border:1px solid rgba(255,210,74,.4);border-radius:9px;width:44px;height:44px;display:flex;align-items:center;justify-content:center}
.podic{width:48px;height:48px;margin:2px auto 6px;display:flex;align-items:center;justify-content:center;color:var(--acc)}
.podic svg{width:40px;height:40px}
td.sub{color:var(--mut)}
.empty{min-height:220px;display:flex;align-items:center;justify-content:center;text-align:center;color:var(--mut);padding:26px}
.recs{display:grid;grid-template-columns:repeat(auto-fit,minmax(238px,1fr));gap:10px;padding:2px 0}
.rec{display:flex;align-items:center;gap:11px;background:var(--surf);border:1px solid var(--line);border-radius:10px;padding:11px 13px;cursor:pointer;transition:transform .15s,border-color .2s}
.rec:hover{transform:translateY(-2px);border-color:var(--acc)}
.recmed{flex:0 0 auto;color:var(--gold)}.recmed svg{width:26px;height:26px;display:block}
.recmain{flex:1;min-width:0}
.reclbl{font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut)}
.recrider{display:flex;align-items:center;gap:6px;font-weight:700;margin-top:3px;min-width:0}
.recrider .av{width:22px;height:22px}.recrider span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.celln{display:inline-flex;align-items:center;gap:6px;max-width:100%;min-width:0;vertical-align:middle}.celln>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.csel{background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:8px;color:var(--ink);font-family:inherit;font-size:12px;padding:6px 8px;cursor:pointer;width:100%}
.recval{flex:0 0 auto;color:var(--acc);font-weight:700;font-size:15px}
.vsec{margin-bottom:14px}.vtitle{font-family:Orbitron,sans-serif;font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:var(--gold);margin:0 0 6px}
.blist{display:flex;flex-direction:column;gap:3px;margin-top:4px}
.brow{position:relative;display:flex;align-items:center;gap:9px;padding:6px 11px;border-radius:7px;font-size:13px;overflow:hidden;background:rgba(255,255,255,.03)}
.bfill{position:absolute;left:0;top:0;bottom:0;border-radius:7px;background:linear-gradient(90deg,rgba(46,168,255,.30),rgba(46,168,255,.09));z-index:0;transition:width .55s cubic-bezier(.2,.8,.2,1)}
.brow>:not(.bfill){position:relative;z-index:1}
.brow .brk{color:var(--mut);font-variant-numeric:tabular-nums;min-width:14px;text-align:right}
.brow .blab{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.brow .bpct{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.winpin{width:36px;height:36px;border-radius:50%;border:2px solid var(--acc);background:#0e1326 center/cover;box-shadow:0 0 0 4px rgba(46,168,255,.22),0 0 20px rgba(46,168,255,.65)}
#gear{position:fixed;left:14px;bottom:14px;z-index:560;width:38px;height:38px;display:flex;align-items:center;justify-content:center;background:var(--surf);border:1px solid var(--line);border-radius:9px;color:var(--mut);cursor:pointer;box-shadow:var(--shadow);transition:color .2s}
#gear:hover{color:var(--acc)}#gear svg{width:18px;height:18px}
#gear.show{opacity:.45;transition:opacity .3s ease,color .2s}#gear.show:hover{opacity:1}
.cfgpop{position:fixed;left:14px;bottom:60px;z-index:900;display:none;flex-direction:column;gap:12px;min-width:264px;max-width:calc(100vw - 28px);background:linear-gradient(158deg,rgba(26,40,78,.86),rgba(8,12,26,.87));backdrop-filter:blur(16px);border:1px solid var(--line);border-radius:12px;box-shadow:0 24px 70px rgba(0,0,0,.6);padding:14px}
.cfgpop.open{display:flex}
.crow{display:grid;grid-template-columns:90px 1fr;align-items:center;gap:10px;font-size:12px;color:var(--mut);letter-spacing:.4px}
.crow>span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.seg{display:grid;grid-template-columns:1fr 1fr;gap:4px;background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:8px;padding:3px}
.seg button{background:transparent;border:0;color:var(--mut);border-radius:6px;padding:5px 9px;font-size:11px;cursor:pointer}
.seg button.on{background:var(--acc);color:#04101f;font-weight:700}
@media(max-width:560px){.dock button .lbl{display:none}.dock button{padding:11px}}
</style></head><body>
<div id="map"></div>
<div id="veil"></div>
<div id="dots"></div>
__TESTWM__
<video id="intro" autoplay muted playsinline preload="auto"><source src="/static/intro.mp4" type="video/mp4"></video>
<div id="introfx"></div>
<script>
/* Synchronous: if the intro is off (admin site-wide OR this visitor's gear-menu choice),
   kill the autoplaying video BEFORE first paint so no frame ever flashes. */
(function(){try{var c=window.__CFG__||{};
  if(c.intro_enabled===false||localStorage.getItem("eucstats_intro_off")==="1"){
    var v=document.getElementById("intro"),f=document.getElementById("introfx");
    if(v){try{v.pause();}catch(e){}v.remove();} if(f)f.remove();
  }}catch(e){}})();
</script>
<div class="topbar intro">
  <div id="champ" class="champ" style="display:none"></div>
  <div id="chips" class="chips"></div>
</div>
<div class="rfoot intro">
  <a href="https://eucplanet.ried.no" target="_blank" rel="noopener"><img src="/static/euc-planet.svg" alt=""/><span><span data-i18n="foot.poweredby">powered by</span> <b>EUC&nbsp;Planet</b></span></a>
  <a href="https://github.com/eried/eucstats" target="_blank" rel="noopener" aria-label="eucstats on GitHub"><svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg><span>GitHub</span></a>
  <span class="ver" title="HTML last-modified date (auto-updated on deploy)">build __BUILD__</span>
</div>
<div class="panel" id="panel"><div class="phead"><b id="ptitle"></b><div class="pacts"><button id="ppeek" title="Preview as a normal visitor" style="display:none"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg></button><button id="prefresh" data-i18n-title="act.refresh" title="Refresh"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5"/></svg></button><button id="pclose"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div></div><div class="pbody" id="pbody"></div></div>
<div class="dock intro">
  <button class="intro" data-p="riders" data-i18n-aria="dock.riders" aria-label="Riders"><svg class="ic" viewBox="0 0 24 24" fill="currentColor"><circle cx="8" cy="8" r="3.2"/><path d="M2.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5z"/><circle cx="17" cy="9" r="2.6"/><path d="M14.6 14.4c2.8-.7 5.6 1 6.4 4.6h-4.8z"/></svg><span class="lbl" data-i18n="dock.riders">Riders</span></button>
  <button class="intro" data-p="countries" data-i18n-aria="dock.countries" aria-label="Countries"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18"/></svg><span class="lbl" data-i18n="dock.countries">Countries</span></button>
  <button class="intro" data-p="wheels" data-i18n-aria="title.wheels" aria-label="Wheel models"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9.2"/><circle cx="12" cy="12" r="5.3"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><path d="M12 2.8v3.3M12 17.9v3.3M2.8 12h3.3M17.9 12h3.3M5.3 5.3l2.3 2.3M16.4 16.4l2.3 2.3M18.7 5.3l-2.3 2.3M7.6 16.4l-2.3 2.3" stroke-linecap="round"/></svg><span class="lbl" data-i18n="dock.wheels">Wheels</span></button>
  <button class="intro" data-p="brands" data-i18n-aria="title.brands" aria-label="Wheel brands"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M3 11V4h7l10.5 10.5L14 21 3 11Z"/><circle cx="7.3" cy="7.8" r="1.5" fill="currentColor" stroke="none"/></svg><span class="lbl" data-i18n="dock.brands">Brands</span></button>
  <button class="intro" data-p="records" data-i18n-aria="title.records" aria-label="All-time records"><svg class="ic" viewBox="0 0 24 24" fill="currentColor"><path d="M18 2H6v2H3v3a4 4 0 0 0 4 4 5 5 0 0 0 4 3.9V18H8v3h8v-3h-3v-3.1A5 5 0 0 0 17 11a4 4 0 0 0 4-4V4h-3V2Zm0 4h1v1a2 2 0 0 1-1 1.7V6ZM5 7V6h1v2.7A2 2 0 0 1 5 7Z"/></svg><span class="lbl" data-i18n="dock.records">Records</span></button>
  <button class="intro" data-p="tech" data-i18n-aria="title.tech" aria-label="App & devices"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="7" y="2" width="10" height="20" rx="2.6"/><path d="M10.5 18.5h3" stroke-linecap="round"/></svg><span class="lbl" data-i18n="dock.app">App</span></button>
</div>
<button id="gear" class="intro" data-i18n-title="aria.settings" title="Settings"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14 12.94a7.49 7.49 0 0 0 .05-.94 7.49 7.49 0 0 0-.05-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.61-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54a7 7 0 0 0-1.62.94l-2.39-.96a.5.5 0 0 0-.61.22L2.74 8.84a.5.5 0 0 0 .12.64l2.03 1.58a7.49 7.49 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32a.5.5 0 0 0 .61.22l2.39-.96a7 7 0 0 0 1.62.94l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54a7 7 0 0 0 1.62-.94l2.39.96a.5.5 0 0 0 .61-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58ZM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7Z"/></svg></button>
<div id="cfg" class="cfgpop"></div>
<div id="tip"></div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
const API="/api/v1";
const j=p=>fetch(API+p).then(r=>r.json());
const HIDE=Object.assign({boards:[],app:[],records:[],groups:{countries:[],wheels:[],brands:[]},sec:{}},window.__HIDE__||{});
const ADMIN=!!window.__ADMIN__;   // logged-in admin: show hidden metrics dimmed instead of removing them
let ASPUBLIC=false;               // admin "eye" toggle: preview exactly what a normal visitor sees
function isAdminView(){return ADMIN&&!ASPUBLIC;}
const HEAT=Object.assign({zoom:0.1,radius:60,zoom_growth:1,intensity:1,glow_floor:0.45,opacity:0.62},window.__HEAT__||{});
// --- i18n: browser auto-detect or saved cogwheel choice, English fallback per key ---
const I18N=window.__I18N__||{en:{}};
const LANGS=window.__LANGS__||{en:"English"};
function _mapLang(raw){if(!raw)return null;const lc=(""+raw).toLowerCase(),base=lc.split("-")[0],reg=(lc.split("-")[1]||"").toUpperCase();
  if(base==="pt")return "pt-BR";
  if(base==="zh")return /(^|-)(tw|hk|mo|hant)(-|$)/.test(lc)?"zh-Hant":"zh";
  if(base==="nb"||base==="nn"||base==="no")return "no";
  if(base==="es"){const LAT=["419","MX","AR","CO","CL","PE","VE","EC","GT","CU","BO","DO","HN","PY","SV","NI","CR","PA","UY","PR"];return LAT.indexOf(reg)>=0?"es-419":"es";}
  return LANGS[base]?base:null;}
function _detectLang(){try{const s=localStorage.getItem("eucstats_lang");if(s&&LANGS[s])return s;
  const navs=(navigator.languages&&navigator.languages.length)?navigator.languages:[navigator.language||"en"];
  for(let i=0;i<navs.length;i++){const m=_mapLang(navs[i]);if(m&&LANGS[m])return m;}}catch(e){}return "en";}
let LANG=_detectLang();
// only EN + the visitor's language are injected; fetch any other on demand
async function ensureLang(l){if(l==="en"||(I18N[l]&&Object.keys(I18N[l]).length))return;
  try{const d=await j("/i18n/"+encodeURIComponent(l));if(d&&!d.detail)I18N[l]=d;}catch(e){}}
function t(k,vars){let s=(I18N[LANG]&&I18N[LANG][k]);if(s==null)s=(I18N.en&&I18N.en[k]);if(s==null)s=k;
  if(vars)for(const p in vars)s=s.split("{"+p+"}").join(vars[p]);return s;}
function applyI18n(){
  document.querySelectorAll("[data-i18n]").forEach(e=>{e.textContent=t(e.getAttribute("data-i18n"));});
  document.querySelectorAll("[data-i18n-aria]").forEach(e=>{e.setAttribute("aria-label",t(e.getAttribute("data-i18n-aria")));});
  document.querySelectorAll("[data-i18n-title]").forEach(e=>{e.title=t(e.getAttribute("data-i18n-title"));});
  try{document.documentElement.lang=LANG;}catch(e){}}
const cc=c=>c?`<img class="flag" src="https://flagcdn.com/24x18/${(""+c).toLowerCase()}.png" alt="${c}" loading="lazy"/>`:"";
const _RN=(()=>{try{return new Intl.DisplayNames([navigator.language||"en"],{type:"region"});}catch(e){return null;}})();
const cname=c=>{if(!c)return "";try{return (_RN&&_RN.of((""+c).toUpperCase()))||c;}catch(e){return c;}};
const av=(id,has)=>has===false?'<span class="av avph"></span>':`<img class="av" alt="" src="${API}/riders/${encodeURIComponent(id)}/avatar" onerror="this.style.visibility='hidden'"/>`;
const rider=e=>`<span class="rider">${av(e.store_id,e.has_avatar)}${cc(e.flag)}<span>${e.name||e.store_id}</span></span>`;
const CROWN='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 7l4.5 4L12 4l4.5 7L21 7l-1.8 12H4.8L3 7Z"/></svg>';
const FLAG='<svg class="cflag" viewBox="0 0 24 24"><path d="M5 21V3" stroke="#caa12f" stroke-width="2" fill="none" stroke-linecap="round"/><path class="cflagwave" d="M6 4h11l-2.4 3.3L17 10.6H6z" fill="#ffd24a"/></svg>';
const CHEV='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4v3a2 2 0 0 1-2 2H4M20 9h-3a2 2 0 0 1-2-2V4M4 15h3a2 2 0 0 1 2 2v3M15 20v-3a2 2 0 0 1 2-2h3"/></svg>';
const IC={
 early:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 18h18M12 9a4 4 0 0 1 4 4H8a4 4 0 0 1 4-4zM12 5V3M5 9 3.5 7.5M19 9l1.5-1.5"/></svg>',
 peak:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 20h18L14 7l-3.5 6L8 10z"/></svg>',
 energy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 21V10l5 3V10l5 3V7l8 5v9z"/></svg>',
 explorer:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15.5 8.5 13 13l-4.5 2.5L11 11z"/></svg>',
 bigday:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M12 13v4M10 15h4" stroke-linecap="round"/></svg>',
 commuter:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
 frequent:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>',
 marathon:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="14" r="8"/><path d="M12 14V9.5M9 2h6"/></svg>',
 pace:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 14l4-2.5"/></svg>',
 battery:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="8" width="16" height="9" rx="2"/><path d="M21 11v3" stroke-linecap="round"/><path d="M6 12.5h5" stroke-linecap="round"/></svg>',
 night:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>',
 weekend:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/><circle cx="15.5" cy="15.5" r="1.8" fill="currentColor" stroke="none"/></svg>',
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
 streak:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2c1 4-2 5-2 8a2 2 0 0 0 4 0c2 2 3 4 3 6a5 5 0 0 1-10 0c0-4 4-6 5-14z"/></svg>',
 ascent:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 20h18L14 7l-3.2 5.6L8.5 9 3 20z"/></svg>',
 range:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="17" height="10" rx="2"/><path d="M22 10v4"/><path d="M5.5 10v4M8.5 10v4M11.5 10v4" stroke-linecap="round"/></svg>',
 efficiency:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 20c0-9 7-15 16-15 0 10-6 16-15 16 1-4 4-7 8-9-4 1-7 3-9 8z"/></svg>',
 hours:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M9 2h6"/></svg>',
 cruise:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9h12v4a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z"/><path d="M16 10h2a2 2 0 0 1 0 4h-2"/><path d="M7 3c0 1.2-1 1.2-1 2.5M11 3c0 1.2-1 1.2-1 2.5"/></svg>',
 globe:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18"/></svg>',
 altking:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 20l6-11 4 6 2-3 6 8z"/><path d="M9 9l1.4-3"/></svg>',
 freespin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v4h-4"/></svg>',
 sag:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="15" height="10" rx="2"/><path d="M21 10v4"/><path d="M8 9l3 3-3 3"/></svg>',
 rocket:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 3c4 .5 6.5 3.5 7 7-3 .5-4.6 2.2-6 5l-3-3c1.4-3.6 1.2-6.6 2-9zM8 16l-4 4m6-2l-4 4m0-6l-2 2"/></svg>'};
const GIC_PPL='<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="8" r="3"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0z"/></svg>';
const GIC_TRIP='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 19c3-2 4-6 4-9a3 3 0 0 1 6 0c0 4 1 7 4 9"/></svg>';
const BOARDS=[
 {k:"mileage",c:"total_km",u:" km",conv:"dist"},
 {k:"daily",c:"best_day_km",u:" km",conv:"dist"},
 {k:"week",c:"best_week_km",u:" km",conv:"dist"},
 {k:"month",c:"best_month_km",u:" km",conv:"dist"},
 {k:"speed",c:"best_speed",u:" km/h",conv:"spd"},
 {k:"accel",c:"accel_s",u:" s"},
 {k:"gforce",c:"best_gforce",u:" g"},
 {k:"voltage",c:"peak_voltage",u:" V"},
 {k:"streak",c:"longest_streak",u:" d"},
 {k:"ascent",c:"ascent_m",u:" m",conv:"alt"},
 {k:"range",c:"range_km",u:"",conv:"dist"},
 {k:"efficiency",c:"wh_per_km",u:" Wh/km"},
 {k:"hours",c:"hours",u:" h"},
 {k:"cruise",c:"slow_km",u:"",conv:"dist"},
 {k:"globe",c:"countries",u:""},
 {k:"altking",c:"alt_range",u:" m",conv:"alt"},
 {k:"frequent",c:"trips_total",u:""},
 {k:"marathon",c:"ride_hours",u:" h"},
 {k:"pace",c:"avg_speed",conv:"spd"},
 {k:"night",c:"night_rides",u:""},
 {k:"weekend",c:"weekend_km",conv:"dist"},
 {k:"early",c:"morning_rides",u:""},
 {k:"peak",c:"peak_ascent",u:" m",conv:"alt"},
 {k:"energy",c:"energy_kwh",u:" kWh"},
 {k:"explorer",c:"areas",u:""},
 {k:"bigday",c:"rides_in_day",u:""},
 {k:"commuter",c:"weekday_km",conv:"dist"},
 {k:"freespin",c:"freespin_kmh",conv:"spd"}];
BOARDS.forEach(b=>{b.nk="b."+b.k+".n";b.dk="b."+b.k+".d";});   // i18n keys (English lives in i18n.py)
// gated boards (server spec): same name per metric, gate baked in, value under "v"
(window.__GATED__||[]).forEach(g=>{BOARDS.push({k:g.k,nk:"b."+g.base+".n",dk:"b."+g.base+".d",c:"v",u:g.u,conv:g.conv||undefined,ic:g.ic,min_s:g.min_s,min_km:g.min_km});});
// --- units (km/h <-> mph), remembered + smart default by locale; + map style ---
const MI=0.621371, MPH_REGIONS=["US","GB","LR","MM"];
function defaultUnit(){try{const r=((navigator.language||"").split("-")[1]||"").toUpperCase();return MPH_REGIONS.includes(r)?"mph":"kmh";}catch(e){return "kmh";}}
let UNIT=localStorage.getItem("eucstats_unit")||defaultUnit();
const RASTER=(t,a)=>({version:8,sources:{r:{type:"raster",tiles:[t],tileSize:256,attribution:a,maxzoom:19}},layers:[{id:"r",type:"raster",source:"r"}]});
const STYLES={dark:"https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",light:"https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",voyager:"https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",satellite:RASTER("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}","© Esri, Maxar"),terrain:RASTER("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}","© Esri")};
let MAPSTYLE=localStorage.getItem("eucstats_style")||(window.__CFG__&&window.__CFG__.map_style)||"dark";
const mph=()=>UNIT==="mph";
const r1=n=>Math.round((+n||0)*10)/10, r2=n=>Math.round((+n||0)*100)/100;
const dnum=km=>mph()?(""+r1(km*MI)):(""+r1(km)), dunit=()=>mph()?"mi":"km";
const snum=kmh=>mph()?(""+r1(kmh*MI)):(""+r1(kmh)), sunit=()=>mph()?"mph":"km/h";
const tnum=c=>mph()?(""+r1(c*9/5+32)):(""+r1(c)), tunit=()=>mph()?"°F":"°C";   // temperature
const anum=m=>mph()?(""+Math.round(m*3.28084)):(""+Math.round(m)), aunit=()=>mph()?"ft":"m";   // altitude
function bval(b,v){if(v==null)v=0;
  if(b.conv==="dist")return dnum(v)+" "+dunit();
  if(b.conv==="spd")return snum(v)+" "+sunit();
  if(b.conv==="temp")return tnum(v)+tunit();
  if(b.conv==="alt")return anum(v)+" "+aunit();
  return r2(v)+b.u;}
function bt(b){return t(b.nk);}
function bd(b){var s=(b.dk==="b.accel.d"&&mph())?t("b.accel.d_mph"):t(b.dk);
  if(b.min_s){s+=" · ≥"+Math.round(b.min_s/60)+"min ≥"+dnum(b.min_km)+dunit();}   // gate, unit-aware
  return s;}
const RECCONV={mileage_king:"dist",longest_trip:"dist",top_speed:"spd"};
const RECUNIT={sustained_w:" W",sustained_a:" A",peak_voltage:" V",max_gforce:" g"};
function recval(k,v){const c=RECCONV[k];if(c==="dist")return dnum(v)+" "+dunit();if(c==="spd")return snum(v)+" "+sunit();return (Math.round(v*100)/100)+(RECUNIT[k]||"");}
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
  const R=1.7;                                                          // max privacy noise per axis (km)
  const dxKm=((hashStr(key+"x")%1000)/1000)*2*R-R, dyKm=((hashStr(key+"y")%1000)/1000)*2*R-R;  // -R..+R
  const olat=e.lat+dyKm/111, olon=e.lon+dxKm/(111*(Math.cos(e.lat*Math.PI/180)||1e-6));
  showArea(olon,olat,2.7);                                             // dotted ring near the real area
  map.flyTo({center:[olon,olat],zoom:11.2,curve:1.9,duration:2800,easing:easeInOutCubic,essential:true});
}
const CENTROIDS={US:[-98,39,4],GB:[-2,54,5],DE:[10,51,5.2],FR:[2.5,47,5],NO:[9,61,4.6],SE:[16,62,4.4],NL:[5.3,52,6.3],ES:[-3.7,40,5.2],IT:[12.5,42,5.2],PL:[19,52,5.2],CA:[-100,56,3.6],AU:[134,-25,3.9],JP:[138,37,4.6],FI:[26,64,4.4],DK:[10,56,6.2],CH:[8.2,46.8,6.4],AT:[14.5,47.5,5.8],CZ:[15.5,49.8,6.2],PT:[-8,39.5,5.8],SG:[103.8,1.35,9],BR:[-50,-12,3.7],MX:[-102,23,4.5]};
function flyToCountry(arg){
  if(!map)return;closePanel();
  const isObj=arg&&typeof arg==="object";
  const code=isObj?arg.country:arg, c=CENTROIDS[(""+code).toUpperCase()];
  const lon=(isObj&&arg.lon!=null)?arg.lon:(c?c[0]:null);   // pan to where riders actually are
  const lat=(isObj&&arg.lat!=null)?arg.lat:(c?c[1]:null);
  if(lon==null||lat==null)return;
  map.flyTo({center:[lon,lat],zoom:5.8,curve:1.6,duration:2400,easing:easeInOutCubic,essential:true});  // not too near
}
function fitTop3(rows,coordFn){
  if(!map||!rows||!rows.length)return;
  const pts=rows.slice(0,3).map(coordFn).filter(p=>p&&p[0]!=null&&p[1]!=null);
  if(!pts.length)return;
  const b=new maplibregl.LngLatBounds();pts.forEach(p=>b.extend(p));
  try{map.fitBounds(b,{padding:{top:90,bottom:340,left:50,right:50},maxZoom:7,duration:2200,essential:true});}catch(e){}
}
let flowRunning=false;
function flowClear(){if(!map)return;["flow-glow","flow-line","flow-pt"].forEach(l=>{if(map.getLayer(l))map.removeLayer(l);});["flow","flowpts"].forEach(s=>{if(map.getSource(s))map.removeSource(s);});flowRunning=false;}
function flowDash(){flowRunning=true;(function step(){if(!flowRunning||!map.getLayer("flow-line"))return;const s=Math.floor((performance.now()/55)%DASH.length);map.setPaintProperty("flow-line","line-dasharray",DASH[s]);requestAnimationFrame(step);})();}
function arcCoords(a,b,n=48,bulge=0.22){   // curved bezier arc, always bulging UP (north) — flight-path look
  const x1=a[0],y1=a[1],x2=b[0],y2=b[1],mx=(x1+x2)/2,my=(y1+y2)/2,dx=x2-x1,dy=y2-y1;
  let ox=-dy,oy=dx;if(oy<0){ox=-ox;oy=-oy;}   // force the perpendicular bulge toward the top of the map
  const cx=mx+ox*bulge,cy=my+oy*bulge,out=[];
  for(let i=0;i<=n;i++){const t=i/n,u=1-t;out.push([u*u*x1+2*u*t*cx+t*t*x2,u*u*y1+2*u*t*cy+t*t*y2]);}
  return out;
}
function brandFlow(brand){
  if(!map)return;
  j("/groups/brand/"+encodeURIComponent(brand)+"/flow").then(d=>{
    if(!d||!d.factory||!d.points||!d.points.length)return;
    closePanel();flowClear();
    const F=[d.factory.lon,d.factory.lat];
    const arcs=d.points.map(p=>arcCoords(F,[p.lon,p.lat]));
    map.addSource("flow",{type:"geojson",data:{type:"FeatureCollection",features:[]}});
    map.addSource("flowpts",{type:"geojson",data:{type:"FeatureCollection",features:[{type:"Feature",properties:{f:1},geometry:{type:"Point",coordinates:F}}].concat(d.points.map(p=>({type:"Feature",properties:{f:0},geometry:{type:"Point",coordinates:[p.lon,p.lat]}})))}});
    map.addLayer({id:"flow-glow",type:"line",source:"flow",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":"#39c0ff","line-width":5,"line-blur":6,"line-opacity":0.32}});
    map.addLayer({id:"flow-line",type:"line",source:"flow",layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":"#cfe9ff","line-width":1.7,"line-opacity":0.85}});
    map.addLayer({id:"flow-pt",type:"circle",source:"flowpts",paint:{"circle-radius":["case",["==",["get","f"],1],8,5],"circle-color":["case",["==",["get","f"],1],"#ffd24a","#39c0ff"],"circle-blur":0.3,"circle-opacity":0,"circle-opacity-transition":{duration:700},"circle-stroke-color":"#eaf4ff","circle-stroke-width":1.4,"circle-stroke-opacity":0,"circle-stroke-opacity-transition":{duration:700}}});
    map.flyTo({center:F,zoom:4.2,duration:1500,curve:1.5,essential:true});
    setTimeout(()=>{const b=new maplibregl.LngLatBounds();b.extend(F);d.points.forEach(p=>b.extend([p.lon,p.lat]));try{map.fitBounds(b,{padding:70,duration:3000,maxZoom:5,essential:true});}catch(e){}try{map.setPaintProperty("flow-pt","circle-opacity",0.95);map.setPaintProperty("flow-pt","circle-stroke-opacity",0.95);}catch(e){}},1500);
    let t0=null;flowRunning=true;
    function grow(now){
      if(!flowRunning||!map.getSource("flow"))return;
      if(t0===null)t0=now;const t=Math.min(1,(now-t0)/1900),e=1-Math.pow(1-t,3);
      const feats=arcs.map(a=>({type:"Feature",geometry:{type:"LineString",coordinates:a.slice(0,Math.max(2,Math.round(a.length*e)))}}));
      try{map.getSource("flow").setData({type:"FeatureCollection",features:feats});}catch(e2){}
      if(t<1)requestAnimationFrame(grow);else flowDash();
    }
    setTimeout(()=>requestAnimationFrame(grow),700);
    setTimeout(()=>{flowRunning=false;["flow-glow","flow-line"].forEach(l=>{try{map.setPaintProperty(l,"line-opacity-transition",{duration:700});map.setPaintProperty(l,"line-opacity",0);}catch(e){}});try{map.setPaintProperty("flow-pt","circle-opacity",0);map.setPaintProperty("flow-pt","circle-stroke-opacity",0);}catch(e){}},8200);
    setTimeout(flowClear,9400);
  }).catch(()=>{});
}

const pbody=document.getElementById("pbody"),panel=document.getElementById("panel"),ptitle=document.getElementById("ptitle");
let openPanel=null;
function RA(i){return i<3?('animation:rowin .5s both, shinebg 7s ease-in-out '+(1.1+i*0.55)+'s infinite'):('animation:rowin .5s both;animation-delay:'+(i*55)+'ms');}
function GSB(i){return i===0?' gold1':i===1?' silv':i===2?' brnz':'';}
function _tipEl(){return document.getElementById("tip");}
function showTip(html,x,y){const T=_tipEl();if(!T)return;T.innerHTML=html;T.classList.add("on");const r=T.getBoundingClientRect();let nx=x-r.width/2,ny=y-r.height-10;nx=Math.max(8,Math.min(innerWidth-r.width-8,nx));if(ny<8)ny=y+22;T.style.left=nx+"px";T.style.top=ny+"px";}
function hideTip(){const T=_tipEl();if(T)T.classList.remove("on");}
function setCap(icon,desc){const c=document.getElementById("tabcap");if(c)c.innerHTML=(icon||'')+"<span>"+(desc||'')+"</span>";}
const CANHOVER=matchMedia("(hover:hover)").matches;
function bindTips(root,hoverOnly){(root||document).querySelectorAll("[data-tip]").forEach(el=>{const show=()=>{const b=el.getBoundingClientRect();showTip(el.dataset.tip,b.left+b.width/2,b.top);};if(CANHOVER){el.onmouseenter=show;el.onmouseleave=hideTip;}if(!hoverOnly)el.addEventListener("click",e=>{e.stopPropagation();const T=_tipEl();(T&&T.classList.contains("on"))?hideTip():show();});});}
document.addEventListener("click",hideTip);
function countUp(el,target,dur,dec){const t=+target||0;let from;
  if(el.dataset.cur!==undefined){from=+el.dataset.cur;}
  else{const digits=Math.max(1,Math.floor(Math.abs(t)).toString().length);const fl=Math.pow(10,digits-1);from=Math.max(fl,t*0.7);if(from>=t)from=Math.max(0,t-Math.max(1,t*0.3));}
  const delta=t-from;let s0=null;
  function step(now){if(s0===null)s0=now;let p=Math.min(1,(now-s0)/dur);p=1-Math.pow(1-p,3);const v=from+delta*p;el.textContent=dec?v.toFixed(dec):Math.round(v).toLocaleString();if(p<1)requestAnimationFrame(step);else{el.textContent=dec?t.toFixed(dec):Math.round(t).toLocaleString();el.dataset.cur=""+t;}}
  requestAnimationFrame(step);}
const DOCK=["riders","countries","wheels","brands","records","tech"];
function setPanel(name,title,html){
  const prev=openPanel;
  openPanel=name;ptitle.textContent=title;pbody.innerHTML=html;panel.dataset.sec=name;panel.classList.add("open");
  if(prev!==name){   // animate on open/switch only — NOT on in-place refresh (prev===name)
    let anim="panUp";
    if(prev!==null){const oi=DOCK.indexOf(prev),ni=DOCK.indexOf(name);anim=(ni>oi)?"panRight":"panLeft";}
    panel.style.animation="none";void panel.offsetWidth;
    panel.style.animation=anim+" .4s cubic-bezier(.2,.8,.2,1)";
  }
  document.querySelectorAll(".dock button").forEach(b=>b.classList.toggle("on",b.dataset.p===name));
}
function closePanel(){
  if(openPanel===null&&!panel.classList.contains("open"))return;
  openPanel=null;
  document.querySelectorAll(".dock button").forEach(b=>b.classList.remove("on"));
  panel.style.animation="none";void panel.offsetWidth;
  panel.style.animation="panDown .28s ease both";
  setTimeout(()=>{panel.classList.remove("open");panel.style.animation="";},280);
}
document.getElementById("pclose").onclick=closePanel;
function refreshPanel(){const p=openPanel;if(p)HANDLERS[p]();}   // reload in place, no re-animate
document.getElementById("prefresh").onclick=()=>{const b=document.getElementById("prefresh");b.classList.add("spin");refreshPanel();setTimeout(()=>b.classList.remove("spin"),650);};
// admin-only "eye": flip between the dimmed preview and exactly what a normal visitor sees
(function(){const pk=document.getElementById("ppeek");if(!pk)return;
  if(!ADMIN){pk.style.display="none";return;}
  pk.style.display="";
  pk.onclick=()=>{ASPUBLIC=!ASPUBLIC;pk.classList.toggle("on",ASPUBLIC);
    pk.title=ASPUBLIC?"Showing the public view — click to reveal hidden":"Preview as a normal visitor";
    applyDock();const p=openPanel;if(p){openPanel=null;HANDLERS[p]();}};})();

const MEDAL='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18 2H6v2H3v3a4 4 0 0 0 4 4 5 5 0 0 0 4 3.9V18H8v3h8v-3h-3v-3.1A5 5 0 0 0 17 11a4 4 0 0 0 4-4V4h-3V2Zm0 4h1v1a2 2 0 0 1-1 1.7V6ZM5 7V6h1v2.7A2 2 0 0 1 5 7Z"/></svg>';
const WHEELIC='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9.2"/><circle cx="12" cy="12" r="5.3"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><path d="M12 2.8v3.3M12 17.9v3.3M2.8 12h3.3M17.9 12h3.3M5.3 5.3l2.3 2.3M16.4 16.4l2.3 2.3M18.7 5.3l-2.3 2.3M7.6 16.4l-2.3 2.3" stroke-linecap="round"/></svg>';
const BRANDIC='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M3 11V4h7l10.5 10.5L14 21 3 11Z"/><circle cx="7.3" cy="7.8" r="1.5" fill="currentColor" stroke="none"/></svg>';
const BRANDSLUG=n=>(""+n).toLowerCase().replace(/[^a-z0-9]+/g,"");
function brandLogo(name){const mono=(name||"?").replace(/[^A-Za-z0-9]/g,"").slice(0,2).toUpperCase();
  return `<span class="blogo"><span class="bmono">${mono}</span><img alt="" src="/static/brands/${BRANDSLUG(name)}.png" onerror="this.remove()"></span>`;}
function podList(rows,cfg){
  if(!rows||!rows.length) return '<div class="empty">'+t("empty.nodata")+'</div>';
  const o=[1,0,2],rkn=[t("pod.1"),t("pod.2"),t("pod.3")],cls=["gold1","silv","brnz"],top=rows.slice(0,3),fl=e=>cfg.flag?cc(cfg.flag(e)):'';
  const pod=`<div class="podium">`+o.filter(i=>top[i]).map(i=>{const e=top[i];return `<div class="pod p${i+1} ${cls[i]}" data-i="${i}" style="animation:rowin .55s both;animation-delay:${i*90}ms"><div class="rkn">${rkn[i]}</div>${cfg.av?av(e.store_id,e.has_avatar):(cfg.iconFn?`<div class="podic">${cfg.iconFn(e)}</div>`:(cfg.icon?`<div class="podic">${cfg.icon}</div>`:''))}<div class="pname">${fl(e)} ${cfg.label(e)}</div><div class="km">${cfg.val(e)}</div>${cfg.sub?`<div class="psub">${cfg.sub(e)}</div>`:''}</div>`;}).join("")+`</div>`;
  const rest=rows.slice(3);let list='';
  if(rest.length) list=`<table><tbody>`+rest.map((e,i)=>`<tr class="${cfg.click?'sel':''}" data-i="${i+3}" style="animation:rowin .5s both;animation-delay:${i*45}ms"><td class=rk>${i+4}</td><td><span class="celln">${cfg.av?av(e.store_id,e.has_avatar):''}${fl(e)}<span>${cfg.label(e)}</span></span></td><td class=val>${cfg.val(e)}</td>${cfg.sub?`<td class="val sub">${cfg.sub(e)}</td>`:''}</tr>`).join("")+`</tbody></table>`;
  return pod+list;
}
function showRiders(){
  const isH=b=>HIDE.boards.includes(b.k);
  const vis=isAdminView()?BOARDS:BOARDS.filter(b=>!isH(b));
  setPanel("riders",t("title.riders"),`<div class="tabs${vis.length<6?' onerow':''}">${vis.map((b,i)=>`<button class="tab${i?'':' on'}${isH(b)?' peek':''}" data-b="${b.k}" data-tip="${(bd(b)||'').replace(/"/g,'&quot;')}">${IC[b.k]||IC[b.ic]||''}<span>${bt(b)}</span></button>`).join("")}</div><div class="tabcap" id="tabcap"></div><div id="lb"></div>`);
  pbody.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{pbody.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));t.classList.add("on");loadBoard(t.dataset.b);});
  bindTips(pbody,true);if(vis[0])loadBoard(vis[0].k);
}
async function loadBoard(k){
  const b=BOARDS.find(x=>x.k===k),rows=(await j(`/leaderboards/${k}?limit=30`)).entries,cont=document.getElementById("lb");
  if(!cont)return;
  if(b)setCap(IC[b.k]||IC[b.ic]||'',bd(b));
  cont.innerHTML=podList(rows,{av:true,flag:e=>e.flag,label:e=>e.name||e.store_id,val:e=>bval(b,e[b.c]),click:true});
  cont.querySelectorAll("[data-i]").forEach(el=>el.onclick=()=>flyToRider(rows[+el.dataset.i]));
  fitTop3(rows,e=>[e.lon,e.lat]);
}
const GBOARDS=[
 {k:"dist",key:"total_km",conv:"dist",ic:IC.mileage},
 {k:"speed",key:"top_speed",conv:"spd",ic:IC.speed},
 {k:"accel",key:"accel_s",u:" s",asc:true,ic:IC.accel},
 {k:"gforce",key:"max_gforce",u:" g",ic:IC.gforce},
 {k:"power",key:"sustained_w",u:" W",ic:IC.power},
 {k:"current",key:"sustained_a",u:" A",ic:IC.current},
 {k:"voltage",key:"peak_voltage",u:" V",ic:IC.voltage},
 {k:"riders",key:"riders",u:"",ic:GIC_PPL},
 {k:"trips",key:"trips",u:"",ic:GIC_TRIP},
 {k:"ascent",key:"ascent_m",u:" m",ic:IC.ascent},
 {k:"range",key:"range_km",conv:"dist",ic:IC.range},
 {k:"eff",key:"wh_per_km",u:" Wh/km",asc:true,ic:IC.efficiency}];
// group-board i18n keys: names reuse the rider trophies where they match; descs are the short "g.*" set
const GNK={dist:"b.mileage.n",speed:"b.speed.n",accel:"b.accel.n",gforce:"b.gforce.n",power:"b.power.n",current:"b.current.n",voltage:"b.voltage.n",riders:"g.riders",trips:"g.rides",ascent:"b.ascent.n",range:"b.range.n",eff:"b.efficiency.n"};
GBOARDS.forEach(b=>{b.nk=GNK[b.k]||("b."+b.k+".n");b.dk="g."+b.k+".d";});
let GROWS=null;
function gval(b,e){const v=e[b.key];if(v==null)return "—";if(b.conv==="dist")return dnum(v)+" "+dunit();if(b.conv==="spd")return snum(v)+" "+sunit();return r2(v)+(b.u||"");}
function renderGroup(b,cfg){
  const cont=document.getElementById("lb");if(!cont||!GROWS)return;
  setCap(b.ic||'',bd(b));
  const rows=GROWS.filter(e=>e[b.key]!=null).slice().sort((x,y)=>b.asc?((x[b.key]||1e9)-(y[b.key]||1e9)):((y[b.key]||0)-(x[b.key]||0)));
  cont.innerHTML=podList(rows,Object.assign({label:e=>e.name||e.country,val:e=>gval(b,e),sub:e=>t("u.riders",{n:e.riders})},cfg));
  if(cfg.click){cont.querySelectorAll("[data-i]").forEach(el=>el.onclick=()=>flyToCountry(rows[+el.dataset.i]));
    fitTop3(rows,e=>{const c=CENTROIDS[(e.country||"").toUpperCase()];return c?[c[0],c[1]]:null;});}
  if(cfg.flow) cont.querySelectorAll("[data-i]").forEach(el=>el.onclick=()=>brandFlow(rows[+el.dataset.i].name));
}
async function showGroupPanel(kind,name,title,cfg){
  GROWS=(await j("/groups/"+kind)).entries;
  const hid=(HIDE.groups&&HIDE.groups[name])||[];   // each section keeps its own hidden tabs
  const isH=b=>hid.includes(b.k);
  const vis=isAdminView()?GBOARDS:GBOARDS.filter(b=>!isH(b));
  setPanel(name,title,`<div class="tabs${vis.length<6?' onerow':''}">${vis.map((b,i)=>`<button class="tab${i?'':' on'}${isH(b)?' peek':''}" data-b="${i}" data-tip="${(bd(b)||'').replace(/"/g,'&quot;')}">${b.ic||''}<span>${bt(b)}</span></button>`).join("")}</div><div class="tabcap" id="tabcap"></div><div id="lb"></div>`);
  pbody.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{pbody.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));t.classList.add("on");renderGroup(vis[+t.dataset.b],cfg);});
  bindTips(pbody,true);if(vis[0])renderGroup(vis[0],cfg);
}
function showCountries(){showGroupPanel("country","countries",t("title.countries"),{flag:e=>e.country,label:e=>cname(e.country)||e.country,click:true});}
function showWheels(){showGroupPanel("wheel","wheels",t("title.wheels"),{icon:WHEELIC,label:e=>e.name,sub:e=>(e.brand?e.brand+" · ":"")+t("u.riders",{n:e.riders||0})});}
function showBrands(){showGroupPanel("brand","brands",t("title.brands"),{iconFn:e=>brandLogo(e.name),flow:true});}
async function showRecords(){
  const recs=(await j("/records")).filter(r=>r.value!=null&&(isAdminView()||!HIDE.records.includes(r.key)));
  setPanel("records",t("title.records"),`<div class="recs">${recs.map((r,i)=>`<div class="rec sel${HIDE.records.includes(r.key)?' peek':''}" data-i="${i}" style="animation:rowin .5s both;animation-delay:${i*60}ms"><div class="recmed">${MEDAL}</div><div class="recmain"><div class="reclbl">${t("rec."+r.key)}</div><div class="recrider">${cc(r.rider.flag)}${av(r.rider.store_id,r.rider.has_avatar)}<span>${r.rider.name||r.rider.store_id}</span></div></div><div class="recval">${recval(r.key,r.value)}</div></div>`).join("")||'<div class="empty">'+t("empty.norecords")+'</div>'}</div>`);
  pbody.querySelectorAll(".rec.sel").forEach(el=>el.onclick=()=>flyToRider(recs[+el.dataset.i].rider));
}
async function showTech(){
  const d=await j("/stats/versions");
  const fn=e=>`${cc(e.country)} ${cname(e.country)}`;
  const rl=e=>`<span class="celln">${av(e.store_id,e.has_avatar)}${cc(e.flag)}<span>${e.name||e.store_id}</span></span>`;
  const sec=(key,t,h)=>{var hid=HIDE.app.includes(key);if(hid&&!isAdminView())return "";return `<div class="vsec${hid?' peek':''}"><div class="vtitle">${t}</div>${h}</div>`;};
  const tbl=(arr,lab,val)=>`<table>${(arr||[]).slice(0,8).map((e,i)=>`<tr><td class=rk>${i+1}</td><td>${lab(e)}</td><td class=val>${val(e)}</td></tr>`).join("")||'<tr><td class=mut>'+t("empty.nodata")+'</td></tr>'}</table>`;
  const bars=(arr,lab)=>{const a=(arr||[]).slice(0,8),tot=a.reduce((s,e)=>s+(e.riders||0),0)||1;return a.length?`<div class=blist>${a.map((e,i)=>{const pct=Math.round(100*(e.riders||0)/tot);return `<div class=brow><span class=bfill style="width:${pct}%"></span><span class=brk>${i+1}</span><span class=blab>${lab(e)}</span><span class=bpct>${pct}%</span></div>`;}).join("")}</div>`:'<p class=mut>'+t("empty.nodata")+'</p>';};
  const body=sec("adoption","📊 "+t("tech.adoption"),d.latest?`<p class=mut style="margin:2px 0 0">${t("tech.adoptionPct",{pct:d.latest_pct,ver:d.latest})}</p>`:'<p class=mut>'+t("empty.nodata")+'</p>')+
    sec("adopters","🚀 "+t("tech.adopters"),tbl(d.adopters,rl,e=>"v"+(e.ver||"?")))+
    sec("laggards","🐢 "+t("tech.laggards"),tbl(d.laggards,rl,e=>"v"+(e.ver||"?")))+
    sec("appvers","📱 "+t("tech.appvers"),bars(d.appvers,e=>"v"+e.version))+
    sec("osvers","🤖 "+t("tech.osvers"),bars(d.osvers,e=>e.version))+
    sec("countries","🌍 "+t("tech.countries"),bars(d.countries,e=>`${cc(e.country)} ${cname(e.country)||e.country} · v${e.version||"?"}`));
  setPanel("tech",t("title.tech"),body||'<div class="empty">'+t("empty.noapp")+'</div>');
}
const HANDLERS={riders:showRiders,countries:showCountries,wheels:showWheels,brands:showBrands,records:showRecords,tech:showTech};
document.querySelectorAll(".dock button").forEach(b=>b.onclick=()=>{if(openPanel===b.dataset.p)closePanel();else HANDLERS[b.dataset.p]();});
function applyDock(){const SEC=HIDE.sec||{};
  document.querySelectorAll('.dock button[data-p]').forEach(b=>{var hid=SEC[b.dataset.p];
    if(hid&&isAdminView()){b.classList.add('peek');b.style.display='';}
    else if(hid){b.classList.remove('peek');b.style.display='none';}
    else{b.classList.remove('peek');b.style.display='';}});}
applyDock();

function reveal(el,d){ if(el) setTimeout(()=>el.classList.add("show"),d); }
function runIntro(){
  reveal(document.querySelector(".topbar"),1100);
  setTimeout(()=>animateChips(true),1450);   // slow count-up once the topbar is fading in (so it's visible)
  reveal(document.querySelector(".dock"),2300);
  document.querySelectorAll(".dock button").forEach((b,i)=>reveal(b,2700+i*320));
  reveal(document.querySelector(".rfoot"),4100);
  reveal(document.querySelector("#gear"),4450);
}

function renderHeader(){renderChips();renderChampions();}
function renderChips(){
  const chips=[[t("chip.riders"),S.riders,0,"riders"],[t("chip.trips"),S.trips,0,"trips"],[t("chip.total",{unit:dunit()}),mph()?r1(S.total_km*MI):r1(S.total_km),1,"total"],[t("chip.countries"),S.countries,0,"countries"]];
  document.getElementById("chips").innerHTML=chips.map(([l,v,dec,k])=>`<span class="chip"><b data-cv="${v}" data-dec="${dec}" data-k="${k}">0</b> ${l}</span>`).join("");
}
function animateChips(slow){const durs=slow?[2700,3500,3000,3900]:[850,1250,1050,1450];document.querySelectorAll("#chips b[data-cv]").forEach((b,i)=>countUp(b,+b.dataset.cv,durs[i%4],+b.dataset.dec));}
const GLITCHSEL=".clab,.cline b,.dock .lbl,.chip b,.cscore,.tab span,.rk,.recval,.reclbl";
function randomGlitch(){const _C=window.__CFG__||{};const _gi=_C.glitch_intensity||2;const _gb=(_C.glitch_secs||4)*1000;const els=[].slice.call(document.querySelectorAll(GLITCHSEL)).filter(e=>e.offsetParent!==null);if(els.length){const n=1+((Math.random()*_gi)|0);for(let k=0;k<n;k++){const el=els[(Math.random()*els.length)|0];el.classList.remove("rgbglitch");void el.offsetWidth;el.classList.add("rgbglitch");setTimeout(()=>el.classList.remove("rgbglitch"),650);}}setTimeout(randomGlitch,_gb*0.6+Math.random()*_gb*0.8);}
if(!(window.__CFG__&&window.__CFG__.glitch_enabled===false))setTimeout(randomGlitch,2800);
async function pollStats(){
  try{const ns=await j("/stats/summary"),nc=await j("/champions");S=ns;WC=nc;
    const map={riders:ns.riders,trips:ns.trips,total:mph()?r1(ns.total_km*MI):r1(ns.total_km),countries:ns.countries};
    document.querySelectorAll("#chips b[data-k]").forEach(b=>{const tgt=map[b.dataset.k];if(""+tgt!==b.dataset.cv){b.dataset.cv=""+tgt;countUp(b,tgt,1000,+b.dataset.dec);}});
    renderChampions();
  }catch(e){}
}
function renderChampions(){
  const ch=document.getElementById("champ");
  const C=WC||{};
  if(!(C.day||C.week||C.month)){ch.style.display="none";return;}
  ch.style.display="block";ch.style.cursor="default";ch.onclick=null;
  const line=(lab,c)=>c?`<div class="cline" data-sid="${c.store_id}"><span class="clab">${lab}</span>${cc(c.flag)}<b>${c.name||c.store_id}</b><span class="cscore">${t("champ.pts",{n:c.score})}</span></div>`:`<div class="cline"><span class="clab">${lab}</span><span class="mut">${t("champ.norides")}</span></div>`;
  const tip=((C.formula?`<b>${C.formula}</b><br>`:"")+t("champ.tip")).replace(/"/g,"&quot;");
  ch.innerHTML=`<div class="chead">${FLAG}<span>${t("champ.title")}</span><button class="cinfo" data-tip="${tip}">&#9432;</button><button class="ccol" title="${t("champ.toggle")}">${CHEV}</button></div>`+
    line(t("champ.day"),C.day)+line(t("champ.week"),C.week)+line(t("champ.month"),C.month);
  ch.querySelectorAll(".cline[data-sid]").forEach(el=>{el.style.cursor="pointer";el.onclick=()=>{const c=[C.day,C.week,C.month].find(x=>x&&x.store_id===el.dataset.sid);if(c)flyToRider(c);};});
  const tb=document.querySelector(".topbar");
  const setC=(v)=>{if(tb)tb.classList.toggle("collapsed",v);try{localStorage.setItem("eucstats_champ_collapsed",v?"1":"0");}catch(_){}};
  const col=ch.querySelector(".ccol");
  if(col)col.onclick=(e)=>{e.stopPropagation();setC(!(tb&&tb.classList.contains("collapsed")));};
  ch.onclick=()=>{if(tb&&tb.classList.contains("collapsed"))setC(false);};
  setC(localStorage.getItem("eucstats_champ_collapsed")==="1");
  bindTips(ch);
}
let CELLS=null;
function addHeat(){
  if(!CELLS||map.getSource("activity")) return;
  // buffer:512 + a low source maxzoom stop the heat from being clipped at tile seams when you pan.
  // A cell's glow can be far wider than a normal map tile, and MapLibre renders heatmaps per-tile —
  // so by default a cell only paints into its own tile (+128px) and its glow gets chopped at the
  // seam, and cells that scroll off-screen stop contributing entirely. Tiling the points coarsely
  // (few big tiles) with a full buffer makes each cell's glow bleed across the whole viewport, so
  // panning shows every blob it should and there are no straight-line cutoffs. tolerance:0 keeps
  // point positions exact.
  map.addSource("activity",{type:"geojson",buffer:512,maxzoom:9,tolerance:0,data:{type:"FeatureCollection",
    features:CELLS.map(c=>({type:"Feature",geometry:{type:"Point",coordinates:[c.lon,c.lat]},properties:{r:c.rider_count||0}}))}});
  // Heat radius grows EXPONENTIALLY with zoom — true web-mercator scaling (k=1):
  // pixels-per-metre double every zoom level, so the glow doubles too. A couple of cells
  // that look like dots when zoomed out blend into a broad warm area as you zoom in
  // (their on-screen gap and their glow grow at the same rate). Anchored to the admin
  // "Heat radius" at Zref=9 (≈ city/island zoom — the showcase): at that zoom the glow ≈ the
  // admin radius, then it scales geographically both ways. A small floor when zoomed out (RMIN)
  // keeps far views a tight dot, not a giant blob. RMAX is the street-zoom ceiling and is tied to
  // the VIEWPORT (≈0.8·width, hard ceiling 1400): big enough on a desktop to keep the coarse cell
  // grid flattened into one smooth blob as you zoom in (instead of the cell peaks poking through as
  // separate dots), but never wider than the screen — a radius larger than the viewport just paints
  // everything one flat colour (a wash). On a narrow/phone screen the cap is naturally smaller, so
  // it self-limits instead of washing. ["exponential",2] makes the interpolation follow 2^zoom.
  const Rb=HEAT.radius,Zref=9,k=HEAT.zoom_growth,RMIN=Math.max(10,Rb*0.15),RMAX=Math.min(Rb*24,Math.round(innerWidth*0.8),1400);
  const rAt=z=>Math.max(RMIN,Math.min(RMAX,Rb*Math.pow(2,(z-Zref)*k)));
  // z13 is an explicit stop so the (capped) curve hits its full value there instead of the sparse
  // [12,14] interpolation undershooting the street-zoom flattening radius.
  const rRadius=["interpolate",["exponential",2],["zoom"]].concat(
    [3,7,10,12,13,14,16,20].reduce((a,z)=>a.concat([z,Math.round(rAt(z))]),[]));
  map.addLayer({id:"heat",type:"heatmap",source:"activity",paint:{
    // BRIGHTNESS = a steady, always-visible floor that scales up with distinct riders.
    // A lone rider (r=1) reads as a clear cyan glow (~0.45); each doubling of riders steps
    // it toward white (~8+ riders). Quiet cells stay visibly fainter than busy ones but
    // never disappear. log2 so each doubling of riders is one even step.
    "heatmap-weight":["min",1,["+",HEAT.glow_floor,["*",0.20,["/",["ln",["max",1,["get","r"]]],["ln",2]]]]],
    // INTENSITY a touch stronger when zoomed OUT (sparse far cells read better) and settles
    // to the dialed value through the mid zooms. Above z12 it ramps up gently: at street zoom
    // the radius is held to a small cap (see RMAX) so the heat stays a localized glow instead of
    // a screen-wide wash — but a capped radius still spreads each cell's weight, so this small
    // intensity bump keeps the glow visible and merged rather than fading out. Stops at/below z12
    // are unchanged, so the dialed-in z8–12 look is preserved exactly.
    "heatmap-intensity":["interpolate",["linear"],["zoom"],3,1.45*HEAT.intensity,11,1.3*HEAT.intensity,
      12,1.3*HEAT.intensity,13,1.8*HEAT.intensity,15,2.0*HEAT.intensity,18,2.2*HEAT.intensity],
    "heatmap-radius":rRadius,
    "heatmap-opacity":0,
    "heatmap-color":["interpolate",["linear"],["heatmap-density"],
      0,"rgba(0,0,0,0)",0.1,"rgba(22,35,94,0.6)",0.3,"#2b6fd6",0.5,"#1fb6c9",0.7,"#3fe6a8",0.86,"#c8f7e6",1,"#ffffff"]}});
  map.setPaintProperty("heat","heatmap-opacity-transition",{duration:1500});
  requestAnimationFrame(()=>map.setPaintProperty("heat","heatmap-opacity",HEAT.opacity));   // softer, more organic
}
function setupCfg(){
  const gear=document.getElementById("gear"),cfg=document.getElementById("cfg");
  function render(){
    const _C=window.__CFG__||{};
    const adminOff=_C.intro_enabled===false;              // intro turned off site-wide by admin
    const on=!adminOff && localStorage.getItem("eucstats_intro_off")!=="1";   // this visitor's choice
    const maps=[["dark",t("map.dark")],["light",t("map.light")],["voyager",t("map.voyager")],["satellite",t("map.satellite")],["terrain",t("map.topo")]];
    const langs=Object.keys(LANGS).map(l=>`<option value="${l}" ${LANG===l?'selected':''}>${LANGS[l]}</option>`).join("");
    cfg.innerHTML=`<div class="crow"><span>${t("cfg.language")}</span><select id="langsel" class="csel">${langs}</select></div>`+
      `<div class="crow"><span>${t("cfg.units")}</span><div class="seg"><button data-u="kmh" class="${UNIT==='kmh'?'on':''}">${t("u.metric")}</button><button data-u="mph" class="${UNIT==='mph'?'on':''}">${t("u.imperial")}</button></div></div>`+
      `<div class="crow"><span>${t("cfg.map")}</span><select id="mapsel" class="csel">${maps.map(([v,l])=>`<option value="${v}" ${MAPSTYLE===v?'selected':''}>${l}</option>`).join("")}</select></div>`+
      `<div class="crow"><span>${t("cfg.intro")}</span><span class="introctl">`+
        `<label class="cck${adminOff?' dis':''}" title="${adminOff?t('cfg.intro_off'):t('cfg.intro_play')}"><input type="checkbox" id="introchk"${on?' checked':''}${adminOff?' disabled':''}> ${t("cfg.enabled")}</label>`+
        `<button id="introbtn" class="cbtn"${on?'':' disabled'}>${t("cfg.replay")}</button>`+
      `</span></div>`;
    const lsel=cfg.querySelector("#langsel");
    if(lsel)lsel.onchange=async()=>{LANG=lsel.value;try{localStorage.setItem("eucstats_lang",LANG);}catch(e){}await ensureLang(LANG);applyI18n();renderHeader();animateChips();const p=openPanel;if(p){openPanel=null;HANDLERS[p]();}render();};
    cfg.querySelectorAll("[data-u]").forEach(b=>b.onclick=()=>{UNIT=b.dataset.u;localStorage.setItem("eucstats_unit",UNIT);render();renderHeader();animateChips();const p=openPanel;if(p){openPanel=null;HANDLERS[p]();}});
    const ms=cfg.querySelector("#mapsel");if(ms)ms.onchange=()=>{MAPSTYLE=ms.value;localStorage.setItem("eucstats_style",MAPSTYLE);map.setStyle(STYLES[MAPSTYLE]);};
    const chk=cfg.querySelector("#introchk"),ib=cfg.querySelector("#introbtn");
    if(chk) chk.onchange=()=>{ if(chk.checked){localStorage.removeItem("eucstats_intro_off");}else{localStorage.setItem("eucstats_intro_off","1");} if(ib)ib.disabled=!chk.checked; };
    if(ib) ib.onclick=()=>{ if(ib.disabled)return; try{localStorage.removeItem("eucstats_intro_seen");}catch(e){} ib.disabled=true; location.reload(); };
  }
  render(); gear.onclick=()=>cfg.classList.toggle("open");
}

(function(){function sv(){var h=(window.visualViewport&&window.visualViewport.height)||window.innerHeight;document.documentElement.style.setProperty("--appvh",h+"px");}sv();addEventListener("resize",sv);addEventListener("orientationchange",sv);if(window.visualViewport)window.visualViewport.addEventListener("resize",sv);})();
async function init(){
  // Wire the autoplaying intro <video> FIRST, before any network await. Otherwise the
  // repeat-visit "seek to the end + reveal" attaches only after the i18n/stats fetches,
  // by which time the video has played its full length (hidden), then the map loads.
  let mapReady=false,videoDone=false,introRan=false;
  const tzFull=Intl.DateTimeFormat().resolvedOptions().timeZone||"";
  const [rlon,rlat,rz]=TZMAP[tzFull]||REGION[tzFull.split("/")[0]]||[10,30,3];
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
    map.flyTo({center:[tlon,tlat],zoom:Math.max(1.5,tz-0.5),duration:5000,curve:1.5,easing:easeInOutCubic,essential:true});  // -0.5: settle slightly further out
    runIntro();
  }
  const vid=document.getElementById("intro"),fx=document.getElementById("introfx");
  const _C=window.__CFG__||{};
  const _introOff=localStorage.getItem("eucstats_intro_off")==="1";   // visitor opted out via the gear menu
  if(_C.intro_enabled===false||_introOff){ if(vid)vid.remove(); if(fx)fx.remove(); videoDone=true; }
  else {
  if(vid&&_C.intro_src){const _so=vid.querySelector("source"); if(_so&&_so.getAttribute("src")!==_C.intro_src){_so.setAttribute("src",_C.intro_src); vid.load();}}
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
  }
  // now localize the chrome, fetch data, and build the map
  await ensureLang(LANG); applyI18n();
  S=await j("/stats/summary"); WC=await j("/champions"); renderHeader();
  const _ps=(window.__CFG__&&typeof window.__CFG__.poll_secs==="number")?window.__CFG__.poll_secs:30; if(_ps>0)setInterval(pollStats,_ps*1000);
  // maxZoom stops where the coarse ~1-3km privacy-grid heat still reads well; past this the bins
  // can't be flattened without washing the screen, and street-level rider presence isn't shown.
  map=new maplibregl.Map({container:"map",style:STYLES[MAPSTYLE],center:[rlon,rlat],zoom:1.5,maxZoom:13.23,attributionControl:true});
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),"top-right");
  addEventListener("resize",()=>map.resize()); addEventListener("orientationchange",()=>setTimeout(()=>map.resize(),120)); if(window.visualViewport)window.visualViewport.addEventListener("resize",()=>map.resize()); setTimeout(()=>map.resize(),80);
  const veil=document.getElementById("veil");
  const setVeil=()=>{const z=map.getZoom();veil.style.opacity=Math.max(0,Math.min(1,(6-z)/4.5));};
  map.on("zoom",setVeil); setVeil();
  map.on("style.load",addHeat);   // re-add heat after a style switch
  setupCfg();
  map.on("load",async ()=>{
    CELLS=await j("/map/cells?zoom="+HEAT.zoom); addHeat();
    mapReady=true; doIntro();
  });
}
init().catch(()=>{const c=document.getElementById("chips");c.classList.add("show");c.innerHTML='<span class="chip">'+t("empty.apierror")+'</span>';});
</script></body></html>"""


def _build_date():
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(__file__), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _clarity_tag():
    """Microsoft Clarity loader, only when a (safe, alphanumeric) project id is set."""
    cid = config.CLARITY_ID
    if not cid or not cid.isalnum():
        return ""
    return ('<script>(function(c,l,a,r,i,t,y){c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};'
            't=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;'
            'y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);})'
            '(window,document,"clarity","script","' + cid + '");</script>')


def _hide_cfg(db, admin=False, accept_language=""):
    import json
    from web import i18n
    h = settings.get_hidden(db)
    hide = {"boards": h["boards"], "app": h["app"], "records": h["records"],
            "groups": h["groups"], "sec": settings.sections_fully_hidden(db)}
    hm = settings.get_heatmap(db)
    heat = {"zoom": hm["cell_size"], "radius": hm["radius"], "zoom_growth": hm["zoom_growth"],
            "intensity": hm["intensity"], "glow_floor": hm["glow_floor"], "opacity": hm["opacity"]}
    gated = [{"k": b["k"], "base": b["base"], "u": b["u"], "conv": b["conv"], "ic": b["ic"],
              "min_s": b["min_s"], "min_km": b["min_km"]}
             for b in settings.gated_boards() + settings.ungated_new_boards()]
    # inject only English + the visitor's language (others lazy-load via /api/v1/i18n/{loc})
    loc = i18n.pick(accept_language)
    i18n_small = {"en": i18n.EN}
    if loc != "en":
        i18n_small[loc] = i18n.locale_table(loc)
    return ('<script>window.__HIDE__=' + json.dumps(hide)
            + ';window.__GATED__=' + json.dumps(gated)
            + ';window.__ADMIN__=' + ('true' if admin else 'false')
            + ';window.__CFG__=' + json.dumps(settings.get_behaviour(db))
            + ';window.__HEAT__=' + json.dumps(heat)
            + ';window.__I18N__=' + json.dumps(i18n_small, ensure_ascii=False)
            + ';window.__LANGS__=' + json.dumps(i18n.LANG_NAMES, ensure_ascii=False)
            + ';</script>')


@public_router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    import html as _html
    admin = bool(request.session.get("admin_auth"))   # logged-in admin previews hidden metrics dimmed
    accept = request.headers.get("accept-language", "")
    tm = settings.get_test_mode()
    testwm = f'<div id="testwm">{_html.escape(tm["text"])}</div>' if tm["enabled"] else ''
    return HTMLResponse(_PAGE.replace("__BUILD__", _build_date())
                        .replace("__CLARITY__", _clarity_tag())
                        .replace("__HIDECFG__", _hide_cfg(db, admin, accept))
                        .replace("__TESTWM__", testwm))
