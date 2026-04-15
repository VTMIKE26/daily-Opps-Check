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
# PEREGRINE FIT PROFILE
# ---------------------------------------------------------------------------
HIGH_VALUE_KEYWORDS = [
    # Core mission — law enforcement
    "federated search", "enterprise search", "investigative platform",
    "law enforcement analytics", "crime gun intelligence", "CGIC",
    "NIBIN", "eTrace", "gun violence", "ballistic intelligence",
    "investigative workflow", "intelligence platform",
    # Palantir replacement signals — HIGH priority
    "Palantir", "Gotham", "Foundry", "alternative to Palantir",
    "replace Palantir", "Palantir replacement", "data platform modernization",
    "legacy data platform", "platform consolidation", "enterprise data platform",
    "data platform migration", "incumbent replacement",
    # General data integration — broad federal market
    "data integration platform", "data fusion", "data ingestion",
    "enterprise data integration", "data unification", "unified data environment",
    "data silo", "siloed systems", "disparate data sources",
    "multi-source data", "data harmonization", "data normalization",
    "master data management", "MDM",
    # Entity & record capabilities
    "entity resolution", "record deduplication", "record linkage",
    "ontology", "knowledge graph", "link analysis",
    "graph analytics", "relationship mapping",
    # Analytics & visualization
    "geospatial analysis", "geospatial intelligence", "GEOINT",
    "situational awareness", "real-time dashboard", "operational dashboard",
    "temporal analysis", "proactive alerting", "geofence",
    "heat map", "predictive analytics", "advanced analytics",
    # Security & compliance
    "CJIS", "AWS GovCloud", "FedRAMP High", "FedRAMP Moderate",
    "NIST SP 800-53", "ICAM", "SAML", "SSO",
    "zero trust", "Section 508", "role-based access", "attribute-based access",
    # Public safety operations
    "records management system", "RMS", "CAD", "computer-aided dispatch",
    "public safety", "first responder", "operational intelligence",
    # Agency-specific — ATF
    "ATF", "Bureau of Alcohol Tobacco", "crime gun", "NIBIN lead", "trace data",
    # Corrections & supervision
    "community supervision", "probation", "parole", "reentry",
    "corrections", "offender management", "supervision officer",
    "court services", "pretrial", "case supervision",
    "Smart21", "CSOSA", "supervision data",
    # Platform capabilities
    "OCR", "optical character recognition", "document intelligence",
    "walk-up usable", "mobile application", "biometric authentication",
    # Intelligence community / defense data
    "all-source analytics", "multi-INT", "intelligence fusion",
    "targeting platform", "mission data", "operational data environment",
    "common operating picture", "COP", "command and control",
]

MEDIUM_VALUE_KEYWORDS = [
    # General tech
    "SaaS", "software as a service", "enterprise platform",
    "data analytics", "data management", "interoperability",
    "API integration", "cloud platform", "digital transformation",
    "IT modernization", "legacy modernization", "dashboard", "visualization",
    # AI/ML
    "artificial intelligence", "machine learning", "AI/ML",
    "natural language processing", "NLP", "predictive analytics",
    "automation", "anomaly detection", "generative AI", "large language model",
    # Data platform signals
    "data lake", "data warehouse", "ETL", "ELT", "data pipeline",
    "data fabric", "data mesh", "data catalog", "metadata management",
    "data governance", "data quality", "data lineage",
    "cloud migration", "hybrid cloud", "multi-cloud",
    # Law enforcement adjacent
    "law enforcement", "public safety", "criminal justice",
    "emergency management", "homeland security", "intelligence",
    "investigative", "surveillance", "gang", "violent crime",
    "body camera", "evidence management", "digital evidence",
    # Agencies (non-ATF)
    "DOJ", "FBI", "DEA", "Marshal", "CBP", "ICE", "TSA",
    "DHS", "DoD", "Army", "Navy", "Air Force", "ODNI",
    "FEMA", "police department", "sheriff", "state police",
    # Contract vehicles
    "NASA SEWP", "SEWP", "GSA Schedule", "small business set-aside",
    "8(a)", "HUBZone", "SDVOSB", "WOSB",
    # Corrections/justice
    "justice system", "recidivism", "social services", "reintegration",
    "court order", "supervision program", "offender",
    # Data infrastructure
    "data model", "SharePoint", "SQL database", "file share",
    "near real time", "audit log", "retention policy",
    "commercial item", "annual subscription",
    # Competitors / market signals
    "Palantir alternative", "IBM i2", "Esri", "Tableau",
    "data visualization platform", "analytics platform",
]

NEGATIVE_KEYWORDS = [
    "construction", "HVAC", "janitorial", "landscaping", "food service",
    "furniture", "vehicle maintenance", "facilities management", "printing",
    "audio visual installation", "base operations", "custodial",
    "grounds maintenance", "pest control", "generator", "electrical install",
    "medical supply", "pharmaceutical", "staffing agency", "temp staff",
    "office supplies", "clothing", "uniform", "laundry", "refuse",
    "aircraft maintenance", "ship repair", "ammunition supply",
]

NAICS_CODES = [
    "513210",  # Software Publishers — Peregrine's primary NAICS
    "541511",  # Custom Computer Programming Services
    "541512",  # Computer Systems Design Services
    "541519",  # Other Computer Related Services
    "518210",  # Data Processing, Hosting, and Related Services
    "541690",  # Other Scientific and Technical Consulting
    "541715",  # R&D in Physical, Engineering, and Life Sciences
    "541614",  # Process, Physical Distribution, and Logistics Consulting
    "519130",  # Internet Publishing and Web Search Portals
    "561611",  # Investigation Services
    "561621",  # Security Systems Services
    "923120",  # Administration of Public Health Programs
    "922150",  # Parole Offices and Probation Offices
    "922110",  # Courts
    "922120",  # Police Protection
    "922190",  # Other Justice, Public Order, and Safety Activities
]

TARGET_AGENCIES = [
    # Tier 1 — active relationships / RFIs submitted
    "Bureau of Alcohol, Tobacco, Firearms", "ATF",
    "Department of Justice", "DOJ",
    "Federal Bureau of Investigation", "FBI",
    "Drug Enforcement Administration", "DEA",
    "U.S. Marshals", "USMS",
    "Court Services and Offender Supervision", "CSOSA",
    # Tier 2 — law enforcement / intelligence / border
    "Department of Homeland Security", "DHS",
    "Customs and Border Protection", "CBP",
    "Immigration and Customs Enforcement", "ICE",
    "Secret Service", "USSS",
    "Transportation Security Administration", "TSA",
    "Office of the Director of National Intelligence", "ODNI",
    "Defense Intelligence Agency", "DIA",
    "National Security Agency", "NSA",
    "Central Intelligence Agency", "CIA",
    # Tier 3 — corrections, justice, social services
    "Pretrial Services Agency", "PSA",
    "Bureau of Prisons", "BOP",
    "Office of Justice Programs", "OJP",
    "National Institute of Justice", "NIJ",
    "Office of Juvenile Justice", "OJJDP",
    "Office of National Drug Control", "ONDCP",
    # Tier 4 — DoD / defense data consumers
    "Department of Defense", "DoD",
    "Department of the Army", "Department of the Navy",
    "Department of the Air Force", "Space Force",
    "Defense Advanced Research Projects", "DARPA",
    "Special Operations Command", "SOCOM",
    "U.S. Northern Command", "NORTHCOM",
    # Tier 5 — broader federal data platform buyers
    "Federal Emergency Management Agency", "FEMA",
    "General Services Administration", "GSA",
    "Department of Health and Human Services", "HHS",
    "Centers for Disease Control", "CDC",
    "Social Security Administration", "SSA",
    "Department of Veterans Affairs", "VA",
    "Department of State",
    "Department of the Treasury",
    # State / local
    "police department", "sheriff", "state police",
    "public safety", "law enforcement agency",
    "probation", "parole", "corrections", "supervision agency",
]

# ---------------------------------------------------------------------------
# DATA CLASS
# ---------------------------------------------------------------------------
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
    source: str          # "SAM.gov", "Federal Register", "USASpending", etc.
    naics: str = ""
    score: int = 0
    score_reasons: list = field(default_factory=list)
    tier: str = ""

# ---------------------------------------------------------------------------
# DATE UTILITIES
# ---------------------------------------------------------------------------
def parse_date_flexible(date_str: str) -> datetime | None:
    """Try multiple date formats and return a datetime or None."""
    if not date_str or date_str in ("TBD", "N/A", "See posting", "Watch for recompete"):
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y",
        "%b %d, %Y", "%d %b %Y",
    ]
    clean = date_str.strip()[:25]
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None

def is_expired(opp: "Opportunity") -> bool:
    """
    Return True if the response deadline has clearly passed.
    We use a 2-day grace buffer to avoid timezone edge cases.
    If no deadline is parseable we keep the opportunity (err on side of inclusion).
    """
    today = datetime.utcnow()
    grace = today - timedelta(days=2)

    # Try response_date first, then posted_date as fallback
    for date_str in [opp.response_date, opp.posted_date]:
        dt = parse_date_flexible(date_str)
        if dt:
            return dt < grace
    return False  # Can't determine — keep it


# ---------------------------------------------------------------------------
# CAPABILITY-BASED SCORING ENGINE
#
# Peregrine's core capabilities (what it actually does):
#   1. Data Integration & Unification  — connect siloed systems into one environment
#   2. Investigative / Operational Analytics — search, link analysis, geospatial, dashboards
#   3. Federated Search — query across internal + external sources simultaneously
#   4. Entity Resolution & Deduplication — patented record merging across systems
#   5. Secure SaaS Platform — FedRAMP, CJIS, AWS GovCloud, NIST SP 800-53
#   6. Public Safety / Law Enforcement — RMS, CAD, NIBIN, eTrace, crime gun intelligence
#   7. Corrections & Supervision — probation, parole, offender management, CSOSA
#   8. Palantir / Legacy Platform Replacement — enterprise intelligence modernization
#
# Scoring is CAPABILITY-MATCH driven, not keyword-spray:
#   - Each capability has a cluster of specific, meaningful phrases
#   - A hit in a cluster scores once for that cluster (no double-counting spray)
#   - A NAICS match alone scores 0 — it must co-occur with capability signals
#   - Hard exclusions for clearly irrelevant work
# ---------------------------------------------------------------------------

# Each capability cluster: (capability_name, points_if_matched, [phrases])
# Phrases must be specific enough that a hit strongly implies Peregrine can do the work.
CAPABILITY_CLUSTERS = [
    (
        "Data Integration & Unification",
        25,
        [
            "data integration", "data unification", "data fusion", "unified data",
            "integrate disparate", "siloed data", "data silos", "disparate systems",
            "disparate data sources", "multi-source data", "data harmonization",
            "data ingestion", "enterprise data platform", "data integration platform",
            "master data management", "data normalization", "data lake", "data fabric",
            "data mesh", "federated data", "data consolidation", "unified environment",
        ],
    ),
    (
        "Investigative & Operational Analytics",
        25,
        [
            "investigative analytics", "investigative platform", "investigative workflow",
            "link analysis", "relationship mapping", "entity analytics",
            "operational intelligence", "operational dashboard", "situational awareness",
            "real-time dashboard", "temporal analysis", "geospatial analysis",
            "geospatial intelligence", "common operating picture", "pattern of life",
            "advanced analytics", "crime analytics", "predictive analytics",
            "intelligence platform", "all-source analytics", "mission analytics",
        ],
    ),
    (
        "Federated Search",
        25,
        [
            "federated search", "enterprise search", "cross-system search",
            "unified search", "search across", "search multiple systems",
            "multi-system search", "search and retrieval", "information retrieval",
            "search capability", "knowledge retrieval", "query across",
        ],
    ),
    (
        "Entity Resolution & Record Intelligence",
        20,
        [
            "entity resolution", "record deduplication", "record linkage",
            "record resolution", "duplicate records", "identity resolution",
            "entity matching", "data deduplication", "master record",
            "person record", "entity-centric", "record consolidation",
            "ontology", "knowledge graph", "graph analytics",
        ],
    ),
    (
        "Secure Government SaaS Platform",
        15,
        [
            "fedramp high", "fedramp moderate", "fedramp authorized",
            "cjis", "nist sp 800-53", "aws govcloud", "govcloud",
            "zero trust", "icam", "saml 2.0", "single sign-on",
            "role-based access control", "attribute-based access",
            "section 508", "wcag", "audit logging", "data sovereignty",
            "secure saas", "government saas", "cloud-hosted",
        ],
    ),
    (
        "Public Safety & Law Enforcement",
        20,
        [
            "law enforcement", "public safety", "police", "sheriff",
            "nibin", "etrace", "crime gun", "ballistic", "cgic",
            "rms", "records management system", "cad", "computer-aided dispatch",
            "first responder", "criminal investigation", "violent crime",
            "gang intelligence", "crime reduction", "officer wellness",
            "body camera", "evidence management", "fusion center",
        ],
    ),
    (
        "Corrections & Community Supervision",
        20,
        [
            "community supervision", "probation", "parole", "reentry",
            "corrections", "offender management", "supervision officer",
            "court services", "pretrial", "case supervision",
            "csosa", "bureau of prisons", "department of corrections",
            "recidivism", "offender data", "supervision platform",
            "smart21", "case management supervision",
        ],
    ),
    (
        "Platform Modernization / Incumbent Replacement",
        20,
        [
            "palantir", "gotham", "foundry", "platform replacement",
            "platform modernization", "legacy platform", "incumbent replacement",
            "platform consolidation", "technology refresh",
            "legacy modernization", "platform migration",
            "ibm i2", "data platform upgrade",
        ],
    ),
]

# Hard exclusions — if ANY of these appear, immediately discard
HARD_EXCLUSIONS = [
    # Physical / facilities
    "construction", "hvac", "janitorial", "landscaping", "food service",
    "furniture", "vehicle maintenance", "facilities management", "custodial",
    "grounds maintenance", "pest control", "generator maintenance",
    "electrical installation", "plumbing", "roofing", "flooring",
    # Medical / pharma
    "medical supply", "pharmaceutical", "drug manufacturing",
    "clinical trial", "healthcare staffing", "nursing",
    # Staffing / HR
    "staffing agency", "temp staff", "temporary personnel",
    "recruitment services", "executive search firm",
    # Logistics / supply chain
    "office supplies", "clothing", "uniform", "laundry",
    "refuse collection", "shipping", "freight",
    "aircraft maintenance", "ship repair", "vehicle fleet",
    # Hardware procurement (Peregrine is software-only)
    "hardware procurement", "server hardware", "network hardware",
    "laptop purchase", "desktop purchase", "tablet purchase",
    "computer purchase", "printer purchase", "monitor purchase",
    "switch procurement", "router procurement", "firewall appliance",
    "storage hardware", "rack equipment",
    "network cabling", "structured cabling",
    "body-worn camera purchase", "body camera hardware",
    "radio hardware", "radio procurement", "radio system purchase",
    "vehicle purchase", "vehicle acquisition", "fleet vehicle",
    "body armor", "ballistic vest", "tactical equipment purchase",
    "weapon purchase", "firearm purchase", "ammunition",
    "sensor hardware", "drone hardware", "uav procurement",
    # Medical / pharma / lab
    "medical supply", "pharmaceutical", "drug manufacturing",
    "clinical trial", "healthcare staffing", "nursing",
    "laboratory equipment", "lab supplies", "reagent",
    # Staffing / HR (pure body shop)
    "staffing agency", "temp staff", "temporary personnel",
    "recruitment services", "executive search firm",
    # Physical logistics
    "office supplies", "clothing", "uniform", "laundry",
    "refuse collection", "shipping", "freight", "moving services",
    "aircraft maintenance", "ship repair", "engine overhaul",
    # Low-signal IT services
    "help desk staffing", "printer maintenance",
    "telephone system installation", "audio visual installation",
    # Professional services unrelated to Peregrine
    "legal services", "legal counsel", "attorney services",
    "financial audit", "accounting services",
    "translation services", "interpretation services",
]

# Penalty signals — reduce score if present (suggest mismatch but don't hard exclude)
PENALTY_SIGNALS = [
    ("custom software development only", -10),   # pure dev shop ask, not SaaS
    ("staffing augmentation", -10),               # T&M body shop, not product
    ("time and materials", -8),
    ("independent verification", -8),             # IV&V work, not platform
    ("penetration testing", -8),                  # pure security assessment
    ("audit services", -8),                       # compliance audit, not platform
    ("translation services", -15),
    ("legal services", -15),
    ("training services only", -10),              # pure training contract
]


def score_opportunity(opp: Opportunity) -> Opportunity:
    """
    Score based on genuine capability match.
    Rules:
      - Hard exclusion = score -1, stop immediately
      - Past deadline = score -1, stop immediately
      - Each capability cluster can score at most once
      - NAICS alone contributes 0 — must have capability signal too
      - Agency tier adds a modest bonus only when capability already matches
      - Penalties reduce score for mismatch signals
      - Minimum 2 capability clusters must match for Strong Fit
    """
    # ── 1. Hard exclusion check ───────────────────────────────────────────────
    text = f"{opp.title} {opp.description} {opp.agency}".lower()
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

    for cap_name, cap_points, phrases in CAPABILITY_CLUSTERS:
        hits = [p for p in phrases if p.lower() in text]
        if hits:
            score += cap_points
            clusters_matched += 1
            # Show the 3 most specific hits (longest phrases = more specific)
            top_hits = sorted(hits, key=len, reverse=True)[:3]
            reasons.append(f"✓ {cap_name}: matched '{top_hits[0]}'" +
                          (f" + {len(hits)-1} more" if len(hits) > 1 else ""))

    # ── 4. Penalty signals ───────────────────────────────────────────────────
    for signal, penalty in PENALTY_SIGNALS:
        if signal.lower() in text:
            score += penalty
            reasons.append(f"⚠ Penalty: '{signal}' suggests partial mismatch ({penalty} pts)")

    # ── 5. Agency tier bonus — ONLY applied when capability already matched ──
    if clusters_matched >= 1:
        tier1_agencies = [
            "bureau of alcohol", "atf", "department of justice", "doj",
            "federal bureau of investigation", "fbi", "drug enforcement",
            "dea", "u.s. marshals", "csosa", "court services and offender",
        ]
        tier2_agencies = [
            "department of homeland security", "dhs", "customs and border",
            "cbp", "immigration and customs", "ice", "secret service",
            "transportation security", "tsa", "odni", "defense intelligence",
            "dia", "national security agency", "nsa",
        ]
        tier3_agencies = [
            "bureau of prisons", "bop", "office of justice", "ojp",
            "national institute of justice", "nij", "pretrial services",
            "department of defense", "dod", "socom", "darpa",
            "fema", "gsa",
        ]
        if any(a in text for a in tier1_agencies):
            score += 15
            reasons.append("✓ Tier 1 target agency (active Peregrine relationship/RFI)")
        elif any(a in text for a in tier2_agencies):
            score += 10
            reasons.append("✓ Tier 2 target agency (strong law enforcement/intel fit)")
        elif any(a in text for a in tier3_agencies):
            score += 5
            reasons.append("✓ Tier 3 target agency (good federal fit)")

    # ── 6. Notice type bonus ─────────────────────────────────────────────────
    if clusters_matched >= 1:
        type_bonuses = {
            "RFI": 8, "Sources Sought": 8, "Pre-Solicitation": 5,
            "Industry Day": 10, "Federal Register RFI": 7, "Award Intel": 3,
        }
        bonus = type_bonuses.get(opp.opp_type, 0)
        if bonus:
            score += bonus
            label = {
                "Industry Day": "Industry Day — attend to shape requirements",
                "RFI": "RFI — respond to shape the eventual RFP",
                "Sources Sought": "Sources Sought — demonstrate capability now",
                "Pre-Solicitation": "Pre-Solicitation — early engagement window",
                "Federal Register RFI": "Federal Register RFI — respond to shape the RFP",
            }.get(opp.opp_type, "")
            if label:
                reasons.append(f"✓ {label}")

    # ── 7. Minimum cluster rule — with exception for highly specific asks ────
    # Single-cluster matches are usually still worth seeing if the cluster
    # is highly specific to Peregrine's unique capabilities.
    specific_clusters = {
        "Federated Search",
        "Entity Resolution & Record Intelligence",
        "Corrections & Community Supervision",
        "Platform Modernization / Incumbent Replacement",
    }
    matched_cluster_names = set()
    for cap_name, cap_points, phrases in CAPABILITY_CLUSTERS:
        if any(p.lower() in text for p in phrases):
            matched_cluster_names.add(cap_name)
    is_specific_single = (
        clusters_matched == 1 and
        bool(matched_cluster_names & specific_clusters)
    )
    if clusters_matched < 2 and not is_specific_single and score >= 40:
        score = 39  # Cap at Good Fit if single non-specific cluster

    # ── 8. Assign tier — widened thresholds ─────────────────────────────────
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
    from_date = (today - timedelta(days=1)).strftime("%m/%d/%Y")
    to_date = today.strftime("%m/%d/%Y")

    notice_types = {"r": "RFI", "s": "Sources Sought", "i": "Industry Day", "p": "Pre-Solicitation"}

    for code, label in notice_types.items():
        try:
            resp = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params={"api_key": SAM_API_KEY, "postedFrom": from_date,
                        "postedTo": to_date, "noticetype": code, "limit": 100,
                        "active": "Yes"},
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
                    url=f"https://sam.gov/opp/{item.get('noticeId','')}/view",
                    opp_type=label,
                    source="SAM.gov",
                    naics=item.get("naicsCode", ""),
                )
                results.append(score_opportunity(opp))
        except Exception as e:
            print(f"[SAM.gov] Error fetching {label}: {e}")

    # Also run broad keyword search for industry days 60 days out
    for kw in [
        # Law enforcement / public safety
        "federated search law enforcement",
        "crime gun intelligence platform",
        "investigative data integration",
        "NIBIN analytics platform",
        "law enforcement SaaS",
        "public safety data platform",
        # Palantir replacement signals
        "data platform replacement",
        "enterprise intelligence platform",
        "analytics platform modernization",
        "legacy platform migration",
        # General data integration — wide net
        "data integration platform federal",
        "enterprise data unification",
        "multi-source data analytics",
        "data fusion platform",
        "disparate data sources integration",
        # Corrections / supervision
        "community supervision data platform",
        "probation parole data integration",
        "corrections intelligence platform",
        "offender management analytics",
        # Defense / intel
        "operational intelligence platform",
        "mission data analytics",
        "all source analytics",
    ]:
        try:
            resp = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params={"api_key": SAM_API_KEY, "keywords": kw,
                        "postedFrom": (today - timedelta(days=7)).strftime("%m/%d/%Y"),
                        "postedTo": (today + timedelta(days=60)).strftime("%m/%d/%Y"),
                        "noticetype": "i", "limit": 25,
                        "active": "Yes"},
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
                    url=f"https://sam.gov/opp/{item.get('noticeId','')}/view",
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
    since = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    search_terms = [
        # Law enforcement
        "request for information federated search law enforcement",
        "sources sought investigative analytics platform",
        "request for information crime gun intelligence",
        "sources sought data integration public safety",
        "request for information CJIS compliant platform",
        "sources sought NIBIN analytics",
        # Palantir replacement / general data platform
        "request for information enterprise data platform",
        "sources sought data integration platform",
        "request for information analytics modernization",
        "sources sought data unification platform",
        "request for information data fusion platform",
        # Corrections / supervision
        "sources sought community supervision data",
        "request for information corrections data integration",
        "sources sought probation parole platform",
        "request for information offender management",
        # Defense / intel
        "request for information operational intelligence",
        "sources sought mission analytics platform",
        "industry day data integration federal",
        "request for information FedRAMP High data platform",
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
                url = doc.get("html_url", f"https://www.federalregister.gov/documents/{doc_id}")

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

            time.sleep(0.3)  # Be polite to the API
        except Exception as e:
            print(f"[FederalRegister] Error for '{term}': {e}")

    print(f"[Federal Register] {len(results)} notices fetched")
    return results


# ---------------------------------------------------------------------------
# SOURCE 3: USASPENDING.GOV API v2
# NO API KEY REQUIRED — completely open
# Use case: competitive intelligence — find recent contracts in our NAICS space
# so we know who's spending, on what, and can pursue follow-on/recompete opps
# ---------------------------------------------------------------------------
def fetch_usaspending_intel() -> list[Opportunity]:
    results = []
    today = datetime.utcnow()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    keyword_batches = [
        ["law enforcement analytics platform"],
        ["federated search government"],
        ["crime intelligence platform"],
        ["public safety data integration"],
        ["investigative software"],
        ["community supervision platform"],
        ["corrections data analytics"],
        ["enterprise data integration platform"],
        ["data fusion analytics"],
        ["operational intelligence platform"],
        ["data platform modernization"],
    ]

    for keywords in keyword_batches:
        payload = {
            "subawards": False,
            "limit": 15,
            "page": 1,
            "filters": {
                "keywords": keywords,
                "award_type_codes": ["A", "B", "C", "D"],
                "time_period": [{"start_date": start_date, "end_date": end_date}],
                "naics_codes": NAICS_CODES,
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
                    url=f"https://www.usaspending.gov/award/{award_id}/",
                    opp_type="Award Intel",
                    source="USASpending.gov",
                )
                results.append(score_opportunity(opp))

            time.sleep(0.5)
        except Exception as e:
            print(f"[USASpending] Error: {e}")

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
                    url=url_,
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
def fetch_congress_signals() -> list[Opportunity]:
    results = []
    today = datetime.utcnow()
    since = (today - timedelta(days=3)).strftime("%Y-%m-%d")

    # Congress.gov offers public RSS feeds for committee activities
    committee_feeds = [
        {
            "url": "https://www.congress.gov/rss/most-recent-bills.xml",
            "label": "Most Recent Bills",
        },
        {
            "url": "https://www.congress.gov/rss/most-recent-actions.xml",
            "label": "Most Recent Actions",
        },
    ]

    signal_keywords = [
        "public safety", "law enforcement", "artificial intelligence", "data analytics",
        "homeland security", "emergency management", "cybersecurity", "surveillance",
        "technology", "digital", "information technology",
    ]

    for feed in committee_feeds:
        try:
            resp = requests.get(feed["url"], headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")

            for item in items[:15]:
                title_el = item.find("title")
                link_el  = item.find("link")
                desc_el  = item.find("description")
                date_el  = item.find("pubDate")

                title = title_el.text or "Untitled" if title_el is not None else "Untitled"
                desc  = unescape(re.sub("<[^>]+>", "", desc_el.text or "")) if desc_el is not None else ""
                url_  = link_el.text or "" if link_el is not None else ""
                date_ = date_el.text or "" if date_el is not None else ""

                combined = f"{title} {desc}".lower()
                if not any(kw in combined for kw in signal_keywords):
                    continue

                opp = Opportunity(
                    title=f"[LEGISLATIVE SIGNAL] {title}",
                    notice_id=f"CON-{hash(title) % 10**9}",
                    agency="U.S. Congress",
                    posted_date=date_[:10] if date_ else today.strftime("%Y-%m-%d"),
                    response_date="Monitor for follow-on procurement",
                    description=(
                        f"Congressional activity that may signal upcoming federal procurement. "
                        f"{desc[:800]}"
                    ),
                    url=url_,
                    opp_type="Legislative Signal",
                    source="Congress.gov",
                )
                results.append(score_opportunity(opp))
            time.sleep(0.2)
        except Exception as e:
            print(f"[Congress] {feed['label']}: {e}")

    print(f"[Congress.gov] {len(results)} signals fetched")
    return results


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

    filtered = [
        o for o in unique
        if o.score > 0 and
        o.tier not in ("⚪ Low Fit", "⛔ Not a Fit", "⛔ Expired")
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

def opp_card(o: Opportunity) -> str:
    tier_color = {
        "🟢 Strong Fit": "#1a7a4a",
        "🟡 Good Fit":   "#7a6a00",
        "🔵 Possible Fit": "#1a4a7a",
    }.get(o.tier, "#555")

    reasons_html = "".join(f"<li style='margin-bottom:2px'>{r}</li>" for r in o.score_reasons)
    desc_preview = o.description[:350].strip() + ("..." if len(o.description) > 350 else "")

    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin-bottom:12px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
      <div style="margin-bottom:8px;">
        <span style="font-size:11px;font-weight:700;color:{tier_color};background:{tier_color}18;
                     padding:2px 8px;border-radius:12px;">{o.tier} · {o.score}pts</span>
        {source_badge(o.source)}
        <span style="font-size:11px;color:#888;margin-left:6px;">{o.opp_type}</span>
      </div>
      <div style="font-weight:700;font-size:15px;color:#111;margin-bottom:5px;line-height:1.3">{o.title}</div>
      <div style="font-size:12px;color:#666;margin-bottom:8px;">
        🏛 {o.agency[:80]}
        &nbsp;·&nbsp; 📅 {o.posted_date}
        &nbsp;·&nbsp; ⏰ {o.response_date}
      </div>
      <div style="font-size:13px;color:#333;line-height:1.5;margin-bottom:8px;">{desc_preview}</div>
      <div style="font-size:12px;color:#555;margin-bottom:10px;">
        <strong>Why it fits:</strong>
        <ul style="margin:3px 0 0 14px;padding:0;">{reasons_html}</ul>
      </div>
      <a href="{o.url}" style="display:inline-block;background:#0057b8;color:#fff;text-decoration:none;
         padding:6px 14px;border-radius:5px;font-size:12px;font-weight:600;">View Source →</a>
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

def build_html_email(opps: list[Opportunity], run_date: str,
                     source_counts: dict) -> str:
    tiers = {
        "strong":   [o for o in opps if "Strong" in o.tier],
        "good":     [o for o in opps if "Good" in o.tier],
        "possible": [o for o in opps if "Possible" in o.tier],
    }
    ind_days  = [o for o in opps if o.opp_type == "Industry Day"]
    fr_rfis   = [o for o in opps if o.source == "Federal Register"]
    usa_intel = [o for o in opps if o.source == "USASpending.gov"]
    signals   = [o for o in opps if o.source == "Congress.gov"]
    events    = [o for o in opps if o.source == "Events Intelligence"]

    stats = [
        ("Total", len(opps)),
        ("🟢 Strong", len(tiers["strong"])),
        ("🟡 Good", len(tiers["good"])),
        ("RFIs/SS", sum(1 for o in opps if o.opp_type in ("RFI", "Sources Sought", "Federal Register RFI"))),
        ("Industry Days", len(ind_days)),
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
  {build_section("🔍 Competitive Intel (Recent Awards)", usa_intel[:8])}
  {build_section("🏛 Legislative Signals", signals[:5])}
  {build_section("🎤 Events & Conferences to Attend", sorted([o for o in opps if o.source == "Events Intelligence"], key=lambda x: x.score, reverse=True))}

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
                    url=url_,
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
            url=ev["url"],
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
        ("Congress.gov",      fetch_congress_signals),
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

    strong = sum(1 for o in ranked if "Strong" in o.tier)
    good   = sum(1 for o in ranked if "Good" in o.tier)

    # Build and send
    html = build_html_email(ranked, run_date, source_counts)
    subject = f"🦅 Peregrine Daily — {strong} Strong · {good} Good Fits | {run_date}"
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
