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
    assert len(cities) == sum(len(v) for v in config.CITIES.values())
    assert len({c[1] for c in cities}) == len(cities)
    for country, city, (lat, lon) in cities:
        assert -90 < lat < 90 and -180 < lon < 180


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
