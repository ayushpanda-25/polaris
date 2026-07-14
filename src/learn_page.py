"""
The /learn page — the Polaris Academy.

A segmented, in-depth guide to reading the Polaris dealer-GEX terminal:
a quick-start, a component-by-component anatomy of the map, the four views,
map-reading patterns, the ΔΓ/Δt + freshness deep dives, and the
map→trigger→filter→manage flow.

Terminology is Polaris's own — taught by SIGN (positive node / negative
node), never by palette color, because palettes are user-switchable.

Served as a self-contained static HTML route (no external CSS/JS) off the
underlying Flask server — both the local Dash app and the Vercel entry.
"""
from __future__ import annotations


LEARN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#04060d">
<title>POLARIS · Academy</title>
<link rel="icon" type="image/svg+xml" href="/assets/star.svg">
<style>
  :root {
    --bg: #04060d;
    --ink: rgba(255,255,255,0.92);
    --ink-2: rgba(255,255,255,0.62);
    --ink-3: rgba(255,255,255,0.34);
    --glass: rgba(255,255,255,0.05);
    --glass-2: rgba(255,255,255,0.03);
    --edge: rgba(255,255,255,0.10);
    --edge-bright: rgba(255,255,255,0.18);
    --polar: #6ee7ff;
    --pos: #45e3ff;
    --neg: #ff3d9e;
    --warn: #ffb340;
    --good: #3ddc84;
    --r-lg: 22px; --r-md: 16px;
    --font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", sans-serif;
    --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0; padding: 0; background: var(--bg); color: var(--ink-2);
    font-family: var(--font); font-size: 14.5px; line-height: 1.65;
    -webkit-font-smoothing: antialiased; overflow-x: hidden;
  }
  .aurora { position: fixed; inset: 0; z-index: -2; overflow: hidden; pointer-events: none; }
  .orb { position: absolute; border-radius: 50%; filter: blur(95px); opacity: 0.42; }
  .orb-polar { width: 54vw; height: 54vw; left: -14vw; top: -22vw;
    background: radial-gradient(circle at 38% 38%, #38b6e04d, #0e4a6a33 48%, transparent 70%); }
  .orb-violet { width: 46vw; height: 46vw; right: -12vw; top: 34vh;
    background: radial-gradient(circle at 60% 40%, #8b7cff34, transparent 70%); }
  .stars { position: fixed; inset: -60px; z-index: -1; pointer-events: none;
    background-image:
      radial-gradient(1px 1px at 22px 34px, rgba(255,255,255,0.55), transparent),
      radial-gradient(1px 1px at 168px 92px, rgba(255,255,255,0.32), transparent),
      radial-gradient(1.4px 1.4px at 308px 142px, rgba(160,230,255,0.45), transparent),
      radial-gradient(1px 1px at 424px 58px, rgba(255,255,255,0.28), transparent),
      radial-gradient(1.3px 1.3px at 92px 212px, rgba(255,255,255,0.36), transparent);
    background-size: 470px 310px; background-repeat: repeat; opacity: 0.4; }

  .topbar {
    position: sticky; top: 14px; z-index: 60;
    width: min(1000px, calc(100vw - 26px)); margin: 14px auto 0;
    display: flex; align-items: center; gap: 10px; padding: 10px 20px;
    border-radius: 999px; background: var(--glass);
    -webkit-backdrop-filter: blur(24px) saturate(180%); backdrop-filter: blur(24px) saturate(180%);
    border: 1px solid var(--edge);
    box-shadow: inset 0 1px 0 var(--edge-bright), 0 18px 44px rgba(0,0,0,0.4);
  }
  @supports not ((backdrop-filter: blur(2px))) {
    .topbar, .tabs, .box, .card, .anat, table { background: rgba(18,22,34,0.94); }
  }
  .topbar img { width: 20px; height: 20px; filter: drop-shadow(0 0 7px rgba(110,231,255,0.55)); }
  .topbar .brand { font-size: 13px; font-weight: 700; letter-spacing: 0.24em;
    background: linear-gradient(92deg, #fff 25%, var(--polar) 80%);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
  .topbar .sub { color: var(--ink-3); font-size: 10px; letter-spacing: 0.2em; text-transform: uppercase; }
  .topbar a.back { margin-left: auto; color: var(--ink-2); font-size: 12.5px; font-weight: 600;
    text-decoration: none; padding: 6px 14px; border-radius: 999px; border: 1px solid transparent; transition: all .25s; }
  .topbar a.back:hover { color: var(--ink); border-color: var(--edge); background: rgba(255,255,255,0.05); }

  /* segmented tab nav */
  .tabs {
    position: sticky; top: 74px; z-index: 55;
    width: min(1000px, calc(100vw - 26px)); margin: 12px auto 0;
    display: flex; gap: 4px; padding: 5px; overflow-x: auto; scrollbar-width: none;
    border-radius: 999px; background: var(--glass);
    -webkit-backdrop-filter: blur(24px) saturate(180%); backdrop-filter: blur(24px) saturate(180%);
    border: 1px solid var(--edge); box-shadow: inset 0 1px 0 var(--edge-bright), 0 12px 30px rgba(0,0,0,0.3);
  }
  .tabs::-webkit-scrollbar { display: none; }
  .tabs button {
    flex: none; cursor: pointer; font-family: var(--font); font-size: 12.5px; font-weight: 600;
    color: var(--ink-2); white-space: nowrap; padding: 8px 15px; border-radius: 999px;
    border: 1px solid transparent; background: transparent; transition: all .25s;
  }
  .tabs button:hover { color: var(--ink); }
  .tabs button.on { color: #0b0c12;
    background: linear-gradient(180deg, #ffffff, #d9dde6);
    box-shadow: 0 2px 10px rgba(0,0,0,0.35), inset 0 1px 0 #fff; }

  .container { max-width: 900px; margin: 0 auto; padding: 30px 24px 100px; }
  section.tab { display: none; animation: fade .4s ease; }
  section.tab.on { display: block; }
  @keyframes fade { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

  h1 { color: var(--ink); font-size: 25px; font-weight: 700; letter-spacing: -0.02em; margin: 8px 0 6px; }
  h1 .eyebrow { display: block; color: var(--polar); font-size: 11px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase; margin-bottom: 8px; }
  h2 { color: var(--ink); font-size: 18px; font-weight: 700; letter-spacing: -0.01em; margin: 36px 0 8px; }
  h3 { color: var(--polar); font-size: 12.5px; letter-spacing: 0.08em; text-transform: uppercase; margin: 22px 0 6px; }
  p { margin: 8px 0 14px; }
  .lead { color: var(--ink-3); font-size: 14.5px; font-style: italic; margin: 4px 0 18px; }
  ul, ol { padding-left: 20px; margin: 8px 0 14px; }
  li { margin-bottom: 8px; }
  .pos { color: var(--pos); font-weight: 700; }
  .neg { color: var(--neg); font-weight: 700; }
  .key { color: var(--ink); font-weight: 650; }
  .dim { color: var(--ink-3); }
  .warn { color: var(--warn); font-weight: 650; }
  code { font-family: var(--mono); background: rgba(255,255,255,0.06); color: var(--polar);
    padding: 1px 7px; border: 1px solid var(--edge); border-radius: 7px; font-size: 12px; }

  .box { background: var(--glass);
    -webkit-backdrop-filter: blur(20px) saturate(160%); backdrop-filter: blur(20px) saturate(160%);
    border: 1px solid var(--edge); border-radius: var(--r-lg);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 14px 36px rgba(0,0,0,0.3);
    padding: 18px 22px; margin: 16px 0; }
  .box .label { color: var(--polar); font-size: 10px; font-weight: 700; letter-spacing: 0.18em;
    text-transform: uppercase; margin-bottom: 8px; }
  .hero { font-size: 16px; line-height: 1.7; color: var(--ink); }
  .callout { border-left: 3px solid var(--warn); background: rgba(255,179,64,0.06);
    border-radius: 0 var(--r-md) var(--r-md) 0; padding: 12px 18px; margin: 16px 0; font-size: 13.5px; }
  .callout.polar { border-left-color: var(--polar); background: rgba(110,231,255,0.06); }
  .callout .h { display: block; color: var(--ink); font-weight: 700; font-size: 12px;
    letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 4px; }

  /* two-node comparison */
  .duo { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 18px 0; }
  .duo .card { padding: 18px 20px; }
  .card { background: var(--glass);
    -webkit-backdrop-filter: blur(20px) saturate(160%); backdrop-filter: blur(20px) saturate(160%);
    border: 1px solid var(--edge); border-radius: var(--r-lg);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 12px 30px rgba(0,0,0,0.28); padding: 16px 20px; }
  .card.pos-card { border-color: rgba(69,227,255,0.28); background: rgba(69,227,255,0.05); }
  .card.neg-card { border-color: rgba(255,61,158,0.28); background: rgba(255,61,158,0.05); }
  .card .tag { font-size: 11px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; }
  .card .big { display: block; color: var(--ink); font-size: 17px; font-weight: 700; margin: 6px 0 2px; }
  .card .sub { color: var(--ink-3); font-size: 12px; font-style: italic; margin-bottom: 8px; }
  .card ul { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }

  /* anatomy list */
  .anat { background: var(--glass);
    -webkit-backdrop-filter: blur(20px) saturate(160%); backdrop-filter: blur(20px) saturate(160%);
    border: 1px solid var(--edge); border-radius: var(--r-lg);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.08), 0 10px 26px rgba(0,0,0,0.24);
    padding: 4px 6px; margin: 14px 0; }
  .anat .row { padding: 15px 16px; border-bottom: 1px solid rgba(255,255,255,0.06); }
  .anat .row:last-child { border-bottom: none; }
  .anat .name { color: var(--ink); font-weight: 700; font-size: 14px; }
  .anat .name .pill { display: inline-block; font-family: var(--mono); font-size: 10.5px; font-weight: 600;
    color: var(--polar); background: rgba(110,231,255,0.08); border: 1px solid rgba(110,231,255,0.22);
    border-radius: 6px; padding: 1px 7px; margin-left: 8px; vertical-align: 1px; }
  .anat .row p { margin: 5px 0 0; font-size: 13px; }

  /* numbered checklist */
  ol.checklist { counter-reset: c; list-style: none; padding: 0; }
  ol.checklist > li { counter-increment: c; position: relative; padding: 14px 18px 14px 56px;
    margin-bottom: 10px; font-size: 13.5px; background: var(--glass);
    -webkit-backdrop-filter: blur(18px); backdrop-filter: blur(18px);
    border: 1px solid var(--edge); border-radius: 16px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.08); }
  ol.checklist > li::before { content: counter(c); position: absolute; left: 15px; top: 13px;
    width: 26px; height: 26px; border-radius: 50%; color: #0b0c12; font-weight: 700; font-size: 13px;
    text-align: center; line-height: 26px; background: linear-gradient(180deg,#fff,#c8ecf9);
    box-shadow: 0 2px 8px rgba(0,0,0,0.35); }
  ol.checklist .name { color: var(--ink); font-weight: 700; }

  table { width: 100%; border-collapse: collapse; margin: 14px 0; font-size: 13px;
    background: var(--glass-2); border: 1px solid var(--edge); border-radius: var(--r-md); overflow: hidden; }
  th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); vertical-align: top; }
  th { color: var(--polar); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 700; }
  tr:last-child td { border-bottom: none; }

  .diagram { font-family: var(--mono); font-size: 12px; color: var(--ink-2); white-space: pre;
    overflow-x: auto; background: rgba(0,0,0,0.28); border: 1px solid var(--edge);
    border-radius: var(--r-md); padding: 16px 18px; margin: 14px 0; line-height: 1.5; }

  .lifecycle { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 14px 0; font-size: 12.5px; }
  .lifecycle .st { padding: 7px 13px; border-radius: 999px; border: 1px solid var(--edge);
    font-family: var(--mono); font-weight: 700; }
  .lifecycle .arrow { color: var(--ink-3); }
  .st.fresh { color: var(--warn); border-color: rgba(255,179,64,0.4); background: rgba(255,179,64,0.08); }
  .st.charge { color: var(--pos); border-color: rgba(69,227,255,0.35); background: rgba(69,227,255,0.06); }
  .st.held { color: var(--ink); }
  .st.decay { color: var(--ink-3); background: rgba(255,255,255,0.03); }

  .footer { margin-top: 60px; padding-top: 22px; border-top: 1px solid var(--edge);
    color: var(--ink-3); font-size: 11.5px; line-height: 1.7; }
  .footer .credit { color: var(--ink-2); }
  @media (max-width: 720px) {
    .duo { grid-template-columns: 1fr; }
    .tabs { top: 70px; }
    h1 { font-size: 22px; }
  }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } section.tab.on { animation: none; } }
</style>
</head>
<body>

<div class="aurora"><div class="orb orb-polar"></div><div class="orb orb-violet"></div></div>
<div class="stars"></div>

<div class="topbar">
  <img src="/assets/star.svg" alt="Polaris">
  <span class="brand">POLARIS</span>
  <span class="sub">Academy</span>
  <a class="back" href="/">← Back to terminal</a>
</div>

<nav class="tabs" id="tabs">
  <button data-t="start" class="on">Start Here</button>
  <button data-t="anatomy">Anatomy</button>
  <button data-t="views">The Views</button>
  <button data-t="reading">Reading the Map</button>
  <button data-t="deep">ΔΓ/Δt &amp; Freshness</button>
  <button data-t="decision">Map → Decision</button>
</nav>

<div class="container">

<!-- ============================ START HERE ============================ -->
<section class="tab on" id="start">
  <h1><span class="eyebrow">Start here · the 2-minute version</span>Read where dealers are forced to trade.</h1>

  <div class="box">
    <div class="label">The whole idea, one paragraph</div>
    <p class="hero" style="margin:0">
      Options dealers have to hedge what they sell, and that hedging <span class="key">forces</span>
      them to buy and sell at predictable price levels. Polaris maps where those forced levels sit.
      So instead of guessing where price goes, you read where dealers <span class="key">need</span>
      it to go. Everything else on this site is detail on top of that sentence.
    </p>
  </div>

  <h2>The two node types — this is 80% of everything</h2>
  <p>A <span class="key">node</span> is one cell on the map: a strike at an expiry holding concentrated
     dealer money. Brightness ≈ size ≈ pull. Every node is one of two kinds, and you read them by
     <span class="key">sign</span>, not by color.</p>

  <div class="duo">
    <div class="card pos-card">
      <span class="tag pos">Positive node</span>
      <span class="big">Cushion</span>
      <span class="sub">dealers are LONG gamma here</span>
      <ul>
        <li>They buy dips, sell rips — hedging <span class="key">against</span> the move.</li>
        <li>Price <span class="pos">slows down</span> as it arrives; the level absorbs it.</li>
        <li>Walls, floors, pins. This is what you fade extremes against.</li>
      </ul>
    </div>
    <div class="card neg-card">
      <span class="tag neg">Negative node</span>
      <span class="big">Fuel</span>
      <span class="sub">dealers are SHORT gamma here</span>
      <ul>
        <li>They buy rises, sell falls — hedging <span class="key">with</span> the move.</li>
        <li>Price <span class="neg">speeds up</span> through it: deflects on first touch, then slides fast once it truly breaks.</li>
        <li>Magnets, fuel, slingshots. This is a get-out-of-the-way warning, not a target.</li>
      </ul>
    </div>
  </div>

  <div class="callout polar">
    <span class="h">Teach yourself by sign, not color</span>
    Polaris palettes are yours to switch, so "the pink cells" or "the cyan cells" stops being true the
    moment you change themes. Always think <span class="pos">positive node</span> /
    <span class="neg">negative node</span>. One hue family means positive, the other negative, and
    brightness is size — whatever palette you run.
  </div>

  <h2>Five things to find on any map</h2>
  <ol class="checklist">
    <li><span class="name">The Star Node</span> — the biggest absolute exposure anywhere on the map.
        Polaris shows it in the header with its value. Price gravitates to it and pins around it; when
        price overshoots it, the odds of reverting back are high. <span class="key">It's the single most
        important number on screen.</span></li>
    <li><span class="name">Stacks</span> — two or three <span class="pos">positive nodes</span> at
        neighboring strikes = a wall that very rarely breaks on the first try.</li>
    <li><span class="name">Rug / reverse-rug</span> — a <span class="pos">positive node</span> with a
        <span class="neg">negative node</span> right <em>below</em> it = reject the ceiling, slice down
        fast. A <span class="neg">negative node</span> right <em>above</em> a <span class="pos">positive</span>
        one = fuel for an upside rip. <span class="key">Never fade that structure.</span></li>
    <li><span class="name">Air pockets</span> — strike zones with no exposure. Price slides through them
        fast: great <em>targets</em>, terrible <em>support</em>.</li>
    <li><span class="name">Sleepers</span> — far-from-price nodes growing violently. They front-run the
        big travel days (see the <a href="#" data-goto="deep" class="pos" style="text-decoration:none">ΔΓ/Δt</a> deep dive).</li>
  </ol>

  <div class="callout">
    <span class="h">The one caveat that goes first, not last</span>
    Polaris data is CBOE, <span class="warn">~15-min delayed</span>. The map draws the picture; it must
    <span class="key">never</span> pull the trigger. Your entry comes from live price on your own chart,
    <em>at</em> a level the map drew. The map is probabilities, not prophecy.
  </div>

  <p class="dim" style="margin-top:22px">Ready for more? <span class="key">Anatomy</span> breaks down every
     part of the terminal; <span class="key">Reading the Map</span> turns these five things into setups.</p>
</section>

<!-- ============================ ANATOMY ============================ -->
<section class="tab" id="anatomy">
  <h1><span class="eyebrow">Anatomy of the terminal</span>Every part of the map, decoded.</h1>
  <p class="lead">Open the terminal in another tab and match each piece as you read.</p>

  <h3>The board</h3>
  <div class="anat">
    <div class="row"><div class="name">The grid</div>
      <p>Strikes stack <span class="key">vertically</span> (price levels); expiries run
      <span class="key">left → right</span>, nearest first. You're looking at dealer positioning across
      price and time at once.</p></div>
    <div class="row"><div class="name">A cell = a node</div>
      <p>Each tile is one strike × one expiry. Its <span class="key">brightness is size</span> (how much
      dealer gamma sits there) and its <span class="key">side/sign is direction</span> (positive = cushion,
      negative = fuel). A bare, dark tile means no meaningful exposure — an air pocket.</p></div>
    <div class="row"><div class="name">The dollar label</div>
      <p>Each populated cell prints its exposure in dollars (e.g. <code>$1.2M</code>). The number is the
      real magnitude; the color is compressed so small-but-real cells still show.</p></div>
    <div class="row"><div class="name">The spot line <span class="pill">dotted white</span></div>
      <p>Where price is <span class="key">right now</span>. Read the walls above it (resistance / targets up)
      versus below it (support / targets down).</p></div>
  </div>

  <h3>The Star Node</h3>
  <div class="anat">
    <div class="row"><div class="name">On the map <span class="pill">bracketed cell</span></div>
      <p>The reticle brackets the single dominant node — the largest absolute exposure on the board. Price
      gravitates here and pins; overshoots tend to revert.</p></div>
    <div class="row"><div class="name">In the header <span class="pill">Star Node · Star Value</span></div>
      <p>The stat chips show its strike and signed size. <span class="key">Positive value</span> = pin/cushion
      regime (fade extremes). <span class="key">Negative value</span> = trend/fuel regime (get out of the way).
      Distance from spot matters: far = a <em>target</em> price drifts toward; on top of spot = a <em>pin</em>.</p></div>
    <div class="row"><div class="name">"No clear leader"</div>
      <p>When the top node isn't meaningfully bigger than its runners-up, Polaris dims the Star Node and
      writes <span class="dim">"no clear leader."</span> The day (or the hour) is too quiet to trust a
      dominant magnet — <span class="key">don't trade off it.</span></p></div>
  </div>

  <h3>State &amp; freshness chips</h3>
  <div class="anat">
    <div class="row"><div class="name">State pill <span class="pill">Fresh · Charging · Held · Decaying</span></div>
      <p>The Star Node's lifecycle, derived from its ΔΓ/Δt and how long it has held.
      <span class="warn">Fresh</span> = just moved here, unproven. <span class="pos">Charging</span> = gamma
      still building, magnet strengthening. <span class="key">Held</span> = settled, proven. Decaying =
      gamma rolling off, pull fading. (More in the deep dive.)</p></div>
    <div class="row"><div class="name">Reshuffled <span class="pill">⚠ Ns ago</span></div>
      <p>The Star Node strike just changed — a macro print, a round-number break, a 0DTE roll, or a whale.
      <span class="key">Your old thesis is invalid</span> until it settles (2–5 min). Re-read fresh.</p></div>
    <div class="row"><div class="name">Freshness dot <span class="pill">Live · Lagging · Stale · Offline</span></div>
      <p>Top-right. <span class="pos">Live</span> = trust the map. Stale / Offline = stop reading it.</p></div>
    <div class="row"><div class="name">CBOE backup pill <span class="pill">15m delayed</span></div>
      <p>Shows when Polaris is serving delayed CBOE chains (its always-on backbone). The map is still true
      shape; it's just not real-time — one more reason entries come from your live chart.</p></div>
  </div>

  <h3>Secondary structure &amp; controls</h3>
  <div class="anat">
    <div class="row"><div class="name">Gatekeepers <span class="pill">node row</span></div>
      <p>The next-strongest nodes sitting between spot and the Star Node — the deflection levels price has to
      clear to reach the magnet. A failed test of a gatekeeper often signals a regime flip.</p></div>
    <div class="row"><div class="name">View selector <span class="pill">GEX · GEX·√T · VEX · ΔΓ/Δt</span></div>
      <p>Four ways to slice the same positioning. See <a href="#" data-goto="views" class="pos" style="text-decoration:none">The Views</a>.</p></div>
    <div class="row"><div class="name">Palette picker <span class="pill">cosmetic</span></div>
      <p>Purely your look — it remembers your choice. It never changes the data. Read by sign.</p></div>
    <div class="row"><div class="name">ORION <span class="pill">multi-index</span></div>
      <p>Several index maps side by side, same moment. A regime call is only high-conviction when the panels
      <span class="key">agree</span>. Two of three agreeing = tradeable; all disagreeing = chop, stand down.</p></div>
  </div>
</section>

<!-- ============================ THE VIEWS ============================ -->
<section class="tab" id="views">
  <h1><span class="eyebrow">The four views</span>Same data, four lenses.</h1>
  <p class="lead">Each view re-reads the same dealer positioning. Use the right one for the job.</p>

  <div class="card" style="margin:14px 0"><div class="name" style="color:var(--ink);font-weight:700;font-size:15px">GEX <span class="dim" style="font-weight:400;font-size:12px">· the base map</span></div>
    <p style="margin:8px 0 0">Where dealer gamma sits, as it is. Each cell is the hedging flow that hits the
    market for a move at that strike. <span class="key">Your default — 90% of reads.</span> Near the close,
    0DTE naturally dominates (gamma scales as 1/√T); that's a feature, telling you where the closing pin forms.</p></div>

  <div class="card" style="margin:14px 0"><div class="name" style="color:var(--ink);font-weight:700;font-size:15px">GEX·√T <span class="dim" style="font-weight:400;font-size:12px">· time-scaled (Polaris-specific)</span></div>
    <p style="margin:8px 0 0">The same GEX × √T, which cancels the 1/√T scaling and weights nearer expiries
    more honestly. Removes 0DTE's visual dominance so longer-dated structure shows. <span class="key">A good
    intraday default</span>, and the one to reach for in the morning to read multi-day positioning.</p></div>

  <div class="card" style="margin:14px 0"><div class="name" style="color:var(--ink);font-weight:700;font-size:15px">VEX <span class="dim" style="font-weight:400;font-size:12px">· vol-sensitivity map</span></div>
    <p style="margin:8px 0 0">Dealer hedging driven by <span class="key">volatility</span> changes, not spot
    moves. Often points the same way as GEX; on whipsaw days it diverges. Best for 5+ day swings — but it
    takes over <em>intraday</em> when volatility spikes. Use it as a confirmation layer: GEX and VEX agreeing
    = high conviction; fighting = expect chop.</p></div>

  <div class="card" style="margin:14px 0"><div class="name" style="color:var(--ink);font-weight:700;font-size:15px">ΔΓ/Δt <span class="dim" style="font-weight:400;font-size:12px">· rate of change — Polaris's edge</span></div>
    <p style="margin:8px 0 0">The rate of change of <span class="key">every node at once</span>. Most tools
    show a node's velocity only when you click it — Polaris shows the whole map moving. It surfaces which
    strikes are <span class="pos">charging up</span> into the close before their GEX is even large. It's also
    the hardest view for a new reader, which is exactly why it gets its own
    <a href="#" data-goto="deep" class="pos" style="text-decoration:none">deep dive</a>.</p></div>
</section>

<!-- ============================ READING THE MAP ============================ -->
<section class="tab" id="reading">
  <h1><span class="eyebrow">Reading the map</span>From cells to setups.</h1>

  <h2>The structures that repeat</h2>
  <table>
    <tr><th>Structure</th><th>What you see</th><th>What it means</th></tr>
    <tr><td class="key">Stack</td><td>2–3 positive nodes at neighboring strikes</td><td>A wall. Rarely breaks first try — fade into it, don't chase through it.</td></tr>
    <tr><td class="key">Rug</td><td>Positive node with a negative node right below</td><td>Reject the ceiling, slice down fast.</td></tr>
    <tr><td class="key">Reverse-rug</td><td>Negative node right above a positive node</td><td>Fuel for an upside rip. <span class="warn">Never fade it.</span></td></tr>
    <tr><td class="key">Air pocket</td><td>A strike zone with no exposure</td><td>Fast slide-through. Great target, terrible support.</td></tr>
    <tr><td class="key">Sleeper</td><td>A far node growing violently (ΔΓ/Δt)</td><td>Front-runs a big travel day. Watch it.</td></tr>
  </table>

  <h2>Nodes are zones, not lines</h2>
  <p>Don't demand an exact touch. A node is a <span class="key">zone</span> — roughly ±5 SPX points, or
     about ±$1.50 on an SPY scale. Expect <em>overshoot</em> where a negative node sits right next to it.
     Price arriving "at the node" means arriving in the zone.</p>

  <div class="callout">
    <span class="h">The cardinal sin</span>
    <span class="key">Never chase INTO a node.</span> The node is the wall, not the trade. You trade the
    reaction at the level, not the run toward it.
  </div>

  <h2>Between two competing nodes</h2>
  <p>When price sits between two comparable nodes, it gravitates to the <span class="key">size-weighted
     midpoint</span>. The middle of that range is chop — trade the <span class="key">edges</span>, not the
     mush in between.</p>

  <h2>Read the indices together</h2>
  <p>Flip to ORION. If SPY, SPX, and QQQ agree on the regime, conviction is real. If they disagree, it's a
     mixed call — <span class="key">two of three agreeing is tradeable; all three disagreeing is a stand-down
     day.</span></p>

  <h2>Counting tests of a level</h2>
  <div class="diagram">1st touch   strongest   — the level does its job
2nd touch   still good
3rd touch   hands off    — the level is tiring
4th touch   probably breaking

Once broken, levels FLIP roles:  a floor becomes a ceiling.</div>
</section>

<!-- ============================ DEEP DIVE ============================ -->
<section class="tab" id="deep">
  <h1><span class="eyebrow">Deep dive</span>ΔΓ/Δt and freshness.</h1>

  <h2>ΔΓ/Δt — the map moves before price</h2>
  <div class="box"><div class="label">The core insight</div>
    <p style="margin:0" class="hero">A node's <span class="key">rate of change beats its size.</span> A level
    that is growing is strengthening; a level that is dimming is dying — and it tells you <em>before</em>
    price confirms it.</p></div>

  <table>
    <tr><th>What you see</th><th>What it means</th></tr>
    <tr><td class="pos key">Growing</td><td>Dealers committing → the level is strengthening.</td></tr>
    <tr><td class="dim key">Dimming</td><td>Dealers leaving → the level is dying.</td></tr>
    <tr><td class="neg key">Divergence</td><td><span class="key">The killer pattern.</span> Price pushes UP while a downside node keeps <span class="key">growing</span> → the pump is suspect. A node that won't shrink during a rally is telling you the truth.</td></tr>
    <tr><td class="warn key">Clearing the floors</td><td>The support node under price quietly shrinks while a downside node grows → breakdown risk is building.</td></tr>
  </table>

  <div class="callout polar">
    <span class="h">Why this view is Polaris's edge</span>
    Most desks can only inspect one node's velocity at a time. ΔΓ/Δt shows the whole board's rate of
    change at once, so <span class="key">sleepers</span> and <span class="key">divergences</span> jump out
    before they'd ever show up in raw size.
  </div>

  <h2>Freshness — has this level already fired?</h2>
  <p>Ask one question: <span class="key">where was price delivered from?</span> If price got here by rejecting
     off the node above, that node <em>already fired</em>. Spent nodes are weak targets; fresh, untouched
     nodes carry the full odds.</p>

  <div class="lifecycle">
    <span class="st fresh">FRESH</span><span class="arrow">→</span>
    <span class="st charge">TESTED</span><span class="arrow">→</span>
    <span class="st held">DELIVERED</span><span class="arrow">→</span>
    <span class="st decay">DECAYING</span>
  </div>
  <p>A node is strongest when <span class="key">fresh</span> (untouched), still useful when
     <span class="key">tested</span>, weak once it has <span class="key">delivered</span> (price already
     reacted off it), and fading as it <span class="key">decays</span>. Polaris's <span class="key">State
     pill</span> surfaces the ΔΓ/Δt-derived side of this (Fresh / Charging / Held / Decaying); the
     "already fired?" check is still yours to make from how price arrived.</p>

  <div class="callout">
    <span class="h">Time-of-day trust</span>
    Afternoon nodes are more reliable than morning ones — positioning firms up as the day goes on (charm).
    First 30 minutes: positioning still building, low trust. Last 30: peak strength.
  </div>
</section>

<!-- ============================ DECISION ============================ -->
<section class="tab" id="decision">
  <h1><span class="eyebrow">Map → decision</span>How a read becomes a trade.</h1>
  <p class="lead">The map is step one of four. It never trades on its own.</p>

  <div class="diagram">MAP              TRIGGER             FILTER            MANAGE
where are    →   price action    →   is reward ≥   →   scale out into
the levels       AT a level only     3× the risk?      the node stack</div>

  <ol class="checklist">
    <li><span class="name">Map</span> — find the levels on Polaris: Star Node, stacks, rugs, air pockets,
        sleepers. This is context, not a signal.</li>
    <li><span class="name">Trigger</span> — wait for <span class="key">live price action AT a level</span> on
        your own chart. No level, no trade. The map is delayed; the trigger must be live.</li>
    <li><span class="name">Filter</span> — take it only if the reward to the next node is at least
        <span class="key">3×</span> the risk to your invalidation. Most reads fail this filter — that's the point.</li>
    <li><span class="name">Manage</span> — scale out <span class="key">into</span> the next node stack, where
        dealer hedging gives you liquidity. Don't wait for it to be perfect.</li>
  </ol>

  <h2>Days the map says: don't trade</h2>
  <p>The highest-value skill here is recognizing a <span class="key">no-trade map</span>. When there are many
     rival nodes in every direction, price chops between them and theta burns your premium. Standing down is
     a decision. Signs:</p>
  <ul>
    <li>Star Node dimmed to <span class="dim">"no clear leader."</span></li>
    <li>ORION panels disagree — no index consensus.</li>
    <li>Comparable nodes stacked above <em>and</em> below spot — boxed in.</li>
    <li>Everything flickering / reshuffling — positioning hasn't settled.</li>
  </ul>

  <h2>The caveats, stated plainly</h2>
  <div class="callout"><span class="h">Delayed data</span>
    CBOE, ~15-min delayed. The map draws the picture; your live chart pulls the trigger.</div>
  <div class="callout"><span class="h">Probabilities, not prophecy</span>
    Nodes deflect <em>most</em> of the time. Stacked structure still fails on real news.</div>
  <div class="callout"><span class="h">Event days distort the scale</span>
    One ultra-bright node on an FOMC / NFP / CPI expiry drowns everything else. On those days, read the near
    columns by the <span class="key">numbers</span>, not the brightness.</div>
</section>

<div class="footer">
  <p><span class="warn">Not financial advice.</span> This teaches how to read the tool. Nothing here is a
  recommendation to buy or sell anything. Polaris · the fixed pin in the sky.</p>
</div>

</div>

<script>
(function () {
  var tabs = document.getElementById('tabs');
  var btns = [].slice.call(tabs.querySelectorAll('button'));
  var secs = [].slice.call(document.querySelectorAll('section.tab'));
  function show(name, push) {
    btns.forEach(function (b) { b.classList.toggle('on', b.dataset.t === name); });
    secs.forEach(function (s) { s.classList.toggle('on', s.id === name); });
    if (push && history.replaceState) history.replaceState(null, '', '#' + name);
    window.scrollTo({ top: 0, behavior: 'instant' in window ? 'auto' : 'auto' });
  }
  btns.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.t, true); }); });
  // in-page "go to tab" links
  document.querySelectorAll('[data-goto]').forEach(function (a) {
    a.addEventListener('click', function (e) { e.preventDefault(); show(a.dataset.goto, true); });
  });
  var start = (location.hash || '').replace('#', '');
  if (start && document.getElementById(start)) show(start, false);
})();
</script>

</body>
</html>
"""


def register_learn_route(server) -> None:
    """
    Mount the /learn route on a Flask server (the underlying app.server of
    either the local Dash app or the Vercel serverless entry).
    """
    @server.route("/learn")
    def _learn_view():
        from flask import Response
        return Response(LEARN_HTML, mimetype="text/html; charset=utf-8")
