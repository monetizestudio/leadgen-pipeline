"""Every 2 hours: check Gmail for replies and bounces from leads, update Notion, ping your phone.

Only reads messages (BODY.PEEK), never marks them read or changes your inbox.
"""
import datetime as dt
import email
import imaplib
import re
from email.utils import parseaddr

from . import config, notion, notify

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NEGATIVE = re.compile(r"\b(not interested|unsubscribe|remove me|stop emailing|no thanks|no thank you|^no\b)",
                      re.MULTILINE)


def _contacted_by_email():
    pages = notion.query(config.LEADS_DB, {"property": "Status", "select": {"equals": "Contacted"}})
    return {p["properties"]["Email"]["email"].lower(): p for p in pages if p["properties"]["Email"]["email"]}


def _body(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
            except Exception:
                return ""
    return ""


def run():
    leads = _contacted_by_email()
    if not leads:
        return
    since = (dt.date.today() - dt.timedelta(days=3)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    box.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    box.select("INBOX", readonly=True)
    _, ids = box.search(None, f'(SINCE "{since}")')

    replied, bounced = [], []
    for mid in ids[0].split():
        _, data = box.fetch(mid, "(BODY.PEEK[])")
        msg = email.message_from_bytes(data[0][1])
        sender = parseaddr(msg.get("From", ""))[1].lower()
        body = _body(msg)

        if "mailer-daemon" in sender or "postmaster" in sender:
            for addr in EMAIL_RE.findall(body):
                page = leads.pop(addr.lower(), None)
                if page:
                    notion.update_page(page["id"], {"Status": notion.select("Bounced")})
                    bounced.append(addr)
            continue

        page = leads.pop(sender, None)
        if not page:
            continue
        first_lines = body.strip().lower()[:300]
        status = "Not interested" if NEGATIVE.search(first_lines) else "Replied"
        notion.update_page(page["id"], {"Status": notion.select(status)})
        name = notion.read(page, "Business")
        if status == "Replied":
            replied.append(name)
            notify.push(f"🔥 Reply from {name}", f"{sender}: {body.strip()[:200]}", priority="high",
                        tags="fire")
    box.logout()
    if replied or bounced:
        notes = f"Replies: {', '.join(replied) or 'none'}. Bounced: {len(bounced)}"
        notion.log_run(f"Reply check {dt.datetime.now():%Y-%m-%d %H:%M}", "Directories send",
                       0, len(replied), "Done", notes)


if __name__ == "__main__":
    run()
