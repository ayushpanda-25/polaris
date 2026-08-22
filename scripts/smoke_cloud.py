"""Smoke test for the Vercel cloud entry — forces the VENDORED CBOE path
(POLARIS_PROMETHEUS_DIR=/nonexistent) so it exercises exactly what runs on
Vercel, where the local prometheus repo is absent."""
import os, sys, time
from pathlib import Path

# Force the vendored (no-prometheus-repo) fetch, like Vercel.
os.environ["POLARIS_PROMETHEUS_DIR"] = "/nonexistent-force-vendored"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# NOTE: the real prometheus repo is importable on this Mac (another sys.path
# route), so _import_prometheus_cboe() returns the REAL provider here. On Vercel
# there is no repo, so the try/except ImportError falls to the vendored shim.
# We therefore test the VENDORED functions DIRECTLY (that IS the Vercel path),
# then run the full pipeline with the vendored provider forced in.
print("1) vendored CBOE fetch + parse (the Vercel path, tested directly) …", flush=True)
from src.data_feed import _VendoredCboe, _vendored_parse_occ, PrometheusBackupFeed
parse_occ = _vendored_parse_occ
raw = _VendoredCboe.fetch_raw("SPY")
n_opts = len(((raw.get("data") or {}).get("options")) or [])
spot = (raw.get("data") or {}).get("current_price")
print(f"   OK  SPY spot={spot}  options={n_opts}  parse('SPY260919C00500000')={parse_occ('SPY260919C00500000')}")
assert n_opts > 100 and spot, "CBOE payload looks empty"
# index underscore routing (_SPX.json / _VIX.json)
assert _VendoredCboe._url("SPX").endswith("/_SPX.json"), _VendoredCboe._url("SPX")
assert _VendoredCboe._url("VIX").endswith("/_VIX.json"), _VendoredCboe._url("VIX")
assert _VendoredCboe._url("NVDA").endswith("/NVDA.json"), _VendoredCboe._url("NVDA")
print("   OK  index underscore routing correct (_SPX / _VIX / NVDA)")

print("2) PrometheusBackupFeed snapshot with VENDORED provider forced …", flush=True)
feed = PrometheusBackupFeed()
feed._cboe = _VendoredCboe            # force exactly what Vercel uses
feed._parse_occ = _vendored_parse_occ
for tk in ("SPY", "SPX", "VIX", "NVDA"):
    t0 = time.time()
    snap = feed.get_chain_snapshot(tk)
    print(f"   OK  {tk:4s} spot={snap.spot:>9.2f}  contracts={len(snap.contracts):>4d}  "
          f"source={snap.source}  {time.time()-t0:.2f}s")
    assert snap.contracts and snap.source == "cboe"

print("3) LazyCBOECache compute-on-read …", flush=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "polaris_api_index", str(Path(__file__).resolve().parents[1] / "api" / "index.py"))
mod = importlib.util.module_from_spec(spec)
t0 = time.time()
spec.loader.exec_module(mod)          # builds create_app + pre-warms SPY
print(f"   OK  module import + SPY pre-warm in {time.time()-t0:.2f}s")
g = mod._cache.get_grid("SPY")
assert g is not None, "SPY grid missing after pre-warm"
print(f"   OK  SPY grid strikes={len(g.strikes)}  expiries={len(g.expiries)}  src={mod._cache.get_source('SPY')}")

print("4) WSGI request to '/' (Flask test client) …", flush=True)
client = mod.server.test_client()
t0 = time.time()
r = client.get("/")
body = r.get_data(as_text=True)
print(f"   status={r.status_code}  bytes={len(body)}  {time.time()-t0:.2f}s")
assert r.status_code == 200, f"expected 200, got {r.status_code}"
assert "Polaris" in body or "polaris" in body, "no Polaris marker in HTML"
# Dash serves a bootstrap shell; confirm the react entry + our assets link exist.
assert "react" in body.lower() or "_dash" in body, "no Dash bootstrap in HTML"
print("   OK  Dash shell served, contains Polaris marker")

print("5) /learn Academy route (should 302 → Nexus academy) …", flush=True)
r2 = client.get("/learn")
loc = r2.headers.get("Location", "")
print(f"   /learn status={r2.status_code}  →  {loc}")
assert r2.status_code == 302 and "astraiosalgo.com/academy" in loc, "learn redirect wrong"

print("\nALL SMOKE CHECKS PASSED ✅")
