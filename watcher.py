#!/usr/bin/env python3
"""Poll many public job boards, keep new keyword matches, email links via Gmail."""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEEN_PATH = ROOT / "seen.json"
KEYWORDS_PATH = ROOT / "keywords.txt"

USER_AGENT = "job-watcher/1.1 (+personal alerts; links back to source)"
TIMEOUT = 45


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_keywords() -> list[str]:
    words: list[str] = []
    for line in KEYWORDS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line.lower())
    return words


def load_seen() -> dict[str, str]:
    if not SEEN_PATH.exists():
        return {}
    data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_seen(seen: dict[str, str]) -> None:
    SEEN_PATH.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def http_get_json(url: str) -> object:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(as_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(as_text(v) for v in value.values())
    return str(value).strip()


def job(
    *,
    source: str,
    jid: str,
    title: object,
    company: object,
    url: object,
    location: object = "",
    tags: object = "",
) -> dict | None:
    title_s = as_text(title)
    url_s = as_text(url)
    if not url_s or not title_s:
        return None
    company_s = as_text(company)
    location_s = as_text(location)
    tags_s = as_text(tags)
    return {
        "id": f"{source.lower()}:{as_text(jid) or url_s}",
        "source": source,
        "title": title_s,
        "company": company_s,
        "url": url_s,
        "location": location_s,
        "text": " ".join([title_s, company_s, location_s, tags_s]).lower(),
    }


def score_job(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in text)


def fetch_remotive() -> list[dict]:
    payload = http_get_json(
        "https://remotive.com/api/remote-jobs?category=software-dev&limit=100"
    )
    out: list[dict] = []
    for j in payload.get("jobs", []):
        item = job(
            source="Remotive",
            jid=str(j["id"]),
            title=j.get("title") or "",
            company=j.get("company_name") or "",
            url=j.get("url") or "",
            location=j.get("candidate_required_location") or "",
            tags=f"{j.get('category') or ''} {j.get('job_type') or ''}",
        )
        if item:
            out.append(item)
    return out


def fetch_arbeitnow() -> list[dict]:
    payload = http_get_json("https://www.arbeitnow.com/api/job-board-api")
    out: list[dict] = []
    for j in payload.get("data", []):
        item = job(
            source="Arbeitnow",
            jid=str(j.get("slug") or j.get("url")),
            title=j.get("title") or "",
            company=j.get("company_name") or "",
            url=j.get("url") or "",
            location=j.get("location") or "",
            tags=" ".join(j.get("tags") or []),
        )
        if item:
            out.append(item)
    return out


def fetch_remoteok() -> list[dict]:
    # First element is often legal metadata, not a job.
    payload = http_get_json("https://remoteok.com/api")
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for j in payload:
        if not isinstance(j, dict) or not j.get("id") or not j.get("url"):
            continue
        tags = " ".join(j.get("tags") or [])
        item = job(
            source="RemoteOK",
            jid=str(j["id"]),
            title=j.get("position") or j.get("title") or "",
            company=j.get("company") or "",
            url=j.get("url") or "",
            location=j.get("location") or "",
            tags=tags,
        )
        if item:
            out.append(item)
    return out


def fetch_jobicy() -> list[dict]:
    # Public API: https://jobicy.com/api/v2/remote-jobs
    q = urllib.parse.urlencode({"count": 50, "tag": "javascript"})
    payload = http_get_json(f"https://jobicy.com/api/v2/remote-jobs?{q}")
    out: list[dict] = []
    for j in payload.get("jobs", []):
        item = job(
            source="Jobicy",
            jid=str(j.get("id") or j.get("url")),
            title=j.get("jobTitle") or "",
            company=j.get("companyName") or "",
            url=j.get("url") or "",
            location=j.get("jobGeo") or "",
            tags=as_text(
                [
                    j.get("jobType"),
                    j.get("jobLevel"),
                    j.get("jobIndustry"),
                ]
            ),
        )
        if item:
            out.append(item)
    # second pass: react/node tags
    q2 = urllib.parse.urlencode({"count": 50, "tag": "react"})
    payload2 = http_get_json(f"https://jobicy.com/api/v2/remote-jobs?{q2}")
    seen_urls = {x["url"] for x in out}
    for j in payload2.get("jobs", []):
        item = job(
            source="Jobicy",
            jid=str(j.get("id") or j.get("url")),
            title=j.get("jobTitle") or "",
            company=j.get("companyName") or "",
            url=j.get("url") or "",
            location=j.get("jobGeo") or "",
            tags="react",
        )
        if item and item["url"] not in seen_urls:
            out.append(item)
            seen_urls.add(item["url"])
    return out


def fetch_himalayas() -> list[dict]:
    payload = http_get_json("https://himalayas.app/jobs/api?limit=100")
    rows = payload if isinstance(payload, list) else payload.get("jobs") or payload.get("data") or []
    out: list[dict] = []
    for j in rows:
        if not isinstance(j, dict):
            continue
        slug = j.get("slug") or j.get("id") or ""
        url = j.get("applicationLink") or j.get("url") or ""
        if slug and not url:
            url = f"https://himalayas.app/jobs/{slug}"
        cats = j.get("categories") or j.get("skills") or []
        if isinstance(cats, list):
            tags = " ".join(str(c) for c in cats)
        else:
            tags = str(cats)
        item = job(
            source="Himalayas",
            jid=str(slug or url),
            title=j.get("title") or "",
            company=(j.get("companyName") or j.get("company") or ""),
            url=url,
            location=j.get("location") or j.get("timezoneRestrictions") or "Remote",
            tags=tags,
        )
        if item:
            out.append(item)
    return out


SOURCES = (
    ("Remotive", fetch_remotive),
    ("Arbeitnow", fetch_arbeitnow),
    ("RemoteOK", fetch_remoteok),
    ("Jobicy", fetch_jobicy),
    ("Himalayas", fetch_himalayas),
)


def fetch_all() -> list[dict]:
    collected: list[dict] = []
    errors: list[str] = []
    for name, fn in SOURCES:
        try:
            batch = fn()
            print(f"fetched {len(batch)} from {name}")
            collected.extend(batch)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as e:
            errors.append(f"{name}: {e}")
            print(f"WARN {name} failed: {e}", file=sys.stderr)
    if not collected and errors:
        raise RuntimeError("all sources failed: " + "; ".join(errors))
    return collected


def send_gmail(subject: str, body: str) -> None:
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip().replace(" ", "")
    to = os.environ.get("ALERT_TO", user).strip()
    if not user or not password:
        raise RuntimeError("GMAIL_USER and GMAIL_APP_PASSWORD are required")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
        smtp.starttls(context=context)
        smtp.login(user, password)
        smtp.send_message(msg)


def format_alert(matches: list[tuple[dict, int]]) -> tuple[str, str]:
    subject = f"[jobs] {len(matches)} new link{'s' if len(matches) != 1 else ''}"
    lines = [
        f"{len(matches)} new jobs (keyword filter).",
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    for item, score in matches:
        company = f" @ {item['company']}" if item["company"] else ""
        loc = f" | {item['location']}" if item["location"] else ""
        lines.append(f"{item['title']}{company}")
        lines.append(f"  [{item['source']}] score={score}{loc}")
        lines.append(f"  {item['url']}")
        lines.append("")
    lines.append(
        "Boards: Remotive, Arbeitnow, RemoteOK, Jobicy, Himalayas. "
        "Not Jobstreet/LinkedIn."
    )
    return subject, "\n".join(lines)


def self_check() -> None:
    assert score_job("react node.js remote", ["react", "node.js", "aws"]) == 2
    assert strip_html("<b>hi</b>") == "hi"
    item = job(
        source="Test",
        jid="1",
        title="Dev",
        company="Co",
        url="https://example.com/j/1",
    )
    assert item and item["id"] == "test:1"
    print("self-check OK")


def main() -> int:
    load_dotenv()
    if "--self-check" in sys.argv:
        self_check()
        return 0

    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN", "").lower() in {
        "1",
        "true",
        "yes",
    }
    seed = os.environ.get("SEED", "").lower() in {"1", "true", "yes"}
    min_score = int(os.environ.get("MIN_SCORE") or "2")

    keywords = load_keywords()
    if not keywords:
        raise RuntimeError("keywords.txt is empty")

    seen = load_seen()
    if not seen:
        seed = True
        print("seen.json empty -> SEED (save links, no email)")

    jobs = fetch_all()
    matches: list[tuple[dict, int]] = []
    for item in jobs:
        if item["id"] in seen:
            continue
        score = score_job(item["text"], keywords)
        if score >= min_score:
            matches.append((item, score))

    matches.sort(key=lambda m: (-m[1], m[0]["title"]))
    # Cap email size
    max_alert = int(os.environ.get("MAX_ALERT") or "40")
    if len(matches) > max_alert:
        print(f"capping alert {len(matches)} -> {max_alert}")
        matches = matches[:max_alert]

    print(f"new links: {len(matches)} (min_score={min_score})")

    now = datetime.now(timezone.utc).isoformat()
    for item, _ in matches:
        seen[item["id"]] = now

    if matches and not seed and not dry_run:
        subject, body = format_alert(matches)
        send_gmail(subject, body)
        print(f"emailed {len(matches)} link(s)")
    elif matches and seed:
        print(f"seeded {len(matches)} without email")
    elif matches and dry_run:
        subject, body = format_alert(matches)
        print("--- DRY RUN ---")
        print(subject)
        print(body)
    else:
        print("nothing new")

    save_seen(seen)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
