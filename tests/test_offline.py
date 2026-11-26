"""Offline checks: parsing, templates and message building (no network needed)."""
from pipeline import ai, config, find, messages, scrape

HTML = """<html><body>
<a href="mailto:hello@studioarc.co.uk?subject=Hi">Email us</a>
<a href="https://wa.me/447700900123">WhatsApp</a>
<p>Contact: info@studioarc.co.uk or press@other.com. Logo: logo@2x.png</p>
<script>var x="tracker@sentry.io"</script>
</body></html>"""


def test_extract_contacts():
    emails, was, text = scrape.extract_contacts(HTML, "https://studioarc.co.uk")
    assert emails[0].endswith("@studioarc.co.uk")
    assert "tracker@sentry.io" not in emails
    assert not any(e.endswith(".png") for e in emails)
    assert was == ["447700900123"]
    assert "Contact" in text


def test_normalize_site():
    assert scrape.normalize_site("www.Example.com/about") == "https://example.com"
    assert scrape.normalize_site("http://www.a.co.uk") == "https://a.co.uk"
    assert scrape.normalize_site("") == ""


def test_templates_fill():
    f = messages.fields("Arc Studio", "", "Interior Design", "London", "Saw the Hackney loft project.")
    subject, body = messages.first_email(f, "Interior Design")
    assert subject == "Arc Studio's project videos"
    assert body.startswith("Hi there,") and "interior design" in body and "{" not in body
    re_subj, re_body = messages.first_email(
        messages.fields("Prime Homes", "Sara", "Real Estate", "Dubai", "x"), "Real Estate")
    assert "Dubai" in re_body and "Sara" in re_body
    assert "{" not in messages.whatsapp(f)
    for t in (2, 3):
        assert "{" not in messages.followup_email(f, t)
    assert config.OPT_OUT in messages.footer()


def test_qualify_rules():
    good = {"english": True, "in_niche": True, "medium_sized": True, "fit_score": 7, "observation": "x"}
    assert ai.is_qualified(good)
    assert not ai.is_qualified({**good, "medium_sized": False})
    assert not ai.is_qualified({**good, "fit_score": 4})
    assert not ai.is_qualified(None)


def test_build_props_prefers_whatsapp():
    lead = {"name": "Arc", "website": "https://arc.com", "niche": "Architecture", "city": "Dubai",
            "country": "UAE"}
    res = {"fit_score": 8, "fit_notes": "n", "first_name": "Omar", "observation": "Saw the villa."}
    props, ch = find.build_props(lead, res, {"emails": ["a@arc.com"], "whatsapp": ["971500000000"]})
    assert ch == "WhatsApp" and props["WhatsApp"]["phone_number"] == "+971500000000"
    props, ch = find.build_props(lead, res, {"emails": ["a@arc.com"], "whatsapp": []})
    assert ch == "Email" and props["Email"]["email"] == "a@arc.com"


def test_city_rotation_covers_all():
    cities = find.tonight_cities()
    want = sum(len(config.CITIES[c]) for cs in config.SEARCH_COUNTRIES.values() for c in cs)
    assert len(cities) == want
    assert len({c[1] for c in cities}) == len(cities)
    for country, city, (lat, lon) in cities:
        assert -90 < lat < 90 and -180 < lon < 180
    assert {c[0] for c in find.region_cities("na")} == {"US"}
    assert {c[0] for c in find.region_cities("uae")} == {"UAE"}


def test_targets_are_100_a_day_25_per_region():
    assert config.REGION_DAILY_NEW == {"australia": 25, "uae": 25, "uk": 25, "na": 25}
    assert config.DAILY_TARGET == 100
    assert set(config.REGION_TZ) == set(config.REGIONS) == set(config.SEARCH_COUNTRIES)
    assert config.region_of("Canada") == "na" and config.region_of("UK") == "uk"
    for r in config.REGIONS:
        assert config.region_today(r)


def test_redact_hides_keys():
    config.GEMINI_API_KEY = "AQ.secret123"
    msg = config.redact("404 for url: https://x/models/m:generateContent?key=AQ.secret123")
    assert "secret123" not in msg and "key=***" in msg
    assert "secret" not in config.redact("token AQ.secret123 leaked")
    config.GEMINI_API_KEY = ""


def test_pick_model_newest_flash():
    names = ["gemini-2.0-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.5-pro",
             "gemini-3.8-flash-preview-09", "gemini-3.5-flash-image", "text-embedding-004"]
    assert ai.pick_model(names) == "gemini-3.5-flash"   # stable beats newer preview
    assert ai.pick_model(names + ["gemini-flash-latest"]) == "gemini-flash-latest"
    ranked = ai.rank_models(names)
    assert ranked.index("gemini-3.8-flash-preview-09") > ranked.index("gemini-3.5-flash-lite")
    assert ai.pick_model(["gemini-4.0-flash-preview"]) == "gemini-4.0-flash-preview"
    assert ai.pick_model(["gemini-3.5-pro"]) is None


def test_verdict_labels():
    base = {"english": True, "in_niche": True, "medium_sized": True, "size": "medium",
            "fit_score": 8, "observation": "x", "fit_notes": "ok"}
    assert ai.verdict(base)[0] == "Qualified"
    assert ai.verdict({**base, "english": False})[0] == "Not English"
    assert ai.verdict({**base, "in_niche": False})[0] == "Not in niche"
    assert ai.verdict({**base, "medium_sized": False, "size": "large"})[0] == "Too big"
    assert ai.verdict({**base, "medium_sized": False, "size": "small"})[0] == "Too small"
    assert ai.verdict({**base, "fit_score": 3})[0] == "Low score"
    assert ai.verdict(None)[0] == "AI error"


def test_reply_body_strips_quoted_thread():
    from email.message import EmailMessage
    from pipeline import replies
    m = EmailMessage()
    m.set_content("Yes, send me details please.\n\nOn Mon, Azeem wrote:\n> Hi there, old message")
    assert replies._body(m) == "Yes, send me details please."


def test_calendly_invitee_detection():
    from email.message import EmailMessage
    from pipeline import replies, config as cfg
    cfg.GMAIL_ADDRESS = "me@gmail.com"
    m = EmailMessage()
    m["From"] = "Calendly <notifications@calendly.com>"
    m["Subject"] = "New Event: Omar Khan - 03:00pm Mon, Nov 23, 2026 - 15 Minute Meeting"
    body = "Invitee: Omar Khan\nInvitee Email: omar@arcstudio.ae\nEvent Date/Time: ..."
    assert replies._calendly_invitee(m, body) == "omar@arcstudio.ae"
    m2 = EmailMessage(); m2["From"] = "omar@arcstudio.ae"; m2["Subject"] = "Re: hi"
    assert replies._calendly_invitee(m2, body) is None


def test_call_falls_back_on_timeout_and_503(monkeypatch):
    import requests as rq
    calls = []

    class R:
        def __init__(self, code):
            self.status_code, self.text = code, ""

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    def fake_post(url, **kw):
        calls.append(url)
        if "bad-slow" in url:
            raise rq.Timeout("slow")
        if "bad-busy" in url:
            return R(503)
        return R(200)

    monkeypatch.setattr(ai.requests, "post", fake_post)
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    ai._candidates[:] = ["gemini-bad-slow", "gemini-bad-busy", "gemini-flash-latest"]
    assert ai._call("hi")["candidates"]
    assert ai._candidates[0] == "gemini-flash-latest"      # remembered for next calls
    assert any("bad-slow" in c for c in calls) and any("bad-busy" in c for c in calls)
    ai._candidates[:] = []


def test_ai_list_answer_does_not_crash():
    good = {"english": True, "in_niche": True, "medium_sized": True, "fit_score": 7, "observation": "x"}
    assert ai.verdict([good])[0] == "AI error"      # verdict never crashes on odd shapes
    assert not ai.is_qualified([good])


def test_qualify_unwraps_list(monkeypatch):
    import json
    good = {"english": True, "in_niche": True, "medium_sized": True, "size": "medium",
            "fit_score": 7, "observation": "x", "fit_notes": ""}
    monkeypatch.setattr(ai, "_call", lambda p: {"candidates": [{"content": {"parts": [{"text": json.dumps([good])}]}}]})
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    lead = {"niche": "Architecture", "city": "Dubai", "country": "UAE", "name": "A", "website": "https://a.ae"}
    assert ai.verdict(ai.qualify(lead, "text"))[0] == "Qualified"


def test_notify_emoji_title_is_json_safe(monkeypatch):
    from pipeline import notify
    sent = {}

    class R:
        status_code = 200
        text = ""

    def fake_post(url, json=None, **kw):
        sent.update(json)
        return R()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    monkeypatch.setattr(config, "NTFY_TOPIC", "t")
    notify.push("📅 Zoom booked: Arc", "body", priority="urgent", tags="calendar")
    assert sent["title"].startswith("📅") and sent["priority"] == 5 and sent["tags"] == ["calendar"]
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: (_ for _ in ()).throw(UnicodeEncodeError("latin-1", "x", 0, 1, "bad")))
    notify.push("⚠️ x", "y")   # must not raise


def test_clean_emails_drops_junk_and_prefixes():
    got = scrape.clean_emails(["email:info@easyletproperty.com", "support@elated-themes.com",
                               "Mailto:Sales@Arc.co.uk", "a@b"], "https://arc.co.uk")
    assert got == ["sales@arc.co.uk", "info@easyletproperty.com"]


def test_send_plan_counts_today_and_tops_up(monkeypatch):
    import datetime as d
    from pipeline import send
    today = d.date(2026, 11, 23)

    def page(country, touches=0, last=None, found="2026-11-20"):
        return {"id": "x", "properties": {
            "Country": {"type": "select", "select": {"name": country}},
            "Date found": {"type": "date", "date": {"start": found}},
            "Last touch": {"type": "date", "date": {"start": last} if last else None},
            "Touches": {"type": "number", "number": touches}}}

    emails = [page("Canada", found="2026-11-01")] + [page("US") for _ in range(40)]
    whatsapps = [page("US") for _ in range(20)]
    contacted = [page("US", 1, "2026-11-20"), page("US", 1, "2026-11-22")]

    def fake_query(db, f):
        t = str(f)
        if "'equals': '2026-11-23'" in t:
            return [1] * 10                     # 10 already done today by an earlier run
        if "Contacted" in t:
            return contacted
        return whatsapps if "WhatsApp" in t else emails

    monkeypatch.setattr(send.notion, "query", fake_query)
    fu, wa, q, before, owed = send.plan("na", today)
    assert before == 10 and owed == 15          # only the rest of today's 25
    assert len(wa) == 15                        # WhatsApp first: fills all 15
    assert len(fu) == 1                         # only the day-3 email follow-up is due
    assert send.notion.read(q[0], "Country") == "US"   # US before Canada
    whatsapps[:] = whatsapps[:4]
    fu, wa, q, before, owed = send.plan("na", today)
    assert len(wa) == 4 and owed - len(wa) == 11       # the other 11 are emailed


def test_reply_matching():
    from pipeline import replies
    def page(email, site, subject):
        return {"properties": {"Email": {"type": "email", "email": email},
                               "Website": {"type": "url", "url": site},
                               "Subject": {"type": "rich_text", "rich_text": [{"plain_text": subject}]}}}
    leads = {"info@acme-interiors.co.uk": page("info@acme-interiors.co.uk", "https://www.acme-interiors.co.uk", "Quick idea for Acme Interiors"),
             "hello@other.com": page("hello@other.com", "https://other.com", "A video idea for Other")}
    assert replies.match_lead(leads, "info@acme-interiors.co.uk", "x") == "info@acme-interiors.co.uk"
    assert replies.match_lead(leads, "john@acme-interiors.co.uk", "x") == "info@acme-interiors.co.uk"
    assert replies.match_lead(leads, "john.smith@gmail.com", "RE: Re: Quick idea for Acme Interiors") == "info@acme-interiors.co.uk"
    assert replies.match_lead(leads, "someone@gmail.com", "hello") is None


def test_auto_reply_detected():
    from email.message import EmailMessage
    from pipeline import replies
    m = EmailMessage(); m["Subject"] = "Re: project videos"
    assert replies.is_auto_reply(m, "I am out of the office with no access to email; returning Tuesday.")
    m2 = EmailMessage(); m2["Subject"] = "Re: project videos"
    assert not replies.is_auto_reply(m2, "Sounds interesting, can you send the audit?")


def test_ping_levels(monkeypatch):
    from pipeline import notify
    sent = []
    monkeypatch.setattr(notify, "push", lambda t, m, priority="default", tags="": sent.append((t, priority)))
    notify.action("Zoom booked with X", "m"); notify.fyi("UK done 25/25", "m")
    assert sent[0] == ("🔴 ACTION: Zoom booked with X", "urgent")
    assert sent[1] == ("UK done 25/25", "low")


def test_playbook_covers_every_intent():
    from pipeline import playbook
    assert playbook.normalize("interested") == "ready_to_talk"
    assert playbook.normalize("Wants Audit") == "wants_audit"
    assert playbook.normalize("nonsense") == "other"
    assert playbook.plan("wants_audit") == {"send": True, "status": "Interested", "days": 3, "ping": False}
    assert playbook.plan("not_now")["status"] == "Later"
    assert not playbook.plan("sensitive")["send"] and not playbook.plan("not_interested")["send"]
    for intent in playbook.PLAYBOOK:
        if intent not in ("not_interested", "sensitive", "other"):
            assert intent in playbook.REPLY_PROMPT
    f = dict(first="Tony", company="Clamroc", link="https://x.y/z", sender="Azeem")
    for t in playbook.NUDGES + [playbook.LATER_CHECKIN]:
        out = t.format(**f)
        assert "https://x.y/z" in out and "{" not in out


def test_new_reply_dedupe():
    from email.message import EmailMessage
    from pipeline import replies

    def page(status, last_id="", last_touch="2026-11-25"):
        def rt(v):
            return {"type": "rich_text", "rich_text": [{"plain_text": v}]}
        return {"properties": {"Status": {"type": "select", "select": {"name": status}},
                               "Last reply ID": rt(last_id),
                               "Last touch": {"type": "date", "date": {"start": last_touch}}}}
    m = EmailMessage(); m["Message-ID"] = "<a@x>"; m["Date"] = "Wed, 25 Nov 2026 02:20:00 +0000"
    assert replies.is_new_reply(page("Contacted"), m)
    assert not replies.is_new_reply(page("Interested", "<a@x>"), m)          # already answered
    assert replies.is_new_reply(page("Interested", "<old@x>"), m)            # a new message
    assert not replies.is_new_reply(page("Interested", "", "2026-11-25"), m)  # legacy lead, same day as our reply
    assert replies.is_new_reply(page("Interested", "", "2026-11-24"), m)


def test_wants_site_only_for_audit_like_replies():
    from pipeline import replies
    assert replies.wants_site("Yes please send the audit")
    assert not replies.wants_site("How much do you charge?")


def test_check_booking_ping(monkeypatch):
    from pipeline import notify, playbook
    sent = []
    monkeypatch.setattr(notify, "push", lambda t, m, priority="default", tags="": sent.append((t, m, priority)))
    notify.check_booking("Clamroc", "Audit sent.")
    assert sent[0][0] == "📅 CHECK: did Clamroc book?" and sent[0][2] == "high" and "Clamroc booked" in sent[0][1]
    assert playbook.plan("says_booked")["ping"] and "says_booked" in playbook.REPLY_PROMPT


def test_dropped_connection_reconnects_not_pauses(monkeypatch):
    import smtplib
    from pipeline import send
    monkeypatch.setattr(send.time, "sleep", lambda s: None)
    expired = smtplib.SMTPServerDisconnected("(421, b'4.7.0 Connection expired, try reconnecting.')")
    assert send.is_dropped(expired) and not send.is_limit(expired)
    assert send.is_dropped(smtplib.SMTPSenderRefused(421, b"4.7.0 Connection expired, try reconnecting", "x"))
    assert send.is_limit(smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded"))
    assert send.is_limit(smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted"))

    sent, logins = [], []

    class FakeSMTP:
        def __init__(self, fail_first):
            self.fail_first = fail_first
        def send_message(self, msg):
            if self.fail_first:
                self.fail_first = False
                raise expired
            sent.append(msg["To"])
        def quit(self):
            pass

    def connect():
        logins.append(1)
        return FakeSMTP(fail_first=len(logins) == 1)   # first session is stale

    m = send.Mailer(connect=connect)
    m.send("a@shop.com", "Hi", "body")
    m.send("b@shop.com", "Hi", "body")
    assert sent == ["a@shop.com", "b@shop.com"] and len(logins) == 2 and m.reconnects == 1


def test_real_limit_is_not_retried(monkeypatch):
    import smtplib
    import pytest
    from pipeline import send
    monkeypatch.setattr(send.time, "sleep", lambda s: None)
    limit = smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded")

    class Limited:
        def send_message(self, msg):
            raise limit
        def quit(self):
            pass

    m = send.Mailer(connect=Limited)
    with pytest.raises(smtplib.SMTPDataError):
        m.send("a@shop.com", "Hi", "body")
    assert m.reconnects == 0
