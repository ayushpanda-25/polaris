"""
Flask-level authentication gate for Polaris.

Three tiers:
  1. Astraios members — enter a friend code set in config.FRIEND_CODES.
  2. BYOK users — enter their own LSEG API key (format-validated, not stored).
  3. A link out to the cloud terminal, which has its own Astraios account gate
     (src/astraios_auth.py) — it stopped being an open demo when it was locked.

The gate is a pure Flask layer that sits in front of the Dash app via
@before_request. The login page is self-contained HTML (no Dash/React
dependency). Once authenticated, a signed Flask session cookie grants
access until the browser session ends or the user hits /logout.
"""
from __future__ import annotations

import hmac
import re
import secrets
import threading
import time

from flask import Flask, Response, redirect, request, session

# Lazy import to avoid circular dependency at module level.
# config is imported inside register_auth() instead.

# Paths that must bypass auth (login routes + Dash STATIC assets only).
# NB: the bare "/_dash-" prefix used to be here — it whitelisted the Dash DATA
# endpoints (/_dash-update-component, /_dash-layout, /_dash-dependencies) too,
# leaking the live GEX grid to anyone unauthenticated. Only the static bundle
# path is public now; the data endpoints fall back to requiring a session.
_PUBLIC_PREFIXES = (
    "/login",
    "/_dash-component-suites/",
    "/assets/",
    "/_favicon",
    "/_reload-hash",
)

# LSEG API key format: 32+ hex characters
_LSEG_KEY_RE = re.compile(r"^[a-f0-9]{32,}$", re.IGNORECASE)


# The North Star mark, inlined so the login page stays self-contained (it must
# render before any Dash asset loads). Shared with astraios_auth.py's page.
STAR_SVG = """
    <svg width="52" height="52" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="ps_core" cx="50%" cy="50%" r="62%">
          <stop offset="0%"  stop-color="#ffffff"/>
          <stop offset="42%" stop-color="#dff6ff"/>
          <stop offset="82%" stop-color="#6ee7ff"/>
          <stop offset="100%" stop-color="#38b6e0"/>
        </radialGradient>
        <filter id="ps_glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="0.9" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <g opacity="0.4" stroke="#6ee7ff" stroke-width="0.55" stroke-linecap="round">
        <line x1="5" y1="5" x2="9" y2="9"/>
        <line x1="19" y1="5" x2="15" y2="9"/>
        <line x1="5" y1="19" x2="9" y2="15"/>
        <line x1="19" y1="19" x2="15" y2="15"/>
      </g>
      <path d="M12 0 L13.2 10.2 L24 12 L13.2 13.8 L12 24 L10.8 13.8 L0 12 L10.8 10.2 Z"
            fill="url(#ps_core)" filter="url(#ps_glow)"/>
      <circle cx="12" cy="12" r="0.9" fill="#ffffff"/>
    </svg>
"""


def _login_html(error: str = "") -> str:
    """Return the liquid-glass login page as a self-contained HTML string."""
    star_svg = STAR_SVG

    error_block = ""
    if error:
        error_block = f"""
        <div class="error-box">{error}</div>
        """

    # Import config here to get the demo URL
    try:
        import config as app_config
        demo_url = getattr(app_config, "DEMO_URL", "https://polaris-omega-five.vercel.app")
    except ImportError:
        demo_url = "https://polaris-omega-five.vercel.app"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#04060d">
    <title>Polaris — Connect</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --bg: #04060d;
            --ink: rgba(255,255,255,0.92);
            --ink-2: rgba(255,255,255,0.56);
            --ink-3: rgba(255,255,255,0.34);
            --glass: rgba(255,255,255,0.055);
            --edge: rgba(255,255,255,0.10);
            --edge-bright: rgba(255,255,255,0.18);
            --polar: #6ee7ff;
            --font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", sans-serif;
            --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
        }}
        body {{
            background: var(--bg);
            color: var(--ink);
            font-family: var(--font);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            overflow: hidden;
            -webkit-font-smoothing: antialiased;
        }}
        .aurora {{ position: fixed; inset: 0; z-index: -2; overflow: hidden; }}
        .orb {{ position: absolute; border-radius: 50%; filter: blur(95px); opacity: 0.5; }}
        .orb-polar {{ width: 58vw; height: 58vw; left: -16vw; top: -22vw;
            background: radial-gradient(circle at 38% 38%, #38b6e04d, #0e4a6a33 48%, transparent 70%);
            animation: drift-a 80s ease-in-out infinite alternate; }}
        .orb-violet {{ width: 48vw; height: 48vw; right: -14vw; bottom: -16vw;
            background: radial-gradient(circle at 60% 40%, #8b7cff40, #4636a322 52%, transparent 72%);
            animation: drift-b 95s ease-in-out infinite alternate; }}
        @keyframes drift-a {{ to {{ transform: translate(8vw, 6vh) scale(1.10); }} }}
        @keyframes drift-b {{ to {{ transform: translate(-6vw, -8vh) scale(0.93); }} }}
        .stars {{ position: fixed; inset: -60px; z-index: -1; pointer-events: none;
            background-image:
                radial-gradient(1px 1px at 22px 34px, rgba(255,255,255,0.7), transparent),
                radial-gradient(1px 1px at 168px 92px, rgba(255,255,255,0.4), transparent),
                radial-gradient(1.4px 1.4px at 308px 142px, rgba(160,230,255,0.6), transparent),
                radial-gradient(1px 1px at 424px 58px, rgba(255,255,255,0.32), transparent),
                radial-gradient(1.3px 1.3px at 92px 212px, rgba(255,255,255,0.45), transparent),
                radial-gradient(1px 1px at 244px 262px, rgba(190,210,255,0.4), transparent);
            background-size: 470px 310px; background-repeat: repeat;
            opacity: 0.5; animation: twinkle 8s ease-in-out infinite alternate; }}
        @keyframes twinkle {{ to {{ opacity: 0.2; }} }}
        .gate {{
            width: min(420px, calc(100vw - 32px));
            padding: 46px 40px 38px;
            text-align: center;
            border-radius: 28px;
            background: var(--glass);
            -webkit-backdrop-filter: blur(24px) saturate(180%);
            backdrop-filter: blur(24px) saturate(180%);
            border: 1px solid var(--edge);
            box-shadow:
                inset 0 1px 0 var(--edge-bright),
                inset 0 -1px 0 rgba(255,255,255,0.04),
                0 24px 60px rgba(0,0,0,0.45);
            animation: rise .9s cubic-bezier(.2,.7,.2,1) both;
        }}
        @keyframes rise {{
            from {{ opacity: 0; transform: translateY(26px); filter: blur(8px); }}
        }}
        @supports not ((backdrop-filter: blur(2px)) or (-webkit-backdrop-filter: blur(2px))) {{
            .gate {{ background: rgba(18, 22, 34, 0.93); }}
        }}
        .logo {{ margin-bottom: 14px; filter: drop-shadow(0 0 16px rgba(110,231,255,0.45)); }}
        .title {{
            font-size: 24px;
            font-weight: 700;
            letter-spacing: 0.28em;
            margin-bottom: 6px;
            background: linear-gradient(92deg, #ffffff 25%, var(--polar) 80%);
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            font-size: 11px;
            color: var(--ink-3);
            letter-spacing: 0.22em;
            text-transform: uppercase;
            margin-bottom: 34px;
        }}
        .input-group {{ margin-bottom: 14px; text-align: left; }}
        .input-label {{
            font-size: 10px;
            color: var(--ink-3);
            letter-spacing: 0.14em;
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 7px;
            padding-left: 6px;
        }}
        input[type="text"], input[type="password"] {{
            width: 100%;
            padding: 13px 18px;
            background: rgba(255,255,255,0.06);
            border: 1px solid var(--edge);
            border-radius: 999px;
            color: var(--ink);
            font-family: var(--mono);
            font-size: 14px;
            outline: none;
            transition: border-color .25s, background .25s, box-shadow .25s;
        }}
        input::placeholder {{ color: var(--ink-3); font-family: var(--font); }}
        input:focus {{
            border-color: rgba(110,231,255,0.5);
            background: rgba(255,255,255,0.08);
            box-shadow: 0 0 0 4px rgba(110,231,255,0.08);
        }}
        .btn {{
            width: 100%;
            padding: 13px;
            color: #0b0c12;
            background: linear-gradient(180deg, #ffffff, #d9dde6);
            border: none;
            border-radius: 999px;
            font-family: var(--font);
            font-size: 13.5px;
            font-weight: 700;
            letter-spacing: 0.12em;
            cursor: pointer;
            margin-top: 10px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.4), inset 0 1px 0 #fff;
            transition: transform .25s, box-shadow .25s;
        }}
        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 26px rgba(110,231,255,0.22), inset 0 1px 0 #fff;
        }}
        .error-box {{
            color: #ffb4ae;
            font-size: 12.5px;
            margin-top: 14px;
            padding: 10px 16px;
            border: 1px solid rgba(255,69,58,0.30);
            border-radius: 14px;
            background: rgba(255,69,58,0.10);
        }}
        .toggle-link {{
            font-size: 12px;
            color: var(--ink-3);
            cursor: pointer;
            margin-top: 18px;
            display: inline-block;
            transition: color .25s;
        }}
        .toggle-link:hover {{ color: var(--polar); }}
        .member-input {{ display: none; margin-top: 14px; }}
        .member-input.visible {{ display: block; }}
        .demo-link {{
            display: block;
            margin-top: 26px;
            font-size: 12px;
            color: var(--ink-3);
            text-decoration: none;
            transition: color .25s;
        }}
        .demo-link:hover {{ color: var(--polar); }}
        .divider {{
            border-top: 1px solid var(--edge);
            margin: 26px 0 0;
        }}
        @media (prefers-reduced-motion: reduce) {{
            .orb, .stars {{ animation: none !important; }}
            .gate {{ animation: none; }}
        }}
    </style>
</head>
<body>
    <div class="aurora">
        <div class="orb orb-polar"></div>
        <div class="orb orb-violet"></div>
    </div>
    <div class="stars"></div>
    <div class="gate">
        <div class="logo">{star_svg}</div>
        <div class="title">POLARIS</div>
        <div class="subtitle">Dealer GEX Terminal</div>

        <form id="authForm" method="POST" action="/login/submit">
            <!-- Primary: LSEG API Key -->
            <div class="input-group" id="lseg-group">
                <div class="input-label">LSEG API Key</div>
                <input type="password" name="lseg_key" id="lseg-input"
                       placeholder="Enter your LSEG API key"
                       autocomplete="off" spellcheck="false">
            </div>

            <!-- Hidden by default: Astraios member code -->
            <div class="member-input" id="member-group">
                <div class="input-label">Astraios Access Code</div>
                <input type="password" name="friend_code" id="member-input"
                       placeholder="Enter member code"
                       autocomplete="off" spellcheck="false">
            </div>

            <button type="submit" class="btn">CONNECT</button>

            {error_block}
        </form>

        <div class="toggle-link" id="toggle-member" onclick="toggleMember()">
            Astraios member? Enter access code
        </div>

        <div class="divider"></div>

        <a class="demo-link" href="{demo_url}" target="_blank" rel="noopener">
            Members: open the cloud terminal &rarr;
        </a>
    </div>

    <script>
        function toggleMember() {{
            const group = document.getElementById('member-group');
            const link = document.getElementById('toggle-member');
            const lsegGroup = document.getElementById('lseg-group');
            if (group.classList.contains('visible')) {{
                group.classList.remove('visible');
                lsegGroup.style.display = 'block';
                link.textContent = 'Astraios member? Enter access code';
            }} else {{
                group.classList.add('visible');
                lsegGroup.style.display = 'none';
                link.textContent = 'Have an LSEG key? Enter API key';
                document.getElementById('member-input').focus();
            }}
        }}
    </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Login throttle
#
# /login/submit used to accept unlimited guesses at the access code. The code is
# 16 chars from a large alphabet, so a blind guess is hopeless — but "the secret
# is long" is not a rate limit, and it stops being true the moment a shorter
# code is set by hand via POLARIS_FRIEND_CODES.
#
# Polaris is ONE long-lived process (the watchdog keeps a single dashboard
# alive), so an in-memory counter is genuinely authoritative here — unlike a
# serverless app, where it would reset on every cold start.
# ---------------------------------------------------------------------------
_MAX_ATTEMPTS = 8           # failures allowed inside the window
_ATTEMPT_WINDOW = 300.0     # 5 minutes
_LOCKOUT = 300.0            # then refuse this IP for 5 minutes

_attempts: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}
_attempts_lock = threading.Lock()


def _client_ip() -> str:
    """Best-effort client identity. Polaris binds 127.0.0.1, so this is nearly
    always localhost; the proxy headers matter only if it is ever fronted."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _throttle_retry_after(ip: str) -> int:
    """Seconds this IP must wait, or 0 if it may try now."""
    now = time.time()
    with _attempts_lock:
        until = _locked_until.get(ip, 0.0)
        if until > now:
            return int(until - now) + 1
        if until:
            # Lock expired — clear the slate so the next window starts clean.
            _locked_until.pop(ip, None)
            _attempts.pop(ip, None)
    return 0


def _record_failure(ip: str) -> None:
    now = time.time()
    with _attempts_lock:
        hits = [t for t in _attempts.get(ip, []) if now - t < _ATTEMPT_WINDOW]
        hits.append(now)
        _attempts[ip] = hits
        if len(hits) >= _MAX_ATTEMPTS:
            _locked_until[ip] = now + _LOCKOUT
        # Keep the dicts from growing without bound on a long-lived process.
        if len(_attempts) > 512:
            for stale in [k for k, v in _attempts.items() if not v or now - v[-1] > _ATTEMPT_WINDOW]:
                _attempts.pop(stale, None)
                _locked_until.pop(stale, None)


def _record_success(ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(ip, None)
        _locked_until.pop(ip, None)


def register_auth(server: Flask) -> None:
    """
    Wire authentication into a Flask server (the one underlying a Dash app).

    Call this AFTER creating the Dash app:
        app = Dash(...)
        register_auth(app.server)
    """
    import importlib
    try:
        app_config = importlib.import_module("config")
    except ImportError:
        from . import config as _  # noqa: F401 — force path resolution
        import config as app_config

    # SESSION_SECRET is always a strong value from config (env or generated); the
    # fallback here is a random per-process key, never a guessable literal.
    server.secret_key = getattr(app_config, "SESSION_SECRET", None) or secrets.token_hex(32)
    friend_codes = [c.lower() for c in getattr(app_config, "FRIEND_CODES", [])]
    allow_byok = bool(getattr(app_config, "ALLOW_BYOK", False))

    # Session cookie hardening. HttpOnly keeps the cookie away from any script on
    # the page; SameSite=Lax means another site's form POST or fetch can't ride
    # the session (Polaris serves the owner's live feed, and the browser will
    # happily attach cookies to a cross-site request without this). Secure is
    # left OFF on purpose: Polaris binds 127.0.0.1 over plain http, and a Secure
    # cookie would simply never be stored. Turn it on if it is ever put behind
    # TLS.
    server.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    @server.before_request
    def _auth_gate():
        path = request.path

        # Allow public paths through without auth
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return None

        # Check session
        if session.get("authenticated"):
            return None

        # Not authenticated — redirect to login
        return redirect("/login")

    @server.route("/login", methods=["GET"])
    def _login_page():
        return Response(_login_html(), mimetype="text/html; charset=utf-8")

    @server.route("/login/submit", methods=["POST"])
    def _login_submit():
        ip = _client_ip()

        # Refuse before comparing anything: a locked-out client shouldn't even
        # get the timing signal of a comparison, let alone another guess.
        wait = _throttle_retry_after(ip)
        if wait:
            minutes = max(1, (wait + 59) // 60)
            return Response(
                _login_html(
                    error=f"Too many attempts. Try again in {minutes} minute"
                    f"{'' if minutes == 1 else 's'}."
                ),
                status=429,
                mimetype="text/html; charset=utf-8",
                headers={"Retry-After": str(wait)},
            )

        lseg_key = (request.form.get("lseg_key") or "").strip()
        code = (request.form.get("friend_code") or "").strip()

        # Try friend code first (if provided). Constant-time compare against each
        # configured code so response timing can't leak the secret char by char.
        if code:
            probe = code.lower()
            if any(hmac.compare_digest(probe, fc) for fc in friend_codes):
                _record_success(ip)
                session["authenticated"] = True
                session["method"] = "friend_code"
                session["ts"] = int(time.time())
                return redirect("/")
            _record_failure(ip)
            return Response(
                _login_html(error="Invalid access code."),
                status=401,
                mimetype="text/html; charset=utf-8",
            )

        # Try LSEG key. Disabled by default: a bare 32-hex string is only a
        # FORMAT check (the served data is the owner's feed regardless), so it
        # granted access to anyone. Re-enable with POLARIS_ALLOW_BYOK=1.
        if lseg_key:
            if allow_byok and _LSEG_KEY_RE.match(lseg_key):
                _record_success(ip)
                session["authenticated"] = True
                session["method"] = "lseg_key"
                session["ts"] = int(time.time())
                return redirect("/")
            _record_failure(ip)
            return Response(
                _login_html(error="Invalid access code."),
                status=401,
                mimetype="text/html; charset=utf-8",
            )

        # Nothing entered
        return Response(
            _login_html(error="Please enter an LSEG API key or Astraios access code."),
            mimetype="text/html; charset=utf-8",
        )

    @server.route("/logout")
    def _logout():
        session.clear()
        return redirect("/login")
