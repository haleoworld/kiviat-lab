#!/usr/bin/env python3
"""Monthly Telegram reminders (one-way nudges).

Runs DAILY (via launchd) but only sends from the first Saturday of each month
until you mark each task done. Two tasks:
  - finance:  log last month's statements & numbers
  - receipts: upload last month's business receipts & invoices

Configure in the data-root .env (data/.env):
    TELEGRAM_BOT_TOKEN=123456:ABC...      # from @BotFather
    TELEGRAM_CHAT_ID=987654321            # from @userinfobot
    KIVIAT_PUBLIC_URL=https://<host>/kiviat-lab

"Done" is set by tapping the link in a message (-> /reminder-done?task=...), or
by telling Claude. Pauses that task until next month.

    python reminder.py                # send any pending tasks (date-gated)
    python reminder.py --force        # send all tasks now (testing)
    python reminder.py --force receipts   # send just one task now (testing)
"""
from __future__ import annotations

import datetime
import os
import sys
import urllib.parse
import urllib.request

from dotenv import load_dotenv

import paths
import views

if paths.ENV_FILE.exists():
    load_dotenv(paths.ENV_FILE)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID")
APP_URL = (os.environ.get("KIVIAT_PUBLIC_URL") or "").rstrip("/")


def first_saturday(d: datetime.date) -> datetime.date:
    for day in range(1, 8):
        if datetime.date(d.year, d.month, day).weekday() == 5:  # 5 = Saturday
            return datetime.date(d.year, d.month, day)
    return d


def prev_month_label(today: datetime.date) -> str:
    prev = today.replace(day=1) - datetime.timedelta(days=1)
    return prev.strftime("%B %Y")


def _open(): return f"{APP_URL}/" if APP_URL else "the app"
def _done(task): return f"{APP_URL}/reminder-done?task={task}" if APP_URL else "(set KIVIAT_PUBLIC_URL)"


def msg_finance(today: datetime.date) -> str:
    return (
        f"📊 <b>New month — update your finances</b>\n\n"
        f"Log <b>{prev_month_label(today)}</b>'s statements &amp; numbers: chequing "
        f"(RBC/BMO/TD), cards, mortgages, car &amp; RRSP loans, RRSP/TFSA/RESP "
        f"(Questrade/TD), business (TD/IBKR/Amex), plus property &amp; vehicle values.\n\n"
        f"Open the app: {_open()}\n(or send your numbers to Claude.)\n\n"
        f"✅ Done? Tap to stop these reminders:\n{_done('finance')}"
    )


def msg_receipts(today: datetime.date) -> str:
    return (
        f"🧾 <b>New month — upload your business receipts</b>\n\n"
        f"Upload <b>{prev_month_label(today)}</b>'s business <b>receipts &amp; invoices</b> into "
        f"the app. They'll be OCR'd, categorized, sales-tax/HST calculated, and matched to your "
        f"bank &amp; credit-card transactions — ready to export for corp tax.\n\n"
        f"Upload: {_open()}\n\n"
        f"✅ Done with receipts? Tap to stop these reminders:\n{_done('receipts')}"
    )


TASKS = [("finance", msg_finance), ("receipts", msg_receipts)]


def send(text: str) -> int:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    with urllib.request.urlopen(url, data=data, timeout=20) as r:
        return r.status


def main() -> None:
    args = sys.argv[1:]
    force = "--force" in args
    only = next((a for a in args if a in ("finance", "receipts")), None)
    today = datetime.date.today()
    cycle = today.strftime("%Y-%m")

    if not TOKEN or not CHAT:
        print("reminder: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping.")
        return
    if not force and today < first_saturday(today):
        print(f"reminder: {today} is before this month's first Saturday — skipping.")
        return

    sent = []
    for task, builder in TASKS:
        if only and task != only:
            continue
        if not force and views.reminder_done_for(task) == cycle:
            continue
        try:
            send(builder(today))
            sent.append(task)
        except Exception as e:
            print(f"reminder: {task} send FAILED: {e}")
    print(f"reminder: sent {sent or 'nothing'} for cycle {cycle}.")


if __name__ == "__main__":
    main()
