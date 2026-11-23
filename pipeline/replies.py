"""Every 2 hours: check Gmail for replies and bounces from leads.

- Bounce          -> Status Bounced
- Not interested  -> Status Not interested (never contacted again)
- Out of office   -> ignored, follow-ups continue
- Interested / wants details / question -> Gemini writes a reply that answers them and steers to a
  quick Zoom call (booking link). It is SENT automatically, in the same email thread.
  Status -> Interested. If they don't book in 3 days, one nudge is sent.
- Sensitive (angry, complaint, legal, asks for a phone call, anything risky) -> saved as a Gmail draft
  and your phone gets a ping instead of auto-sending.
- Calendly "New Event" email for a lead -> Status Meeting + 📅 ping. That's your cue: Zoom call.

Reads your inbox read-only (BODY.PEEK): never marks anything read.
"""
import datetime as dt
import email
import imaplib
import json
import os
import re
import smtplib
import time
from email.message import EmailMessage
from email.utils import parseaddr

import requests

from . import ai, config, notion, notify

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
             "yahoo.co.uk", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "msn.com",
             "hotmail.co.uk", "outlook.co.uk", "gmx.com", "zoho.com", "yandex.com"}


def _domain(addr_or_url):
    d = (addr_or_url or "").lower().split("@")[-1]
    d = re.sub(r"^https?://", "", d).split("/")[0]
    return d[4:] if d.startswith("www.") else d


def _norm_subject(s):
    s = (s or "").strip().lower()
    while True:
        t = re.sub(r"^(re|fwd?|aw|sv)\s*:\s*", "", s)
        if t == s:
            return re.sub(r"\s+", " ", s)
        s = t


def match_lead(leads, sender, subject):
    """Find the lead a reply belongs to: exact address, then same company domain
    (owner answering from their own address), then the subject of our email."""
    if sender in leads:
        return sender
    dom = _domain(sender)
    if dom and dom not in FREE_MAIL:
        for key, page in leads.items():
            site = (page["properties"].get("Website") or {}).get("url") or ""
            if _domain(key) == dom or _domain(site) == dom:
                return key
    subj = _norm_subject(subject)
    if len(subj) >= 12:
        for key, page in leads.items():
            if _norm_subject(notion.read(page, "Subject")) == subj:
                return key
    return None
BOOKING_LINK = os.environ.get("BOOKING_LINK", "").strip()

REPLY_PROMPT = """You are writing on behalf of {sender}, who runs Monetize Studio (Meta ads + cinematic video
content for architecture, interior design and real estate businesses).

We emailed this lead:
\"\"\"{ours}\"\"\"

They replied:
\"\"\"{theirs}\"\"\"

Classify their reply and, if useful, draft {sender}'s answer. Reply with JSON only:
- "intent": one of "interested", "wants_details", "question", "not_interested", "out_of_office",
  "sensitive", "other". Use "sensitive" if they are angry, complain, mention legal/spam/reporting,
  ask for a phone number or call right now, ask for something we can't answer, or you are unsure.
- "summary": one short line, what they said
- "draft": for interested / wants_details / question: a short, warm, human reply (60-110 words) that
  answers what they asked in plain words, then invites them to a quick Zoom call using this
  booking link: {link}
  Rules: never invent prices, results, client names or guarantees. If they ask about price, say it depends
  on scope and is easiest to cover on the call. No "I hope this finds you well". Sign off as {first}.
  For other intents (including sensitive) still write a careful, polite draft for a human to check.
"""


def _body(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
            except Exception:
                return ""
            # keep only their new text, not the quoted thread below it
            return re.split(r"\n(?:On .+ wrote:|-{2,}\s*Original Message|>)", text, maxsplit=1)[0].strip()
    return ""


def classify(ours, theirs):
    prompt = REPLY_PROMPT.format(sender=config.SENDER_NAME, first=config.SENDER_NAME.split()[0],
                                 ours=ours[:2500], theirs=theirs[:3000],
                                 link=BOOKING_LINK or "[ADD BOOKING LINK]")
    try:
        data = ai._call(prompt)
        return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as e:
        print(f"[replies] AI could not read reply: {config.redact(e)[:150]}")
        return {"intent": "retry", "summary": theirs[:150], "draft": ""}


OOO_RE = re.compile(r"out of (the )?office|on (annual )?leave|automatic reply|auto[- ]?reply|away from (the )?office"
                    r"|limited access to (my )?email|no access to email", re.I)


def is_auto_reply(msg, body):
    """Out-of-office / autoresponder, detected without AI so it never gets held for Azeem."""
    auto = (msg.get("Auto-Submitted", "no") or "no").lower()
    if auto != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    return bool(OOO_RE.search(msg.get("Subject", "") or "") or OOO_RE.search((body or "")[:600]))


def save_draft(box, to, subject, body, in_reply_to):
    msg = EmailMessage()
    msg["From"] = f"{config.SENDER_NAME} <{config.GMAIL_ADDRESS}>"
    msg["To"] = to
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    box.append('"[Gmail]/Drafts"', r"(\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())


NUDGE = ("Hi {first}, just making it easy: here's the link to grab a quick call whenever suits you. "
         "I'll come prepared with a few ideas for {company}.\n\n{link}\n\n{sender}")


def _smtp():
    s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60)
    s.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    return s


def send_reply(to, subject, body, in_reply_to=None):
    msg = EmailMessage()
    msg["From"] = f"{config.SENDER_NAME} <{config.GMAIL_ADDRESS}>"
    msg["To"] = to
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    with _smtp() as s:
        s.send_message(msg)


def send_nudges():
    """Interested leads that haven't booked 3 days after our reply get one nudge."""
    if not BOOKING_LINK:
        return 0
    today = dt.date.today().isoformat()
    due = notion.query(config.LEADS_DB, {"and": [
        {"property": "Status", "select": {"equals": "Interested"}},
        {"property": "Next follow-up", "date": {"on_or_before": today}}]})
    sent = 0
    for page in due:
        to = page["properties"]["Email"]["email"]
        if not to:
            continue
        body = NUDGE.format(first=notion.read(page, "First name") or "there",
                            company=notion.read(page, "Business"), link=BOOKING_LINK,
                            sender=config.SENDER_NAME)
        try:
            send_reply(to, notion.read(page, "Subject") or "Quick call", body)
            notion.update_page(page["id"], {"Next follow-up": {"date": None}, "Last touch": notion.date()})
            sent += 1
        except Exception as e:
            print(f"[nudge] {to}: {config.redact(e)[:120]}")
    return sent


def _calendly_invitee(msg, body):
    sender = parseaddr(msg.get("From", ""))[1].lower()
    subject = msg.get("Subject", "")
    if "calendly" not in sender or not subject.lower().startswith("new event"):
        return None
    for addr in EMAIL_RE.findall(body):
        if "calendly" not in addr.lower() and addr.lower() != config.GMAIL_ADDRESS.lower():
            return addr.lower()
    return None


def run():
    nudged = send_nudges()
    pages = []
    for status in ("Contacted", "Replied", "Interested"):
        pages += notion.query(config.LEADS_DB, {"property": "Status", "select": {"equals": status}})
    leads = {p["properties"]["Email"]["email"].lower(): p for p in pages if p["properties"]["Email"]["email"]}
    if not leads:
        return
    since = (dt.date.today() - dt.timedelta(days=3)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    box.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    box.select("INBOX", readonly=True)
    _, ids = box.search(None, f'(SINCE "{since}")')

    auto, held, meetings, bounced, unsub = [], [], [], [], 0
    for mid in ids[0].split():
        _, data = box.fetch(mid, "(BODY.PEEK[])")
        msg = email.message_from_bytes(data[0][1])
        sender = parseaddr(msg.get("From", ""))[1].lower()
        body = _body(msg)

        invitee = _calendly_invitee(msg, body)
        if invitee:
            page = leads.pop(invitee, None)
            if page:
                name = notion.read(page, "Business")
                notion.update_page(page["id"], {"Status": notion.select("Meeting"),
                                                "Next follow-up": {"date": None}})
                meetings.append(name)
                notify.action(f"Zoom booked with {name}", f"{invitee} booked a call. Check your calendar and prepare.",
                              tags="calendar")
            continue

        if "mailer-daemon" in sender or "postmaster" in sender:
            for addr in EMAIL_RE.findall(body):
                page = leads.pop(addr.lower(), None)
                if page and notion.read(page, "Status") == "Contacted":
                    notion.update_page(page["id"], {"Status": notion.select("Bounced")})
                    bounced.append(addr)
            continue

        key = match_lead(leads, sender, msg.get("Subject", ""))
        page = leads.get(key) if key else None
        if not page or notion.read(page, "Status") != "Contacted":
            continue                       # only first replies here; later back-and-forth is Azeem's
        name = notion.read(page, "Business")
        if is_auto_reply(msg, body):
            continue                       # out of office: keep following up later
        res = classify(notion.read(page, "Message") or "", body)
        intent = res.get("intent", "other")
        if intent == "retry":
            continue                       # AI down: leave it, the next run (2 h) reads it again
        if intent == "out_of_office":
            continue                       # keep following up later
        leads.pop(key, None)
        if key != sender:
            print(f"[replies] matched {sender} to lead {key} (different address)")
        summary = (res.get("summary") or body[:150]).strip()

        if intent == "not_interested":
            notion.update_page(page["id"], {"Status": notion.select("Not interested"),
                                            "Reply": notion.text(summary)})
            unsub += 1
            continue

        draft = (res.get("draft") or "").strip()
        subject = msg.get("Subject", notion.read(page, "Subject") or "")
        safe = intent in ("interested", "wants_details", "question") and BOOKING_LINK and draft \
            and BOOKING_LINK in draft
        if safe:
            try:
                send_reply(sender, subject, draft, msg.get("Message-ID"))
                notion.update_page(page["id"], {
                    "Status": notion.select("Interested"), "Reply": notion.text(summary),
                    "Last touch": notion.date(),
                    "Next follow-up": notion.date(dt.date.today() + dt.timedelta(days=3))})
                auto.append(name)
                notify.fyi(f"🔥 {name} replied, booking link sent (nothing to do)", summary, tags="fire")
                continue
            except Exception as e:
                print(f"[replies] auto-send failed for {sender}: {config.redact(e)[:120]}")
        # anything risky or failed: keep it for Azeem as a draft
        notion.update_page(page["id"], {"Status": notion.select("Replied"), "Reply": notion.text(summary),
                                        "Last touch": notion.date()})
        try:
            if draft:
                save_draft(box, sender, subject, draft, msg.get("Message-ID"))
            note = "Needs you: draft is in Gmail > Drafts." if draft else "Needs you: reply from Gmail."
        except Exception:
            note = "Needs you: reply from Gmail."
        held.append(name)
        if len(body.strip()) < 3:
            continue                       # blank reply: logged in Notion, no ping
        notify.action(f"{name} replied, answer them", f"{summary}\n{note}", tags="speech_balloon")
    box.logout()

    if auto or held or meetings or bounced or unsub or nudged:
        notes = (f"Meetings booked: {', '.join(meetings) or 'none'}. Auto-replied with booking link: "
                 f"{', '.join(auto) or 'none'}. Held for Azeem: {', '.join(held) or 'none'}. "
                 f"Nudges sent: {nudged}. Not interested: {unsub}. Bounced: {len(bounced)}.")
        notion.log_run(f"Reply check {dt.datetime.now():%Y-%m-%d %H:%M}", "Directories send",
                       0, len(auto) + len(meetings), "Done", notes)


if __name__ == "__main__":
    run()
