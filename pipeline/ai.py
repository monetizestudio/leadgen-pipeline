"""Gemini (free API) checks each business and writes the personal observation line.

The model is picked automatically from the models your key can use (newest Flash model),
so Google retiring an old model name never breaks the pipeline. To force a model, set a
GEMINI_MODEL secret (e.g. gemini-flash-latest).
"""
import json
import os
import re
import time

import requests

from . import config

BASE = "https://generativelanguage.googleapis.com/v1beta"
SKIP_WORDS = ("image", "tts", "audio", "live", "embedding", "vision", "exp", "learnlm", "computer")
_model = None
_fails = 0
MAX_CONSECUTIVE_FAILS = 8

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


def _version(name):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def pick_model(names):
    """Choose the newest stable Flash model from a list of model names."""
    flash = [n for n in names if "flash" in n and not any(w in n for w in SKIP_WORDS)]
    if not flash:
        return None
    stable = [n for n in flash if "preview" not in n] or flash
    # newest version first; prefer full Flash over Flash-Lite; prefer short canonical names
    stable.sort(key=lambda n: (-_version(n), "lite" in n, len(n)))
    return stable[0]


def resolve_model(force=False):
    global _model
    if _model and not force:
        return _model
    forced = os.environ.get("GEMINI_MODEL", "").strip()
    if forced and not force:
        _model = forced
        return _model
    r = requests.get(f"{BASE}/models", params={"key": config.GEMINI_API_KEY, "pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].removeprefix("models/") for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    _model = pick_model(names) or "gemini-flash-latest"
    print(f"[ai] using model {_model}")
    return _model


def _call(prompt):
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    for attempt in range(4):
        url = f"{BASE}/models/{resolve_model()}:generateContent"
        r = requests.post(url, params={"key": config.GEMINI_API_KEY}, json=body, timeout=90)
        if r.status_code == 404:          # model retired/renamed: pick again
            resolve_model(force=True)
            continue
        if r.status_code in (429, 500, 503):
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return None


def qualify(lead, site_text):
    """Return the AI verdict dict, or None if this lead couldn't be checked."""
    global _fails
    prompt = PROMPT.format(niche=lead["niche"], city=lead["city"], country=lead["country"],
                           name=lead["name"], website=lead["website"], text=site_text[:6000])
    try:
        data = _call(prompt)
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        res = json.loads(raw)
        _fails = 0
        return res
    except (requests.RequestException, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        _fails += 1
        print(f"[ai] could not check {lead['name']}: {str(e)[:150]}")
        if _fails >= MAX_CONSECUTIVE_FAILS:
            raise RuntimeError(f"Gemini failed {_fails} times in a row. Last error: {str(e)[:200]}")
        return None
    finally:
        time.sleep(config.GEMINI_DELAY_SEC)


def is_qualified(res):
    return bool(res and res.get("english") and res.get("in_niche") and res.get("medium_sized")
                and int(res.get("fit_score", 0) or 0) >= config.MIN_FIT_SCORE and res.get("observation"))
