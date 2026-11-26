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


GMAIL_LIMIT_SIGNS = ("5.4.5", "sending limit", "quota", "rate limit", "unusual", "suspended",
                     "try again later", "temporarily", "4.7.0")
# Gmail drops an SMTP session that stays open for a long run ("421 4.7.0 Connection expired").
# That is not a limit: log in again and resend. Only pause if Gmail still refuses on a fresh login.
DROPPED_SIGNS = ("connection expired", "try reconnecting", "connection unexpectedly closed",
                 "server not connected", "connection reset", "broken pipe", "timed out")


def is_dropped(err):
    if isinstance(err, (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError)):
        return True
    return any(sign in str(err).lower() for sign in DROPPED_SIGNS)


def is_limit(err):
    if isinstance(err, smtplib.SMTPAuthenticationError):
        return True             # app password rejected: stop and tell Azeem
    if is_dropped(err):
        return False
    return any(sign in str(err).lower() for sign in GMAIL_LIMIT_SIGNS)


class Mailer:
    """Gmail SMTP that logs in again whenever the connection has gone stale."""

    def __init__(self, connect=None):
        self._connect = connect or self._gmail
        self.smtp = None
        self.reconnects = 0

    @staticmethod
    def _gmail():
        smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60)
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        return smtp

    def _fresh(self):
        self.close()
        self.smtp = self._connect()

    def send(self, to, subject, body):
        if self.smtp is None:
            self._fresh()
        for attempt in range(3):
            try:
                return _send(self.smtp, to, subject, body)
            except smtplib.SMTPRecipientsRefused:
                raise
            except Exception as e:
                # dropped session, or a 421 that may just be the old session: reconnect and retry
                if attempt == 2 or not (is_dropped(e) or "421" in str(e)):
                    raise
                print(f"[send] connection dropped ({config.redact(e)[:80]}), logging in again")
                self.reconnects += 1
                time.sleep(5 * (attempt + 1))
                self._fresh()

    def close(self):
        if self.smtp is not None:
            try:
                self.smtp.quit()
            except Exception:
                pass
            self.smtp = None


def _uncounted_queued(region, channel):
    pages = notion.query(config.LEADS_DB, {"and": [
        _countries_filter(config.REGIONS[region]),
        {"property": "Channel", "select": {"equals": channel}},
        {"property": "Status", "select": {"equals": "Queued"}},
        {"property": "Outreach day", "date": {"is_empty": True}}]})
    first = config.SEARCH_COUNTRIES[region]     # US before Canada, then oldest first
    pages.sort(key=lambda p: (0 if notion.read(p, "Country") in first else 1, notion.read(p, "Date found") or ""))
    return pages


def plan(region, today, max_new=None):
    """What this run should do: (follow-ups, WhatsApp leads to save, email leads, done today, still owed).
    A region's 25 a day = WhatsApp leads saved for Azeem's Sunday batch (first priority) + first emails.
    Counting what already went out today (region time, "Outreach day") makes repeat/late runs safe."""
    countries = config.REGIONS[region]
    done_today = len(notion.query(config.LEADS_DB, {"and": [
        _countries_filter(countries),
        {"property": "Outreach day", "date": {"equals": today.isoformat()}}]}))
    owed = max(0, config.REGION_DAILY_NEW[region] - done_today)
    if max_new is not None:
        owed = min(owed, max_new)
    whatsapp = _uncounted_queued(region, "WhatsApp")[:owed]
    queued = _uncounted_queued(region, "Email")
    contacted = notion.query(config.LEADS_DB, {"and": [
        _countries_filter(countries), {"property": "Channel", "select": {"equals": "Email"}},
        {"property": "Status", "select": {"equals": "Contacted"}}]})
    followups = []
    for p in contacted:
        touches = int(p["properties"]["Touches"]["number"] or 0)
        if touches in (1, 2) and _due(p, touches + 1, today):
            followups.append((p, touches + 1))
    return followups[:config.MAX_FOLLOWUPS_PER_RUN], whatsapp, queued, done_today, owed


def _address(page):
    raw = page["properties"]["Email"]["email"] or ""
    clean = scrape.clean_emails([raw])
    return clean[0] if clean else ""


def run(region, max_new=None):
    today = config.region_today(region)
    followups, whatsapp, queued, sent_before, owed = plan(region, today, max_new)
    if not followups and owed == 0:
        print(f"{region}: {sent_before} new emails already sent today and no follow-ups due. Nothing to do.")
        return
    goal = config.REGION_DAILY_NEW[region]
    # WhatsApp first: these just get counted for today and wait for the Sunday bulk send (no follow-ups)
    for page in whatsapp:
        notion.update_page(page["id"], {"Outreach day": notion.date(today)})
    wa_saved = len(whatsapp)
    email_owed = owed - wa_saved
    to_email = min(email_owed, len(queued))
    notify.fyi(f"🚀 {region.upper()} outreach started",
                f"{wa_saved} WhatsApp leads saved for Sunday, now sending {to_email} emails "
                f"and {len(followups)} follow-ups ({sent_before}/{goal} were already done today). "
                f"About {(to_email + len(followups)) * 3 // 2} min.", tags="rocket")

    sent_new, sent_fu, skipped, errors = 0, 0, 0, []
    paused = ""
    mailer = Mailer()
    try:
        def limit_hit(err):
            return config.redact(err)[:200] if is_limit(err) else ""

        for page, touch in followups:
            to = _address(page)
            if not to:
                continue
            try:
                mailer.send(to, "Re: " + notion.read(page, "Subject"),
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
            if sent_new >= email_owed:
                break
            to = _address(page)
            if not to:
                notion.update_page(page["id"], {"Status": notion.select("Bounced"),
                                                "Reply": notion.text("Not a usable email address")})
                skipped += 1
                continue
            try:
                mailer.send(to, notion.read(page, "Subject"), notion.read(page, "Message"))
                notion.update_page(page["id"], {
                    "Email": {"email": to},
                    "Status": notion.select("Contacted"),
                    "Touches": {"number": 1},
                    "Date contacted": notion.date(today),
                    "Last touch": notion.date(today),
                    "Outreach day": notion.date(today),
                })
                sent_new += 1
                print(f"sent {sent_new}/{email_owed}: {to}")
            except smtplib.SMTPRecipientsRefused:
                notion.update_page(page["id"], {"Status": notion.select("Bounced")})
                skipped += 1
            except Exception as e:
                errors.append(f"{to}: {config.redact(e)[:100]}")
                paused = limit_hit(e)
                if paused:
                    break
            time.sleep(random.randint(*config.SEND_DELAY_SEC))
    finally:
        mailer.close()

    wa_waiting = len(notion.query(config.LEADS_DB, {"and": [
        _countries_filter(config.REGIONS[region]),
        {"property": "Channel", "select": {"equals": "WhatsApp"}},
        {"property": "Status", "select": {"equals": "Queued"}}]}))
    total_new = sent_before + wa_saved + sent_new
    short = total_new < goal and max_new is None
    result = "Failed" if paused else ("Partial" if short or errors else "Done")
    notes = (f"{region.upper()}: {total_new}/{goal} contacted today. This run: {wa_saved} WhatsApp saved, "
             f"{sent_new} first emails, {sent_fu} follow-ups. {wa_waiting} WhatsApp leads waiting for Sunday. "
             f"Bad addresses skipped: {skipped}. Errors: {len(errors)}. Reconnects: {mailer.reconnects}")
    if short and not paused:
        notes += f". SHORT: ran out of queued {region.upper()} leads."
    if errors:
        notes += " | " + "; ".join(errors[:3])
    if paused:
        notes = f"GMAIL PAUSED SENDING, stopped to protect the account: {paused}. " + notes
    notion.log_run(f"Send {region} {today}", "Directories send", goal, total_new, result, notes)
    if paused:
        notify.action("Gmail stopped sending, run paused", notes, tags="no_entry")
    else:
        flag = "⚠️ " if short else "✉️ "
        notify.fyi(f"{flag}{region.upper()} done {total_new}/{goal}: {sent_new} emails + {wa_saved} WhatsApp",
                   notes, tags="email")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, choices=list(config.REGIONS))
    ap.add_argument("--max", type=int, default=None, help="test: at most this many new emails this run")
    args = ap.parse_args()
    try:
        run(args.region, args.max)
    except Exception as e:
        traceback.print_exc()
        notify.fyi(f"⚠️ {args.region.upper()} email run crashed (Claude will check)", "Nothing for you to do. " + config.redact(e)[:250], tags="warning")
        raise


if __name__ == "__main__":
    main()
