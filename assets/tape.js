/* ════════════════════════════════════════════════════════════════════
   POLARIS — the strike tape.

   Hover a cell and this draws the card: what the node is worth now, its
   line over the last half hour, and how fast it got there.

   All of it runs in the browser. The server ships one payload per poll
   (`tape-store`) holding the whole visible board — live values plus the
   history behind them out of gex_snapshots — and every hover after that is
   a local lookup. That is deliberate: hover fires dozens of times a minute,
   and routing it through a Dash callback would spend a serverless
   invocation on each one (the same trap the visibility heartbeat avoids).

   Two sources feed one series:
     • the server tape — deep (30 min of sparkline + 1h/4h/1d anchors),
       present wherever gex.db is (the Mac terminal)
     • the ring buffer below — shallow but universal: every poll appends the
       board, so the cloud terminal, which has no snapshot store at all,
       still grows a real tape while you watch it

   Timestamps are the SERVER's throughout (each payload carries the clock it
   was built on), so nothing here depends on the viewer's clock being right.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var RING = {};                 // "SPY|gex" → [{t: served, c: {cellKey: value}}]
  var RING_MAX = 240;            // ~2h at the cloud's 30s poll, ~1h at the Mac's 15s
  var RING_KEYS = 6;             // a few tickers/modes back, then evict
  var cursor = { x: 0, y: 0 };
  var lastKey = null;

  // Short lookbacks, computed from the merged series. The extended ones
  // (1h/4h/1d) are anchored server-side — see cell_history.EXTENDED_LAGS.
  var LAGS = [
    [60, "1 min"],
    [300, "5 min"],
    [600, "10 min"],
    [900, "15 min"]
  ];

  // A cell counts as HOT only if it is both moving fast AND big enough to
  // matter on this board — otherwise a $2K cell doubling to $4K would out-
  // shout the Star Node.
  var HOT_PCT = 35;              // |5-min change| in percent
  var HOT_FLOOR = 0.08;          // ... and at least 8% of the board's largest cell

  document.addEventListener("mousemove", function (e) {
    cursor.x = e.clientX;
    cursor.y = e.clientY;
  }, { passive: true });

  // ── formatting ────────────────────────────────────────────────────
  // Values arrive in thousands of dollars, same units the board labels use.

  function fmtValue(v, opts) {
    opts = opts || {};
    if (v === null || v === undefined || !isFinite(v)) return "—";
    var sign = v < 0 ? "−" : (opts.signed ? "+" : "");
    var a = Math.abs(v);
    var group = function (n, dp) {
      return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
    };
    // ∂Γ/∂t sums run into the trillions, so the ladder needs a B rung.
    if (a >= 1e6) return sign + "$" + group(a / 1e6, 2) + "B";
    if (a >= 1000) return sign + "$" + group(a / 1000, 2) + "M";
    if (a >= 1) return sign + "$" + group(a, a >= 100 ? 0 : 1) + "K";
    if (a === 0) return "$0";
    return sign + "$" + a.toFixed(2) + "K";
  }

  function fmtPct(p) {
    if (p === null || p === undefined || !isFinite(p)) return "—";
    var sign = p < 0 ? "−" : "+";
    var a = Math.abs(p);
    if (a >= 1000) return sign + Math.round(a).toLocaleString() + "%";
    return sign + a.toFixed(1) + "%";
  }

  function fmtAge(sec) {
    if (sec === null || sec === undefined) return "";
    if (sec < 90) return Math.round(sec) + "s";
    if (sec < 5400) return Math.round(sec / 60) + "m";
    if (sec < 172800) return (sec / 3600).toFixed(1) + "h";
    return Math.round(sec / 86400) + "d";
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ── the series ────────────────────────────────────────────────────

  function ringKey(tape) { return tape.ticker + "|" + tape.mode; }

  function record(tape) {
    if (!tape || !tape.ok || !tape.cells) return;
    var key = ringKey(tape);
    var buf = RING[key] || (RING[key] = []);
    var last = buf[buf.length - 1];
    // Poll cadence outruns the compute loop, so the same grid comes around
    // more than once — stamp one entry per computed grid, not per poll.
    if (last && last.g === tape.ts) return;
    var vals = {};
    for (var k in tape.cells) {
      if (Object.prototype.hasOwnProperty.call(tape.cells, k)) vals[k] = tape.cells[k].v;
    }
    buf.push({ t: tape.served, g: tape.ts, c: vals });
    if (buf.length > RING_MAX) buf.splice(0, buf.length - RING_MAX);

    var keys = Object.keys(RING);
    while (keys.length > RING_KEYS) delete RING[keys.shift()];
  }

  /** Server history and locally-recorded polls, merged into one ascending
   *  [[t, value], …]. Points within 5s of each other collapse — a server row
   *  and a ring entry for the same instant are the same observation. */
  function seriesFor(tape, key) {
    var pts = [];
    var cell = tape.cells[key];
    if (cell && cell.s && tape.t) {
      for (var i = 0; i < tape.t.length; i++) {
        var v = cell.s[i];
        if (v !== null && v !== undefined) pts.push([tape.t[i], v]);
      }
    }
    var buf = RING[ringKey(tape)] || [];
    for (var j = 0; j < buf.length; j++) {
      var rv = buf[j].c[key];
      if (rv !== null && rv !== undefined) pts.push([buf[j].t, rv]);
    }
    pts.sort(function (a, b) { return a[0] - b[0]; });
    var out = [];
    for (var n = 0; n < pts.length; n++) {
      if (out.length && pts[n][0] - out[out.length - 1][0] < 5) out[out.length - 1] = pts[n];
      else out.push(pts[n]);
    }
    return out;
  }

  /** Point NEAREST to `now - lag`, among those aged between half and twice
   *  that lookback. Mirrors cell_history.pick_anchor — see the note there on
   *  why nearest beats newest-at-or-before at a 69-second cadence. Null when
   *  the band is empty, so a gap reads "—" instead of a wrong number. */
  var LAG_MIN_FRAC = 0.5, LAG_MAX_FRAC = 2.0;
  function anchorAt(series, now, lag) {
    var target = now - lag;
    var oldest = now - lag * LAG_MAX_FRAC, newest = now - lag * LAG_MIN_FRAC;
    var best = null;
    for (var i = 0; i < series.length; i++) {
      var t = series[i][0];
      if (t < oldest || t > newest) continue;
      if (!best || Math.abs(t - target) < Math.abs(best[0] - target)) best = series[i];
    }
    return best;
  }

  function change(cur, past) {
    if (past === null || past === undefined) return null;
    var d = cur - past;
    // Percent is against |past| so a negative node growing more negative
    // reads as growth, not decay. A cell coming from exactly zero has no
    // meaningful percentage — the absolute delta carries it.
    var pct = past === 0 ? null : (d / Math.abs(past)) * 100;
    return { d: d, pct: pct, flip: (past < 0) !== (cur < 0) && past !== 0 && cur !== 0 };
  }

  // ── sparkline ─────────────────────────────────────────────────────

  function sparkline(series, w, h) {
    if (series.length < 2) return "";
    var pad = 4;
    var t0 = series[0][0], t1 = series[series.length - 1][0];
    var span = Math.max(t1 - t0, 1);
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < series.length; i++) {
      lo = Math.min(lo, series[i][1]);
      hi = Math.max(hi, series[i][1]);
    }
    var flat = hi - lo < 1e-9;
    if (flat) { hi += 1; lo -= 1; }                  // a flat line still needs a band
    var X = function (t) { return pad + ((t - t0) / span) * (w - 2 * pad); };
    var Y = function (v) { return pad + (1 - (v - lo) / (hi - lo)) * (h - 2 * pad); };

    var d = "";
    for (var j = 0; j < series.length; j++) {
      d += (j ? "L" : "M") + X(series[j][0]).toFixed(1) + " " + Y(series[j][1]).toFixed(1);
    }
    var rising = series[series.length - 1][1] >= series[0][1];
    var stroke = rising ? "var(--tape-up)" : "var(--tape-down)";
    var base = (lo < 0 && hi > 0) ? Y(0) : h - pad;   // fill down to zero when zero is on screen
    var area = d + "L" + X(t1).toFixed(1) + " " + base.toFixed(1) +
               "L" + X(t0).toFixed(1) + " " + base.toFixed(1) + "Z";

    var zero = "";
    if (lo < 0 && hi > 0) {
      zero = '<line x1="' + pad + '" x2="' + (w - pad) + '" y1="' + Y(0).toFixed(1) +
             '" y2="' + Y(0).toFixed(1) + '" class="tape-zero"/>';
    }
    var last = series[series.length - 1];
    // Fade the fill out downward. A flat line sitting high in the band would
    // otherwise fill the whole box and read as a bar, not a track.
    var grad = '<defs><linearGradient id="tape-grad" x1="0" x2="0" y1="0" y2="1">' +
      '<stop offset="0" stop-color="' + stroke + '" stop-opacity="0.26"/>' +
      '<stop offset="1" stop-color="' + stroke + '" stop-opacity="0.01"/></linearGradient></defs>';
    return '<svg class="tape-spark" viewBox="0 0 ' + w + " " + h + '" width="' + w +
      '" height="' + h + '" preserveAspectRatio="none" aria-hidden="true">' + grad +
      '<path d="' + area + '" fill="url(#tape-grad)"/>' + zero +
      '<path d="' + d + '" fill="none" stroke="' + stroke +
      '" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>' +
      '<circle cx="' + X(last[0]).toFixed(1) + '" cy="' + Y(last[1]).toFixed(1) +
      '" r="2.6" fill="' + stroke + '"/></svg>' +
      // One label, not two, when the whole window sits on one value — a band
      // repeating the same number twice just looks broken.
      '<div class="tape-axis"><span>' + esc(fmtValue(hi)) + '</span><span>' +
      (flat ? "" : esc(fmtValue(lo))) + '</span></div>';
  }

  // ── the card ──────────────────────────────────────────────────────

  /** Does this row's anchor sit far enough from its nominal lookback to be
   *  worth admitting?
   *
   *  The absolute floor is what makes the flag mean something. Snapshots land
   *  about every 69 seconds, so a "1 min" row honestly resolves anywhere from
   *  30s to 2 minutes back — flagging that every time would just train the eye
   *  to ignore the mark. A 60s floor leaves the short rows alone (the anchor
   *  band already bounds them) and keeps the flag for what it is really for:
   *  the long rows, where a restart or a dead feed can put "4 hours" three
   *  hours out of position. On those, 15% is about an hour — worth saying. */
  function isOff(actual, nominal) {
    if (!actual || !nominal) return false;
    return Math.abs(actual - nominal) > Math.max(nominal * 0.15, 60);
  }

  function row(label, ch, nominal, actual) {
    if (!ch) {
      return '<div class="tape-row dim"><span class="tape-row-k">' + esc(label) +
        '</span><span class="tape-row-v">—</span><span class="tape-row-p">—</span></div>';
    }
    // Flag a row whose anchor sits well off its nominal lookback, in either
    // direction — after a gap, "1 hour" may be the closest thing on disk to
    // an hour ago without being an hour ago.
    var off = isOff(actual, nominal);
    var cls = ch.d === 0 ? "flat" : (ch.d > 0 ? "up" : "down");
    return '<div class="tape-row' + (off ? " off" : "") + '">' +
      '<span class="tape-row-k">' + esc(label) + (off ? '<i>' + esc(fmtAge(actual)) + '</i>' : "") + '</span>' +
      '<span class="tape-row-v ' + cls + '">' + esc(fmtValue(ch.d, { signed: true })) + '</span>' +
      '<span class="tape-row-p ' + cls + '">' + esc(fmtPct(ch.pct)) + '</span></div>';
  }

  function build(tape, key) {
    var cell = tape.cells[key];
    if (!cell) return null;
    var parts = key.split("|");
    var strike = parts[0], expLabel = parts[1];
    var meta = (tape.exp || {})[expLabel] || {};
    var series = seriesFor(tape, key);
    var now = tape.served;
    var cur = cell.v;

    // Short windows off the merged series.
    var short = LAGS.map(function (l) {
      var a = anchorAt(series, now, l[0]);
      return { label: l[1], nominal: l[0], ch: a ? change(cur, a[1]) : null,
               actual: a ? now - a[0] : null };
    });
    // Extended windows come pre-anchored from the snapshot store; if there's
    // no store, a long-lived ring buffer can still reach the nearer ones.
    var ext = (tape.lags || []).map(function (lag, i) {
      var past = cell.x ? cell.x[i] : null;
      var age = (tape.extTs && tape.extTs[i]) ? now - tape.extTs[i] : null;
      if (past === null || past === undefined) {
        var a = anchorAt(series, now, lag);
        if (a) { past = a[1]; age = now - a[0]; }
      }
      return { label: tape.labels[i], nominal: lag,
               ch: (past === null || past === undefined) ? null : change(cur, past),
               actual: age };
    });

    var vel = short[0].ch;
    var five = short[1].ch;
    var flipped = short.some(function (s) { return s.ch && s.ch.flip; });

    var badges = "";
    if (tape.star === key) badges += '<span class="tape-badge star">★ STAR NODE</span>';
    if (flipped) badges += '<span class="tape-badge flip">SIGN FLIP</span>';
    else if (five && five.pct !== null && Math.abs(cur) >= HOT_FLOOR * (tape.vmax || 0)) {
      if (five.pct >= HOT_PCT) badges += '<span class="tape-badge hot">HOT</span>';
      else if (five.pct <= -HOT_PCT) badges += '<span class="tape-badge fade">FADING</span>';
    }

    var html = '<div class="tape-head"><div>' +
      '<div class="tape-strike">' + esc(strike) + '</div>' +
      '<div class="tape-exp">' + esc(expLabel) +
      (meta.dte === null || meta.dte === undefined ? "" : " · " + meta.dte + " DTE") +
      '</div></div><div class="tape-badges">' + badges + '</div></div>';

    // The mode label keeps its own casing — uppercasing "Color (∂Γ/∂t)"
    // turns the time variable into ∂T, which is a different derivative.
    html += '<div class="tape-now"><span class="tape-now-k">' +
      '<span class="tape-now-mode">' + esc(tape.modeLabel) + '</span> NOW</span>' +
      '<span class="tape-now-v ' + (cur >= 0 ? "pos" : "neg") + '">' +
      esc(fmtValue(cur)) + '</span></div>';

    html += '<div class="tape-sec">Value over time</div>';
    if (series.length >= 2) {
      var span = series[series.length - 1][0] - series[0][0];
      html += '<div class="tape-chart">' + sparkline(series, 262, 58) + '</div>' +
        '<div class="tape-times"><span>' + esc(fmtAge(span)) + ' ago</span>' +
        '<span>' + esc(fmtAge(span / 2)) + '</span><span>now</span></div>';
    } else if (tape.reason === "not-stored") {
      html += '<div class="tape-none">History isn\'t stored for this view — ' +
        'it builds while the tab stays open.</div>';
    } else {
      html += '<div class="tape-none">Collecting… the line fills in as the board refreshes.</div>';
    }

    html += '<div class="tape-sec">Rate of change</div>';
    short.forEach(function (s) { html += row(s.label, s.ch, s.nominal, s.actual); });
    if (ext.length) {
      html += '<div class="tape-sec">Extended</div>';
      ext.forEach(function (s) { html += row(s.label, s.ch, s.nominal, s.actual); });
    }

    if (vel) {
      html += '<div class="tape-vel">1m velocity <b class="' +
        (vel.d === 0 ? "flat" : (vel.d > 0 ? "up" : "down")) + '">' + esc(fmtPct(vel.pct)) + '</b> <span>(' +
        esc(fmtValue(vel.d, { signed: true })) + ')</span></div>';
    }
    if (cell.w && cell.w.length) {
      html += '<div class="tape-warn"><b>⚠ Sign uncertain</b>' +
        cell.w.map(function (r) { return "<span>" + esc(r) + "</span>"; }).join("") +
        '</div>';
    }
    var offRow = function (s) { return s.ch && isOff(s.actual, s.nominal); };
    if (short.some(offRow) || ext.some(offRow)) {
      html += '<div class="tape-foot">Rows marked with an age were measured at the ' +
        'nearest snapshot, not exactly that far back.</div>';
    }
    return html;
  }

  function place(el) {
    var pad = 14;
    var r = el.getBoundingClientRect();
    var x = cursor.x + 20, y = cursor.y + 18;
    if (x + r.width > window.innerWidth - pad) x = cursor.x - r.width - 20;
    if (y + r.height > window.innerHeight - pad) y = window.innerHeight - r.height - pad;
    el.style.left = Math.max(pad, x) + "px";
    el.style.top = Math.max(pad, y) + "px";
  }

  function hide(el) {
    if (el) { el.dataset.open = "0"; el.innerHTML = ""; }
    lastKey = null;
  }

  window.polarisTape = {
    tick: function (hover, tape) {
      var el = document.getElementById("cell-tape");
      var nu = (window.dash_clientside || {}).no_update;
      try {
        record(tape);
        if (!el) return nu;
        if (!hover || !hover.points || !hover.points.length || !tape || !tape.ok) {
          hide(el);
          return nu;
        }
        var p = hover.points[0];
        var key = String(p.y) + "|" + String(p.x);
        var html = build(tape, key);
        if (!html) { hide(el); return nu; }
        // Re-place only when the pointer lands on a different cell, so the
        // card holds still while you read it.
        var moved = key !== lastKey;
        el.innerHTML = html;
        el.dataset.open = "1";
        if (moved) { lastKey = key; place(el); }
      } catch (err) {
        if (window.console) console.warn("[polaris tape]", err);
        hide(el);
      }
      return nu;
    }
  };
})();
