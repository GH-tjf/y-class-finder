#!/usr/bin/env python3
"""Pull the YMCA of Middle Tennessee class schedule + branch info into data.json / data.js.

Source: the same public JSON feed the ymcamidtn.org schedule page (and its PDFs) is built from:
  /schedules/get-event-data?location=0&category=0&date=YYYY-MM-DD   (all branches, one day)
  /locations  (drupal-settings-json -> openyMap: name, lat/lng, address, phone, hours)
Run nightly. Exits non-zero (and leaves the old data in place) if the pull looks broken.
"""
import html
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://www.ymcamidtn.org"
UA = "Mozilla/5.0 (schedule-viewer; personal use)"
DAYS = 14
OUT = Path(__file__).resolve().parent
DAY_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)", re.I)


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def text_tokens(markup):
    t = re.sub(r"<[^>]+>", "\n", markup)
    return [html.unescape(x).strip() for x in t.split("\n") if x.strip()]


def parse_locations():
    page = get("/locations?type=ymca,camps,youth-development-sites&amenities")
    m = re.search(r'data-drupal-selector="drupal-settings-json">(.*?)</script>', page, re.S)
    settings = json.loads(m.group(1))
    locs = []
    for x in settings["openyMap"]:
        tok = text_tokens(x["markup"])
        hrefs = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', x["markup"])]
        phone = next((h[4:] for h in hrefs if h.startswith("tel:")), "")
        page_url = next((BASE + h for h in hrefs if h.startswith("/locations/")), "")
        # address = the token right after the name
        addr = ""
        if x["name"] in tok:
            i = tok.index(x["name"])
            if i + 1 < len(tok) and not tok[i + 1].lower().startswith("tel"):
                addr = tok[i + 1]
        hours, group = [], None
        if any(t.lower().startswith("building hours") for t in tok):
            start = next(i for i, t in enumerate(tok) if t.lower().startswith("building hours")) + 1
            i = start
            while i < len(tok):
                t = tok[i]
                if DAY_RE.match(t) and i + 1 < len(tok):
                    if group is None:
                        group = {"title": "Facility Hours", "days": []}
                        hours.append(group)
                    group["days"].append([t.rstrip(":"), tok[i + 1]])
                    i += 2
                    continue
                if len(t) > 40 or t.lower().startswith(("view", "learn", "get directions", "directions")):
                    break
                group = {"title": t.rstrip(":"), "days": []}
                hours.append(group)
                i += 1
            hours = [g for g in hours if g["days"]]
        locs.append({
            "id": x["location_id"], "name": x["name"], "type": (x.get("tags") or ["YMCA"])[0],
            "lat": x["lat"], "lng": x["lng"], "address": addr, "phone": phone,
            "url": page_url, "hours": hours,
        })
    return locs


def main():
    locs = parse_locations()
    by_name = {l["name"]: l for l in locs}
    events, classes = [], {}
    today = date.today()
    per_day = {}
    for d in range(DAYS):
        day = today + timedelta(days=d)
        raw = json.loads(get(f"/schedules/get-event-data?location=0&category=0&date={day.isoformat()}"))
        per_day[day.isoformat()] = len(raw)
        for e in raw:
            li = e.get("location_info") or {}
            loc = by_name.get(e["location"])
            if loc is not None and li.get("days"):
                # the feed carries this week's real hours (holiday closures included)
                loc.setdefault("week_hours", li["days"])
            cid = e.get("class") or e["name"]
            if cid not in classes:
                ci = e.get("class_info") or {}
                desc = (e.get("session_description") or ci.get("description") or "").strip()
                classes[cid] = {"name": e["name"], "desc": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", desc)))[:600],
                                "url": ci.get("path") or ""}
            events.append({
                "c": cid, "l": e["location"], "r": e.get("room") or "", "i": (e.get("instructor") or "").strip(),
                "k": e.get("category") or "", "s": int(e["start_timestamp"]), "e": int(e["end_timestamp"]),
                "u": e.get("register_url") or "",
            })
        time.sleep(1)

    # sanity: the first week should be well populated; refuse to overwrite good data with junk
    first_week = sum(list(per_day.values())[:7])
    if first_week < 500 or len(locs) < 10:
        print(f"ABORT: implausible pull (first-week events={first_week}, locations={len(locs)}); old data kept", file=sys.stderr)
        sys.exit(2)

    data = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "per_day": per_day, "locations": locs, "classes": classes, "events": events}
    blob = json.dumps(data, separators=(",", ":"))
    (OUT / "data.json.tmp").write_text(blob)
    (OUT / "data.json.tmp").replace(OUT / "data.json")
    (OUT / "data.js.tmp").write_text("window.YDATA=" + blob + ";")
    (OUT / "data.js.tmp").replace(OUT / "data.js")
    print(f"ok: {len(events)} events, {len(classes)} classes, {len(locs)} locations, {len(blob)//1024} KB; per-day {per_day}")


if __name__ == "__main__":
    main()
