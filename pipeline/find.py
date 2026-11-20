"""Nightly run: find 100 qualified foreign businesses with contact details and queue them in Notion.

Usage:  python -m pipeline.find            (full run)
        python -m pipeline.find --limit 5  (quick test: stop after 5 qualified leads)
"""
import argparse
import datetime as dt
import time
import traceback

from . import ai, config, messages, notion, notify, scrape


def tonight_cities():
    flat = [(country, city, ll) for country, cities in config.CITIES.items() for city, ll in cities.items()]
    start = (dt.date.today().toordinal() * config.CITIES_PER_NIGHT) % len(flat)
    # all cities, starting at tonight's offset, so we keep going if early cities run dry
    return flat[start:] + flat[:start]


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
        "Fit score": {"number": int(res.get("fit_score", 0))},
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


def run(limit):
    started = time.time()
    c = {"cities": 0, "found": 0, "checked": 0, "no_contact": 0, "duplicate": 0, "thin": 0,
         "had_contact": 0, "rejected": 0, "qualified": 0}
    reject_reasons = {}
    by_channel = {"Email": 0, "WhatsApp": 0}
    seen = set()
    city_errors = []

    for country, city, (lat, lon) in tonight_cities():
        if c["qualified"] >= limit or c["checked"] >= config.MAX_CANDIDATES:
            break
        if (time.time() - started) / 60 > config.FIND_TIME_LIMIT_MIN:
            break
        try:
            businesses = scrape.overpass_businesses(lat, lon)
            print(f"{city}: {len(businesses)} businesses with websites")
        except Exception as e:  # one city failing shouldn't stop the night
            city_errors.append(f"{city}: {config.redact(e)[:120]}")
            print(f"skip {city_errors[-1]}")
            continue
        c["cities"] += 1
        c["found"] += len(businesses)
        for biz in businesses:
            if c["qualified"] >= limit or c["checked"] >= config.MAX_CANDIDATES:
                break
            if biz["website"] in seen:
                continue
            seen.add(biz["website"])
            lead = {**biz, "city": city, "country": country}

            site = scrape.fetch_site(lead["website"])
            if biz["osm_email"]:
                site["emails"] = scrape.clean_emails([biz["osm_email"]] + site["emails"], lead["website"])
            email = site["emails"][0] if site["emails"] else ""
            if notion.lead_exists(lead["website"], email):
                c["duplicate"] += 1      # already in Leads: never contact twice, not logged again
                continue
            c["checked"] += 1
            has_e, has_w = bool(site["emails"]), bool(site["whatsapp"])
            if not (has_e or has_w):
                c["no_contact"] += 1     # rule: no contact details = doesn't count
                notion.log_checked(lead, "No contact", "No email or WhatsApp on website or map listing")
                continue
            c["had_contact"] += 1
            if len(site["text"]) < 200:
                c["thin"] += 1
                notion.log_checked(lead, "Thin website", "Website had too little text to judge",
                                   has_e, has_w)
                continue

            res = ai.qualify(lead, site["text"])
            label, reason = ai.verdict(res)
            score = int(res.get("fit_score", 0) or 0) if res else None
            notion.log_checked(lead, label, reason, has_e, has_w, score)
            if label != "Qualified":
                c["rejected"] += 1
                reject_reasons[label] = reject_reasons.get(label, 0) + 1
                continue

            props, channel = build_props(lead, res, site)
            notion.create_lead(props)
            c["qualified"] += 1
            by_channel[channel] += 1
            print(f"[{c['qualified']}/{limit}] {lead['name']} ({city}) via {channel}")

    mins = int((time.time() - started) / 60)
    found = c["qualified"]
    result = "Done" if found >= limit else ("Partial" if found else "Failed")
    rejects = ", ".join(f"{k} {v}" for k, v in sorted(reject_reasons.items(), key=lambda x: -x[1])) or "none"
    notes = (f"Funnel: {c['cities']} cities searched > {c['found']} businesses with websites > "
             f"{c['checked']} checked > {c['had_contact']} had contact > {found} qualified. "
             f"Queued: Email {by_channel['Email']}, WhatsApp {by_channel['WhatsApp']}. "
             f"Rejected by AI: {rejects}. No contact {c['no_contact']}, thin site {c['thin']}, "
             f"already in Leads {c['duplicate']}. {mins} min.")
    if city_errors:
        notes += f" Cities failed: {len(city_errors)} (e.g. {city_errors[0]})"
    notion.log_run(f"Directories find {dt.date.today()}", "Directories find", limit, found, result, notes)
    notify.push("Directories: leads found", f"{found}/{limit} qualified. {notes}", tags="mag")
    if c["checked"] == 0:
        # nothing could even be checked: make the GitHub run go red so it's noticed
        raise RuntimeError("No businesses could be checked. " + notes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=config.DAILY_TARGET)
    args = ap.parse_args()
    try:
        run(args.limit)
    except Exception as e:
        traceback.print_exc()
        if "No businesses could be checked" not in str(e):  # that case is already logged above
            try:
                notion.log_run(f"Directories find {dt.date.today()}", "Directories find", args.limit, 0,
                               "Failed", f"Error: {config.redact(e)[:500]}")
            finally:
                notify.push("Directories find FAILED", config.redact(e)[:300], priority="high", tags="warning")
        raise


if __name__ == "__main__":
    main()
