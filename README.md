# 🦅 Peregrine Daily Federal Scanner

Automated daily intelligence tool for Peregrine.io — searches federal procurement opportunities and competitive/industry signals every weekday morning and delivers a ranked HTML digest by email.

---

## What It Does

Every weekday morning, the scanner:

1. Sweeps **SAM.gov** government-wide via notice-type, keyword, and title searches
2. Runs **4 targeted agency-name sweeps** — DOJ, DHS, DoD, and (new) a broader set of financial-crimes/investigative/oversight agencies — to catch matches SAM's keyword search sometimes misses
3. Scores every result against **10 capability clusters** and **65 hard exclusions**
4. Ranks results into Strong / Good / Possible / Low Fit tiers
5. Pulls competitor news, agency budget/spending signals, and industry news from RSS/Google News
6. Delivers a single HTML email digest (Gmail SMTP primary, SendGrid fallback) and saves a local `digest_YYYYMMDD.html` copy

---

## Scoring System

Every opportunity is scored against Peregrine's capability clusters. Points accumulate across every cluster whose phrases appear in the title, description, or NAICS hints — multiple clusters can score at once. The total determines the tier.

| Cluster | Points | Signal |
|---|---|---|
| Data Integration & Unification | 20 | Enterprise data platforms, unified environments, data fusion |
| Investigative & Operational Analytics | 20 | Crime analytics, digital evidence, link analysis |
| Federated & Enterprise Search | 20 | Cross-system search, information retrieval |
| Entity Resolution & Record Intelligence | 20 | Deduplication, identity resolution, knowledge graphs |
| Secure Government SaaS | 15 | FedRAMP, GovCloud, Zero Trust, CJIS |
| Public Safety & Law Enforcement | 20 | LE platforms, RMS, fusion centers, body-worn video |
| Corrections & Community Supervision | 20 | Probation, parole, offender management |
| Platform Modernization & Replacement | 20 | Legacy modernization, Palantir replacement |
| **Ontology & Semantic Modeling** *(new)* | **40** | "ontology" / "ontologies" |
| AI & Machine Learning | 22 | AI/ML platforms, LLMs, predictive models |

A notice with no capability match but matching an "engagement event" phrase (industry day, sources sought, RFI, etc.) against a Tier-1 LE/security agency still scores a small watch-worthy amount (15 or 5 pts) so early signals aren't missed entirely.

### Tier Thresholds

| Tier | Score | Action |
|---|---|---|
| 🟢 Strong Fit | ≥ 40 pts | Act Now |
| 🟡 Good Fit | ≥ 15 pts | Review Today |
| 🔵 Possible Fit | > 0 pts | Review These |
| ⚪ Low Fit | 0 pts | Any keyword match only |
| ⛔ Excluded | — | Hard exclusion matched (checked *before* scoring) |

Because the new Ontology cluster is worth 40 pts on its own, a single mention of "ontology" is enough by itself to land a notice in Strong Fit — unless it also trips a hard exclusion, which is checked first.

### Hard Exclusions

**65 terms** immediately disqualify an opportunity (score = 0, tier = Excluded) regardless of any capability match. These cover procurement categories with zero relevance to Peregrine's software platform: physical facilities/maintenance (HVAC, plumbing, roofing), hardware/equipment purchases, weapons/munitions, staffing/janitorial/food/medical services, network cabling, software license renewals, training-only contracts, and construction. Full phrase list is in `HARD_EXCLUSIONS` in the script, and in the companion **Scoring Reference** doc.

---

## Data Sources

### 🔵 SAM.gov API *(requires free API key)*
Primary federal procurement database, searched government-wide (no agency restriction):
- **Notice-type sweep** — 6 types (Sources Sought, Presolicitation, Combined Synopsis, Special Notice, Solicitation, Intent to Bundle), 30-day window
- **Keyword sweep** — 13 terms, 90-day window
- **Title sweep** — 16 terms, 90-day window, with extra pagination on high-volume terms
- **Watchlist** — 5 pinned notice IDs checked directly by ID every run

### 🏛 DOJ, DHS, DoD Agency Sweeps
Each searches SAM.gov directly by `organizationName` for a named list of agencies (DOJ: 9 orgs, DHS: 6 orgs, DoD: 5 orgs), catching notices the government-wide keyword/title search sometimes misses due to SAM's imperfect text search.

### 🆕 Other-Agencies Sweep
Same approach, extended to **18 financial-crimes, investigative, and oversight organizations** outside DOJ/DHS/DoD: Treasury, FinCEN, IRS, State/Diplomatic Security, USPS, VA, HHS, SSA, Labor, DOT, Interior, Commerce, GSA, SEC, FTC, CFPB, EPA, and agency Inspectors General generally. Same scoring, same exclusions — just a wider net of agency names searched by name.

### 🔎 Competitor Intelligence
Per-competitor Google News RSS queries for all 12 tracked competitors, max 2 articles per competitor, last 7 days.

### 📡 Industry News
RSS feeds from FedScoop, Nextgov, GCN, and GovTech (public safety), filtered to relevant keywords, last 7 days, max 15 articles.

### 💵 Agency Budget & Spending Signals
Google News RSS across 7 targeted queries (DOJ Budget, FBI Technology, DHS Budget, ICE Technology, CISA Budget, Byrne JAG, NIBIN Funding), last 7 days, max 18 items.

---

## Email Digest Sections

| Section | Content |
|---|---|
| 🟢 Strong Fit — Act Now | ≥ 40 pts, with scoring reasons |
| 🟡 Good Fit — Review Today | ≥ 15 pts |
| 🔵 Possible Fit — Review These | > 0 pts |
| ⚪ Low Fit | 0 pts but matched an engagement-event signal (top 20 shown) |
| 🔎 Competitor Intelligence | Grouped by competitor, last 7 days |
| 📡 Agency Budget & Spending Signals | Last 7 days |
| 📰 Industry News | Last 7 days |
| 📣 Industry Days | All open "industry day" notices across every tier |

Subject line format: `Peregrine Daily Scanner | 3 Strong · 5 Good · 8 Possible | Sep 09`

---

## Competitors Monitored (12)

Palantir · Axon · ShotSpotter · Mark43 · Tyler Technologies · Motorola Solutions · IBM i2 · Esri · Databricks · Appriss · SuperCom · Flock Safety

---

## Setup

### Step 1 — SAM.gov API key (free)
1. Sign in at [sam.gov](https://sam.gov)
2. Go to **Profile → API Keys → Generate Key**

### Step 2 — Email delivery
The scanner tries **Gmail SMTP first** (goes straight to inbox), falling back to **SendGrid** if Gmail isn't configured.
- **Gmail**: enable 2FA, then generate an App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
- **SendGrid** (fallback, free tier: 100 emails/day): sign up at [sendgrid.com](https://sendgrid.com) → **Settings → API Keys → Create API Key** → Restricted → Mail Send only

### Step 3 — GitHub Secrets
Go to **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `SAM_API_KEY` | Your SAM.gov API key |
| `GMAIL_APP_PASSWORD` | Your Gmail App Password (primary send method) |
| `SENDGRID_API_KEY` | Your SendGrid API key (fallback) |
| `EMAIL_TO` | Destination inbox |
| `EMAIL_FROM` | Sending address |

### Step 4 — Reliable scheduling via cron-job.org

GitHub Actions cron is unreliable (30–90 min delays common). Use [cron-job.org](https://cron-job.org) (free) as the primary trigger:

**cron-job.org settings:**
- **URL**: `https://api.github.com/repos/VTMIKE26/daily-Opps-Check/actions/workflows/daily_scan.yml/dispatches`
- **Method**: POST
- **Headers**: `Authorization: token YOUR_GITHUB_PAT` · `Accept: application/vnd.github+json` · `Content-Type: application/json`
- **Body**: `{"ref":"main"}`
- **Schedule**: `0 12 * * 1-5` (7am EST) AND `0 11 * * 1-5` (7am EDT)

Generate a GitHub PAT at [github.com/settings/tokens](https://github.com/settings/tokens) with `repo` + `workflow` scopes. A successful test run returns HTTP **204**.

The GitHub native cron stays configured as a backup trigger.

---

## Running Locally

```bash
pip install requests

export SAM_API_KEY="your_key_here"
export GMAIL_APP_PASSWORD="your_gmail_app_password"   # optional — primary send method
export SENDGRID_API_KEY="your_sendgrid_key"           # optional — fallback send method
export EMAIL_TO="mike.kelly@peregrine.io"
export EMAIL_FROM="mikefkelly26@gmail.com"

python daily_scan.py
```

Output: `digest_YYYYMMDD.html` saved locally.

---

## API Usage

SAM.gov calls scale with how many pages each sweep actually returns (a sweep stops early once a page comes back under 100 results), so real usage is normally well under the worst case:

| Sweep | Max calls (worst case) |
|---|---|
| SAM.gov core (notice-type + keyword + title + watchlist) | ~62 |
| DOJ agency sweep | ~27 |
| DHS agency sweep | ~18 |
| DoD agency sweep | ~15 |
| Other-Agencies sweep *(new)* | ~54 |
| **Total** | **~176** |

The scanner backs off automatically on SAM.gov HTTP 429 responses (rate limited) and reuses cached results across sweeps rather than re-querying.

---

## File Structure

```
daily-Opps-Check/
├── daily_scan.py               # Main script — sources, scoring, email
├── .github/
│   └── workflows/
│       ├── daily_scan.yml      # Primary workflow (dispatch + backup cron)
│       └── keep_alive.yml      # Commits timestamp to prevent 60-day inactivity suspension
└── README.md
```
