"""
Staffing firm job poller.

Checks Apex Systems, Brooksource and Experis for new Twin Cities jobs that fit
an entry-level candidate, remembers what it already sent in jobs.db, and emails
only the new matches.

Run:  python poller.py            (check + email)
      python poller.py --dry-run  (check + print, no email, nothing saved)
"""

import html
import json
import os
import re
import smtplib
import sqlite3
import sys
import urllib.request
from email.mime.text import MIMEText

DB_FILE = "jobs.db"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Title must contain one of these to count as a role worth looking at.
GOOD_WORDS = [
    "developer", "software", "engineer", "programmer", "web", "application",
    "qa", "quality assurance", "test", "analyst", "support", "help desk",
    "helpdesk", "technician", "data", "it ", "systems", "junior", "associate",
    "entry", "intern", "configuration",
]

# Title containing any of these is skipped (too senior or wrong stack).
BAD_WORDS = [
    "senior", "sr.", "sr ", "lead", "principal", "manager", "director",
    "architect", "staff ", "head of", " iii", " iv", " 3", " 4", " 5",
    "java", "cobol", "mainframe", "salesforce", "servicenow", "sap ",
    "workday", "oracle", "dynamics", "nurse", "medical rep", "accountant",
    "payroll", "recruiter",
    # non-software fields that share words like "engineer" / "technician"
    "lab ", "laboratory", "manufacturing", "mechanical", "materials",
    "medical device", "hardware", "failure analysis", "supplier",
    "surveillance", "compliance", "project engineer", "design engineer",
    "engineering technician", "repair", "r&d", "accounts payable",
    "stockroom", "quality technician", "qa technician", "penetration",
]

# Description containing any of these is skipped.
BAD_DESC = [
    "u.s. citizen", "us citizen", "united states citizen", "citizenship required",
    "green card", "security clearance", "secret clearance", "public trust",
]

TWIN_CITIES = [
    "minneapolis", "st. paul", "saint paul", "st paul", "bloomington", "edina",
    "eagan", "minnetonka", "eden prairie", "plymouth", "roseville", "maple grove",
    "hopkins", "golden valley", "st. louis park", "saint louis park", "richfield",
    "burnsville", "woodbury", "arden hills", "shoreview", "brooklyn park",
    "wayzata", "fridley", "mendota heights", "chaska", "shakopee", "lakeville",
]


# ---------- small helpers ----------

def fetch(url, data=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text or ""))


def too_many_years(desc):
    # "3+ years", "5 years of experience" -> skip if the smallest number is 3 or more
    nums = [int(n) for n in re.findall(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?years?", desc)]
    nums = [n for n in nums if n < 30]
    return bool(nums) and min(nums) >= 3


def is_good(title, desc=""):
    t = " " + title.lower() + " "
    if not any(w in t for w in GOOD_WORDS):
        return False
    if any(w in t for w in BAD_WORDS):
        return False
    d = desc.lower()
    if any(w in d for w in BAD_DESC):
        return False
    if too_many_years(d):
        return False
    return True


def in_twin_cities(location):
    loc = location.lower()
    return any(city in loc for city in TWIN_CITIES)


# ---------- one function per firm; each returns a list of job dicts ----------

def apex_jobs():
    # Newest-first table of all US jobs; scan the first pages and keep MN ones.
    jobs = []
    row_re = re.compile(
        r'<tr class="(?:odd|even)">.*?href="(/job/[^"]+)".*?>([^<]+)</a>.*?'
        r'>([^<]*)</a>.*?>([^<]*)</a>.*?>([^<]*)</a>', re.S)
    for page in range(3):  # newest 75 jobs is plenty when checking every 30 min
        url = ("https://www.apexsystems.com/search-results-usa"
               "?sort=lastposteddesc&page=%d" % page)
        page_html = fetch(url)
        for link, title, city, state, posted in row_re.findall(page_html):
            if state.strip() != "MN":
                continue
            title = html.unescape(title.strip())
            jobs.append({
                "id": "apex:" + link,
                "firm": "Apex Systems",
                "title": title,
                "location": city.strip() + ", MN",
                "type": "",
                "posted": posted.strip(),
                "contact": "",
                "link": "https://www.apexsystems.com" + link,
                "desc": "",
            })
    return jobs


def brooksource_jobs():
    jobs = []
    for page in (1, 2, 3):
        url = ("https://jobs.brooksource.com/jobs/wp-json/wp/v2/job-listings"
               "?per_page=100&page=%d" % page)
        try:
            items = json.loads(fetch(url))
        except Exception:
            break  # past the last page
        if not items:
            break
        for it in items:
            meta = it.get("meta", {})
            location = meta.get("_job_location", "")
            remote = meta.get("_remote_position") == 1
            if not (in_twin_cities(location) or "minnesota" in location.lower() or remote):
                continue
            jobs.append({
                "id": "brooksource:%s" % it["id"],
                "firm": "Brooksource",
                "title": html.unescape(it["title"]["rendered"]),
                "location": location + (" (remote)" if remote else ""),
                "type": "",
                "posted": "",  # feed date is a refresh date, not the real post date
                "contact": "",
                "link": it.get("link", ""),
                "desc": strip_tags(it.get("content", {}).get("rendered", "")),
            })
    return jobs


def experis_jobs():
    # Same search the Experis site runs: 40 miles around downtown Minneapolis.
    body = {"filter": {
        "page": "1", "searchKeyword": "", "searchkeyword": "", "offset": 0,
        "totalCount": 0, "limit": 100, "haslocation": True, "language": "",
        "latitude": 44.9778, "longitude": -93.2650, "radius": 40,
    }}
    data = json.loads(fetch("https://www.experis.com/api/services/Jobs/searchjobs", body))
    jobs = []
    for it in data.get("jobsItems", []):
        jobs.append({
            "id": "experis:%s" % it["jobID"],
            "firm": "Experis",
            "title": it.get("jobTitle", ""),
            "location": it.get("jobLocation", ""),
            "type": it.get("employmentType") or "",
            "posted": (it.get("publishfromDate") or "")[:10],
            "contact": it.get("jobContactEmail") or "",
            "link": "https://www.experis.com" + it.get("jobURL", ""),
            "desc": strip_tags(it.get("publicDescription", "")),
        })
    return jobs


FIRMS = [
    ("Apex Systems", apex_jobs),
    ("Brooksource", brooksource_jobs),
    ("Experis", experis_jobs),
]


# ---------- memory (sqlite) ----------

def open_db():
    db = sqlite3.connect(DB_FILE)
    db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, title TEXT, seen_at TEXT)")
    return db


def is_seen(db, job_id):
    return db.execute("SELECT 1 FROM seen WHERE id = ?", (job_id,)).fetchone() is not None


def mark_seen(db, job):
    db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?, datetime('now'))",
               (job["id"], job["title"]))


# ---------- AI review (Gemini) ----------

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

CANDIDATE = """
Candidate:
- Computer Science new grad, University of Minnesota, graduating December 2026.
- Strong in C++, C, Python; full-stack web (JavaScript); Linux; SQL basics.
- Has NO Java experience. No industry internship; experience is TA work and projects.
- Work authorization: F-1 OPT from January 2027 with STEM extension = can work
  up to 3 years with NO sponsorship needed. Cannot take roles that require US
  citizenship, a green card, or a security clearance.
- Wants software / IT / QA / data / support roles. Contract or contract-to-hire is fine.
"""

PROMPT = """You screen job postings for one candidate.
%s
For each job below decide:
- "apply": entry-level friendly (0-2 years), matches his skills, no blocker.
- "maybe": could work but has a concern (e.g. asks 2-3 years, unclear stack,
  or says "no sponsorship now or in the future").
- "skip": needs 3+ years, senior-level, wrong field, Java-heavy, or needs
  citizenship / green card / clearance.

Return ONLY a JSON list, one object per job, in this form:
[{"id": "<job id>", "verdict": "apply|maybe|skip", "reason": "<under 15 words>"}]

Jobs:
%s
"""


def apex_description(link):
    page_html = fetch(link)
    text = strip_tags(page_html)
    start = text.find("Job Description")
    if start >= 0:
        text = text[start:]
    return re.sub(r"\s+", " ", text)[:3000]


def ask_gemini(jobs):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    parts = []
    for job in jobs:
        desc = re.sub(r"\s+", " ", job["desc"])[:3000]
        parts.append("ID: %s\nTitle: %s\nLocation: %s\nDescription: %s\n"
                     % (job["id"], job["title"], job["location"], desc or "(none)"))
    body = {
        "contents": [{"parts": [{"text": PROMPT % (CANDIDATE, "\n---\n".join(parts))}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    url = ("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
           % (GEMINI_MODEL, key))
    data = json.loads(fetch(url, body))
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    verdicts = {}
    for item in json.loads(text):
        verdicts[item["id"]] = (item.get("verdict", "maybe"), item.get("reason", ""))
    return verdicts


def ai_review(jobs):
    # Apex lists have no description, so open each Apex job page first.
    for job in jobs:
        if job["firm"] == "Apex Systems" and not job["desc"]:
            try:
                job["desc"] = apex_description(job["link"])
            except Exception:
                pass
    verdicts = {}
    for i in range(0, len(jobs), 15):  # 15 jobs per request keeps it small
        result = ask_gemini(jobs[i:i + 15])
        if result is None:
            return False
        verdicts.update(result)
    for job in jobs:
        job["verdict"], job["reason"] = verdicts.get(job["id"], ("maybe", "AI gave no answer"))
    return True


# ---------- email ----------

def job_lines(job):
    line = "- %s | %s | %s" % (job["title"], job["firm"], job["location"])
    if job["type"]:
        line += " | " + job["type"]
    if job["posted"]:
        line += " | posted " + job["posted"]
    lines = [line]
    if job.get("reason"):
        lines.append("  why: " + job["reason"])
    lines.append("  " + job["link"])
    if job["contact"]:
        lines.append("  recruiter: " + job["contact"])
    return lines


def build_email(new_jobs, errors, ai_used):
    lines = []
    if ai_used:
        sections = [("APPLY", "apply"), ("MAYBE", "maybe")]
        for heading, verdict in sections:
            group = [j for j in new_jobs if j.get("verdict") == verdict]
            if group:
                lines.append("\n== " + heading + " ==")
                for job in group:
                    lines.extend(job_lines(job))
        skipped = [j for j in new_jobs if j.get("verdict") == "skip"]
        if skipped:
            lines.append("\n(AI skipped %d: %s)" % (
                len(skipped), "; ".join(j["title"] for j in skipped)))
    else:
        lines.append("(AI check off or failed - showing all keyword matches)")
        for job in new_jobs:
            lines.extend(job_lines(job))
    if errors:
        lines.append("\n(Could not check: " + ", ".join(errors) + ")")
    return "\n".join(lines).strip()


def send_email(subject, body):
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("EMAIL_TO", user)
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


# ---------- main ----------

def main():
    dry_run = "--dry-run" in sys.argv
    db = open_db()
    new_jobs = []
    errors = []

    for name, get_jobs in FIRMS:
        try:
            jobs = get_jobs()
        except Exception as e:
            print("%s: FAILED (%s)" % (name, e))
            errors.append(name)
            continue
        good = [j for j in jobs if is_good(j["title"], j["desc"])]
        already = set(j["id"] for j in new_jobs)
        fresh = []
        for j in good:
            if not is_seen(db, j["id"]) and j["id"] not in already:
                fresh.append(j)
                already.add(j["id"])
        print("%s: %d local jobs, %d fit, %d new" % (name, len(jobs), len(good), len(fresh)))
        new_jobs.extend(fresh)

    if not new_jobs:
        print("Nothing new.")
        return

    try:
        ai_used = ai_review(new_jobs)
    except Exception as e:
        print("AI check FAILED (%s)" % e)
        ai_used = False

    good_count = len([j for j in new_jobs if j.get("verdict") != "skip"])
    body = build_email(new_jobs, errors, ai_used)
    print("\n" + body)

    if dry_run:
        return

    if good_count > 0:
        subject = "%d new staffing-firm jobs (Twin Cities)" % good_count
        if ai_used:
            applies = len([j for j in new_jobs if j.get("verdict") == "apply"])
            subject = "%d apply / %d new staffing-firm jobs" % (applies, good_count)
        send_email(subject, body)
        print("\nEmailed.")
    else:
        print("\nAI skipped everything new - no email.")

    for job in new_jobs:
        mark_seen(db, job)
    db.commit()


if __name__ == "__main__":
    main()
