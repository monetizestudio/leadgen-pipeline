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


GMAIL_LIMIT_SIGNS = ("421", "4.7.0", "5.4.5", "sending limit", "quota", "rate limit", "unusual",
                     "temporarily", "suspended")


class GmailPaused(Exception):
    pass


def run(region, max_total):
    countries = config.REGIONS[region]
    base = {"and": [_countries_filter(countries), {"property": "Channel", "select": {"equals": "Email"}}]}
    queued = notion.query(config.LEADS_DB, {"and": base["and"] + [
        {"property": "Status", "select": {"equals": "Queued"}}]})
    queued.sort(key=lambda p: notion.read(p, "Date found") or "")   # oldest leads first
    contacted = notion.query(config.LEADS_DB, {"and": base["and"] + [
        {"property": "Status", "select": {"equals": "Contacted"}}]})
    followups = []
    for p in contacted:
        touches = int(p["properties"]["Touches"]["number"] or 0)
        if touches in (1, 2) and _due(p, touches + 1):
            followups.append((p, touches + 1))

    # one budget for this run: follow-ups first (warmer), then new leads
    followups = followups[:max_total]
    queued = queued[:max(0, max_total - len(followups))]

    sent_new, sent_fu, errors = 0, 0, []
    paused = ""
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)

        def check_limit(err):
            text = str(err).lower()
            if any(sign in text for sign in GMAIL_LIMIT_SIGNS):
                raise GmailPaused(config.redact(err)[:200])

        for page, touch in followups:
            to = page["properties"]["Email"]["email"]
            try:
                _send(smtp, to, "Re: " + notion.read(page, "Subject"),
                      messages.followup_email(_fields(page), touch))
                notion.update_page(page["id"], {"Touches": {"number": touch}, "Last touch": notion.date()})
                sent_fu += 1
            except GmailPaused:
                raise
            except Exception as e:  # keep going, one bad address shouldn't stop the run
                errors.append(f"{to}: {config.redact(e)[:100]}")
                try:
                    check_limit(e)
                except GmailPaused as gp:
                    paused = str(gp)
                    break
            time.sleep(random.randint(*config.SEND_DELAY_SEC))

        for page in ([] if paused else queued):
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
                errors.append(f"{to}: {config.redact(e)[:100]}")
                try:
                    check_limit(e)
                except GmailPaused as gp:
                    paused = str(gp)
                    break
            time.sleep(random.randint(*config.SEND_DELAY_SEC))

    wa_waiting = len(notion.query(config.LEADS_DB, {"and": [
        _countries_filter(countries),
        {"property": "Channel", "select": {"equals": "WhatsApp"}},
        {"property": "Status", "select": {"equals": "Queued"}}]}))
    target = len(queued) + len(followups)
    done = sent_new + sent_fu
    result = "Done" if not errors else ("Partial" if done else "Failed")
    notes = (f"{region.upper()}: {sent_new} first emails, {sent_fu} follow-ups "
             f"(run cap {max_total}, today's cap {config.daily_email_cap()}). "
             f"{wa_waiting} WhatsApp leads waiting for bulk sender. Errors: {len(errors)}")
    if errors:
        notes += " | " + "; ".join(errors[:3])
    if paused:
        result = "Failed"
        notes = f"GMAIL PAUSED SENDING, stopped to protect the account: {paused}. " + notes
    notion.log_run(f"Send {region} {dt.date.today()}", "Directories send", target, done, result, notes)
    if paused:
        notify.push("⛔ Gmail limit hit, sending paused", notes, priority="urgent", tags="no_entry")
    else:
        notify.push(f"Emails sent: {region.upper()}", notes, tags="email")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, choices=list(config.REGIONS))
    ap.add_argument("--max", type=int, default=None, help="emails this run (default: today's warm-up cap / 4)")
    args = ap.parse_args()
    try:
        run(args.region, args.max if args.max is not None else config.per_run_cap())
    except Exception as e:
        traceback.print_exc()
        notify.push(f"Send {args.region} FAILED", config.redact(e)[:300], priority="high", tags="warning")
        raise


if __name__ == "__main__":
    main()
