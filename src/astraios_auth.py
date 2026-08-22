"""
Astraios account sign-in for the PUBLIC cloud Polaris terminal.

The Mac instance is gated by a shared access code (src/auth.py). That is the
wrong door for polaris.astraiosalgo.com: it is a real outbound website, one code
has to be handed around by hand, and a single leak opens the whole terminal with
no way to revoke one person.

So the cloud terminal signs people in with their ASTRAIOS ACCOUNT — the same
email and password they use on Meridian, checked against the same Supabase
project. That buys:

  * no second credential to distribute, rotate, or forget,
  * revoking someone in Meridian revokes Polaris the moment they sign in again,
  * an Academy-only signup (desk_member = false, schema_v39) or a LAPSED trial
    (desk_access_until in the past, schema_v50) is refused here for exactly the
    reason Meridian's own session gate refuses them,
  * two-factor is enforced when the account has it. Meridian requires aal2 in
    middleware; a Polaris that accepted a bare password would quietly become the
    weak door onto the same shared account, which is the whole failure mode the
    2026-08 auth hardening existed to close.

Nothing LSEG-derived sits behind this gate — the cloud feed is CBOE-only and
stays that way. The gate is here because the TERMINAL is the member perk.

No Supabase token is ever persisted. The password (and 2FA code) are exchanged
for a token inside ONE request, used to read the caller's own profile row, and
dropped. The signed Flask cookie carries identity only: user id, email, handle.
That is deliberate — Flask sessions are signed but READABLE, so a bearer token
in the cookie would be a bearer token sitting in the browser in plain sight.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone

import requests
from flask import Flask, Response, redirect, request, session

from .auth import (
    _PUBLIC_PREFIXES,
    STAR_SVG,
    _client_ip,
    _record_failure,
    _record_success,
    _throttle_retry_after,
)

_TIMEOUT = 12  # seconds — Supabase auth is fast; a hung call must not hang a lambda
_DEFAULT_MAX_AGE = 43_200  # 12h, then re-authenticate
MERIDIAN_URL = os.environ.get("MERIDIAN_URL", "https://astraios-meridian.vercel.app")


# ---------------------------------------------------------------------------
# Supabase plumbing
# ---------------------------------------------------------------------------
def _sb_config() -> tuple[str, str]:
    """(project URL, anon key). The anon key is the PUBLIC one — it is already
    shipped in Meridian's browser bundle; RLS is what protects the data."""
    url = (
        os.environ.get("POLARIS_SUPABASE_URL")
        or os.environ.get("SUPABASE_URL")
        or ""
    ).strip().rstrip("/")
    key = (
        os.environ.get("POLARIS_SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or ""
    ).strip()
    return url, key


def _config_error() -> str:
    """Why sign-in cannot work at all, or '' when it can.

    Checked on every render so a missing env var says so in plain language
    instead of presenting a form that can never succeed.
    """
    url, key = _sb_config()
    if not url or not key:
        return (
            "Sign-in is not configured on this deployment "
            "(POLARIS_SUPABASE_URL / POLARIS_SUPABASE_ANON_KEY)."
        )
    # On serverless there is no single long-lived process: without an explicit
    # signing key every lambda invents its own, so a cookie minted by one
    # instance is gibberish to the next and nobody ever stays signed in.
    if os.environ.get("VERCEL") and not os.environ.get("POLARIS_SESSION_SECRET", "").strip():
        return "Sign-in is not configured on this deployment (POLARIS_SESSION_SECRET)."
    return ""


def _jwt_claims(token: str) -> dict:
    """Read a JWT payload WITHOUT verifying the signature.

    Sound here precisely because it is not a client-supplied token: it came back
    from Supabase over TLS inside this same request, so we are parsing our own
    fresh response. Used only to read `aal`.
    """
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _password_grant(url: str, key: str, email: str, password: str) -> tuple[dict | None, str]:
    try:
        r = requests.post(
            f"{url}/auth/v1/token?grant_type=password",
            json={"email": email, "password": password},
            headers={"apikey": key, "Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None, "Could not reach the Astraios sign-in service. Try again."
    if r.status_code == 200:
        return r.json(), ""
    if r.status_code == 429:
        return None, "Too many sign-in attempts. Wait a minute and try again."
    # Everything else (400 invalid credentials, 401, 403 unconfirmed email) gets
    # ONE message on purpose: distinguishing "no such account" from "wrong
    # password" tells an attacker which emails are real.
    return None, "Incorrect email or password."


def _verified_factor(user: dict) -> dict | None:
    for f in user.get("factors") or []:
        if f.get("status") == "verified":
            return f
    return None


def _mfa_verify(url: str, key: str, token: str, factor_id: str, code: str) -> tuple[str, str]:
    """Run challenge → verify. Returns (aal2 access token, error)."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        ch = requests.post(
            f"{url}/auth/v1/factors/{factor_id}/challenge",
            json={},
            headers=headers,
            timeout=_TIMEOUT,
        )
        if ch.status_code != 200:
            return "", "Could not start two-factor verification. Try again."
        challenge_id = ch.json().get("id")
        vr = requests.post(
            f"{url}/auth/v1/factors/{factor_id}/verify",
            json={"challenge_id": challenge_id, "code": code},
            headers=headers,
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return "", "Could not reach the Astraios sign-in service. Try again."
    if vr.status_code != 200:
        return "", "That two-factor code was not accepted."
    return vr.json().get("access_token", ""), ""


def _parse_ts(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _desk_active(profile: dict | None) -> bool:
    """The Python twin of lib/deskAccess.ts `deskAccessActive` — and of the SQL
    `public.is_desk_member()` under it.

    Same deliberate fail-OPEN on an unparseable date as the TypeScript: the
    database is the enforcer either way, so the costly mistake is locking out a
    real member, not showing the shell to someone RLS already refuses.
    """
    if not profile or not profile.get("desk_member"):
        return False
    until = profile.get("desk_access_until")
    if not until:
        return True  # null = permanent
    ts = _parse_ts(str(until))
    return True if ts is None else ts > time.time()


def _fetch_profile(url: str, key: str, token: str, uid: str) -> tuple[dict | None, str]:
    """Read the caller's OWN profile row with the caller's own token, so RLS
    applies exactly as it does in Meridian. No service key lives here.

    Returns (row_or_None, error). The split matters: "the query broke" and "you
    are not a member" are completely different answers, and collapsing them is
    how a typo in a column name spends an afternoon impersonating an entitlement
    refusal. Only an empty result is an entitlement answer; anything else is
    reported as a fault, with the reason, so it can be read and fixed.
    """
    try:
        r = requests.get(
            f"{url}/rest/v1/profiles",
            params={
                # NB: the column is `handle`. There is no profiles.username —
                # asking for one returns PostgREST 42703, not an empty row.
                "id": f"eq.{uid}",
                "select": "desk_member,desk_access_until,handle,display_name",
            },
            headers={"apikey": key, "Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None, "Could not reach the Astraios membership service. Try again."
    if r.status_code != 200:
        # Surfaced to the operator in the logs; the member just sees "try again".
        print(f"[polaris-auth] profile lookup failed: {r.status_code} {r.text[:200]}",
              file=sys.stderr)
        return None, "Could not verify your membership just now. Try again."
    rows = r.json()
    if isinstance(rows, list) and rows:
        return rows[0], ""
    return None, ""  # genuinely no row for this account = not a desk member


# ---------------------------------------------------------------------------
# The sign-in page
#
# Self-contained HTML on purpose: it has to render before any Dash asset loads,
# and it must not depend on the React bundle it is standing in front of. Same
# liquid-glass language as the Mac gate, different door.
# ---------------------------------------------------------------------------
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#04060d">
    <meta name="robots" content="noindex">
    <title>Polaris &mdash; Sign in</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg: #04060d;
            --ink: rgba(255,255,255,0.92);
            --ink-3: rgba(255,255,255,0.34);
            --glass: rgba(255,255,255,0.055);
            --edge: rgba(255,255,255,0.10);
            --edge-bright: rgba(255,255,255,0.18);
            --polar: #6ee7ff;
            --font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", sans-serif;
            --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
        }
        body {
            background: var(--bg); color: var(--ink); font-family: var(--font);
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh; padding: 24px 0;
            -webkit-font-smoothing: antialiased;
        }
        .aurora { position: fixed; inset: 0; z-index: -2; overflow: hidden; }
        .orb { position: absolute; border-radius: 50%; filter: blur(95px); opacity: 0.5; }
        .orb-polar { width: 58vw; height: 58vw; left: -16vw; top: -22vw;
            background: radial-gradient(circle at 38% 38%, #38b6e04d, #0e4a6a33 48%, transparent 70%);
            animation: drift-a 80s ease-in-out infinite alternate; }
        .orb-violet { width: 48vw; height: 48vw; right: -14vw; bottom: -16vw;
            background: radial-gradient(circle at 60% 40%, #8b7cff40, #4636a322 52%, transparent 72%);
            animation: drift-b 95s ease-in-out infinite alternate; }
        @keyframes drift-a { to { transform: translate(8vw, 6vh) scale(1.10); } }
        @keyframes drift-b { to { transform: translate(-6vw, -8vh) scale(0.93); } }
        .stars { position: fixed; inset: -60px; z-index: -1; pointer-events: none;
            background-image:
                radial-gradient(1px 1px at 22px 34px, rgba(255,255,255,0.7), transparent),
                radial-gradient(1px 1px at 168px 92px, rgba(255,255,255,0.4), transparent),
                radial-gradient(1.4px 1.4px at 308px 142px, rgba(160,230,255,0.6), transparent),
                radial-gradient(1px 1px at 424px 58px, rgba(255,255,255,0.32), transparent),
                radial-gradient(1.3px 1.3px at 92px 212px, rgba(255,255,255,0.45), transparent),
                radial-gradient(1px 1px at 244px 262px, rgba(190,210,255,0.4), transparent);
            background-size: 470px 310px; background-repeat: repeat;
            opacity: 0.5; animation: twinkle 8s ease-in-out infinite alternate; }
        @keyframes twinkle { to { opacity: 0.2; } }
        .gate {
            width: min(420px, calc(100vw - 32px));
            padding: 44px 40px 34px; text-align: center; border-radius: 28px;
            background: var(--glass);
            -webkit-backdrop-filter: blur(24px) saturate(180%);
            backdrop-filter: blur(24px) saturate(180%);
            border: 1px solid var(--edge);
            box-shadow: inset 0 1px 0 var(--edge-bright),
                        inset 0 -1px 0 rgba(255,255,255,0.04),
                        0 24px 60px rgba(0,0,0,0.45);
            animation: rise .9s cubic-bezier(.2,.7,.2,1) both;
        }
        @keyframes rise { from { opacity: 0; transform: translateY(26px); filter: blur(8px); } }
        @supports not ((backdrop-filter: blur(2px)) or (-webkit-backdrop-filter: blur(2px))) {
            .gate { background: rgba(18, 22, 34, 0.93); }
        }
        .logo { margin-bottom: 14px; filter: drop-shadow(0 0 16px rgba(110,231,255,0.45)); }
        .title {
            font-size: 24px; font-weight: 700; letter-spacing: 0.28em; margin-bottom: 6px;
            background: linear-gradient(92deg, #ffffff 25%, var(--polar) 80%);
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .subtitle {
            font-size: 11px; color: var(--ink-3); letter-spacing: 0.22em;
            text-transform: uppercase; margin-bottom: 30px;
        }
        .input-group { margin-bottom: 13px; text-align: left; }
        .input-label {
            font-size: 10px; color: var(--ink-3); letter-spacing: 0.14em;
            text-transform: uppercase; font-weight: 600; margin-bottom: 7px; padding-left: 6px;
        }
        input {
            width: 100%; padding: 13px 18px; background: rgba(255,255,255,0.06);
            border: 1px solid var(--edge); border-radius: 999px; color: var(--ink);
            font-family: var(--mono); font-size: 14px; outline: none;
            transition: border-color .25s, background .25s, box-shadow .25s;
        }
        input::placeholder { color: var(--ink-3); font-family: var(--font); }
        input:focus {
            border-color: rgba(110,231,255,0.5); background: rgba(255,255,255,0.08);
            box-shadow: 0 0 0 4px rgba(110,231,255,0.08);
        }
        .btn {
            width: 100%; padding: 13px; color: #0b0c12;
            background: linear-gradient(180deg, #ffffff, #d9dde6);
            border: none; border-radius: 999px; font-family: var(--font);
            font-size: 13.5px; font-weight: 700; letter-spacing: 0.12em;
            cursor: pointer; margin-top: 10px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.4), inset 0 1px 0 #fff;
            transition: transform .25s, box-shadow .25s;
        }
        .btn:hover { transform: translateY(-2px);
            box-shadow: 0 8px 26px rgba(110,231,255,0.22), inset 0 1px 0 #fff; }
        .btn[disabled] { opacity: .45; cursor: not-allowed; transform: none; }
        .error-box {
            color: #ffb4ae; font-size: 12.5px; margin-top: 14px; padding: 10px 16px;
            border: 1px solid rgba(255,69,58,0.30); border-radius: 14px;
            background: rgba(255,69,58,0.10); text-align: left;
        }
        .hint {
            font-size: 11px; color: var(--ink-3); margin-top: 7px;
            padding-left: 6px; text-align: left; line-height: 1.5;
        }
        .divider { border-top: 1px solid var(--edge); margin: 24px 0 0; }
        .foot {
            display: block; margin-top: 20px; font-size: 12px; color: var(--ink-3);
            text-decoration: none; transition: color .25s; line-height: 1.6;
        }
        .foot:hover { color: var(--polar); }
        @media (prefers-reduced-motion: reduce) {
            .orb, .stars { animation: none !important; }
            .gate { animation: none; }
        }
    </style>
</head>
<body>
    <div class="aurora">
        <div class="orb orb-polar"></div>
        <div class="orb orb-violet"></div>
    </div>
    <div class="stars"></div>
    <div class="gate">
        <div class="logo">__STAR__</div>
        <div class="title">POLARIS</div>
        <div class="subtitle">Astraios Members</div>

        <form method="POST" action="/login/submit" autocomplete="on">
            <div class="input-group">
                <div class="input-label">Email</div>
                <input type="email" name="email" value="__EMAIL__" required
                       placeholder="you@example.com" autocomplete="username"
                       spellcheck="false" autocapitalize="none" autofocus>
            </div>
            <div class="input-group">
                <div class="input-label">Password</div>
                <input type="password" name="password" required
                       placeholder="Your Astraios password"
                       autocomplete="current-password">
            </div>
            <div class="input-group">
                <div class="input-label">Two-factor code</div>
                <input type="text" name="mfa_code" inputmode="numeric"
                       pattern="[0-9]*" maxlength="6" placeholder="000000"
                       autocomplete="one-time-code" spellcheck="false">
                <div class="hint">__MFA_HINT__</div>
            </div>

            <button type="submit" class="btn" __DISABLED__>SIGN IN</button>
            __ERROR__
        </form>

        <div class="divider"></div>
        <a class="foot" href="__MERIDIAN__" target="_blank" rel="noopener">
            Same account as Meridian. Not a member yet? Request access &rarr;
        </a>
    </div>
</body>
</html>"""


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _page(error: str = "", email: str = "", show_mfa: bool = False,
          disabled: bool = False) -> str:
    err_html = f'<div class="error-box">{_esc(error)}</div>' if error else ""
    hint = (
        "From your authenticator app."
        if show_mfa
        else "Only if you have two-factor turned on."
    )
    return (
        _PAGE.replace("__STAR__", STAR_SVG)
        .replace("__EMAIL__", _esc(email))
        .replace("__ERROR__", err_html)
        .replace("__MFA_HINT__", hint)
        .replace("__DISABLED__", "disabled" if disabled else "")
        .replace("__MERIDIAN__", _esc(MERIDIAN_URL))
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def register_astraios_auth(server: Flask) -> None:
    """Gate a Flask server (the one under a Dash app) on Astraios accounts.

    Call AFTER creating the Dash app and BEFORE register_learn_route, so /login
    wins over any later catch-all:

        app = Dash(...)
        register_astraios_auth(app.server)
    """
    secret = os.environ.get("POLARIS_SESSION_SECRET", "").strip()
    # A per-process fallback keeps LOCAL runs working; on Vercel the missing env
    # var is reported by _config_error() instead of silently logging people out.
    server.secret_key = secret or secrets.token_hex(32)

    max_age = int(os.environ.get("POLARIS_SESSION_MAX_AGE", _DEFAULT_MAX_AGE))
    # Secure by default: this deployment is https-only. The escape hatch exists
    # for a local http smoke test, where a Secure cookie is simply never stored.
    secure_cookie = os.environ.get("POLARIS_COOKIE_INSECURE", "").strip() not in ("1", "true", "yes")
    server.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure_cookie,
    )

    @server.before_request
    def _gate():
        path = request.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return None
        if session.get("authenticated"):
            # Polaris holds no refresh token, so a session cannot silently live
            # forever the way a Meridian tab does — it ages out and re-auths.
            ts = session.get("ts", 0)
            if max_age <= 0 or (time.time() - ts) < max_age:
                return None
            session.clear()
        return redirect("/login")

    @server.route("/login", methods=["GET"])
    def _login_page():
        cfg = _config_error()
        return Response(
            _page(error=cfg, disabled=bool(cfg)),
            status=200,
            mimetype="text/html; charset=utf-8",
        )

    @server.route("/login/submit", methods=["POST"])
    def _login_submit():
        cfg = _config_error()
        if cfg:
            return Response(_page(error=cfg, disabled=True), status=503,
                            mimetype="text/html; charset=utf-8")

        ip = _client_ip()
        # Best-effort only on serverless: the counter lives in one warm lambda,
        # so it slows a burst rather than bounding one absolutely. Supabase's own
        # per-IP auth rate limit is the real backstop, which is why a 429 from it
        # is surfaced verbatim rather than swallowed.
        wait = _throttle_retry_after(ip)
        if wait:
            minutes = max(1, (wait + 59) // 60)
            return Response(
                _page(error=f"Too many attempts. Try again in {minutes} minute"
                            f"{'' if minutes == 1 else 's'}."),
                status=429,
                mimetype="text/html; charset=utf-8",
                headers={"Retry-After": str(wait)},
            )

        url, key = _sb_config()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        code = (request.form.get("mfa_code") or "").strip()

        if not email or not password:
            return Response(_page(error="Enter your email and password.", email=email),
                            status=400, mimetype="text/html; charset=utf-8")

        data, err = _password_grant(url, key, email, password)
        if err:
            _record_failure(ip)
            return Response(_page(error=err, email=email), status=401,
                            mimetype="text/html; charset=utf-8")

        token = data.get("access_token", "")
        user = data.get("user") or {}
        uid = user.get("id")
        if not token or not uid:
            _record_failure(ip)
            return Response(_page(error="Sign-in failed. Try again.", email=email),
                            status=502, mimetype="text/html; charset=utf-8")

        # Two-factor, when the account has it. The password alone got us an aal1
        # token; anything gated must wait for aal2, exactly as Meridian's
        # middleware waits. Note the code travels in the SAME request as the
        # password — no half-authenticated token is ever parked in a cookie
        # between two page loads.
        factor = _verified_factor(user)
        if factor and _jwt_claims(token).get("aal") != "aal2":
            if not code:
                _record_failure(ip)
                return Response(
                    _page(error="This account uses two-factor. Re-enter your "
                                "password and add the 6-digit code.",
                          email=email, show_mfa=True),
                    status=401, mimetype="text/html; charset=utf-8")
            token2, mfa_err = _mfa_verify(url, key, token, factor["id"], code)
            if mfa_err:
                _record_failure(ip)
                return Response(_page(error=mfa_err, email=email, show_mfa=True),
                                status=401, mimetype="text/html; charset=utf-8")
            token = token2

        # Entitlement. Authentication says who they are; this says whether the
        # desk is theirs. Academy-only accounts and lapsed trials stop here.
        profile, lookup_err = _fetch_profile(url, key, token, uid)
        if lookup_err:
            # A broken lookup is OUR fault, not a verdict on the member — say so
            # rather than telling a paying member they are not one.
            return Response(_page(error=lookup_err, email=email), status=503,
                            mimetype="text/html; charset=utf-8")
        if not _desk_active(profile):
            _record_failure(ip)
            return Response(
                _page(error="This Astraios account does not have desk access. "
                            "Polaris is part of the Meridian desk — open Meridian "
                            "to check your membership.",
                      email=email),
                status=403, mimetype="text/html; charset=utf-8")

        _record_success(ip)
        session.clear()
        session["authenticated"] = True
        session["method"] = "astraios"
        session["uid"] = uid
        session["email"] = email
        session["handle"] = (profile or {}).get("handle") or ""
        session["ts"] = int(time.time())
        return redirect("/")

    @server.route("/logout")
    def _logout():
        session.clear()
        return redirect("/login")
