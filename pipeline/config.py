"""All settings for the Monetize Studio directory pipeline. Edit this file to change targets."""
import os
import re

# ---------- Secrets (set as GitHub Actions secrets, never commit them) ----------
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
SENDER_NAME = os.environ.get("SENDER_NAME", "Azeem Danish")
SENDER_ADDRESS = os.environ.get("SENDER_ADDRESS", "Monetize Studio, Karachi, Pakistan")


def redact(text):
    """Remove secrets from any text before it is logged to Notion, ntfy or the console."""
    text = str(text)
    for secret in (GEMINI_API_KEY, NOTION_TOKEN, GMAIL_APP_PASSWORD):
        if secret:
            text = text.replace(secret, "***")
    return re.sub(r"key=[A-Za-z0-9._\-]+", "key=***", text)

# ---------- Notion databases (Lead Gen HQ) ----------
LEADS_DB = "41e3ff41f39f81bc9a1cc6646c5ba814"
RUNS_DB = "41e3ff41f39f8162bed5f2011e55ae06"
CHECKED_DB = "41e3ff41f39f814ea83dc87b7123cc2f"   # audit log: every business checked

# ---------- Targets ----------
# Azeem's target (23 Nov 2026): 100 first emails SENT every day, 25 per region.
# The finder qualifies as many businesses as it takes to keep every region stocked.
REGION_DAILY_NEW = {"australia": 25, "uae": 25, "uk": 25, "na": 25}
DAILY_TARGET = sum(REGION_DAILY_NEW.values())   # 100 first emails a day
# UAE overflow (26 Nov 2026): UAE slots its own leads can't fill go to these regions, so the total stays 100.
OVERFLOW_FROM = "uae"
OVERFLOW_TO = ["na", "uk", "australia"]   # remainder goes to the first ones listed
QUEUE_DAYS = 2                # finder keeps this many days of email leads queued per region
MAX_CANDIDATES = 1500         # stop checking after this many businesses in one night
MIN_FIT_SCORE = 6             # 1-10, AI fit score needed to qualify
FIND_TIME_LIMIT_MIN = 270     # stop the find run after this many minutes (job timeout is 300)
CITY_CHUNK = 60               # businesses checked in one city before giving the next region a turn
DAILY_EMAIL_CAP = 300         # safety ceiling: all emails per day incl. follow-ups
MAX_FOLLOWUPS_PER_RUN = 50    # follow-ups are sent on top of the 25 new emails
SEND_DELAY_SEC = (60, 120)    # random pause between emails: 1-2 minutes
GEMINI_DELAY_SEC = 7          # pause between AI calls (free tier rate limit)

# ---------- Countries and cities (rotated each night) ----------
CITIES = {  # city -> (latitude, longitude) of the centre; search radius below
    "UAE": {"Dubai": (25.2048, 55.2708), "Abu Dhabi": (24.4539, 54.3773), "Sharjah": (25.3463, 55.4209),
            "Ajman": (25.4052, 55.5136), "Ras Al Khaimah": (25.8007, 55.9762), "Al Ain": (24.2075, 55.7447),
            "Dubai Marina": (25.0805, 55.1403), "Fujairah": (25.1288, 56.3265), "Umm Al Quwain": (25.5647, 55.5552)},
    "UK": {"London": (51.5074, -0.1278), "Manchester": (53.4808, -2.2426), "Birmingham": (52.4862, -1.8904),
           "Leeds": (53.8008, -1.5491), "Bristol": (51.4545, -2.5879), "Glasgow": (55.8642, -4.2518),
           "Liverpool": (53.4084, -2.9916), "Edinburgh": (55.9533, -3.1883)},
    "US": {"New York": (40.7128, -74.0060), "Los Angeles": (34.0522, -118.2437), "Houston": (29.7604, -95.3698),
           "Dallas": (32.7767, -96.7970), "Miami": (25.7617, -80.1918), "Chicago": (41.8781, -87.6298),
           "Phoenix": (33.4484, -112.0740), "Atlanta": (33.7490, -84.3880), "San Diego": (32.7157, -117.1611),
           "Austin": (30.2672, -97.7431), "Denver": (39.7392, -104.9903), "Seattle": (47.6062, -122.3321),
           "Charlotte": (35.2271, -80.8431), "Tampa": (27.9506, -82.4572)},
    "Canada": {"Toronto": (43.6532, -79.3832), "Vancouver": (49.2827, -123.1207), "Calgary": (51.0447, -114.0719),
               "Ottawa": (45.4215, -75.6972), "Edmonton": (53.5461, -113.4938), "Mississauga": (43.5890, -79.6441)},
    "Australia": {"Sydney": (-33.8688, 151.2093), "Melbourne": (-37.8136, 144.9631), "Brisbane": (-27.4698, 153.0251),
                  "Perth": (-31.9505, 115.8605), "Adelaide": (-34.9285, 138.6007), "Gold Coast": (-28.0167, 153.4000)},
}
SEARCH_RADIUS_M = 20000

# Regional send runs -> which countries they email (each fires at that region's morning)
REGIONS = {
    "australia": ["Australia"],
    "uae": ["UAE"],
    "uk": ["UK"],
    "na": ["US", "Canada"],      # US is searched; Canada leads already queued still get sent
}
SEARCH_COUNTRIES = {"australia": ["Australia"], "uae": ["UAE"], "uk": ["UK"], "na": ["US"]}
REGION_TZ = {"australia": "Australia/Sydney", "uae": "Asia/Dubai", "uk": "Europe/London",
             "na": "America/New_York"}
WIDE_RADIUS_M = 40000         # second pass for a region whose cities ran dry

# ---------- Niches: OpenStreetMap tags -> Notion niche ----------
OSM_TAGS = [
    ("office", "architect", "Architecture"),
    ("office", "interior_design", "Interior Design"),
    ("shop", "interior_decoration", "Interior Design"),
    ("craft", "interior_decorator", "Interior Design"),
    ("shop", "kitchen", "Kitchen & Bath"),
    ("shop", "bathroom_furnishing", "Kitchen & Bath"),
    ("shop", "tiles", "Kitchen & Bath"),
    ("craft", "builder", "Remodeling"),
    ("office", "estate_agent", "Real Estate"),
    ("shop", "estate_agent", "Real Estate"),
]

# Extra map categories searched only in these countries (UAE ran dry on the main list, Nov 2026)
EXTRA_OSM_TAGS = {
    "UAE": [
        ("shop", "furniture", "Interior Design"),
        ("shop", "curtain", "Interior Design"),
        ("shop", "lighting", "Interior Design"),
        ("craft", "carpenter", "Interior Design"),
        ("office", "construction_company", "Remodeling"),
        ("craft", "tiler", "Kitchen & Bath"),
        ("office", "property_management", "Real Estate"),
    ],
}
# Generic "office=company" listings kept only when the name says what they do (UAE fit-out firms list like this)
COMPANY_NAME_NICHES = {
    # "UAE": [(r"fit[\s-]?out|interior|decor|joinery", "Interior Design"), ...]
    # Turned off 27 Nov 2026: the name-regex search over every office=company was too slow on the free
    # Overpass servers and stalled the 26 Nov night run. Format: country -> [(regex, niche), ...]
}
# UAE businesses list a mobile number (05x / +971 5x) and use it for WhatsApp, often with no wa.me link
MOBILE_AS_WHATSAPP = {"UAE": r"(?:\+|00)?971[\s.-]?\(?0?\)?[\s.-]?(5[024568])[\s.-]?(\d{3})[\s.-]?(\d{4})|\b0(5[024568])[\s.-]?(\d{3})[\s.-]?(\d{4})\b"}
CONTACT_PATHS_EXTRA = {"UAE": ["/en", "/en/contact", "/en/contact-us"]}   # many UAE sites are bilingual
THIN_CHARS = {"UAE": 120}   # site text needed to judge fit (default 200); UAE sites are often image-heavy

NICHE_WORD = {
    "Architecture": "architecture",
    "Interior Design": "interior design",
    "Kitchen & Bath": "kitchen and bath",
    "Remodeling": "remodeling",
    "Real Estate": "real estate",
}

# ---------- Messages ----------
OPT_OUT = "Not relevant? Just reply 'no' and I won't follow up."

EMAIL_SUBJECT = {
    "design": "{company}'s project videos",
    "realestate": "Quick idea for {company}'s listings",
}

EMAIL_1 = {
    "design": (
        "Hi {first_name},\n\n{observation}\n\n"
        "Most {niche} businesses we look at have great work but post it as still photos, and Meta's "
        "algorithm now pushes video far harder. That's usually where enquiries are being left on the table.\n\n"
        "I run Monetize Studio. We make cinematic project videos (walkthroughs, before/after reveals) and run "
        "Meta ads that turn them into consultation requests.\n\n"
        "Can I send you a free 2-minute video audit of {company}'s Instagram? You'll see exactly what I'd change.\n\n"
        "{sender}\nMonetize Studio"
    ),
    "realestate": (
        "Hi {first_name},\n\n{observation}\n\n"
        "Agents getting the most buyer enquiries right now are running short property-tour videos as Meta lead "
        "ads, so the right buyers in {city} see the listing, not just followers.\n\n"
        "I run Monetize Studio. We make those tour videos and run the ads end to end.\n\n"
        "Want a free 2-minute audit of how {company}'s listings come across on Instagram and Facebook?\n\n"
        "{sender}\nMonetize Studio"
    ),
}

EMAIL_2 = ("Hi {first_name}, following up on my note below. Still happy to put that free audit together for "
           "{company}. Just reply \"yes\" and I'll send it within a day.\n\n{sender}")
EMAIL_3 = ("Hi {first_name}, last note from me. If marketing isn't a priority right now, no problem at all. "
           "If it becomes one, I'm a reply away.\n\n{sender}")

WHATSAPP_1 = ("Hi {first_name}, Azeem here from Monetize Studio. Came across {company}. {observation} "
              "We make project/property videos and run Meta ads for {niche} businesses. "
              "Would a free 2-min audit of your Instagram be useful?")

FOLLOWUP_GAP_DAYS = {2: 3, 3: 4}   # touch number -> days after previous touch


def region_today(region):
    """Today's date in that region's own time zone, so late or repeated runs count the same day."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    return _dt.datetime.now(ZoneInfo(REGION_TZ[region])).date()


def region_of(country):
    return next((r for r, cs in REGIONS.items() if country in cs), None)
