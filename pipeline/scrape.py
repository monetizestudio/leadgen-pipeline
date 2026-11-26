"""Find businesses on OpenStreetMap and pull contact details from their websites."""
import re
import time
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

UA = {"User-Agent": "MonetizeStudioLeadFinder/1.0 (contact: monetizestudio.github.io)"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp\.com/send\?phone=)\+?(\d{8,15})")
JUNK_EMAIL = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yourname", "@2x", ".png",
              ".jpg", ".jpeg", ".gif", ".svg", ".webp", "noreply", "no-reply", "godaddy", "schema.org",
              # website theme / builder / agency addresses found in site footers
              "-themes.", "themeforest", "themeisle", "themerex", "envato", "wordpress", "wpengine", "squarespace", "wix.com", "weebly", "shopify",
              "qodeinteractive", "webflow", "hubspot", "mailchimp", "sentry.io", "cloudflare")
EMAIL_OK = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
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
        e = unquote(str(e)).strip().strip(".").lower()
        e = re.sub(r"^(mailto:|email:|e-mail:|e:)\s*", "", e)
        if not EMAIL_OK.match(e) or any(j in e for j in JUNK_EMAIL):
            continue
        if e not in out:
            out.append(e)
    # prefer an address on the business's own domain
    out.sort(key=lambda e: 0 if host and e.endswith("@" + host) else 1)
    return out


def uae_style_mobiles(text, country):
    """Mobile numbers that count as WhatsApp in this country (UAE: 05x / +971 5x), as 9715XXXXXXXX."""
    pattern = config.MOBILE_AS_WHATSAPP.get(country)
    if not pattern or not text:
        return []
    out = []
    for groups in re.findall(pattern, text):
        digits = "".join(g for g in groups if g)
        if len(digits) == 9:
            n = "971" + digits
            if n not in out:
                out.append(n)
    return out


def extract_contacts(html, site="", country=""):
    """Return (emails, whatsapp_numbers, visible_text) from one HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    hrefs = " ".join(a.get("href", "") for a in soup.find_all("a"))
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = " ".join(soup.get_text(" ").split())
    mailtos = re.findall(r"mailto:([^?\"'\s]+)", hrefs)
    emails = clean_emails(mailtos + EMAIL_RE.findall(text), site)
    was = list(dict.fromkeys(WA_RE.findall(hrefs + " " + text)))
    for n in uae_style_mobiles(hrefs + " " + text, country):
        if n not in was:
            was.append(n)
    return emails, was, text


OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def _ask_overpass(q, deadline):
    """Run one Overpass query, trying each free server in turn. Raises with every server's error."""
    errors = []
    for server in OVERPASS_SERVERS:
        for attempt in range(2):
            if time.time() > deadline:
                errors.append("out of time")
                raise RuntimeError("; ".join(errors))
            try:
                r = requests.post(server, data={"data": q}, headers=UA, timeout=120)
            except requests.RequestException as e:
                errors.append(f"{server.split('/')[2]}: {type(e).__name__}")
                break
            if r.status_code in (429, 502, 503, 504):
                errors.append(f"{server.split('/')[2]}: HTTP {r.status_code}")
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code != 200:
                errors.append(f"{server.split('/')[2]}: HTTP {r.status_code}")
                break
            return r.json()
    raise RuntimeError("; ".join(errors) or "no server answered")


def overpass_businesses(lat, lon, radius=None, country=""):
    """Businesses in our niches within `radius` metres of a city centre that list a website.
    The main niche list is one query (as it always was); a country's extra categories are a second,
    separate query, so if the extras are slow or fail the main search still works."""
    radius = radius or config.SEARCH_RADIUS_M
    around = f"(around:{radius},{lat},{lon})"
    has_site = '[~"^(website|contact:website)$"~"."]'
    extra = config.EXTRA_OSM_TAGS.get(country, [])
    tags = config.OSM_TAGS + extra
    name_rules = config.COMPANY_NAME_NICHES.get(country, [])

    def query(tag_list, more=""):
        parts = "".join(f'nwr["{k}"="{v}"]{has_site}{around};' for k, v, _ in tag_list) + more
        return f"[out:json][timeout:90];({parts});out center tags 400;"

    deadline = time.time() + 360          # give up on a city after 6 min, so one slow city can't eat the night
    try:
        data = _ask_overpass(query(config.OSM_TAGS), deadline)
    except RuntimeError as e:
        raise RuntimeError(f"OpenStreetMap search failed ({e})")
    more = ""
    if name_rules:
        names = "|".join(rx for rx, _ in name_rules).replace("[\\s-]?", ".?").replace('"', "")
        more = f'nwr["office"="company"]["name"~"{names}",i]{has_site}{around};'
    if extra or more:
        try:
            data["elements"] = data.get("elements", []) + \
                _ask_overpass(query(extra, more), deadline).get("elements", [])
        except RuntimeError as e:  # extras are a bonus: keep the main results
            print(f"extra categories skipped ({e})")
    out = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        niche = next((nm for k, v, nm in tags if t.get(k) == v), None)
        if not niche and t.get("office") == "company":
            niche = next((nm for rx, nm in name_rules if re.search(rx, t.get("name", ""), re.I)), None)
        phones = " ".join(t.get(k, "") for k in ("contact:whatsapp", "whatsapp", "mobile", "contact:mobile",
                                                  "phone", "contact:phone"))

        site = normalize_site(t.get("website") or t.get("contact:website"))
        if not (t.get("name") and site and niche):
            continue
        out.append({
            "name": t["name"],
            "website": site,
            "niche": niche,
            "osm_email": t.get("email") or t.get("contact:email") or "",
            "osm_whatsapp": uae_style_mobiles(phones, country),
        })
    time.sleep(5)  # be gentle with the free Overpass servers
    return out


def fetch_site(site, country=""):
    """Visit home + contact/about pages. Returns dict with emails, whatsapp, text."""
    emails, was, texts = [], [], []
    deadline = time.time() + 60             # a slow site gets at most a minute
    for path in CONTACT_PATHS + config.CONTACT_PATHS_EXTRA.get(country, []):
        if time.time() > deadline:
            break
        try:
            r = requests.get(urljoin(site + "/", path.lstrip("/")), headers=UA, timeout=15)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            e, w, t = extract_contacts(r.text, site, country)
            emails += [x for x in e if x not in emails]
            was += [x for x in w if x not in was]
            texts.append(t[:3000])
        except requests.RequestException:
            continue
    return {"emails": clean_emails(emails, site), "whatsapp": was, "text": " ".join(texts)[:8000]}
