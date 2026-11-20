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
    found, checked, no_contact, rejected = 0, 0, 0, 0
    by_channel = {"Email": 0, "WhatsApp": 0}
    seen = set()
    city_errors = []

    for country, city, (lat, lon) in tonight_cities():
        if found >= limit or checked >= config.MAX_CANDIDATES:
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
        for biz in businesses:
            if found >= limit or checked >= config.MAX_CANDIDATES:
                break
            if biz["website"] in seen:
                continue
            seen.add(biz["website"])
            lead = {**biz, "city": city, "country": country}
            checked += 1

            site = scrape.fetch_site(lead["website"])
            if biz["osm_email"]:
                site["emails"] = scrape.clean_emails([biz["osm_email"]] + site["emails"], lead["website"])
            if not (site["emails"] or site["whatsapp"]):
                no_contact += 1          # rule: no contact details = doesn't count
                continue
            email = site["emails"][0] if site["emails"] else ""
            if notion.lead_exists(lead["website"], email):
                continue                 # already in Notion, never contact twice
            if len(site["text"]) < 200:
                rejected += 1
                continue

            res = ai.qualify(lead, site["text"])
            if not ai.is_qualified(res):
                rejected += 1
                continue

            props, channel = build_props(lead, res, site)
            notion.create_lead(props)
            found += 1
            by_channel[channel] += 1
            print(f"[{found}/{limit}] {lead['name']} ({city}) via {channel}")

    mins = int((time.time() - started) / 60)
    result = "Done" if found >= limit else ("Partial" if found else "Failed")
    notes = (f"Checked {checked}, no contact {no_contact}, not a fit {rejected}. "
             f"Email {by_channel['Email']}, WhatsApp {by_channel['WhatsApp']}. {mins} min.")
    if city_errors:
        notes += f" Cities failed: {len(city_errors)} (e.g. {city_errors[0]})"
    notion.log_run(f"Directories find {dt.date.today()}", "Directories find", limit, found, result, notes)
    notify.push("Directories: leads found", f"{found}/{limit} qualified leads queued. {notes}",
                tags="mag")
    if checked == 0:
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
