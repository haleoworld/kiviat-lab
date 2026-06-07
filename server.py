#!/usr/bin/env python3
"""Kiviat Lab web server (Phase 3).

Serves a phone-friendly dashboard + a small JSON API over the computed views,
behind a passcode login page (session cookie).

    python server.py            # http://localhost:8000
    python server.py --host 0.0.0.0 --port 8000   # reachable over Tailscale

Set the passcode in the data-root .env as KIVIAT_PASSCODE (or KIVIAT_PASSWORD).
The server refuses to bind beyond localhost unless a passcode is set.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import config
import paths
import views

# Load the passcode from the data-root .env.
if paths.ENV_FILE.exists():
    load_dotenv(paths.ENV_FILE)
PASSCODE = os.environ.get("KIVIAT_PASSCODE") or os.environ.get("KIVIAT_PASSWORD")

# When served under a subpath by a stripping proxy (e.g. Tailscale serve --set-path
# /kaviat-lab), the app RECEIVES paths at root but must EMIT links/redirects with the
# prefix. BASE is that outgoing prefix (no trailing slash); "" means served at root.
BASE = os.environ.get("KIVIAT_BASE_PATH", "").rstrip("/")

COOKIE = "kiviat_session"
MONTH = 60 * 60 * 24 * 30

app = FastAPI(title="Kiviat Lab")
WEB_DIR = paths.CODE_ROOT / "web"


def serve_html(filename: str) -> HTMLResponse:
    """Serve an HTML file with a <base href> so its relative URLs resolve under BASE."""
    html = (WEB_DIR / filename).read_text()
    html = html.replace("<head>", f'<head>\n  <base href="{BASE}/">', 1)
    return HTMLResponse(html)


def _session_token() -> str:
    """Stateless cookie value: HMAC keyed by the passcode. Survives restarts;
    invalidated automatically if the passcode changes."""
    return hmac.new(PASSCODE.encode(), b"kiviat-session-v1", hashlib.sha256).hexdigest()


def is_authed(request: Request) -> bool:
    if not PASSCODE:
        return True  # no passcode configured → open (localhost dev only)
    cookie = request.cookies.get(COOKIE, "")
    return bool(cookie) and hmac.compare_digest(cookie, _session_token())


def require_api_auth(request: Request):
    if not is_authed(request):
        raise HTTPException(401, "login required")


# ---------- auth pages ----------

@app.get("/login")
def login_page():
    return serve_html("login.html")


@app.post("/login")
def login(passcode: str = Form(...)):
    if PASSCODE and secrets.compare_digest(passcode, PASSCODE):
        resp = RedirectResponse(f"{BASE}/", status_code=303)
        resp.set_cookie(COOKIE, _session_token(), httponly=True, samesite="lax",
                        max_age=MONTH, path=f"{BASE}/")
        return resp
    return RedirectResponse(f"{BASE}/login?error=1", status_code=303)


@app.get("/logout")
def logout():
    resp = RedirectResponse(f"{BASE}/login", status_code=303)
    resp.delete_cookie(COOKIE, path=f"{BASE}/")
    return resp


# ---------- app ----------

@app.get("/")
def index(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("index.html")


@app.get("/api/families")
def api_families(request: Request, _: None = Depends(require_api_auth)):
    fams = []
    active = config.load_app_config().get("active_family")
    for fid in paths.list_families():
        fcfg = config.load_family_config(fid)
        fams.append({"id": fid, "name": fcfg.get("name") or fid, "active": fid == active})
    return {"families": fams, "active": active}


@app.get("/api/dashboard")
def api_dashboard(request: Request, family: Optional[str] = None,
                  _: None = Depends(require_api_auth)):
    fid = config.resolve_family(family)
    if not fid:
        raise HTTPException(404, "no family specified and no active family set")
    if not paths.family_exists(fid):
        raise HTTPException(404, f"no such family: {fid}")
    return JSONResponse(views.dashboard(fid))


def main():
    ap = argparse.ArgumentParser(description="Run the Kiviat Lab web server.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = this machine only; 0.0.0.0 = reachable over Tailscale")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    # Refuse to expose beyond localhost without a passcode set.
    if args.host != "127.0.0.1" and not PASSCODE:
        raise SystemExit(
            "Refusing to bind to a non-local address with no passcode.\n"
            f"  Set KIVIAT_PASSCODE in {paths.ENV_FILE} first, then re-run.\n"
            "  (This prevents anyone on your tailnet from reading your finances.)"
        )
    print("🔒 login required" if PASSCODE else "⚠ no passcode set — open (localhost only)")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
