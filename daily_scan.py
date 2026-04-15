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
    "public safety", "law enforcement", "data integration", "data fusion",
    "real-time analytics", "decision support", "situational awareness",
    "emergency management", "crime analytics", "predictive analytics",
    "FedRAMP", "mission critical", "operational intelligence",
    "data platform", "artificial intelligence", "machine learning",
    "CJIS", "justice information", "first responder", "homeland security",
    "geospatial", "visualization", "real-time dashboard",
    "data driven policing", "smart policing", "officer wellness",
    "incident management", "investigative analytics",
]

MEDIUM_VALUE_KEYWORDS = [
    "data analytics", "cloud platform", "SaaS", "software as a service",
    "data management", "API", "interoperability", "digital transformation",
    "modernization", "cybersecurity", "AWS GovCloud", "zero trust",
    "state and local", "DoD", "DHS", "DOJ", "FBI", "ATF", "DEA",
    "intelligence", "sensor fusion", "IoT", "smart city",
    "data warehouse", "ETL", "pipeline", "dashboard", "AI/ML",
    "natural language processing", "NLP", "computer vision",
    "records management", "CAD", "computer-aided dispatch",
]

NEGATIVE_KEYWORDS = [
    "construction", "HVAC", "janitorial", "landscaping", "food service",
    "furniture", "vehicle maintenance", "facilities management", "printing",
    "audio visual installation", "base operations", "custodial",
    "grounds maintenance", "pest control", "generator", "electrical install",
]

NAICS_CODES = [
    "541511",  # Custom Computer Programming
    "541512",  # Computer Systems Design
    "541519",  # Other Computer Related
    "541715",  # R&D Physical Sciences
    "518210",  # Data Processing and Hosting
    "519130",  # Internet Publishing / Web Portals
    "541690",  # Other Scientific/Technical Consulting
]

TARGET_AGENCIES = [
    "Department of Homeland Security", "DHS",
    "Department of Justice", "DOJ",
    "Federal Bureau of Investigation", "FBI",
    "Drug Enforcement Administration", "DEA",
    "Bureau of Alcohol, Tobacco", "ATF",
    "Department of Defense", "DoD",
    "Department of the Army", "Department of the Navy", "Department of the Air Force",
    "Office of the Director of National Intelligence", "ODNI",
    "Customs and Border Protection", "CBP",
    "Secret Service", "U.S. Marshals",
    "Transportation Security Administration", "TSA",
    "Immigration and Customs Enforcement", "ICE",
    "General Services Administration", "GSA",
    "Department of Health and Human Services", "HHS",
    "Centers for Disease Control", "CDC",
    "Federal Emergency Management Agency", "FEMA",
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
# SCORING ENGINE
# ---------------------------------------------------------------------------
def score_opportunity(opp: Opportunity) -> Opportunity:
    text = f"{opp.title} {opp.description} {opp.agency}".lower()
    score = 0
    reasons = []

    for kw in NEGATIVE_KEYWORDS:
        if kw.lower() in text:
            opp.score = -1
            opp.tier = "⛔ Not a Fit"
            opp.score_reasons = [f"Excluded: contains '{kw}'"]
            return opp

    high_hits = [kw for kw in HIGH_VALUE_KEYWORDS if kw.lower() in text]
    if high_hits:
        score += min(len(high_hits) * 15, 60)
        reasons.append(f"Core match: {', '.join(high_hits[:5])}")

    med_hits = [kw for kw in MEDIUM_VALUE_KEYWORDS if kw.lower() in text]
    if med_hits:
        score += min(len(med_hits) * 5, 25)
        reasons.append(f"Adjacent match: {', '.join(med_hits[:5])}")

    if opp.naics and any(opp.naics.startswith(n) for n in NAICS_CODES):
        score += 15
        reasons.append(f"NAICS match: {opp.naics}")

    agency_hits = [a for a in TARGET_AGENCIES if a.lower() in text]
    if agency_hits:
        score += min(len(agency_hits) * 5, 15)
        reasons.append(f"Target agency: {agency_hits[0]}")

    type_bonuses = {
        "RFI": 5, "Sources Sought": 5, "Pre-Solicitation": 3,
        "Industry Day": 8, "Federal Register RFI": 6, "Award Intel": 2,
    }
    if opp.opp_type in type_bonuses:
        score += type_bonuses[opp.opp_type]
        if opp.opp_type == "Industry Day":
            reasons.append("Industry Day — attend to shape requirements")
        elif opp.opp_type in ("RFI", "Sources Sought", "Federal Register RFI"):
            reasons.append("Early-stage: respond to shape the RFP")

    if score >= 50:
        tier = "🟢 Strong Fit"
    elif score >= 25:
        tier = "🟡 Good Fit"
    elif score > 0:
        tier = "🔵 Possible Fit"
    else:
        tier = "⚪ Low Fit"

    opp.score = score
    opp.tier = tier
    opp.score_reasons = reasons if reasons else ["No strong keyword matches found"]
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
                    url=f"https://sam.gov/opp/{item.get('noticeId','')}/view",
                    opp_type=label,
                    source="SAM.gov",
                    naics=item.get("naicsCode", ""),
                )
                results.append(score_opportunity(opp))
        except Exception as e:
            print(f"[SAM.gov] Error fetching {label}: {e}")

    # Also run broad keyword search for industry days 60 days out
    for kw in ["public safety data analytics", "law enforcement AI", "data integration emergency"]:
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
        "request for information public safety data",
        "sources sought law enforcement analytics",
        "request for information data integration",
        "industry day public safety technology",
        "sources sought artificial intelligence law enforcement",
        "request for information situational awareness",
        "sources sought emergency management platform",
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
        ["public safety"],
        ["law enforcement software"],
        ["data analytics government"],
        ["emergency management technology"],
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

    filtered = [o for o in unique if o.score > 0]
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

    stats = [
        ("Total", len(opps)),
        ("🟢 Strong", len(tiers["strong"])),
        ("🟡 Good", len(tiers["good"])),
        ("RFIs/SS", sum(1 for o in opps if o.opp_type in ("RFI", "Sources Sought", "Federal Register RFI"))),
        ("Industry Days", len(ind_days)),
        ("Award Intel", len(usa_intel)),
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
    <div style="font-size:13px;opacity:0.75;margin-top:5px">{run_date} &nbsp;·&nbsp; 5 Sources Searched</div>
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

  {build_section("🟢 Strong Fit — Act Now", tiers["strong"])}
  {build_section("🟡 Good Fit — Review Today", tiers["good"])}
  {build_section("📅 Industry Days & Events", ind_days)}
  {build_section("📰 Federal Register RFIs", [o for o in fr_rfis if o.opp_type != "Industry Day" and "Strong" not in o.tier and "Good" not in o.tier])}
  {build_section("🔍 Competitive Intel (Recent Awards)", usa_intel[:8])}
  {build_section("🏛 Legislative Signals", signals[:5])}
  {build_section("🔵 Possible Fit — Scan Manually", tiers["possible"])}

  <!-- Footer -->
  <div style="text-align:center;font-size:11px;color:#bbb;margin-top:24px;padding:20px;border-top:1px solid #e0e0e0">
    Peregrine Daily Scanner &nbsp;·&nbsp; {run_date}<br>
    Sources: SAM.gov · Federal Register · USASpending.gov · Agency RSS · Congress.gov<br><br>
    <a href="https://sam.gov/search/?index=opp" style="color:#0057b8;text-decoration:none">Browse all SAM.gov</a> &nbsp;·&nbsp;
    <a href="https://www.federalregister.gov/documents/search?conditions%5Btype%5D%5B%5D=NOTICE" style="color:#0057b8;text-decoration:none">Federal Register Notices</a> &nbsp;·&nbsp;
    <a href="https://www.usaspending.gov/search" style="color:#0057b8;text-decoration:none">USASpending Search</a>
  </div>
</div>
</body></html>"""


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
        ("SAM.gov",          fetch_sam_gov),
        ("Federal Register", fetch_federal_register),
        ("USASpending.gov",  fetch_usaspending_intel),
        ("Agency RSS",       fetch_agency_rss_feeds),
        ("Congress.gov",     fetch_congress_signals),
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
