# PDF thumbnails in the receipts list

**Status:** DONE (not yet committed). 2026-06-26. Requested by Terry.

**Why:** PDF receipts showed only a "📄 PDF · open" tile in the receipts list — no
at-a-glance preview, unlike image receipts which render inline. Terry scans receipts
to PDF on iPhone (auto-crop/deskew is worth keeping), so the fix is to give PDFs a
real thumbnail rather than push him toward lower-quality camera photos.

**Approach:** Lazy, server-side, cached. Rasterize page 1 of a PDF once via macOS
Quick Look (`qlmanage -t`) — same shell-out-to-macOS pattern as the existing HEIC→JPEG
(`sips`), so **no new pip dependency**. Backfills existing PDF receipts automatically.

## Changes
- **business.py** — `thumbs_dir(family_id)`; `thumb_path(family_id, rid)`: images
  return the original; PDFs rasterize to cached `thumbs/<rid>.png` via
  `qlmanage -t -s 400 -o <thumbs_dir> <pdf>` (output `<rid>.pdf.png` renamed to
  `<rid>.png`), cached once. `delete_receipt` also removes the cached thumb.
- **server.py** — `GET /api/business/file/{rid}/thumb` (same auth as `/file/{rid}`):
  serves the PNG/image; 404 if it can't be produced.
- **web/business.html** — receipts-list PDF branch now renders
  `<img src=".../thumb">` (wrapped in the link that opens the full PDF in a new tab)
  with a 📄 corner badge; `onerror` → falls back to the old "📄 PDF · open" tile.

## Verified
- Core (Python): PDF→cached 219×400 PNG, cache hit on 2nd call, image→original,
  missing rid→None.
- HTTP (authed): PDF thumb 200 image/png; image thumb 200 image/jpeg; missing 404;
  full-file endpoint still 200 application/pdf (no regression).
- business.html inline JS parses clean (`node --check`).

## Scope held
Receipts list only. Statement PDFs keep current behavior (extend later if wanted).
Considered but declined: in-app "scan" button — web can't invoke iOS VisionKit
document scanner; a `capture` camera button only yields raw photos (worse for
extraction), so not worth it.
