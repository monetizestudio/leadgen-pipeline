"""Regional send run: 25 first emails a day per region (Australia, UAE, UK, US), plus day-3 and
day-7 follow-ups on top. Safe to run more than once a day: it counts what already went out today
(in that region's time zone) and only sends what's still owed, so a late or retried run fills the gap.

Usage:  python -m pipeline.send --region uae        (regions: australia, uae, uk, na)
        python -m pipeline.send --region uk --max 2  (test: send at most 2 new emails)
WhatsApp leads are left as Queued for the WhatsApp bulk sender (Leads > Channel = WhatsApp).
"""
import argparse
import datetime as dt
import random
import smtplib
import time
import traceback
from email.message import EmailMessage
from email.utils import make_msgid

from . import config, messages, notion, notify, scrape


def _countries_filter(countries):
    return {"or": [{"property": "Country", "select": {"equals": c}} for c in countries]}


def _send(smtp, to, subject, body):
    msg = EmailMessage()
    msg["From"] = f"{config.SENDER_NAME} <{config.GMAIL_ADDRESS}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=config.GMAIL_ADDRESS.split("@")[-1])
    msg.set_content(body + messages.footer())
    smtp.send_message(msg)


def _due(page, touch, today):
    last = notion.read(page, "Last touch")
    if not last:
        return False
    gap = config.FOLLOWUP_GAP_DAYS[touch]
    return dt.date.fromisoformat(last[:10]) <= today - dt.timedelta(days=gap)


def _fields(page):
    return messages.fields(notion.read(page, "Business"), notion.read(page, "First name"),
                           notion.read(page, "Niche"), notion.read(page, "City"), "")


GMAIL_LIMIT_SIGNS = ("421", "4.7.0", "5.4.5", "sending limit", "quota", "rate limit", "unusual",
                     "temporarily", "suspended")


def plan(region, today, max_new=None):
    """What this run should send: (follow-ups, new leads, new already sent today, new still owed).
    Counting what already went out today (region time) makes repeat/late runs safe."""
    countries = config.REGIONS[region]
    base = [_countries_filter(countries), {"property": "Channel", "select": {"equals": "Email"}}]
    sent_today = len(notion.query(config.LEADS_DB, {"and": [
        _countries_filter(countries),
        {"property": "Date contacted", "date": {"equals": today.isoformat()}}]}))
    owed = max(0, config.REGION_DAILY_NEW[region] - sent_today)
    if max_new is not None:
        owed = min(owed, max_new)
    queued = notion.query(config.LEADS_DB, {"and": base + [
        {"property": "Status", "select": {"equals": "Queued"}}]})
    # search countries first (US before Canada), then oldest leads first
    first = config.SEARCH_COUNTRIES[region]
    queued.sort(key=lambda p: (0 if notion.read(p, "Country") in first else 1, notion.read(p, "Date found") or ""))
    contacted = notion.query(config.LEADS_DB, {"and": base + [
        {"property": "Status", "select": {"equals": "Contacted"}}]})
    followups = []
    for p in contacted:
        touches = int(p["properties"]["Touches"]["number"] or 0)
        if touches in (1, 2) and _due(p, touches + 1, today):
            followups.append((p, touches + 1))
    return followups[:config.MAX_FOLLOWUPS_PER_RUN], queued, sent_today, owed


def _address(page):
    raw = page["properties"]["Email"]["email"] or ""
    clean = scrape.clean_emails([raw])
    return clean[0] if clean else ""


def run(region, max_new=None):
    today = config.region_today(region)
    followups, queued, sent_before, owed = plan(region, today, max_new)
    if not followups and owed == 0:
        print(f"{region}: {sent_before} new emails already sent today and no follow-ups due. Nothing to do.")
        return

    sent_new, sent_fu, skipped, errors = 0, 0, 0, []
    paused = ""
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)

        def limit_hit(err):
            text = str(err).lower()
            return config.redact(err)[:200] if any(sign in text for sign in GMAIL_LIMIT_SIGNS) else ""

        for page, touch in followups:
            to = _address(page)
            if not to:
                continue
            try:
                _send(smtp, to, "Re: " + notion.read(page, "Subject"),
                      messages.followup_email(_fields(page), touch))
                notion.update_page(page["id"], {"Touches": {"number": touch}, "Last touch": notion.date(today)})
                sent_fu += 1
            except Exception as e:  # keep going, one bad address shouldn't stop the run
                errors.append(f"{to}: {config.redact(e)[:100]}")
                paused = limit_hit(e)
                if paused:
                    break
            time.sleep(random.randint(*config.SEND_DELAY_SEC))

        for page in ([] if paused else queued):
            if sent_new >= owed:
                break
            to = _address(page)
            if not to:
                notion.update_page(page["id"], {"Status": notion.select("Bounced"),
                                                "Reply": notion.text("Not a usable email address")})
                skipped += 1
                continue
            try:
                _send(smtp, to, notion.read(page, "Subject"), notion.read(page, "Message"))
                notion.update_page(page["id"], {
                    "Email": {"email": to},
                    "Status": notion.select("Contacted"),
                    "Touches": {"number": 1},
                    "Date contacted": notion.date(today),
                    "Last touch": notion.date(today),
                })
                sent_new += 1
                print(f"sent {sent_new}/{owed}: {to}")
            except smtplib.SMTPRecipientsRefused:
                notion.update_page(page["id"], {"Status": notion.select("Bounced")})
                skipped += 1
            except Exception as e:
                errors.append(f"{to}: {config.redact(e)[:100]}")
                paused = limit_hit(e)
                if paused:
                    break
            time.sleep(random.randint(*config.SEND_DELAY_SEC))

    wa_waiting = len(notion.query(config.LEADS_DB, {"and": [
        _countries_filter(config.REGIONS[region]),
        {"property": "Channel", "select": {"equals": "WhatsApp"}},
        {"property": "Status", "select": {"equals": "Queued"}}]}))
    total_new = sent_before + sent_new
    goal = config.REGION_DAILY_NEW[region]
    short = total_new < goal and max_new is None
    result = "Failed" if paused else ("Partial" if short or errors else "Done")
    notes = (f"{region.upper()}: {sent_new} first emails this run ({total_new}/{goal} today), "
             f"{sent_fu} follow-ups. {wa_waiting} WhatsApp leads waiting for bulk sender. "
             f"Bad addresses skipped: {skipped}. Errors: {len(errors)}")
    if short and not paused:
        notes += f". SHORT: ran out of queued {region.upper()} email leads."
    if errors:
        notes += " | " + "; ".join(errors[:3])
    if paused:
        notes = f"GMAIL PAUSED SENDING, stopped to protect the account: {paused}. " + notes
    notion.log_run(f"Send {region} {today}", "Directories send", goal, total_new, result, notes)
    if paused:
        notify.push("⛔ Gmail limit hit, sending paused", notes, priority="urgent", tags="no_entry")
    else:
        flag = "⚠️ " if short else "✉️ "
        notify.push(f"{flag}Emails sent: {region.upper()} {total_new}/{goal}", notes, tags="email")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, choices=list(config.REGIONS))
    ap.add_argument("--max", type=int, default=None, help="test: at most this many new emails this run")
    args = ap.parse_args()
    try:
        run(args.region, args.max)
    except Exception as e:
        traceback.print_exc()
        notify.push(f"Send {args.region} FAILED", config.redact(e)[:300], priority="high", tags="warning")
        raise


if __name__ == "__main__":
    main()
