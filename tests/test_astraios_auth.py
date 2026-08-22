"""Astraios sign-in gate — the cloud terminal's door.

Supabase is mocked at the `requests` boundary, so these run offline and assert
the DECISIONS (who gets in, who is refused, what is stored) rather than the
vendor's wire format. The one thing they must never do is create a real account
in the live project to test against.
"""
import base64
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import astraios_auth as aa  # noqa: E402


# --- helpers ---------------------------------------------------------------
def _jwt(aal="aal1"):
    head = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(
        json.dumps({"aal": aal, "sub": "u-1"}).encode()
    ).decode().rstrip("=")
    return f"{head}.{body}.sig"


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("POLARIS_SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("POLARIS_SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("POLARIS_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("POLARIS_COOKIE_INSECURE", "1")  # test client speaks http
    monkeypatch.delenv("VERCEL", raising=False)


@pytest.fixture
def client(env):
    from flask import Flask

    app = Flask(__name__)

    @app.route("/")
    def _home():
        return "TERMINAL"

    aa.register_astraios_auth(app)
    # The throttle counter is module state shared with src.auth; a test that
    # fails 8 times would otherwise lock out the next one.
    from src import auth as base_auth

    base_auth._attempts.clear()
    base_auth._locked_until.clear()
    return app.test_client()


def _wire(monkeypatch, *, grant=None, profile=None, challenge=None, verify=None):
    """Point every Supabase call at canned responses."""
    def fake_post(url, **kw):
        if "token?grant_type=password" in url:
            return grant
        if url.endswith("/challenge"):
            return challenge
        if url.endswith("/verify"):
            return verify
        raise AssertionError(f"unexpected POST {url}")

    def fake_get(url, **kw):
        assert "/rest/v1/profiles" in url
        return profile

    monkeypatch.setattr(aa.requests, "post", fake_post)
    monkeypatch.setattr(aa.requests, "get", fake_get)


def _ok_grant(aal="aal1", factors=None):
    return FakeResp(200, {
        "access_token": _jwt(aal),
        "user": {"id": "u-1", "factors": factors or []},
    })


MEMBER = FakeResp(200, [{"desk_member": True, "desk_access_until": None,
                         "handle": "ayush_panda", "display_name": "Ayush"}])


# --- desk-access rule (the Python twin of lib/deskAccess.ts) ----------------
def test_desk_active_rules():
    assert aa._desk_active({"desk_member": True, "desk_access_until": None})
    assert not aa._desk_active({"desk_member": False, "desk_access_until": None})
    assert not aa._desk_active(None)
    future = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 86400))
    past = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 86400))
    assert aa._desk_active({"desk_member": True, "desk_access_until": future})
    assert not aa._desk_active({"desk_member": True, "desk_access_until": past})
    # Unparseable date fails OPEN, matching the TS and the SQL under it.
    assert aa._desk_active({"desk_member": True, "desk_access_until": "not-a-date"})


def test_jwt_claims_reads_aal():
    assert aa._jwt_claims(_jwt("aal2"))["aal"] == "aal2"
    assert aa._jwt_claims("garbage") == {}


# --- the gate --------------------------------------------------------------
def test_terminal_requires_login(client):
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_dash_data_endpoint_is_gated(client):
    # The layout endpoint is the one that leaks the live board if left open.
    r = client.get("/_dash-layout")
    assert r.status_code == 302


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    body = r.data.decode()
    assert 'name="email"' in body and 'name="password"' in body


def test_bad_password_is_refused(client, monkeypatch):
    _wire(monkeypatch, grant=FakeResp(400, {"error": "invalid_grant"}))
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "wrong"})
    assert r.status_code == 401
    assert "Incorrect email or password" in r.data.decode()


def test_member_signs_in(client, monkeypatch):
    _wire(monkeypatch, grant=_ok_grant(), profile=MEMBER)
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert client.get("/").data == b"TERMINAL"


def test_session_holds_no_supabase_token(client, monkeypatch):
    """The cookie is signed, not encrypted — anything in it is readable."""
    _wire(monkeypatch, grant=_ok_grant(), profile=MEMBER)
    client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    with client.session_transaction() as s:
        assert set(s) == {"authenticated", "method", "uid", "email", "handle", "ts"}
        assert "eyJ" not in json.dumps(dict(s))  # no JWT smuggled in


def test_academy_only_account_is_refused(client, monkeypatch):
    academy = FakeResp(200, [{"desk_member": False, "desk_access_until": None,
                              "handle": "new", "display_name": "New"}])
    _wire(monkeypatch, grant=_ok_grant(), profile=academy)
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 403
    assert "does not have desk access" in r.data.decode()
    assert client.get("/").status_code == 302  # still locked out


def test_lapsed_trial_is_refused(client, monkeypatch):
    past = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 86400))
    lapsed = FakeResp(200, [{"desk_member": True, "desk_access_until": past,
                             "handle": "trial", "display_name": "Trial"}])
    _wire(monkeypatch, grant=_ok_grant(), profile=lapsed)
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 403


def test_two_factor_account_needs_a_code(client, monkeypatch):
    """Password alone must not open the door on an MFA-enrolled account —
    otherwise Polaris is the weak entrance to the shared Astraios login."""
    factors = [{"id": "f-1", "status": "verified", "factor_type": "totp"}]
    _wire(monkeypatch, grant=_ok_grant(factors=factors), profile=MEMBER)
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 401
    assert "two-factor" in r.data.decode()
    assert client.get("/").status_code == 302


def test_two_factor_code_completes_sign_in(client, monkeypatch):
    factors = [{"id": "f-1", "status": "verified", "factor_type": "totp"}]
    _wire(
        monkeypatch,
        grant=_ok_grant(factors=factors),
        profile=MEMBER,
        challenge=FakeResp(200, {"id": "c-1"}),
        verify=FakeResp(200, {"access_token": _jwt("aal2")}),
    )
    r = client.post("/login/submit",
                    data={"email": "a@b.com", "password": "pw", "mfa_code": "123456"})
    assert r.status_code == 302
    assert client.get("/").data == b"TERMINAL"


def test_wrong_two_factor_code_is_refused(client, monkeypatch):
    factors = [{"id": "f-1", "status": "verified", "factor_type": "totp"}]
    _wire(
        monkeypatch,
        grant=_ok_grant(factors=factors),
        profile=MEMBER,
        challenge=FakeResp(200, {"id": "c-1"}),
        verify=FakeResp(422, {"error": "invalid code"}),
    )
    r = client.post("/login/submit",
                    data={"email": "a@b.com", "password": "pw", "mfa_code": "000000"})
    assert r.status_code == 401
    assert client.get("/").status_code == 302


def test_unverified_factor_does_not_demand_a_code(client, monkeypatch):
    """A half-enrolled factor isn't 2FA yet — Meridian only enforces verified
    ones, and demanding a code here would lock the member out of Polaris."""
    factors = [{"id": "f-1", "status": "unverified", "factor_type": "totp"}]
    _wire(monkeypatch, grant=_ok_grant(factors=factors), profile=MEMBER)
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 302


def test_logout_clears_the_session(client, monkeypatch):
    _wire(monkeypatch, grant=_ok_grant(), profile=MEMBER)
    client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    client.get("/logout")
    assert client.get("/").status_code == 302


def test_session_expires(client, monkeypatch):
    _wire(monkeypatch, grant=_ok_grant(), profile=MEMBER)
    client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    with client.session_transaction() as s:
        s["ts"] = int(time.time()) - aa._DEFAULT_MAX_AGE - 60
    assert client.get("/").status_code == 302


def test_missing_config_refuses_instead_of_pretending(monkeypatch):
    monkeypatch.delenv("POLARIS_SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("POLARIS_SUPABASE_ANON_KEY", "anon-key")
    from flask import Flask

    app = Flask(__name__)
    aa.register_astraios_auth(app)
    c = app.test_client()
    assert "not configured" in c.get("/login").data.decode()
    assert c.post("/login/submit", data={"email": "a@b.com", "password": "p"}).status_code == 503


def test_serverless_without_a_session_secret_is_a_config_error(monkeypatch):
    """Random per-lambda keys mean nobody ever stays signed in — say so."""
    monkeypatch.setenv("POLARIS_SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("POLARIS_SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.delenv("POLARIS_SESSION_SECRET", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert "POLARIS_SESSION_SECRET" in aa._config_error()


# --- the profile lookup: fault vs verdict --------------------------------
def test_profile_query_asks_for_columns_that_exist(client, monkeypatch):
    """The live table has `handle`, not `username`. Asking for a column that is
    not there returns PostgREST 42703, which once read as "not a member" and
    locked an admin out of his own terminal."""
    seen = {}

    def fake_get(url, **kw):
        seen["select"] = kw["params"]["select"]
        return MEMBER

    monkeypatch.setattr(aa.requests, "post", lambda url, **kw: _ok_grant())
    monkeypatch.setattr(aa.requests, "get", fake_get)
    client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    cols = set(seen["select"].split(","))
    assert cols == {"desk_member", "desk_access_until", "handle", "display_name"}


def test_broken_profile_lookup_is_not_reported_as_no_access(client, monkeypatch):
    """A 400 from PostgREST is our bug. Telling a real member they have no desk
    access sends them to support instead of sending us to the logs."""
    _wire(monkeypatch, grant=_ok_grant(),
          profile=FakeResp(400, {"code": "42703", "message": "column does not exist"}))
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 503
    body = r.data.decode()
    assert "does not have desk access" not in body
    assert "Try again" in body
    assert client.get("/").status_code == 302  # still not let in


def test_no_profile_row_is_a_verdict(client, monkeypatch):
    """An empty result IS an entitlement answer — that one stays a refusal."""
    _wire(monkeypatch, grant=_ok_grant(), profile=FakeResp(200, []))
    r = client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 403
    assert "does not have desk access" in r.data.decode()


def test_signed_in_handle_comes_from_the_handle_column(client, monkeypatch):
    _wire(monkeypatch, grant=_ok_grant(), profile=MEMBER)
    client.post("/login/submit", data={"email": "a@b.com", "password": "pw"})
    with client.session_transaction() as s:
        assert s["handle"] == "ayush_panda"
