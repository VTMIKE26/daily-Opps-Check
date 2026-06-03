#!/usr/bin/env python3
"""Peregrine Daily Federal Opportunity Scanner"""
import os, re, time, json, xml.etree.ElementTree as ET
from html import unescape
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import requests

SAM_API_KEY      = os.environ.get("SAM_API_KEY", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_TO         = os.environ.get("EMAIL_TO", "mike.kelly@peregrine.io")
EMAIL_FROM       = os.environ.get("EMAIL_FROM", "mikefkelly26@gmail.com")

print(f"[Config] SAM_API_KEY set:      {'YES' if SAM_API_KEY else 'NO'}")
print(f"[Config] SENDGRID_API_KEY set: {'YES' if SENDGRID_API_KEY else 'NO'}")
print(f"[Config] EMAIL_TO:             {EMAIL_TO}")
print(f"[Config] EMAIL_FROM:           {EMAIL_FROM}")

HEADERS = {"User-Agent": "PeregrineScanner/3.0", "Accept": "application/json"}

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
    naics:         str  = ""
    score:         int  = 0
    tier:          str  = ""
    score_reasons: list = field(default_factory=list)

CAPABILITY_CLUSTERS = [
    ("Data Integration & Unification", 20, [
        "data integration", "data unification", "data fusion", "data silos",
        "data harmonization", "enterprise data platform", "data consolidation",
        "data normalization", "data pipeline", "data fabric", "data lake",
        "data warehouse", "data analytics", "analytics platform",
        "data management platform", "data platform", "analytics solution",
        "information sharing", "master data", "unified data",
        "data integration platform",
    ]),
    ("Investigative & Operational Analytics", 20, [
        "investigative analytics", "investigative platform",
        "link analysis", "relationship mapping", "situational awareness",
        "operational intelligence", "crime analytics", "crime analysis",
        "intelligence platform", "predictive analytics",
        "geospatial analysis", "digital evidence",
        "evidence management platform", "digital forensics platform",
    ]),
    ("Federated & Enterprise Search", 20, [
        "federated search", "enterprise search", "cross-system search",
        "unified search", "information retrieval",
    ]),
    ("Entity Resolution & Record Intelligence", 20, [
        "entity resolution", "record deduplication", "record linkage",
        "identity resolution", "identity matching", "record matching",
        "data deduplication", "entity matching", "knowledge graph",
        "fuzzy matching", "probabilistic matching", "master person index",
    ]),
    ("Secure Government SaaS", 15, [
        "fedramp", "govcloud", "zero trust", "cyber essentials",
        "iso 27001", "secure cloud", "government cloud",
        "identity and access management", "il2", "il3", "il4",
        "cjis compliant", "audit logging",
    ]),
    ("Public Safety & Law Enforcement", 20, [
        "law enforcement platform", "law enforcement analytics",
        "law enforcement data", "law enforcement technology",
        "police data", "police analytics", "policing platform",
        "public safety platform", "public safety analytics",
        "records management system", "crime recording system",
        "custody management", "computer aided dispatch", "cad system",
        "fusion center", "fusion centre", "digital forensics",
        "body worn video", "body worn camera",
        "automatic number plate recognition", "anpr",
        "police national database",
        "decentralized information sharing",
        "information sharing environment",
    ]),
    ("Corrections & Community Supervision", 20, [
        "community supervision", "probation", "parole",
        "offender management", "prison management",
        "case management system", "hmpps", "hmps",
        "youth offending", "electronic monitoring", "electronic tagging",
        "offender data", "reoffending",
    ]),
    ("Platform Modernization & Replacement", 20, [
        "platform replacement", "legacy platform", "legacy system",
        "legacy modernization", "platform modernization",
        "it modernization", "digital transformation", "cloud migration",
        "system replacement", "palantir", "niche", "xhibit",
        "commercial solutions opening", "sovereign cloud", "defense cloud",
    ]),
    ("AI & Machine Learning", 22, [
        "artificial intelligence", "machine learning",
        "ai platform", "ai solution", "ai system",
        " generative ai", "generative ai ",
        "large language model", " llm ",
        "natural language processing", " nlp ",
        "computer vision", "predictive model",
        "decision support", "automated analysis",
        "ai-powered", "ai-driven", "responsible ai",
    ]),
]

HARD_EXCLUSIONS = [
    "fire suppression", "fire alarm", "hvac", "plumbing",
    "electrical installation", "roof replacement", "flooring",
    "lift maintenance", "elevator maintenance",
    "uniform supply", "stationery", "office furniture", "catering",
    "food supply", "medical supplies", "pharmaceutical", "laundry",
    "body armour", "body armor", "taser", "firearms", "ammunition",
    "vehicle purchase", "vehicle fleet", "fleet procurement",
    "fleet management", "radio procurement",
    "construction works", "refurbishment", "building works",
    "grounds maintenance", "grass cutting", "hedge cutting",
    "landscaping", "cleaning contract", "janitorial", "waste management",
    "network cabling", "structured cabling", "fibre optic installation",
    "mobile phone contract", "telephony system",
    "hardware maintenance contract", "printer maintenance",
    "copier maintenance", "mfd maintenance",
    "microsoft licence renewal", "oracle licence renewal",
    "software licence renewal",
    "staffing agency", "temporary staffing", "security guard services",
    "drug treatment", "alcohol treatment", "substance misuse",
    "domestic abuse refuge", "homeless shelter",
    "firearms training", "first aid training", "physical training contract",
    "avionics", "missile", "munitions", "weapons system", "naval vessel",
]

TIER_STRONG = 40
TIER_GOOD   = 15

NAICS_HINTS = {
    "541": "software IT services data analytics platform",
    "5415": "computer systems design data integration analytics platform",
    "518":  "cloud computing data processing hosting",
    "922":  "law enforcement criminal justice public safety records",
    "9221": "police law enforcement records analytics platform",
}

_SAM_RATE_LIMITED = [False]
_SAM_CACHE: list  = []


def parse_date(s: str) -> Optional[datetime]:
    if not s or s == "TBD":
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            pass
    return None


def is_expired(opp: Opportunity) -> bool:
    dt = parse_date(opp.response_date)
    return bool(dt and dt < datetime.utcnow() - timedelta(days=2))


def clean_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    return url


def score_opportunity(opp: Opportunity) -> Opportunity:
    naics_hint = ""
    for prefix, hint in NAICS_HINTS.items():
        if opp.naics and opp.naics.startswith(prefix):
            naics_hint = hint
            break

    text = f" {opp.title} {opp.description} {naics_hint} ".lower()

    for excl in HARD_EXCLUSIONS:
        if excl in text:
            opp.score = 0
            opp.tier  = "Excluded"
            opp.score_reasons = [f"Excluded: '{excl}'"]
            return opp

    total, reasons = 0, []
    for cname, pts, phrases in CAPABILITY_CLUSTERS:
        m = next((p for p in phrases if p in text), None)
        if m:
            total += pts
            reasons.append(f"+ {cname}: '{m}'")

    if total == 0:
        ENGAGEMENT = [
            "industry day", "vendor day", "market survey",
            "sources sought", "request for information",
            "broad agency announcement", "baa",
            "commercial solutions opening", "pre-solicitation",
            "market research", "notice of intent", "special notice",
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
        is_eng = any(s in full_text for s in ENGAGEMENT)
        is_t1  = any(a in opp.agency.lower() for a in TIER1)
        if is_eng and is_t1:
            total = 15
            reasons = ["+ Engagement event — Tier 1 LE/Security agency: watch for follow-on RFP"]
        elif is_eng:
            total = 5
            reasons = ["+ Engagement event — Federal agency"]

    opp.score = total
    opp.score_reasons = reasons
    opp.tier = (
        "Strong" if total >= TIER_STRONG else
        "Good"   if total >= TIER_GOOD   else
        "Possible" if total > 0 else
        "Low"
    )
    return opp


def _sam_search(params: dict, label: str, seen: set, results: list, pages: int = 1) -> bool:
    if _SAM_RATE_LIMITED[0]:
        return False
    for page in range(pages):
        if _SAM_RATE_LIMITED[0]:
            break
        try:
            p = {"api_key": SAM_API_KEY, "limit": 100, "offset": page * 100, **params}
            r = requests.get("https://api.sam.gov/opportunities/v2/search",
                             params=p, headers=HEADERS, timeout=20)
            if r.status_code == 429:
                print(f"[SAM.gov] 429 Rate limited on {label}")
                _SAM_RATE_LIMITED[0] = True
                return False
            if r.status_code != 200:
                print(f"[SAM.gov] HTTP {r.status_code} on {label}: {r.text[:200]}")
                return True
            data  = r.json()
            total = data.get("totalRecords", 0)
            items = data.get("opportunitiesData", [])
            if page == 0:
                print(f"[SAM.gov] {label}: totalRecords={total}, returned={len(items)}")
            new_n = 0
            for item in items:
                nid = item.get("noticeId") or item.get("id") or ""
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                new_n += 1
                results.append(score_opportunity(Opportunity(
                    title         = item.get("title", "Untitled"),
                    notice_id     = nid,
                    agency        = (item.get("fullParentPathName")
                                     or item.get("departmentName") or "Unknown"),
                    posted_date   = item.get("postedDate", ""),
                    response_date = (item.get("responseDeadLine")
                                     or item.get("reponseDeadLine", "TBD")),
                    description   = (item.get("description") or "")[:400],
                    url           = clean_url(f"https://sam.gov/opp/{nid}/view"),
                    opp_type      = item.get("type") or "Notice",
                    source        = "SAM.gov",
                    naics         = item.get("naicsCode", ""),
                )))
            if new_n:
                print(f"[SAM.gov] {label}: {data.get('totalRecords',0)} total, {new_n} new")
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
    results, seen = [], set()
    today   = datetime.utcnow()
    to_date = today.strftime("%m/%d/%Y")
    d30     = (today - timedelta(days=30)).strftime("%m/%d/%Y")
    d90     = (today - timedelta(days=90)).strftime("%m/%d/%Y")

    # Pass 1: ptype sweeps
    for ptype, lbl in [("r","Sources Sought"),("p","Presolicitation"),
                        ("k","Combined Synopsis"),("s","Special Notice"),
                        ("o","Solicitation"),("i","Intent to Bundle")]:
        if not _sam_search({"ptype": ptype, "postedFrom": d30, "postedTo": to_date},
                           lbl, seen, results, pages=4):
            break

    # Pass 2: keyword searches
    for term, pages in [
        ("data management solutions", 1), ("sovereign cloud", 1),
        ("commercial solutions opening", 1), ("federated search", 1),
        ("investigative analytics", 1), ("law enforcement analytics", 1),
        ("offender management system", 1), ("community supervision", 1),
        ("intelligence platform", 1), ("records management system", 1),
        ("industry day", 10), ("sources sought data", 1),
        ("broad agency announcement", 3),
    ]:
        if _SAM_RATE_LIMITED[0]: break
        _sam_search({"keyword": term, "postedFrom": d90, "postedTo": to_date},
                    f"kw={term}", seen, results, pages=pages)

    # Pass 3: title searches
    for term, pages in [
        ("data analytics", 3), ("data integration", 3),
        ("artificial intelligence", 3), ("machine learning", 2),
        ("platform modernization", 2), ("digital transformation", 2),
        ("data management", 3), ("investigative platform", 1),
        ("digital evidence", 1), ("crime analytics", 1),
        ("entity resolution", 1), ("fusion center", 1),
        ("public safety platform", 1), ("predictive analytics", 2),
        ("enterprise data", 1), ("community supervision", 1),
    ]:
        if _SAM_RATE_LIMITED[0]: break
        _sam_search({"title": term, "postedFrom": d90, "postedTo": to_date},
                    f"title={term}", seen, results, pages=pages)

    # Pass 4: watchlist
    today_str = today.strftime("%m/%d/%Y")
    for nid in [
        "b2910bda98f342149cd76c39de3038c6",
        "55c0c5ea5ef84232869c0134386dfa48",
        "d32237c586bc45489644f757c52faa22",
        "7ca078ff27e24fd2b06a1553bbeadc59",
        "70e476afd4584a63a9890f0071e4871e",
    ]:
        if _SAM_RATE_LIMITED[0]: break
        try:
            r = requests.get("https://api.sam.gov/opportunities/v2/search",
                             params={"api_key": SAM_API_KEY, "noticeid": nid, "limit": 5},
                             headers=HEADERS, timeout=15)
            if r.status_code == 200:
                for item in r.json().get("opportunitiesData", []):
                    item_nid = item.get("noticeId") or item.get("id") or ""
                    if not item_nid or item_nid in seen:
                        continue
                    seen.add(item_nid)
                    opp = score_opportunity(Opportunity(
                        title=item.get("title","Untitled"), notice_id=item_nid,
                        agency=item.get("fullParentPathName","Unknown"),
                        posted_date=item.get("postedDate",""),
                        response_date=item.get("responseDeadLine","TBD"),
                        description=(item.get("description") or "")[:400],
                        url=clean_url(f"https://sam.gov/opp/{item_nid}/view"),
                        opp_type=item.get("type","Notice"),
                        source="SAM.gov", naics=item.get("naicsCode",""),
                    ))
                    results.append(opp)
                    print(f"[SAM.gov] Watch: {item.get('title','?')[:60]} score={opp.score}")
        except Exception as e:
            print(f"[SAM.gov] Watch {nid[:8]}: {e}")

    _SAM_CACHE.clear()
    _SAM_CACHE.extend(results)
    print(f"[SAM.gov] Total: {len(results)}")
    return results


def _agency_sweep(agencies: list, label: str,
                  frag_check) -> list:
    if not SAM_API_KEY or _SAM_RATE_LIMITED[0]:
        cached = [o for o in _SAM_CACHE if frag_check(o.agency)]
        print(f"[{label}] {len(cached)} from cache")
        return cached
    results, seen = [], set(o.notice_id for o in _SAM_CACHE)
    today   = datetime.utcnow()
    d90     = (today - timedelta(days=90)).strftime("%m/%d/%Y")
    to_date = today.strftime("%m/%d/%Y")
    for agency in agencies:
        if _SAM_RATE_LIMITED[0]: break
        new_n = 0
        for page in range(3):
            if _SAM_RATE_LIMITED[0]: break
            try:
                r = requests.get(
                    "https://api.sam.gov/opportunities/v2/search",
                    params={"api_key": SAM_API_KEY, "organizationName": agency,
                            "postedFrom": d90, "postedTo": to_date,
                            "limit": 100, "offset": page * 100},
                    headers=HEADERS, timeout=20)
                if r.status_code == 429:
                    _SAM_RATE_LIMITED[0] = True; break
                if r.status_code != 200: break
                items = r.json().get("opportunitiesData", [])
                for item in items:
                    nid = item.get("noticeId") or item.get("id") or ""
                    if not nid or nid in seen: continue
                    seen.add(nid); new_n += 1
                    results.append(score_opportunity(Opportunity(
                        title=item.get("title","Untitled"), notice_id=nid,
                        agency=item.get("fullParentPathName", agency),
                        posted_date=item.get("postedDate",""),
                        response_date=item.get("responseDeadLine","TBD"),
                        description=(item.get("description") or "")[:400],
                        url=clean_url(f"https://sam.gov/opp/{nid}/view"),
                        opp_type=item.get("type","Notice"),
                        source="SAM.gov", naics=item.get("naicsCode",""),
                    )))
                if len(items) < 100: break
                time.sleep(0.2)
            except Exception as e:
                print(f"[{label}] {agency[:25]}: {e}"); break
        if new_n: print(f"[{label}] {agency[:35]}: {new_n}")
        time.sleep(0.2)
    for o in _SAM_CACHE:
        if frag_check(o.agency) and o.notice_id not in seen:
            results.append(o)
    print(f"[{label}] Total: {len(results)}")
    return results


DOJ_FRAGS = ["department of justice","federal bureau","fbi","atf",
             "alcohol, tobacco","drug enforcement","bureau of prisons",
             "marshals","court services","csosa","eousa",
             "office of justice programs","community oriented policing"]
DHS_FRAGS = ["homeland security","immigration and customs","customs and border",
             "cybersecurity and infrastructure","cisa","secret service",
             "federal emergency management","transportation security",
             "coast guard","citizenship and immigration"]
DOD_FRAGS = ["dept of defense","department of defense","army","navy",
             "air force","marine corps","space force","national guard",
             "defense intelligence","dcsa","darpa","erdc",
             "army research","defense advanced"]

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
    ], "DOJ", lambda a: any(f in a.lower() for f in DOJ_FRAGS))

def fetch_dhs_opportunities() -> list:
    return _agency_sweep([
        "Immigration and Customs Enforcement",
        "Customs and Border Protection",
        "Cybersecurity and Infrastructure Security Agency",
        "Transportation Security Administration",
        "Federal Emergency Management Agency",
        "United States Secret Service",
    ], "DHS", lambda a: any(f in a.lower() for f in DHS_FRAGS))

def fetch_dod_opportunities() -> list:
    return _agency_sweep([
        "Defense Counterintelligence and Security Agency",
        "Defense Intelligence Agency",
        "Army Research Laboratory",
        "Engineer Research and Development Center",
        "Defense Advanced Research Projects Agency",
    ], "DoD", lambda a: any(f in a.lower() for f in DOD_FRAGS))


def fetch_competitor_intel() -> list:
    items, seen = [], set()
    QUERIES = [
        ("Palantir",           "Palantir+federal+government+law+enforcement"),
        ("Axon",               "Axon+Enterprise+law+enforcement+police"),
        ("ShotSpotter",        "ShotSpotter+OR+SoundThinking+police+gunshot"),
        ("Mark43",             "Mark43+records+management+police"),
        ("Tyler Technologies", "Tyler+Technologies+criminal+justice+software"),
        ("Motorola Solutions", "Motorola+Solutions+public+safety+law+enforcement"),
        ("IBM i2",             "IBM+i2+intelligence+analytics+law+enforcement"),
        ("Esri",               "Esri+law+enforcement+GIS+crime+analytics"),
        ("Databricks",         "Databricks+government+federal+data"),
        ("Appriss",            "Appriss+criminal+justice+data"),
        ("SuperCom",           "SuperCom+offender+monitoring+corrections"),
        ("Flock Safety",       "Flock+Safety+license+plate+recognition"),
    ]
    for comp, query in QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0",
                                           "Accept": "application/rss+xml"}, timeout=15)
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            count = 0
            for item in root.findall(".//item"):
                if count >= 2: break
                t = item.find("title"); l = item.find("link")
                d = item.find("description"); p = item.find("pubDate")
                title = (t.text or "").strip() if t is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>","", (d.text or ""))).strip() if d is not None else ""
                url_  = (l.text or "").strip() if l is not None else ""
                date_ = (p.text or "").strip() if p is not None else ""
                if not title or title in seen: continue
                if date_:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub = parsedate_to_datetime(date_).replace(tzinfo=None)
                        if (datetime.utcnow() - pub).days > 7: continue
                    except Exception:
                        continue
                seen.add(title)
                items.append({"competitor": comp, "title": title,
                              "url": clean_url(url_), "source": "Google News",
                              "date": date_[:16], "summary": desc[:280]})
                count += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[CompIntel] {comp}: {e}")
    print(f"[Competitor Intel] {len(items)} signals")
    return items


def fetch_industry_news() -> list:
    news, seen = [], set()
    FEEDS = [
        {"url": "https://fedscoop.com/feed/",                    "source": "FedScoop"},
        {"url": "https://www.nextgov.com/rss/all/",              "source": "Nextgov"},
        {"url": "https://gcn.com/rss-feeds/all.aspx",            "source": "GCN"},
        {"url": "https://www.govtech.com/public-safety/rss.xml", "source": "GovTech"},
    ]
    KW = ["law enforcement","public safety","data analytics","artificial intelligence",
          "criminal justice","fedramp","records management","predictive","digital evidence"]
    for feed in FEEDS:
        try:
            r = requests.get(feed["url"], headers={"User-Agent": HEADERS["User-Agent"],
                                                   "Accept": "text/xml"}, timeout=15)
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:15]:
                t = item.find("title"); d = item.find("description")
                l = item.find("link");  p = item.find("pubDate")
                title = (t.text or "").strip() if t is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>","", (d.text or ""))).strip() if d is not None else ""
                url_  = (l.text or "").strip() if l is not None else ""
                date_ = (p.text or "").strip() if p is not None else ""
                if not title or title in seen: continue
                if not any(kw in f"{title} {desc}".lower() for kw in KW): continue
                if date_:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub = parsedate_to_datetime(date_).replace(tzinfo=None)
                        if (datetime.utcnow() - pub).days > 7: continue
                    except Exception:
                        continue
                seen.add(title)
                news.append({"title": title, "url": clean_url(url_),
                             "source": feed["source"], "date": date_[:16],
                             "summary": desc[:250]})
            time.sleep(0.2)
        except Exception as e:
            print(f"[News] {feed['source']}: {e}")
    print(f"[Industry News] {len(news)} articles")
    return news[:15]


def fetch_budget_news() -> list:
    items, seen = [], set()
    QUERIES = [
        ("DOJ Budget",     "Department+of+Justice+budget+technology+data"),
        ("FBI Technology", "FBI+technology+data+analytics+platform"),
        ("DHS Budget",     "Department+of+Homeland+Security+budget+technology"),
        ("ICE Technology", "ICE+immigration+enforcement+technology+data"),
        ("CISA Budget",    "CISA+cybersecurity+budget+technology"),
        ("Byrne JAG",      "Byrne+JAG+grant+law+enforcement+technology"),
        ("NIBIN Funding",  "NIBIN+crime+gun+intelligence+funding"),
    ]
    for label, query in QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0",
                                           "Accept": "application/rss+xml"}, timeout=15)
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            count = 0
            for item in root.findall(".//item"):
                if count >= 2: break
                t = item.find("title"); l = item.find("link")
                d = item.find("description"); p = item.find("pubDate")
                title = (t.text or "").strip() if t is not None else ""
                desc  = unescape(re.sub(r"<[^>]+>","", (d.text or ""))).strip() if d is not None else ""
                url_  = (l.text or "").strip() if l is not None else ""
                date_ = (p.text or "").strip() if p is not None else ""
                if not title or title in seen: continue
                if date_:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub = parsedate_to_datetime(date_).replace(tzinfo=None)
                        if (datetime.utcnow() - pub).days > 7: continue
                    except Exception:
                        continue
                seen.add(title)
                items.append({"label": label, "title": title, "summary": desc[:280],
                              "url": clean_url(url_), "date": date_[:16]})
                count += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[BudgetNews] {label}: {e}")
    print(f"[Budget News] {len(items)} signals")
    return items[:18]


def deduplicate_and_rank(opps: list) -> list:
    seen, out = set(), []
    for o in sorted(opps, key=lambda x: x.score, reverse=True):
        if is_expired(o): continue
        key = o.notice_id or o.title[:60].lower()
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def build_opps_html(title: str, opps: list, color: str) -> str:
    if not opps:
        return ""
    rows = ""
    for o in opps[:50]:
        link = (f'<a href="{o.url}" style="font-weight:700;font-size:14px;'
                f'color:#0057b8;text-decoration:none;">{o.title[:120]}</a>'
                if o.url else
                f'<b style="font-size:14px;">{o.title[:120]}</b>')
        reasons_html = ""
        if o.score_reasons:
            bullets = "".join(f"<li>{r}</li>" for r in o.score_reasons[:4])
            reasons_html = (f'<ul style="margin:4px 0 0;padding-left:16px;'
                            f'font-size:12px;color:#555;">{bullets}</ul>')
        deadline = ""
        if o.response_date and o.response_date not in ("TBD", ""):
            dt = parse_date(o.response_date)
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
            f'<div style="border:1px solid #e8e8e8;border-radius:6px;padding:12px;'
            f'margin-bottom:10px;background:#fff;">'
            f'<div style="margin-bottom:5px;">{link}{deadline}</div>'
            f'<div style="font-size:12px;color:#666;">&#x1F3DB; {o.agency[:80]}'
            f' &nbsp;&middot;&nbsp; &#x1F4EC; {o.posted_date[:10]}</div>'
            f'<div style="font-size:11px;color:#999;margin-top:2px;">'
            f'Source: {o.source} &nbsp;&middot;&nbsp; Score: {o.score}pts'
            f' &nbsp;&middot;&nbsp; <a href="{o.url}" style="color:#0057b8;">View</a>'
            f'</div>{reasons_html}</div>'
        )
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid {color};'
            f'padding-bottom:5px;">{title} ({len(opps)})</h2>{rows}</div>')


def build_industry_days_html(opps: list) -> str:
    days = [o for o in opps
            if "industry day" in o.title.lower()
            and o.source != "Events Intelligence"]
    days.sort(key=lambda x: x.posted_date, reverse=True)
    if not days:
        return ""
    rows = ""
    for o in days[:30]:
        link = (f'<a href="{o.url}" style="font-weight:700;font-size:14px;'
                f'color:#1a5276;text-decoration:none;">{o.title[:120]}</a>'
                if o.url else
                f'<b style="font-size:14px;color:#1a5276;">{o.title[:120]}</b>')
        rows += (f'<div style="border:1px solid #d6eaf8;border-radius:6px;'
                 f'padding:12px;margin-bottom:8px;background:#eaf4fb;">'
                 f'<div style="margin-bottom:4px;">{link}</div>'
                 f'<div style="font-size:12px;color:#555;">&#x1F3DB; {o.agency[:80]}'
                 f' &nbsp;&middot;&nbsp; &#x1F4EC; {o.posted_date[:10]}</div>'
                 f'<div style="font-size:11px;color:#888;margin-top:2px;">'
                 f'Source: {o.source} &nbsp;&middot;&nbsp; '
                 f'<a href="{o.url}" style="color:#1a5276;">View on SAM.gov</a>'
                 f'</div></div>')
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#111;border-bottom:2px solid #d6eaf8;'
            f'padding-bottom:5px;">&#x1F4E3; Industry Days ({len(days)})</h2>'
            f'<p style="font-size:12px;color:#888;margin:0 0 8px;">'
            f'All federal agency Industry Day notices.</p>{rows}</div>')


def build_competitor_html(items: list) -> str:
    if not items: return ""
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in items:
        grouped[item["competitor"]].append(item)
    rows = ""
    for comp in sorted(grouped.keys()):
        sh = ""
        for s in grouped[comp][:2]:
            link = (f'<a href="{s["url"]}" style="color:#0057b8;font-weight:600;">'
                    f'{s["title"][:90]}</a>'
                    if s.get("url") else
                    f'<b>{s["title"][:90]}</b>')
            sh += (f'<div style="margin-bottom:8px;padding-bottom:8px;'
                   f'border-bottom:1px solid #f0f0f0;">'
                   f'<div style="font-size:13px;">{link}</div>'
                   f'<div style="font-size:11px;color:#888;">'
                   f'{s["source"]} &middot; {s["date"][:10]}</div>'
                   + (f'<div style="font-size:12px;color:#555;margin-top:2px;">'
                      f'{s.get("summary","")[:200]}</div>' if s.get("summary") else "")
                   + '</div>')
        rows += (f'<div style="margin-bottom:14px;">'
                 f'<div style="font-weight:700;font-size:12px;color:#555;'
                 f'margin-bottom:5px;text-transform:uppercase;">&#x2694;&#xFE0F; {comp}</div>'
                 f'{sh}</div>')
    comps = ", ".join(sorted(grouped.keys()))
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">&#x1F50E; Competitor Intelligence ({len(items)})</h2>'
            f'<p style="font-size:12px;color:#888;margin:0 0 10px;">Monitoring: {comps}</p>'
            f'{rows}</div>')


def build_news_html(items: list, title: str) -> str:
    if not items: return ""
    rows = ""
    for item in items[:12]:
        link = (f'<a href="{item["url"]}" style="color:#0057b8;font-weight:600;">'
                f'{item.get("title","")[:100]}</a>'
                if item.get("url") else
                f'<b>{item.get("title","")[:100]}</b>')
        label = item.get("label") or item.get("source", "")
        rows += (f'<div style="margin-bottom:10px;padding-bottom:10px;'
                 f'border-bottom:1px solid #f0f0f0;">'
                 f'<div style="font-size:12px;font-weight:700;color:#555;'
                 f'margin-bottom:2px;">&#x1F4E1; {label}</div>'
                 f'<div style="font-size:13px;">{link}</div>'
                 f'<div style="font-size:11px;color:#888;">'
                 f'{item.get("date","")[:10]}</div>'
                 + (f'<div style="font-size:12px;color:#555;margin-top:2px;">'
                    f'{item.get("summary","")[:200]}</div>' if item.get("summary") else "")
                 + '</div>')
    return (f'<div style="margin:20px 0 6px">'
            f'<h2 style="font-size:16px;color:#222;border-bottom:2px solid #eee;'
            f'padding-bottom:5px;">{title} ({len(items)})</h2>'
            f'{rows}</div>')


def build_email(ranked: list, run_date: str, source_counts: dict,
                competitor_items: list, news_items: list,
                budget_news: list) -> str:

    def _k(o): return (o.notice_id or o.title[:60].lower()).strip()
    def _dedup(lst):
        seen, out = set(), []
        for o in lst:
            k = _k(o)
            if k not in seen: seen.add(k); out.append(o)
        return out

    shown = set()
    strong = _dedup([o for o in ranked if o.tier == "Strong"])
    shown.update(_k(o) for o in strong)
    good = _dedup([o for o in ranked if o.tier == "Good" and _k(o) not in shown])
    shown.update(_k(o) for o in good)
    possible = _dedup([o for o in ranked if o.tier == "Possible" and _k(o) not in shown])
    shown.update(_k(o) for o in possible)
    low = _dedup([o for o in ranked
                  if o.tier == "Low" and o.score > 0 and _k(o) not in shown])

    sc_rows = "".join(
        f'<tr><td style="padding:3px 10px;font-size:12px;color:#555;">{k}</td>'
        f'<td style="padding:3px 10px;font-weight:700;font-size:12px;">{v}</td></tr>'
        for k, v in sorted(source_counts.items())
    )

    ns = len(strong); ng = len(good); np = len(possible)

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>'
        'body{font-family:Helvetica Neue,Arial,sans-serif;background:#f5f5f5;margin:0;padding:0}'
        '.wrap{max-width:720px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden}'
        '.hdr{background:#0057b8;padding:24px 28px;color:#fff}'
        '.content{padding:20px 28px}'
        '</style></head><body>'
        '<div class="wrap">'
        '<div class="hdr">'
        '<div style="font-size:22px;font-weight:700;">&#x1F985; Peregrine Daily Scanner</div>'
        f'<div style="font-size:14px;opacity:0.85;margin-top:4px;">{run_date}</div>'
        '<div style="margin-top:12px;display:flex;gap:16px;flex-wrap:wrap;">'
        f'<span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">&#x1F7E2; {ns} Strong</span>'
        f'<span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">&#x1F7E1; {ng} Good</span>'
        f'<span style="background:rgba(255,255,255,0.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;">&#x1F535; {np} Possible</span>'
        '</div></div>'
        '<div class="content">'
        '<details style="margin-bottom:16px;border:1px solid #eee;border-radius:6px;padding:8px 12px;">'
        '<summary style="font-size:12px;color:#888;cursor:pointer;">Sources searched today</summary>'
        f'<table style="margin-top:8px;border-collapse:collapse;">{sc_rows}</table>'
        '</details>'
        + build_opps_html("&#x1F7E2; Strong Fit &#x2014; Act Now", strong, "#27ae60")
        + build_opps_html("&#x1F7E1; Good Fit &#x2014; Review Today", good, "#f39c12")
        + build_opps_html("&#x1F535; Possible Fit &#x2014; Review These", possible, "#2980b9")
        + build_opps_html("&#x26AA; Low Fit &#x2014; Any Keyword Match", low[:20], "#95a5a6")
        + build_competitor_html(competitor_items)
        + build_news_html(budget_news, "&#x1F4E1; Agency Budget &amp; Spending Signals")
        + build_news_html(news_items, "&#x1F4F0; Industry News")
        + build_industry_days_html(ranked)
        + '</div></div></body></html>'
    )


def send_email(html: str, subject: str):
    print(f"[Email] To:{EMAIL_TO} | Key:{'SET' if SENDGRID_API_KEY else 'NOT SET'}")
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
            print(f"[Email] Sent OK (HTTP {r.status_code})")
        else:
            print(f"[Email] Failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[Email] Error: {e}")


def main():
    today    = datetime.utcnow()
    run_date = today.strftime("%B %d, %Y")
    sep = "=" * 60
    print(f"\n{sep}\n  Peregrine Daily Scanner -- {run_date}\n{sep}")

    source_counts = {}
    all_opps      = []

    for label, fn in [
        ("SAM.gov",  fetch_sam_gov),
        ("DOJ",      fetch_doj_opportunities),
        ("DHS",      fetch_dhs_opportunities),
        ("DoD",      fetch_dod_opportunities),
    ]:
        print(f"\n[{label}] Fetching...")
        try:
            batch = fn()
            source_counts[label] = len(batch)
            all_opps.extend(batch)
        except Exception as e:
            print(f"[{label}] FAILED: {e}")
            source_counts[label] = 0

    print(f"\n[Scoring] Deduplicating {len(all_opps)} raw opportunities...")
    ranked = deduplicate_and_rank(all_opps)
    ns = sum(1 for o in ranked if o.tier == "Strong")
    ng = sum(1 for o in ranked if o.tier == "Good")
    np = sum(1 for o in ranked if o.tier == "Possible")
    print(f"[Tiers] Strong:{ns}  Good:{ng}  Possible:{np}")

    print("\n[Competitor Intel] Fetching...")
    try:
        competitor_items = fetch_competitor_intel()
        source_counts["Competitor Intel"] = len(competitor_items)
    except Exception as e:
        print(f"[Competitor Intel] FAILED: {e}")
        competitor_items = []

    print("\n[Industry News] Fetching...")
    try:
        news_items = fetch_industry_news()
        source_counts["Industry News"] = len(news_items)
    except Exception as e:
        print(f"[Industry News] FAILED: {e}")
        news_items = []

    print("\n[Budget News] Fetching...")
    try:
        budget_news = fetch_budget_news()
        source_counts["Budget News"] = len(budget_news)
    except Exception as e:
        print(f"[Budget News] FAILED: {e}")
        budget_news = []

    if ns >= 1:
        subject = f"Peregrine Daily Scanner | {ns} Strong · {ng} Good · {np} Possible | {today.strftime('%b %d')}"
    elif ng >= 1:
        subject = f"Peregrine Daily Scanner | {ng} Good · {np} Possible | {today.strftime('%b %d')}"
    else:
        subject = f"Peregrine Daily Scanner | No Strong Matches | {today.strftime('%b %d')}"

    html = build_email(ranked, run_date, source_counts,
                       competitor_items, news_items, budget_news)
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
