#!/usr/bin/env python3
"""
Peregrine.io Daily Federal Opportunity Scanner
Searches SAM.gov, Federal Register, USASpending.gov and agency sources
for opportunities matching Peregrine's capabilities.
"""
import os, re, time, json
import xml.etree.ElementTree as ET
from html import unescape
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import requests

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
SAM_API_KEY      = os.environ.get("SAM_API_KEY", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_TO         = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
EMAIL_FROM       = os.environ.get("EMAIL_FROM", "mikefkelly26@gmail.com")

print(f"[Config] SAM_API_KEY set:      {'YES' if SAM_API_KEY else 'NO'}")
print(f"[Config] SENDGRID_API_KEY set: {'YES' if SENDGRID_API_KEY else 'NO'}")
print(f"[Config] EMAIL_TO:             {EMAIL_TO}")
print(f"[Config] EMAIL_FROM:           {EMAIL_FROM}")

HEADERS = {"User-Agent": "PeregrineScanner/3.0", "Accept": "application/json"}

# ---------------------------------------------------------------------------
# DATA MODEL
# ---------------------------------------------------------------------------
@dataclass
class Opportunity:
    title:         str
    notice_id:     str
    agency:        str
    posted_date:   str
    response_date: str
    description:   str
    url:           str
    opp_type:      str
    source:        str
    naics:         str   = ""
    score:         int   = 0
    tier:          str   = ""
    score_reasons: list  = field(default_factory=list)

# ---------------------------------------------------------------------------
# SCORING DATA
# ---------------------------------------------------------------------------
CAPABILITY_CLUSTERS = [
    ("Data Integration & Unification", 20, [
        "data integration", "data unification", "data fusion", "data silos",
        "data harmonization", "enterprise data platform", "data consolidation",
        "data normalization", "data pipeline", "data fabric", "data lake",
        "data warehouse", "data mesh", "data analytics", "analytics platform",
        "data management", "data management platform", "data solution",
        "data platform", "analytics solution", "business intelligence",
        "software platform", "enterprise software", "cloud platform",
        "information sharing", "master data", "data environment",
        "unified data", "data integration platform", "data unification platform",
    ]),
    ("Investigative & Operational Analytics", 20, [
        "investigative analytics", "investigative platform",
        "link analysis", "relationship mapping", "situational awareness",
        "operational intelligence", "crime analytics", "crime analysis",
        "advanced analytics", "intelligence platform", "predictive analytics",
        "geospatial analysis", "geospatial intelligence",
        "digital evidence", "evidence review platform", "evidence analytics",
        "evidence management platform", "digital forensics platform",
        "investigative data platform", "body camera analytics",
        "intelligence-led", "intelligence led policing",
    ]),
    ("Federated & Enterprise Search", 20, [
        "federated search", "enterprise search", "cross-system search",
        "unified search", "cross-database search", "multi-source search",
        "search federation", "information retrieval",
    ]),
    ("Entity Resolution & Record Intelligence", 20, [
        "entity resolution", "record deduplication", "record linkage",
        "duplicate records", "identity resolution", "identity matching",
        "record matching", "data deduplication", "entity matching",
        "knowledge graph", "person resolution", "fuzzy matching",
        "probabilistic matching", "golden record", "data quality",
        "master person index", "subject resolution",
    ]),
    ("Secure Government SaaS", 15, [
        "cyber essentials", "official sensitive", "jsig", "iso 27001",
        "cloud security", "secure cloud", "government cloud",
        "g-cloud", "gcloud", "govcloud", "zero trust",
        "identity and access management", "iam", "audit logging",
        "il2", "il3", "il4", "fedramp", "cjis compliant",
        "dsp toolkit",
    ]),
    ("Public Safety & Law Enforcement", 20, [
        "law enforcement platform", "law enforcement software",
        "law enforcement analytics", "law enforcement data",
        "law enforcement technology", "police data", "police analytics",
        "policing platform", "policing software", "policing analytics",
        "public safety platform", "public safety software",
        "public safety technology", "public safety data",
        "public safety analytics", "public safety system",
        "records management system", "crime recording system",
        "custody suite", "custody management",
        "computer aided dispatch", "cad system",
        "serious and organised crime", "intelligence management",
        "national intelligence model",
        "fusion centre", "fusion center",
        "digital forensics", "digital investigation",
        "body worn video", "body worn camera",
        "automatic number plate recognition", "anpr",
        "police national database", "pnd", "pnc",
        "decentralized information sharing",
        "information sharing environment",
    ]),
    ("Corrections & Community Supervision", 20, [
        "community supervision", "probation", "parole",
        "offender management", "prison management", "prisoner management",
        "case management system", "rehabilitation technology",
        "hmpps", "hmps", "noms", "nomis", "oasys", "delius",
        "youth offending", "youth justice",
        "electronic monitoring", "electronic tagging",
        "curfew monitoring", "offender data",
        "community payback", "unpaid work", "reoffending",
    ]),
    ("Platform Modernization & Replacement", 20, [
        "platform replacement", "incumbent replacement",
        "platform consolidation", "legacy platform",
        "legacy system", "legacy modernization",
        "platform modernization", "it modernization",
        "digital transformation", "cloud migration",
        "software modernization", "application modernization",
        "system replacement", "re-platforming",
        "palantir", "niche", "xhibit",
        "commercial solutions opening", "other transaction authority",
        "sovereign cloud", "defense cloud",
    ]),
    ("AI & Machine Learning", 22, [
        "artificial intelligence", "machine learning",
        "ai platform", "ai solution", "ai system",
        " generative ai", "generative ai ",
        "large language model", " llm ",
        "natural language processing", " nlp ",
        "computer vision", "predictive model",
        "decision support", "automated analysis",
        "ai-powered", "ai-driven",
        "responsible ai", "explainable ai",
        "ai governance", "ai analytics",
    ]),
]

HARD_EXCLUSIONS = [
    # Physical facilities
    "fire suppression", "fire alarm", "hvac", "plumbing",
    "electrical installation", "roof replacement", "flooring",
    "window replacement", "lift maintenance", "elevator maintenance",
    "lighting installation", "cctv installation",
    # Physical goods
    "uniform supply", "stationery", "office furniture", "catering",
    "food supply", "medical supplies", "pharmaceutical", "laundry",
    "body armour", "body armor", "taser", "firearms", "ammunition",
    "vehicle purchase", "vehicle fleet", "fleet procurement",
    "fleet management", "radio procurement", "handheld radio",
    # Construction / estates
    "construction works", "refurbishment", "building works",
    "estates management", "facilities management",
    "grounds maintenance", "grass cutting", "hedge cutting",
    "landscaping", "grounds keeping", "cleaning contract",
    "janitorial", "waste management",
    # Network/telecom infrastructure
    "network cabling", "structured cabling", "fibre optic installation",
    "wide area network service", "mobile phone contract",
    "telephony system", "pbx system", "voip hardware",
    # Hardware maintenance
    "hardware maintenance contract", "server hardware support",
    "printer maintenance", "copier maintenance", "mfd maintenance",
    "planned preventive maintenance", "ppm contract",
    # Licence renewals
    "microsoft licence renewal", "oracle licence renewal",
    "software licence renewal",
    # Staffing only
    "staffing agency", "temporary staffing",
    "security guard services", "door supervision",
    # Treatment / social
    "drug treatment", "alcohol treatment",
    "mental health treatment", "substance misuse",
    "domestic abuse refuge", "homeless shelter",
    # Training only
    "firearms training", "first aid training",
    "physical training contract", "driver training",
    # Military hardware
    "avionics", "missile", "munitions", "weapons system",
    "naval vessel", "aircraft maintenance contract",
]

TIER_STRONG = 40
TIER_GOOD   = 15

NAICS_HINTS = {
    "541": "software IT services data analytics platform",
    "5411": "legal services",
    "5415": "computer systems design data integration analytics",
    "54151": "computer programming software development analytics platform",
    "54152": "computer systems design integration",
    "518": "cloud computing data processing hosting",
    "519": "data analytics information services",
    "922": "law enforcement criminal justice public safety",
    "9221": "law enforcement police public safety",
    "9221120": "police law enforcement records analytics",
    "9221150": "probation parole corrections supervision",
}

# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------
def parse_date_flexible(date_str: str) -> Optional[datetime]:
    if not date_str or date_str in ("TBD", ""):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str[:19], fmt)
        except Exception:
            pass
    return None


def is_expired(opp: Opportunity) -> bool:
    dt = parse_date_flexible(opp.response_date)
    if dt and dt < datetime.utcnow() - timedelta(days=2):
        return True
    return False


def clean_url(url: str, fallback: str = "") -> str:
    if not url:
        return fallback
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url

# ---------------------------------------------------------------------------
# SCORING ENGINE
# ---------------------------------------------------------------------------
def score_opportunity(opp: Opportunity) -> Opportunity:
    # Build text — title + description + NAICS hint
    naics_hint = ""
    for prefix, hint in NAICS_HINTS.items():
        if opp.naics and opp.naics.startswith(prefix):
            naics_hint = hint
            break

    text = f" {opp.title} {opp.description} {naics_hint} ".lower()

    # Hard exclusions
    for excl in HARD_EXCLUSIONS:
        if excl in text:
            opp.score = 0
            opp.tier = "⛔ Not a Fit"
            opp.score_reasons = [f"Excluded: '{excl}'"]
            return opp

    # Score against clusters
    total, reasons = 0, []
    for cluster_name, pts, phrases in CAPABILITY_CLUSTERS:
        matched = next((p for p in phrases if p in text), None)
        if matched:
            total += pts
            reasons.append(f"✓ {cluster_name}: matched '{matched}'")

    # Engagement event boost — Industry Days, RFIs, Sources Sought
    # from Tier 1 agencies score even with generic titles
    if total == 0:
        ENGAGEMENT = [
            "industry day", "vendor day", "market survey",
            "sources sought", "request for information",
            "broad agency announcement", "baa",
            "commercial solutions opening",
            "pre-solicitation", "market research",
            "notice of intent", "special notice",
            "industry engagement",
        ]
        TIER1 = [
            "department of justice", "justice,", "federal bureau",
            "fbi", "atf", "bureau of prisons", "dea ",
            "drug enforcement", "marshals", "court services",
            "csosa", "eousa", "homeland security", "ice,",
            "immigration", "customs and border", "cisa",
            "secret service", "coast guard", "defense,",
            "dept of defense", "army", "navy", "air force",
            "national guard", "treasury", "fincen", "ofac",
        ]
        full_text = f" {opp.title} {opp.description} ".lower()
        agency_l  = opp.agency.lower()
        is_eng = any(s in full_text for s in ENGAGEMENT)
        is_t1  = any(a in agency_l for a in TIER1)
        if is_eng and is_t1:
            total = 15
            reasons = ["✓ Engagement event — Tier 1 LE/Security agency: watch for follow-on RFP"]
        elif is_eng:
            total = 5
            reasons = ["✓ Engagement event — Federal agency: potential expansion opportunity"]

    opp.score = total
    opp.score_reasons = reasons
    opp.tier = (
        "🟢 Strong Fit" if total >= TIER_STRONG
        else "🟡 Good Fit" if total >= TIER_GOOD
        else "🔵 Possible Fit" if total > 0
        else "⚪ Low Fit"
    )
    return opp

# ---------------------------------------------------------------------------
# SAM.GOV FETCH
# ---------------------------------------------------------------------------
_SAM_RATE_LIMITED = [False]
_SAM_RESULTS_CACHE: list = []


def _sam_search(extra_params: dict, label: str,
                seen_ids: set, results: list,
                pages: int = 1) -> bool:
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
                params=params, headers=HEADERS, timeout=20,
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
                    response_date = (item.get("responseDeadLine")
                                     or item.get("reponseDeadLine", "TBD")),
                    description   = (item.get("description") or "")[:500],
                    url           = clean_url(f"https://sam.gov/opp/{nid}/view"),
                    opp_type      = item.get("type") or "Notice",
                    source        = "SAM.gov",
                    naics         = item.get("naicsCode", ""),
                )))
            if new_count:
                print(f"[SAM.gov] {label} p{page+1}: "
                      f"{data.get('totalRecords',0)} total | {new_count} new")
            if len(items) < 100:
                break
            time.sleep(0.2)
        except Exception as e:
            print(f"[SAM.gov] {label}: {e}")
            return True
    time.sleep(0.15)
    return True


def fetch_sam_gov() -> list:
    if not SAM_API_KEY:
        print("[SAM.gov] No API key — skipping")
        return []

    results, seen_ids = [], set()
    today   = datetime.utcnow()
    to_date = today.strftime("%m/%d/%Y")
    d30     = (today - timedelta(days=30)).strftime("%m/%d/%Y")
    d90     = (today - timedelta(days=90)).strftime("%m/%d/%Y")

    # Pass 1: ptype sweeps — all agencies, last 30 days, 4 pages each
    for ptype, lbl in [
        ("r", "Sources Sought"), ("p", "Presolicitation"),
        ("k", "Combined Synopsis"), ("s", "Special Notice"),
        ("o", "Solicitation"), ("i", "Intent to Bundle"),
    ]:
        if not _sam_search({"ptype": ptype, "postedFrom": d30, "postedTo": to_date},
                           lbl, seen_ids, results, pages=4):
            break

    # Pass 2: keyword searches — broad, catches description matches
    KW = [
        ("data management solutions",    1),
        ("sovereign cloud",              1),
        ("commercial solutions opening", 1),
        ("federated search",             1),
        ("investigative analytics",      1),
        ("entity resolution",            1),
        ("law enforcement analytics",    1),
        ("offender management system",   1),
        ("community supervision",        1),
        ("intelligence platform",        1),
        ("data integration platform",    1),
        ("records management system",    1),
        ("industry day",                 10),
        ("sources sought data",          1),
        ("broad agency announcement",    3),
    ]
    for term, pages in KW:
        if _SAM_RATE_LIMITED[0]:
            break
        _sam_search({"keyword": term, "postedFrom": d90, "postedTo": to_date},
                    f"kw={term}", seen_ids, results, pages=pages)

    # Pass 3: title searches — specific capability terms
    TITLE = [
        ("data analytics",            3), ("data integration",         3),
        ("artificial intelligence",   3), ("machine learning",         2),
        ("platform modernization",    2), ("digital transformation",   2),
        ("data management",           3), ("data management solutions",1),
        ("investigative platform",    1), ("digital evidence",         1),
        ("crime analytics",           1), ("fusion center",            1),
        ("public safety platform",    1), ("predictive analytics",     2),
        ("enterprise data",           1), ("zero trust analytics",     1),
        ("fedramp analytics",         1), ("identity resolution",      1),
        ("corrections platform",      1), ("community supervision",    1),
        ("law enforcement analytics", 1), ("sovereign cloud",          1),
    ]
    for term, pages in TITLE:
        if _SAM_RATE_LIMITED[0]:
            break
        _sam_search({"title": term, "postedFrom": d90, "postedTo": to_date},
                    f"title={term}", seen_ids, results, pages=pages)

    # Pass 4: deadline-based search — catches open notices regardless of posted date
    today_str  = today.strftime("%m/%d/%Y")
    future_str = (today + timedelta(days=180)).strftime("%m/%d/%Y")
    for term in ["data management solutions", "law enforcement analytics",
                 "intelligence platform", "federated search"]:
        if _SAM_RATE_LIMITED[0]:
            break
        _sam_search({"title": term, "rdlfrom": today_str, "rdlto": future_str},
                    f"rdl={term}", seen_ids, results)

    # Pass 5: watchlist — direct notice ID lookups
    WATCH = [
        "b2910bda98f342149cd76c39de3038c6",  # Data Management Solutions — FBI
        "55c0c5ea5ef84232869c0134386dfa48",  # Sovereign Defense Cloud — ERDC
        "d32237c586bc45489644f757c52faa22",  # FBI CJIS Decentralized Info Sharing
        "7ca078ff27e24fd2b06a1553bbeadc59",  # DOJ Industry Day
        "70e476afd4584a63a9890f0071e4871e",  # Additional notice
    ]
    for nid in WATCH:
        if _SAM_RATE_LIMITED[0]:
            break
        try:
            r = requests.get(
                "https://api.sam.gov/opportunities/v2/search",
                params={"api_key": SAM_API_KEY, "noticeid": nid, "limit": 5},
                headers=HEADERS, timeout=15,
            )
            if r.status_code == 200:
                for item in r.json().get("opportunitiesData", []):
                    item_nid = item.get("noticeId") or item.get("id") or ""
                    if not item_nid or item_nid in seen_ids:
                        continue
                    seen_ids.add(item_nid)
                    opp = score_opportunity(Opportunity(
                        title         = item.get("title", "Untitled"),
                        notice_id     = item_nid,
                        agency        = item.get("fullParentPathName") or "Unknown",
                        posted_date   = item.get("postedDate", ""),
                        response_date = (item.get("responseDeadLine")
                                         or item.get("reponseDeadLine", "TBD")),
                        description   = (item.get("description") or "")[:500],
                        url           = clean_url(f"https://sam.gov/opp/{item_nid}/view"),
                        opp_type      = item.get("type") or "Notice",
                        source        = "SAM.gov",
                        naics         = item.get("naicsCode", ""),
                    ))
                    results.append(opp)
                    print(f"[SAM.gov] Watch ✓ {item.get('title','?')[:55]} | score={opp.score}")
        except Exception as e:
            print(f"[SAM.gov] Watch {nid[:8]}: {e}")

    _SAM_RESULTS_CACHE.clear()
    _SAM_RESULTS_CACHE.extend(results)
    print(f"[SAM.gov] {len(results)} total opportunities")
    return results

# ---------------------------------------------------------------------------
# AGENCY FILTERS (DOJ / DHS / DoD)
# ---------------------------------------------------------------------------
DOJ_FRAGS = [
    "department of justice", "dept of justice", "alcohol, tobacco", "atf",
    "federal bureau of investigation", "fbi", "drug enforcement", "dea",
    "bureau of prisons", "bop", "office of justice programs", "ojp",
    "court services and offender", "csosa", "community oriented policing",
    "u.s. marshals", "marshals service",
    "executive office for united states attorneys", "national security division",
]
DHS_FRAGS = [
    "homeland security", "dhs", "customs and border protection", "cbp",
    "immigration and customs enforcement", "ice",
    "cybersecurity and infrastructure", "cisa",
    "federal emergency management", "fema",
    "transportation security administration", "tsa",
    "secret service", "usss", "citizenship and immigration services",
    "federal law enforcement training", "fletc",
]
DOD_FRAGS = [
    "national guard", "national guard bureau", "defense information systems",
    "disa", "defense intelligence agency", "dia",
    "defense logistics agency", "dla", "defense advanced research", "darpa",
    "engineer research and development", "erdc",
    "army corps of engineers", "army research laboratory",
    "defense counterintelligence", "dcsa",
    "office of the secretary of defense", "osd",
    "army", "navy", "air force", "marine corps", "space force",
    "joint chiefs", "combatant command",
]


def _is_doj(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DOJ_FRAGS)

def _is_dhs(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DHS_FRAGS)

def _is_dod(path: str) -> bool:
    p = path.lower()
    return any(f in p for f in DOD_FRAGS)


def _agency_sweep(agencies: list, label: str) -> list:
    """Fetch ALL notices from each agency and score locally."""
    if not SAM_API_KEY or _SAM_RATE_LIMITED[0]:
        return []
    results, seen_ids = [], set(o.notice_id for o in _SAM_RESULTS_CACHE)
    today   = datetime.utcnow()
    d90     = (today - timedelta(days=90)).strftime("%m/%d/%Y")
    to_date = today.strftime("%m/%d/%Y")
    for agency in agencies:
        if _SAM_RATE_LIMITED[0]:
            break
        new_count = 0
        for page in range(3):
            if _SAM_RATE_LIMITED[0]:
                break
            try:
                r = requests.get(
                    "https://api.sam.gov/opportunities/v2/search",
                    params={"api_key": SAM_API_KEY, "organizationName": agency,
                            "postedFrom": d90, "postedTo": to_date,
                            "limit": 100, "offset": page * 100},
                    headers=HEADERS, timeout=20,
                )
                if r.status_code == 429:
                    _SAM_RATE_LIMITED[0] = True
                    break
                if r.status_code != 200:
                    break
                items = r.json().get("opportunitiesData", [])
                for item in items:
                    nid = item.get("noticeId") or item.get("id") or ""
                    if not nid or nid in seen_ids:
                        continue
                    seen_ids.add(nid)
                    new_count += 1
                    results.append(score_opportunity(Opportunity(
                        title         = item.get("title", "Untitled"),
                        notice_id     = nid,
                        agency        = item.get("fullParentPathName") or agency,
                        posted_date   = item.get("postedDate", ""),
                        response_date = (item.get("responseDeadLine")
                                         or item.get("reponseDeadLine", "TBD")),
                        description   = (item.get("description") or "")[:500],
                        url           = clean_url(f"https://sam.gov/opp/{nid}/view"),
                        opp_type      = item.get("type") or "Notice",
                        source        = "SAM.gov",
                        naics         = item.get("naicsCode", ""),
                    )))
                if len(items) < 100:
                    break
                time.sleep(0.2)
            except Exception as e:
                print(f"[{label}] {agency[:30]}: {e}")
                break
        if new_count:
            print(f"[{label}] {agency[:35]}: {new_count} new")
        time.sleep(0.2)
    # Also add cache hits
    for o in _SAM_RESULTS_CACHE:
        if label == "DOJ" and _is_doj(o.agency) and o.notice_id not in seen_ids:
            results.append(o)
        elif label == "DHS" and _is_dhs(o.agency) and o.notice_id not in seen_ids:
            results.append(o)
        elif label == "DoD" and _is_dod(o.agency) and o.notice_id not in seen_ids:
            results.append(o)
    print(f"[{label}] Total: {len(results)}")
    return results


def fetch_doj_opportunities() -> list:
    return _agency_sweep([
        "Department of Justice",
        "Federal Bureau of Investigation",
        "Alcohol, Tobacco, Firearms and Explosives",
        "Drug Enforcement Administration",
        "Bureau of Prisons",
        "U.S. Marshals Service",
        "Court Services and Offender Supervision Agency",
        "Executive Office for United States Attorneys",
        "Office of Justice Programs",
        "Community Oriented Policing Services",
    ], "DOJ")


def fetch_dhs_opportunities() -> list:
    return _agency_sweep([
        "Immigration and Customs Enforcement",
        "Customs and Border Protection",
        "Cybersecurity and Infrastructure Security Agency",
        "Transportation Security Administration",
        "Federal Emergency Management Agency",
        "United States Secret Service",
        "Coast Guard",
    ], "DHS")


def fetch_dod_opportunities() -> list:
    return _agency_sweep([
        "Defense Counterintelligence and Security Agency",
        "Defense Intelligence Agency",
        "National Guard Bureau",
        "Army Research Laboratory",
        "Engineer Research and Development Center",
        "Defense Advanced Research Projects Agency",
    ], "DoD")

# ---------------------------------------------------------------------------
# OTHER SOURCES
# ---------------------------------------------------------------------------
def fetch_federal_register() -> list:
    results, seen = [], set()
    today = datetime.utcnow()
    since = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    terms = ["data analytics", "law enforcement analytics", "IT modernization",
             "artificial intelligence", "digital evidence", "records management",
             "community supervision", "investigative platform"]
    for term in terms:
        try:
            params = (f"conditions[term]={requests.utils.quote(term)}"
                      f"&conditions[publication_date][gte]={since}"
                      f"&conditions[type][]=NOTICE&per_page=10&order=newest"
                      f"&fields[]=document_number&fields[]=title&fields[]=abstract"
                      f"&fields[]=publication_date&fields[]=html_url")
            r = requests.get(
                f"https://www.federalregister.gov/api/v1/documents.json?{params}",
                headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20)
            if r.status_code != 200:
                continue
            for doc in r.json().get("results", []):
                did = doc.get("document_number", "")
                if did in seen:
                    continue
                title    = (doc.get("title") or "").strip()
                abstract = (doc.get("abstract") or "").strip()
                combined = f"{title} {abstract}".lower()
                if not any(s in combined for s in
                           ["request for information", "sources sought",
                            "industry day", "market research"]):
                    continue
                seen.add(did)
                opp = score_opportunity(Opportunity(
                    title=title, notice_id=f"FR-{did}",
                    agency="Federal Register",
                    posted_date=doc.get("publication_date", ""),
                    response_date="TBD",
                    description=abstract[:500],
                    url=clean_url(doc.get("html_url", "")),
                    opp_type="Federal Register RFI",
                    source="Federal Register",
                ))
                results.append(opp)
            time.sleep(0.2)
        except Exception as e:
            print(f"[FederalRegister] '{term}': {e}")
    print(f"[Federal Register] {len(results)} notices")
    return results


def fetch_usaspending_intel() -> list:
    results, seen = [], set()
    today = datetime.utcnow()
    start = (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")
    batches = [["law enforcement software"], ["data analytics"],
               ["community supervision"], ["palantir"], ["corrections software"]]
    for keywords in batches:
        try:
            r = requests.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json={"subawards": False, "limit": 10, "page": 1,
                      "filters": {"keywords": keywords,
                                   "award_type_codes": ["A","B","C","D"],
                                   "time_period": [{"start_date": start, "end_date": end}]},
                      "fields": ["Award ID","Recipient Name","Start Date","End Date",
                                 "Award Amount","Awarding Agency","Description"],
                      "sort": "Award Amount", "order": "desc"},
                headers={**HEADERS, "Content-Type": "application/json"}, timeout=30)
            r.raise_for_status()
            for award in r.json().get("results", []):
                aid = award.get("Award ID", "")
                nid = f"USA-{aid}"
                if nid in seen:
                    continue
                seen.add(nid)
                amount    = award.get("Award Amount", 0) or 0
                recipient = award.get("Recipient Name", "Unknown")
                agency    = award.get("Awarding Agency", "")
                desc      = (award.get("Description", "") or "")[:150]
                opp = Opportunity(
                    title=f"[AWARD] {desc[:80] or 'Contract'} — {recipient}",
                    notice_id=nid, agency=agency,
                    posted_date=award.get("Start Date", end),
                    response_date="Watch for recompete",
                    description=f"Award: ${amount:,.0f} to {recipient}. {desc}",
                    url=clean_url(f"https://www.usaspending.gov/award/{aid}/"),
                    opp_type="Award Intel", source="USASpending.gov",
                )
                opp.score = 0
                opp.tier  = "⚪ Low Fit"
                results.append(opp)
            time.sleep(0.5)
        except Exception as e:
            print(f"[USASpending] {keywords}: {e}")
    print(f"[USASpending] {len(results)} award records")
    return results


def fetch_agency_rss_feeds() -> list:
    return []


def fetch_events_intelligence() -> list:
    return []


def fetch_industry_news() -> list:
    news, seen = [], set()
    feeds = [
        {"url": "https://fedscoop.com/feed/",                    "source": "FedScoop"},
        {"url": "https://www.nextgov.com/rss/all/",              "source": "Nextgov"},
        {"url": "https://gcn.com/rss-feeds/all.aspx",            "source": "GCN"},
        {"url": "https://www.govtech.com/public-safety/rss.xml", "source": "GovTech"},
        {"url": "https://www.police1.com/rss/all/",              "source": "Police1"},
        {"url": "https://www.corrections1.com/rss/all/",         "source": "Corrections1"},
    ]
    keywords = ["law enforcement", "public safety", "data analytics",
                "artificial intelligence", "criminal justice", "corrections",
                "fedramp", "records management", "predictive", "surveillance"]
    for feed in feeds:
        try:
            r = requests.get(feed["url"], headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": "application/rss+xml, text/xml"}, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:15]:
                t  = item.find("title");  d = item.find("description")
                l  = item.find("link");   p = item.find("pubDate")
                title = (t.text or "").strip() if t is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d.text or ""))).strip() if d is not None else ""
                url_  = (l.text or "").strip() if l is not None else ""
                date_ = (p.text or "").strip() if p is not None else ""
                if not title or title in seen:
                    continue
                if not any(kw in f"{title} {desc}".lower() for kw in keywords):
                    continue
                # 7-day filter
                if date_:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub = parsedate_to_datetime(date_).replace(tzinfo=None)
                        if (datetime.utcnow() - pub).days > 7:
                            continue
                    except Exception:
                        continue
                seen.add(title)
                news.append({"title": title, "url": clean_url(url_),
                             "source": feed["source"], "date": date_[:16],
                             "summary": desc[:250]})
            time.sleep(0.2)
        except Exception as e:
            print(f"[IndustryNews] {feed['source']}: {e}")
    print(f"[Industry News] {len(news)} articles")
    return news[:15]


def fetch_growth_news() -> list:
    return []


COMPETITOR_NEWS_QUERIES = [
    ("Palantir",           "Palantir+federal+government+contract"),
    ("Axon",               "Axon+Enterprise+law+enforcement"),
    ("ShotSpotter",        "ShotSpotter+OR+SoundThinking+police"),
    ("Mark43",             "Mark43+records+management+police"),
    ("Tyler Technologies", "Tyler+Technologies+criminal+justice"),
    ("Motorola Solutions", "Motorola+Solutions+law+enforcement"),
    ("IBM i2",             "IBM+i2+intelligence+analytics"),
    ("Esri",               "Esri+law+enforcement+GIS"),
    ("Databricks",         "Databricks+government+federal"),
    ("Appriss",            "Appriss+criminal+justice"),
    ("SuperCom",           "SuperCom+offender+monitoring"),
    ("Flock Safety",       "Flock+Safety+license+plate"),
]

COMPETITORS = [{"name": n} for n, _ in COMPETITOR_NEWS_QUERIES]


def fetch_competitor_intel() -> list:
    items, seen = [], set()
    for comp_name, query in COMPETITOR_NEWS_QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PeregrineScanner/3.0)",
                "Accept": "application/rss+xml"}, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            count = 0
            for item in root.findall(".//item"):
                if count >= 2:
                    break
                t = item.find("title"); l = item.find("link")
                d = item.find("description"); p = item.find("pubDate")
                title = (t.text or "").strip() if t is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>", "", (d.text or ""))).strip() if d is not None else ""
                url_  = (l.text or "").strip() if l is not None else ""
                date_ = (p.text or "").strip() if p is not None else ""
                if not title or title in seen:
                    continue
                # 7-day filter
                if date_:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub = parsedate_to_datetime(date_).replace(tzinfo=None)
                        if (datetime.utcnow() - pub).days > 7:
                            continue
                    except Exception:
                        continue
                seen.add(title)
                items.append({"competitor": comp_name, "title": title,
                              "url": clean_url(url_), "source": "Google News",
                              "date": date_[:16], "summary": desc[:300]})
                count += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[CompIntel] {comp_name}: {e}")
    # USASpending recompetes
    today = datetime.utcnow()
    end_soon = (today + timedelta(days=365)).strftime("%Y-%m-%d")
    for comp_name, keywords in [
        ("Palantir", ["palantir"]),
        ("Axon", ["axon enterprise"]),
        ("Tyler Technologies", ["tyler technologies"]),
        ("Motorola Solutions", ["motorola solutions"]),
    ]:
        try:
            r = requests.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json={"subawards": False, "limit": 5, "page": 1,
                      "filters": {"keywords": keywords,
                                   "award_type_codes": ["A","B","C","D"],
                                   "time_period": [{"start_date": "2020-01-01",
                                                    "end_date": end_soon}]},
                      "fields": ["Award ID","Recipient Name","Start Date","End Date",
                                 "Award Amount","Awarding Agency","Description"]},
                headers={**HEADERS, "Content-Type": "application/json"}, timeout=20)
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
                    urgency = ("🔴" if days_left < 90 else "🟡" if days_left < 180 else "🟢")
                    items.append({
                        "competitor": f"{comp_name} — Recompete Alert",
                        "title": f"{urgency} {comp_name} @ {award.get('Awarding Agency','')} — ${award.get('Award Amount',0):,.0f}",
                        "url": clean_url(f"https://www.usaspending.gov/award/{award.get('Award ID','')}/"),
                        "source": "USASpending.gov",
                        "date": end_str,
                        "summary": f"Contract ends {end_str} ({days_left}d). {(award.get('Description','') or '')[:150]}",
                        "is_recompete": True,
                        "days_left": days_left,
                    })
                except Exception:
                    pass
            time.sleep(0.3)
        except Exception as e:
            print(f"[Recompetes] {comp_name}: {e}")
    print(f"[Competitor Intel] {len(items)} signals")
    return items


def fetch_federal_funding() -> list:
    items, seen = [], set()
    GRANT_EXCLUSIONS = [
        "treatment court", "drug court", "mental health court",
        "substance abuse treatment", "behavioral health",
        "victim services", "victim compensation",
        "domestic abuse refuge", "homeless shelter",
        "food bank", "scholarship", "fellowship",
        "road", "bridge", "wildfire", "flood",
        "healthcare", "dental", "hospital",
        "body armor", "vehicle purchase", "fleet",
        "construction", "grounds maintenance",
    ]
    TECH_SIGNALS = [
        "technology", "software", "data analytics", "data platform",
        "information system", "digital", "analytics platform",
        "records management", "information technology",
    ]
    PROG_SIGNALS = [
        "byrne jag", "edward byrne", "justice assistance",
        "cops office", "second chance act", "justice reinvestment",
        "violence reduction", "community violence intervention",
        "smart policing", "nibin", "data-driven policing",
    ]
    for kw in ["law enforcement technology grant", "crime analytics platform",
               "byrne jag", "cops office technology", "second chance act",
               "data-driven policing", "community supervision technology",
               "offender management system", "digital evidence management"]:
        try:
            r = requests.post(
                "https://apply07.grants.gov/grantsws/rest/opportunities/search/",
                json={"keyword": kw, "oppStatuses": "posted", "rows": 8,
                      "sortBy": "openDate|desc"},
                headers={"Content-Type": "application/json",
                         "User-Agent": HEADERS["User-Agent"]}, timeout=20)
            if r.status_code != 200:
                continue
            for opp in r.json().get("oppHits", []):
                oid = str(opp.get("id", ""))
                if oid in seen:
                    continue
                title    = (opp.get("title", "") or "").strip()
                synopsis = (opp.get("synopsis", "") or "").strip()
                agency   = (opp.get("agencyName", "") or "").strip()
                combined = f"{title} {synopsis}".lower()
                if any(e in combined for e in GRANT_EXCLUSIONS):
                    continue
                if (not any(s in combined for s in TECH_SIGNALS) and
                        not any(p in combined for p in PROG_SIGNALS)):
                    continue
                seen.add(oid)
                items.append({
                    "type":       "🎯 Direct Tech Grant" if kw in ["law enforcement technology grant","crime analytics platform","digital evidence management","offender management system","community supervision technology"] else "💰 Customer Budget Signal",
                    "title":      title, "agency": agency,
                    "summary":    synopsis[:350],
                    "url":        clean_url(f"https://www.grants.gov/search-results-detail/{oid}"),
                    "open_date":  opp.get("openDate", ""),
                    "close_date": opp.get("closeDate", ""),
                    "source":     "grants.gov", "relevance": kw,
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
    print(f"[Federal Funding] {len(deduped)} grants")
    return deduped[:12]


def fetch_agency_budget_news() -> list:
    items, seen = [], set()
    QUERIES = [
        ("DOJ Budget",       "Department+of+Justice+budget+technology+data"),
        ("ATF Technology",   "ATF+technology+data+platform"),
        ("FBI Technology",   "FBI+technology+data+analytics"),
        ("DHS Budget",       "Department+of+Homeland+Security+budget+technology"),
        ("ICE Technology",   "ICE+enforcement+technology+data"),
        ("CISA Budget",      "CISA+cybersecurity+budget+technology"),
        ("Byrne JAG",        "Byrne+JAG+grant+law+enforcement+technology"),
        ("NIBIN Funding",    "NIBIN+crime+gun+intelligence+funding"),
        ("COPS Office",      "COPS+Office+grant+technology+policing"),
    ]
    for label, query in QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PeregrineScanner/3.0)",
                "Accept": "application/rss+xml"}, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            count = 0
            for item in root.findall(".//item"):
                if count >= 2:
                    break
                t = item.find("title"); l = item.find("link")
                d = item.find("description"); p = item.find("pubDate")
                title = (t.text or "").strip() if t is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>","", (d.text or ""))).strip() if d is not None else ""
                url_  = (l.text or "").strip() if l is not None else ""
                date_ = (p.text or "").strip() if p is not None else ""
                if not title or title in seen:
                    continue
                # 7-day filter
                if date_:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub = parsedate_to_datetime(date_).replace(tzinfo=None)
                        if (datetime.utcnow() - pub).days > 7:
                            continue
                    except Exception:
                        continue
                seen.add(title)
                items.append({"label": label, "title": title, "summary": desc[:280],
                              "url": clean_url(url_), "date": date_[:16],
                              "source": "Google News"})
                count += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[BudgetNews] {label}: {e}")
    print(f"[Agency Budget News] {len(items)} signals")
    return items[:20]

# ---------------------------------------------------------------------------
# DEDUP & RANK
# ---------------------------------------------------------------------------
def deduplicate_and_rank(opps: list) -> list:
    seen, out = set(), []
    for o in sorted(opps, key=lambda x: x.score, reverse=True):
        if is_expired(o):
            continue
        key = o.notice_id or o.title[:60].lower()
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out

# ---------------------------------------------------------------------------
# EMAIL RENDERING
# ---------------------------------------------------------------------------
def build_section(title: str, opps: list) -> str:
    if not opps:
        return ""
    rows = ""
    for o in opps[:50]:
        link = (f'<a href="{o.url}" style="font-weight:700;font-size:14px;'
                f'color:#0057b8;text-decoration:none;">{o.title[:120]}</a>'
                if o.url else
                f'<span style="font-weight:700;font-size:14px;color:#333;">'
                f'{o.title[:120]}</span>')
        reasons_html = ""
        if o.score_reasons:
            bullets = "".join(f"<li>{r}</li>" for r in o.score_reasons[:4])
            reasons_html = (f'<ul style="margin:4px 0 0;padding-left:18px;'
                            f'font-size:12px;color:#555;">{bullets}</ul>')
        deadline = ""
        if o.response_date and o.response_date not in ("TBD", ""):
            dt = parse_date_flexible(o.response_date)
            if dt:
                days = (dt - datetime.utcnow()).days
                if 0 <= days <= 7:
                    deadline = (f' <span style="background:#c0392b;color:#fff;'
                                f'font-size:10px;padding:1px 6px;border-radius:8px;">'
                                f'Due in {days}d</span>')
                elif 0 <= days <= 30:
                    deadline = (f' <span style="background:#e67e22;color:#fff;'
                                f'font-size:10px;padding:1px 6px;border-radius:8px;">'
                                f'Due in {days}d</span>')
        rows += (
            '<div style="border:1px solid #e8e8e8;border-radius:6px;padding:12px;'
            'margin-bottom:10px;background:#fff;">'
            f'<div style="margin-bottom:6px;">{link}{deadline}</div>'
            f'<div style="font-size:12px;color:#666;">🏛 {o.agency[:80]}'
            f' &nbsp;·&nbsp; 📬 {o.posted_date[:10]}</div>'
            f'<div style="font-size:11px;color:#999;margin-top:2px;">'
            f'Source: {o.source} &nbsp;·&nbsp; Score: {o.score}pts'
            f' &nbsp;·&nbsp; <a href="{o.url}" style="color:#0057b8;">View</a>'
            f'</div>{reasons_html}</div>'
        )
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">{title} ({len(opps)})</h2>{rows}</div>')


def build_award_intel_section(awards: list) -> str:
    if not awards:
        return ""
    rows = ""
    for o in awards[:5]:
        rows += (f'<div style="border-left:3px solid #95a5a6;padding:8px 10px;'
                 f'margin-bottom:8px;background:#f9f9f9;">'
                 f'<div style="font-size:13px;font-weight:600;color:#333;">'
                 f'{o.title[:100]}</div>'
                 f'<div style="font-size:11px;color:#888;">'
                 f'{o.agency[:70]} · {o.posted_date[:10]}</div></div>')
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">📊 Award Intel — Recent Contract Wins</h2>'
            f'{rows}</div>')


def _grant_why_it_fits(item: dict) -> list:
    text  = f" {item.get('title','')} {item.get('summary','')} {item.get('relevance','')} ".lower()
    rtype = (item.get('type', '') or "").lower()
    reasons = []
    if "direct tech" in rtype:
        reasons.append(("🎯", "Direct Technology Grant", "Funding for software/data platform procurement"))
    else:
        reasons.append(("💰", "Customer Budget Signal", "Funding flowing to agencies that buy Peregrine"))
    checks = [
        (("data analytics","data platform","information sharing"),"⬡","Data Integration","Peregrine unifies data from RMS, CAD, jail, court"),
        (("investigative","crime analytics","digital evidence","intelligence platform"),"◎","Investigative Analytics","Peregrine surfaces patterns for investigators"),
        (("community supervision","probation","offender management","corrections"),"⬡","Corrections & Supervision","Peregrine deployed at CSOSA"),
        (("law enforcement","police","fusion center","records management"),"⬟","Public Safety","Direct LE agency funding"),
        (("byrne jag","bjag","edward byrne","justice assistance"),"💵","Byrne JAG","Most flexible LE grant — agencies use for analytics"),
        (("cops office","community oriented policing"),"👮","COPS Office","COPS grants fund technology and data systems"),
        (("violence reduction","gun violence","nibin"),"🎯","Violence Reduction","Funds NIBIN/analytics platforms Peregrine provides"),
    ]
    for terms, icon, cname, desc in checks:
        if any(t in text for t in terms):
            reasons.append((icon, cname, desc))
    seen, deduped = set(), []
    for r in reasons:
        if r[1] not in seen:
            seen.add(r[1])
            deduped.append(r)
    return deduped[:4]


def build_funding_section(funding_items: list) -> str:
    if not funding_items:
        return ""
    rows = ""
    for item in funding_items[:12]:
        badge_color = "#27ae60" if "Direct" in item["type"] else "#0057b8"
        badge_text  = item["type"].replace("🎯 ","").replace("💰 ","")
        link = (f'<a href="{item["url"]}" style="font-weight:700;font-size:14px;'
                f'color:#0057b8;text-decoration:none;">{item["title"][:110]}</a>'
                if item.get("url") else
                f'<span style="font-weight:700;font-size:14px;color:#333;">'
                f'{item["title"][:110]}</span>')
        reasons = _grant_why_it_fits(item)
        why_html = ""
        if reasons:
            bullets = "".join(f'<li><strong>{i} {l}:</strong> {d}</li>'
                              for i,l,d in reasons)
            why_html = (f'<div style="margin-top:8px;padding:8px;background:#f8fafe;'
                        f'border-left:3px solid #0057b8;border-radius:0 4px 4px 0;">'
                        f'<div style="font-size:11px;font-weight:700;color:#0057b8;'
                        f'margin-bottom:4px;text-transform:uppercase;">Why It Fits</div>'
                        f'<ul style="margin:0;padding-left:16px;font-size:12px;'
                        f'line-height:1.6;">{bullets}</ul></div>')
        rows += (
            f'<div style="border:1px solid #e8e8e8;border-radius:6px;padding:12px;'
            f'margin-bottom:10px;background:#fff;">'
            f'<div style="margin-bottom:6px;">'
            f'<span style="background:{badge_color};color:#fff;font-size:10px;'
            f'font-weight:700;padding:2px 7px;border-radius:10px;">{badge_text}</span>'
            f'<span style="font-size:11px;color:#888;margin-left:8px;">'
            f'{item["source"]} · {item.get("open_date","")[:10]}</span></div>'
            f'<div style="margin-bottom:4px;">{link}</div>'
            f'<div style="font-size:12px;color:#666;">🏛 {item["agency"][:80]}'
            + (f' · Closes: {item["close_date"]}' if item.get("close_date") else "")
            + f'</div>'
            + (f'<div style="font-size:12px;color:#555;margin-top:4px;">'
               f'{item.get("summary","")[:280]}</div>' if item.get("summary") else "")
            + why_html + '</div>'
        )
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">💰 Federal Funding — Last 10 Days ({len(funding_items)})</h2>'
            f'<p style="font-size:12px;color:#888;margin:0 0 10px;">'
            f'Direct tech grants · Customer budget signals</p>{rows}</div>')


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
            link = (f'<a href="{s["url"]}" style="color:#0057b8;text-decoration:none;'
                    f'font-weight:600;">{s["title"][:95]}</a>'
                    if s.get("url") else
                    f'<span style="font-weight:600;">{s["title"][:95]}</span>')
            rows += (f'<div style="margin-bottom:8px;padding-bottom:8px;'
                     f'border-bottom:1px solid #f0f0f0;">'
                     f'<div style="font-size:12px;font-weight:700;color:#555;'
                     f'margin-bottom:2px;">📡 {label}</div>'
                     f'<div style="font-size:13px;">{link}</div>'
                     f'<div style="font-size:11px;color:#888;">'
                     f'{s["source"]} · {s["date"][:10]}</div>'
                     + (f'<div style="font-size:12px;color:#555;margin-top:2px;">'
                        f'{s.get("summary","")[:180]}</div>' if s.get("summary") else "")
                     + '</div>')
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">📡 Agency Budget & Spending Signals ({len(budget_news)})</h2>'
            f'{rows}</div>')


def build_competitor_section(intel_items: list, growth_items: list = None) -> str:
    if not intel_items:
        return ""
    palantir_rc = sorted([i for i in intel_items if i.get("is_recompete") and "Palantir" in i.get("competitor","")], key=lambda x: x.get("days_left",999))
    other_rc    = sorted([i for i in intel_items if i.get("is_recompete") and "Palantir" not in i.get("competitor","")], key=lambda x: x.get("days_left",999))
    news_stories = [i for i in intel_items if not i.get("is_recompete")]

    def _rc_rows(rcs):
        rows = ""
        for rc in rcs[:6]:
            link = (f'<a href="{rc["url"]}" style="font-weight:700;color:#c0392b;'
                    f'text-decoration:none;">{rc["title"][:120]}</a>'
                    if rc.get("url") else
                    f'<span style="font-weight:700;color:#c0392b;">{rc["title"][:120]}</span>')
            rows += (f'<div style="border-left:3px solid #c0392b;padding:8px 10px;'
                     f'margin-bottom:8px;background:#fff9f9;">'
                     f'<div style="font-size:13px;">{link}</div>'
                     f'<div style="font-size:11px;color:#888;">Expires: {rc["date"]} · {rc["source"]}</div>'
                     + (f'<div style="font-size:12px;color:#555;margin-top:2px;">'
                        f'{rc.get("summary","")[:200]}</div>' if rc.get("summary") else "")
                     + '</div>')
        return rows

    palantir_html = ""
    if palantir_rc:
        palantir_html = (f'<div style="margin-bottom:16px;border:1px solid #f5c6cb;'
                         f'border-radius:8px;padding:14px;background:#fff9f9;">'
                         f'<div style="font-weight:700;font-size:13px;color:#c0392b;'
                         f'margin-bottom:8px;">🎯 Palantir Recompetes ({len(palantir_rc)})</div>'
                         f'{_rc_rows(palantir_rc)}</div>')
    other_rc_html = ""
    if other_rc:
        other_rc_html = (f'<div style="margin-bottom:16px;">'
                         f'<div style="font-weight:700;font-size:13px;color:#e67e22;'
                         f'margin-bottom:8px;">⚡ Other Competitor Recompetes ({len(other_rc)})</div>'
                         f'{_rc_rows(other_rc)}</div>')

    from collections import defaultdict
    grouped = defaultdict(list)
    for item in news_stories:
        grouped[item["competitor"]].append(item)
    news_rows = ""
    for comp in sorted(grouped.keys()):
        stories = grouped[comp][:2]
        sh = ""
        for s in stories:
            link = (f'<a href="{s["url"]}" style="color:#0057b8;text-decoration:none;'
                    f'font-weight:600;">{s["title"][:90]}</a>'
                    if s.get("url") else
                    f'<span style="font-weight:600;">{s["title"][:90]}</span>')
            sh += (f'<div style="margin-bottom:8px;padding-bottom:8px;'
                   f'border-bottom:1px solid #f0f0f0;">'
                   f'<div style="font-size:13px;">{link}</div>'
                   f'<div style="font-size:11px;color:#888;">'
                   f'{s["source"]} · {s["date"][:10]}</div>'
                   + (f'<div style="font-size:12px;color:#555;margin-top:2px;">'
                      f'{s.get("summary","")[:200]}</div>' if s.get("summary") else "")
                   + '</div>')
        news_rows += (f'<div style="margin-bottom:14px;">'
                      f'<div style="font-weight:700;font-size:12px;color:#555;'
                      f'margin-bottom:6px;text-transform:uppercase;">⚔️ {comp}</div>'
                      f'{sh}</div>')

    monitoring = ", ".join(c["name"] for c in COMPETITORS)
    news_rows_fallback = news_rows or "<p style='color:#aaa;font-size:13px;'>No competitor news this week.</p>"
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">&#x1F50E; Competitor Intelligence</h2>'
            f'<p style="font-size:12px;color:#888;margin:0 0 12px;">'
            f'Monitoring: {monitoring}</p>'
            f'{palantir_html}{other_rc_html}'
            f'{news_rows_fallback}'
            f'</div>')


def build_news_section(news_items: list) -> str:
    if not news_items:
        return ""
    rows = ""
    for item in news_items[:12]:
        link = (f'<a href="{item["url"]}" style="color:#0057b8;text-decoration:none;'
                f'font-weight:600;">{item["title"][:100]}</a>'
                if item.get("url") else
                f'<span style="font-weight:600;">{item["title"][:100]}</span>')
        rows += (f'<div style="margin-bottom:10px;padding-bottom:10px;'
                 f'border-bottom:1px solid #f0f0f0;">'
                 f'<div style="font-size:13px;">{link}</div>'
                 f'<div style="font-size:11px;color:#888;">'
                 f'{item["source"]} · {item["date"][:10]}</div>'
                 + (f'<div style="font-size:12px;color:#555;margin-top:2px;">'
                    f'{item.get("summary","")[:200]}</div>' if item.get("summary") else "")
                 + '</div>')
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">📰 Industry News ({len(news_items)})</h2>'
            f'{rows}</div>')


def _possible_fits(non_events: list, tiers: dict, shown: set = None) -> list:
    shown = shown or set()
    def _k(o): return (o.notice_id or o.title[:60].lower()).strip()
    possible = [o for o in tiers.get("possible", [])
                if _k(o) not in shown and o.source != "Events Intelligence"]
    if possible:
        return possible
    low = sorted([o for o in non_events
                  if o.tier == "⚪ Low Fit" and o.score > 0 and _k(o) not in shown],
                 key=lambda x: x.score, reverse=True)
    return low[:10]


def build_industry_days_section(opps: list) -> str:
    """Separate section: ANY opportunity with 'industry day' in the title."""
    days = sorted(
        [o for o in opps
         if "industry day" in o.title.lower()
         and o.source != "Events Intelligence"],
        key=lambda x: x.posted_date, reverse=True
    )
    if not days:
        return ""
    rows = ""
    for o in days[:30]:
        link = (f'<a href="{o.url}" style="font-weight:700;font-size:14px;'
                f'color:#1a5276;text-decoration:none;">{o.title[:120]}</a>'
                if o.url else
                f'<span style="font-weight:700;font-size:14px;color:#1a5276;">'
                f'{o.title[:120]}</span>')
        deadline = ""
        if o.response_date and o.response_date not in ("TBD", ""):
            dt = parse_date_flexible(o.response_date)
            if dt:
                d = (dt - datetime.utcnow()).days
                if 0 <= d <= 7:
                    deadline = (f' <span style="background:#c0392b;color:#fff;'
                                f'font-size:10px;padding:1px 6px;border-radius:8px;">'
                                f'Due in {d}d</span>')
                elif 0 <= d <= 30:
                    deadline = (f' <span style="background:#e67e22;color:#fff;'
                                f'font-size:10px;padding:1px 6px;border-radius:8px;">'
                                f'Due in {d}d</span>')
        rows += (
            '<div style="border:1px solid #d6eaf8;border-radius:6px;padding:12px;'
            'margin-bottom:8px;background:#eaf4fb;">'
            f'<div style="margin-bottom:4px;">{link}{deadline}</div>'
            f'<div style="font-size:12px;color:#555;">🏛 {o.agency[:80]}'
            f' &nbsp;·&nbsp; 📬 {o.posted_date[:10]}</div>'
            f'<div style="font-size:11px;color:#888;margin-top:2px;">'
            f'Source: {o.source} &nbsp;·&nbsp; '
            f'<a href="{o.url}" style="color:#1a5276;">View on SAM.gov</a></div>'
            '</div>'
        )
    return (
        '<div style="margin:20px 0 6px">'
        f'<h2 style="font-size:16px;color:#111;border-bottom:2px solid #d6eaf8;'
        f'padding-bottom:5px;">📣 Industry Days ({len(days)})</h2>'
        '<p style="font-size:12px;color:#888;margin:0 0 10px;">'
        'All federal agency Industry Day notices — precede formal solicitations.</p>'
        f'{rows}</div>'
    )


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

    def _k(o): return (o.notice_id or o.title[:60].lower()).strip()
    def _dedup(lst):
        seen, out = set(), []
        for o in lst:
            k = _k(o)
            if k not in seen: seen.add(k); out.append(o)
        return out

    shown = set()
    strong_list   = _dedup([o for o in non_events if "Strong"   in o.tier])
    shown.update(_k(o) for o in strong_list)
    good_list     = _dedup([o for o in non_events if "Good"     in o.tier and _k(o) not in shown])
    shown.update(_k(o) for o in good_list)
    possible_list = _dedup([o for o in non_events if "Possible" in o.tier and _k(o) not in shown])
    shown.update(_k(o) for o in possible_list)
    low_fit_list  = _dedup([o for o in non_events if o.tier == "⚪ Low Fit" and o.score > 0 and _k(o) not in shown])
    shown.update(_k(o) for o in low_fit_list)
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
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:'Helvetica Neue',Arial,sans-serif;background:#f5f5f5;margin:0;padding:0}}
.wrap{{max-width:720px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden}}
.hdr{{background:#0057b8;padding:24px 28px;color:#fff}}
.content{{padding:20px 28px}}
</style></head><body>
<div class="wrap">
<div class="hdr">
  <div style="font-size:22px;font-weight:700;">🦅 Peregrine Daily Scanner</div>
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
  {build_industry_days_section(opps)}
</div>
</div></body></html>"""


def send_email(html: str, subject: str):
    print(f"[Email] To:{EMAIL_TO} From:{EMAIL_FROM} Key:{'SET' if SENDGRID_API_KEY else 'NOT SET'}")
    if not all([SENDGRID_API_KEY, EMAIL_TO, EMAIL_FROM]):
        print("[Email] SKIPPED — missing config")
        return
    try:
        r = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {SENDGRID_API_KEY}",
                     "Content-Type": "application/json"},
            json={"personalizations": [{"to": [{"email": EMAIL_TO}]}],
                  "from": {"email": EMAIL_FROM, "name": "Peregrine Federal Scanner"},
                  "subject": subject,
                  "content": [{"type": "text/html", "value": html}]},
            timeout=30,
        )
        if r.status_code in (200, 202):
            print(f"[Email] ✓ Sent (HTTP {r.status_code})")
        else:
            print(f"[Email] ✗ Failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[Email] Error: {e}")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    today    = datetime.utcnow()
    run_date = today.strftime("%B %d, %Y")
    sep = "=" * 60
    print(f"\n{sep}\n  Peregrine Daily Scanner -- {run_date}\n{sep}")

    source_counts = {}
    all_opps      = []

    sources = [
        ("SAM.gov",          fetch_sam_gov),
        ("DOJ",              fetch_doj_opportunities),
        ("DHS",              fetch_dhs_opportunities),
        ("DoD",              fetch_dod_opportunities),
        ("Federal Register", fetch_federal_register),
        ("USASpending.gov",  fetch_usaspending_intel),
        ("Agency RSS",       fetch_agency_rss_feeds),
        ("Events",           fetch_events_intelligence),
    ]
    for label, fn in sources:
        print(f"\n[{label}] Fetching...")
        try:
            batch = fn()
            source_counts[label] = len(batch)
            all_opps.extend(batch)
            if label == "SAM.gov":
                _SAM_RESULTS_CACHE.clear()
                _SAM_RESULTS_CACHE.extend(batch)
        except Exception as e:
            print(f"[{label}] FAILED: {e}")
            source_counts[label] = 0

    print(f"\n[Scoring] Deduplicating {len(all_opps)} raw opportunities...")
    ranked   = deduplicate_and_rank(all_opps)
    strong   = sum(1 for o in ranked if "Strong"   in o.tier)
    good     = sum(1 for o in ranked if "Good"     in o.tier)
    possible = sum(1 for o in ranked if "Possible" in o.tier)
    print(f"[Tiers] 🟢 {strong} Strong  🟡 {good} Good  🔵 {possible} Possible")

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

    if strong == 0 and good == 0:
        subject = f"Peregrine Daily Scanner | No Strong Matches | {today.strftime('%b %d')}"
    elif strong >= 1:
        subject = f"Peregrine Daily Scanner | {strong} Strong · {good} Good · {possible} Possible | {today.strftime('%b %d')}"
    else:
        subject = f"Peregrine Daily Scanner | {good} Good · {possible} Possible | {today.strftime('%b %d')}"

    html = build_html_email(
        ranked, run_date, source_counts,
        news_items=news_items,
        competitor_items=competitor_items,
        growth_items=growth_items,
        funding_items=funding_items,
        budget_news=budget_news,
    )
    print(f"\n[Email] HTML: {len(html):,} chars | Subject: {subject}")
    send_email(html, subject)

    fname = f"digest_{today.strftime('%Y%m%d')}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[Done] Saved: {fname}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print(f"[FATAL ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        raise
