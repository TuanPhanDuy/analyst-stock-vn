#!/usr/bin/env python3
"""
Send a short "pipeline failed" email.

Called from GitHub Actions `if: failure()` steps so a crashed scan/advisor
surfaces in your inbox instead of dying silently in the Actions log.

Usage:  python scripts/notify_failure.py "<job name>"

Reads GMAIL_USER / GMAIL_APP_PASSWORD / NOTIFY_EMAIL from the environment
(same secrets the scan itself uses). If they are unset it just prints and
exits 0 — a missing alert channel must never mask the original failure.

GitHub provides GITHUB_SERVER_URL / GITHUB_REPOSITORY / GITHUB_RUN_ID in
Actions runs, which we turn into a direct link to the failed run.
"""
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

ICT = timezone(timedelta(hours=7))


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return "(run locally — no GitHub run URL)"


def main() -> None:
    job = sys.argv[1] if len(sys.argv) > 1 else "analyst-stock-vn pipeline"
    now = datetime.now(ICT).strftime("%A %d %b %Y · %H:%M ICT")
    url = _run_url()

    gmail_user = os.environ.get("GMAIL_USER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_addr = os.environ.get("NOTIFY_EMAIL", "")

    body = (
        f"❌ {job} FAILED\n\n"
        f"When: {now}\n"
        f"Run:  {url}\n\n"
        f"No trading email was sent for this run. Check the Actions log and "
        f"re-run once the cause (usually a vnstock/network hiccup) clears.\n"
    )
    print(body)

    if not all([gmail_user, app_password, to_addr]):
        print("[notify_failure] email creds unset — printed only.")
        return

    subject = f"❌ FAILED: {job}"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"VN Trading Desk <{gmail_user}>"
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(gmail_user, app_password)
            srv.sendmail(gmail_user, to_addr, msg.as_string())
        print(f"[notify_failure] alert emailed → {to_addr}")
    except Exception as e:
        # Never let the alerter itself crash the failure step.
        print(f"[notify_failure] could not send email: {e}")


if __name__ == "__main__":
    main()
