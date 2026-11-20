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
    assert len(set(cities)) == len(cities)
