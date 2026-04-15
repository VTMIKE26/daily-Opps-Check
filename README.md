# 🦅 Peregrine Daily Federal Scanner — v2 Multi-Source Edition

Automated daily search across **5 federal data sources** for RFIs, Sources Sought notices,
Industry Days, competitive intelligence, and legislative signals relevant to Peregrine.io.
Ranks every result by fit, and emails a formatted HTML digest every morning at 9am ET.

---

## What's New in v2

| Feature | v1 | v2 |
|---|---|---|
| SAM.gov RFIs & Industry Days | ✅ | ✅ |
| Federal Register RFIs | ❌ | ✅ No key needed |
| USASpending.gov Award Intel | ❌ | ✅ No key needed |
| Agency RSS Feeds (DHS, GSA) | ❌ | ✅ No key needed |
| Congressional Signals | ❌ | ✅ No key needed |
| Source badge in email | ❌ | ✅ |
| Failure alert email | ❌ | ✅ |
| Console scan summary | ❌ | ✅ |

---

## Data Sources

### 1. 🔵 SAM.gov API *(requires free API key)*
The primary federal procurement database. Searches daily for:
- **RFIs** (Request for Information)
- **Sources Sought** notices
- **Pre-Solicitations**
- **Industry Days** — broad keyword search 60 days forward

### 2. 📰 Federal Register API *(no key required)*
The official U.S. government journal, which agencies use to publish RFIs and market
research notices separate from SAM.gov. Many DHS, DOJ, and intelligence community
notices appear here first. Searched using 7 targeted keyword queries.

### 3. 💰 USASpending.gov API *(no key required)*
Provides **competitive intelligence** on recent contract awards — who is spending money
in Peregrine's target space, at which agencies, and at what values. This helps you
identify recompete opportunities and warm target accounts.

### 4. 📡 Agency RSS Feeds *(no key required)*
Public RSS/Atom feeds from DHS, GSA Interact, and other agencies. Catches procurement
and event announcements that don't always surface in SAM.gov immediately.

### 5. 🏛 Congress.gov Signals *(no key required)*
Tracks committee reports and bills mentioning public safety, law enforcement technology,
AI, and data analytics — early-warning signals of upcoming federal IT investment.

---

## Setup (15 minutes)

### Step 1 — Get your SAM.gov API key (free)
1. Sign in at [sam.gov](https://sam.gov) (create a free account if needed)
2. Go to **Profile → API Keys → Generate Key**
3. Copy the key — you'll add it as a GitHub secret

### Step 2 — Set up your email sender
**Gmail (recommended):**
1. Enable 2-Factor Authentication on your Google account
2. Google Account → Security → 2-Step Verification → **App Passwords**
3. Generate an App Password for "Mail"
4. Use this as `SMTP_PASSWORD` below

**Other providers:** Use any SMTP service (SendGrid, Mailgun, Outlook, etc.)

### Step 3 — Deploy to GitHub Actions
1. Push this repo to your GitHub account
2. Go to **Settings → Secrets and variables → Actions → New repository secret**
3. Add **all 7 secrets** below:

| Secret Name     | Value                                           |
|-----------------|-------------------------------------------------|
| `SAM_API_KEY`   | Your SAM.gov API key                            |
| `EMAIL_FROM`    | From address (e.g. `alerts@yourdomain.com`)     |
| `EMAIL_TO`      | To address(es), comma-separated                 |
| `SMTP_HOST`     | `smtp.gmail.com`                                |
| `SMTP_PORT`     | `587`                                           |
| `SMTP_USER`     | Your Gmail (or SMTP login) address              |
| `SMTP_PASSWORD` | Your Gmail App Password (NOT your Google login) |

4. Go to **Actions → Peregrine Daily Federal Scanner → Run workflow** to test now.

The scheduled run fires **Mon–Fri at 9am ET** automatically after that.

---

## Customizing the Fit Scoring

Open `search_and_alert.py` and edit these three lists:

```python
HIGH_VALUE_KEYWORDS = [
    "public safety", "law enforcement", "data integration", ...
]   # +15 pts each, max 60 pts

MEDIUM_VALUE_KEYWORDS = [
    "data analytics", "cloud platform", "SaaS", ...
]   # +5 pts each, max 25 pts

NEGATIVE_KEYWORDS = [
    "construction", "HVAC", "janitorial", ...
]   # Auto-excluded from results
```

Also tune the target agencies list for bonus points:
```python
TARGET_AGENCIES = [
    "Department of Homeland Security", "FBI", "DOJ", ...
]   # +5 pts per match, max 15 pts
```

### Scoring Tiers
| Tier | Points |
|------|--------|
| 🟢 Strong Fit | ≥ 50 |
| 🟡 Good Fit | 25–49 |
| 🔵 Possible Fit | 1–24 |
| ⛔ Not a Fit | Contains negative keyword |

---

## Running Locally

```bash
pip install -r requirements.txt

export SAM_API_KEY="your_key_here"
export EMAIL_FROM="you@gmail.com"
export EMAIL_TO="you@gmail.com"
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="you@gmail.com"
export SMTP_PASSWORD="your_app_password"

python search_and_alert.py
```

A local HTML digest is saved as `digest_YYYYMMDD.html` after each run — open in your
browser to preview before the email arrives.

---

## Alternative Deployment (No GitHub)

The script is a single Python file with one dependency (`requests`). It runs anywhere:

| Platform | How |
|----------|-----|
| **AWS Lambda** | Deploy as Lambda function; set EventBridge (CloudWatch) cron `cron(0 13 ? * MON-FRI *)` |
| **Google Cloud Run** | Deploy as container; set Cloud Scheduler trigger |
| **Render.com** | Free tier cron job, paste env vars in dashboard |
| **Railway.app** | Cron service, `$0` for low-usage |
| **Local Mac/Linux** | `crontab -e` → `0 13 * * 1-5 cd /path/to/dir && python search_and_alert.py` |

---

## File Structure

```
peregrine_v2/
├── search_and_alert.py           # Main script (all 5 sources)
├── requirements.txt              # Just: requests
├── .github/
│   └── workflows/
│       └── daily_scan.yml        # GitHub Actions cron + failure alerting
└── README.md
```

---

## Roadmap / Easy Extensions

- **SBIR.gov** — The SBIR/STTR API is also public and free; adds R&D funding opps
- **Regulations.gov** — Public comment periods often precede procurement
- **State procurement portals** — Many states publish RSS feeds (CA, TX, NY, FL)
- **Slack notification** — Replace or augment email with a Slack webhook (5 lines of code)
- **Google Sheets logging** — Append new opportunities to a sheet using the Sheets API
