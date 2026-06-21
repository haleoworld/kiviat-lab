# Plan — iOS Share-Sheet Inbox + Shortcut

**Approved:** 2026-06-20. Send a batch of transaction screenshots / receipt photos / PDFs
straight from the iOS Share Sheet (Photos or Files) into Kiviat Lab, tagged as receipts or
statements (+ account), processed in the background with **per-file progress** in the app.

**Decision (user):** the **Shortcut asks** the type, and fetches the **live account list** for
the menu. No auto-detection in v1 — reliable, never misroutes.

## Backend
- **Token auth** — `KIVIAT_API_TOKEN` in `data/.env` (gitignored). `require_token_or_session`
  accepts the browser session cookie OR an `X-Kiviat-Token` header (for the Shortcut). Server is
  tailnet-only, so the token is a second lock.
- **`GET /api/inbox/accounts`** → active accounts (so the Shortcut menu auto-updates on archive/add).
- **`POST /api/inbox`** (`kind`, `account?`, `files[]`) → saves files, creates a job (one item per
  file, status `queued`), starts a **background thread**, returns `{job_id, queued}` instantly.
- **`GET /api/inbox/jobs`** → recent jobs with per-file status for the progress view.
- **`POST /api/inbox/retry/{job_id}/{index}`** → re-queue a failed file.
- **`inbox.py`**: storage under `business/inbox/<job_id>/` (`job.json` + `NN_<filename>`).
  `process_job` routes each file → `business.ingest_upload` (receipt) or
  `statements.ingest_statement` (statement, with account); status `queued → processing →
  done|failed`. Atomic job writes (temp + replace) so polled reads are clean.

## App — Inbox page (`/inbox`, under Business)
- Each batch newest-first: kind (+account), time, **progress bar** (done / processing / failed),
  expandable per-file status; done → link to Receipts/Statements review; failed → reason + retry.
- Auto-polls `/api/inbox/jobs` while anything is queued/processing.

## The Shortcut (user builds; steps + token provided)
1. Receive Images / PDFs / Files from Share Sheet
2. Choose from Menu → Receipts / Statements
3. If Statements → GET `/api/inbox/accounts` → "Choose from List"
4. POST files + `kind`/`account` to `/api/inbox` with `X-Kiviat-Token`
5. Notification: "Sent N to Kiviat — track in the app"

## Out of scope (v1)
- No auto-detection of type/account. No crash-resume — a mid-batch server restart leaves items
  stuck; re-share or retry. Mixed receipt+statement batches = two shares.
