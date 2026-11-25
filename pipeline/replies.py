"""Every 2 hours: read lead replies in the outreach Gmail and move every lead toward a booked Zoom.

The full reply playbook (which reply gets which answer) lives in pipeline/playbook.py. In short:
- Asked for the audit      -> the audit is written from their website and SENT, ending with the booking link
- Wants a call / a time    -> booking link (time proposed also pings Azeem)
- Details / price question -> plain answer + free audit offer + booking link (no invented prices)
- Not now                  -> friendly "I'll check back", Status Later, one check-in after 30 days
- Referral                 -> thanks + ask for the intro, ping Azeem
- Not interested           -> Status Not interested, never contacted again
- Sensitive / unclear      -> Gmail draft + loud ping, nothing sent
- Bounce -> Bounced. Out of office -> ignored, follow-ups continue.
- Calendly "New Event" email for a lead -> Status Meeting + loud ping (closed lead: Azeem takes the Zoom).
Follow-up replies are handled too (whole conversation as context), up to playbook.MAX_AUTO_REPLIES per lead.
Interested leads that don't book get 2 nudges (day 3, day 7). Later leads get one check-in at day 30.
Outbox: text in a lead's Notion "Send next" field is sent in the same thread on the next run, then cleared.
Every message in and out is logged in the lead's "Conversation" field (newest first).

Reads the inbox read-only (BODY.PEEK): never marks anything read.
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

from email.utils import parsedate_to_datetime

from . import ai, config, notion, notify, playbook, scrape

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


def _calendly_invitee(msg, body):
    sender = parseaddr(msg.get("From", ""))[1].lower()
    subject = msg.get("Subject", "")
    if "calendly" not in sender or not subject.lower().startswith("new event"):
        return None
    for addr in EMAIL_RE.findall(body):
        if "calendly" not in addr.lower() and addr.lower() != config.GMAIL_ADDRESS.lower():
            return addr.lower()
    return None




# ---------- conversation log + AI ----------
def _log(page, line):
    """Prepend one line to the lead's Conversation field (newest first, trimmed to Notion's limit)."""
    old = notion.read(page, "Conversation") or ""
    new = f"[{dt.date.today().isoformat()}] {line.strip()}\n{old}".strip()
    page["properties"]["Conversation"] = {"type": "rich_text", "rich_text": [{"plain_text": new[:1900]}]}
    return notion.text(new[:1900])


def _site_text(page):
    site = (page["properties"].get("Website") or {}).get("url") or ""
    if not site:
        return ""
    try:
        return scrape.fetch_site(site)["text"][:5000]
    except Exception as e:
        print(f"[replies] could not read site for audit: {config.redact(e)[:100]}")
        return ""


def classify(page, theirs, site_text=""):
    first = config.SENDER_NAME.split()[0]
    prompt = playbook.REPLY_PROMPT.format(
        sender=config.SENDER_NAME, first=first, link=BOOKING_LINK or "[ADD BOOKING LINK]",
        company=notion.read(page, "Business") or "", niche=notion.read(page, "Niche") or "",
        city=notion.read(page, "City") or "", country=notion.read(page, "Country") or "",
        lead_first=notion.read(page, "First name") or "unknown",
        ours=(notion.read(page, "Message") or "")[:2500], history=(notion.read(page, "Conversation") or "")[:2500],
        theirs=theirs[:3000], site=site_text[:5000])
    try:
        data = ai._call(prompt)
        res = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
        if isinstance(res, list):
            res = next((x for x in res if isinstance(x, dict)), {})
        res["intent"] = playbook.normalize(res.get("intent"))
        return res
    except Exception as e:
        print(f"[replies] AI could not read reply: {config.redact(e)[:150]}")
        return {"intent": "retry", "summary": theirs[:150], "draft": ""}


def wants_site(theirs):
    """Only fetch their website when the audit is likely needed (saves time on other replies)."""
    return bool(re.search(r"\b(yes|sure|audit|sample|send|show|example|go ahead|please do|interested)\b",
                          theirs or "", re.I))


def _msg_date(msg):
    try:
        return parsedate_to_datetime(msg.get("Date")).date()
    except Exception:
        return dt.date.today()


def is_new_reply(page, msg):
    """True if this message hasn't been handled yet. First replies (Status Contacted) are always new.
    Later replies: Message-ID differs from the last one handled; for older leads with no ID stored, only
    messages dated after our last touch count (so nothing already answered is answered twice)."""
    status = notion.read(page, "Status")
    mid = (msg.get("Message-ID") or "").strip()
    last_id = (notion.read(page, "Last reply ID") or "").strip()
    if mid and mid == last_id:
        return False
    if status == "Contacted":
        return True
    if last_id:
        return True
    last_touch = notion.read(page, "Last touch")
    return bool(last_touch) and _msg_date(msg).isoformat() > last_touch


def _num(page, name):
    return int(notion.read(page, name) or 0)


# ---------- nudges / check-ins ----------
def send_nudges():
    """Interested leads that haven't booked: nudge 1 at their next-touch date, nudge 2 four days later.
    Later leads: one check-in at their next-touch date."""
    if not BOOKING_LINK:
        return 0
    today = dt.date.today()
    sent = 0
    for status in ("Interested", "Later"):
        due = notion.query(config.LEADS_DB, {"and": [
            {"property": "Status", "select": {"equals": status}},
            {"property": "Next follow-up", "date": {"on_or_before": today.isoformat()}}]})
        for page in due:
            to = page["properties"]["Email"]["email"]
            if not to:
                continue
            n = _num(page, "Nudges")
            if status == "Interested" and n >= len(playbook.NUDGES):
                notion.update_page(page["id"], {"Next follow-up": {"date": None}})
                continue
            tpl = playbook.NUDGES[n] if status == "Interested" else playbook.LATER_CHECKIN
            body = tpl.format(first=notion.read(page, "First name") or "there",
                              company=notion.read(page, "Business"), link=BOOKING_LINK,
                              sender=config.SENDER_NAME.split()[0])
            nxt = None
            if status == "Interested" and n < len(playbook.NUDGE_GAPS):
                nxt = notion.date(today + dt.timedelta(days=playbook.NUDGE_GAPS[n]))
            try:
                send_reply(to, notion.read(page, "Subject") or "Quick call", body)
                label = f"nudge {n + 1}" if status == "Interested" else "30-day check-in"
                notion.update_page(page["id"], {"Next follow-up": nxt or {"date": None}, "Last touch": notion.date(),
                                                "Nudges": {"number": n + 1},
                                                "Conversation": _log(page, f"WE ({label}): {body[:300]}")})
                sent += 1
            except Exception as e:
                print(f"[nudge] {to}: {config.redact(e)[:120]}")
    return sent


# ---------- outbox: hand-written emails from Notion "Send next" ----------
def send_outbox(latest):
    """Send each lead's "Send next" text in the same thread as their latest message, then clear it."""
    pages = notion.query(config.LEADS_DB, {"property": "Send next", "rich_text": {"is_not_empty": True}})
    sent = []
    for page in pages:
        status = notion.read(page, "Status")
        to = page["properties"]["Email"]["email"]
        body = (notion.read(page, "Send next") or "").strip()
        if not to or not body or status in ("Not interested", "Bounced"):
            continue
        body = body.replace("{link}", BOOKING_LINK)
        msg = latest.get(to.lower())
        reply_to = parseaddr(msg.get("From", ""))[1].lower() if msg else to
        subject = msg.get("Subject", "") if msg else (notion.read(page, "Subject") or "Quick follow-up")
        try:
            send_reply(reply_to, subject, body, msg.get("Message-ID") if msg else None)
        except Exception as e:
            print(f"[outbox] send failed: {config.redact(e)[:120]}")
            continue
        props = {"Send next": notion.text(""), "Last touch": notion.date(),
                 "Auto replies": {"number": _num(page, "Auto replies") + 1},
                 "Conversation": _log(page, f"WE (hand-written): {body[:400]}")}
        if msg and msg.get("Message-ID"):
            props["Last reply ID"] = notion.text(msg.get("Message-ID").strip())
        if status in ("Contacted", "Replied"):
            props["Status"] = notion.select("Interested")
        if status in ("Contacted", "Replied", "Interested") and not notion.read(page, "Next follow-up"):
            props["Next follow-up"] = notion.date(dt.date.today() + dt.timedelta(days=3))
        notion.update_page(page["id"], props)
        sent.append(notion.read(page, "Business"))
    return sent


# ---------- one reply ----------
def handle_reply(box, page, msg, sender, body, counts):
    name = notion.read(page, "Business")
    status = notion.read(page, "Status")
    res = classify(page, body, _site_text(page) if wants_site(body) else "")
    intent = res.get("intent", "other")
    if intent == "retry":
        return False                       # AI down: the next run (2 h) reads it again
    summary = (res.get("summary") or body[:150]).strip()
    mid = (msg.get("Message-ID") or "").strip()
    step = playbook.plan(intent)
    convo = _log(page, f"THEM ({intent}): {body[:300]}")
    base = {"Reply": notion.text(summary), "Conversation": convo}
    if mid:
        base["Last reply ID"] = notion.text(mid)

    if intent == "not_interested":
        notion.update_page(page["id"], {**base, "Status": notion.select("Not interested"),
                                        "Next follow-up": {"date": None}})
        counts["unsub"] += 1
        return True

    draft = (res.get("draft") or "").strip()
    subject = msg.get("Subject", notion.read(page, "Subject") or "")
    auto_n = _num(page, "Auto replies")
    safe = (step["send"] and BOOKING_LINK and draft and BOOKING_LINK in draft
            and auto_n < playbook.MAX_AUTO_REPLIES)
    if safe:
        try:
            send_reply(sender, subject, draft, msg.get("Message-ID"))
            props = {**base, "Status": notion.select(step["status"]), "Last touch": notion.date(),
                     "Auto replies": {"number": auto_n + 1}, "Nudges": {"number": 0},
                     "Next follow-up": notion.date(dt.date.today() + dt.timedelta(days=step["days"])),
                     "Conversation": _log(page, f"WE ({intent}): {draft[:400]}")}
            notion.update_page(page["id"], props)
            counts["auto"].append(f"{name} ({intent})")
            if step["ping"]:
                notify.action(f"{name}: {intent.replace('_', ' ')}", f"{summary}\nAnswered with the booking link. "
                              "Watch for the Calendly booking.", tags="fire")
            else:
                notify.fyi(f"🔥 {name} replied ({intent.replace('_', ' ')}), answered (nothing to do)", summary,
                           tags="fire")
            return True
        except Exception as e:
            print(f"[replies] auto-send failed: {config.redact(e)[:120]}")
    # risky, unclear, over the auto-reply limit, or the send failed: keep it as a draft
    notion.update_page(page["id"], {**base, "Status": notion.select("Replied"), "Last touch": notion.date()})
    try:
        if draft:
            save_draft(box, sender, subject, draft, msg.get("Message-ID"))
        note = "Draft is in the outreach Gmail > Drafts." if draft else "Reply from the outreach Gmail."
    except Exception:
        note = "Reply from the outreach Gmail."
    counts["held"].append(name)
    if len(body.strip()) >= 3:             # blank reply: logged in Notion, no ping
        notify.action(f"{name} replied ({intent})", f"{summary}\n{note} Claude will also pick it up.",
                      tags="speech_balloon")
    return True


ACTIVE = ("Contacted", "Replied", "Interested", "Later")


def run():
    nudged = send_nudges()
    pages = []
    for status in ACTIVE:
        pages += notion.query(config.LEADS_DB, {"property": "Status", "select": {"equals": status}})
    leads = {p["properties"]["Email"]["email"].lower(): p for p in pages if p["properties"]["Email"]["email"]}
    if not leads:
        return
    since = (dt.date.today() - dt.timedelta(days=3)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    box.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    box.select("INBOX", readonly=True)
    _, ids = box.search(None, f'(SINCE "{since}")')

    counts = {"auto": [], "held": [], "unsub": 0}
    meetings, bounced = [], []
    latest = {}                            # lead key -> (msg, sender, body): newest message from each lead
    for mid in ids[0].split():             # IMAP returns oldest first, so later messages overwrite
        _, data = box.fetch(mid, "(BODY.PEEK[])")
        msg = email.message_from_bytes(data[0][1])
        sender = parseaddr(msg.get("From", ""))[1].lower()
        body = _body(msg)

        invitee = _calendly_invitee(msg, body)
        if invitee:
            page = leads.get(invitee)
            if page and notion.read(page, "Status") != "Meeting":
                name = notion.read(page, "Business")
                notion.update_page(page["id"], {"Status": notion.select("Meeting"), "Next follow-up": {"date": None},
                                                "Conversation": _log(page, "BOOKED a Zoom via Calendly")})
                page["properties"]["Status"] = {"type": "select", "select": {"name": "Meeting"}}
                meetings.append(name)
                notify.action(f"Zoom booked with {name}", f"{invitee} booked a call. Check your calendar and prepare.",
                              tags="calendar")
            continue

        if "mailer-daemon" in sender or "postmaster" in sender:
            for addr in EMAIL_RE.findall(body):
                page = leads.get(addr.lower())
                if page and notion.read(page, "Status") == "Contacted":
                    notion.update_page(page["id"], {"Status": notion.select("Bounced")})
                    page["properties"]["Status"] = {"type": "select", "select": {"name": "Bounced"}}
                    bounced.append(addr)
            continue

        key = match_lead(leads, sender, msg.get("Subject", ""))
        if not key or is_auto_reply(msg, body):
            continue                       # not a lead, or out of office: follow-ups continue
        latest[key] = (msg, sender, body)

    outbox = send_outbox({k: v[0] for k, v in latest.items()})
    for key, (msg, sender, body) in latest.items():
        page = leads[key]
        if notion.read(page, "Status") not in ACTIVE or notion.read(page, "Business") in outbox:
            continue
        if not is_new_reply(page, msg):
            continue
        if key != sender:
            print("[replies] matched a reply from a different address to its lead")
        handle_reply(box, page, msg, sender, body, counts)
    box.logout()

    auto, held, unsub = counts["auto"], counts["held"], counts["unsub"]
    if auto or held or meetings or bounced or unsub or nudged or outbox:
        notes = (f"Meetings booked: {', '.join(meetings) or 'none'}. Answered automatically: "
                 f"{', '.join(auto) or 'none'}. Hand-written sent: {', '.join(outbox) or 'none'}. "
                 f"Held (draft): {', '.join(held) or 'none'}. Nudges/check-ins sent: {nudged}. "
                 f"Not interested: {unsub}. Bounced: {len(bounced)}.")
        notion.log_run(f"Reply check {dt.datetime.now():%Y-%m-%d %H:%M}", "Directories send",
                       0, len(auto) + len(meetings) + len(outbox), "Done", notes)


if __name__ == "__main__":
    run()
