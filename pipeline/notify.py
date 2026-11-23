"""Phone alerts through ntfy (free app). Silently skips if no topic is set.

Sent as JSON so titles can carry emojis (an emoji in an HTTP header used to crash the run).
A failed alert never stops the real work.
"""
import requests

from . import config

PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5, "max": 5}


def payload(title, message, priority="default", tags=""):
    return {
        "topic": config.NTFY_TOPIC,
        "title": config.redact(title),
        "message": config.redact(message),
        "priority": PRIORITY.get(str(priority), 3),
        "tags": [t.strip() for t in str(tags).split(",") if t.strip()],
    }


def push(title, message, priority="default", tags=""):
    body = payload(title, message, priority, tags)
    if not config.NTFY_TOPIC:
        print(f"[notify] {body['title']}: {body['message']}")
        return
    try:
        r = requests.post("https://ntfy.sh/", json=body, timeout=15)
        if r.status_code >= 400:
            print(f"[notify] ntfy said HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[notify] failed: {config.redact(e)[:150]}")
