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
  remodeling, or real estate). FALSE for solicitors/lawyers, plumbers, electricians, heating engineers,
  damage restoration, cleaners, removals, property factors/block management, web or marketing agencies
- "size": "small", "medium" or "large" by the definition above
- "medium_sized": true only if size is "medium"
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


def rank_models(names):
    """Order Flash models from most to least reliable:
    Google's stable 'latest' aliases first, then stable versions (newest first, full Flash before Lite),
    then previews last."""
    flash = [n for n in names if "flash" in n and not any(w in n for w in SKIP_WORDS)]
    aliases = [n for n in ("gemini-flash-latest", "gemini-flash-lite-latest") if n in flash]
    rest = [n for n in flash if n not in aliases]
    stable = sorted([n for n in rest if "preview" not in n], key=lambda n: (-_version(n), "lite" in n, len(n)))
    previews = sorted([n for n in rest if "preview" in n], key=lambda n: (-_version(n), "lite" in n, len(n)))
    return aliases + stable + previews


def pick_model(names):
    ranked = rank_models(names)
    return ranked[0] if ranked else None


_candidates = []


def candidates():
    """Models to try, best first. A GEMINI_MODEL secret, if set, goes to the front."""
    global _candidates
    if _candidates:
        return _candidates
    forced = os.environ.get("GEMINI_MODEL", "").strip()
    names = []
    try:
        r = requests.get(f"{BASE}/models", params={"key": config.GEMINI_API_KEY, "pageSize": 200}, timeout=30)
        r.raise_for_status()
        names = [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
    except requests.RequestException as e:
        print(f"[ai] could not list models: {config.redact(e)[:120]}")
    ranked = rank_models(names) or ["gemini-flash-latest", "gemini-flash-lite-latest"]
    _candidates = ([forced] if forced else []) + [n for n in ranked if n != forced]
    print(f"[ai] model order: {', '.join(_candidates[:5])}")
    return _candidates


def _promote(model):
    """Remember a model that just worked so the next calls start with it."""
    if _candidates and _candidates[0] != model:
        _candidates.remove(model)
        _candidates.insert(0, model)
        print(f"[ai] now using {model}")


def resolve_model(force=False):
    return candidates()[0]


def _call(prompt):
    """Try each model in turn. Timeouts, overload (429/5xx) and retired models (404) move on to
    the next model instead of failing the lead."""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    last = "no model answered"
    for model in list(candidates())[:4]:
        for attempt in range(2):
            try:
                r = requests.post(f"{BASE}/models/{model}:generateContent",
                                  params={"key": config.GEMINI_API_KEY}, json=body, timeout=60)
            except (requests.Timeout, requests.ConnectionError) as e:
                last = f"{model}: {type(e).__name__}"
                time.sleep(5)
                continue
            if r.status_code == 404:
                last = f"{model}: 404 not found"
                break                                   # retired: next model
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"{model}: HTTP {r.status_code}"
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 200:
                last = f"{model}: HTTP {r.status_code} {r.text[:120]}"
                break
            _promote(model)
            return r.json()
    raise requests.RequestException(f"All models failed (last: {config.redact(last)})")


def qualify(lead, site_text):
    """Return the AI verdict dict, or None if this lead couldn't be checked."""
    global _fails
    prompt = PROMPT.format(niche=lead["niche"], city=lead["city"], country=lead["country"],
                           name=lead["name"], website=lead["website"], text=site_text[:6000])
    try:
        data = _call(prompt)
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        res = json.loads(raw)
        if isinstance(res, list):          # Gemini sometimes wraps the answer in a list
            res = next((x for x in res if isinstance(x, dict)), None)
        if not isinstance(res, dict):
            raise TypeError(f"unexpected AI answer: {raw[:80]}")
        _fails = 0
        return res
    except (requests.RequestException, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        _fails += 1
        print(f"[ai] could not check {lead['name']}: {config.redact(e)[:150]}")
        if _fails >= MAX_CONSECUTIVE_FAILS:
            raise RuntimeError(f"Gemini failed {_fails} times in a row. Last error: {config.redact(e)[:200]}")
        return None
    finally:
        time.sleep(config.GEMINI_DELAY_SEC)


def verdict(res):
    """(result label for the audit log, reason) for an AI answer."""
    if not isinstance(res, dict) or not res:
        return "AI error", "Gemini could not check this one"
    notes = res.get("fit_notes", "")
    if not res.get("english"):
        return "Not English", notes
    if not res.get("in_niche"):
        return "Not in niche", notes
    if not res.get("medium_sized"):
        return ("Too big" if str(res.get("size", "")).lower() == "large" else "Too small"), notes
    if int(res.get("fit_score", 0) or 0) < config.MIN_FIT_SCORE or not res.get("observation"):
        return "Low score", notes
    return "Qualified", notes


def is_qualified(res):
    return bool(isinstance(res, dict) and res.get("english") and res.get("in_niche") and res.get("medium_sized")
                and int(res.get("fit_score", 0) or 0) >= config.MIN_FIT_SCORE and res.get("observation"))
