"""Nightly run: keep every region stocked with qualified email leads so 25 a day can go out per region.

For each region (Australia, UAE, UK, US) it tops the email queue up to QUEUE_DAYS x 25 leads, checking
as many businesses as it takes. Regions take turns city by city, so one big city can't eat the night.

Usage:  python -m pipeline.find            (full run)
        python -m pipeline.find --limit 5  (quick test: stop after 5 qualified leads in total)
"""
import argparse
import datetime as dt
import time
import traceback

from . import ai, config, messages, notion, notify, scrape


def region_cities(region, day=None):
    """That region's search cities, rotated so each night starts at a different one."""
    flat = [(country, city, ll) for country in config.SEARCH_COUNTRIES[region]
            for city, ll in config.CITIES[country].items()]
    start = (day or dt.date.today()).toordinal() % len(flat)
    return flat[start:] + flat[:start]


def tonight_cities():
    """Every search city once (used by the tests and for a quick overview)."""
    return [c for r in config.SEARCH_COUNTRIES for c in region_cities(r)]


def queued_ready(region):
    """Leads waiting to be counted toward a region's daily 25 (email or WhatsApp)."""
    return len(notion.query(config.LEADS_DB, {"and": [
        {"or": [{"property": "Country", "select": {"equals": c}} for c in config.REGIONS[region]]},
        {"property": "Status", "select": {"equals": "Queued"}},
        {"property": "Outreach day", "date": {"is_empty": True}}]}))


def build_props(lead, res, contacts):
    channel = "WhatsApp" if contacts["whatsapp"] else "Email"
    email = contacts["emails"][0] if contacts["emails"] else ""
    f = messages.fields(lead["name"], res.get("first_name"), lead["niche"], lead["city"], res["observation"])
    subject, body = messages.first_email(f, lead["niche"])
    msg = messages.whatsapp(f) if channel == "WhatsApp" else body
    props = {
        "Business": notion.title(lead["name"]),
        "Website": {"url": lead["website"]},
        "Country": notion.select(lead["country"]),
        "City": notion.text(lead["city"]),
        "Niche": notion.select(lead["niche"]),
        "Fit score": {"number": int(res.get("fit_score", 0) or 0)},
        "Fit notes": notion.text(res.get("fit_notes", "")),
        "First name": notion.text(res.get("first_name", "")),
        "Channel": notion.select(channel),
        "Message": notion.text(msg),
        "Subject": notion.text(subject),
        "Touches": {"number": 0},
        "Date found": notion.date(),
        "Status": notion.select("Queued"),
    }
    if email:
        props["Email"] = {"email": email}
    if contacts["whatsapp"]:
        props["WhatsApp"] = {"phone_number": "+" + contacts["whatsapp"][0]}
    return props, channel


class Region:
    """Search state for one region during the night."""

    def __init__(self, name, need):
        self.name, self.need = name, need
        self.cities = region_cities(name)
        self.passes = [config.SEARCH_RADIUS_M, config.WIDE_RADIUS_M]
        self.queue = []          # (country, city, business) still to check
        self.c = {"cities": 0, "found": 0, "checked": 0, "no_contact": 0, "duplicate": 0, "thin": 0,
                  "had_contact": 0, "rejected": 0, "email": 0, "whatsapp": 0}
        self.rejects = {}
        self.errors = []

    @property
    def short(self):
        return max(0, self.need - self.c["email"] - self.c["whatsapp"])

    def refill(self, seen):
        """Load the next city's businesses. False when every city (both radii) is used up."""
        while not self.queue:
            if not self.cities:
                self.passes.pop(0)
                if not self.passes:
                    return False
                self.cities = region_cities(self.name)
            country, city, (lat, lon) = self.cities.pop(0)
            try:
                found = scrape.overpass_businesses(lat, lon, self.passes[0])
            except Exception as e:  # one city failing shouldn't stop the night
                self.errors.append(f"{city}: {config.redact(e)[:120]}")
                print(f"skip {self.errors[-1]}")
                continue
            fresh = [b for b in found if b["website"] not in seen]
            print(f"{self.name}/{city} ({self.passes[0] // 1000} km): {len(found)} with websites, {len(fresh)} new")
            self.c["cities"] += 1
            self.c["found"] += len(fresh)
            self.queue = [(country, city, b) for b in fresh]
        return True

    def summary(self):
        c = self.c
        rej = ", ".join(f"{k} {v}" for k, v in sorted(self.rejects.items(), key=lambda x: -x[1])) or "none"
        s = (f"{self.name.upper()}: {c['email'] + c['whatsapp']}/{self.need} leads ({c['whatsapp']} WhatsApp, {c['email']} email). "
             f"{c['cities']} cities > {c['found']} businesses > {c['checked']} checked > "
             f"{c['had_contact']} had contact. Rejected: {rej}; no contact {c['no_contact']}, "
             f"thin {c['thin']}, already in Leads {c['duplicate']}.")
        if self.errors:
            s += f" Cities failed: {len(self.errors)} (e.g. {self.errors[0]})"
        return s


def check_one(reg, country, city, biz, seen):
    """Check one business; adds it to Leads if it qualifies."""
    c = reg.c
    if biz["website"] in seen:
        return
    seen.add(biz["website"])
    lead = {**biz, "city": city, "country": country}
    site = scrape.fetch_site(lead["website"])
    if biz["osm_email"]:
        site["emails"] = scrape.clean_emails([biz["osm_email"]] + site["emails"], lead["website"])
    email = site["emails"][0] if site["emails"] else ""
    if notion.lead_exists(lead["website"], email):
        c["duplicate"] += 1      # already in Leads: never contact twice
        return
    c["checked"] += 1
    has_e, has_w = bool(site["emails"]), bool(site["whatsapp"])
    if not (has_e or has_w):
        c["no_contact"] += 1
        notion.log_checked(lead, "No contact", "No email or WhatsApp on website or map listing")
        return
    c["had_contact"] += 1
    if len(site["text"]) < 200:
        c["thin"] += 1
        notion.log_checked(lead, "Thin website", "Website had too little text to judge", has_e, has_w)
        return
    res = ai.qualify(lead, site["text"])
    label, reason = ai.verdict(res)
    score = int(res.get("fit_score", 0) or 0) if res else None
    notion.log_checked(lead, label, reason, has_e, has_w, score)
    if label != "Qualified":
        c["rejected"] += 1
        reg.rejects[label] = reg.rejects.get(label, 0) + 1
        return
    props, channel = build_props(lead, res, site)
    notion.create_lead(props)
    c["email" if channel == "Email" else "whatsapp"] += 1
    print(f"[{reg.name} {c['email'] + c['whatsapp']}/{reg.need}] {lead['name']} ({city}) via {channel}")


def run(limit=None):
    started = time.time()
    target = config.QUEUE_DAYS
    regions = []
    for name, per_day in config.REGION_DAILY_NEW.items():
        have = queued_ready(name)
        regions.append(Region(name, max(0, per_day * target - have)))
        print(f"{name}: {have} leads ready, need {regions[-1].need} more")
    if limit:  # test run: spread a small total across regions
        for r in regions:
            r.need = min(r.need, max(1, limit // len(regions)))
    total_need = sum(r.need for r in regions)
    seen = set()
    checked = 0

    def out_of_time():
        return (time.time() - started) / 60 > config.FIND_TIME_LIMIT_MIN or checked >= config.MAX_CANDIDATES

    active = [r for r in regions if r.need > 0]
    while active and not out_of_time():
        active.sort(key=lambda r: -r.short)        # region furthest from its target goes first
        reg = active[0]
        if not reg.refill(seen):
            active.remove(reg)                     # this region has nothing left to search
            continue
        for _ in range(config.CITY_CHUNK):
            if not reg.queue or reg.short == 0 or out_of_time():
                break
            country, city, biz = reg.queue.pop(0)
            before = reg.c["checked"]
            try:
                check_one(reg, country, city, biz, seen)
            except Exception as e:
                if "Gemini failed" in str(e):
                    raise                          # AI is down: stop and alert
                reg.errors.append(f"{biz['name']}: {config.redact(e)[:100]}")
                print(f"[find] skipped {biz['name']}: {config.redact(e)[:150]}")
            checked += reg.c["checked"] - before
        if reg.short == 0:
            active.remove(reg)

    mins = int((time.time() - started) / 60)
    found = sum(r.c["email"] + r.c["whatsapp"] for r in regions)
    wa = sum(r.c["whatsapp"] for r in regions)
    short = [r for r in regions if r.short]
    result = "Done" if not short else ("Partial" if found else "Failed")
    notes = f"{found}/{total_need} leads queued ({wa} WhatsApp) in {mins} min, {checked} businesses checked. " + \
        " | ".join(r.summary() for r in regions)
    if out_of_time() and short:
        notes += " Stopped at the time/check limit."
    notion.log_run(f"Directories find {dt.date.today()}", "Directories find", total_need, found, result, notes)
    per = ", ".join(f"{r.name.upper()} {r.c['email'] + r.c['whatsapp']}/{r.need}" for r in regions)
    notify.push("Directories: leads found", f"{found}/{total_need} leads queued, {wa} of them WhatsApp ({per}). "
                f"{checked} businesses checked in {mins} min.", tags="mag")
    if total_need and checked == 0:
        raise RuntimeError("No businesses could be checked. " + notes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="test: stop after about this many leads in total")
    args = ap.parse_args()
    try:
        run(args.limit or None)
    except Exception as e:
        traceback.print_exc()
        if "No businesses could be checked" not in str(e):  # that case is already logged above
            try:
                notion.log_run(f"Directories find {dt.date.today()}", "Directories find", 0, 0,
                               "Failed", f"Error: {config.redact(e)[:500]}")
            finally:
                notify.push("Directories find FAILED", config.redact(e)[:300], priority="high", tags="warning")
        raise


if __name__ == "__main__":
    main()
