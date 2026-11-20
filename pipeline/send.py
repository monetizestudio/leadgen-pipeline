"""Regional send run: emails queued leads at that region's morning, plus day-3 and day-7 follow-ups.

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

from . import config, messages, notion, notify


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


def _due(page, touch):
    last = notion.read(page, "Last touch")
    if not last:
        return False
    gap = config.FOLLOWUP_GAP_DAYS[touch]
    return dt.date.fromisoformat(last[:10]) <= dt.date.today() - dt.timedelta(days=gap)


def _fields(page):
    return messages.fields(notion.read(page, "Business"), notion.read(page, "First name"),
                           notion.read(page, "Niche"), notion.read(page, "City"), "")


def run(region, max_new):
    countries = config.REGIONS[region]
    base = {"and": [_countries_filter(countries), {"property": "Channel", "select": {"equals": "Email"}}]}
    queued = notion.query(config.LEADS_DB, {"and": base["and"] + [
        {"property": "Status", "select": {"equals": "Queued"}}]})[:max_new]
    contacted = notion.query(config.LEADS_DB, {"and": base["and"] + [
        {"property": "Status", "select": {"equals": "Contacted"}}]})
    followups = []
    for p in contacted:
        touches = int(p["properties"]["Touches"]["number"] or 0)
        if touches in (1, 2) and _due(p, touches + 1):
            followups.append((p, touches + 1))

    sent_new, sent_fu, errors = 0, 0, []
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)

        for page, touch in followups:
            to = page["properties"]["Email"]["email"]
            try:
                _send(smtp, to, "Re: " + notion.read(page, "Subject"),
                      messages.followup_email(_fields(page), touch))
                notion.update_page(page["id"], {"Touches": {"number": touch}, "Last touch": notion.date()})
                sent_fu += 1
            except Exception as e:  # keep going, one bad address shouldn't stop the run
                errors.append(f"{to}: {e}")
            time.sleep(random.randint(*config.SEND_DELAY_SEC))

        for page in queued:
            to = page["properties"]["Email"]["email"]
            if not to:
                continue
            try:
                _send(smtp, to, notion.read(page, "Subject"), notion.read(page, "Message"))
                notion.update_page(page["id"], {
                    "Status": notion.select("Contacted"),
                    "Touches": {"number": 1},
                    "Date contacted": notion.date(),
                    "Last touch": notion.date(),
                })
                sent_new += 1
                print(f"sent {sent_new}: {to}")
            except smtplib.SMTPRecipientsRefused:
                notion.update_page(page["id"], {"Status": notion.select("Bounced")})
            except Exception as e:
                errors.append(f"{to}: {e}")
            time.sleep(random.randint(*config.SEND_DELAY_SEC))

    wa_waiting = len(notion.query(config.LEADS_DB, {"and": [
        _countries_filter(countries),
        {"property": "Channel", "select": {"equals": "WhatsApp"}},
        {"property": "Status", "select": {"equals": "Queued"}}]}))
    target = len(queued) + len(followups)
    done = sent_new + sent_fu
    result = "Done" if not errors else ("Partial" if done else "Failed")
    notes = (f"{region.upper()}: {sent_new} first emails, {sent_fu} follow-ups. "
             f"{wa_waiting} WhatsApp leads waiting for bulk sender. Errors: {len(errors)}")
    if errors:
        notes += " | " + "; ".join(errors[:3])
    notion.log_run(f"Send {region} {dt.date.today()}", "Directories send", target, done, result, notes)
    notify.push(f"Emails sent: {region.upper()}", notes, tags="email")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, choices=list(config.REGIONS))
    ap.add_argument("--max", type=int, default=config.SEND_CAP_PER_RUN)
    args = ap.parse_args()
    try:
        run(args.region, args.max)
    except Exception as e:
        traceback.print_exc()
        notify.push(f"Send {args.region} FAILED", config.redact(e)[:300], priority="high", tags="warning")
        raise


if __name__ == "__main__":
    main()
