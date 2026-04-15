#!/usr/bin/env python3
import os
import sys
import requests
from datetime import datetime

print("=" * 50)
print("Peregrine Scanner — Debug Mode")
print("=" * 50)

SAM_API_KEY      = os.environ.get("SAM_API_KEY", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_TO         = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
EMAIL_FROM       = os.environ.get("EMAIL_FROM", "mike.kelly@peregrine.io")

print(f"SAM_API_KEY set:      {'YES' if SAM_API_KEY else 'NO'}")
print(f"SENDGRID_API_KEY set: {'YES' if SENDGRID_API_KEY else 'NO'}")
print(f"EMAIL_TO:             {EMAIL_TO}")
print(f"EMAIL_FROM:           {EMAIL_FROM}")

if not SENDGRID_API_KEY:
    print("ERROR: SENDGRID_API_KEY is missing")
    sys.exit(1)

print("\nAttempting to send test email via SendGrid...")

payload = {
    "personalizations": [{"to": [{"email": EMAIL_TO}]}],
    "from": {"email": EMAIL_FROM, "name": "Peregrine Scanner"},
    "subject": f"Peregrine Test Email — {datetime.utcnow().strftime('%B %d, %Y')}",
    "content": [{"type": "text/html", "value": "<h2>Test successful!</h2><p>The Peregrine daily scanner is working.</p>"}],
}

try:
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    print(f"SendGrid response status: {resp.status_code}")
    print(f"SendGrid response body:   {resp.text}")

    if resp.status_code == 202:
        print("SUCCESS: email sent!")
    else:
        print("FAILED — see error above")
        sys.exit(1)

except Exception as e:
    print(f"EXCEPTION: {type(e).__name__}: {e}")
    sys.exit(1)
