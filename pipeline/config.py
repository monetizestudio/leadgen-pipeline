"""All settings for the Monetize Studio directory pipeline. Edit this file to change targets."""
import os

# ---------- Secrets (set as GitHub Actions secrets, never commit them) ----------
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
SENDER_NAME = os.environ.get("SENDER_NAME", "Azeem Danish")
SENDER_ADDRESS = os.environ.get("SENDER_ADDRESS", "Monetize Studio, Karachi, Pakistan")

# ---------- Notion databases (Lead Gen HQ) ----------
LEADS_DB = "41e3ff41f39f81bc9a1cc6646c5ba814"
RUNS_DB = "41e3ff41f39f8162bed5f2011e55ae06"

# ---------- Targets ----------
DAILY_TARGET = 100            # qualified leads with contact details per night
MAX_CANDIDATES = 600          # stop checking after this many businesses
MIN_FIT_SCORE = 6             # 1-10, AI fit score needed to qualify
FIND_TIME_LIMIT_MIN = 150     # stop the find run after this many minutes
SEND_CAP_PER_RUN = 45         # max first emails per regional send run
SEND_DELAY_SEC = (45, 110)    # random pause between emails
GEMINI_DELAY_SEC = 7          # pause between AI calls (free tier rate limit)

# ---------- Countries and cities (rotated each night) ----------
CITIES = {
    "UAE": ["Dubai", "Abu Dhabi", "Sharjah"],
    "UK": ["London", "Manchester", "Birmingham", "Leeds", "Bristol", "Glasgow", "Liverpool", "Edinburgh"],
    "US": ["New York", "Los Angeles", "Houston", "Dallas", "Miami", "Chicago", "Phoenix",
           "Atlanta", "San Diego", "Austin", "Denver", "Seattle", "Charlotte", "Tampa"],
    "Canada": ["Toronto", "Vancouver", "Calgary", "Ottawa", "Edmonton", "Mississauga"],
    "Australia": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast"],
}
CITIES_PER_NIGHT = 6

# Regional send runs -> which countries they email (each fires at that region's morning)
REGIONS = {
    "australia": ["Australia"],
    "uae": ["UAE"],
    "uk": ["UK"],
    "na": ["US", "Canada"],
}

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
