#!/usr/bin/env python3
"""Kiviat Lab web server (Phase 3, first pass).

Serves a phone-friendly dashboard + a small JSON API over the computed views.

    python server.py            # http://localhost:8000
    python server.py --host 0.0.0.0 --port 8000   # reachable over Tailscale/LAN

Read-only for now: it renders the family's snapshot-derived metrics. Upload /
review / commit come later.
"""
from __future__ import annotations

import argparse
import os
import secrets
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import config
import paths
import views

# Load secrets from the data-root .env (KIVIAT_PASSWORD gates remote access).
if paths.ENV_FILE.exists():
    load_dotenv(paths.ENV_FILE)
PASSWORD = os.environ.get("KIVIAT_PASSWORD")
USERNAME = os.environ.get("KIVIAT_USER", "family")

_security = HTTPBasic(auto_error=False)


def require_auth(creds: Optional[HTTPBasicCredentials] = Depends(_security)):
    """Password gate. If KIVIAT_PASSWORD is unset, the app is open (localhost dev only)."""
    if not PASSWORD:
        return
    ok = creds and secrets.compare_digest(creds.username, USERNAME) \
        and secrets.compare_digest(creds.password, PASSWORD)
    if not ok:
        raise HTTPException(401, "Authentication required",
                            headers={"WWW-Authenticate": "Basic"})


app = FastAPI(title="Kiviat Lab", dependencies=[Depends(require_auth)])

WEB_DIR = paths.CODE_ROOT / "web"


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/families")
def api_families():
    fams = []
    active = config.load_app_config().get("active_family")
    for fid in paths.list_families():
        fcfg = config.load_family_config(fid)
        fams.append({"id": fid, "name": fcfg.get("name") or fid, "active": fid == active})
    return {"families": fams, "active": active}


@app.get("/api/dashboard")
def api_dashboard(family: Optional[str] = None):
    fid = config.resolve_family(family)
    if not fid:
        raise HTTPException(404, "no family specified and no active family set")
    if not paths.family_exists(fid):
        raise HTTPException(404, f"no such family: {fid}")
    return JSONResponse(views.dashboard(fid))


def main():
    ap = argparse.ArgumentParser(description="Run the Kiviat Lab web server.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = this machine only; 0.0.0.0 = reachable over Tailscale/LAN")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    # Refuse to expose beyond localhost without a password set.
    if args.host != "127.0.0.1" and not PASSWORD:
        raise SystemExit(
            "Refusing to bind to a non-local address with no password.\n"
            f"  Set KIVIAT_PASSWORD in {paths.ENV_FILE} first, then re-run.\n"
            "  (This prevents anyone on the network from reading your finances.)"
        )
    if PASSWORD:
        print(f"🔒 auth ON (user: {USERNAME})")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
