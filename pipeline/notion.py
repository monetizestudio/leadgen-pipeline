"""Small Notion API client for the Leads and Daily Runs databases."""
import datetime as dt
import time

import requests

from . import config

API = "https://api.notion.com/v1"


def _headers():
    return {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _req(method, path, **kw):
    for attempt in range(5):
        r = requests.request(method, f"{API}{path}", headers=_headers(), timeout=30, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        time.sleep(0.35)  # stay under Notion's ~3 requests/second
        return r.json()
    r.raise_for_status()


def query(db_id, filter_=None):
    """Return all pages in a database matching the filter."""
    body, out = {}, []
    if filter_:
        body["filter"] = filter_
    while True:
        data = _req("POST", f"/databases/{db_id}/query", json=body)
        out.extend(data["results"])
        if not data.get("has_more"):
            return out
        body["start_cursor"] = data["next_cursor"]


# ---------- property builders ----------
def text(v):
    return {"rich_text": [{"text": {"content": (v or "")[:1900]}}]}


def title(v):
    return {"title": [{"text": {"content": (v or "")[:200]}}]}


def select(v):
    return {"select": {"name": v}} if v else {"select": None}


def date(d=None):
    return {"date": {"start": (d or dt.date.today()).isoformat()}}


# ---------- property readers ----------
def read(page, name):
    p = page["properties"].get(name)
    if not p:
        return None
    t = p["type"]
    if t in ("title", "rich_text"):
        return "".join(x["plain_text"] for x in p[t])
    if t == "select":
        return p["select"]["name"] if p["select"] else None
    if t == "date":
        return p["date"]["start"] if p["date"] else None
    return p.get(t)


# ---------- leads ----------
def lead_exists(website, email):
    ors = []
    if website:
        ors.append({"property": "Website", "url": {"equals": website}})
    if email:
        ors.append({"property": "Email", "email": {"equals": email}})
    if not ors:
        return False
    return bool(query(config.LEADS_DB, {"or": ors}))


def create_lead(props):
    return _req("POST", "/pages", json={"parent": {"database_id": config.LEADS_DB}, "properties": props})


def update_page(page_id, props):
    return _req("PATCH", f"/pages/{page_id}", json={"properties": props})


def log_run(name, channel, target, done, result, notes=""):
    props = {
        "Run": title(name),
        "Date": date(),
        "Channel": select(channel),
        "Target": {"number": target},
        "Done": {"number": done},
        "Result": select(result),
        "Notes": text(notes),
    }
    return _req("POST", "/pages", json={"parent": {"database_id": config.RUNS_DB}, "properties": props})
