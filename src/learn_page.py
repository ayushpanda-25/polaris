"""
The /learn page — a single-page mental model for reading the Polaris
dashboard. Served as a static HTML route off the underlying Flask server
(both the local Dash app and the Vercel serverless entry).

Liquid-glass theming to match the terminal: night-sky navy, glass
panels, polar-cyan accent. Self-contained — no external CSS or JS.
"""
from __future__ import annotations


LEARN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#04060d">
<title>POLARIS · Learn</title>
<link rel="icon" type="image/svg+xml" href="/assets/star.svg">
<style>
  :root {
    --bg: #04060d;
    --ink: rgba(255,255,255,0.92);
    --ink-2: rgba(255,255,255,0.62);
    --ink-3: rgba(255,255,255,0.34);
    --glass: rgba(255,255,255,0.05);
    --edge: rgba(255,255,255,0.10);
    --edge-bright: rgba(255,255,255,0.18);
    --polar: #6ee7ff;
    --pos: #45e3ff;
    --neg: #ff3d9e;
    --warn: #ffb340;
    --font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", sans-serif;
    --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--ink-2);
    font-family: var(--font);
    font-size: 14.5px;
    line-height: 1.65;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
  }
  .aurora { position: fixed; inset: 0; z-index: -2; overflow: hidden; pointer-events: none; }
  .orb { position: absolute; border-radius: 50%; filter: blur(95px); opacity: 0.45; }
  .orb-polar { width: 54vw; height: 54vw; left: -14vw; top: -20vw;
    background: radial-gradient(circle at 38% 38%, #38b6e04d, #0e4a6a33 48%, transparent 70%); }
  .orb-violet { width: 46vw; height: 46vw; right: -12vw; top: 30vh;
    background: radial-gradient(circle at 60% 40%, #8b7cff38, transparent 70%); }
  .stars { position: fixed; inset: -60px; z-index: -1; pointer-events: none;
    background-image:
      radial-gradient(1px 1px at 22px 34px, rgba(255,255,255,0.6), transparent),
      radial-gradient(1px 1px at 168px 92px, rgba(255,255,255,0.35), transparent),
      radial-gradient(1.4px 1.4px at 308px 142px, rgba(160,230,255,0.5), transparent),
      radial-gradient(1px 1px at 424px 58px, rgba(255,255,255,0.3), transparent),
      radial-gradient(1.3px 1.3px at 92px 212px, rgba(255,255,255,0.4), transparent);
    background-size: 470px 310px; background-repeat: repeat; opacity: 0.45; }
  .topbar {
    position: sticky; top: 16px; z-index: 50;
    width: min(920px, calc(100vw - 28px));
    margin: 16px auto 0;
    display: flex; align-items: center; gap: 10px;
    padding: 10px 20px;
    border-radius: 999px;
    background: var(--glass);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    backdrop-filter: blur(24px) saturate(180%);
    border: 1px solid var(--edge);
    box-shadow: inset 0 1px 0 var(--edge-bright), 0 18px 44px rgba(0,0,0,0.4);
  }
  @supports not ((backdrop-filter: blur(2px))) {
    .topbar, .box, .mode, .test-list li { background: rgba(18,22,34,0.93); }
  }
  .topbar img { width: 20px; height: 20px;
    filter: drop-shadow(0 0 7px rgba(110,231,255,0.55)); }
  .topbar .brand { font-size: 13px; font-weight: 700; letter-spacing: 0.24em;
    background: linear-gradient(92deg, #fff 25%, var(--polar) 80%);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
  .topbar .sub { color: var(--ink-3); font-size: 10px; letter-spacing: 0.2em; text-transform: uppercase; }
  .topbar a.back {
    margin-left: auto;
    color: var(--ink-2);
    font-size: 12.5px;
    font-weight: 600;
    text-decoration: none;
    padding: 6px 14px;
    border-radius: 999px;
    border: 1px solid transparent;
    transition: all .25s;
  }
  .topbar a.back:hover { color: var(--ink); border-color: var(--edge); background: rgba(255,255,255,0.05); }
  .container {
    max-width: 880px;
    margin: 0 auto;
    padding: 36px 24px 90px;
  }
  h1 {
    color: var(--ink);
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 44px 0 10px;
  }
  h2 {
    color: var(--polar);
    font-size: 15px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin: 28px 0 8px;
  }
  p { margin: 8px 0 14px; }
  .lead {
    color: var(--ink-3);
    font-size: 14px;
    font-style: italic;
    margin-bottom: 22px;
  }
  ul, ol { padding-left: 22px; margin: 8px 0 14px; }
  li { margin-bottom: 8px; }
  .pos { color: var(--pos); font-weight: 700; }
  .neg { color: var(--neg); font-weight: 700; }
  .key { color: var(--ink); font-weight: 650; }
  .dim { color: var(--ink-3); }
  .warn { color: var(--warn); }
  code, .code {
    font-family: var(--mono);
    background: rgba(255,255,255,0.06);
    color: var(--polar);
    padding: 1px 7px;
    border: 1px solid var(--edge);
    border-radius: 7px;
    font-size: 12px;
  }
  .box {
    background: var(--glass);
    -webkit-backdrop-filter: blur(20px) saturate(160%);
    backdrop-filter: blur(20px) saturate(160%);
    border: 1px solid var(--edge);
    border-radius: 20px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 14px 36px rgba(0,0,0,0.3);
    padding: 18px 22px;
    margin: 16px 0;
  }
  .box .label {
    color: var(--polar);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  .modes { display: grid; grid-template-columns: 1fr; gap: 12px; margin: 12px 0 24px; }
  .mode {
    background: var(--glass);
    -webkit-backdrop-filter: blur(20px) saturate(160%);
    backdrop-filter: blur(20px) saturate(160%);
    border: 1px solid var(--edge);
    border-radius: 20px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 14px 36px rgba(0,0,0,0.3);
    padding: 16px 20px;
  }
  .mode .name { color: var(--ink); font-weight: 700; font-size: 14.5px; letter-spacing: 0.04em; }
  .mode .blurb { color: var(--ink-3); font-size: 12.5px; margin-top: 3px; font-style: italic; }
  .mode .body { font-size: 13.5px; margin-top: 10px; }
  .test-list {
    counter-reset: testc;
    list-style: none;
    padding: 0;
  }
  .test-list li {
    counter-increment: testc;
    background: var(--glass);
    -webkit-backdrop-filter: blur(20px) saturate(160%);
    backdrop-filter: blur(20px) saturate(160%);
    border: 1px solid var(--edge);
    border-radius: 18px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.10), 0 10px 28px rgba(0,0,0,0.25);
    padding: 14px 18px 14px 58px;
    margin-bottom: 10px;
    position: relative;
    font-size: 13.5px;
  }
  .test-list li::before {
    content: counter(testc);
    position: absolute;
    left: 16px;
    top: 15px;
    color: #0b0c12;
    font-weight: 700;
    font-size: 13px;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    background: linear-gradient(180deg, #ffffff, #c8ecf9);
    box-shadow: 0 2px 8px rgba(0,0,0,0.35);
    text-align: center;
    line-height: 26px;
  }
  .test-list .name { color: var(--ink); font-weight: 700; }
  .footer {
    margin-top: 70px;
    padding-top: 22px;
    border-top: 1px solid var(--edge);
    color: var(--ink-3);
    font-size: 11.5px;
    text-align: center;
    letter-spacing: 0.04em;
  }
  @media (prefers-reduced-motion: reduce) { .orb { animation: none; } }
</style>
</head>
<body>

<div class="aurora">
  <div class="orb orb-polar"></div>
  <div class="orb orb-violet"></div>
</div>
<div class="stars"></div>

<div class="topbar">
  <img src="/assets/star.svg" alt="Polaris">
  <span class="brand">POLARIS</span>
  <span class="sub">Learn</span>
  <a class="back" href="/">← Back to terminal</a>
</div>

<div class="container">

<p class="lead">
A working trader's mental model for reading the Polaris dealer GEX terminal.
Read this once, then keep it open in a tab while you watch the heatmap until
the patterns become automatic.
</p>

<div class="box">
  <div class="label">THE NAMING</div>
  <p style="margin-bottom:0">
    <span class="key">Polaris finds you the Star Node.</span>
    Polaris is the navigation star — the tool you orient yourself by. The
    Star Node is the brightest object on the map — the dominant magnet of
    dealer positioning, the one strike everything else orbits around. The
    whole dashboard exists to put you on the Star Node.
  </p>
</div>

<h1>The 30-Second Version</h1>
<div class="box">
  <div class="label">THE WHOLE SYSTEM IN THREE QUESTIONS</div>
  <p>
    1. <span class="key">Where is the magnet?</span> — find the Star Node strike.<br>
    2. <span class="key">Is it pulling or repelling?</span> — check the sign.<br>
    3. <span class="key">Are all three indices saying the same thing?</span> — flip to Orion Mode.
  </p>
  <p class="dim" style="margin-bottom:0">
    If the answer to #3 is "no", sit out. If it's "yes", you have a regime call.
  </p>
</div>

<h1>What Is GEX?</h1>
<p>
  Market makers (Citadel, Susquehanna, Optiver, etc.) sit on the other side of every
  retail and institutional options trade. They don't want directional risk, so they
  hedge their positions in the underlying continuously.
</p>
<p>
  <span class="key">Gamma Exposure (GEX)</span> measures how much hedging pressure
  exists at each strike. The sign tells you which direction that pressure pushes price.
</p>

<div class="modes">
  <div class="mode">
    <div class="name pos">+ POSITIVE GEX (bright positive-side cells)</div>
    <div class="blurb">Dealers are LONG gamma at this strike</div>
    <div class="body">
      Dealers' hedges fight price movement. They sell into rallies, buy into dips.
      This <span class="pos">stabilizes</span> price. Strong positive GEX zones act
      like magnets — price gets pulled toward them and pinned. <span class="key">This is what you fade extremes against.</span>
    </div>
  </div>
  <div class="mode">
    <div class="name neg">– NEGATIVE GEX (bright negative-side cells)</div>
    <div class="blurb">Dealers are SHORT gamma at this strike</div>
    <div class="body">
      Dealers' hedges chase price. They sell weakness, buy strength. This
      <span class="neg">amplifies</span> moves and creates trends. Strong negative
      GEX zones REPEL price — moves through them accelerate.
      <span class="key">This is the get-out-of-the-way warning, not a target.</span>
    </div>
  </div>
</div>

<div class="box">
  <div class="label">A NOTE ON COLOR</div>
  <p style="margin-bottom:0">
    The heatmap palette is yours to choose (the <span class="key">Palette</span>
    picker in the control deck — it remembers your choice). Whatever palette you
    run, the rule is the same: <span class="key">one hue family means positive,
    the other means negative, and brightness is magnitude.</span> The default
    Nebula palette maps positive to cyan and negative to magenta.
  </p>
</div>

<h1>The View Modes</h1>
<p class="lead">Four ways to slice the same dealer positioning data. Use the right one for the job.</p>

<div class="modes">

  <div class="mode">
    <div class="name">GEX</div>
    <div class="blurb">Raw gamma exposure — Skylit's default view</div>
    <div class="body">
      The standard view. Each cell is the dollar amount of dealer hedging that
      will hit the market for a 1% move at that strike. <span class="key">Use this 90% of the time.</span>
      0DTE dominates near the close because gamma scales as 1/√T — that's a feature,
      not a bug. It's telling you where the closing pin will be.
    </div>
  </div>

  <div class="mode">
    <div class="name">GEX·√T</div>
    <div class="blurb">Time-normalized — equalizes 0DTE vs longer-dated positioning</div>
    <div class="body">
      Same GEX, multiplied by √T to cancel the natural 1/√T gamma scaling. Removes
      the 0DTE dominance so you can see longer-dated structure. <span class="key">Use this in the morning</span>
      when you want to read the multi-day positioning, or any time you suspect the
      0DTE is masking what's happening at the back of the curve.
    </div>
  </div>

  <div class="mode">
    <div class="name">VEX</div>
    <div class="blurb">Vanna exposure — dealer hedges driven by volatility changes</div>
    <div class="body">
      Vanna is dealer hedging in response to IV changes (not spot moves). Different
      mechanism, often points the same direction as GEX, but on whipsaw days it
      diverges. <span class="key">Use this as a confirmation layer:</span> if both
      GEX and VEX agree on a regime, conviction is high. If they fight each other,
      expect a chop day.
    </div>
  </div>

  <div class="mode">
    <div class="name">ΔΓ/Δt &nbsp;<span class="dim">(Color)</span></div>
    <div class="blurb">Rate of gamma growth into expiry — the "becoming magnetic" view</div>
    <div class="body">
      Color is ∂Γ/∂t — the SPEED at which gamma is growing as expiry approaches.
      Strikes that show a Color spike are about to become magnetic into the close
      even if their current GEX isn't huge yet. <span class="key">Use this in the
      afternoon</span> to see which strikes the closing pin is forming around
      before it actually forms.
    </div>
  </div>

</div>

<h1>How To Tell Signal From Noise</h1>
<p>
  The Star Node strike isn't always meaningful. On quiet days the top cell barely beats
  the runners-up and the Star Node is just whichever cell happened to win by a hair.
  Polaris flags these as <span class="dim">"no clear leader"</span> automatically,
  but here are the five tests in order of importance:
</p>

<ol class="test-list">
  <li>
    <div class="name">MAGNITUDE GAP</div>
    The Star Node's |GEX| should be at least <span class="key">2× the median of the top 5 cells</span>.
    Polaris uses 1.5× as the threshold to flag "no clear leader" automatically.
  </li>
  <li>
    <div class="name">STABILITY</div>
    Has the Star Node been at the same strike for 30+ minutes? If yes, dealers have
    settled their hedges there. If it's flickering between strikes, ignore it
    until it picks one.
  </li>
  <li>
    <div class="name">TIME OF DAY</div>
    First 30 min: positioning still building, ignore the Star Node. Last 30 min:
    the Star Node is at peak strength, gamma concentrates. Lunch (11:30–13:00 ET): dead
    time, low signal.
  </li>
  <li>
    <div class="name">MULTI-INDEX AGREEMENT</div>
    Flip to Orion Mode. If SPY/SPX/QQQ all show the same regime, conviction is real.
    If they disagree, the regime call is mixed and you sit out. <span class="key">This is what Orion is for.</span>
  </li>
  <li>
    <div class="name">RESHUFFLE COOLDOWN</div>
    If Polaris shows a <span class="warn">RESHUFFLED Ns ago</span> chip in the
    stat row, the Star Node strike just changed. Wait for it to settle before acting —
    your old thesis no longer describes current dealer positioning.
  </li>
</ol>

<h1>How To Read The Dashboard, Step By Step</h1>
<ol>
  <li>
    <span class="key">Glance at the freshness dot</span> in the top bar.
    <span class="pos">Live</span> = trust everything. <span class="neg">Stale</span>
    or <span class="dim">Offline</span> = stop reading.
  </li>
  <li>
    <span class="key">Look at the Star Node's side of the palette.</span> Positive = pin regime,
    fade extremes. Negative = trend regime, get out of the way.
  </li>
  <li>
    <span class="key">Check the Star Node's distance from spot.</span> If spot is far
    from the Star Node, it's a <em>target</em> — price will drift toward it.
    If spot is right at the Star Node, it's a <em>pin</em> — price will sit there.
  </li>
  <li>
    <span class="key">Look at the gatekeepers</span> in the pill row under the heatmap. These
    are the secondary nodes between current price and the Star Node — failed tests of
    these often signal a regime flip.
  </li>
  <li>
    <span class="key">Flip to Orion Mode</span> and check whether SPY, SPX, and
    QQQ all agree on the regime. If yes → high conviction. If no → low conviction,
    sit out.
  </li>
  <li>
    <span class="key">End of day:</span> the Star Node at 30 minutes before close is
    usually where it'll close, ±2 strikes. <em>Unless</em> a reshuffle happens.
  </li>
</ol>

<h1>What Orion Mode Is</h1>
<p>
  Three index heatmaps side by side: SPY, SPX, QQQ. Same time, same mode, three
  different positioning maps. The reason it exists: <span class="key">a setup is
  only high-conviction when all three indices agree.</span> If SPY's gamma flips
  negative at 580 but QQQ's stays positive at 510, the negative regime call is
  suspect. If all three flip together, you have real conviction.
</p>
<p class="dim">
  Pick ORION from the ticker rail to enter the view.
</p>

<h1>What The Significance Flag Means</h1>
<p>
  When Polaris detects that the top GEX cell isn't meaningfully bigger than its
  runners-up, the stat row shows the Star Node strike dimmed with the value
  replaced by <span class="dim">"no clear leader"</span>. This means the day is
  too quiet (or the time too early) for dealer positioning to give you a signal.
  <span class="key">When this flag appears, don't trade off the Star Node.</span>
  It's noise.
</p>

<h1>What The Reshuffle Flag Means</h1>
<p>
  When the Star Node strike+expiry suddenly changes, Polaris shows
  <span class="warn">⚠ RESHUFFLED Ns ago</span> in the stat row for 2 minutes.
  This usually happens when:
</p>
<ul>
  <li>A macro print drops (FOMC, NFP, CPI)</li>
  <li>Spot crosses a major round-number level</li>
  <li>0DTE expires and dealer positioning instantly shifts to the next expiry</li>
  <li>A whale executes a huge single trade</li>
</ul>
<p>
  <span class="key">Immediately after a reshuffle, your old thesis is invalid.</span>
  Wait 2–5 minutes for the new structure to settle, then re-read the dashboard
  fresh. The old Star Node is no longer where dealers are hedging.
</p>

<h1>The Slogan</h1>
<div class="box">
  <p style="margin:0; font-size: 15px; color: var(--ink); font-weight: 650; letter-spacing: 0.02em;">
    "Where is the magnet? Is it pulling or repelling? Are all three indices saying the same thing?"
  </p>
</div>

<div class="footer">
  POLARIS · github.com/ayushpanda-25/polaris · The fixed pin in the sky.
</div>

</div>

</body>
</html>
"""


def register_learn_route(server) -> None:
    """
    Mount the /learn route on a Flask server (the underlying app.server
    of either the local Dash app or the Vercel serverless entry).
    """
    @server.route("/learn")
    def _learn_view():
        from flask import Response
        return Response(LEARN_HTML, mimetype="text/html; charset=utf-8")
