"""
The /learn page — a single-page mental model for reading the Polaris
dashboard. Served as a static HTML route off the underlying Flask server
(both the local Dash app and the Vercel serverless entry).

Bloomberg-style theming: black background, orange accents, mono font.
Self-contained — no external CSS or JS.
"""
from __future__ import annotations


LEARN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POLARIS · Learn</title>
<link rel="icon" type="image/svg+xml" href="/assets/northstar.svg">
<style>
  :root {
    --bg: #000000;
    --panel: #0a0a0a;
    --border: #1a1a1a;
    --border-bright: #2a2a2a;
    --orange: #fa8c00;
    --amber: #ffb627;
    --text: #d4d4d4;
    --dim: #7a7a7a;
    --cyan: #00b4d8;
    --green: #00ff7f;
    --red: #ff3333;
    --yellow: #ffd60a;
    --mono: 'JetBrains Mono', 'IBM Plex Mono', 'Menlo', 'Consolas', monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.6;
  }
  .topbar {
    display: flex; align-items: center;
    height: 56px;
    border-bottom: 2px solid var(--orange);
    padding: 0 24px;
  }
  .topbar img { width: 24px; height: 24px; margin-right: 12px;
    filter: drop-shadow(0 0 4px rgba(250,140,0,0.5)); }
  .topbar .brand { color: var(--orange); font-size: 16px; font-weight: 700; letter-spacing: 2px; }
  .topbar .sub { color: var(--dim); font-size: 9px; letter-spacing: 1px; margin-left: 8px; }
  .topbar a.back {
    margin-left: auto;
    color: var(--orange);
    font-size: 11px;
    letter-spacing: 1px;
    text-decoration: none;
    padding: 6px 14px;
    border: 1px solid var(--border-bright);
  }
  .container {
    max-width: 880px;
    margin: 0 auto;
    padding: 32px 24px 80px 24px;
  }
  h1 {
    color: var(--orange);
    font-size: 18px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin: 32px 0 4px 0;
    border-bottom: 1px solid var(--border-bright);
    padding-bottom: 8px;
  }
  h2 {
    color: var(--amber);
    font-size: 14px;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin: 28px 0 8px 0;
  }
  p { margin: 8px 0 14px 0; color: var(--text); }
  .lead {
    color: var(--dim);
    font-size: 12px;
    font-style: italic;
    margin-bottom: 20px;
  }
  ul, ol { padding-left: 22px; margin: 8px 0 14px 0; }
  li { margin-bottom: 8px; }
  .pos { color: var(--green); font-weight: 700; }
  .neg { color: var(--red); font-weight: 700; }
  .key { color: var(--amber); font-weight: 700; }
  .dim { color: var(--dim); }
  .cyan { color: var(--cyan); }
  .yellow { color: var(--yellow); }
  code, .code {
    background: var(--panel);
    color: var(--cyan);
    padding: 1px 6px;
    border: 1px solid var(--border-bright);
    font-size: 11px;
  }
  .box {
    background: var(--panel);
    border: 1px solid var(--border-bright);
    border-left: 3px solid var(--orange);
    padding: 14px 18px;
    margin: 16px 0;
  }
  .box .label {
    color: var(--orange);
    font-size: 9px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 6px;
  }
  .modes { display: grid; grid-template-columns: 1fr; gap: 12px; margin: 12px 0 24px 0; }
  .mode {
    background: var(--panel);
    border: 1px solid var(--border-bright);
    padding: 14px 18px;
  }
  .mode .name { color: var(--amber); font-weight: 700; font-size: 13px; letter-spacing: 1px; }
  .mode .blurb { color: var(--dim); font-size: 11px; margin-top: 4px; font-style: italic; }
  .mode .body { color: var(--text); font-size: 12px; margin-top: 10px; }
  .test-list {
    counter-reset: testc;
    list-style: none;
    padding: 0;
  }
  .test-list li {
    counter-increment: testc;
    background: var(--panel);
    border: 1px solid var(--border-bright);
    padding: 12px 16px 12px 50px;
    margin-bottom: 8px;
    position: relative;
  }
  .test-list li::before {
    content: counter(testc);
    position: absolute;
    left: 14px;
    top: 14px;
    color: var(--orange);
    font-weight: 700;
    font-size: 14px;
    width: 24px;
    height: 24px;
    border: 1px solid var(--orange);
    text-align: center;
    line-height: 22px;
  }
  .test-list .name { color: var(--amber); font-weight: 700; }
  .footer {
    margin-top: 60px;
    padding-top: 20px;
    border-top: 1px solid var(--border-bright);
    color: var(--dim);
    font-size: 10px;
    text-align: center;
  }
</style>
</head>
<body>

<div class="topbar">
  <img src="/assets/northstar.svg" alt="Polaris">
  <span class="brand">POLARIS</span>
  <span class="sub"> · LEARN</span>
  <a class="back" href="/">← BACK TO TERMINAL</a>
</div>

<div class="container">

<p class="lead">
A working trader's mental model for reading the Polaris dealer GEX terminal.
Read this once, then keep it open in a tab while you watch the heatmap until
the patterns become automatic.
</p>

<h1>The 30-Second Version</h1>
<div class="box">
  <div class="label">THE WHOLE SYSTEM IN THREE QUESTIONS</div>
  <p>
    1. <span class="key">Where is the magnet?</span> — find the King strike.<br>
    2. <span class="key">Is it pulling or repelling?</span> — check the sign.<br>
    3. <span class="key">Are all three indices saying the same thing?</span> — flip to Trinity Mode.
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
    <div class="name pos">+ POSITIVE GEX (amber/yellow cells)</div>
    <div class="blurb">Dealers are LONG gamma at this strike</div>
    <div class="body">
      Dealers' hedges fight price movement. They sell into rallies, buy into dips.
      This <span class="pos">stabilizes</span> price. Strong positive GEX zones act
      like magnets — price gets pulled toward them and pinned. <span class="key">This is what you fade extremes against.</span>
    </div>
  </div>
  <div class="mode">
    <div class="name neg">– NEGATIVE GEX (deep red cells)</div>
    <div class="blurb">Dealers are SHORT gamma at this strike</div>
    <div class="body">
      Dealers' hedges chase price. They sell weakness, buy strength. This
      <span class="neg">amplifies</span> moves and creates trends. Strong negative
      GEX zones REPEL price — moves through them accelerate.
      <span class="key">This is the get-out-of-the-way warning, not a target.</span>
    </div>
  </div>
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
    <div class="name">Δ\u0393/Δt &nbsp;<span class="dim">(Color)</span></div>
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
  The King strike isn't always meaningful. On quiet days the top cell barely beats
  the runners-up and the "King" is just whichever cell happened to win by a hair.
  Polaris flags these as <span class="dim">"no clear leader"</span> automatically,
  but here are the five tests in order of importance:
</p>

<ol class="test-list">
  <li>
    <div class="name">MAGNITUDE GAP</div>
    The King's |GEX| should be at least <span class="key">2× the median of the top 5 cells</span>.
    Polaris uses 1.5× as the threshold to flag "no clear leader" automatically.
  </li>
  <li>
    <div class="name">STABILITY</div>
    Has the King been at the same strike for 30+ minutes? If yes, dealers have
    settled their hedges there. If it's flickering between strikes, ignore it
    until it picks one.
  </li>
  <li>
    <div class="name">TIME OF DAY</div>
    First 30 min: positioning still building, ignore the King. Last 30 min:
    King is at peak strength, gamma concentrates. Lunch (11:30–13:00 ET): dead
    time, low signal.
  </li>
  <li>
    <div class="name">MULTI-INDEX AGREEMENT</div>
    Flip to Trinity Mode. If SPY/SPX/QQQ all show the same regime, conviction is real.
    If they disagree, the regime call is mixed and you sit out. <span class="key">This is what Trinity is for.</span>
  </li>
  <li>
    <div class="name">RESHUFFLE COOLDOWN</div>
    If Polaris shows a <span class="yellow">RESHUFFLED Ns ago</span> tag in the
    header, the King strike just changed. Wait for it to settle before acting —
    your old thesis no longer describes current dealer positioning.
  </li>
</ol>

<h1>How To Read The Dashboard, Step By Step</h1>
<ol>
  <li>
    <span class="key">Glance at the freshness badge</span> in the top right.
    <span class="pos">LIVE</span> = trust everything. <span class="neg">STALE</span>
    or <span class="dim">OFFLINE</span> = stop reading.
  </li>
  <li>
    <span class="key">Look at the King's color.</span> Amber/green = pin regime,
    fade extremes. Deep red = trend regime, get out of the way.
  </li>
  <li>
    <span class="key">Check the King's distance from spot.</span> If spot is far
    from the King, the King is a <em>target</em> — price will drift toward it.
    If spot is right at the King, the King is a <em>pin</em> — price will sit there.
  </li>
  <li>
    <span class="key">Look at the gatekeepers</span> in the bottom status bar. These
    are the secondary nodes between current price and the King — failed tests of
    these often signal a regime flip.
  </li>
  <li>
    <span class="key">Flip to Trinity Mode</span> and check whether SPY, SPX, and
    QQQ all agree on the regime. If yes → high conviction. If no → low conviction,
    sit out.
  </li>
  <li>
    <span class="key">End of day:</span> the King at 30 minutes before close is
    usually where it'll close, ±2 strikes. <em>Unless</em> a reshuffle happens.
  </li>
</ol>

<h1>What Trinity Mode Is</h1>
<p>
  Three index heatmaps side by side: SPY, SPX, QQQ. Same time, same mode, three
  different positioning maps. The reason it exists: <span class="key">a setup is
  only high-conviction when all three indices agree.</span> If SPY's gamma flips
  negative at 580 but QQQ's stays positive at 510, the negative regime call is
  suspect. If all three flip together, you have real conviction.
</p>
<p class="dim">
  Pick TRINITY from the ticker dropdown to enter the view.
</p>

<h1>What The Significance Flag Means</h1>
<p>
  When Polaris detects that the top GEX cell isn't meaningfully bigger than its
  runners-up, the header shows the King strike in dim grey with the value
  replaced by <span class="dim">"no clear leader"</span>. This means the day is
  too quiet (or the time too early) for dealer positioning to give you a signal.
  <span class="key">When this flag appears, don't trade off the King.</span>
  It's noise.
</p>

<h1>What The Reshuffle Flag Means</h1>
<p>
  When the King strike+expiry suddenly changes, Polaris shows
  <span class="yellow">⚠ RESHUFFLED Ns ago</span> in the header for 2 minutes.
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
  fresh. The old King is no longer where dealers are hedging.
</p>

<h1>The Slogan</h1>
<div class="box">
  <p style="margin:0; font-size: 14px; color: var(--amber); font-weight: 700; letter-spacing: 1px;">
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
