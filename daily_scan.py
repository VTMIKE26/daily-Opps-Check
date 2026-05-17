#!/usr/bin/env python3
"""
Peregrine.io Daily Federal Opportunity Scanner — Multi-Source Edition
=======================================================================
Data Sources (all free, no registration required except SAM.gov API):
  1. SAM.gov API v2          — RFIs, Sources Sought, Pre-Solicitations, Industry Days
  2. Federal Register API    — RFI notices published by federal agencies (NO KEY)
  3. USASpending.gov API v2  — Recent contract awards in target NAICS (competitive intel) (NO KEY)
  4. DHS/DOJ/FBI procurement — Web-scraped upcoming solicitations & industry events
  5. GSA eBuy / schedules    — RSS/public feed scrape for IT Schedule 70 opportunities

Outputs:
  - Ranked HTML email digest sent to configured recipients
  - Local HTML file saved for auditing
"""

import os
import re
import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from html import unescape
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# CONFIGURATION — only 3 secrets needed
# ---------------------------------------------------------------------------
SAM_API_KEY       = os.environ.get("SAM_API_KEY", "")
SENDGRID_API_KEY  = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_TO          = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
EMAIL_FROM        = os.environ.get("EMAIL_FROM", "mike.kelly@peregrine.io")

# Debug output — printed in GitHub Actions logs (secrets are masked automatically)
print(f"[Config] SAM_API_KEY set:      {'YES' if SAM_API_KEY else 'NO - SAM.gov results will be empty'}")
print(f"[Config] SENDGRID_API_KEY set: {'YES' if SENDGRID_API_KEY else 'NO - will fail at send step'}")
print(f"[Config] EMAIL_TO:             {EMAIL_TO}")
print(f"[Config] EMAIL_FROM:           {EMAIL_FROM}")

HEADERS = {
    "User-Agent": "PeregrineOpportunityScanner/2.0 (federal procurement research; contact@peregrine.io)",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# PEREGRINE CORE CAPABILITIES (grounded in actual product)
#
# Peregrine is a secure enterprise data integration and intelligence platform
# purpose-built for law enforcement, public safety, and corrections agencies.
# It does NOT provide: hardware, staffing, maintenance, construction, or
# general IT helpdesk. It IS: a SaaS data platform with analytics.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DATA CLASS
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field as dc_field

@dataclass
class Opportunity:
    title: str
    notice_id: str
    agency: str
    posted_date: str
    response_date: str
    description: str
    url: str
    opp_type: str
    source: str
    naics: str = ""
    score: int = 0
    score_reasons: list = dc_field(default_factory=list)
    tier: str = ""


# ---------------------------------------------------------------------------
# DATE UTILITIES
# ---------------------------------------------------------------------------
def parse_date_flexible(date_str: str):
    """Try multiple date formats and return a datetime or None."""
    if not date_str or date_str in ("TBD", "N/A", "See posting",
            "Watch for recompete", "See event page for registration deadline",
            "Monitor for follow-on procurement"):
        return None
    fmts = [
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y",
    ]
    clean = date_str.strip()[:25]
    for fmt in fmts:
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None

def is_expired(opp) -> bool:
    """
    Return True ONLY if the response deadline has clearly passed.
    Only checks response_date — never posted_date (which is always in the past).
    If response_date is TBD/unparseable, assume still active.
    """
    grace = datetime.utcnow() - timedelta(days=2)
    dt = parse_date_flexible(opp.response_date)
    if dt:
        return dt < grace
    # TBD or unparseable deadline = assume still open
    return False

def clean_url(url: str, fallback: str = "") -> str:
    """
    Validate and clean a URL. Returns the URL if valid, fallback otherwise.
    Ensures URLs start with http/https, strips whitespace, and handles
    common malformed patterns from API responses.
    """
    if not url:
        return fallback
    url = url.strip()
    # Must start with http or https
    if not url.startswith(("http://", "https://")):
        # Try prepending https
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("www."):
            url = "https://" + url
        else:
            return fallback
    # Basic sanity — no spaces, reasonable length
    if " " in url or len(url) > 2000:
        return fallback
    return url


# Peregrine's 6 core capability areas — what it actually sells and deploys
CAPABILITY_CLUSTERS = [
    (
        # Peregrine unifies siloed data from multiple systems into one platform
        "Data Integration & Unification", 20,
        [
            # Core phrases
            "data integration", "data unification", "data fusion",
            "disparate systems", "disparate data", "data silos",
            "siloed data", "data harmonization", "fragmented data",
            "enterprise data platform", "data integration platform",
            "unified data", "unified platform", "data consolidation",
            "information integration", "information sharing",
            "master data management", "data normalization",
            "data ingestion", "data pipeline", "data fabric",
            "data lake", "data warehouse", "data mesh",
            # Shorter triggers that appear in real titles
            "data analytics", "analytics platform", "analytics tool",
            "data management", "data management platform",
            "data management system", "data solution",
            "data platform", "data environment",
            "analytics solution", "analytics service",
            "reporting tool", "reporting platform",
            "dashboard", "business intelligence",
            "software platform", "enterprise software",
            "cloud platform", "cloud solution", "cloud-based",
        ],
    ),
    (
        # Peregrine surfaces connections, patterns, and insights for investigators
        "Investigative & Operational Analytics", 20,
        [
            # Core phrases
            "investigative analytics", "investigative platform",
            "investigative tool", "investigative system",
            "link analysis", "relationship mapping",
            "situational awareness", "operational intelligence",
            "operational dashboard", "pattern of life",
            "geospatial analysis", "geospatial intelligence",
            "crime analytics", "crime analysis",
            "advanced analytics", "intelligence platform",
            "intelligence system", "real-time analytics",
            "predictive analytics", "predictive policing",
            "common operating picture",
            # Shorter triggers
            "investigation management", "case analytics",
            "operational analysis", "mission analytics",
            "visualization", "geospatial", "mapping platform",
            "predictive", "intelligence analysis",
            # Digital evidence — DOJ DERP and similar platforms
            "digital evidence", "evidence review platform",
            "evidence analytics", "evidence management platform",
            "media review platform", "digital forensics platform",
            "investigative data platform",
        ],
    ),
    (
        # Peregrine lets users search across multiple connected systems at once
        "Federated & Enterprise Search", 20,
        [
            "federated search", "enterprise search",
            "cross-system search", "unified search",
            "search across", "search multiple",
            "search and retrieval", "information retrieval",
            "search capability", "search platform",
            "search solution", "search system",
            "knowledge retrieval", "query across",
            "semantic search", "full-text search",
            "document search", "content search",
        ],
    ),
    (
        # Peregrine deduplicates and resolves records across systems
        "Entity Resolution & Record Intelligence", 20,
        [
            "entity resolution", "record deduplication",
            "record linkage", "duplicate records",
            "identity resolution", "entity matching",
            "data deduplication", "entity-centric",
            "record consolidation", "ontology",
            "knowledge graph", "graph analytics",
            "relationship graph", "master record",
            "person record", "record resolution",
            "deduplication", "entity management",
        ],
    ),
    (
        # Peregrine is FedRAMP-authorized, CJIS-compliant, runs on AWS GovCloud
        "Secure Government SaaS", 15,
        [
            "fedramp", "cjis", "nist 800-53", "nist sp 800",
            "govcloud", "zero trust", "icam",
            "saml", "single sign-on", "sso",
            "role-based access", "rbac",
            "attribute-based access", "abac",
            "section 508", "audit logging",
            "authority to operate", "ato",
            "cloud security", "secure cloud",
            "government cloud", "cloud compliance",
        ],
    ),
    (
        # Public Safety & Law Enforcement — must imply SOFTWARE/DATA need,
        # not just any law enforcement adjacent work. "police" alone matches
        # vehicle purchases, uniforms, etc. Require compound terms that signal
        # a technology or data platform requirement.
        "Public Safety & Law Enforcement", 20,
        [
            # Specific Peregrine integrations (always relevant)
            "nibin", "etrace", "crime gun", "ballistic intelligence",
            "cgic", "crime gun intelligence",
            # Platform/system terms — imply software procurement
            "records management system", "records management software",
            "computer-aided dispatch", "computer aided dispatch", "cad system", "cad software",
            "law enforcement platform", "law enforcement software",
            "law enforcement analytics", "law enforcement data",
            "law enforcement technology", "law enforcement information",
            "public safety platform", "public safety software",
            "public safety technology", "public safety data",
            "public safety analytics", "public safety system",
            "policing platform", "policing software",
            "fusion center", "fusion center platform",
            "criminal justice platform", "criminal justice software",
            "criminal justice information system", "criminal justice data",
            "crime analytics", "crime data", "crime intelligence",
            "evidence management system", "evidence management platform",
            "investigation platform", "investigative software",
            "body camera data", "body camera analytics",
        ],
    ),
    (
        # Peregrine is deployed for probation/parole agencies (CSOSA use case)
        "Corrections & Community Supervision", 20,
        [
            "community supervision", "probation", "parole",
            "reentry", "offender management",
            "supervision officer", "court services",
            "pretrial", "case supervision",
            "csosa", "bureau of prisons",
            "department of corrections",
            "recidivism", "offender data",
            "supervision platform", "smart21",
            "supervised release",
            "correctional software", "correctional platform",
            "correctional data", "correctional analytics",
            "offender tracking", "supervision software",
            "supervision system", "case management",
            "supervision platform", "supervision system", "supervision software",
            "supervision analytics", "offender supervision",
        ],
    ),
    (
        # Peregrine replaces legacy and incumbent platforms like Palantir
        "Platform Modernization & Replacement", 20,
        [
            "palantir", "palantir replacement",
            "palantir alternative", "gotham", "foundry",
            "ibm i2", "platform replacement",
            "incumbent replacement", "platform consolidation",
            "legacy platform", "legacy system",
            "legacy modernization", "platform modernization",
            "platform migration", "technology refresh",
            "system modernization",             "data platform upgrade",
            "it modernization", "digital transformation",
            "software modernization", "cloud migration",
            "application modernization",
            "platform modernization", "legacy modernization",
            "system modernization", "data modernization",
            "technology modernization", "network modernization",
        
            "commercial solutions opening", "other transaction authority",
            "sovereign cloud", "defense cloud",],
    ),
    (
        # Peregrine embeds AI/ML for investigative decision support
        "AI & Machine Learning", 22,
        [
            "artificial intelligence", "machine learning",
            "ai/ml", "ai platform", "ai solution",
            "ai system", "ai services",
            # Space-padded to prevent substring matches like "regenerative" → "generative ai"
            " generative ai", "generative ai ",
            "large language model", " llm ",
            "natural language processing", " nlp ",
            "computer vision", "predictive model",
            "decision support", "decision support system",
            "automated analysis", "intelligent automation",
            "ai-powered", "ai-driven",
            "ai for law enforcement", "ai public safety",
            "responsible ai", "explainable ai",
            "ai governance", "ai analytics",
        ],
    ),
]

# NAICS hints — infer capability when SAM.gov description is blank

# Hard exclusions — ONLY work that has zero software/data component
# Keep very specific to avoid blocking legitimate IT solicitations
HARD_EXCLUSIONS = [
    # Maintenance & repair (the big new addition)
    "maintenance and repair", "repair and maintenance", "maintenance services only",
    "equipment repair", "equipment maintenance", "preventive maintenance",
    "corrective maintenance", "vehicle repair", "vehicle maintenance",
    "facility maintenance", "building maintenance", "hvac maintenance",
    "elevator maintenance", "generator maintenance", "engine repair",
    "aircraft maintenance", "ship repair", "vessel maintenance",
    # Physical facilities
    "janitorial services", "landscaping services", "custodial services",
    "grounds maintenance", "pest control services", "roofing services",
    "flooring installation", "plumbing services", "painting services",
    # Hardware-only procurement (specific phrases)
    "hardware procurement", "hardware purchase", "purchase of laptops",
    "purchase of desktops", "purchase of servers", "purchase of tablets",
    "network cabling", "structured cabling", "body-worn camera purchase",
    "body camera hardware", "purchase of radios", "radio hardware",
    "purchase of body armor", "ballistic vest", "purchase of firearms",
    "ammunition procurement", "vehicle purchase", "fleet vehicle acquisition",
    "drone procurement", "uav procurement", "sensor hardware purchase",
    # Food & clothing
    "food service contract", "food supply", "clothing procurement",
    "uniform procurement", "laundry services",
    # Medical / pharma (non-IT)
    "pharmaceutical procurement", "drug manufacturing", "medical supply",
    "laboratory reagent", "clinical trial services",
    # Construction & infrastructure projects
    "construction project", "construction contract", "construction services",
    "design and construction", "build and construction", "new construction",
    "renovation project", "renovation contract", "building renovation",
    "infrastructure construction", "facility construction",
    "construction management", "general contractor",
    "design-build", "design build", "architect and engineer",
    # Logistics
    "refuse collection", "moving services", "freight services",
    "shipping contract",
    # Professional services unrelated to Peregrine
    "translation services", "interpretation services",
    "attorney services", "legal representation",
    "financial audit services", "accounting services",
    # Hardware & equipment procurement — Peregrine is software only
    "purchase of equipment", "equipment procurement", "equipment acquisition",
    "hardware and equipment", "purchase hardware",
    "body armor", "protective equipment procurement",
    "weapon system", "weapons system", "small arms",
    "radio procurement", "radio acquisition", "portable radio",
    "vehicle acquisition", "vehicle procurement", "fleet acquisition",
    "license plate reader", "lpr procurement",
    "surveillance camera", "camera system procurement",
    "biometric device", "biometric hardware",
    "taser procurement", "less lethal",
    "furniture procurement", "office furniture",
    "it equipment purchase", "computer equipment purchase",
    "printer procurement", "copier procurement",
    "mobile device procurement", "tablet procurement",
    # Staffing-only contracts
    "staffing services", "staff augmentation", "labor category",
    "temporary staffing", "personnel services contract",
    "security guard", "guard services", "physical security services",
    # Training-only (not software training)
    "firearms training", "defensive tactics", "use of force training",
    "physical fitness", "k-9 training", "canine training",
    # Equipment rental and physical goods
    "equipment rental", "rental of equipment", "equipment lease",
    "air compressor", "generator rental", "forklift rental",
    "heavy equipment", "construction equipment",
    "medical equipment", "laboratory equipment",
    "audio visual equipment", "av equipment",
    "office equipment rental",
    # Physical goods procurement
    "purchase of supplies", "office supplies",
    "janitorial supplies", "cleaning supplies",
    # Hardware devices — tablets, phones, computers
    "tablets", "tablet procurement", "tablet purchase",
    "mobile devices", "smartphones", "cell phones",
    "laptops", "desktops", "workstations",
    "printers", "copiers", "scanners",
    # Physical facilities and infrastructure — not software
    "fire suppression", "fire alarm", "fire protection",
    "audio system", "audio visual", "av system",
    "hvac", "plumbing", "electrical system",
    "roof replacement", "flooring replacement", "window replacement",
    "elevator", "escalator", "generator replacement",
    "lighting system", "lighting replacement",
    "physical security system", "access control hardware",
    "camera installation", "cctv installation",
    # Maintenance contracts — not software development
    "annual maintenance", "annual software maintenance",
    "software maintenance agreement", "maintenance and support contract",
    "hardware maintenance", "software assurance",
    # Embassy / consular / facilities
    "embassy", "consular", "chancery",
    # Military hardware, aircraft, weapons systems — not Peregrine's market
    "crypto modernization", "cryptographic", "encryption hardware",
    "router solution", "b-52", "aircraft", "avionics",
    "missile", "munitions", "ammunition", "ordnance",
    "radar", "sonar", "weapons system", "armament",
    "c-130", "f-35", "v-22", "helicopter",
    "ship", "submarine", "vessel",
    "military vehicle", "tactical vehicle",
    # Network / telecom / infrastructure — not software
    "vpn", "ethernet", "transport services", "network infrastructure",
    "telecommunications", "telecom services", "internet service provider",
    "network cabling", "structured cabling", "fiber optic",
    "wireless network", "cellular services", "satellite services",
    "bandwidth services", "circuit services", "wan services",
    "network connectivity", "connectivity services",
    # Hardware support & maintenance agreements — not software
    "maintenance agreement", "service agreement hardware",
    "network server", "server maintenance", "server hardware",
    "hardware support", "hardware maintenance agreement",
    "pma maintenance", "preventive maintenance agreement",
    "network equipment", "server equipment",
    "storage hardware", "storage array",
    "firewall hardware", "switch hardware", "router hardware",
    "data center hardware", "rack hardware",
]

# Penalty signals — mismatch indicators (reduce score but don't exclude)
PENALTY_SIGNALS = [
    ("staffing augmentation", -8),
    ("time and materials labor", -6),
    ("independent verification and validation", -6),
    ("iv&v services", -6),
    ("penetration testing only", -5),
]

# NAICS prefix → capability hints for scoring when description is blank
NAICS_CAPABILITY_HINTS = {
    "513":    "software platform data management analytics",
    "541511": "software development platform custom application",
    "541512": "computer systems design technology platform",
    "541519": "computer services it solution technology",
    "518210": "data processing hosting cloud platform analytics",
    "541690": "technical consulting analytics data solution",
    "922":    "law enforcement criminal justice public safety",
    "922110": "courts criminal justice case management",
    "922120": "police law enforcement public safety records",
    "922150": "probation parole corrections supervision offender",
    "922190": "public safety justice corrections law enforcement",
    "923":    "corrections supervision justice case management",
}

def score_opportunity(opp: Opportunity) -> Opportunity:
    """
    Score based on capability match. Permissive — surfaces anything that could
    plausibly involve Peregrine's platform. Uses NAICS hints when description
    is empty (common with SAM.gov search API).
    """
    # Build enriched text including NAICS-derived capability hints
    naics_hint = ""
    if opp.naics:
        for prefix, hint in NAICS_CAPABILITY_HINTS.items():
            if opp.naics.startswith(prefix):
                naics_hint = hint
                break
    # Score only against title + description + NAICS hints
    # Agency name intentionally excluded — an agency match alone is not a capability fit
    text = f" {opp.title} {opp.description} {naics_hint} ".lower()  # padded for word-boundary phrase matching
    # Keep agency text separate for display only
    agency_text = opp.agency.lower()
    for excl in HARD_EXCLUSIONS:
        if excl.lower() in text:
            opp.score = -1
            opp.tier = "⛔ Not a Fit"
            opp.score_reasons = [f"Excluded: unrelated work (contains '{excl}')"]
            return opp

    # ── 2. Expired opportunity check ─────────────────────────────────────────
    if is_expired(opp):
        opp.score = -1
        opp.tier = "⛔ Expired"
        opp.score_reasons = [f"Response deadline has passed ({opp.response_date})"]
        return opp

    # ── 3. Capability cluster matching ───────────────────────────────────────
    score = 0
    reasons = []
    clusters_matched = 0
    title_only = opp.title.lower()
    saas_hits = []  # Track SaaS hits separately — only count if core cluster also matched

    for cap_name, cap_points, phrases in CAPABILITY_CLUSTERS:
        # Always check title independently — SAM.gov often has rich titles but empty descriptions
        # A title match alone is always meaningful and should always score
        title_hits = [p for p in phrases if p.lower() in title_only]
        desc_hits  = [p for p in phrases if p.lower() in text]
        # Merge, deduplicate, prefer longer (more specific) phrases
        all_hits = list({p: None for p in (title_hits + desc_hits)}.keys())

        if not all_hits:
            continue

        # Flag if this was a title-only match so we can note it
        title_only_match = bool(title_hits) and not bool(desc_hits)

        # Secure SaaS cluster: defer — only count if a core cluster also matched
        if cap_name.startswith("Secure Government SaaS"):
            saas_hits = all_hits
            continue

        score += cap_points
        clusters_matched += 1
        top_hits = sorted(all_hits, key=len, reverse=True)[:3]
        source_note = " (title match)" if title_only_match else ""
        reasons.append(f"✓ {cap_name}: matched '{top_hits[0]}'{source_note}" +
                      (f" + {len(all_hits)-1} more" if len(all_hits) > 1 else ""))

    # Now add SaaS score — but ONLY if at least one core capability cluster matched
    if saas_hits and clusters_matched >= 1:
        score += 15
        clusters_matched += 1
        top = sorted(saas_hits, key=len, reverse=True)[0]
        reasons.append(f"✓ Secure Govt SaaS context: '{top}' (with core capability match)")

    # ── 4. Penalty signals ───────────────────────────────────────────────────
    for signal, penalty in PENALTY_SIGNALS:
        if signal.lower() in text:
            score += penalty
            reasons.append(f"⚠ Penalty: '{signal}' suggests partial mismatch ({penalty} pts)")

    # ── 5. Assign tier — purely capability-based, no bonuses ────────────────
    # Strong Fit = 2+ clusters matched (40+ pts)
    # Good Fit   = 1 cluster matched  (15-39 pts)
    # Possible   = partial signal      (1-14 pts)
    if score >= 40:
        tier = "🟢 Strong Fit"
    elif score >= 15:
        tier = "🟡 Good Fit"
    elif score > 0:
        tier = "🔵 Possible Fit"
    else:
        tier = "⚪ Low Fit"

    opp.score = max(score, 0)
    opp.tier = tier
    opp.score_reasons = reasons if reasons else [
        "No clear capability match — review manually"
    ]
    return opp

# ---------------------------------------------------------------------------
# SOURCE 1: SAM.gov (all agency-targeted searches in one function)
# Public API key limit: 1,000 calls/day. This function uses ~55 calls total.
# ---------------------------------------------------------------------------

_SAM_RATE_LIMITED = [False]  # global flag — stops all SAM calls on 429
_SAM_RESULTS_CACHE: list = []  # shared cache — DOJ/DHS filter from this

def _sam_search(extra_params: dict, label: str,
                seen_ids: set, results: list,
                pages: int = 1) -> bool:
    """SAM.gov search with optional pagination. Returns False if rate limited."""
    if _SAM_RATE_LIMITED[0]:
        return False
    for page in range(pages):
        if _SAM_RATE_LIMITED[0]:
            break
        try:
            params = {"api_key": SAM_API_KEY,
                      "limit": 100, "offset": page * 100, **extra_params}
            r = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params=params, headers=HEADERS, timeout=15,
            )
            if r.status_code == 429:
                print(f"[SAM.gov] Rate limit — stopping ({label})")
                _SAM_RATE_LIMITED[0] = True
                return False
            if r.status_code != 200:
                return True
            data  = r.json()
            items = data.get("opportunitiesData", [])
            new_count = 0
            for item in items:
                nid = item.get("noticeId") or item.get("id") or ""
                if not nid or nid in seen_ids:
                    continue
                seen_ids.add(nid)
                new_count += 1
                results.append(score_opportunity(Opportunity(
                    title         = item.get("title", "Untitled"),
                    notice_id     = nid,
                    agency        = (item.get("fullParentPathName")
                                     or item.get("departmentName") or "Unknown"),
                    posted_date   = item.get("postedDate", ""),
                    response_date = item.get("responseDeadLine", "TBD"),
                    description   = (item.get("description") or "")[:2000],
                    url           = clean_url(f"https://sam.gov/opp/{nid}/view",
                                              "https://sam.gov/search"),
                    opp_type      = item.get("type") or "Notice",
                    source        = "SAM.gov",
                    naics         = item.get("naicsCode", ""),
                )))
            if new_count:
                print(f"[SAM.gov] {label} p{page+1}: {data.get('totalRecords',0)} total | {new_count} new")
            if len(items) < 100:
                break  # no more pages
            time.sleep(0.2)
        except Exception as e:
            print(f"[SAM.gov] {label}: {e}")
            return True
    time.sleep(0.2)
    return True


def fetch_sam_gov() -> list[Opportunity]:
    """
    Comprehensive SAM.gov fetch — covers ALL federal agencies.

    Strategy:
      Pass 1: Paginated ptype sweeps (no agency filter) — catches everything
              posted in last 30 days across all agencies. 2 pages per ptype
              = up to 200 results per notice type.
      Pass 2: Capability title searches across all agencies — 90-day window,
              paginated for high-volume terms. Catches older opps and anything
              that fell outside the ptype page limit.
      Pass 3: Broad keyword sweep for Peregrine-specific terms that wouldn't
              appear in generic ptype sweeps.

    Results are cached for DOJ/DHS/DoD to post-filter at zero extra cost.
    """
    if not SAM_API_KEY:
        print("[SAM.gov] No API key — skipping")
        return []

    results, seen_ids = [], set()
    today   = datetime.utcnow()
    to_date = today.strftime("%m/%d/%Y")
    d30     = (today - timedelta(days=30)).strftime("%m/%d/%Y")
    d90     = (today - timedelta(days=90)).strftime("%m/%d/%Y")

    # ── Pass 1: Paginated ptype sweeps — ALL agencies, last 30 days ───────────
    # 4 pages × 100 = up to 400 per notice type. Covers ~2400 recent notices.
    for ptype, lbl in [
        ("r", "Sources Sought"),
        ("p", "Presolicitation"),
        ("k", "Combined Synopsis"),
        ("s", "Special Notice"),
        ("o", "Solicitation"),
        ("i", "Intent to Bundle"),
    ]:
        if not _sam_search({"ptype": ptype, "postedFrom": d30, "postedTo": to_date},
                           lbl, seen_ids, results, pages=4):
            break

    # ── Pass 2a: Broad keyword searches — catches opps that title= misses ──────
    # SAM.gov keyword= searches title AND description, wider net than title=
    KEYWORD_SEARCHES = [
        # Specific enough to return <100 results each
        ("data management solutions",    1),
        ("sovereign cloud",              1),
        ("commercial solutions opening", 1),
        ("defense cloud",                1),
        ("federated search",             1),
        ("investigative analytics",      1),
        ("entity resolution",            1),
        ("crime gun intelligence",       1),
        ("fedramp high",                 1),
        ("zero trust data",              1),
        ("law enforcement analytics",    1),
        ("offender management system",   1),
        ("community supervision",        1),
        ("intelligence platform",        1),
        ("data integration platform",    1),
        ("records management system",    1),
    ]
    for term, pages in KEYWORD_SEARCHES:
        if _SAM_RATE_LIMITED[0]:
            break
        _sam_search({"keyword": term, "postedFrom": d90, "postedTo": to_date},
                    f"kw={term}", seen_ids, results, pages=pages)

    # ── Pass 2b: Capability title searches — ALL agencies, 90-day window ──────
    TITLE_SEARCHES = [
        # Specific compound phrases — low volume, 1 page sufficient
        ("investigative platform",     1),
        ("community supervision",      1),
        ("digital evidence",           1),
        ("federated search",           1),
        ("law enforcement analytics",  1),
        ("public safety platform",     1),
        ("entity resolution",          1),
        ("crime analytics",            1),
        ("offender management",        1),
        ("records management system",  1),
        ("enterprise data",            1),
        ("data environment",           1),
        ("data fabric",                1),
        ("zero trust analytics",       1),
        ("fedramp analytics",          1),
        ("investigative analytics",    1),
        ("intelligence platform",      1),
        ("identity resolution",        1),
        ("record deduplication",       1),
        ("crime gun intelligence",     1),
        ("body camera analytics",      1),
        ("corrections platform",       1),
        ("fusion center",              1),
        ("platform replacement",       1),
        ("computer vision analytics",  1),
        ("surveillance analytics",     1),
        ("data unification",           1),
        ("information sharing platform", 1),
        # High-volume terms — paginate to catch deeper results
        ("data analytics",             3),
        ("data integration",           3),
        ("IT modernization",           2),
        ("artificial intelligence",    3),
        ("machine learning",           2),
        ("platform modernization",     2),
        ("predictive analytics",       2),
        ("data management",            3),
        ("data management solutions",    1),
        ("digital transformation",     2),
        ("sovereign cloud",               1),
        ("defense cloud",                 1),
        ("commercial solutions opening",  1),
        ("enterprise analytics",          1),
        ("data science platform",         1),
        ("cross domain solution",         1),
    ]
    for term, pages in TITLE_SEARCHES:
        if _SAM_RATE_LIMITED[0]:
            break
        if not _sam_search({"title": term, "postedFrom": d90, "postedTo": to_date},
                           f"title={term}", seen_ids, results, pages=pages):
            break

    # ── Pass 3: Response deadline search — catches open notices regardless ──────
    # of posted date or active status. Searches for notices still accepting
    # responses. This is the most reliable way to find currently open opps.
    today_str  = today.strftime("%m/%d/%Y")
    future_str = (today + timedelta(days=180)).strftime("%m/%d/%Y")
    
    DEADLINE_TERMS = [
        "data management solutions",
        "sovereign cloud",
        "commercial solutions opening",
        "data management platform",
        "investigative analytics",
        "law enforcement analytics",
        "intelligence platform",
        "data integration platform",
        "federated search",
        "offender management system",
        "records management system",
    ]
    for term in DEADLINE_TERMS:
        if _SAM_RATE_LIMITED[0]:
            break
        _sam_search(
            {"title": term, "rdlfrom": today_str, "rdlto": future_str},
            f"rdl={term}", seen_ids, results, pages=1
        )

    # ── Pass 4: Direct notice ID lookups — guaranteed catch of known opps ─────
    # noticeid= fetches a SPECIFIC notice regardless of date, active, or archive
    WATCH_LIST = [
        "b2910bda98f342149cd76c39de3038c6",  # Data Management Solutions — FBI
        "55c0c5ea5ef84232869c0134386dfa48",  # Sovereign Defense Cloud — ERDC
        "70e476afd4584a63a9890f0071e4871e",  # Additional notice
        "d32237c586bc45489644f757c52faa22",  # FBI CJIS Decentralized Info Sharing RFI
    ]
    for nid in WATCH_LIST:
        if nid in seen_ids or _SAM_RATE_LIMITED[0]:
            continue
        try:
            r = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params={"api_key": SAM_API_KEY, "noticeid": nid,
                        "postedFrom": "01/01/2020", "postedTo": today_str},
                headers=HEADERS, timeout=15,
            )
            if r.status_code == 200:
                for item in r.json().get("opportunitiesData", []):
                    item_nid = item.get("noticeId") or item.get("id") or ""
                    if not item_nid or item_nid in seen_ids:
                        continue
                    seen_ids.add(item_nid)
                    # Fetch actual description text
                    desc_text = ""
                    desc_url  = item.get("description") or ""
                    if desc_url and desc_url.startswith("http"):
                        try:
                            dr = requests.get(f"{desc_url}&api_key={SAM_API_KEY}",
                                              headers=HEADERS, timeout=10)
                            if dr.status_code == 200:
                                import html as _html
                                desc_text = re.sub(r"<[^>]+>", " ",
                                    _html.unescape(dr.text))[:3000]
                        except Exception:
                            pass
                    opp = score_opportunity(Opportunity(
                        title         = item.get("title", "Untitled"),
                        notice_id     = item_nid,
                        agency        = item.get("fullParentPathName") or "Unknown",
                        posted_date   = item.get("postedDate", ""),
                        response_date = item.get("responseDeadLine") or
                                        item.get("reponseDeadLine", "TBD"),
                        description   = desc_text or (item.get("description") or "")[:2000],
                        url           = clean_url(f"https://sam.gov/opp/{item_nid}/view",
                                                  "https://sam.gov/search"),
                        opp_type      = item.get("type") or "Notice",
                        source        = "SAM.gov",
                        naics         = item.get("naicsCode", ""),
                    ))
                    results.append(opp)
                    print(f"[SAM.gov] Watch: {item.get('title','?')[:60]} | score={opp.score}")
        except Exception as e:
            print(f"[SAM.gov] Watch {nid[:8]}: {e}")

    # Cache for DOJ/DHS/DoD post-filtering — zero extra API calls
    _SAM_RESULTS_CACHE.clear()
    _SAM_RESULTS_CACHE.extend(results)
    print(f"[SAM.gov] {len(results)} total opportunities across all agencies")
    return results



# DOD agency path fragments
DOD_PATH_FRAGMENTS = [
    "national guard", "national guard bureau", "ngb",
    "defense information systems", "disa",
    "defense intelligence agency", "dia",
    "defense logistics agency", "dla",
    "defense advanced research", "darpa",
    "engineer research and development", "erdc",
    "army corps of engineers", "usace",
    "army futures command",
    "army research laboratory", "arl",
    "office of the secretary of defense", "osd",
    "defense contract", "defense finance",
    "defense threat reduction", "dtra",
    "special operations command", "socom",
    "engineer research and development", "erdc", "erdc werx",
    "army corps of engineers", "usace",
    "army research laboratory", "arl",
    "defense threat reduction", "dtra",
    "special operations command", "socom",
    "army", "navy", "air force", "marine corps", "space force",
    "joint chiefs", "combatant command",
]
DOJ_PATH_FRAGMENTS = [
    "department of justice", "dept of justice",
    "alcohol, tobacco", "atf",
    "federal bureau of investigation", "fbi",
    "drug enforcement administration", "dea",
    "bureau of prisons", "bop",
    "office of justice programs", "ojp",
    "court services and offender", "csosa",
    "community oriented policing", "cops office",
    "u.s. marshals", "marshals service",
    "executive office for united states attorneys",
    "national security division",
]
DHS_PATH_FRAGMENTS = [
    "homeland security", "dhs",
    "customs and border protection", "cbp",
    "immigration and customs enforcement", "ice",
    "coast guard", "uscg",
    "cybersecurity and infrastructure", "cisa",
    "federal emergency management", "fema",
    "transportation security administration", "tsa",
    "secret service", "usss",
    "citizenship and immigration services", "uscis",
    "federal law enforcement training", "fletc",
]
AGENCY_SEARCH_TERMS = [
    "data integration", "data analytics platform", "data management platform",
    "enterprise data platform", "data unification", "information sharing platform",
    "investigative analytics", "crime analytics", "law enforcement analytics",
    "intelligence platform", "link analysis", "digital evidence",
    "evidence management", "situational awareness", "operational intelligence",
    "federated search", "enterprise search", "cross-system search",
    "entity resolution", "record deduplication", "identity resolution",
    "data deduplication", "fedramp", "cjis", "govcloud", "zero trust",
    "law enforcement platform", "public safety platform",
    "records management system", "fusion center", "crime gun intelligence",
    "body camera analytics", "community supervision", "offender management",
    "probation", "corrections platform", "court services",
    "IT modernization", "platform modernization", "legacy modernization",
    "platform replacement", "digital transformation",
    "artificial intelligence", "machine learning", "predictive analytics",
    "computer vision", "AI platform",
]


def _is_dod(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DOD_PATH_FRAGMENTS)


def _is_doj(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DOJ_PATH_FRAGMENTS)


def _is_dhs(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DHS_PATH_FRAGMENTS)


def fetch_doj_opportunities() -> list:
    """
    Fetch ALL notices from DOJ sub-agencies and score them locally.
    
    KEY INSIGHT: SAM.gov API only searches titles. Many great opportunities
    like the FBI CJIS RFI have generic titles ("RFI - Information Sharing")
    but highly relevant descriptions. The only reliable way to catch them
    is to fetch EVERYTHING from these agencies and let our scoring engine
    find the gems.
    
    Each agency gets its own sweep: all ptypes, 90-day window.
    ~14 API calls total — very cheap, highly reliable.
    """
    if not SAM_API_KEY or _SAM_RATE_LIMITED[0]:
        # Fall back to cache filter
        from_cache = [o for o in _SAM_RESULTS_CACHE if _is_doj(o.agency)]
        print(f"[DOJ] {len(from_cache)} from SAM cache (no API key / rate limited)")
        return from_cache

    results  = []
    seen_ids = set(o.notice_id for o in _SAM_RESULTS_CACHE)  # don't re-add cache hits
    today    = datetime.utcnow()
    d90      = (today - timedelta(days=90)).strftime("%m/%d/%Y")
    to_date  = today.strftime("%m/%d/%Y")

    # Every DOJ sub-agency — fetch ALL their notices
    DOJ_AGENCIES = [
        "Federal Bureau of Investigation",
        "Alcohol, Tobacco, Firearms and Explosives",
        "Drug Enforcement Administration",
        "Bureau of Prisons",
        "U.S. Marshals Service",
        "Court Services and Offender Supervision Agency",
        "Executive Office for United States Attorneys",
        "National Security Division",
        "Office of Justice Programs",
        "Community Oriented Policing Services",
        "Justice Management Division",
    ]

    desc_fetches = 0  # cap description fetches to avoid timeout
    for agency in DOJ_AGENCIES:
        if _SAM_RATE_LIMITED[0]:
            break
        new_count = 0
        for page in range(3):  # up to 300 per agency
            if _SAM_RATE_LIMITED[0]:
                break
            try:
                r = requests.get(
                    "https://api.sam.gov/opportunities/v2/search",
                    params={
                        "api_key":          SAM_API_KEY,
                        "organizationName": agency,
                        "postedFrom":       d90,
                        "postedTo":         to_date,
                        "limit":            100,
                        "offset":           page * 100,
                    },
                    headers=HEADERS, timeout=20,
                )
                if r.status_code == 429:
                    print(f"[DOJ] Rate limited on {agency}")
                    _SAM_RATE_LIMITED[0] = True
                    break
                if r.status_code != 200:
                    break
                data  = r.json()
                items = data.get("opportunitiesData", [])
                total = data.get("totalRecords", 0)
                for item in items:
                    nid = item.get("noticeId") or item.get("id") or ""
                    if not nid or nid in seen_ids:
                        continue
                    seen_ids.add(nid)
                    new_count += 1
                    title_str = item.get("title", "Untitled")
                    # Quick title-only score first to decide if worth fetching desc
                    quick_opp = score_opportunity(Opportunity(
                        title=title_str, notice_id=nid,
                        agency=item.get("fullParentPathName") or agency,
                        posted_date=item.get("postedDate",""),
                        response_date=item.get("responseDeadLine") or "TBD",
                        description="", url="", opp_type="", source="SAM.gov",
                    ))
                    # Only fetch description if title scored OR agency is top-tier
                    TOP_AGENCIES = ["federal bureau of investigation", "cjis",
                                    "alcohol, tobacco", "bureau of prisons",
                                    "court services and offender"]
                    fetch_desc = (quick_opp.score > 0 or
                                  any(a in agency.lower() for a in TOP_AGENCIES))
                    desc_text = ""
                    if fetch_desc and desc_fetches < 30:
                        desc_url = item.get("description") or ""
                        if desc_url and desc_url.startswith("http"):
                            try:
                                dr = requests.get(
                                    f"{desc_url}&api_key={SAM_API_KEY}",
                                    headers=HEADERS, timeout=5)
                                if dr.status_code == 200:
                                    import html as _html
                                    desc_text = re.sub(r"<[^>]+>", " ",
                                        _html.unescape(dr.text))[:2000]
                                desc_fetches += 1
                            except Exception:
                                pass
                    opp = score_opportunity(Opportunity(
                        title         = title_str,
                        notice_id     = nid,
                        agency        = item.get("fullParentPathName") or agency,
                        posted_date   = item.get("postedDate", ""),
                        response_date = item.get("responseDeadLine") or
                                        item.get("reponseDeadLine", "TBD"),
                        description   = desc_text,
                        url           = clean_url(
                            f"https://sam.gov/opp/{nid}/view",
                            "https://sam.gov/search"),
                        opp_type      = item.get("type") or "Notice",
                        source        = "SAM.gov",
                        naics         = item.get("naicsCode", ""),
                    ))
                    results.append(opp)
                if len(items) < 100:
                    break
                time.sleep(0.15)
            except Exception as e:
                print(f"[DOJ] {agency}: {e}")
                break
        if new_count:
            print(f"[DOJ] {agency}: {new_count} opportunities")
        time.sleep(0.2)

    # Also add anything from the SAM cache we might have missed
    for o in _SAM_RESULTS_CACHE:
        if _is_doj(o.agency) and o.notice_id not in seen_ids:
            results.append(o)

    scored = [o for o in results if o.score > 0]
    print(f"[DOJ] Total: {len(results)} fetched, {len(scored)} scored relevant")
    return results


def fetch_dhs_opportunities() -> list:
    """Fetch ALL notices from DHS sub-agencies and score locally."""
    if not SAM_API_KEY or _SAM_RATE_LIMITED[0]:
        from_cache = [o for o in _SAM_RESULTS_CACHE if _is_dhs(o.agency)]
        print(f"[DHS] {len(from_cache)} from SAM cache")
        return from_cache

    results  = []
    seen_ids = set(o.notice_id for o in _SAM_RESULTS_CACHE)
    today    = datetime.utcnow()
    d90      = (today - timedelta(days=90)).strftime("%m/%d/%Y")
    to_date  = today.strftime("%m/%d/%Y")

    DHS_AGENCIES = [
        "Immigration and Customs Enforcement",
        "Customs and Border Protection",
        "Cybersecurity and Infrastructure Security Agency",
        "Transportation Security Administration",
        "Federal Emergency Management Agency",
        "United States Secret Service",
        "United States Citizenship and Immigration Services",
        "Federal Law Enforcement Training Centers",
        "Science and Technology Directorate",
        "Intelligence and Analysis",
        "Coast Guard",
    ]

    desc_fetches = 0
    for agency in DHS_AGENCIES:
        if _SAM_RATE_LIMITED[0]:
            break
        new_count = 0
        for page in range(3):
            if _SAM_RATE_LIMITED[0]:
                break
            try:
                r = requests.get(
                    "https://api.sam.gov/opportunities/v2/search",
                    params={
                        "api_key":          SAM_API_KEY,
                        "organizationName": agency,
                        "postedFrom":       d90,
                        "postedTo":         to_date,
                        "limit":            100,
                        "offset":           page * 100,
                    },
                    headers=HEADERS, timeout=20,
                )
                if r.status_code == 429:
                    _SAM_RATE_LIMITED[0] = True
                    break
                if r.status_code != 200:
                    break
                data  = r.json()
                items = data.get("opportunitiesData", [])
                for item in items:
                    nid = item.get("noticeId") or item.get("id") or ""
                    if not nid or nid in seen_ids:
                        continue
                    seen_ids.add(nid)
                    new_count += 1
                    title_str = item.get("title", "Untitled")
                    quick_opp = score_opportunity(Opportunity(
                        title=title_str, notice_id=nid,
                        agency=item.get("fullParentPathName") or agency,
                        posted_date=item.get("postedDate",""),
                        response_date=item.get("responseDeadLine") or "TBD",
                        description="", url="", opp_type="", source="SAM.gov",
                    ))
                    DHS_TOP = ["immigration and customs", "customs and border",
                               "cybersecurity", "secret service"]
                    fetch_desc = (quick_opp.score > 0 or
                                  any(a in agency.lower() for a in DHS_TOP))
                    desc_text = ""
                    if fetch_desc and desc_fetches < 30:
                        desc_url = item.get("description") or ""
                        if desc_url and desc_url.startswith("http"):
                            try:
                                dr = requests.get(
                                    f"{desc_url}&api_key={SAM_API_KEY}",
                                    headers=HEADERS, timeout=5)
                                if dr.status_code == 200:
                                    import html as _html
                                    desc_text = re.sub(r"<[^>]+>", " ",
                                        _html.unescape(dr.text))[:2000]
                                desc_fetches += 1
                            except Exception:
                                pass
                    opp = score_opportunity(Opportunity(
                        title         = title_str,
                        notice_id     = nid,
                        agency        = item.get("fullParentPathName") or agency,
                        posted_date   = item.get("postedDate", ""),
                        response_date = item.get("responseDeadLine") or
                                        item.get("reponseDeadLine", "TBD"),
                        description   = desc_text,
                        url           = clean_url(
                            f"https://sam.gov/opp/{nid}/view",
                            "https://sam.gov/search"),
                        opp_type      = item.get("type") or "Notice",
                        source        = "SAM.gov",
                        naics         = item.get("naicsCode", ""),
                    ))
                    results.append(opp)
                if len(items) < 100:
                    break
                time.sleep(0.15)
            except Exception as e:
                print(f"[DHS] {agency}: {e}")
                break
        if new_count:
            print(f"[DHS] {agency}: {new_count} opportunities")
        time.sleep(0.2)

    for o in _SAM_RESULTS_CACHE:
        if _is_dhs(o.agency) and o.notice_id not in seen_ids:
            results.append(o)

    scored = [o for o in results if o.score > 0]
    print(f"[DHS] Total: {len(results)} fetched, {len(scored)} scored relevant")
    return results


def fetch_dod_opportunities() -> list:
    from_cache = [o for o in _SAM_RESULTS_CACHE if _is_dod(o.agency)]
    if from_cache:
        print(f"[DoD] {len(from_cache)} opportunities (from SAM cache)")
    return from_cache


# ---------------------------------------------------------------------------
# SOURCE 2: FEDERAL REGISTER
# ---------------------------------------------------------------------------
def fetch_federal_register() -> list:
    results, seen_ids = [], set()
    today  = datetime.utcnow()
    since  = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    terms  = [
        "data analytics", "law enforcement analytics",
        "community supervision", "IT modernization",
        "investigative platform", "artificial intelligence",
        "digital evidence", "records management",
    ]
    for term in terms:
        try:
            params = (
                f"conditions[term]={requests.utils.quote(term)}"
                f"&conditions[publication_date][gte]={since}"
                f"&conditions[type][]=NOTICE&per_page=10&order=newest"
                f"&fields[]=document_number&fields[]=title&fields[]=abstract"
                f"&fields[]=publication_date&fields[]=agencies&fields[]=html_url"
            )
            r = requests.get(
                f"https://www.federalregister.gov/api/v1/documents.json?{params}",
                headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20,
            )
            if r.status_code != 200:
                continue
            for doc in r.json().get("results", []):
                doc_id = doc.get("document_number", "")
                if doc_id in seen_ids:
                    continue
                title = (doc.get("title", "") or "").strip()
                abstract = (doc.get("abstract", "") or "").strip()
                combined = f"{title} {abstract}".lower()
                if not any(s in combined for s in ["request for information", "sources sought",
                                                     "industry day", "market research", "rfi"]):
                    continue
                seen_ids.add(doc_id)
                agencies = ", ".join(a.get("name", "") for a in doc.get("agencies", []) if a.get("name"))
                opp = Opportunity(
                    title=title, notice_id=f"FR-{doc_id}",
                    agency=agencies or "Federal Agency",
                    posted_date=doc.get("publication_date", ""),
                    response_date="TBD",
                    description=abstract[:2000],
                    url=clean_url(doc.get("html_url", ""), "https://www.federalregister.gov"),
                    opp_type="Federal Register RFI", source="Federal Register",
                )
                results.append(score_opportunity(opp))
            time.sleep(0.2)
        except Exception as e:
            print(f"[FederalRegister] '{term}': {e}")
    print(f"[Federal Register] {len(results)} notices")
    return results


# ---------------------------------------------------------------------------
# SOURCE 3: USASPENDING — competitive intel
# ---------------------------------------------------------------------------
def fetch_usaspending_intel() -> list:
    results, seen_ids = [], set()
    today = datetime.utcnow()
    start = (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")
    batches = [
        ["law enforcement software"], ["data analytics"],
        ["community supervision"],    ["investigative platform"],
        ["palantir"],                 ["corrections software"],
    ]
    for keywords in batches:
        try:
            payload = {
                "subawards": False, "limit": 10, "page": 1,
                "filters": {
                    "keywords": keywords,
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": start, "end_date": end}],
                },
                "fields": ["Award ID", "Recipient Name", "Start Date", "End Date",
                           "Award Amount", "Awarding Agency", "Awarding Sub Agency",
                           "Description"],
                "sort": "Award Amount", "order": "desc",
            }
            r = requests.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            for award in r.json().get("results", []):
                aid = award.get("Award ID", "")
                nid = f"USA-{aid}"
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)
                amount    = award.get("Award Amount", 0) or 0
                recipient = award.get("Recipient Name", "Unknown")
                agency    = award.get("Awarding Agency", "")
                sub       = award.get("Awarding Sub Agency", "")
                desc      = (award.get("Description", "") or "")[:150]
                start_dt  = award.get("Start Date", "")
                end_dt    = award.get("End Date", "")
                opp = Opportunity(
                    title=f"[AWARD INTEL] {desc[:80] or 'Contract'} — {recipient}",
                    notice_id=nid,
                    agency=f"{agency} / {sub}",
                    posted_date=start_dt or end,
                    response_date="Watch for recompete",
                    description=(f"Award to {recipient} by {agency}. "
                                 f"Value: ${amount:,.0f}. Period: {start_dt} to {end_dt}. {desc}"),
                    url=clean_url(f"https://www.usaspending.gov/award/{aid}/",
                                  "https://www.usaspending.gov"),
                    opp_type="Award Intel", source="USASpending.gov",
                )
                results.append(score_opportunity(opp))
            time.sleep(0.5)
        except Exception as e:
            print(f"[USASpending] {keywords}: {e}")
    print(f"[USASpending] {len(results)} award intel records")
    return results


# ---------------------------------------------------------------------------
# SOURCE 4: AGENCY RSS FEEDS
# ---------------------------------------------------------------------------
def fetch_agency_rss_feeds() -> list:
    return []  # RSS feeds captured in industry news; this source reserved


# ---------------------------------------------------------------------------
# SOURCE 5: EVENTS INTELLIGENCE
# ---------------------------------------------------------------------------
KNOWN_EVENTS = [
    {"title": "IACP Annual Conference", "date": "2026-10-18", "location": "Boston, MA",
     "url": "https://www.theiacp.org/events/iacp-annual-conference",
     "tags": ["law enforcement", "public safety"]},
    {"title": "Corrections Technology Summit", "date": "2026-07-15", "location": "Nashville, TN",
     "url": "https://www.corrections.com", "tags": ["corrections", "supervision"]},
    {"title": "GovSec Conference", "date": "2026-06-10", "location": "Washington, DC",
     "url": "https://www.govsecinfo.com", "tags": ["government security", "law enforcement"]},
    {"title": "SEARCH Symposium", "date": "2026-05-20", "location": "New Orleans, LA",
     "url": "https://www.search.org", "tags": ["criminal justice", "technology"]},
]


def fetch_events_intelligence() -> list:
    results = []
    today   = datetime.utcnow()
    cutoff  = today + timedelta(days=90)
    for ev in KNOWN_EVENTS:
        try:
            ev_dt = datetime.strptime(ev["date"], "%Y-%m-%d")
            if ev_dt < today or ev_dt > cutoff:
                continue
            opp = Opportunity(
                title=ev["title"],
                notice_id=f"EVT-{ev['date']}-{ev['title'][:20].replace(' ','')}",
                agency="Industry Event",
                posted_date=today.strftime("%Y-%m-%d"),
                response_date=ev["date"],
                description=f"Industry event. Location: {ev.get('location', 'TBD')}",
                url=ev.get("url", ""),
                opp_type="Industry Day",
                source="Events Intelligence",
            )
            results.append(score_opportunity(opp))
        except Exception:
            pass
    print(f"[Events] {len(results)} upcoming events (next 90 days)")
    return results


# ---------------------------------------------------------------------------
# INDUSTRY NEWS
# ---------------------------------------------------------------------------
def fetch_industry_news() -> list[dict]:
    news   = []
    seen   = set()
    feeds  = [
        {"url": "https://fedscoop.com/feed/",                   "source": "FedScoop"},
        {"url": "https://www.nextgov.com/rss/all/",             "source": "Nextgov"},
        {"url": "https://gcn.com/rss-feeds/all.aspx",           "source": "GCN"},
        {"url": "https://www.govtech.com/public-safety/rss.xml","source": "GovTech"},
        {"url": "https://www.police1.com/rss/all/",             "source": "Police1"},
        {"url": "https://www.corrections1.com/rss/all/",        "source": "Corrections1"},
    ]
    keywords = [
        "law enforcement", "public safety", "data analytics", "artificial intelligence",
        "machine learning", "criminal justice", "corrections", "fedramp", "cjis",
        "records management", "predictive", "surveillance", "crime analytics",
    ]
    for feed in feeds:
        try:
            r = requests.get(feed["url"], headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:15]:
                t_el = item.find("title")
                l_el = item.find("link")
                d_el = item.find("description")
                p_el = item.find("pubDate")
                title = (t_el.text or "").strip() if t_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d_el.text or ""))).strip() if d_el is not None else ""
                url_  = (l_el.text or "").strip() if l_el is not None else ""
                date_ = (p_el.text or "").strip() if p_el is not None else ""
                if not title or title in seen:
                    continue
                combined = f"{title} {desc}".lower()
                if not any(kw in combined for kw in keywords):
                    continue
                seen.add(title)
                news.append({
                    "title": title, "url": clean_url(url_, ""),
                    "source": feed["source"], "date": date_[:16],
                    "summary": desc[:250],
                })
            time.sleep(0.2)
        except Exception as e:
            print(f"[IndustryNews] {feed['source']}: {e}")
    print(f"[Industry News] {len(news)} articles")
    return news[:15]


def fetch_growth_news() -> list[dict]:
    return []  # Placeholder — industry news covers this


# ---------------------------------------------------------------------------
# COMPETITOR INTELLIGENCE
# ---------------------------------------------------------------------------
COMPETITORS = [
    {"name": "Palantir",           "search": "Palantir law enforcement government",        "tags": ["palantir"]},
    {"name": "Axon",               "search": "Axon public safety technology",               "tags": ["axon"]},
    {"name": "ShotSpotter",        "search": "ShotSpotter gunshot detection",               "tags": ["shotspotter", "soundthinking"]},
    {"name": "Mark43",             "search": "Mark43 records management police",            "tags": ["mark43"]},
    {"name": "Tyler Technologies", "search": "Tyler Technologies criminal justice",         "tags": ["tyler technologies"]},
    {"name": "Motorola Solutions", "search": "Motorola Solutions public safety",            "tags": ["motorola solutions"]},
    {"name": "IBM i2",             "search": "IBM i2 law enforcement analytics",            "tags": ["ibm i2"]},
    {"name": "Esri",               "search": "Esri law enforcement government GIS",         "tags": ["esri"]},
    {"name": "Databricks",         "search": "Databricks government federal",               "tags": ["databricks"]},
    {"name": "Appriss",            "search": "Appriss corrections supervision",             "tags": ["appriss"]},
    {"name": "SuperCom",           "search": "SuperCom offender monitoring",                "tags": ["supercom"]},
    {"name": "Flock Safety",       "search": "Flock Safety license plate law enforcement", "tags": ["flock safety", "flock camera"]},
]

COMPETITOR_NEWS_FEEDS = [
    {"url": "https://fedscoop.com/feed/",                    "source": "FedScoop"},
    {"url": "https://www.nextgov.com/rss/all/",              "source": "Nextgov"},
    {"url": "https://gcn.com/rss-feeds/all.aspx",            "source": "GCN"},
    {"url": "https://www.govtech.com/public-safety/rss.xml", "source": "GovTech"},
    {"url": "https://www.police1.com/rss/all/",              "source": "Police1"},
    {"url": "https://www.corrections1.com/rss/all/",         "source": "Corrections1"},
    {"url": "https://defensescoop.com/feed/",                "source": "DefenseScoop"},
    {"url": "https://statescoop.com/feed/",                  "source": "StateScoop"},
]

COMPETITOR_NEWS_QUERIES = [
    ("Palantir",           "Palantir+federal+government+contract"),
    ("Axon",               "Axon+Enterprise+law+enforcement+technology"),
    ("ShotSpotter",        "ShotSpotter+OR+SoundThinking+police"),
    ("Mark43",             "Mark43+records+management+police"),
    ("Tyler Technologies", "Tyler+Technologies+public+safety+government"),
    ("Motorola Solutions", "Motorola+Solutions+law+enforcement+data"),
    ("IBM i2",             "IBM+i2+intelligence+analytics+government"),
    ("Esri",               "Esri+law+enforcement+public+safety+GIS"),
    ("Databricks",         "Databricks+government+law+enforcement+federal"),
    ("Appriss",            "Appriss+criminal+justice+data"),
    ("SuperCom",           "SuperCom+offender+monitoring+supervision"),
    ("Flock Safety",       "Flock+Safety+license+plate+law+enforcement"),
]


def fetch_competitor_intel() -> list[dict]:
    items_out  = []
    seen_titles = set()

    def _fetch_gnews(comp_name: str, query: str, max_items: int = 5):
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        out = []
        try:
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PeregrineScanner/2.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if r.status_code != 200:
                return out
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:max_items]:
                t_el = item.find("title")
                l_el = item.find("link")
                d_el = item.find("description")
                p_el = item.find("pubDate")
                title = (t_el.text or "").strip() if t_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d_el.text or ""))).strip() if d_el is not None else ""
                url_  = (l_el.text or "").strip() if l_el is not None else ""
                date_ = (p_el.text or "").strip() if p_el is not None else ""
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                out.append({
                    "competitor": comp_name,
                    "title": title,
                    "url": clean_url(url_, ""),
                    "source": "Google News",
                    "date": date_[:16] if date_ else "",
                    "summary": desc[:300],
                })
        except Exception as e:
            print(f"[CompetitorIntel] Google News {comp_name}: {e}")
        return out

    for comp_name, query in COMPETITOR_NEWS_QUERIES:
        items_out.extend(_fetch_gnews(comp_name, query, max_items=2))
        time.sleep(0.2)

    # USASpending recompetes
    recompete_targets = [
        ("Palantir",           ["palantir"]),
        ("Axon",               ["axon enterprise", "axon public safety"]),
        ("Tyler Technologies", ["tyler technologies"]),
        ("Motorola Solutions", ["motorola solutions"]),
        ("Mark43",             ["mark43"]),
        ("IBM i2",             ["ibm i2", "i2 analyst"]),
        ("ShotSpotter",        ["shotspotter", "soundthinking"]),
        ("Flock Safety",       ["flock safety"]),
    ]
    today    = datetime.utcnow()
    end_soon = (today + timedelta(days=365)).strftime("%Y-%m-%d")
    for comp_name, keywords in recompete_targets:
        try:
            payload = {
                "subawards": False, "limit": 5, "page": 1,
                "filters": {
                    "keywords": keywords,
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": "2020-01-01", "end_date": end_soon}],
                },
                "fields": ["Award ID", "Recipient Name", "Start Date", "End Date",
                           "Award Amount", "Awarding Agency", "Awarding Sub Agency", "Description"],
            }
            r = requests.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=20,
            )
            if r.status_code != 200:
                continue
            for award in r.json().get("results", []):
                end_str = (award.get("End Date", "") or "")[:10]
                if not end_str:
                    continue
                try:
                    end_dt    = datetime.strptime(end_str, "%Y-%m-%d")
                    days_left = (end_dt - today).days
                    if days_left < 0 or days_left > 365:
                        continue
                    urgency  = ("🔴 Expires < 90d" if days_left < 90
                                else "🟡 Expires < 180d" if days_left < 180
                                else "🟢 Expires < 1yr")
                    agency   = award.get("Awarding Agency", "")
                    amount   = award.get("Award Amount", 0) or 0
                    desc     = (award.get("Description", "") or "")[:150]
                    award_id = award.get("Award ID", "")
                    items_out.append({
                        "competitor": f"{comp_name} — Recompete Alert",
                        "title":  f"{urgency} | {comp_name} @ {agency} — ${amount:,.0f}",
                        "url":    clean_url(f"https://www.usaspending.gov/award/{award_id}/",
                                            "https://www.usaspending.gov"),
                        "source": "USASpending.gov",
                        "date":   end_str,
                        "summary": f"Contract ends {end_str} ({days_left}d). {desc}",
                        "is_recompete": True,
                        "days_left": days_left,
                    })
                except Exception:
                    continue
            time.sleep(0.3)
        except Exception as e:
            print(f"[Recompetes] {comp_name}: {e}")

    print(f"[Competitor Intel] {len(items_out)} signals")
    return items_out


# ---------------------------------------------------------------------------
# GRANTS / FEDERAL FUNDING
# ---------------------------------------------------------------------------
def fetch_federal_funding() -> list[dict]:
    items, seen = [], set()
    today = datetime.utcnow()
    since = (today - timedelta(days=10))
    TECH_TERMS = [
        "law enforcement technology grant", "public safety technology grant",
        "criminal justice data analytics", "records management system grant",
        "community supervision technology", "offender management system",
        "crime gun intelligence center", "digital evidence management",
    ]
    CUSTOMER_TERMS = [
        "byrne jag", "edward byrne", "justice assistance grant",
        "cops office technology", "community oriented policing",
        "second chance act", "justice reinvestment initiative",
        "violence reduction", "community violence intervention",
        "smart policing initiative", "data-driven policing",
        "homeland security grant program",
    ]
    GRANT_EXCLUSIONS = [
        "treatment court", "drug court", "mental health court",
        "substance abuse treatment", "behavioral health", "mental health services",
        "victim services", "victim compensation", "domestic violence shelter",
        "housing assistance", "homeless", "nutrition", "food bank",
        "scholarship", "fellowship", "research only",
        "road", "bridge", "wildfire", "flood", "hurricane",
        "healthcare", "dental", "hospital", "public health",
        "body armor", "equipment purchase", "vehicle", "construction",
    ]
    TECH_SIGNALS = [
        "technology", "software", "data analytics", "data platform",
        "information system", "digital", "analytics platform",
        "records management", "information technology", "data-driven",
    ]
    PROGRAM_SIGNALS = [
        "byrne jag", "edward byrne", "justice assistance",
        "cops office", "second chance act", "justice reinvestment",
        "violence reduction", "community violence intervention",
        "smart policing", "nibin", "crime gun", "data-driven policing",
    ]
    for kw in TECH_TERMS + CUSTOMER_TERMS:
        try:
            r = requests.post(
                "https://apply07.grants.gov/grantsws/rest/opportunities/search/",
                json={"keyword": kw, "oppStatuses": "posted", "rows": 8, "sortBy": "openDate|desc"},
                headers={"Content-Type": "application/json", "User-Agent": HEADERS["User-Agent"]},
                timeout=20,
            )
            if r.status_code != 200:
                continue
            for opp in r.json().get("oppHits", []):
                opp_id   = str(opp.get("id", ""))
                if opp_id in seen:
                    continue
                title    = (opp.get("title", "") or "").strip()
                synopsis = (opp.get("synopsis", "") or "").strip()
                agency   = (opp.get("agencyName", "") or "").strip()
                combined = f"{title} {synopsis}".lower()
                if any(excl in combined for excl in GRANT_EXCLUSIONS):
                    continue
                if not any(s in combined for s in TECH_SIGNALS) and \
                   not any(p in combined for p in PROGRAM_SIGNALS):
                    continue
                seen.add(opp_id)
                is_tech = kw in TECH_TERMS
                items.append({
                    "type":       "🎯 Direct Tech Grant" if is_tech else "💰 Customer Budget Signal",
                    "title":      title,
                    "agency":     agency,
                    "number":     opp.get("number", ""),
                    "open_date":  opp.get("openDate", ""),
                    "close_date": opp.get("closeDate", ""),
                    "summary":    synopsis[:350],
                    "url":        clean_url(f"https://www.grants.gov/search-results-detail/{opp_id}",
                                            "https://www.grants.gov"),
                    "source":     "grants.gov",
                    "relevance":  kw,
                })
            time.sleep(0.2)
        except Exception as e:
            print(f"[Funding] '{kw}': {e}")

    seen_titles = set()
    deduped = []
    for item in items:
        key = item["title"][:60].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(item)
    deduped.sort(key=lambda x: x.get("open_date", ""), reverse=True)
    print(f"[Federal Funding] {len(deduped)} relevant grants")
    return deduped[:15]


def fetch_agency_budget_news() -> list[dict]:
    items, seen = [], set()
    BUDGET_QUERIES = [
        ("DOJ Budget",       "Department+of+Justice+budget+technology+data+analytics"),
        ("ATF Technology",   "ATF+Alcohol+Tobacco+Firearms+technology+data"),
        ("FBI Technology",   "FBI+technology+data+analytics+platform"),
        ("DHS Budget",       "Department+of+Homeland+Security+budget+technology"),
        ("ICE Technology",   "ICE+immigration+enforcement+technology+data"),
        ("CISA Budget",      "CISA+cybersecurity+budget+technology"),
        ("Byrne JAG News",   "Byrne+JAG+grant+law+enforcement+technology"),
        ("Violence Reduction","community+violence+intervention+grant+technology"),
        ("NIBIN Funding",    "NIBIN+crime+gun+intelligence+funding+ATF"),
    ]
    for label, query in BUDGET_QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PeregrineScanner/2.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            count = 0
            for item in root.findall(".//item"):
                if count >= 2:
                    break
                t_el = item.find("title")
                l_el = item.find("link")
                d_el = item.find("description")
                p_el = item.find("pubDate")
                title = (t_el.text or "").strip() if t_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d_el.text or ""))).strip() if d_el is not None else ""
                url_  = (l_el.text or "").strip() if l_el is not None else ""
                date_ = (p_el.text or "").strip() if p_el is not None else ""
                if not title or title in seen:
                    continue
                seen.add(title)
                items.append({
                    "label": label, "title": title, "summary": desc[:280],
                    "url": clean_url(url_, ""), "date": date_[:16], "source": "Google News",
                })
                count += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[BudgetNews] {label}: {e}")
    print(f"[Agency Budget News] {len(items)} signals")
    return items[:20]


# ---------------------------------------------------------------------------
# EMAIL BUILDING
# ---------------------------------------------------------------------------
def deduplicate_and_rank(opps: list) -> list:
    seen = set()
    out  = []
    for o in sorted(opps, key=lambda x: x.score, reverse=True):
        if is_expired(o):
            continue
        key = o.notice_id or o.title[:60].lower()
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def build_section(title: str, opps: list) -> str:
    if not opps:
        return ""
    rows = ""
    for o in opps[:50]:
        link = (f'<a href="{o.url}" style="font-weight:700;font-size:14px;color:#0057b8;text-decoration:none;">{o.title[:120]}</a>'
                if o.url else f'<span style="font-weight:700;font-size:14px;color:#333;">{o.title[:120]}</span>')
        reasons_html = ""
        if o.score_reasons:
            bullets = "".join(f"<li>{r}</li>" for r in o.score_reasons[:4])
            reasons_html = f'<ul style="margin:4px 0 0 0;padding-left:18px;font-size:12px;color:#555;">{bullets}</ul>'
        deadline = ""
        if o.response_date and o.response_date != "TBD":
            try:
                d = parse_date_flexible(o.response_date)
                if d:
                    days = (d - datetime.utcnow()).days
                    if days <= 7:
                        deadline = f' <span style="background:#c0392b;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px;">Due in {days}d</span>'
                    elif days <= 30:
                        deadline = f' <span style="background:#e67e22;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px;">Due in {days}d</span>'
            except Exception:
                pass
        rows += f"""
        <div style="border:1px solid #e8e8e8;border-radius:6px;padding:12px;margin-bottom:10px;background:#fff;">
          <div style="margin-bottom:6px;">{link}{deadline}</div>
          <div style="font-size:12px;color:#666;">🏛 {o.agency[:80]} &nbsp;·&nbsp; 📬 Posted: {o.posted_date[:10]}</div>
          <div style="font-size:11px;color:#999;margin-top:2px;">
            Source: {o.source} &nbsp;·&nbsp; Score: {o.score}pts &nbsp;·&nbsp;
            <a href="{o.url}" style="color:#0057b8;">View on SAM.gov</a>
          </div>
          {reasons_html}
        </div>"""
    return f"""
    <div style="margin:20px 0 6px">
      <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">{title} ({len(opps)})</h2>
      {rows}
    </div>"""


def build_award_intel_section(awards: list) -> str:
    if not awards:
        return ""
    rows = ""
    for o in awards[:5]:
        rows += f"""
        <div style="border-left:3px solid #95a5a6;padding:8px 10px;margin-bottom:8px;background:#f9f9f9;">
          <div style="font-size:13px;font-weight:600;color:#333;">{o.title[:100]}</div>
          <div style="font-size:11px;color:#888;">{o.agency[:70]} · {o.posted_date[:10]}</div>
        </div>"""
    return f"""
    <div style="margin:20px 0 6px">
      <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">📊 Award Intel — Recent Contract Wins</h2>
      {rows}
    </div>"""


def _grant_why_it_fits(item: dict) -> list:
    title  = (item.get("title","") or "").lower()
    summ   = (item.get("summary","") or "").lower()
    rele   = (item.get("relevance","") or "").lower()
    rtype  = (item.get("type","") or "").lower()
    text   = f" {title} {summ} {rele} "
    reasons = []
    if "direct tech" in rtype:
        reasons.append(("🎯","Direct Technology Grant","Funding for software/data platform procurement"))
    else:
        reasons.append(("💰","Customer Budget Signal","Funding flowing to agencies that buy Peregrine"))
    cap_checks = [
        (("data integrat","data analytics","data platform","information sharing"),
         "⬡","Data Integration","Peregrine unifies data from RMS, CAD, jail, court, and federal systems"),
        (("investigative","crime analytics","intelligence platform","link analysis","digital evidence"),
         "◎","Investigative Analytics","Peregrine surfaces patterns and links for investigators"),
        (("community supervision","probation","parole","offender management","reentry","second chance","corrections"),
         "⬡","Corrections & Supervision","Peregrine deployed at CSOSA for offender data analytics"),
        (("law enforcement","public safety","police","fusion center","records management"),
         "⬟","Public Safety","Direct LE agency funding — Peregrine's primary buyer"),
        (("byrne jag","bjag","edward byrne","justice assistance"),
         "💵","Byrne JAG","Most flexible LE grant — agencies routinely use for analytics platforms"),
        (("cops office","community oriented policing"),
         "👮","COPS Office","COPS grants fund technology and data systems"),
        (("violence reduction","gun violence","antiviolence","nibin"),
         "🎯","Violence Reduction","Funds NIBIN/analytics platforms Peregrine provides to ATF"),
        (("second chance","reentry","recidivism","justice reinvestment"),
         "🔄","Reentry/Justice Reform","Funds supervision tech and offender data systems"),
    ]
    for terms, icon, cname, desc in cap_checks:
        if any(t in text for t in terms):
            reasons.append((icon, cname, desc))
    seen = set()
    deduped = []
    for r in reasons:
        if r[1] not in seen:
            seen.add(r[1])
            deduped.append(r)
    return deduped[:4]


def build_funding_section(funding_items: list) -> str:
    if not funding_items:
        return """
        <div style="margin:20px 0 6px">
          <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">💰 Federal Funding Opportunities</h2>
          <p style="color:#aaa;font-size:13px;font-style:italic">No relevant funding in last 10 days.</p>
        </div>"""
    rows = ""
    for item in funding_items[:12]:
        badge_color = "#27ae60" if "Direct" in item["type"] else "#0057b8"
        badge_text  = item["type"].replace("🎯 ","").replace("💰 ","")
        close_html  = f' &middot; <strong>Closes:</strong> {item["close_date"]}' if item.get("close_date") else ""
        link = (f'<a href="{item["url"]}" style="font-weight:700;font-size:14px;color:#0057b8;text-decoration:none;">{item["title"][:110]}</a>'
                if item.get("url") else f'<span style="font-weight:700;font-size:14px;color:#333;">{item["title"][:110]}</span>')
        reasons = _grant_why_it_fits(item)
        why_html = ""
        if reasons:
            bullets = "".join(
                f'<li><strong>{ico} {lbl}:</strong> {dsc}</li>'
                for ico, lbl, dsc in reasons
            )
            why_html = (
                '<div style="margin-top:8px;padding:8px 10px;background:#f8fafe;'
                'border-left:3px solid #0057b8;border-radius:0 4px 4px 0;">'
                '<div style="font-size:11px;font-weight:700;color:#0057b8;margin-bottom:4px;'
                'text-transform:uppercase;letter-spacing:0.5px;">Why It Fits</div>'
                f'<ul style="margin:0;padding-left:16px;font-size:12px;line-height:1.6;">{bullets}</ul>'
                '</div>'
            )
        rows += (
            '<div style="border:1px solid #e8e8e8;border-radius:6px;padding:12px;margin-bottom:10px;background:#fff;">'
            '<div style="margin-bottom:6px;">'
            f'<span style="background:{badge_color};color:#fff;font-size:10px;font-weight:700;'
            f'padding:2px 7px;border-radius:10px;">{badge_text}</span>'
            f'<span style="font-size:11px;color:#888;margin-left:8px;">'
            f'{item["source"]} &middot; {item.get("open_date","")[:10]}</span>'
            '</div>'
            f'<div style="margin-bottom:4px;">{link}</div>'
            f'<div style="font-size:12px;color:#666;margin-bottom:4px;">'
            f'&#x1F3DB; {item["agency"][:90]}{close_html}</div>'
            + (f'<div style="font-size:12px;color:#555;line-height:1.5;">{item.get("summary","")[:280]}</div>'
               if item.get("summary") else "")
            + why_html + '</div>'
        )
    return (
        '<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">'
        f'&#x1F4B0; Federal Funding — Last 10 Days ({len(funding_items)})</h2>'
        '<p style="font-size:12px;color:#888;margin:0 0 10px;">'
        'Direct tech grants &middot; Customer budget signals</p>'
        f'{rows}</div>'
    )


def build_budget_news_section(budget_news: list) -> str:
    if not budget_news:
        return ""
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in budget_news:
        grouped[item["label"]].append(item)
    rows = ""
    for label in sorted(grouped.keys()):
        for s in grouped[label][:2]:
            link = (f'<a href="{s["url"]}" style="color:#0057b8;text-decoration:none;font-weight:600;">{s["title"][:95]}</a>'
                    if s.get("url") else f'<span style="font-weight:600;">{s["title"][:95]}</span>')
            summary_html = ("<div style='font-size:12px;color:#555;margin-top:2px;'>" + s.get("summary","")[:180] + "</div>") if s.get("summary") else ""
            rows += (
                f'<div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #f0f0f0;">'
                f'<div style="font-size:12px;font-weight:700;color:#555;margin-bottom:2px;">&#x1F4E1; {label}</div>'
                f'<div style="font-size:13px;">{link}</div>'
                f'<div style="font-size:11px;color:#888;">{s["source"]} &middot; {s["date"][:10]}</div>'
                f'{summary_html}'
                f'</div>'
            )
    return (
        '<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">'
        f'&#x1F4E1; Agency Budget &amp; Spending Signals ({len(budget_news)})</h2>'
        f'{rows}</div>'
    )


def build_competitor_section(intel_items: list, growth_items: list = None) -> str:
    palantir_rc = sorted(
        [i for i in intel_items if i.get("is_recompete") and "Palantir" in i.get("competitor","")],
        key=lambda x: x.get("days_left", 999)
    )
    other_rc = sorted(
        [i for i in intel_items if i.get("is_recompete") and "Palantir" not in i.get("competitor","")],
        key=lambda x: x.get("days_left", 999)
    )
    news_stories = [i for i in intel_items if not i.get("is_recompete")]

    def _rc_rows(rcs):
        rows = ""
        for rc in rcs[:6]:
            link = (f'<a href="{rc["url"]}" style="font-weight:700;color:#c0392b;text-decoration:none;">{rc["title"][:120]}</a>'
                    if rc.get("url") else f'<span style="font-weight:700;color:#c0392b;">{rc["title"][:120]}</span>')
            rc_summary = ("<div style='font-size:12px;color:#555;margin-top:2px;'>" + rc.get("summary","")[:200] + "</div>") if rc.get("summary") else ""
            rows += (
                '<div style="border-left:3px solid #c0392b;padding:8px 10px;margin-bottom:8px;'
                'background:#fff9f9;border-radius:0 4px 4px 0;">'
                f'<div style="font-size:13px;">{link}</div>'
                f'<div style="font-size:11px;color:#888;">Expires: {rc["date"]} &middot; {rc["source"]}</div>'
                f'{rc_summary}'
                '</div>'
            )
        return rows

    palantir_html = ""
    if palantir_rc:
        palantir_html = (
            '<div style="margin-bottom:16px;border:1px solid #f5c6cb;border-radius:8px;padding:14px;background:#fff9f9;">'
            f'<div style="font-weight:700;font-size:13px;color:#c0392b;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px;">'
            f'🎯 Palantir Recompete Opportunities ({len(palantir_rc)} expiring)</div>'
            '<p style="font-size:12px;color:#666;margin:0 0 8px;">Active Palantir contracts expiring within 12 months — displacement opportunities for Peregrine.</p>'
            f'{_rc_rows(palantir_rc)}</div>'
        )

    other_rc_html = ""
    if other_rc:
        other_rc_html = (
            '<div style="margin-bottom:16px;">'
            f'<div style="font-weight:700;font-size:13px;color:#e67e22;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px;">'
            f'⚡ Other Competitor Recompetes ({len(other_rc)} expiring)</div>'
            f'{_rc_rows(other_rc)}</div>'
        )

    news_rows = ""
    if news_stories:
        from collections import defaultdict
        grouped = defaultdict(list)
        for item in news_stories:
            grouped[item["competitor"]].append(item)
        for comp_name in sorted(grouped.keys()):
            stories = grouped[comp_name][:2]
            story_html = ""
            for s in stories:
                link = (f'<a href="{s["url"]}" style="color:#0057b8;text-decoration:none;font-weight:600;">{s["title"][:90]}</a>'
                        if s.get("url") else f'<span style="font-weight:600;color:#333;">{s["title"][:90]}</span>')
                ns_summary = ("<div style='font-size:12px;color:#555;margin-top:2px;'>" + s.get("summary","")[:200] + "</div>") if s.get("summary") else ""
                story_html += (
                    '<div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #f0f0f0;">'
                    f'<div style="font-size:13px;">{link}</div>'
                    f'<div style="font-size:11px;color:#888;margin-top:2px;">{s["source"]} &middot; {s["date"][:10]}</div>'
                    f'{ns_summary}'
                    '</div>'
                )
            news_rows += (
                f'<div style="margin-bottom:14px;">'
                f'<div style="font-weight:700;font-size:12px;color:#555;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">⚔️ {comp_name}</div>'
                f'{story_html}</div>'
            )

    total = len(palantir_rc) + len(other_rc) + len(news_stories)
    monitoring = ", ".join(c["name"] for c in COMPETITORS)
    news_or_fallback = news_rows or "<p style='color:#aaa;font-size:13px;font-style:italic'>No competitor news today.</p>"
    return (
        f'<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">&#x1F50E; Competitor Intelligence ({total} signals)</h2>'
        f'<p style="font-size:12px;color:#888;margin:0 0 12px;">Monitoring: {monitoring}</p>'
        f'{palantir_html}{other_rc_html}{news_or_fallback}'
        f'</div>'
    )


def build_news_section(news_items: list) -> str:
    if not news_items:
        return ""
    rows = ""
    for item in news_items[:10]:
        link = (f'<a href="{item["url"]}" style="color:#0057b8;text-decoration:none;font-weight:600;">{item["title"][:100]}</a>'
                if item.get("url") else f'<span style="font-weight:600;">{item["title"][:100]}</span>')
        ni_summary = (("<div style='font-size:12px;color:#555;margin-top:2px;'>" + item.get("summary","")[:200] + "</div>") if item.get("summary") else "")
        rows += (
            f'<div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #f0f0f0;">'
            f'<div style="font-size:13px;">{link}</div>'
            f'<div style="font-size:11px;color:#888;margin-top:2px;">{item["source"]} &middot; {item["date"][:10]}</div>'
            f'{ni_summary}'
            f'</div>'
        )
    return (
        f'<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">📰 Industry News &amp; Market Signals ({len(news_items)})</h2>'
        f'{rows}</div>'
    )


def _possible_fits(non_events: list, tiers: dict, shown: set = None) -> list:
    shown = shown or set()
    def _k(o): return (o.notice_id or o.title[:60].lower()).strip()
    def _unseen(lst): return [o for o in lst if _k(o) not in shown]
    possible = _unseen([o for o in tiers.get("possible", []) if o.source != "Events Intelligence"])
    if possible:
        return possible
    low = sorted(_unseen([o for o in non_events if o.tier == "⚪ Low Fit" and o.score > 0]),
                 key=lambda x: x.score, reverse=True)
    if low:
        return low[:10]
    TITLE_KW = ["analytics platform", "data platform", "software platform",
                "analytics solution", "data integration", "law enforcement analytics"]
    return sorted([o for o in _unseen(non_events)
                   if o.tier not in ("⛔ Not a Fit", "⛔ Expired")
                   and any(kw in o.title.lower() for kw in TITLE_KW)],
                  key=lambda x: x.score, reverse=True)[:10]


def build_html_email(opps: list, run_date: str,
                     source_counts: dict = None,
                     news_items: list = None,
                     competitor_items: list = None,
                     growth_items: list = None,
                     funding_items: list = None,
                     budget_news: list = None) -> str:

    source_counts = source_counts or {}
    non_events    = [o for o in opps if o.source != "Events Intelligence"]
    events        = [o for o in opps if o.source == "Events Intelligence"]
    usa_intel     = [o for o in non_events if o.source == "USASpending.gov"]
    non_events    = [o for o in non_events if o.source != "USASpending.gov"]

    def _key(o): return (o.notice_id or o.title[:60].lower()).strip()
    def _dedup(lst):
        seen = set(); out = []
        for o in lst:
            k = _key(o)
            if k not in seen: seen.add(k); out.append(o)
        return out

    shown = set()
    strong_list = _dedup([o for o in non_events if "Strong" in o.tier])
    shown.update(_key(o) for o in strong_list)
    good_list = _dedup([o for o in non_events if "Good" in o.tier and _key(o) not in shown])
    shown.update(_key(o) for o in good_list)
    possible_list = _dedup([o for o in non_events if "Possible" in o.tier and _key(o) not in shown])
    shown.update(_key(o) for o in possible_list)
    low_fit_list = _dedup([o for o in non_events if o.tier == "⚪ Low Fit" and o.score > 0 and _key(o) not in shown])
    shown.update(_key(o) for o in low_fit_list)

    tiers = {"strong": strong_list, "good": good_list, "possible": possible_list}

    strong   = len(strong_list)
    good     = len(good_list)
    possible = len(possible_list)

    sc_rows = "".join(
        f'<tr><td style="padding:3px 10px;color:#555;font-size:12px;">{k}</td>'
        f'<td style="padding:3px 10px;font-weight:700;color:#222;font-size:12px;">{v}</td></tr>'
        for k, v in sorted(source_counts.items())
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:'Helvetica Neue',Arial,sans-serif;background:#f5f5f5;margin:0;padding:0}}
.wrap{{max-width:720px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden}}
.header{{background:#0057b8;padding:24px 28px;color:#fff}}
.content{{padding:20px 28px}}
</style></head><body>
<div class="wrap">
<div class="header">
  <div style="font-size:22px;font-weight:700;letter-spacing:-0.5px;">🦅 Peregrine Daily Scanner</div>
  <div style="font-size:14px;opacity:0.85;margin-top:4px;">{run_date}</div>
  <div style="margin-top:12px;display:flex;gap:16px;flex-wrap:wrap;">
    <span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">🟢 {strong} Strong</span>
    <span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">🟡 {good} Good</span>
    <span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">🔵 {possible} Possible</span>
  </div>
</div>
<div class="content">
  <details style="margin-bottom:16px;border:1px solid #eee;border-radius:6px;padding:8px 12px;">
    <summary style="font-size:12px;color:#888;cursor:pointer;">Sources searched today</summary>
    <table style="margin-top:8px;border-collapse:collapse;">{sc_rows}</table>
  </details>

  {build_section("🟢 Strong Fit — Act Now", strong_list)}
  {build_section("🟡 Good Fit — Review Today", good_list)}
  {build_section("🔵 Possible Fit — Review These", _possible_fits(non_events, tiers, shown))}
  {build_section("⚪ Low Fit — Any Keyword Match", low_fit_list)}
  {build_award_intel_section(usa_intel[:5])}
  {build_competitor_section(competitor_items or [], growth_items=growth_items or [])}
  {build_funding_section(funding_items or [])}
  {build_budget_news_section(budget_news or [])}
  {build_news_section(news_items or [])}
  {build_section("🎤 Events & Conferences (Next 3 Months)", sorted(events, key=lambda x: x.score, reverse=True))}
</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# EMAIL SEND
# ---------------------------------------------------------------------------
def send_email(html_body: str, subject: str):
    api_key  = os.environ.get("SENDGRID_API_KEY", "")
    email_to = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
    email_from = os.environ.get("EMAIL_FROM", "mikefkelly26@gmail.com")
    if not api_key:
        print("[Email] No SENDGRID_API_KEY — skipping send")
        return
    payload = {
        "personalizations": [{"to": [{"email": email_to}]}],
        "from": {"email": email_from, "name": "Peregrine Federal Scanner"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }
    try:
        r = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        if r.status_code in (200, 202):
            print(f"[Email] Sent to {email_to} ✓")
        else:
            print(f"[Email] Send failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[Email] Error: {e}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    today    = datetime.utcnow()
    run_date = today.strftime("%B %d, %Y")
    print(f"\n{'='*60}")
    print(f"  Peregrine Daily Scanner — {run_date}")
    print(f"{'='*60}")

    SAM_KEY = os.environ.get("SAM_API_KEY", "")
    SG_KEY  = os.environ.get("SENDGRID_API_KEY", "")
    ET_TO   = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
    ET_FROM = os.environ.get("EMAIL_FROM", "mikefkelly26@gmail.com")
    print(f"[Config] SAM_API_KEY set:      {'YES' if SAM_KEY else 'NO'}")
    print(f"[Config] SENDGRID_API_KEY set: {'YES' if SG_KEY else 'NO'}")
    print(f"[Config] EMAIL_TO:             {ET_TO}")
    print(f"[Config] EMAIL_FROM:           {ET_FROM}")

    source_counts = {}
    all_opps      = []

    sources = [
        ("SAM.gov",           fetch_sam_gov),
        ("DOJ",               fetch_doj_opportunities),
        ("DHS",               fetch_dhs_opportunities),
        ("DoD",               fetch_dod_opportunities),
        ("Federal Register",  fetch_federal_register),
        ("USASpending.gov",   fetch_usaspending_intel),
        ("Agency RSS",        fetch_agency_rss_feeds),
        ("Events",            fetch_events_intelligence),
    ]
    for label, fn in sources:
        print(f"\n[{label}] Fetching...")
        try:
            batch = fn()
            source_counts[label] = len(batch)
            all_opps.extend(batch)
            # Populate cache after SAM.gov runs
            if label == "SAM.gov":
                _SAM_RESULTS_CACHE.clear()
                _SAM_RESULTS_CACHE.extend(batch)
        except Exception as e:
            print(f"[{label}] FAILED: {e}")
            source_counts[label] = 0

    print(f"\n[Scoring] Deduplicating and ranking {len(all_opps)} raw opportunities...")
    ranked = deduplicate_and_rank(all_opps)
    print(f"[Scoring] {len(ranked)} unique active opportunities after dedup")

    strong   = sum(1 for o in ranked if "Strong" in o.tier)
    good     = sum(1 for o in ranked if "Good" in o.tier)
    possible = sum(1 for o in ranked if "Possible" in o.tier)
    print(f"[Tiers] 🟢 Strong: {strong}  🟡 Good: {good}  🔵 Possible: {possible}")

    print("\n[Industry News] Fetching...")
    try:
        news_items = fetch_industry_news()
        source_counts["Industry News"] = len(news_items)
    except Exception as e:
        print(f"[Industry News] FAILED: {e}")
        news_items = []

    print("\n[Competitor Intel] Fetching...")
    try:
        competitor_items = fetch_competitor_intel()
        source_counts["Competitor Intel"] = len(competitor_items)
    except Exception as e:
        print(f"[Competitor Intel] FAILED: {e}")
        competitor_items = []

    growth_items = []
    if not competitor_items:
        try:
            growth_items = fetch_growth_news()
        except Exception as e:
            print(f"[Growth News] FAILED: {e}")

    print("\n[Federal Funding] Fetching...")
    try:
        funding_items = fetch_federal_funding()
        source_counts["Federal Funding"] = len(funding_items)
    except Exception as e:
        print(f"[Federal Funding] FAILED: {e}")
        funding_items = []

    print("\n[Budget News] Fetching...")
    try:
        budget_news = fetch_agency_budget_news()
        source_counts["Budget News"] = len(budget_news)
    except Exception as e:
        print(f"[Budget News] FAILED: {e}")
        budget_news = []

    # Build subject
    if strong == 0 and good == 0 and possible == 0:
        subject = f"Peregrine Daily Scanner | No Matches Today | {today.strftime('%b %d')}"
    elif strong >= 1:
        subject = f"Peregrine Daily Scanner | {strong} Strong · {good} Good · {possible} Possible | {today.strftime('%b %d')}"
    else:
        subject = f"Peregrine Daily Scanner | {good} Good · {possible} Possible Fits | {today.strftime('%b %d')}"

    html = build_html_email(
        ranked, run_date, source_counts,
        news_items=news_items,
        competitor_items=competitor_items,
        growth_items=growth_items,
        funding_items=funding_items,
        budget_news=budget_news,
    )

    send_email(html, subject)

    fname = f"digest_{today.strftime('%Y%m%d')}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[Done] Digest saved: {fname}")
    print(f"[Done] Subject: {subject}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print(f"[FATAL ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        rai
"""
Peregrine.io Daily Federal Opportunity Scanner — Multi-Source Edition
=======================================================================
Data Sources (all free, no registration required except SAM.gov API):
  1. SAM.gov API v2          — RFIs, Sources Sought, Pre-Solicitations, Industry Days
  2. Federal Register API    — RFI notices published by federal agencies (NO KEY)
  3. USASpending.gov API v2  — Recent contract awards in target NAICS (competitive intel) (NO KEY)
  4. DHS/DOJ/FBI procurement — Web-scraped upcoming solicitations & industry events
  5. GSA eBuy / schedules    — RSS/public feed scrape for IT Schedule 70 opportunities

Outputs:
  - Ranked HTML email digest sent to configured recipients
  - Local HTML file saved for auditing
"""

import os
import re
import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from html import unescape
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# CONFIGURATION — only 3 secrets needed
# ---------------------------------------------------------------------------
SAM_API_KEY       = os.environ.get("SAM_API_KEY", "")
SENDGRID_API_KEY  = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_TO          = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
EMAIL_FROM        = os.environ.get("EMAIL_FROM", "mike.kelly@peregrine.io")

# Debug output — printed in GitHub Actions logs (secrets are masked automatically)
print(f"[Config] SAM_API_KEY set:      {'YES' if SAM_API_KEY else 'NO - SAM.gov results will be empty'}")
print(f"[Config] SENDGRID_API_KEY set: {'YES' if SENDGRID_API_KEY else 'NO - will fail at send step'}")
print(f"[Config] EMAIL_TO:             {EMAIL_TO}")
print(f"[Config] EMAIL_FROM:           {EMAIL_FROM}")

HEADERS = {
    "User-Agent": "PeregrineOpportunityScanner/2.0 (federal procurement research; contact@peregrine.io)",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# PEREGRINE CORE CAPABILITIES (grounded in actual product)
#
# Peregrine is a secure enterprise data integration and intelligence platform
# purpose-built for law enforcement, public safety, and corrections agencies.
# It does NOT provide: hardware, staffing, maintenance, construction, or
# general IT helpdesk. It IS: a SaaS data platform with analytics.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DATA CLASS
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field as dc_field

@dataclass
class Opportunity:
    title: str
    notice_id: str
    agency: str
    posted_date: str
    response_date: str
    description: str
    url: str
    opp_type: str
    source: str
    naics: str = ""
    score: int = 0
    score_reasons: list = dc_field(default_factory=list)
    tier: str = ""


# ---------------------------------------------------------------------------
# DATE UTILITIES
# ---------------------------------------------------------------------------
def parse_date_flexible(date_str: str):
    """Try multiple date formats and return a datetime or None."""
    if not date_str or date_str in ("TBD", "N/A", "See posting",
            "Watch for recompete", "See event page for registration deadline",
            "Monitor for follow-on procurement"):
        return None
    fmts = [
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y",
    ]
    clean = date_str.strip()[:25]
    for fmt in fmts:
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None

def is_expired(opp) -> bool:
    """
    Return True ONLY if the response deadline has clearly passed.
    Only checks response_date — never posted_date (which is always in the past).
    If response_date is TBD/unparseable, assume still active.
    """
    grace = datetime.utcnow() - timedelta(days=2)
    dt = parse_date_flexible(opp.response_date)
    if dt:
        return dt < grace
    # TBD or unparseable deadline = assume still open
    return False

def clean_url(url: str, fallback: str = "") -> str:
    """
    Validate and clean a URL. Returns the URL if valid, fallback otherwise.
    Ensures URLs start with http/https, strips whitespace, and handles
    common malformed patterns from API responses.
    """
    if not url:
        return fallback
    url = url.strip()
    # Must start with http or https
    if not url.startswith(("http://", "https://")):
        # Try prepending https
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("www."):
            url = "https://" + url
        else:
            return fallback
    # Basic sanity — no spaces, reasonable length
    if " " in url or len(url) > 2000:
        return fallback
    return url


# Peregrine's 6 core capability areas — what it actually sells and deploys
CAPABILITY_CLUSTERS = [
    (
        # Peregrine unifies siloed data from multiple systems into one platform
        "Data Integration & Unification", 20,
        [
            # Core phrases
            "data integration", "data unification", "data fusion",
            "disparate systems", "disparate data", "data silos",
            "siloed data", "data harmonization", "fragmented data",
            "enterprise data platform", "data integration platform",
            "unified data", "unified platform", "data consolidation",
            "information integration", "information sharing",
            "master data management", "data normalization",
            "data ingestion", "data pipeline", "data fabric",
            "data lake", "data warehouse", "data mesh",
            # Shorter triggers that appear in real titles
            "data analytics", "analytics platform", "analytics tool",
            "data management", "data management platform",
            "data management system", "data solution",
            "data platform", "data environment",
            "analytics solution", "analytics service",
            "reporting tool", "reporting platform",
            "dashboard", "business intelligence",
            "software platform", "enterprise software",
            "cloud platform", "cloud solution", "cloud-based",
        ],
    ),
    (
        # Peregrine surfaces connections, patterns, and insights for investigators
        "Investigative & Operational Analytics", 20,
        [
            # Core phrases
            "investigative analytics", "investigative platform",
            "investigative tool", "investigative system",
            "link analysis", "relationship mapping",
            "situational awareness", "operational intelligence",
            "operational dashboard", "pattern of life",
            "geospatial analysis", "geospatial intelligence",
            "crime analytics", "crime analysis",
            "advanced analytics", "intelligence platform",
            "intelligence system", "real-time analytics",
            "predictive analytics", "predictive policing",
            "common operating picture",
            # Shorter triggers
            "investigation management", "case analytics",
            "operational analysis", "mission analytics",
            "visualization", "geospatial", "mapping platform",
            "predictive", "intelligence analysis",
            # Digital evidence — DOJ DERP and similar platforms
            "digital evidence", "evidence review platform",
            "evidence analytics", "evidence management platform",
            "media review platform", "digital forensics platform",
            "investigative data platform",
        ],
    ),
    (
        # Peregrine lets users search across multiple connected systems at once
        "Federated & Enterprise Search", 20,
        [
            "federated search", "enterprise search",
            "cross-system search", "unified search",
            "search across", "search multiple",
            "search and retrieval", "information retrieval",
            "search capability", "search platform",
            "search solution", "search system",
            "knowledge retrieval", "query across",
            "semantic search", "full-text search",
            "document search", "content search",
        ],
    ),
    (
        # Peregrine deduplicates and resolves records across systems
        "Entity Resolution & Record Intelligence", 20,
        [
            "entity resolution", "record deduplication",
            "record linkage", "duplicate records",
            "identity resolution", "entity matching",
            "data deduplication", "entity-centric",
            "record consolidation", "ontology",
            "knowledge graph", "graph analytics",
            "relationship graph", "master record",
            "person record", "record resolution",
            "deduplication", "entity management",
        ],
    ),
    (
        # Peregrine is FedRAMP-authorized, CJIS-compliant, runs on AWS GovCloud
        "Secure Government SaaS", 15,
        [
            "fedramp", "cjis", "nist 800-53", "nist sp 800",
            "govcloud", "zero trust", "icam",
            "saml", "single sign-on", "sso",
            "role-based access", "rbac",
            "attribute-based access", "abac",
            "section 508", "audit logging",
            "authority to operate", "ato",
            "cloud security", "secure cloud",
            "government cloud", "cloud compliance",
        ],
    ),
    (
        # Public Safety & Law Enforcement — must imply SOFTWARE/DATA need,
        # not just any law enforcement adjacent work. "police" alone matches
        # vehicle purchases, uniforms, etc. Require compound terms that signal
        # a technology or data platform requirement.
        "Public Safety & Law Enforcement", 20,
        [
            # Specific Peregrine integrations (always relevant)
            "nibin", "etrace", "crime gun", "ballistic intelligence",
            "cgic", "crime gun intelligence",
            # Platform/system terms — imply software procurement
            "records management system", "records management software",
            "computer-aided dispatch", "computer aided dispatch", "cad system", "cad software",
            "law enforcement platform", "law enforcement software",
            "law enforcement analytics", "law enforcement data",
            "law enforcement technology", "law enforcement information",
            "public safety platform", "public safety software",
            "public safety technology", "public safety data",
            "public safety analytics", "public safety system",
            "policing platform", "policing software",
            "fusion center", "fusion center platform",
            "criminal justice platform", "criminal justice software",
            "criminal justice information system", "criminal justice data",
            "crime analytics", "crime data", "crime intelligence",
            "evidence management system", "evidence management platform",
            "investigation platform", "investigative software",
            "body camera data", "body camera analytics",
        ],
    ),
    (
        # Peregrine is deployed for probation/parole agencies (CSOSA use case)
        "Corrections & Community Supervision", 20,
        [
            "community supervision", "probation", "parole",
            "reentry", "offender management",
            "supervision officer", "court services",
            "pretrial", "case supervision",
            "csosa", "bureau of prisons",
            "department of corrections",
            "recidivism", "offender data",
            "supervision platform", "smart21",
            "supervised release",
            "correctional software", "correctional platform",
            "correctional data", "correctional analytics",
            "offender tracking", "supervision software",
            "supervision system", "case management",
            "supervision platform", "supervision system", "supervision software",
            "supervision analytics", "offender supervision",
        ],
    ),
    (
        # Peregrine replaces legacy and incumbent platforms like Palantir
        "Platform Modernization & Replacement", 20,
        [
            "palantir", "palantir replacement",
            "palantir alternative", "gotham", "foundry",
            "ibm i2", "platform replacement",
            "incumbent replacement", "platform consolidation",
            "legacy platform", "legacy system",
            "legacy modernization", "platform modernization",
            "platform migration", "technology refresh",
            "system modernization",             "data platform upgrade",
            "it modernization", "digital transformation",
            "software modernization", "cloud migration",
            "application modernization",
            "platform modernization", "legacy modernization",
            "system modernization", "data modernization",
            "technology modernization", "network modernization",
        ],
    ),
    (
        # Peregrine embeds AI/ML for investigative decision support
        "AI & Machine Learning", 22,
        [
            "artificial intelligence", "machine learning",
            "ai/ml", "ai platform", "ai solution",
            "ai system", "ai services",
            # Space-padded to prevent substring matches like "regenerative" → "generative ai"
            " generative ai", "generative ai ",
            "large language model", " llm ",
            "natural language processing", " nlp ",
            "computer vision", "predictive model",
            "decision support", "decision support system",
            "automated analysis", "intelligent automation",
            "ai-powered", "ai-driven",
            "ai for law enforcement", "ai public safety",
            "responsible ai", "explainable ai",
            "ai governance", "ai analytics",
        ],
    ),
]

# NAICS hints — infer capability when SAM.gov description is blank

# Hard exclusions — ONLY work that has zero software/data component
# Keep very specific to avoid blocking legitimate IT solicitations
HARD_EXCLUSIONS = [
    # Maintenance & repair (the big new addition)
    "maintenance and repair", "repair and maintenance", "maintenance services only",
    "equipment repair", "equipment maintenance", "preventive maintenance",
    "corrective maintenance", "vehicle repair", "vehicle maintenance",
    "facility maintenance", "building maintenance", "hvac maintenance",
    "elevator maintenance", "generator maintenance", "engine repair",
    "aircraft maintenance", "ship repair", "vessel maintenance",
    # Physical facilities
    "janitorial services", "landscaping services", "custodial services",
    "grounds maintenance", "pest control services", "roofing services",
    "flooring installation", "plumbing services", "painting services",
    # Hardware-only procurement (specific phrases)
    "hardware procurement", "hardware purchase", "purchase of laptops",
    "purchase of desktops", "purchase of servers", "purchase of tablets",
    "network cabling", "structured cabling", "body-worn camera purchase",
    "body camera hardware", "purchase of radios", "radio hardware",
    "purchase of body armor", "ballistic vest", "purchase of firearms",
    "ammunition procurement", "vehicle purchase", "fleet vehicle acquisition",
    "drone procurement", "uav procurement", "sensor hardware purchase",
    # Food & clothing
    "food service contract", "food supply", "clothing procurement",
    "uniform procurement", "laundry services",
    # Medical / pharma (non-IT)
    "pharmaceutical procurement", "drug manufacturing", "medical supply",
    "laboratory reagent", "clinical trial services",
    # Construction & infrastructure projects
    "construction project", "construction contract", "construction services",
    "design and construction", "build and construction", "new construction",
    "renovation project", "renovation contract", "building renovation",
    "infrastructure construction", "facility construction",
    "construction management", "general contractor",
    "design-build", "design build", "architect and engineer",
    # Logistics
    "refuse collection", "moving services", "freight services",
    "shipping contract",
    # Professional services unrelated to Peregrine
    "translation services", "interpretation services",
    "attorney services", "legal representation",
    "financial audit services", "accounting services",
    # Hardware & equipment procurement — Peregrine is software only
    "purchase of equipment", "equipment procurement", "equipment acquisition",
    "hardware and equipment", "purchase hardware",
    "body armor", "protective equipment procurement",
    "weapon system", "weapons system", "small arms",
    "radio procurement", "radio acquisition", "portable radio",
    "vehicle acquisition", "vehicle procurement", "fleet acquisition",
    "license plate reader", "lpr procurement",
    "surveillance camera", "camera system procurement",
    "biometric device", "biometric hardware",
    "taser procurement", "less lethal",
    "furniture procurement", "office furniture",
    "it equipment purchase", "computer equipment purchase",
    "printer procurement", "copier procurement",
    "mobile device procurement", "tablet procurement",
    # Staffing-only contracts
    "staffing services", "staff augmentation", "labor category",
    "temporary staffing", "personnel services contract",
    "security guard", "guard services", "physical security services",
    # Training-only (not software training)
    "firearms training", "defensive tactics", "use of force training",
    "physical fitness", "k-9 training", "canine training",
    # Equipment rental and physical goods
    "equipment rental", "rental of equipment", "equipment lease",
    "air compressor", "generator rental", "forklift rental",
    "heavy equipment", "construction equipment",
    "medical equipment", "laboratory equipment",
    "audio visual equipment", "av equipment",
    "office equipment rental",
    # Physical goods procurement
    "purchase of supplies", "office supplies",
    "janitorial supplies", "cleaning supplies",
    # Hardware devices — tablets, phones, computers
    "tablets", "tablet procurement", "tablet purchase",
    "mobile devices", "smartphones", "cell phones",
    "laptops", "desktops", "workstations",
    "printers", "copiers", "scanners",
    # Physical facilities and infrastructure — not software
    "fire suppression", "fire alarm", "fire protection",
    "audio system", "audio visual", "av system",
    "hvac", "plumbing", "electrical system",
    "roof replacement", "flooring replacement", "window replacement",
    "elevator", "escalator", "generator replacement",
    "lighting system", "lighting replacement",
    "physical security system", "access control hardware",
    "camera installation", "cctv installation",
    # Maintenance contracts — not software development
    "annual maintenance", "annual software maintenance",
    "software maintenance agreement", "maintenance and support contract",
    "hardware maintenance", "software assurance",
    # Embassy / consular / facilities
    "embassy", "consular", "chancery",
    # Military hardware, aircraft, weapons systems — not Peregrine's market
    "crypto modernization", "cryptographic", "encryption hardware",
    "router solution", "b-52", "aircraft", "avionics",
    "missile", "munitions", "ammunition", "ordnance",
    "radar", "sonar", "weapons system", "armament",
    "c-130", "f-35", "v-22", "helicopter",
    "ship", "submarine", "vessel",
    "military vehicle", "tactical vehicle",
    # Network / telecom / infrastructure — not software
    "vpn", "ethernet", "transport services", "network infrastructure",
    "telecommunications", "telecom services", "internet service provider",
    "network cabling", "structured cabling", "fiber optic",
    "wireless network", "cellular services", "satellite services",
    "bandwidth services", "circuit services", "wan services",
    "network connectivity", "connectivity services",
    # Hardware support & maintenance agreements — not software
    "maintenance agreement", "service agreement hardware",
    "network server", "server maintenance", "server hardware",
    "hardware support", "hardware maintenance agreement",
    "pma maintenance", "preventive maintenance agreement",
    "network equipment", "server equipment",
    "storage hardware", "storage array",
    "firewall hardware", "switch hardware", "router hardware",
    "data center hardware", "rack hardware",
]

# Penalty signals — mismatch indicators (reduce score but don't exclude)
PENALTY_SIGNALS = [
    ("staffing augmentation", -8),
    ("time and materials labor", -6),
    ("independent verification and validation", -6),
    ("iv&v services", -6),
    ("penetration testing only", -5),
]

# NAICS prefix → capability hints for scoring when description is blank
NAICS_CAPABILITY_HINTS = {
    "513":    "software platform data management analytics",
    "541511": "software development platform custom application",
    "541512": "computer systems design technology platform",
    "541519": "computer services it solution technology",
    "518210": "data processing hosting cloud platform analytics",
    "541690": "technical consulting analytics data solution",
    "922":    "law enforcement criminal justice public safety",
    "922110": "courts criminal justice case management",
    "922120": "police law enforcement public safety records",
    "922150": "probation parole corrections supervision offender",
    "922190": "public safety justice corrections law enforcement",
    "923":    "corrections supervision justice case management",
}

def score_opportunity(opp: Opportunity) -> Opportunity:
    """
    Score based on capability match. Permissive — surfaces anything that could
    plausibly involve Peregrine's platform. Uses NAICS hints when description
    is empty (common with SAM.gov search API).
    """
    # Build enriched text including NAICS-derived capability hints
    naics_hint = ""
    if opp.naics:
        for prefix, hint in NAICS_CAPABILITY_HINTS.items():
            if opp.naics.startswith(prefix):
                naics_hint = hint
                break
    # Score only against title + description + NAICS hints
    # Agency name intentionally excluded — an agency match alone is not a capability fit
    text = f" {opp.title} {opp.description} {naics_hint} ".lower()  # padded for word-boundary phrase matching
    # Keep agency text separate for display only
    agency_text = opp.agency.lower()
    for excl in HARD_EXCLUSIONS:
        if excl.lower() in text:
            opp.score = -1
            opp.tier = "⛔ Not a Fit"
            opp.score_reasons = [f"Excluded: unrelated work (contains '{excl}')"]
            return opp

    # ── 2. Expired opportunity check ─────────────────────────────────────────
    if is_expired(opp):
        opp.score = -1
        opp.tier = "⛔ Expired"
        opp.score_reasons = [f"Response deadline has passed ({opp.response_date})"]
        return opp

    # ── 3. Capability cluster matching ───────────────────────────────────────
    score = 0
    reasons = []
    clusters_matched = 0
    title_only = opp.title.lower()
    saas_hits = []  # Track SaaS hits separately — only count if core cluster also matched

    for cap_name, cap_points, phrases in CAPABILITY_CLUSTERS:
        # Always check title independently — SAM.gov often has rich titles but empty descriptions
        # A title match alone is always meaningful and should always score
        title_hits = [p for p in phrases if p.lower() in title_only]
        desc_hits  = [p for p in phrases if p.lower() in text]
        # Merge, deduplicate, prefer longer (more specific) phrases
        all_hits = list({p: None for p in (title_hits + desc_hits)}.keys())

        if not all_hits:
            continue

        # Flag if this was a title-only match so we can note it
        title_only_match = bool(title_hits) and not bool(desc_hits)

        # Secure SaaS cluster: defer — only count if a core cluster also matched
        if cap_name.startswith("Secure Government SaaS"):
            saas_hits = all_hits
            continue

        score += cap_points
        clusters_matched += 1
        top_hits = sorted(all_hits, key=len, reverse=True)[:3]
        source_note = " (title match)" if title_only_match else ""
        reasons.append(f"✓ {cap_name}: matched '{top_hits[0]}'{source_note}" +
                      (f" + {len(all_hits)-1} more" if len(all_hits) > 1 else ""))

    # Now add SaaS score — but ONLY if at least one core capability cluster matched
    if saas_hits and clusters_matched >= 1:
        score += 15
        clusters_matched += 1
        top = sorted(saas_hits, key=len, reverse=True)[0]
        reasons.append(f"✓ Secure Govt SaaS context: '{top}' (with core capability match)")

    # ── 4. Penalty signals ───────────────────────────────────────────────────
    for signal, penalty in PENALTY_SIGNALS:
        if signal.lower() in text:
            score += penalty
            reasons.append(f"⚠ Penalty: '{signal}' suggests partial mismatch ({penalty} pts)")

    # ── 5. Assign tier — purely capability-based, no bonuses ────────────────
    # Strong Fit = 2+ clusters matched (40+ pts)
    # Good Fit   = 1 cluster matched  (15-39 pts)
    # Possible   = partial signal      (1-14 pts)
    if score >= 40:
        tier = "🟢 Strong Fit"
    elif score >= 15:
        tier = "🟡 Good Fit"
    elif score > 0:
        tier = "🔵 Possible Fit"
    else:
        tier = "⚪ Low Fit"

    opp.score = max(score, 0)
    opp.tier = tier
    opp.score_reasons = reasons if reasons else [
        "No clear capability match — review manually"
    ]
    return opp

# ---------------------------------------------------------------------------
# SOURCE 1: SAM.gov (all agency-targeted searches in one function)
# Public API key limit: 1,000 calls/day. This function uses ~55 calls total.
# ---------------------------------------------------------------------------

_SAM_RATE_LIMITED = [False]  # global flag — stops all SAM calls on 429
_SAM_RESULTS_CACHE: list = []  # shared cache — DOJ/DHS filter from this

def _sam_search(extra_params: dict, label: str,
                seen_ids: set, results: list,
                pages: int = 1) -> bool:
    """SAM.gov search with optional pagination. Returns False if rate limited."""
    if _SAM_RATE_LIMITED[0]:
        return False
    for page in range(pages):
        if _SAM_RATE_LIMITED[0]:
            break
        try:
            params = {"api_key": SAM_API_KEY, "active": "Yes",
                      "limit": 100, "offset": page * 100, **extra_params}
            r = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params=params, headers=HEADERS, timeout=15,
            )
            if r.status_code == 429:
                print(f"[SAM.gov] Rate limit — stopping ({label})")
                _SAM_RATE_LIMITED[0] = True
                return False
            if r.status_code != 200:
                return True
            data  = r.json()
            items = data.get("opportunitiesData", [])
            new_count = 0
            for item in items:
                nid = item.get("noticeId") or item.get("id") or ""
                if not nid or nid in seen_ids:
                    continue
                seen_ids.add(nid)
                new_count += 1
                results.append(score_opportunity(Opportunity(
                    title         = item.get("title", "Untitled"),
                    notice_id     = nid,
                    agency        = (item.get("fullParentPathName")
                                     or item.get("departmentName") or "Unknown"),
                    posted_date   = item.get("postedDate", ""),
                    response_date = item.get("responseDeadLine", "TBD"),
                    description   = (item.get("description") or "")[:2000],
                    url           = clean_url(f"https://sam.gov/opp/{nid}/view",
                                              "https://sam.gov/search"),
                    opp_type      = item.get("type") or "Notice",
                    source        = "SAM.gov",
                    naics         = item.get("naicsCode", ""),
                )))
            if new_count:
                print(f"[SAM.gov] {label} p{page+1}: {data.get('totalRecords',0)} total | {new_count} new")
            if len(items) < 100:
                break  # no more pages
            time.sleep(0.2)
        except Exception as e:
            print(f"[SAM.gov] {label}: {e}")
            return True
    time.sleep(0.2)
    return True


def fetch_sam_gov() -> list[Opportunity]:
    """
    Comprehensive SAM.gov fetch — covers ALL federal agencies.

    Strategy:
      Pass 1: Paginated ptype sweeps (no agency filter) — catches everything
              posted in last 30 days across all agencies. 2 pages per ptype
              = up to 200 results per notice type.
      Pass 2: Capability title searches across all agencies — 90-day window,
              paginated for high-volume terms. Catches older opps and anything
              that fell outside the ptype page limit.
      Pass 3: Broad keyword sweep for Peregrine-specific terms that wouldn't
              appear in generic ptype sweeps.

    Results are cached for DOJ/DHS/DoD to post-filter at zero extra cost.
    """
    if not SAM_API_KEY:
        print("[SAM.gov] No API key — skipping")
        return []

    results, seen_ids = [], set()
    today   = datetime.utcnow()
    to_date = today.strftime("%m/%d/%Y")
    d30     = (today - timedelta(days=30)).strftime("%m/%d/%Y")
    d90     = (today - timedelta(days=90)).strftime("%m/%d/%Y")

    # ── Pass 1: Paginated ptype sweeps — ALL agencies, last 30 days ───────────
    # 2 pages × 100 results = up to 200 per notice type across every agency
    for ptype, lbl in [
        ("r", "Sources Sought"),
        ("p", "Presolicitation"),
        ("k", "Combined Synopsis"),
        ("s", "Special Notice"),
        ("o", "Solicitation"),
        ("i", "Intent to Bundle"),
    ]:
        if not _sam_search({"ptype": ptype, "postedFrom": d30, "postedTo": to_date},
                           lbl, seen_ids, results, pages=2):
            break

    # ── Pass 2: Capability title searches — ALL agencies, 90-day window ───────
    # Covers opps older than 30 days and anything missed by ptype page cap.
    # High-volume terms paginated to 3 pages (up to 300 results each).
    TITLE_SEARCHES = [
        # Specific compound phrases — low volume, 1 page sufficient
        ("investigative platform",     1),
        ("community supervision",      1),
        ("digital evidence",           1),
        ("federated search",           1),
        ("law enforcement analytics",  1),
        ("public safety platform",     1),
        ("entity resolution",          1),
        ("crime analytics",            1),
        ("offender management",        1),
        ("records management system",  1),
        ("enterprise data",            1),
        ("data environment",           1),
        ("data fabric",                1),
        ("zero trust analytics",       1),
        ("fedramp analytics",          1),
        ("investigative analytics",    1),
        ("intelligence platform",      1),
        ("identity resolution",        1),
        ("record deduplication",       1),
        ("crime gun intelligence",     1),
        ("body camera analytics",      1),
        ("corrections platform",       1),
        ("fusion center",              1),
        ("platform replacement",       1),
        ("computer vision analytics",  1),
        ("surveillance analytics",     1),
        ("data unification",           1),
        ("information sharing platform", 1),
        # High-volume terms — paginate to catch deeper results
        ("data analytics",             3),
        ("data integration",           3),
        ("IT modernization",           2),
        ("artificial intelligence",    3),
        ("machine learning",           2),
        ("platform modernization",     2),
        ("predictive analytics",       2),
        ("data management",            3),
        ("data management solutions",    1),
        ("digital transformation",     2),
        ("sovereign cloud",               1),
        ("defense cloud",                 1),
        ("commercial solutions opening",  1),
        ("enterprise analytics",          1),
        ("data science platform",         1),
        ("cross domain solution",         1),
    ]
    for term, pages in TITLE_SEARCHES:
        if _SAM_RATE_LIMITED[0]:
            break
        if not _sam_search({"title": term, "postedFrom": d90, "postedTo": to_date},
                           f"title={term}", seen_ids, results, pages=pages):
            break

    # Cache for DOJ/DHS/DoD post-filtering — zero extra API calls
    _SAM_RESULTS_CACHE.clear()
    _SAM_RESULTS_CACHE.extend(results)
    print(f"[SAM.gov] {len(results)} total opportunities across all agencies")
    return results



# DOD agency path fragments
DOD_PATH_FRAGMENTS = [
    "national guard", "national guard bureau", "ngb",
    "defense information systems", "disa",
    "defense intelligence agency", "dia",
    "defense logistics agency", "dla",
    "defense advanced research", "darpa",
    "engineer research and development", "erdc",
    "army corps of engineers", "usace",
    "army futures command",
    "army research laboratory", "arl",
    "office of the secretary of defense", "osd",
    "defense contract", "defense finance",
    "defense threat reduction", "dtra",
    "special operations command", "socom",
    "engineer research and development", "erdc", "erdc werx",
    "army corps of engineers", "usace",
    "army research laboratory", "arl",
    "defense threat reduction", "dtra",
    "special operations command", "socom",
    "army", "navy", "air force", "marine corps", "space force",
    "joint chiefs", "combatant command",
]
DOJ_PATH_FRAGMENTS = [
    "department of justice", "dept of justice",
    "alcohol, tobacco", "atf",
    "federal bureau of investigation", "fbi",
    "drug enforcement administration", "dea",
    "bureau of prisons", "bop",
    "office of justice programs", "ojp",
    "court services and offender", "csosa",
    "community oriented policing", "cops office",
    "u.s. marshals", "marshals service",
    "executive office for united states attorneys",
    "national security division",
]
DHS_PATH_FRAGMENTS = [
    "homeland security", "dhs",
    "customs and border protection", "cbp",
    "immigration and customs enforcement", "ice",
    "coast guard", "uscg",
    "cybersecurity and infrastructure", "cisa",
    "federal emergency management", "fema",
    "transportation security administration", "tsa",
    "secret service", "usss",
    "citizenship and immigration services", "uscis",
    "federal law enforcement training", "fletc",
]
AGENCY_SEARCH_TERMS = [
    "data integration", "data analytics platform", "data management platform",
    "enterprise data platform", "data unification", "information sharing platform",
    "investigative analytics", "crime analytics", "law enforcement analytics",
    "intelligence platform", "link analysis", "digital evidence",
    "evidence management", "situational awareness", "operational intelligence",
    "federated search", "enterprise search", "cross-system search",
    "entity resolution", "record deduplication", "identity resolution",
    "data deduplication", "fedramp", "cjis", "govcloud", "zero trust",
    "law enforcement platform", "public safety platform",
    "records management system", "fusion center", "crime gun intelligence",
    "body camera analytics", "community supervision", "offender management",
    "probation", "corrections platform", "court services",
    "IT modernization", "platform modernization", "legacy modernization",
    "platform replacement", "digital transformation",
    "artificial intelligence", "machine learning", "predictive analytics",
    "computer vision", "AI platform",
]


def _is_dod(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DOD_PATH_FRAGMENTS)


def _is_doj(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DOJ_PATH_FRAGMENTS)


def _is_dhs(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DHS_PATH_FRAGMENTS)


def fetch_doj_opportunities() -> list:
    from_cache = [o for o in _SAM_RESULTS_CACHE if _is_doj(o.agency)]
    if from_cache:
        print(f"[DOJ] {len(from_cache)} opportunities (from SAM cache)")
        return from_cache
    print("[DOJ] Cache empty — no fallback calls")
    return []


def fetch_dhs_opportunities() -> list:
    from_cache = [o for o in _SAM_RESULTS_CACHE if _is_dhs(o.agency)]
    if from_cache:
        print(f"[DHS] {len(from_cache)} opportunities (from SAM cache)")
        return from_cache
    print("[DHS] Cache empty — no fallback calls")
    return []


def fetch_dod_opportunities() -> list:
    from_cache = [o for o in _SAM_RESULTS_CACHE if _is_dod(o.agency)]
    if from_cache:
        print(f"[DoD] {len(from_cache)} opportunities (from SAM cache)")
    return from_cache


# ---------------------------------------------------------------------------
# SOURCE 2: FEDERAL REGISTER
# ---------------------------------------------------------------------------
def fetch_federal_register() -> list:
    results, seen_ids = [], set()
    today  = datetime.utcnow()
    since  = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    terms  = [
        "data analytics", "law enforcement analytics",
        "community supervision", "IT modernization",
        "investigative platform", "artificial intelligence",
        "digital evidence", "records management",
    ]
    for term in terms:
        try:
            params = (
                f"conditions[term]={requests.utils.quote(term)}"
                f"&conditions[publication_date][gte]={since}"
                f"&conditions[type][]=NOTICE&per_page=10&order=newest"
                f"&fields[]=document_number&fields[]=title&fields[]=abstract"
                f"&fields[]=publication_date&fields[]=agencies&fields[]=html_url"
            )
            r = requests.get(
                f"https://www.federalregister.gov/api/v1/documents.json?{params}",
                headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20,
            )
            if r.status_code != 200:
                continue
            for doc in r.json().get("results", []):
                doc_id = doc.get("document_number", "")
                if doc_id in seen_ids:
                    continue
                title = (doc.get("title", "") or "").strip()
                abstract = (doc.get("abstract", "") or "").strip()
                combined = f"{title} {abstract}".lower()
                if not any(s in combined for s in ["request for information", "sources sought",
                                                     "industry day", "market research", "rfi"]):
                    continue
                seen_ids.add(doc_id)
                agencies = ", ".join(a.get("name", "") for a in doc.get("agencies", []) if a.get("name"))
                opp = Opportunity(
                    title=title, notice_id=f"FR-{doc_id}",
                    agency=agencies or "Federal Agency",
                    posted_date=doc.get("publication_date", ""),
                    response_date="TBD",
                    description=abstract[:2000],
                    url=clean_url(doc.get("html_url", ""), "https://www.federalregister.gov"),
                    opp_type="Federal Register RFI", source="Federal Register",
                )
                results.append(score_opportunity(opp))
            time.sleep(0.2)
        except Exception as e:
            print(f"[FederalRegister] '{term}': {e}")
    print(f"[Federal Register] {len(results)} notices")
    return results


# ---------------------------------------------------------------------------
# SOURCE 3: USASPENDING — competitive intel
# ---------------------------------------------------------------------------
def fetch_usaspending_intel() -> list:
    results, seen_ids = [], set()
    today = datetime.utcnow()
    start = (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")
    batches = [
        ["law enforcement software"], ["data analytics"],
        ["community supervision"],    ["investigative platform"],
        ["palantir"],                 ["corrections software"],
    ]
    for keywords in batches:
        try:
            payload = {
                "subawards": False, "limit": 10, "page": 1,
                "filters": {
                    "keywords": keywords,
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": start, "end_date": end}],
                },
                "fields": ["Award ID", "Recipient Name", "Start Date", "End Date",
                           "Award Amount", "Awarding Agency", "Awarding Sub Agency",
                           "Description"],
                "sort": "Award Amount", "order": "desc",
            }
            r = requests.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            for award in r.json().get("results", []):
                aid = award.get("Award ID", "")
                nid = f"USA-{aid}"
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)
                amount    = award.get("Award Amount", 0) or 0
                recipient = award.get("Recipient Name", "Unknown")
                agency    = award.get("Awarding Agency", "")
                sub       = award.get("Awarding Sub Agency", "")
                desc      = (award.get("Description", "") or "")[:150]
                start_dt  = award.get("Start Date", "")
                end_dt    = award.get("End Date", "")
                opp = Opportunity(
                    title=f"[AWARD INTEL] {desc[:80] or 'Contract'} — {recipient}",
                    notice_id=nid,
                    agency=f"{agency} / {sub}",
                    posted_date=start_dt or end,
                    response_date="Watch for recompete",
                    description=(f"Award to {recipient} by {agency}. "
                                 f"Value: ${amount:,.0f}. Period: {start_dt} to {end_dt}. {desc}"),
                    url=clean_url(f"https://www.usaspending.gov/award/{aid}/",
                                  "https://www.usaspending.gov"),
                    opp_type="Award Intel", source="USASpending.gov",
                )
                results.append(score_opportunity(opp))
            time.sleep(0.5)
        except Exception as e:
            print(f"[USASpending] {keywords}: {e}")
    print(f"[USASpending] {len(results)} award intel records")
    return results


# ---------------------------------------------------------------------------
# SOURCE 4: AGENCY RSS FEEDS
# ---------------------------------------------------------------------------
def fetch_agency_rss_feeds() -> list:
    return []  # RSS feeds captured in industry news; this source reserved


# ---------------------------------------------------------------------------
# SOURCE 5: EVENTS INTELLIGENCE
# ---------------------------------------------------------------------------
KNOWN_EVENTS = [
    {"title": "IACP Annual Conference", "date": "2026-10-18", "location": "Boston, MA",
     "url": "https://www.theiacp.org/events/iacp-annual-conference",
     "tags": ["law enforcement", "public safety"]},
    {"title": "Corrections Technology Summit", "date": "2026-07-15", "location": "Nashville, TN",
     "url": "https://www.corrections.com", "tags": ["corrections", "supervision"]},
    {"title": "GovSec Conference", "date": "2026-06-10", "location": "Washington, DC",
     "url": "https://www.govsecinfo.com", "tags": ["government security", "law enforcement"]},
    {"title": "SEARCH Symposium", "date": "2026-05-20", "location": "New Orleans, LA",
     "url": "https://www.search.org", "tags": ["criminal justice", "technology"]},
]


def fetch_events_intelligence() -> list:
    results = []
    today   = datetime.utcnow()
    cutoff  = today + timedelta(days=90)
    for ev in KNOWN_EVENTS:
        try:
            ev_dt = datetime.strptime(ev["date"], "%Y-%m-%d")
            if ev_dt < today or ev_dt > cutoff:
                continue
            opp = Opportunity(
                title=ev["title"],
                notice_id=f"EVT-{ev['date']}-{ev['title'][:20].replace(' ','')}",
                agency="Industry Event",
                posted_date=today.strftime("%Y-%m-%d"),
                response_date=ev["date"],
                description=f"Industry event. Location: {ev.get('location', 'TBD')}",
                url=ev.get("url", ""),
                opp_type="Industry Day",
                source="Events Intelligence",
            )
            results.append(score_opportunity(opp))
        except Exception:
            pass
    print(f"[Events] {len(results)} upcoming events (next 90 days)")
    return results


# ---------------------------------------------------------------------------
# INDUSTRY NEWS
# ---------------------------------------------------------------------------
def fetch_industry_news() -> list[dict]:
    news   = []
    seen   = set()
    feeds  = [
        {"url": "https://fedscoop.com/feed/",                   "source": "FedScoop"},
        {"url": "https://www.nextgov.com/rss/all/",             "source": "Nextgov"},
        {"url": "https://gcn.com/rss-feeds/all.aspx",           "source": "GCN"},
        {"url": "https://www.govtech.com/public-safety/rss.xml","source": "GovTech"},
        {"url": "https://www.police1.com/rss/all/",             "source": "Police1"},
        {"url": "https://www.corrections1.com/rss/all/",        "source": "Corrections1"},
    ]
    keywords = [
        "law enforcement", "public safety", "data analytics", "artificial intelligence",
        "machine learning", "criminal justice", "corrections", "fedramp", "cjis",
        "records management", "predictive", "surveillance", "crime analytics",
    ]
    for feed in feeds:
        try:
            r = requests.get(feed["url"], headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:15]:
                t_el = item.find("title")
                l_el = item.find("link")
                d_el = item.find("description")
                p_el = item.find("pubDate")
                title = (t_el.text or "").strip() if t_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d_el.text or ""))).strip() if d_el is not None else ""
                url_  = (l_el.text or "").strip() if l_el is not None else ""
                date_ = (p_el.text or "").strip() if p_el is not None else ""
                if not title or title in seen:
                    continue
                combined = f"{title} {desc}".lower()
                if not any(kw in combined for kw in keywords):
                    continue
                seen.add(title)
                news.append({
                    "title": title, "url": clean_url(url_, ""),
                    "source": feed["source"], "date": date_[:16],
                    "summary": desc[:250],
                })
            time.sleep(0.2)
        except Exception as e:
            print(f"[IndustryNews] {feed['source']}: {e}")
    print(f"[Industry News] {len(news)} articles")
    return news[:15]


def fetch_growth_news() -> list[dict]:
    return []  # Placeholder — industry news covers this


# ---------------------------------------------------------------------------
# COMPETITOR INTELLIGENCE
# ---------------------------------------------------------------------------
COMPETITORS = [
    {"name": "Palantir",           "search": "Palantir law enforcement government",        "tags": ["palantir"]},
    {"name": "Axon",               "search": "Axon public safety technology",               "tags": ["axon"]},
    {"name": "ShotSpotter",        "search": "ShotSpotter gunshot detection",               "tags": ["shotspotter", "soundthinking"]},
    {"name": "Mark43",             "search": "Mark43 records management police",            "tags": ["mark43"]},
    {"name": "Tyler Technologies", "search": "Tyler Technologies criminal justice",         "tags": ["tyler technologies"]},
    {"name": "Motorola Solutions", "search": "Motorola Solutions public safety",            "tags": ["motorola solutions"]},
    {"name": "IBM i2",             "search": "IBM i2 law enforcement analytics",            "tags": ["ibm i2"]},
    {"name": "Esri",               "search": "Esri law enforcement government GIS",         "tags": ["esri"]},
    {"name": "Databricks",         "search": "Databricks government federal",               "tags": ["databricks"]},
    {"name": "Appriss",            "search": "Appriss corrections supervision",             "tags": ["appriss"]},
    {"name": "SuperCom",           "search": "SuperCom offender monitoring",                "tags": ["supercom"]},
    {"name": "Flock Safety",       "search": "Flock Safety license plate law enforcement", "tags": ["flock safety", "flock camera"]},
]

COMPETITOR_NEWS_FEEDS = [
    {"url": "https://fedscoop.com/feed/",                    "source": "FedScoop"},
    {"url": "https://www.nextgov.com/rss/all/",              "source": "Nextgov"},
    {"url": "https://gcn.com/rss-feeds/all.aspx",            "source": "GCN"},
    {"url": "https://www.govtech.com/public-safety/rss.xml", "source": "GovTech"},
    {"url": "https://www.police1.com/rss/all/",              "source": "Police1"},
    {"url": "https://www.corrections1.com/rss/all/",         "source": "Corrections1"},
    {"url": "https://defensescoop.com/feed/",                "source": "DefenseScoop"},
    {"url": "https://statescoop.com/feed/",                  "source": "StateScoop"},
]

COMPETITOR_NEWS_QUERIES = [
    ("Palantir",           "Palantir+federal+government+contract"),
    ("Axon",               "Axon+Enterprise+law+enforcement+technology"),
    ("ShotSpotter",        "ShotSpotter+OR+SoundThinking+police"),
    ("Mark43",             "Mark43+records+management+police"),
    ("Tyler Technologies", "Tyler+Technologies+public+safety+government"),
    ("Motorola Solutions", "Motorola+Solutions+law+enforcement+data"),
    ("IBM i2",             "IBM+i2+intelligence+analytics+government"),
    ("Esri",               "Esri+law+enforcement+public+safety+GIS"),
    ("Databricks",         "Databricks+government+law+enforcement+federal"),
    ("Appriss",            "Appriss+criminal+justice+data"),
    ("SuperCom",           "SuperCom+offender+monitoring+supervision"),
    ("Flock Safety",       "Flock+Safety+license+plate+law+enforcement"),
]


def fetch_competitor_intel() -> list[dict]:
    items_out  = []
    seen_titles = set()

    def _fetch_gnews(comp_name: str, query: str, max_items: int = 5):
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        out = []
        try:
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PeregrineScanner/2.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if r.status_code != 200:
                return out
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:max_items]:
                t_el = item.find("title")
                l_el = item.find("link")
                d_el = item.find("description")
                p_el = item.find("pubDate")
                title = (t_el.text or "").strip() if t_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d_el.text or ""))).strip() if d_el is not None else ""
                url_  = (l_el.text or "").strip() if l_el is not None else ""
                date_ = (p_el.text or "").strip() if p_el is not None else ""
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                out.append({
                    "competitor": comp_name,
                    "title": title,
                    "url": clean_url(url_, ""),
                    "source": "Google News",
                    "date": date_[:16] if date_ else "",
                    "summary": desc[:300],
                })
        except Exception as e:
            print(f"[CompetitorIntel] Google News {comp_name}: {e}")
        return out

    for comp_name, query in COMPETITOR_NEWS_QUERIES:
        items_out.extend(_fetch_gnews(comp_name, query, max_items=2))
        time.sleep(0.2)

    # USASpending recompetes
    recompete_targets = [
        ("Palantir",           ["palantir"]),
        ("Axon",               ["axon enterprise", "axon public safety"]),
        ("Tyler Technologies", ["tyler technologies"]),
        ("Motorola Solutions", ["motorola solutions"]),
        ("Mark43",             ["mark43"]),
        ("IBM i2",             ["ibm i2", "i2 analyst"]),
        ("ShotSpotter",        ["shotspotter", "soundthinking"]),
        ("Flock Safety",       ["flock safety"]),
    ]
    today    = datetime.utcnow()
    end_soon = (today + timedelta(days=365)).strftime("%Y-%m-%d")
    for comp_name, keywords in recompete_targets:
        try:
            payload = {
                "subawards": False, "limit": 5, "page": 1,
                "filters": {
                    "keywords": keywords,
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": "2020-01-01", "end_date": end_soon}],
                },
                "fields": ["Award ID", "Recipient Name", "Start Date", "End Date",
                           "Award Amount", "Awarding Agency", "Awarding Sub Agency", "Description"],
            }
            r = requests.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=20,
            )
            if r.status_code != 200:
                continue
            for award in r.json().get("results", []):
                end_str = (award.get("End Date", "") or "")[:10]
                if not end_str:
                    continue
                try:
                    end_dt    = datetime.strptime(end_str, "%Y-%m-%d")
                    days_left = (end_dt - today).days
                    if days_left < 0 or days_left > 365:
                        continue
                    urgency  = ("🔴 Expires < 90d" if days_left < 90
                                else "🟡 Expires < 180d" if days_left < 180
                                else "🟢 Expires < 1yr")
                    agency   = award.get("Awarding Agency", "")
                    amount   = award.get("Award Amount", 0) or 0
                    desc     = (award.get("Description", "") or "")[:150]
                    award_id = award.get("Award ID", "")
                    items_out.append({
                        "competitor": f"{comp_name} — Recompete Alert",
                        "title":  f"{urgency} | {comp_name} @ {agency} — ${amount:,.0f}",
                        "url":    clean_url(f"https://www.usaspending.gov/award/{award_id}/",
                                            "https://www.usaspending.gov"),
                        "source": "USASpending.gov",
                        "date":   end_str,
                        "summary": f"Contract ends {end_str} ({days_left}d). {desc}",
                        "is_recompete": True,
                        "days_left": days_left,
                    })
                except Exception:
                    continue
            time.sleep(0.3)
        except Exception as e:
            print(f"[Recompetes] {comp_name}: {e}")

    print(f"[Competitor Intel] {len(items_out)} signals")
    return items_out


# ---------------------------------------------------------------------------
# GRANTS / FEDERAL FUNDING
# ---------------------------------------------------------------------------
def fetch_federal_funding() -> list[dict]:
    items, seen = [], set()
    today = datetime.utcnow()
    since = (today - timedelta(days=10))
    TECH_TERMS = [
        "law enforcement technology grant", "public safety technology grant",
        "criminal justice data analytics", "records management system grant",
        "community supervision technology", "offender management system",
        "crime gun intelligence center", "digital evidence management",
    ]
    CUSTOMER_TERMS = [
        "byrne jag", "edward byrne", "justice assistance grant",
        "cops office technology", "community oriented policing",
        "second chance act", "justice reinvestment initiative",
        "violence reduction", "community violence intervention",
        "smart policing initiative", "data-driven policing",
        "homeland security grant program",
    ]
    GRANT_EXCLUSIONS = [
        "treatment court", "drug court", "mental health court",
        "substance abuse treatment", "behavioral health", "mental health services",
        "victim services", "victim compensation", "domestic violence shelter",
        "housing assistance", "homeless", "nutrition", "food bank",
        "scholarship", "fellowship", "research only",
        "road", "bridge", "wildfire", "flood", "hurricane",
        "healthcare", "dental", "hospital", "public health",
        "body armor", "equipment purchase", "vehicle", "construction",
    ]
    TECH_SIGNALS = [
        "technology", "software", "data analytics", "data platform",
        "information system", "digital", "analytics platform",
        "records management", "information technology", "data-driven",
    ]
    PROGRAM_SIGNALS = [
        "byrne jag", "edward byrne", "justice assistance",
        "cops office", "second chance act", "justice reinvestment",
        "violence reduction", "community violence intervention",
        "smart policing", "nibin", "crime gun", "data-driven policing",
    ]
    for kw in TECH_TERMS + CUSTOMER_TERMS:
        try:
            r = requests.post(
                "https://apply07.grants.gov/grantsws/rest/opportunities/search/",
                json={"keyword": kw, "oppStatuses": "posted", "rows": 8, "sortBy": "openDate|desc"},
                headers={"Content-Type": "application/json", "User-Agent": HEADERS["User-Agent"]},
                timeout=20,
            )
            if r.status_code != 200:
                continue
            for opp in r.json().get("oppHits", []):
                opp_id   = str(opp.get("id", ""))
                if opp_id in seen:
                    continue
                title    = (opp.get("title", "") or "").strip()
                synopsis = (opp.get("synopsis", "") or "").strip()
                agency   = (opp.get("agencyName", "") or "").strip()
                combined = f"{title} {synopsis}".lower()
                if any(excl in combined for excl in GRANT_EXCLUSIONS):
                    continue
                if not any(s in combined for s in TECH_SIGNALS) and \
                   not any(p in combined for p in PROGRAM_SIGNALS):
                    continue
                seen.add(opp_id)
                is_tech = kw in TECH_TERMS
                items.append({
                    "type":       "🎯 Direct Tech Grant" if is_tech else "💰 Customer Budget Signal",
                    "title":      title,
                    "agency":     agency,
                    "number":     opp.get("number", ""),
                    "open_date":  opp.get("openDate", ""),
                    "close_date": opp.get("closeDate", ""),
                    "summary":    synopsis[:350],
                    "url":        clean_url(f"https://www.grants.gov/search-results-detail/{opp_id}",
                                            "https://www.grants.gov"),
                    "source":     "grants.gov",
                    "relevance":  kw,
                })
            time.sleep(0.2)
        except Exception as e:
            print(f"[Funding] '{kw}': {e}")

    seen_titles = set()
    deduped = []
    for item in items:
        key = item["title"][:60].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(item)
    deduped.sort(key=lambda x: x.get("open_date", ""), reverse=True)
    print(f"[Federal Funding] {len(deduped)} relevant grants")
    return deduped[:15]


def fetch_agency_budget_news() -> list[dict]:
    items, seen = [], set()
    BUDGET_QUERIES = [
        ("DOJ Budget",       "Department+of+Justice+budget+technology+data+analytics"),
        ("ATF Technology",   "ATF+Alcohol+Tobacco+Firearms+technology+data"),
        ("FBI Technology",   "FBI+technology+data+analytics+platform"),
        ("DHS Budget",       "Department+of+Homeland+Security+budget+technology"),
        ("ICE Technology",   "ICE+immigration+enforcement+technology+data"),
        ("CISA Budget",      "CISA+cybersecurity+budget+technology"),
        ("Byrne JAG News",   "Byrne+JAG+grant+law+enforcement+technology"),
        ("Violence Reduction","community+violence+intervention+grant+technology"),
        ("NIBIN Funding",    "NIBIN+crime+gun+intelligence+funding+ATF"),
    ]
    for label, query in BUDGET_QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PeregrineScanner/2.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            count = 0
            for item in root.findall(".//item"):
                if count >= 2:
                    break
                t_el = item.find("title")
                l_el = item.find("link")
                d_el = item.find("description")
                p_el = item.find("pubDate")
                title = (t_el.text or "").strip() if t_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d_el.text or ""))).strip() if d_el is not None else ""
                url_  = (l_el.text or "").strip() if l_el is not None else ""
                date_ = (p_el.text or "").strip() if p_el is not None else ""
                if not title or title in seen:
                    continue
                seen.add(title)
                items.append({
                    "label": label, "title": title, "summary": desc[:280],
                    "url": clean_url(url_, ""), "date": date_[:16], "source": "Google News",
                })
                count += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[BudgetNews] {label}: {e}")
    print(f"[Agency Budget News] {len(items)} signals")
    return items[:20]


# ---------------------------------------------------------------------------
# EMAIL BUILDING
# ---------------------------------------------------------------------------
def deduplicate_and_rank(opps: list) -> list:
    seen = set()
    out  = []
    for o in sorted(opps, key=lambda x: x.score, reverse=True):
        if is_expired(o):
            continue
        key = o.notice_id or o.title[:60].lower()
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def build_section(title: str, opps: list) -> str:
    if not opps:
        return ""
    rows = ""
    for o in opps[:20]:
        link = (f'<a href="{o.url}" style="font-weight:700;font-size:14px;color:#0057b8;text-decoration:none;">{o.title[:120]}</a>'
                if o.url else f'<span style="font-weight:700;font-size:14px;color:#333;">{o.title[:120]}</span>')
        reasons_html = ""
        if o.score_reasons:
            bullets = "".join(f"<li>{r}</li>" for r in o.score_reasons[:4])
            reasons_html = f'<ul style="margin:4px 0 0 0;padding-left:18px;font-size:12px;color:#555;">{bullets}</ul>'
        deadline = ""
        if o.response_date and o.response_date != "TBD":
            try:
                d = parse_date_flexible(o.response_date)
                if d:
                    days = (d - datetime.utcnow()).days
                    if days <= 7:
                        deadline = f' <span style="background:#c0392b;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px;">Due in {days}d</span>'
                    elif days <= 30:
                        deadline = f' <span style="background:#e67e22;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px;">Due in {days}d</span>'
            except Exception:
                pass
        rows += f"""
        <div style="border:1px solid #e8e8e8;border-radius:6px;padding:12px;margin-bottom:10px;background:#fff;">
          <div style="margin-bottom:6px;">{link}{deadline}</div>
          <div style="font-size:12px;color:#666;">🏛 {o.agency[:80]} &nbsp;·&nbsp; 📬 Posted: {o.posted_date[:10]}</div>
          <div style="font-size:11px;color:#999;margin-top:2px;">
            Source: {o.source} &nbsp;·&nbsp; Score: {o.score}pts &nbsp;·&nbsp;
            <a href="{o.url}" style="color:#0057b8;">View on SAM.gov</a>
          </div>
          {reasons_html}
        </div>"""
    return f"""
    <div style="margin:20px 0 6px">
      <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">{title} ({len(opps)})</h2>
      {rows}
    </div>"""


def build_award_intel_section(awards: list) -> str:
    if not awards:
        return ""
    rows = ""
    for o in awards[:5]:
        rows += f"""
        <div style="border-left:3px solid #95a5a6;padding:8px 10px;margin-bottom:8px;background:#f9f9f9;">
          <div style="font-size:13px;font-weight:600;color:#333;">{o.title[:100]}</div>
          <div style="font-size:11px;color:#888;">{o.agency[:70]} · {o.posted_date[:10]}</div>
        </div>"""
    return f"""
    <div style="margin:20px 0 6px">
      <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">📊 Award Intel — Recent Contract Wins</h2>
      {rows}
    </div>"""


def _grant_why_it_fits(item: dict) -> list:
    title  = (item.get("title","") or "").lower()
    summ   = (item.get("summary","") or "").lower()
    rele   = (item.get("relevance","") or "").lower()
    rtype  = (item.get("type","") or "").lower()
    text   = f" {title} {summ} {rele} "
    reasons = []
    if "direct tech" in rtype:
        reasons.append(("🎯","Direct Technology Grant","Funding for software/data platform procurement"))
    else:
        reasons.append(("💰","Customer Budget Signal","Funding flowing to agencies that buy Peregrine"))
    cap_checks = [
        (("data integrat","data analytics","data platform","information sharing"),
         "⬡","Data Integration","Peregrine unifies data from RMS, CAD, jail, court, and federal systems"),
        (("investigative","crime analytics","intelligence platform","link analysis","digital evidence"),
         "◎","Investigative Analytics","Peregrine surfaces patterns and links for investigators"),
        (("community supervision","probation","parole","offender management","reentry","second chance","corrections"),
         "⬡","Corrections & Supervision","Peregrine deployed at CSOSA for offender data analytics"),
        (("law enforcement","public safety","police","fusion center","records management"),
         "⬟","Public Safety","Direct LE agency funding — Peregrine's primary buyer"),
        (("byrne jag","bjag","edward byrne","justice assistance"),
         "💵","Byrne JAG","Most flexible LE grant — agencies routinely use for analytics platforms"),
        (("cops office","community oriented policing"),
         "👮","COPS Office","COPS grants fund technology and data systems"),
        (("violence reduction","gun violence","antiviolence","nibin"),
         "🎯","Violence Reduction","Funds NIBIN/analytics platforms Peregrine provides to ATF"),
        (("second chance","reentry","recidivism","justice reinvestment"),
         "🔄","Reentry/Justice Reform","Funds supervision tech and offender data systems"),
    ]
    for terms, icon, cname, desc in cap_checks:
        if any(t in text for t in terms):
            reasons.append((icon, cname, desc))
    seen = set()
    deduped = []
    for r in reasons:
        if r[1] not in seen:
            seen.add(r[1])
            deduped.append(r)
    return deduped[:4]


def build_funding_section(funding_items: list) -> str:
    if not funding_items:
        return """
        <div style="margin:20px 0 6px">
          <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">💰 Federal Funding Opportunities</h2>
          <p style="color:#aaa;font-size:13px;font-style:italic">No relevant funding in last 10 days.</p>
        </div>"""
    rows = ""
    for item in funding_items[:12]:
        badge_color = "#27ae60" if "Direct" in item["type"] else "#0057b8"
        badge_text  = item["type"].replace("🎯 ","").replace("💰 ","")
        close_html  = f' &middot; <strong>Closes:</strong> {item["close_date"]}' if item.get("close_date") else ""
        link = (f'<a href="{item["url"]}" style="font-weight:700;font-size:14px;color:#0057b8;text-decoration:none;">{item["title"][:110]}</a>'
                if item.get("url") else f'<span style="font-weight:700;font-size:14px;color:#333;">{item["title"][:110]}</span>')
        reasons = _grant_why_it_fits(item)
        why_html = ""
        if reasons:
            bullets = "".join(
                f'<li><strong>{ico} {lbl}:</strong> {dsc}</li>'
                for ico, lbl, dsc in reasons
            )
            why_html = (
                '<div style="margin-top:8px;padding:8px 10px;background:#f8fafe;'
                'border-left:3px solid #0057b8;border-radius:0 4px 4px 0;">'
                '<div style="font-size:11px;font-weight:700;color:#0057b8;margin-bottom:4px;'
                'text-transform:uppercase;letter-spacing:0.5px;">Why It Fits</div>'
                f'<ul style="margin:0;padding-left:16px;font-size:12px;line-height:1.6;">{bullets}</ul>'
                '</div>'
            )
        rows += (
            '<div style="border:1px solid #e8e8e8;border-radius:6px;padding:12px;margin-bottom:10px;background:#fff;">'
            '<div style="margin-bottom:6px;">'
            f'<span style="background:{badge_color};color:#fff;font-size:10px;font-weight:700;'
            f'padding:2px 7px;border-radius:10px;">{badge_text}</span>'
            f'<span style="font-size:11px;color:#888;margin-left:8px;">'
            f'{item["source"]} &middot; {item.get("open_date","")[:10]}</span>'
            '</div>'
            f'<div style="margin-bottom:4px;">{link}</div>'
            f'<div style="font-size:12px;color:#666;margin-bottom:4px;">'
            f'&#x1F3DB; {item["agency"][:90]}{close_html}</div>'
            + (f'<div style="font-size:12px;color:#555;line-height:1.5;">{item.get("summary","")[:280]}</div>'
               if item.get("summary") else "")
            + why_html + '</div>'
        )
    return (
        '<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">'
        f'&#x1F4B0; Federal Funding — Last 10 Days ({len(funding_items)})</h2>'
        '<p style="font-size:12px;color:#888;margin:0 0 10px;">'
        'Direct tech grants &middot; Customer budget signals</p>'
        f'{rows}</div>'
    )


def build_budget_news_section(budget_news: list) -> str:
    if not budget_news:
        return ""
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in budget_news:
        grouped[item["label"]].append(item)
    rows = ""
    for label in sorted(grouped.keys()):
        for s in grouped[label][:2]:
            link = (f'<a href="{s["url"]}" style="color:#0057b8;text-decoration:none;font-weight:600;">{s["title"][:95]}</a>'
                    if s.get("url") else f'<span style="font-weight:600;">{s["title"][:95]}</span>')
            summary_html = ("<div style='font-size:12px;color:#555;margin-top:2px;'>" + s.get("summary","")[:180] + "</div>") if s.get("summary") else ""
            rows += (
                f'<div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #f0f0f0;">'
                f'<div style="font-size:12px;font-weight:700;color:#555;margin-bottom:2px;">&#x1F4E1; {label}</div>'
                f'<div style="font-size:13px;">{link}</div>'
                f'<div style="font-size:11px;color:#888;">{s["source"]} &middot; {s["date"][:10]}</div>'
                f'{summary_html}'
                f'</div>'
            )
    return (
        '<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">'
        f'&#x1F4E1; Agency Budget &amp; Spending Signals ({len(budget_news)})</h2>'
        f'{rows}</div>'
    )


def build_competitor_section(intel_items: list, growth_items: list = None) -> str:
    palantir_rc = sorted(
        [i for i in intel_items if i.get("is_recompete") and "Palantir" in i.get("competitor","")],
        key=lambda x: x.get("days_left", 999)
    )
    other_rc = sorted(
        [i for i in intel_items if i.get("is_recompete") and "Palantir" not in i.get("competitor","")],
        key=lambda x: x.get("days_left", 999)
    )
    news_stories = [i for i in intel_items if not i.get("is_recompete")]

    def _rc_rows(rcs):
        rows = ""
        for rc in rcs[:6]:
            link = (f'<a href="{rc["url"]}" style="font-weight:700;color:#c0392b;text-decoration:none;">{rc["title"][:120]}</a>'
                    if rc.get("url") else f'<span style="font-weight:700;color:#c0392b;">{rc["title"][:120]}</span>')
            rc_summary = ("<div style='font-size:12px;color:#555;margin-top:2px;'>" + rc.get("summary","")[:200] + "</div>") if rc.get("summary") else ""
            rows += (
                '<div style="border-left:3px solid #c0392b;padding:8px 10px;margin-bottom:8px;'
                'background:#fff9f9;border-radius:0 4px 4px 0;">'
                f'<div style="font-size:13px;">{link}</div>'
                f'<div style="font-size:11px;color:#888;">Expires: {rc["date"]} &middot; {rc["source"]}</div>'
                f'{rc_summary}'
                '</div>'
            )
        return rows

    palantir_html = ""
    if palantir_rc:
        palantir_html = (
            '<div style="margin-bottom:16px;border:1px solid #f5c6cb;border-radius:8px;padding:14px;background:#fff9f9;">'
            f'<div style="font-weight:700;font-size:13px;color:#c0392b;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px;">'
            f'🎯 Palantir Recompete Opportunities ({len(palantir_rc)} expiring)</div>'
            '<p style="font-size:12px;color:#666;margin:0 0 8px;">Active Palantir contracts expiring within 12 months — displacement opportunities for Peregrine.</p>'
            f'{_rc_rows(palantir_rc)}</div>'
        )

    other_rc_html = ""
    if other_rc:
        other_rc_html = (
            '<div style="margin-bottom:16px;">'
            f'<div style="font-weight:700;font-size:13px;color:#e67e22;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px;">'
            f'⚡ Other Competitor Recompetes ({len(other_rc)} expiring)</div>'
            f'{_rc_rows(other_rc)}</div>'
        )

    news_rows = ""
    if news_stories:
        from collections import defaultdict
        grouped = defaultdict(list)
        for item in news_stories:
            grouped[item["competitor"]].append(item)
        for comp_name in sorted(grouped.keys()):
            stories = grouped[comp_name][:2]
            story_html = ""
            for s in stories:
                link = (f'<a href="{s["url"]}" style="color:#0057b8;text-decoration:none;font-weight:600;">{s["title"][:90]}</a>'
                        if s.get("url") else f'<span style="font-weight:600;color:#333;">{s["title"][:90]}</span>')
                ns_summary = ("<div style='font-size:12px;color:#555;margin-top:2px;'>" + s.get("summary","")[:200] + "</div>") if s.get("summary") else ""
                story_html += (
                    '<div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #f0f0f0;">'
                    f'<div style="font-size:13px;">{link}</div>'
                    f'<div style="font-size:11px;color:#888;margin-top:2px;">{s["source"]} &middot; {s["date"][:10]}</div>'
                    f'{ns_summary}'
                    '</div>'
                )
            news_rows += (
                f'<div style="margin-bottom:14px;">'
                f'<div style="font-weight:700;font-size:12px;color:#555;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">⚔️ {comp_name}</div>'
                f'{story_html}</div>'
            )

    total = len(palantir_rc) + len(other_rc) + len(news_stories)
    monitoring = ", ".join(c["name"] for c in COMPETITORS)
    news_or_fallback = news_rows or "<p style='color:#aaa;font-size:13px;font-style:italic'>No competitor news today.</p>"
    return (
        f'<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">&#x1F50E; Competitor Intelligence ({total} signals)</h2>'
        f'<p style="font-size:12px;color:#888;margin:0 0 12px;">Monitoring: {monitoring}</p>'
        f'{palantir_html}{other_rc_html}{news_or_fallback}'
        f'</div>'
    )


def build_news_section(news_items: list) -> str:
    if not news_items:
        return ""
    rows = ""
    for item in news_items[:10]:
        link = (f'<a href="{item["url"]}" style="color:#0057b8;text-decoration:none;font-weight:600;">{item["title"][:100]}</a>'
                if item.get("url") else f'<span style="font-weight:600;">{item["title"][:100]}</span>')
        ni_summary = (("<div style='font-size:12px;color:#555;margin-top:2px;'>" + item.get("summary","")[:200] + "</div>") if item.get("summary") else "")
        rows += (
            f'<div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #f0f0f0;">'
            f'<div style="font-size:13px;">{link}</div>'
            f'<div style="font-size:11px;color:#888;margin-top:2px;">{item["source"]} &middot; {item["date"][:10]}</div>'
            f'{ni_summary}'
            f'</div>'
        )
    return (
        f'<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">📰 Industry News &amp; Market Signals ({len(news_items)})</h2>'
        f'{rows}</div>'
    )


def _possible_fits(non_events: list, tiers: dict, shown: set = None) -> list:
    shown = shown or set()
    def _k(o): return (o.notice_id or o.title[:60].lower()).strip()
    def _unseen(lst): return [o for o in lst if _k(o) not in shown]
    possible = _unseen([o for o in tiers.get("possible", []) if o.source != "Events Intelligence"])
    if possible:
        return possible
    low = sorted(_unseen([o for o in non_events if o.tier == "⚪ Low Fit" and o.score > 0]),
                 key=lambda x: x.score, reverse=True)
    if low:
        return low[:10]
    TITLE_KW = ["analytics platform", "data platform", "software platform",
                "analytics solution", "data integration", "law enforcement analytics"]
    return sorted([o for o in _unseen(non_events)
                   if o.tier not in ("⛔ Not a Fit", "⛔ Expired")
                   and any(kw in o.title.lower() for kw in TITLE_KW)],
                  key=lambda x: x.score, reverse=True)[:10]


def build_html_email(opps: list, run_date: str,
                     source_counts: dict = None,
                     news_items: list = None,
                     competitor_items: list = None,
                     growth_items: list = None,
                     funding_items: list = None,
                     budget_news: list = None) -> str:

    source_counts = source_counts or {}
    non_events    = [o for o in opps if o.source != "Events Intelligence"]
    events        = [o for o in opps if o.source == "Events Intelligence"]
    usa_intel     = [o for o in non_events if o.source == "USASpending.gov"]
    non_events    = [o for o in non_events if o.source != "USASpending.gov"]

    def _key(o): return (o.notice_id or o.title[:60].lower()).strip()
    def _dedup(lst):
        seen = set(); out = []
        for o in lst:
            k = _key(o)
            if k not in seen: seen.add(k); out.append(o)
        return out

    shown = set()
    strong_list = _dedup([o for o in non_events if "Strong" in o.tier])
    shown.update(_key(o) for o in strong_list)
    good_list = _dedup([o for o in non_events if "Good" in o.tier and _key(o) not in shown])
    shown.update(_key(o) for o in good_list)
    possible_list = _dedup([o for o in non_events if "Possible" in o.tier and _key(o) not in shown])
    shown.update(_key(o) for o in possible_list)
    low_fit_list = _dedup([o for o in non_events if o.tier == "⚪ Low Fit" and o.score > 0 and _key(o) not in shown])
    shown.update(_key(o) for o in low_fit_list)

    tiers = {"strong": strong_list, "good": good_list, "possible": possible_list}

    strong   = len(strong_list)
    good     = len(good_list)
    possible = len(possible_list)

    sc_rows = "".join(
        f'<tr><td style="padding:3px 10px;color:#555;font-size:12px;">{k}</td>'
        f'<td style="padding:3px 10px;font-weight:700;color:#222;font-size:12px;">{v}</td></tr>'
        for k, v in sorted(source_counts.items())
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:'Helvetica Neue',Arial,sans-serif;background:#f5f5f5;margin:0;padding:0}}
.wrap{{max-width:720px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden}}
.header{{background:#0057b8;padding:24px 28px;color:#fff}}
.content{{padding:20px 28px}}
</style></head><body>
<div class="wrap">
<div class="header">
  <div style="font-size:22px;font-weight:700;letter-spacing:-0.5px;">🦅 Peregrine Daily Scanner</div>
  <div style="font-size:14px;opacity:0.85;margin-top:4px;">{run_date}</div>
  <div style="margin-top:12px;display:flex;gap:16px;flex-wrap:wrap;">
    <span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">🟢 {strong} Strong</span>
    <span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">🟡 {good} Good</span>
    <span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">🔵 {possible} Possible</span>
  </div>
</div>
<div class="content">
  <details style="margin-bottom:16px;border:1px solid #eee;border-radius:6px;padding:8px 12px;">
    <summary style="font-size:12px;color:#888;cursor:pointer;">Sources searched today</summary>
    <table style="margin-top:8px;border-collapse:collapse;">{sc_rows}</table>
  </details>

  {build_section("🟢 Strong Fit — Act Now", strong_list)}
  {build_section("🟡 Good Fit — Review Today", good_list)}
  {build_section("🔵 Possible Fit — Review These", _possible_fits(non_events, tiers, shown))}
  {build_section("⚪ Low Fit — Any Keyword Match", low_fit_list)}
  {build_award_intel_section(usa_intel[:5])}
  {build_competitor_section(competitor_items or [], growth_items=growth_items or [])}
  {build_funding_section(funding_items or [])}
  {build_budget_news_section(budget_news or [])}
  {build_news_section(news_items or [])}
  {build_section("🎤 Events & Conferences (Next 3 Months)", sorted(events, key=lambda x: x.score, reverse=True))}
</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# EMAIL SEND
# ---------------------------------------------------------------------------
def send_email(html_body: str, subject: str):
    api_key  = os.environ.get("SENDGRID_API_KEY", "")
    email_to = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
    email_from = os.environ.get("EMAIL_FROM", "mikefkelly26@gmail.com")
    if not api_key:
        print("[Email] No SENDGRID_API_KEY — skipping send")
        return
    payload = {
        "personalizations": [{"to": [{"email": email_to}]}],
        "from": {"email": email_from, "name": "Peregrine Federal Scanner"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }
    try:
        r = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        if r.status_code in (200, 202):
            print(f"[Email] Sent to {email_to} ✓")
        else:
            print(f"[Email] Send failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[Email] Error: {e}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    today    = datetime.utcnow()
    run_date = today.strftime("%B %d, %Y")
    print(f"\n{'='*60}")
    print(f"  Peregrine Daily Scanner — {run_date}")
    print(f"{'='*60}")

    SAM_KEY = os.environ.get("SAM_API_KEY", "")
    SG_KEY  = os.environ.get("SENDGRID_API_KEY", "")
    ET_TO   = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
    ET_FROM = os.environ.get("EMAIL_FROM", "mikefkelly26@gmail.com")
    print(f"[Config] SAM_API_KEY set:      {'YES' if SAM_KEY else 'NO'}")
    print(f"[Config] SENDGRID_API_KEY set: {'YES' if SG_KEY else 'NO'}")
    print(f"[Config] EMAIL_TO:             {ET_TO}")
    print(f"[Config] EMAIL_FROM:           {ET_FROM}")

    source_counts = {}
    all_opps      = []

    sources = [
        ("SAM.gov",           fetch_sam_gov),
        ("DOJ",               fetch_doj_opportunities),
        ("DHS",               fetch_dhs_opportunities),
        ("DoD",               fetch_dod_opportunities),
        ("Federal Register",  fetch_federal_register),
        ("USASpending.gov",   fetch_usaspending_intel),
        ("Agency RSS",        fetch_agency_rss_feeds),
        ("Events",            fetch_events_intelligence),
    ]
    for label, fn in sources:
        print(f"\n[{label}] Fetching...")
        try:
            batch = fn()
            source_counts[label] = len(batch)
            all_opps.extend(batch)
            # Populate cache after SAM.gov runs
            if label == "SAM.gov":
                _SAM_RESULTS_CACHE.clear()
                _SAM_RESULTS_CACHE.extend(batch)
        except Exception as e:
            print(f"[{label}] FAILED: {e}")
            source_counts[label] = 0

    print(f"\n[Scoring] Deduplicating and ranking {len(all_opps)} raw opportunities...")
    ranked = deduplicate_and_rank(all_opps)
    print(f"[Scoring] {len(ranked)} unique active opportunities after dedup")

    strong   = sum(1 for o in ranked if "Strong" in o.tier)
    good     = sum(1 for o in ranked if "Good" in o.tier)
    possible = sum(1 for o in ranked if "Possible" in o.tier)
    print(f"[Tiers] 🟢 Strong: {strong}  🟡 Good: {good}  🔵 Possible: {possible}")

    print("\n[Industry News] Fetching...")
    try:
        news_items = fetch_industry_news()
        source_counts["Industry News"] = len(news_items)
    except Exception as e:
        print(f"[Industry News] FAILED: {e}")
        news_items = []

    print("\n[Competitor Intel] Fetching...")
    try:
        competitor_items = fetch_competitor_intel()
        source_counts["Competitor Intel"] = len(competitor_items)
    except Exception as e:
        print(f"[Competitor Intel] FAILED: {e}")
        competitor_items = []

    growth_items = []
    if not competitor_items:
        try:
            growth_items = fetch_growth_news()
        except Exception as e:
            print(f"[Growth News] FAILED: {e}")

    print("\n[Federal Funding] Fetching...")
    try:
        funding_items = fetch_federal_funding()
        source_counts["Federal Funding"] = len(funding_items)
    except Exception as e:
        print(f"[Federal Funding] FAILED: {e}")
        funding_items = []

    print("\n[Budget News] Fetching...")
    try:
        budget_news = fetch_agency_budget_news()
        source_counts["Budget News"] = len(budget_news)
    except Exception as e:
        print(f"[Budget News] FAILED: {e}")
        budget_news = []

    # Build subject
    if strong == 0 and good == 0 and possible == 0:
        subject = f"Peregrine Daily Scanner | No Matches Today | {today.strftime('%b %d')}"
    elif strong >= 1:
        subject = f"Peregrine Daily Scanner | {strong} Strong · {good} Good · {possible} Possible | {today.strftime('%b %d')}"
    else:
        subject = f"Peregrine Daily Scanner | {good} Good · {possible} Possible Fits | {today.strftime('%b %d')}"

    html = build_html_email(
        ranked, run_date, source_counts,
        news_items=news_items,
        competitor_items=competitor_items,
        growth_items=growth_items,
        funding_items=funding_items,
        budget_news=budget_news,
    )

    send_email(html, subject)

    fname = f"digest_{today.strftime('%Y%m%d')}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[Done] Digest saved: {fname}")
    print(f"[Done] Subject: {subject}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print(f"[FATAL ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        raise
