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
    """Return True if the response deadline has clearly passed (2-day grace)."""
    grace = datetime.utcnow() - timedelta(days=2)
    for date_str in [opp.response_date, opp.posted_date]:
        dt = parse_date_flexible(date_str)
        if dt:
            return dt < grace
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
            "data platform", "data system", "data environment",
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
        # Peregrine's primary market — LE agencies, public safety, fusion centers
        "Public Safety & Law Enforcement", 20,
        [
            # Direct market terms
            "law enforcement", "public safety",
            "police department", "police", "sheriff",
            "criminal justice", "criminal investigation",
            # Peregrine-specific integrations
            "nibin", "etrace", "crime gun", "ballistic",
            "cgic", "crime gun intelligence",
            "records management system", "rms",
            "computer-aided dispatch", "cad system",
            "cad software", "dispatch system",
            # Mission areas
            "first responder", "violent crime",
            "gang", "crime reduction",
            "body camera", "evidence management",
            "fusion center", "law enforcement analytics",
            "policing platform", "public safety software",
            "public safety platform", "public safety technology",
            # Broader triggers
            "justice", "corrections", "courts",
            "prosecution", "investigation platform",
            "crime", "incident management",
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
            "supervised release", "correctional",
            "offender tracking", "supervision software",
            "supervision system", "case management",
            "supervision", "offender supervision",
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
            "system modernization", "system replacement",
            "data platform upgrade", "modernization",
            "it modernization", "digital transformation",
            "software modernization", "cloud migration",
            "application modernization",
        ],
    ),
    (
        # Peregrine embeds AI/ML for investigative decision support
        "AI & Machine Learning", 22,
        [
            "artificial intelligence", "machine learning",
            "ai/ml", "ai platform", "ai solution",
            "ai system", "ai services",
            "generative ai", "large language model", "llm",
            "natural language processing", "nlp",
            "computer vision", "predictive model",
            "decision support", "decision support system",
            "automated analysis", "intelligent automation",
            "algorithmic", "ai-powered",
            "ai for law enforcement", "ai public safety",
            "responsible ai", "explainable ai",
            "ai governance", "ai analytics",
            # Shorter triggers
            "automation", "automated",
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
    text = f"{opp.title} {opp.description} {naics_hint}".lower()
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
    saas_hits = []  # Track SaaS hits separately — only count if core cluster also matches

    for cap_name, cap_points, phrases in CAPABILITY_CLUSTERS:
        hits = [p for p in phrases if p.lower() in text]
        # When description is missing/short, also match against title alone
        if not hits and len(opp.description) < 80:
            hits = [p for p in phrases if p.lower() in title_only]
        if not hits:
            continue

        # Secure SaaS cluster: defer scoring — only add if a core cap also matched
        if cap_name.startswith("Secure Government SaaS"):
            saas_hits = hits
            continue

        score += cap_points
        clusters_matched += 1
        top_hits = sorted(hits, key=len, reverse=True)[:3]
        reasons.append(f"✓ {cap_name}: matched '{top_hits[0]}'" +
                      (f" + {len(hits)-1} more" if len(hits) > 1 else ""))

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
# SOURCE 1: SAM.GOV API v2
# Requires: SAM_API_KEY (free at sam.gov)
# ---------------------------------------------------------------------------
def fetch_sam_gov() -> list[Opportunity]:
    results = []
    today = datetime.utcnow()
    from_date = (today - timedelta(days=7)).strftime("%m/%d/%Y")
    to_date = today.strftime("%m/%d/%Y")

    notice_types = {"r": "RFI", "s": "Sources Sought", "i": "Industry Day", "p": "Pre-Solicitation"}

    for code, label in notice_types.items():
        try:
            resp = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params={"api_key": SAM_API_KEY, "postedFrom": from_date,
                        "postedTo": to_date, "noticetype": code, "limit": 100},
                headers=HEADERS, timeout=30
            )
            resp.raise_for_status()
            for item in resp.json().get("opportunitiesData", []):
                opp = Opportunity(
                    title=item.get("title", "Untitled"),
                    notice_id=item.get("noticeId", ""),
                    agency=item.get("fullParentPathName", item.get("departmentName", "Unknown")),
                    posted_date=item.get("postedDate", ""),
                    response_date=item.get("responseDeadLine", "TBD"),
                    description=(item.get("description", "") or "")[:2000],
                    url=clean_url(f"https://sam.gov/opp/{item.get('noticeId','')}/view", "https://sam.gov/search"),
                    opp_type=label,
                    source="SAM.gov",
                    naics=item.get("naicsCode", ""),
                )
                results.append(score_opportunity(opp))
        except Exception as e:
            print(f"[SAM.gov] Error fetching {label}: {e}")

    # Also run broad keyword search for industry days 60 days out
    for kw in [
        # Data Integration & Unification
        "data integration platform",
        "enterprise data platform",
        "data unification",
        "disparate data sources",
        "data fusion platform",
        # Investigative & Operational Analytics
        "investigative analytics platform",
        "law enforcement analytics",
        "operational intelligence dashboard",
        "crime analytics platform",
        "situational awareness platform",
        # Federated Search
        "federated search capability",
        "enterprise search platform",
        "cross system search",
        # Entity Resolution
        "entity resolution platform",
        "record deduplication",
        # Secure SaaS
        "fedramp law enforcement",
        "cjis compliant platform",
        # Public Safety
        "public safety data platform",
        "crime gun intelligence",
        "nibin analytics",
        "fusion center platform",
        # Corrections
        "community supervision platform",
        "offender management system",
        "probation parole data",
        # Modernization
        "palantir replacement",
        "legacy platform modernization",
        "data platform replacement",
        # AI cluster
        "artificial intelligence law enforcement",
        "machine learning public safety",
        "ai analytics platform government",
        "generative ai federal agency",
        "nlp investigative platform",
    ]:

        try:
            resp = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params={"api_key": SAM_API_KEY, "keywords": kw,
                        "postedFrom": (today - timedelta(days=7)).strftime("%m/%d/%Y"),
                        "postedTo": (today + timedelta(days=60)).strftime("%m/%d/%Y"),
                        "noticetype": "i", "limit": 25},
                headers=HEADERS, timeout=30
            )
            resp.raise_for_status()
            for item in resp.json().get("opportunitiesData", []):
                opp = Opportunity(
                    title=item.get("title", "Untitled"),
                    notice_id=item.get("noticeId", ""),
                    agency=item.get("fullParentPathName", "Unknown"),
                    posted_date=item.get("postedDate", ""),
                    response_date=item.get("responseDeadLine", "TBD"),
                    description=(item.get("description", "") or "")[:2000],
                    url=clean_url(f"https://sam.gov/opp/{item.get('noticeId','')}/view", "https://sam.gov/search"),
                    opp_type="Industry Day",
                    source="SAM.gov",
                    naics=item.get("naicsCode", ""),
                )
                results.append(score_opportunity(opp))
        except Exception as e:
            print(f"[SAM.gov broad] Error: {e}")

    print(f"[SAM.gov] {len(results)} notices fetched")
    return results


# ---------------------------------------------------------------------------
# SOURCE 2: FEDERAL REGISTER API
# NO API KEY REQUIRED — completely open
# Searches for RFI / Sources Sought / Industry Day notices in the Federal Register
# Docs: https://www.federalregister.gov/reader-aids/developer-resources/rest-api
# ---------------------------------------------------------------------------
def fetch_federal_register() -> list[Opportunity]:
    results = []
    today = datetime.utcnow()
    # 30-day window — Federal Register RFIs have longer comment periods
    since = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    # Short targeted terms — Federal Register search matches title/abstract
    search_terms = [
        "data integration",
        "data analytics law enforcement",
        "federated search",
        "entity resolution",
        "public safety software",
        "law enforcement platform",
        "community supervision",
        "offender management",
        "corrections data",
        "palantir",
        "artificial intelligence law enforcement",
        "machine learning government",
        "data platform modernization",
        "crime analytics",
        "investigative platform",
        "situational awareness",
        "information sharing law enforcement",
        "cjis",
        "fedramp data platform",
    ]

    seen_ids = set()

    for term in search_terms:
        try:
            url_params = (
                f"conditions[term]={requests.utils.quote(term)}"
                f"&conditions[publication_date][gte]={since}"
                f"&conditions[type][]=NOTICE"
                f"&per_page=20&order=newest"
                f"&fields[]=document_number&fields[]=title&fields[]=abstract"
                f"&fields[]=publication_date&fields[]=agencies"
                f"&fields[]=html_url&fields[]=comments_close_on"
            )
            resp = requests.get(
                f"https://www.federalregister.gov/api/v1/documents.json?{url_params}",
                headers={"User-Agent": HEADERS["User-Agent"]},
                timeout=30
            )
            resp.raise_for_status()
            docs = resp.json().get("results", [])

            for doc in docs:
                doc_id = doc.get("document_number", "")
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)

                title = doc.get("title", "Untitled")
                abstract = doc.get("abstract", "") or ""
                agencies = ", ".join(
                    a.get("name", "") for a in doc.get("agencies", []) if a.get("name")
                )
                pub_date = doc.get("publication_date", "")
                comment_date = doc.get("comments_close_on", doc.get("comment_date", "TBD"))
                url = clean_url(doc.get("html_url", "") or f"https://www.federalregister.gov/documents/{doc_id}", "https://www.federalregister.gov")

                # Detect if this is actually an RFI/sources sought
                title_lower = title.lower()
                abstract_lower = abstract.lower()
                combined = f"{title_lower} {abstract_lower}"

                rfi_signals = [
                    "request for information", "sources sought", "industry day",
                    "market research", "request for proposal", "pre-solicitation",
                    "notice of intent", "broad agency announcement",
                ]
                if not any(sig in combined for sig in rfi_signals):
                    continue  # Skip non-solicitation notices

                opp_type = "Federal Register RFI"
                if "industry day" in combined:
                    opp_type = "Industry Day"
                elif "sources sought" in combined:
                    opp_type = "Sources Sought"

                opp = Opportunity(
                    title=title,
                    notice_id=f"FR-{doc_id}",
                    agency=agencies or "Federal Agency",
                    posted_date=pub_date,
                    response_date=str(comment_date) if comment_date else "TBD",
                    description=abstract[:2000],
                    url=url,
                    opp_type=opp_type,
                    source="Federal Register",
                )
                results.append(score_opportunity(opp))

            if docs:
                print(f"[FederalRegister] '{term}': {len(docs)} raw, kept {len([x for x in results if x.notice_id.startswith('FR-')])} after filter")
            time.sleep(0.3)
        except Exception as e:
            status = getattr(getattr(e, 'response', None), 'status_code', 'N/A')
            print(f"[FederalRegister] Error for '{term}': {type(e).__name__}: {e} (HTTP {status})")

    print(f"[Federal Register] {len(results)} notices fetched")
    return results


# ---------------------------------------------------------------------------
# SOURCE 3: USASPENDING.GOV API v2
# NO API KEY REQUIRED — completely open
# Use case: competitive intelligence — find recent contracts in our NAICS space
# so we know who's spending, on what, and can pursue follow-on/recompete opps
# ---------------------------------------------------------------------------
def fetch_usaspending_intel() -> list[Opportunity]:
    """
    Searches USASpending.gov for recent contract awards — competitive intel.
    Shows who is spending in Peregrine's space, at which agencies, so you can
    identify recompete opportunities and warm target accounts.
    NO NAICS filter — too restrictive. Short single keywords only.
    """
    results = []
    today = datetime.utcnow()
    # Use fiscal year to date for broader coverage
    start_date = (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    # ONE keyword per batch — short terms that actually appear in award descriptions
    # Multi-word phrases like "law enforcement analytics" almost never match
    keyword_batches = [
        ["law enforcement software"],
        ["public safety platform"],
        ["data integration"],
        ["crime analytics"],
        ["community supervision"],
        ["offender management"],
        ["investigative software"],
        ["palantir"],
        ["data analytics platform"],
        ["records management system"],
        ["criminal justice software"],
        ["corrections software"],
    ]

    for keywords in keyword_batches:
        payload = {
            "subawards": False,
            "limit": 10,
            "page": 1,
            "filters": {
                "keywords": keywords,
                "award_type_codes": ["A", "B", "C", "D"],
                "time_period": [{"start_date": start_date, "end_date": end_date}],
                # NO naics_codes filter — it kills results
            },
            "fields": [
                "Award ID", "Recipient Name", "Start Date", "End Date",
                "Award Amount", "Awarding Agency", "Awarding Sub Agency",
                "Description", "Contract Award Type",
            ],
            "sort": "Award Amount",
            "order": "desc",
        }

        try:
            resp = requests.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=30
            )
            resp.raise_for_status()
            awards = resp.json().get("results", [])

            for award in awards:
                award_id = award.get("Award ID", "")
                amount = award.get("Award Amount", 0) or 0
                recipient = award.get("Recipient Name", "Unknown Contractor")
                agency = award.get("Awarding Agency", "")
                sub_agency = award.get("Awarding Sub Agency", "")
                description = award.get("Description", "") or ""
                start = award.get("Start Date", "")
                end = award.get("End Date", "")

                # Format as competitive intel, not a live solicitation
                title = f"[AWARD INTEL] {description[:80] or 'Contract'} — {recipient}"
                desc_full = (
                    f"Recent award to {recipient} by {agency} ({sub_agency}). "
                    f"Contract value: ${amount:,.0f}. Period: {start} to {end}. "
                    f"Description: {description[:500]}. "
                    f"COMPETITIVE INTEL: This agency has active spending in this space. "
                    f"Watch for recompetes or follow-on opportunities."
                )

                opp = Opportunity(
                    title=title,
                    notice_id=f"USA-{award_id}",
                    agency=f"{agency} / {sub_agency}",
                    posted_date=start or end_date,
                    response_date="Watch for recompete",
                    description=desc_full,
                    url=clean_url(f"https://www.usaspending.gov/award/{award_id}/", "https://www.usaspending.gov/search"),
                    opp_type="Award Intel",
                    source="USASpending.gov",
                )
                results.append(score_opportunity(opp))

            print(f"[USASpending] {keywords}: {len(awards)} awards returned")
            time.sleep(0.5)
        except Exception as e:
            status = getattr(getattr(e, 'response', None), 'status_code', 'N/A')
            print(f"[USASpending] Error for {keywords}: {type(e).__name__}: {e} (HTTP {status})")

    print(f"[USASpending] {len(results)} award intel records fetched")
    return results


# ---------------------------------------------------------------------------
# SOURCE 4: DHS, DOJ, FBI PROCUREMENT PAGES (RSS / ATOM FEEDS)
# Several agencies publish RSS feeds of procurement opportunities
# NO KEY REQUIRED
# ---------------------------------------------------------------------------
def fetch_agency_rss_feeds() -> list[Opportunity]:
    results = []

    # These are public RSS/Atom feeds from federal agency sites
    feeds = [
        {
            "url": "https://feeds.feedburner.com/dhs/zOAi",
            "agency": "DHS",
        },
        {
            "url": "https://www.govinfo.gov/rss/pkg.xml",
            "agency": "GovInfo",
        },
        {
            "url": "https://www.gsa.gov/about-gsa/newsroom/rss-feeds",
            "agency": "GSA",
        },
    ]

    rfi_signals = [
        "request for information", "rfi", "sources sought", "industry day",
        "market research", "pre-solicitation", "broad agency announcement", "baa",
        "public safety", "law enforcement", "data integration", "analytics",
    ]

    for feed in feeds:
        url = feed["url"]
        agency = feed["agency"]
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"[RSS] {agency} feed returned {resp.status_code}, skipping")
                continue
            root = ET.fromstring(resp.content)

            # Handle both RSS and Atom
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)

            for item in items[:20]:
                title_el = item.find("title") or item.find("atom:title", ns)
                desc_el  = item.find("description") or item.find("atom:summary", ns) or item.find("atom:content", ns)
                link_el  = item.find("link") or item.find("atom:link", ns)
                date_el  = item.find("pubDate") or item.find("atom:published", ns)

                title = unescape(title_el.text or "") if title_el is not None else "Untitled"
                desc  = unescape(re.sub("<[^>]+>", "", desc_el.text or "")) if desc_el is not None else ""
                url_  = link_el.text or (link_el.get("href") if link_el is not None else "") or ""
                date_ = date_el.text or "" if date_el is not None else ""

                combined = f"{title} {desc}".lower()
                if not any(sig in combined for sig in rfi_signals):
                    continue

                opp_type = "Federal Register RFI"
                if "industry day" in combined:
                    opp_type = "Industry Day"
                elif "sources sought" in combined:
                    opp_type = "Sources Sought"

                opp = Opportunity(
                    title=title,
                    notice_id=f"RSS-{hash(title + url_) % 10**9}",
                    agency=agency,
                    posted_date=date_[:10] if date_ else datetime.utcnow().strftime("%Y-%m-%d"),
                    response_date="See posting",
                    description=desc[:2000],
                    url=clean_url(url_, ""),
                    opp_type=opp_type,
                    source=f"RSS: {agency}",
                )
                results.append(score_opportunity(opp))
        except Exception as e:
            print(f"[RSS] {agency}: {e}")

    print(f"[Agency RSS] {len(results)} items fetched")
    return results


# ---------------------------------------------------------------------------
# SOURCE 5: CONGRESS.GOV — Tech & AI Committee Activity
# Tracks congressional hearings/markups that signal upcoming federal IT spend
# NO KEY REQUIRED
# ---------------------------------------------------------------------------
def fetch_industry_news() -> list[dict]:
    """
    Fetch recent news from public RSS feeds covering Peregrine's market:
    law enforcement tech, public safety data, govtech, corrections,
    federal IT procurement, and AI in government.
    Returns a list of news item dicts (not Opportunity objects).
    """
    today = datetime.utcnow()
    news_items = []
    seen_titles = set()

    news_feeds = [
        # Law enforcement & public safety tech — verified active feeds
        {"url": "https://www.govtech.com/public-safety/rss.xml",        "source": "GovTech Public Safety"},
        {"url": "https://www.govtech.com/artificial-intelligence/rss.xml","source": "GovTech AI"},
        {"url": "https://www.govtech.com/security/rss.xml",             "source": "GovTech Security"},
        # Federal IT
        {"url": "https://fedscoop.com/feed/",                           "source": "FedScoop"},
        {"url": "https://www.nextgov.com/rss/all/",                     "source": "Nextgov"},
        {"url": "https://gcn.com/rss-feeds/all.aspx",                   "source": "GCN"},
        {"url": "https://www.federaltimes.com/rss/",                    "source": "Federal Times"},
        # Law enforcement specific
        {"url": "https://www.police1.com/rss/all/",                     "source": "Police1"},
        {"url": "https://www.corrections1.com/rss/all/",                "source": "Corrections1"},
    ]

    # Keywords that make a news item relevant to Peregrine
    relevant_keywords = [
        "law enforcement", "public safety", "police", "sheriff", "corrections",
        "probation", "parole", "supervision", "criminal justice", "data analytics",
        "data integration", "data platform", "data sharing", "intelligence platform",
        "investigative", "crime analytics", "records management", "dispatch",
        "palantir", "govtech", "federal it", "fedramp", "cjis",
        "artificial intelligence", "machine learning", "predictive",
        "procurement", "contract award", "rfp", "sources sought",
        "fusion center", "surveillance", "crime reduction", "violent crime",
    ]

    for feed in news_feeds:
        try:
            resp = requests.get(feed["url"], headers={
                "User-Agent": "PeregrineScanner/2.0",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if resp.status_code != 200:
                continue

            root = ET.fromstring(resp.content)
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

            for item in items[:10]:
                title_el = item.find("title")
                link_el  = item.find("link")
                desc_el  = item.find("description") or item.find("summary")
                date_el  = item.find("pubDate") or item.find("published")

                title = (title_el.text or "").strip() if title_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (desc_el.text or ""))).strip() if desc_el is not None else ""
                url_  = (link_el.text or "").strip() if link_el is not None else ""
                date_ = (date_el.text or "").strip() if date_el is not None else ""

                if not title or title in seen_titles:
                    continue

                combined = f"{title} {desc}".lower()
                if not any(kw in combined for kw in relevant_keywords):
                    continue

                seen_titles.add(title)
                news_items.append({
                    "title": title,
                    "source": feed["source"],
                    "url": clean_url(url_, ""),
                    "date": date_[:16] if date_ else today.strftime("%Y-%m-%d"),
                    "summary": desc[:300],
                })
            time.sleep(0.2)
        except Exception as e:
            print(f"[News] {feed['source']}: {e}")

    print(f"[Industry News] {len(news_items)} relevant articles found")
    return news_items[:20]  # Cap at 20 most relevant


# ---------------------------------------------------------------------------
# DEDUP + RANK
# ---------------------------------------------------------------------------
def deduplicate_and_rank(opps: list[Opportunity]) -> list[Opportunity]:
    seen = set()
    unique = []
    for o in opps:
        key = o.notice_id or f"{o.title[:60]}{o.agency[:20]}"
        if key not in seen:
            seen.add(key)
            unique.append(o)

    # Keep only items with at least one capability cluster match (score > 0)
    # Drop: hard exclusions, expired, and items with zero capability signal
    filtered = [
        o for o in unique
        if o.score > 0 and o.tier not in ("⛔ Not a Fit", "⛔ Expired")
    ]
    return sorted(filtered, key=lambda x: x.score, reverse=True)


# ---------------------------------------------------------------------------
# HTML EMAIL BUILDER
# ---------------------------------------------------------------------------
SOURCE_BADGE_COLORS = {
    "SAM.gov":          "#0057b8",
    "Federal Register": "#8b0000",
    "USASpending.gov":  "#006633",
    "Congress.gov":     "#4a0066",
    "RSS: GSA":         "#cc6600",
    "RSS: DHS":         "#333366",
}

def source_badge(source: str) -> str:
    color = SOURCE_BADGE_COLORS.get(source, "#555")
    for k, v in SOURCE_BADGE_COLORS.items():
        if source.startswith(k):
            color = v
            break
    return (f'<span style="background:{color};color:#fff;font-size:10px;font-weight:700;'
            f'padding:2px 7px;border-radius:10px;margin-left:6px;">{source}</span>')

def deadline_badge(response_date: str) -> str:
    """Return a color-coded deadline badge based on urgency."""
    from datetime import datetime, timedelta
    if not response_date or response_date in ("TBD", "N/A", "See posting",
            "Watch for recompete", "See event page for registration deadline",
            "Typically varies — check site"):
        return f'<span style="background:#888;color:#fff;font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;">📅 Due: {response_date}</span>'

    dt = parse_date_flexible(response_date)
    today = datetime.utcnow()
    if not dt:
        return f'<span style="background:#888;color:#fff;font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;">📅 Due: {response_date}</span>'

    days_left = (dt - today).days
    if days_left <= 7:
        color, emoji, label = "#c0392b", "🔴", f"Due in {max(days_left,0)}d — ACT NOW"
    elif days_left <= 14:
        color, emoji, label = "#e67e22", "🟠", f"Due in {days_left}d — Urgent"
    elif days_left <= 30:
        color, emoji, label = "#f39c12", "🟡", f"Due in {days_left}d"
    else:
        color, emoji, label = "#27ae60", "🟢", f"Due {dt.strftime('%b %d, %Y')}"

    return f'<span style="background:{color};color:#fff;font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;">{emoji} {label}</span>'


def opp_card(o: Opportunity) -> str:
    tier_color = {
        "🟢 Strong Fit": "#1a7a4a",
        "🟡 Good Fit":   "#7a6a00",
        "🔵 Possible Fit": "#1a4a7a",
    }.get(o.tier, "#555")

    reasons_html = "".join(f"<li style='margin-bottom:2px'>{r}</li>" for r in o.score_reasons)
    desc_preview = o.description[:350].strip() + ("..." if len(o.description) > 350 else "")
    badge = deadline_badge(o.response_date)

    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin-bottom:12px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
      <div style="margin-bottom:8px;">
        <span style="font-size:11px;font-weight:700;color:{tier_color};background:{tier_color}18;
                     padding:2px 8px;border-radius:12px;">{o.tier} · {o.score}pts</span>
        {source_badge(o.source)}
        <span style="font-size:11px;color:#888;margin-left:6px;">{o.opp_type}</span>
      </div>
      <div style="font-weight:700;font-size:15px;color:#111;margin-bottom:6px;line-height:1.3">{o.title}</div>
      <div style="margin-bottom:8px;">{badge}</div>
      <div style="font-size:12px;color:#666;margin-bottom:8px;">
        🏛 {o.agency[:80]}
        &nbsp;·&nbsp; 📬 Posted: {o.posted_date}
      </div>
      <div style="font-size:13px;color:#333;line-height:1.5;margin-bottom:8px;">{desc_preview}</div>
      <div style="font-size:12px;color:#555;margin-bottom:10px;">
        <strong>Why it fits:</strong>
        <ul style="margin:3px 0 0 14px;padding:0;">{reasons_html}</ul>
      </div>
      {f'<a href="{o.url}" style="display:inline-block;background:#0057b8;color:#fff;text-decoration:none;padding:6px 14px;border-radius:5px;font-size:12px;font-weight:600;">View on Source →</a>' if o.url else '<span style="font-size:12px;color:#888;font-style:italic;">No link available</span>'}
    </div>"""

def build_section(title: str, opps: list[Opportunity]) -> str:
    if not opps:
        return f"""
        <div style="margin:20px 0 6px">
          <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">{title}</h2>
          <p style="color:#aaa;font-size:13px;font-style:italic">No opportunities today.</p>
        </div>"""
    return f"""
    <div style="margin:20px 0 6px">
      <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">{title} ({len(opps)})</h2>
      {"".join(opp_card(o) for o in opps)}
    </div>"""

def build_news_section(news_items: list) -> str:
    """Build a compact industry news digest — just bullets, no full cards."""
    if not news_items:
        return """
        <div style="margin:20px 0 6px">
          <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">📰 Industry News & Signals</h2>
          <p style="color:#aaa;font-size:13px;font-style:italic">No relevant news found today.</p>
        </div>"""

    bullets = ""
    for item in news_items[:12]:
        summary = item.get("summary", "")[:180].strip()
        if summary and not summary.endswith((".", "?", "!")):
            summary += "..."
        bullets += f"""
        <li style="margin-bottom:10px;line-height:1.4;">
          {('<a href="' + item['url'] + '" style="font-weight:600;color:#0057b8;text-decoration:none;">' + item['title'][:100] + '</a>') if item.get('url') else ('<span style="font-weight:600;color:#333;">' + item['title'][:100] + '</span>')}
          <span style="font-size:11px;color:#888;margin-left:6px;">{item['source']} · {item['date'][:10]}</span>
          {f'<div style="font-size:12px;color:#555;margin-top:2px;">{summary}</div>' if summary else ''}
        </li>"""

    return f"""
    <div style="margin:20px 0 6px">
      <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">📰 Industry News & Market Signals ({len(news_items)})</h2>
      <ul style="margin:0;padding-left:18px;list-style:disc;">{bullets}</ul>
    </div>"""


def build_html_email(opps: list[Opportunity], run_date: str,
                     source_counts: dict, news_items: list = None,
                     competitor_items: list = None) -> str:
    # Exclude events from solicitation tiers so stats bar reflects actual RFI/RFP counts
    non_events = [o for o in opps if o.source != "Events Intelligence"]
    tiers = {
        "strong":   [o for o in non_events if "Strong" in o.tier],
        "good":     [o for o in non_events if "Good" in o.tier],
        "possible": [o for o in non_events if "Possible" in o.tier],
    }
    ind_days  = [o for o in opps if o.opp_type == "Industry Day"]
    fr_rfis   = [o for o in opps if o.source == "Federal Register"]
    usa_intel = [o for o in opps if o.source == "USASpending.gov"]
    signals   = [o for o in opps if o.source == "Congress.gov"]
    events    = [o for o in opps if o.source == "Events Intelligence"]

    low_fit = [o for o in non_events if o.tier == "⚪ Low Fit" and o.score > 0 and any(r.startswith("✓") for r in o.score_reasons)]
    stats = [
        ("Total", len(non_events)),
        ("🟢 Strong", len(tiers["strong"])),
        ("🟡 Good", len(tiers["good"])),
        ("🔵 Possible", len(tiers["possible"])),
        ("⚪ Low Fit", len(low_fit)),
        ("Events", len(events)),
    ]
    stat_cells = "".join(f"""
        <td style="text-align:center;padding:4px 14px;border-right:1px solid #e8e8e8">
          <div style="font-size:24px;font-weight:800;color:#0057b8">{v}</div>
          <div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:0.5px">{k}</div>
        </td>""" for k, v in stats)

    source_rows = "".join(
        f'<tr><td style="padding:3px 12px;font-size:12px;color:#555">{src}</td>'
        f'<td style="padding:3px 12px;font-size:12px;font-weight:700;color:#0057b8">{cnt}</td></tr>'
        for src, cnt in source_counts.items()
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,sans-serif">
<div style="max-width:720px;margin:0 auto;padding:16px">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#0057b8 0%,#003580 100%);border-radius:12px;padding:28px 32px;margin-bottom:16px;color:#fff">
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:2.5px;opacity:0.65;margin-bottom:5px">Daily Federal Intelligence — Peregrine.io</div>
    <div style="font-size:26px;font-weight:800;letter-spacing:-0.5px">🦅 Opportunity Digest</div>
    <div style="font-size:13px;opacity:0.75;margin-top:5px">{run_date} &nbsp;·&nbsp; 6 Sources Searched</div>
  </div>

  <!-- Stats Bar -->
  <div style="background:#fff;border-radius:10px;padding:16px 8px;margin-bottom:16px;border:1px solid #e0e0e0">
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
      <tr>{stat_cells}</tr>
    </table>
  </div>

  <!-- Source Summary -->
  <div style="background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:16px;border:1px solid #e0e0e0;font-size:12px">
    <div style="font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#888;margin-bottom:8px">Sources Searched Today</div>
    <table cellpadding="0" cellspacing="0"><tbody>{source_rows}</tbody></table>
  </div>

  {build_section("🟢 Strong Fit — Act Now", [o for o in tiers["strong"] if o.source != "Events Intelligence"])}
  {build_section("🟡 Good Fit — Review Today", [o for o in tiers["good"] if o.source != "Events Intelligence"])}
  {build_section("🔵 Possible Fit — Scan Manually", [o for o in tiers["possible"] if o.source != "Events Intelligence"])}
  {build_section("⚪ Low Fit — Weak Signal (Review Manually)", [o for o in non_events if o.tier == "⚪ Low Fit" and any(r.startswith("✓") for r in o.score_reasons)])}
  {build_section("🏆 Award Intel (Recent Contract Wins)", usa_intel[:8])}
  {build_competitor_section(competitor_items or [])}
  {build_news_section(news_items or [])}
  {build_section("🎤 Events & Conferences to Attend", sorted(events, key=lambda x: x.score, reverse=True))}

  <!-- Footer -->
  <div style="text-align:center;font-size:11px;color:#bbb;margin-top:24px;padding:20px;border-top:1px solid #e0e0e0">
    Peregrine Daily Scanner &nbsp;·&nbsp; {run_date}<br>
    Sources: SAM.gov · Federal Register · USASpending.gov · Agency RSS · Congress.gov · Events Intelligence<br><br>
    <a href="https://sam.gov/search/?index=opp" style="color:#0057b8;text-decoration:none">Browse all SAM.gov</a> &nbsp;·&nbsp;
    <a href="https://www.federalregister.gov/documents/search?conditions%5Btype%5D%5B%5D=NOTICE" style="color:#0057b8;text-decoration:none">Federal Register Notices</a> &nbsp;·&nbsp;
    <a href="https://www.usaspending.gov/search" style="color:#0057b8;text-decoration:none">USASpending Search</a>
  </div>
</div>
</body></html>"""



# ---------------------------------------------------------------------------
# SOURCE 6: EVENTS INTELLIGENCE
# Scrapes public event listing sites for upcoming federal tech conferences,
# summits, and expos relevant to Peregrine's market. No API key required.
# ---------------------------------------------------------------------------

# Known recurring events Peregrine should attend — checked against upcoming dates
KNOWN_EVENTS = [
    {
        "name": "IACP Annual Conference",
        "org": "International Association of Chiefs of Police",
        "url": "https://www.theiacp.org/events",
        "why": "Premier law enforcement leadership event — 400+ agency heads, direct Peregrine customer profile",
        "typical_month": "October",
        "tier": "🟢 Strong Fit",
        "score": 90,
    },
    {
        "name": "National Sheriffs\'Association Annual Conference",
        "org": "National Sheriffs\' Association",
        "url": "https://www.sheriffs.org/events",
        "why": "Sheriff departments are core Peregrine customers — investigative analytics, data integration",
        "typical_month": "June",
        "tier": "🟢 Strong Fit",
        "score": 88,
    },
    {
        "name": "NOBLE Annual Conference",
        "org": "National Organization of Black Law Enforcement Executives",
        "url": "https://www.noblenatl.org",
        "why": "Law enforcement leadership — Peregrine customer profile, violent crime reduction mission",
        "typical_month": "July",
        "tier": "🟢 Strong Fit",
        "score": 82,
    },
    {
        "name": "Police Executive Research Forum (PERF) Annual Meeting",
        "org": "PERF",
        "url": "https://perf.memberclicks.net",
        "why": "Senior law enforcement executives — data-driven policing, analytics buyers",
        "typical_month": "March",
        "tier": "🟢 Strong Fit",
        "score": 85,
    },
    {
        "name": "GovTech Summit",
        "org": "Government Technology",
        "url": "https://www.govtech.com/events",
        "why": "State & local government technology buyers — data integration, public safety tech",
        "typical_month": "November",
        "tier": "🟢 Strong Fit",
        "score": 80,
    },
    {
        "name": "Amazon Web Services (AWS) Public Sector Summit",
        "org": "AWS",
        "url": "https://aws.amazon.com/events/summits/washington-dc/",
        "why": "GovCloud buyers — Peregrine runs on AWS GovCloud, strong partner/customer overlap",
        "typical_month": "June",
        "tier": "🟢 Strong Fit",
        "score": 78,
    },
    {
        "name": "ATOA National Training Conference",
        "org": "ATF Officers Association",
        "url": "https://www.atfoa.org",
        "why": "Direct ATF audience — Peregrine submitted RFI to ATF, NIBIN/eTrace use case",
        "typical_month": "September",
        "tier": "🟢 Strong Fit",
        "score": 92,
    },
    {
        "name": "APPA Annual Training Institute",
        "org": "American Probation and Parole Association",
        "url": "https://www.appa-net.org/eweb/",
        "why": "Probation/parole supervision agencies — direct CSOSA use case expansion",
        "typical_month": "July",
        "tier": "🟢 Strong Fit",
        "score": 87,
    },
    {
        "name": "ACA Winter Conference",
        "org": "American Correctional Association",
        "url": "https://www.aca.org/ACA_Prod_IMIS/ACA/Events",
        "why": "Corrections agencies — BOP, state DOCs, supervision agencies, Peregrine use case",
        "typical_month": "January",
        "tier": "🟢 Strong Fit",
        "score": 83,
    },
    {
        "name": "NASCIO Annual Conference",
        "org": "National Association of State Chief Information Officers",
        "url": "https://www.nascio.org/events/",
        "why": "State CIOs — data integration platform buyers, IT modernization decisions",
        "typical_month": "October",
        "tier": "🟡 Good Fit",
        "score": 72,
    },
    {
        "name": "DHS Industry Day (various)",
        "org": "Department of Homeland Security",
        "url": "https://apfs-cloud.dhs.gov",
        "why": "DHS procurement — directly relevant to Peregrine\'s law enforcement & border mission",
        "typical_month": "Ongoing",
        "tier": "🟢 Strong Fit",
        "score": 88,
    },
    {
        "name": "Intelligence & National Security Summit",
        "org": "AFCEA / INSA",
        "url": "https://www.afcea.org/site/events",
        "why": "Intelligence community data platform buyers — ODNI, NSA, CIA, DIA customer profiles",
        "typical_month": "September",
        "tier": "🟡 Good Fit",
        "score": 70,
    },
    {
        "name": "TECHEXPO Top Secret",
        "org": "TECHEXPO",
        "url": "https://techexposummit.com",
        "why": "Cleared personnel / IC contractors — defense data platform and intelligence fusion",
        "typical_month": "Various",
        "tier": "🟡 Good Fit",
        "score": 68,
    },
    {
        "name": "National Fusion Center Association (NFCA) Training Event",
        "org": "NFCA",
        "url": "https://nfcausa.org",
        "why": "Fusion centers are exact Peregrine customers — multi-source intel, data sharing",
        "typical_month": "April",
        "tier": "🟢 Strong Fit",
        "score": 91,
    },
    {
        "name": "SEARCH National Conference on Justice Information",
        "org": "SEARCH Group",
        "url": "https://www.search.org/events/",
        "why": "Criminal justice information sharing — CJIS, RMS, data integration buyers",
        "typical_month": "August",
        "tier": "🟢 Strong Fit",
        "score": 86,
    },
    {
        "name": "International Justice & Public Safety Network (Nlets) Annual Conference",
        "org": "Nlets",
        "url": "https://www.nlets.org",
        "why": "Criminal justice data exchange — direct overlap with Peregrine federated search use case",
        "typical_month": "April",
        "tier": "🟢 Strong Fit",
        "score": 84,
    },
    {
        "name": "GovTech Law Enforcement Technology Conference",
        "org": "Government Technology",
        "url": "https://events.govtech.com",
        "why": "Law enforcement tech buyers — analytics, data platforms, public safety",
        "typical_month": "Various",
        "tier": "🟢 Strong Fit",
        "score": 81,
    },
    {
        "name": "Esri Federal GIS Conference",
        "org": "Esri",
        "url": "https://www.esri.com/en-us/about/events/federal-gis-conference/overview",
        "why": "Geospatial intelligence buyers — Peregrine geospatial capability, partner/compete opportunity",
        "typical_month": "February",
        "tier": "🟡 Good Fit",
        "score": 65,
    },
    {
        "name": "AFCEA TechNet Cyber",
        "org": "AFCEA",
        "url": "https://www.afcea.org/site/events",
        "why": "DoD cybersecurity — zero trust, FedRAMP, data platform security buyers",
        "typical_month": "May",
        "tier": "🟡 Good Fit",
        "score": 62,
    },
    {
        "name": "National Public Safety Telecommunications Council (NPSTC) Summit",
        "org": "NPSTC",
        "url": "https://npstc.org",
        "why": "Public safety communications & data — CAD, RMS, first responder tech buyers",
        "typical_month": "October",
        "tier": "🟡 Good Fit",
        "score": 66,
    },
]

def fetch_events_intelligence() -> list[Opportunity]:
    """
    Returns known upcoming conferences and events as Opportunity objects.
    Pulls from the curated KNOWN_EVENTS list, checks public event pages
    for date updates where possible, and flags events in the next 90 days.
    """
    results = []
    today = datetime.utcnow()
    ninety_days = today + timedelta(days=90)

    # Try to fetch live date info from a few key event aggregators
    event_feeds = [
        {
            "url": "https://www.govtech.com/events/rss.xml",
            "source": "GovTech Events",
        },
        {
            "url": "https://www.afcea.org/site/rss/events",
            "source": "AFCEA Events",
        },
    ]

    live_events = []
    for feed in event_feeds:
        try:
            resp = requests.get(feed["url"], headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            for item in items[:20]:
                title_el = item.find("title")
                link_el  = item.find("link")
                desc_el  = item.find("description")
                date_el  = item.find("pubDate")

                title = title_el.text or "" if title_el is not None else ""
                desc  = unescape(re.sub("<[^>]+>", "", desc_el.text or "")) if desc_el is not None else ""
                url_  = link_el.text or "" if link_el is not None else ""
                date_ = date_el.text or "" if date_el is not None else ""

                combined = f"{title} {desc}".lower()
                # Filter for relevant events
                event_signals = [
                    "law enforcement", "public safety", "government", "federal",
                    "justice", "homeland", "data", "analytics", "intelligence",
                    "corrections", "police", "sheriff", "cybersecurity",
                ]
                if not any(sig in combined for sig in event_signals):
                    continue

                opp = Opportunity(
                    title=f"[EVENT] {title}",
                    notice_id=f"EVT-{hash(title + url_) % 10**9}",
                    agency=feed["source"],
                    posted_date=date_[:10] if date_ else today.strftime("%Y-%m-%d"),
                    response_date="See event page for registration deadline",
                    description=desc[:1500],
                    url=clean_url(url_, ""),
                    opp_type="Conference/Event",
                    source="Events Intelligence",
                )
                live_events.append(score_opportunity(opp))
        except Exception as e:
            print(f"[Events] Feed error {feed['source']}: {e}")

    results.extend(live_events)

    # Add all known events as standing recommendations
    current_month = today.month
    for ev in KNOWN_EVENTS:
        # Estimate if the event is likely coming up in the next 90 days
        month_map = {
            "January": 1, "February": 2, "March": 3, "April": 4,
            "May": 5, "June": 6, "July": 7, "August": 8,
            "September": 9, "October": 10, "November": 11, "December": 12,
            "Ongoing": 0, "Various": 0,
        }
        ev_month = month_map.get(ev.get("typical_month", "Various"), 0)
        upcoming_note = ""
        if ev_month > 0:
            months_away = (ev_month - current_month) % 12
            if months_away == 0:
                upcoming_note = "⚡ THIS MONTH"
            elif months_away <= 3:
                upcoming_note = f"📆 ~{months_away} month(s) away"
            else:
                upcoming_note = f"🗓 Typically {ev['typical_month']}"
        else:
            upcoming_note = "🔄 Ongoing / check for dates"

        desc = (
            f"{upcoming_note} | {ev['why']} | "
            f"Typical timing: {ev.get('typical_month', 'Varies')}. "
            f"Check {ev['url']} for exact dates and registration."
        )

        opp = Opportunity(
            title=f"[EVENT] {ev['name']}",
            notice_id=f"KNWNEVT-{hash(ev['name']) % 10**9}",
            agency=ev["org"],
            posted_date=today.strftime("%Y-%m-%d"),
            response_date=f"Typically {ev.get('typical_month', 'varies')} — check site",
            description=desc,
            url=clean_url(ev.get("url", ""), "https://sam.gov/search"),
            opp_type="Conference/Event",
            source="Events Intelligence",
        )
        opp.score = ev["score"]
        opp.tier = ev["tier"]
        opp.score_reasons = [ev["why"]]
        results.append(opp)

    print(f"[Events Intelligence] {len(results)} events found ({len(live_events)} live feed + {len(KNOWN_EVENTS)} curated)")
    return results

# ---------------------------------------------------------------------------
# COMPETITOR INTELLIGENCE
# Monitors news on Peregrine's key competitors across its core verticals:
# law enforcement analytics, data integration, corrections supervision.
# ---------------------------------------------------------------------------

COMPETITORS = [
    # Direct law enforcement / public safety analytics competitors
    {"name": "Palantir",        "search": "Palantir law enforcement government contract",    "tags": ["palantir"]},
    {"name": "Axon",            "search": "Axon public safety technology platform",           "tags": ["axon"]},
    {"name": "ShotSpotter",     "search": "ShotSpotter gunshot detection contract award",     "tags": ["shotspotter"]},
    {"name": "Mark43",          "search": "Mark43 records management police software",        "tags": ["mark43"]},
    {"name": "Tyler Technologies","search": "Tyler Technologies criminal justice software",   "tags": ["tyler technologies"]},
    {"name": "Motorola Solutions","search": "Motorola Solutions public safety platform",      "tags": ["motorola solutions"]},
    # Data integration / intelligence platforms
    {"name": "IBM i2",          "search": "IBM i2 law enforcement analytics",                 "tags": ["ibm i2", "ibm"]},
    {"name": "Esri",            "search": "Esri geospatial law enforcement government",       "tags": ["esri"]},
    {"name": "Databricks",      "search": "Databricks government federal data platform",      "tags": ["databricks"]},
    # Corrections / supervision
    {"name": "Appriss",         "search": "Appriss corrections supervision software award",   "tags": ["appriss"]},
    {"name": "SuperCom",        "search": "SuperCom offender monitoring supervision",         "tags": ["supercom"]},
]

# RSS feeds that carry competitor news
COMPETITOR_NEWS_FEEDS = [
    {"url": "https://fedscoop.com/feed/",                   "source": "FedScoop"},
    {"url": "https://www.nextgov.com/rss/all/",             "source": "Nextgov"},
    {"url": "https://gcn.com/rss-feeds/all.aspx",           "source": "GCN"},
    {"url": "https://www.govtech.com/public-safety/rss.xml","source": "GovTech"},
    {"url": "https://www.police1.com/rss/all/",             "source": "Police1"},
    {"url": "https://www.corrections1.com/rss/all/",        "source": "Corrections1"},
    {"url": "https://www.govtech.com/security/rss.xml",     "source": "GovTech Security"},
]

def fetch_competitor_intel() -> list[dict]:
    """
    Scan news feeds for mentions of key competitors.
    Returns list of dicts: {competitor, title, url, source, date, summary, tags_found}
    """
    items_out = []
    seen_titles = set()

    # Build a flat lookup: tag → competitor name
    tag_map = {}
    for comp in COMPETITORS:
        for tag in comp["tags"]:
            tag_map[tag.lower()] = comp["name"]

    all_tags = list(tag_map.keys())

    for feed in COMPETITOR_NEWS_FEEDS:
        try:
            resp = requests.get(feed["url"], headers={
                "User-Agent": "PeregrineScanner/2.0",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=15)
            if resp.status_code != 200:
                print(f"[CompetitorIntel] {feed['source']}: HTTP {resp.status_code}")
                continue

            root = ET.fromstring(resp.content)
            feed_items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

            for item in feed_items[:15]:
                title_el = item.find("title")
                link_el  = item.find("link")
                desc_el  = item.find("description") or item.find("summary")
                date_el  = item.find("pubDate") or item.find("published")

                title = (title_el.text or "").strip() if title_el is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (desc_el.text or ""))).strip() if desc_el is not None else ""
                url_  = (link_el.text or "").strip() if link_el is not None else ""
                date_ = (date_el.text or "").strip() if date_el is not None else ""

                if not title or title in seen_titles:
                    continue

                combined = f"{title} {desc}".lower()

                # Find which competitors are mentioned
                tags_found = [tag_map[tag] for tag in all_tags if tag in combined]
                if not tags_found:
                    continue

                seen_titles.add(title)
                items_out.append({
                    "competitor": ", ".join(sorted(set(tags_found))),
                    "title": title,
                    "url": clean_url(url_, ""),
                    "source": feed["source"],
                    "date": date_[:10] if date_ else "",
                    "summary": desc[:250],
                })

            time.sleep(0.2)
        except Exception as e:
            print(f"[CompetitorIntel] {feed['source']}: {e}")

    # Sort by competitor name, then deduplicate same story from multiple feeds
    seen_story_titles = set()
    deduped = []
    for item in sorted(items_out, key=lambda x: x["competitor"]):
        if item["title"] not in seen_story_titles:
            seen_story_titles.add(item["title"])
            deduped.append(item)

    print(f"[Competitor Intel] {len(deduped)} stories found across {len(COMPETITORS)} competitors")
    return deduped[:20]


def build_competitor_section(intel_items: list) -> str:
    """Render competitor news as a clean grouped section."""
    if not intel_items:
        return """
        <div style="margin:20px 0 6px">
          <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">🔎 Competitor Intelligence</h2>
          <p style="color:#aaa;font-size:13px;font-style:italic">No competitor news found today.</p>
        </div>"""

    # Group by competitor
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in intel_items:
        for comp in item["competitor"].split(", "):
            grouped[comp.strip()].append(item)

    rows = ""
    for comp_name in sorted(grouped.keys()):
        stories = grouped[comp_name][:3]  # max 3 per competitor
        story_html = ""
        for s in stories:
            link = ('<a href="' + s["url"] + '" style="color:#0057b8;text-decoration:none;font-weight:600;">' + s["title"][:90] + '</a>') if s.get("url") else ('<span style="font-weight:600;color:#333;">' + s["title"][:90] + '</span>')
            summary = s.get("summary", "")[:200]
            story_html += f"""
            <div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #f0f0f0;">
              <div style="font-size:13px;">{link}</div>
              <div style="font-size:11px;color:#888;margin-top:2px;">{s['source']} · {s['date']}</div>
              {f'<div style="font-size:12px;color:#555;margin-top:2px;">{summary}</div>' if summary else ''}
            </div>"""

        rows += f"""
        <div style="margin-bottom:14px;">
          <div style="font-weight:700;font-size:13px;color:#c0392b;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">
            ⚔️ {comp_name}
          </div>
          {story_html}
        </div>"""

    return f"""
    <div style="margin:20px 0 6px">
      <h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;padding-bottom:5px;">🔎 Competitor Intelligence ({len(intel_items)} stories)</h2>
      <p style="font-size:12px;color:#888;margin:0 0 12px;">Monitoring: {", ".join(c["name"] for c in COMPETITORS)}</p>
      {rows}
    </div>"""


# ---------------------------------------------------------------------------
# EMAIL SEND — via SendGrid API (no SMTP, no credentials beyond API key)
# ---------------------------------------------------------------------------
def send_email(html_body: str, subject: str):
    if not SENDGRID_API_KEY or SENDGRID_API_KEY == "YOUR_SENDGRID_API_KEY":
        raise RuntimeError("SENDGRID_API_KEY is not set or is still the placeholder value.")

    recipients = [r.strip() for r in EMAIL_TO.split(",")]
    print(f"[Email] Sending to {recipients} via SendGrid...")

    payload = {
        "personalizations": [
            {
                "to": [{"email": r} for r in recipients],
                "subject": subject,
            }
        ],
        "from": {"email": EMAIL_FROM, "name": "Peregrine Federal Scanner"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }

    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if resp.status_code == 202:
        print(f"[Email] Successfully sent to {', '.join(recipients)}")
    else:
        raise RuntimeError(f"SendGrid error {resp.status_code}: {resp.text}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    run_date = datetime.utcnow().strftime("%B %d, %Y")
    print(f"\n{'='*60}")
    print(f"  Peregrine Daily Scanner — {run_date}")
    print(f"{'='*60}\n")

    all_opps = []
    source_counts = {}

    # Fetch from all sources
    sources = [
        ("SAM.gov",           fetch_sam_gov),
        ("Federal Register",  fetch_federal_register),
        ("USASpending.gov",   fetch_usaspending_intel),
        ("Agency RSS",        fetch_agency_rss_feeds),
        ("Industry News",     lambda: []),  # news handled separately below
        ("Events",            fetch_events_intelligence),
    ]

    for name, fn in sources:
        try:
            print(f"\n[{name}] Starting fetch...")
            batch = fn()
            all_opps.extend(batch)
            source_counts[name] = len(batch)
            print(f"[{name}] Done — {len(batch)} items")
        except Exception as e:
            import traceback
            print(f"[{name}] FATAL: {e}")
            traceback.print_exc()
            source_counts[name] = 0

    # Dedup and rank
    ranked = deduplicate_and_rank(all_opps)
    print(f"\n[Ranking] {len(ranked)} unique relevant opportunities after dedup+filter")

    # Exclude events from solicitation counts for subject line
    solicitations = [o for o in ranked if o.source != "Events Intelligence"]
    strong   = sum(1 for o in solicitations if "Strong" in o.tier)
    good     = sum(1 for o in solicitations if "Good" in o.tier)
    possible = sum(1 for o in solicitations if "Possible" in o.tier)

    # Find the soonest deadline across strong/good fits for urgency signal
    from datetime import datetime as _dt
    urgent_deadlines = []
    for o in solicitations:
        if "Strong" in o.tier or "Good" in o.tier:
            dt = parse_date_flexible(o.response_date)
            if dt:
                days = (dt - _dt.utcnow()).days
                if 0 <= days <= 7:
                    urgent_deadlines.append(days)

    # Build dynamic subject line
    if strong == 0 and good == 0:
        subject = f"🦅 Peregrine Daily | No strong matches today — {possible} possible | {run_date}"
    elif urgent_deadlines:
        soonest = min(urgent_deadlines)
        urgent_label = "today" if soonest == 0 else f"in {soonest}d"
        subject = f"🔴 Peregrine Daily | {strong} Strong · {good} Good — deadline {urgent_label} | {run_date}"
    elif strong >= 5:
        subject = f"🚀 Peregrine Daily | {strong} Strong · {good} Good Fits | {run_date}"
    elif strong >= 1:
        subject = f"🦅 Peregrine Daily | {strong} Strong · {good} Good Fits | {run_date}"
    else:
        subject = f"🦅 Peregrine Daily | {good} Good Fits · {possible} Possible | {run_date}"

    # Fetch industry news separately (returns dicts, not Opportunities)
    print("\n[Industry News] Fetching relevant news...")
    try:
        news_items = fetch_industry_news()
        source_counts["Industry News"] = len(news_items)
    except Exception as e:
        print(f"[Industry News] Error: {e}")
        news_items = []
        source_counts["Industry News"] = 0

    # Fetch competitor intelligence
    print("\n[Competitor Intel] Scanning for competitor news...")
    try:
        competitor_items = fetch_competitor_intel()
        source_counts["Competitor Intel"] = len(competitor_items)
    except Exception as e:
        print(f"[Competitor Intel] Error: {e}")
        competitor_items = []
        source_counts["Competitor Intel"] = 0

    # Build and send
    html = build_html_email(ranked, run_date, source_counts, news_items=news_items, competitor_items=competitor_items)
    send_email(html, subject)

    # Save local copy
    fname = f"digest_{datetime.utcnow().strftime('%Y%m%d')}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[Done] Digest saved to {fname}")

    # Print summary to console/logs
    print(f"\n{'─'*40}")
    print(f"  SUMMARY")
    print(f"{'─'*40}")
    for src, cnt in source_counts.items():
        print(f"  {src:<25} {cnt:>4} items")
    print(f"{'─'*40}")
    print(f"  After scoring/filter: {len(ranked):>4}")
    print(f"  Strong Fit 🟢:        {strong:>4}")
    print(f"  Good Fit 🟡:          {good:>4}")
    print(f"{'─'*40}\n")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        raise
