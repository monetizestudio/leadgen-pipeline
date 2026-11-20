"""Gemini (free API) checks each business and writes the personal observation line."""
import json
import time

import requests

from . import config

PROMPT = """You qualify leads for Monetize Studio, a digital marketing agency (Meta ads + cinematic video content).
Target: MEDIUM-sized {niche} businesses in {city}, {country}.

Medium-sized means: running 3+ years (not a startup), roughly 5-50 staff, a physical office/showroom/studio,
a real website with a portfolio or listings, an owner who is reachable. NOT a big corporation or a chain with
an in-house marketing department, NOT a one-person side business.

Business: {name}
Website: {website}
Website text (may be messy):
\"\"\"{text}\"\"\"

Reply with JSON only, these keys:
- "english": true if the business communicates in English
- "in_niche": true if it really is a {niche} business (architecture, interiors, kitchens, bathrooms, tiles,
  remodeling, or real estate)
- "medium_sized": true/false by the definition above
- "fit_score": integer 1-10, how good a client this would be for Meta ads + video content
- "fit_notes": one short line explaining the score (size signals, what they lack)
- "first_name": the owner's/founder's/principal's first name ONLY if clearly stated on the site, else ""
- "observation": ONE sentence addressed to them about something specific and real from their site
  (a project, a service, a location, a detail). No generic praise, no invented facts, no exclamation marks.
  Example: "Saw the Marina loft conversion on your site, the before/after on that staircase is striking."
"""


def qualify(lead, site_text):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}"
           f":generateContent?key={config.GEMINI_API_KEY}")
    prompt = PROMPT.format(niche=lead["niche"], city=lead["city"], country=lead["country"],
                           name=lead["name"], website=lead["website"], text=site_text[:6000])
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    for attempt in range(4):
        r = requests.post(url, json=body, timeout=60)
        if r.status_code in (429, 500, 503):
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    else:
        return None
    time.sleep(config.GEMINI_DELAY_SEC)
    try:
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw)
    except (KeyError, IndexError, json.JSONDecodeError):
        return None


def is_qualified(res):
    return bool(res and res.get("english") and res.get("in_niche") and res.get("medium_sized")
                and int(res.get("fit_score", 0)) >= config.MIN_FIT_SCORE and res.get("observation"))
