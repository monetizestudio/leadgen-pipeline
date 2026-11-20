"""Phone alerts through ntfy (free app). Silently skips if no topic is set."""
import requests

from . import config


def push(title, message, priority="default", tags=""):
    if not config.NTFY_TOPIC:
        print(f"[notify] {title}: {message}")
        return
    try:
        requests.post(f"https://ntfy.sh/{config.NTFY_TOPIC}", data=message.encode("utf-8"),
                      headers={"Title": title, "Priority": priority, "Tags": tags}, timeout=15)
    except requests.RequestException as e:
        print(f"[notify] failed: {e}")
