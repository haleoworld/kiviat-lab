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
import datetime
import hashlib
import hmac
import os
import secrets
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import business
import config
import inbox
import matching
import paths
import statements
import views

# Load the passcode from the data-root .env.
if paths.ENV_FILE.exists():
    load_dotenv(paths.ENV_FILE)
PASSCODE = os.environ.get("KIVIAT_PASSCODE") or os.environ.get("KIVIAT_PASSWORD")
# Bearer token for the iOS Shortcut (Share-Sheet inbox) — auth without the browser session.
API_TOKEN = os.environ.get("KIVIAT_API_TOKEN")

# When served under a subpath by a stripping proxy (e.g. Tailscale serve --set-path
# /kaviat-lab), the app RECEIVES paths at root but must EMIT links/redirects with the
# prefix. BASE is that outgoing prefix (no trailing slash); "" means served at root.
BASE = os.environ.get("KIVIAT_BASE_PATH", "").rstrip("/")

COOKIE = "kiviat_session"
MONTH = 60 * 60 * 24 * 30

app = FastAPI(title="Kiviat Lab")
WEB_DIR = paths.CODE_ROOT / "web"


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """Never let the browser serve a stale page/script/API response. The app's
    JS is inline in the HTML, so disabling HTML caching also refreshes the code.
    (External CDN assets load directly from jsdelivr, unaffected by this.)"""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def serve_html(filename: str, nav: bool = True) -> HTMLResponse:
    """Serve an HTML file with a <base href> so its relative URLs resolve under BASE.
    Unless nav=False, inject the shared left navigation drawer (web/_nav.html)."""
    html = (WEB_DIR / filename).read_text()
    html = html.replace("<head>", f'<head>\n  <base href="{BASE}/">', 1)
    if nav:
        partial = WEB_DIR / "_nav.html"
        if partial.exists():
            html = html.replace("</body>", partial.read_text() + "\n</body>", 1)
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


def require_token_or_session(request: Request):
    """Inbox auth: the browser session cookie OR the Shortcut's X-Kiviat-Token header."""
    if is_authed(request):
        return
    tok = request.headers.get("X-Kiviat-Token", "")
    if API_TOKEN and tok and hmac.compare_digest(tok, API_TOKEN):
        return
    raise HTTPException(401, "auth required")


# ---------- auth pages ----------

@app.get("/login")
def login_page():
    return serve_html("login.html", nav=False)


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


@app.get("/retirement")
def retirement_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("retirement.html")


@app.get("/settings")
def settings_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("settings.html")


@app.get("/finances")
def finances_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("finances.html")


@app.get("/assets")
def assets_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("assets.html")


@app.get("/allocation")
def allocation_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("allocation.html")


@app.get("/dca")
def dca_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("dca.html")


@app.get("/business")
def business_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("business.html")


@app.get("/statements")
def statements_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("statements.html")


@app.get("/inbox")
def inbox_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("inbox.html")


@app.get("/coverage")
def coverage_page(request: Request):
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return serve_html("coverage.html")


@app.get("/reminder-done")
def reminder_done(request: Request, task: str = "finance"):
    """Tapped from a Telegram reminder — pauses that task's daily nudges this month."""
    if not is_authed(request):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    if task not in ("finance", "receipts"):
        task = "finance"
    cycle = datetime.date.today().strftime("%Y-%m")
    views.mark_reminder_done(task, cycle)
    label = "receipt uploads" if task == "receipts" else "the finance update"
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{background:#0a0a0b;color:#e7e7ea;font-family:system-ui;padding:48px 24px;text-align:center}"
        "a{color:#a78bfa}</style></head><body>"
        f"<div style='font-size:42px'>✅</div><h2>Reminders paused for {label} ({cycle})</h2>"
        "<p style='color:#a1a1aa'>No more nudges until next month.</p>"
        f"<p><a href='{BASE}/'>Open Kiviat Lab →</a></p></body></html>")


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


@app.get("/api/members")
def api_members(request: Request, family: Optional[str] = None,
                _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    members = config.load_members(fid)
    enriched = [{**m, "age": views.age_from_birthday(m.get("birthday"))} for m in members]
    return JSONResponse({"family_id": fid, "members": enriched})


@app.put("/api/members")
async def api_members_save(request: Request, family: Optional[str] = None,
                           _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    members = body.get("members") if isinstance(body, dict) else body
    if not isinstance(members, list):
        raise HTTPException(400, "expected {members: [...]}")
    # Enforce a single primary, and strip the derived `age` before persisting.
    seen_primary = False
    clean = []
    for m in members:
        if not isinstance(m, dict):
            continue
        m = {k: v for k, v in m.items() if k != "age"}
        if m.get("primary"):
            m["primary"] = not seen_primary
            seen_primary = seen_primary or m["primary"]
        clean.append(m)
    if clean and not seen_primary:
        clean[0]["primary"] = True  # always have a main person
    config.save_members(fid, clean)
    return JSONResponse({"ok": True, "members":
                         [{**m, "age": views.age_from_birthday(m.get("birthday"))} for m in clean]})


@app.get("/api/finances")
def api_finances(request: Request, family: Optional[str] = None,
                 _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    saved = views.load_finances(fid)
    return JSONResponse({
        "months": saved.get("months", {}),
        "seed": views.finances_seed(fid),
        "seed_month": views.finances_seed_month(fid),
        "currency": views.load_snapshot(fid).get("currency", "CAD"),
    })


@app.put("/api/finances")
async def api_finances_save(request: Request, family: Optional[str] = None,
                            _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected {months: {...}}")
    return JSONResponse({"ok": True, **views.save_finances(fid, body)})


@app.get("/api/assets")
def api_assets(request: Request, family: Optional[str] = None,
               _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    saved = views.load_assets(fid)
    return JSONResponse({
        "months": saved.get("months", {}),
        "seed": views.assets_seed(fid),
        "seed_month": views.finances_seed_month(fid),
        "liabilities": views.assets_liabilities(fid),
        "contributions": views.assets_contributions(fid),
        "currency": views.load_snapshot(fid).get("currency", "CAD"),
    })


@app.put("/api/assets")
async def api_assets_save(request: Request, family: Optional[str] = None,
                          _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected {months: {...}}")
    return JSONResponse({"ok": True, **views.save_assets(fid, body)})


@app.get("/api/allocation")
def api_allocation(request: Request, family: Optional[str] = None,
                   _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse(views.allocation_calculator(fid))


@app.put("/api/allocation")
async def api_allocation_save(request: Request, family: Optional[str] = None,
                              _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected {targets, stats}")
    return JSONResponse({"ok": True, **views.save_alloc_plan(fid, body)})


@app.get("/api/allocation/dca")
def api_dca(request: Request, family: Optional[str] = None,
            _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse(views.dca_plan(fid))


@app.put("/api/allocation/dca")
async def api_dca_save(request: Request, family: Optional[str] = None,
                       _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected {frequency, timeframes}")
    views.save_dca_settings(fid, body)
    return JSONResponse({"ok": True, **views.dca_plan(fid)})


# ---------- business: receipts (Phase 1) ----------

@app.get("/api/business/receipts")
def api_business_receipts(request: Request, family: Optional[str] = None,
                          _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    cfg = business.load_business_config(fid)
    recs = business.load_receipts(fid)
    att = set(business.ATTENTION_FLAGS)
    fys = set()
    for r in recs:
        fy = business.fiscal_year_of(r.get("date"), cfg)
        r["fiscal_year"] = fy
        if fy is not None:
            fys.add(fy)
        r["flags"] = business.receipt_flags(r)
        r["needs_attention"] = any(f in att for f in r["flags"])
    return JSONResponse({
        "receipts": recs,
        "currency": views.load_snapshot(fid).get("currency", "CAD"),
        "categories": business.ALL_CATEGORIES,
        "vendors": sorted({r.get("vendor") for r in recs if r.get("vendor")}),
        "fiscal_years": sorted(fys, reverse=True),
    })


@app.post("/api/business/upload")
async def api_business_upload(request: Request, family: Optional[str] = None,
                             files: list[UploadFile] = File(...),
                             _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    out, errors, duplicates = [], [], []
    for f in files:
        try:
            data = await f.read()
            rec = business.ingest_upload(fid, f.filename, data)
            if rec.get("duplicate"):
                duplicates.append({"file": f.filename, "vendor": rec.get("vendor"),
                                   "date": rec.get("date"), "total": rec.get("total")})
            else:
                out.append(rec)
        except business.ParseError as e:
            errors.append({"file": f.filename, "error": str(e)})
        except Exception as e:  # never 500 the whole batch on one bad file
            errors.append({"file": f.filename, "error": f"upload failed: {e}"})
    return JSONResponse({"receipts": out, "errors": errors, "duplicates": duplicates})


@app.put("/api/business/receipts/{rid}")
async def api_business_update(rid: str, request: Request, family: Optional[str] = None,
                             _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object of fields to update")
    rec = business.update_receipt(fid, rid, body)
    if rec is None:
        raise HTTPException(404, "no such receipt")
    return JSONResponse({"ok": True, "receipt": rec})


@app.post("/api/business/receipts/reparse-failed")
def api_business_reparse_failed(request: Request, family: Optional[str] = None,
                               _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    fixed, still = business.reparse_failed(fid)
    return JSONResponse({"ok": True, "fixed": fixed, "still_failing": still})


@app.delete("/api/business/receipts/{rid}")
def api_business_delete(rid: str, request: Request, family: Optional[str] = None,
                        _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    if not business.delete_receipt(fid, rid):
        raise HTTPException(404, "no such receipt")
    return JSONResponse({"ok": True})


@app.get("/api/business/file/{rid}")
def api_business_file(rid: str, request: Request, family: Optional[str] = None):
    if not is_authed(request):
        raise HTTPException(401, "login required")
    fid = _retire_family(family)
    p = business.upload_path(fid, rid)
    if p is None or not p.exists():
        raise HTTPException(404, "no such file")
    return FileResponse(p)


# ---------- business: config (fiscal year) ----------

@app.get("/api/business/config")
def api_business_config(request: Request, family: Optional[str] = None,
                        _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    cfg = business.load_business_config(fid)
    return JSONResponse({**cfg, "all_accounts": statements.ACCOUNTS,
                         "categories": business.EXPENSE_CATEGORIES,
                         "category_tax": business.load_category_tax(fid),
                         "fiscal_year_bounds_example": {
                             str(fy): business.fiscal_year_bounds(fy, cfg) for fy in (2025, 2026)}})


@app.put("/api/business/config")
async def api_business_config_save(request: Request, family: Optional[str] = None,
                                   _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    out = {}
    if "category_tax" in body and isinstance(body["category_tax"], dict):
        out["category_tax"] = business.save_category_tax(fid, body["category_tax"])
    # fiscal-year / archived-accounts etc. go through the general saver
    if any(k in body for k in ("fiscal_year_start_month", "fiscal_year_start_day", "archived_accounts")):
        out["config"] = business.save_business_config(fid, body)
    return JSONResponse({"ok": True, **out})


@app.post("/api/business/receipts/reestimate-tax")
def api_business_reestimate_tax(request: Request, family: Optional[str] = None,
                                _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse({"ok": True, "updated": business.reestimate_tax(fid)})


# ---------- business: statements (Phase 2) ----------

@app.get("/api/business/statements")
def api_statements(request: Request, family: Optional[str] = None,
                   _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse(statements.overview(fid))


@app.get("/api/business/coverage")
def api_coverage(request: Request, family: Optional[str] = None,
                 _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse(matching.coverage(fid))


@app.get("/api/business/coverage/candidates/{txn_id}")
def api_coverage_candidates(txn_id: str, request: Request, family: Optional[str] = None,
                            _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse({"candidates": matching.candidates_for_txn(fid, txn_id)})


@app.post("/api/business/coverage/link")
async def api_coverage_link(request: Request, family: Optional[str] = None,
                            _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    t = matching.link_receipt(fid, body.get("txn_id"), body.get("receipt_id"))
    if t is None:
        raise HTTPException(404, "transaction or receipt not found")
    return JSONResponse({"ok": True})


@app.post("/api/business/coverage/unlink")
async def api_coverage_unlink(request: Request, family: Optional[str] = None,
                              _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    t = matching.unlink_receipt(fid, body.get("txn_id"))
    if t is None:
        raise HTTPException(404, "transaction not found")
    return JSONResponse({"ok": True})


@app.post("/api/business/statements/import")
async def api_statements_import(request: Request, family: Optional[str] = None,
                                account: str = Form(...),
                                files: list[UploadFile] = File(...),
                                _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    imported, errors = [], []
    for f in files:
        try:
            data = await f.read()
            imported.append(statements.ingest_statement(fid, account, f.filename, data))
        except statements.ParseError as e:
            errors.append({"file": f.filename, "error": str(e)})
        except Exception as e:  # never fail the whole batch on one bad file
            errors.append({"file": f.filename, "error": f"import failed: {e}"})
    return JSONResponse({"ok": True, "imports": imported, "errors": errors})


@app.put("/api/business/statements/txn/{tid}")
async def api_statements_update(tid: str, request: Request, family: Optional[str] = None,
                                _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object of fields to update")
    t = statements.update_transaction(fid, tid, body)
    if t is None:
        raise HTTPException(404, "no such transaction")
    return JSONResponse({"ok": True, "transaction": t})


@app.post("/api/business/statements/import/{import_id}/{action}")
def api_statements_action(import_id: str, action: str, request: Request,
                          family: Optional[str] = None,
                          _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    if action == "delete":
        if not statements.delete_import(fid, import_id):
            raise HTTPException(404, "no such import")
        return JSONResponse({"ok": True})
    status_map = {"commit": "committed", "reject": "rejected"}
    if action not in status_map:
        raise HTTPException(400, "action must be commit, reject, or delete")
    rec = statements.set_import_status(fid, import_id, status_map[action])
    if rec is None:
        raise HTTPException(404, "no such import")
    return JSONResponse({"ok": True, "import": rec})


@app.get("/api/business/statements/file/{import_id}")
def api_statements_file(import_id: str, request: Request, family: Optional[str] = None):
    if not is_authed(request):
        raise HTTPException(401, "login required")
    fid = _retire_family(family)
    p = statements.import_file_path(fid, import_id)
    if p is None or not p.exists():
        raise HTTPException(404, "no such file")
    return FileResponse(p)


# ---------- inbox: iOS Share-Sheet batch upload ----------

@app.get("/api/inbox/accounts")
def api_inbox_accounts(request: Request, family: Optional[str] = None,
                       _: None = Depends(require_token_or_session)):
    fid = _retire_family(family)
    cfg = business.load_business_config(fid)
    return JSONResponse({"accounts": statements.active_accounts(cfg)})


@app.post("/api/inbox")
async def api_inbox_upload(request: Request, family: Optional[str] = None,
                           _: None = Depends(require_token_or_session)):
    """Accept a Share-Sheet batch. Parse the multipart form ourselves and collect EVERY file
    part regardless of field name — iOS Shortcuts can attach files under varying names, so we
    don't rely on a single `files` field."""
    fid = _retire_family(family)
    form = await request.form()
    kind = (form.get("kind") or "").strip()
    account = form.get("account") or None
    batch = (form.get("batch") or "").strip()       # existing job_id to append to (Shortcut loop)
    payload = []
    # (a) real multipart file parts (browser uploads, or a Shortcut file field if it works)
    for key, val in form.multi_items():
        if hasattr(val, "filename") and hasattr(val, "read") and val.filename:
            payload.append((val.filename, await val.read()))
    # (b) base64 text field (reliable from iOS Shortcuts): decode + sniff the type ourselves
    data_b64 = form.get("data")
    if data_b64:
        import base64 as _b64
        try:
            raw = _b64.b64decode(str(data_b64), validate=False)
        except Exception:
            raise HTTPException(400, "couldn't decode the base64 'data' field")
        ext = inbox.sniff_ext(raw)
        fn = (form.get("filename") or "").strip()
        if not fn or fn.lower() in ("shared", "shared" + ext):
            # No real name from the share → a unique, content-based one so a batch isn't all
            # identical. Same image → same name (stable, matches dedup).
            import hashlib as _h
            label = "statement" if kind == "statement" else "receipt"
            fn = f"{label}-{_h.sha256(raw).hexdigest()[:8]}{ext}"
        elif "." not in fn:
            fn += ext
        payload.append((fn, raw))
    if not payload:
        print(f"inbox: 400 no files. form keys={list(form.keys())} kind={kind!r} batch={batch!r}")
        raise HTTPException(400, "no files received in the upload")
    try:
        if batch and inbox.job_exists(fid, batch):
            job = inbox.append_files(fid, batch, payload)   # add to the same batch
        else:
            job = inbox.create_job(fid, kind, account, payload)
    except business.ParseError as e:
        raise HTTPException(e.status, str(e))
    inbox.start_processing(fid, job["id"])
    print(f"inbox POST: kind={kind!r} batch_in={batch[:8] or '-'} received={len(payload)} "
          f"-> job={job['id'][:8]} total_items={len(job['items'])}")
    return JSONResponse({"ok": True, "job_id": job["id"], "received": len(payload),
                         "total": len(job["items"]), "kind": job["kind"], "account": job["account"]})


@app.get("/api/inbox/jobs")
def api_inbox_jobs(request: Request, family: Optional[str] = None,
                   _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse({"jobs": inbox.load_jobs(fid)})


@app.post("/api/inbox/retry/{job_id}/{index}")
def api_inbox_retry(job_id: str, index: int, request: Request, family: Optional[str] = None,
                    _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    job = inbox.retry_item(fid, job_id, index)
    if job is None:
        raise HTTPException(404, "no such job")
    return JSONResponse({"ok": True, "job": job})


def _retire_family(family: Optional[str]) -> str:
    fid = config.resolve_family(family)
    if not fid:
        raise HTTPException(404, "no family specified and no active family set")
    if not paths.family_exists(fid):
        raise HTTPException(404, f"no such family: {fid}")
    return fid


@app.get("/api/retirement")
def api_retirement(request: Request, family: Optional[str] = None,
                   _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    return JSONResponse({
        "defaults": views.retirement_defaults(fid),
        "saved": views.load_retirement_inputs(fid),
    })


@app.put("/api/retirement")
async def api_retirement_save(request: Request, family: Optional[str] = None,
                              _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "expected a JSON object of inputs")
    return JSONResponse({"ok": True, "saved": views.save_retirement_inputs(fid, data)})


@app.delete("/api/retirement")
def api_retirement_clear(request: Request, family: Optional[str] = None,
                         _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    views.clear_retirement_inputs(fid)
    return JSONResponse({"ok": True})


@app.post("/api/retirement/ai-fill")
def api_retirement_aifill(request: Request, family: Optional[str] = None,
                          _: None = Depends(require_api_auth)):
    fid = _retire_family(family)
    try:
        return JSONResponse(views.ai_retirement_fill(fid))
    except views.AIFillError as e:
        raise HTTPException(e.status, str(e))


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
