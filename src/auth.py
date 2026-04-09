"""
Flask-level authentication gate for Polaris.

Three tiers:
  1. Astraios members — enter a friend code set in config.FRIEND_CODES.
  2. BYOK users — enter their own LSEG API key (format-validated, not stored).
  3. Demo — link to Vercel synthetic-data deployment (no auth needed).

The gate is a pure Flask layer that sits in front of the Dash app via
@before_request. The login page is self-contained HTML (no Dash/React
dependency). Once authenticated, a signed Flask session cookie grants
access until the browser session ends or the user hits /logout.
"""
from __future__ import annotations

import re
import time

from flask import Flask, Response, redirect, request, session

# Lazy import to avoid circular dependency at module level.
# config is imported inside register_auth() instead.

# Paths that must bypass auth (Dash static assets, login routes, etc.)
_PUBLIC_PREFIXES = (
    "/login",
    "/_dash-",
    "/assets/",
    "/_favicon",
    "/_reload-hash",
)

# LSEG API key format: 32+ hex characters
_LSEG_KEY_RE = re.compile(r"^[a-f0-9]{32,}$", re.IGNORECASE)


def _login_html(error: str = "") -> str:
    """Return the Bloomberg-themed login page as a self-contained HTML string."""
    # Inline the North Star SVG (scaled up for the login page)
    star_svg = """
    <svg width="48" height="48" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="ns_core" cx="50%" cy="50%" r="60%">
          <stop offset="0%"  stop-color="#ffd60a"/>
          <stop offset="35%" stop-color="#ffb627"/>
          <stop offset="80%" stop-color="#fa8c00"/>
          <stop offset="100%" stop-color="#cc6a00"/>
        </radialGradient>
        <filter id="ns_glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="0.8" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <g opacity="0.35" stroke="#fa8c00" stroke-width="0.6" stroke-linecap="round">
        <line x1="5" y1="5" x2="9" y2="9"/>
        <line x1="19" y1="5" x2="15" y2="9"/>
        <line x1="5" y1="19" x2="9" y2="15"/>
        <line x1="19" y1="19" x2="15" y2="15"/>
      </g>
      <path d="M12 0 L13.2 10.2 L24 12 L13.2 13.8 L12 24 L10.8 13.8 L0 12 L10.8 10.2 Z"
            fill="url(#ns_core)" filter="url(#ns_glow)"/>
      <circle cx="12" cy="12" r="0.9" fill="#fff5d0"/>
    </svg>
    """

    error_block = ""
    if error:
        error_block = f"""
        <div style="
            color: #ff4444;
            font-size: 12px;
            margin-top: 12px;
            padding: 8px 12px;
            border: 1px solid #ff4444;
            border-radius: 4px;
            background: rgba(255, 68, 68, 0.08);
        ">{error}</div>
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
    <title>Polaris — Connect</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #000;
            color: #d4d4d4;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Menlo', 'Consolas', monospace;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .gate {{
            width: 380px;
            padding: 48px 36px;
            text-align: center;
        }}
        .logo {{ margin-bottom: 16px; }}
        .title {{
            font-size: 28px;
            font-weight: 700;
            color: #fa8c00;
            letter-spacing: 6px;
            margin-bottom: 4px;
        }}
        .subtitle {{
            font-size: 11px;
            color: #7a7a7a;
            letter-spacing: 2px;
            text-transform: uppercase;
            margin-bottom: 36px;
        }}
        .input-group {{ margin-bottom: 16px; text-align: left; }}
        .input-label {{
            font-size: 9px;
            color: #7a7a7a;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            margin-bottom: 6px;
        }}
        input[type="text"], input[type="password"] {{
            width: 100%;
            padding: 12px 14px;
            background: #0a0a0a;
            border: 1px solid #2a2a2a;
            border-radius: 4px;
            color: #d4d4d4;
            font-family: inherit;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }}
        input:focus {{
            border-color: #fa8c00;
        }}
        input.error {{
            border-color: #ff4444;
        }}
        .btn {{
            width: 100%;
            padding: 12px;
            background: #fa8c00;
            color: #000;
            border: none;
            border-radius: 4px;
            font-family: inherit;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 2px;
            cursor: pointer;
            margin-top: 8px;
            transition: background 0.2s;
        }}
        .btn:hover {{ background: #ffb627; }}
        .toggle-link {{
            font-size: 11px;
            color: #7a7a7a;
            cursor: pointer;
            margin-top: 16px;
            display: inline-block;
        }}
        .toggle-link:hover {{ color: #fa8c00; }}
        .member-input {{ display: none; margin-top: 16px; }}
        .member-input.visible {{ display: block; }}
        .demo-link {{
            display: block;
            margin-top: 32px;
            font-size: 11px;
            color: #555;
            text-decoration: none;
        }}
        .demo-link:hover {{ color: #fa8c00; }}
        .divider {{
            border-top: 1px solid #1a1a1a;
            margin: 24px 0 20px;
        }}
    </style>
</head>
<body>
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
            No LSEG key? Try the demo &rarr;
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

    server.secret_key = getattr(app_config, "SESSION_SECRET", "polaris-fallback-key")
    friend_codes = [c.lower() for c in getattr(app_config, "FRIEND_CODES", [])]

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
        lseg_key = (request.form.get("lseg_key") or "").strip()
        code = (request.form.get("friend_code") or "").strip()

        # Try friend code first (if provided)
        if code:
            if code.lower() in friend_codes:
                session["authenticated"] = True
                session["method"] = "friend_code"
                session["ts"] = int(time.time())
                return redirect("/")
            return Response(
                _login_html(error="Invalid access code."),
                mimetype="text/html; charset=utf-8",
            )

        # Try LSEG key
        if lseg_key:
            if _LSEG_KEY_RE.match(lseg_key):
                session["authenticated"] = True
                session["method"] = "lseg_key"
                session["ts"] = int(time.time())
                return redirect("/")
            return Response(
                _login_html(error="Invalid LSEG API key format. Expected 32+ hex characters."),
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
