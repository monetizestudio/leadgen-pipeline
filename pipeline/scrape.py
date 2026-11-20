"""Find businesses on OpenStreetMap and pull contact details from their websites."""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

UA = {"User-Agent": "MonetizeStudioLeadFinder/1.0 (contact: monetizestudio.github.io)"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp\.com/send\?phone=)\+?(\d{8,15})")
JUNK_EMAIL = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yourname", "@2x", ".png",
              ".jpg", ".jpeg", ".gif", ".svg", ".webp", "noreply", "no-reply", "godaddy", "schema.org")
CONTACT_PATHS = ["", "/contact", "/contact-us", "/about", "/about-us"]


def normalize_site(url):
    if not url:
        return ""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.")
    return f"https://{host}" if host else ""


def clean_emails(found, site=""):
    host = urlparse(site).netloc.removeprefix("www.") if site else ""
    out = []
    for e in found:
        e = e.strip(".").lower()
        if any(j in e for j in JUNK_EMAIL):
            continue
        if e not in out:
            out.append(e)
    # prefer an address on the business's own domain
    out.sort(key=lambda e: 0 if host and e.endswith("@" + host) else 1)
    return out


def extract_contacts(html, site=""):
    """Return (emails, whatsapp_numbers, visible_text) from one HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    hrefs = " ".join(a.get("href", "") for a in soup.find_all("a"))
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = " ".join(soup.get_text(" ").split())
    mailtos = re.findall(r"mailto:([^?\"'\s]+)", hrefs)
    emails = clean_emails(mailtos + EMAIL_RE.findall(text), site)
    was = list(dict.fromkeys(WA_RE.findall(hrefs + " " + text)))
    return emails, was, text


OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def overpass_businesses(lat, lon, radius=None):
    """Businesses in our niches within `radius` metres of a city centre that list a website.
    Tries each free Overpass server in turn; raises if all of them fail."""
    radius = radius or config.SEARCH_RADIUS_M
    around = f"(around:{radius},{lat},{lon})"
    parts = "".join(
        f'nwr["{k}"="{v}"][~"^(website|contact:website)$"~"."]{around};'
        for k, v, _ in config.OSM_TAGS
    )
    q = f"[out:json][timeout:90];({parts});out center tags 400;"
    last_err = "no server answered"
    data = None
    for server in OVERPASS_SERVERS:
        for attempt in range(2):
            try:
                r = requests.post(server, data={"data": q}, headers=UA, timeout=120)
            except requests.RequestException as e:
                last_err = f"{server}: {e}"
                break
            if r.status_code in (429, 502, 503, 504):
                last_err = f"{server}: HTTP {r.status_code}"
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code != 200:
                last_err = f"{server}: HTTP {r.status_code}"
                break
            data = r.json()
            break
        if data is not None:
            break
    if data is None:
        raise RuntimeError(f"OpenStreetMap search failed ({last_err})")
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        niche = next((nm for k, v, nm in config.OSM_TAGS if tags.get(k) == v), None)
        site = normalize_site(tags.get("website") or tags.get("contact:website"))
        if not (tags.get("name") and site and niche):
            continue
        out.append({
            "name": tags["name"],
            "website": site,
            "niche": niche,
            "osm_email": tags.get("email") or tags.get("contact:email") or "",
        })
    time.sleep(5)  # be gentle with the free Overpass servers
    return out


def fetch_site(site):
    """Visit home + contact/about pages. Returns dict with emails, whatsapp, text."""
    emails, was, texts = [], [], []
    for path in CONTACT_PATHS:
        try:
            r = requests.get(urljoin(site + "/", path.lstrip("/")), headers=UA, timeout=15)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            e, w, t = extract_contacts(r.text, site)
            emails += [x for x in e if x not in emails]
            was += [x for x in w if x not in was]
            texts.append(t[:3000])
        except requests.RequestException:
            continue
    return {"emails": clean_emails(emails, site), "whatsapp": was, "text": " ".join(texts)[:8000]}
