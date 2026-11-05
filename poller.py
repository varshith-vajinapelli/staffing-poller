"""
Staffing firm job poller.

Checks Apex Systems, Brooksource, Experis, Robert Half, TEKsystems,
Insight Global, Randstad and Beacon Hill for new Twin Cities jobs that fit an
entry-level candidate,
has Gemini read the survivors, remembers what it already sent in jobs.db,
and emails only the new matches.

Run:  python poller.py            (check + email)
      python poller.py --dry-run  (check + print, no email, nothing saved)
"""

import datetime
import html
import json
import os
import re
import smtplib
import sqlite3
import sys
import time
import urllib.request
from email.mime.text import MIMEText

DB_FILE = "jobs.db"
MAX_AI_FAILS = 4   # failed AI runs in a row before sending unfiltered (runs are 30 min apart)

# Only show jobs posted within this many days. Change this number to adjust.
# (Brooksource has no real post date, so its jobs are never dropped by age.)
MAX_AGE_DAYS = 7
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Title must contain one of these to count as a role worth looking at.
GOOD_WORDS = [
    "developer", "software", "engineer", "programmer", "web", "application",
    "qa", "quality assurance", "test", "analyst", "data", "it ", "systems",
    "junior", "associate", "automation", "quality engineer", "support",
    "technician",
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
    # help desk / desktop support roles
    "help desk", "helpdesk", "desktop support", "service desk",
]

# Description containing any of these is skipped.
# Only clear requirement phrases - a bare mention like "open to US citizens and
# all authorized candidates" should not drop the job. Anything these miss
# still goes through the AI check, which skips citizenship-only roles.
BAD_DESC = [
    "citizenship required", "citizenship is required",
    "requires us citizenship", "requires u.s. citizenship",
    "must be a us citizen", "must be a u.s. citizen",
    "must be us citizen", "must be u.s. citizen",
    "must be a united states citizen",
    "us citizens only", "u.s. citizens only", "citizens only",
    "green card holders only", "gc holders only",
    "security clearance", "secret clearance", "public trust",
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
    # "3+ years of experience" -> skip if the smallest number is 3 or more.
    # Only counts a number when "experience" follows within ~60 characters, so
    # "18 years of age" or "in business for 20 years" are ignored.
    # Anything this misses still goes through the AI check.
    nums = []
    for m in re.finditer(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?years?", desc):
        after = desc[m.end():m.end() + 60]
        if "experience" in after or "exp " in after or "exp." in after:
            nums.append(int(m.group(1)))
    nums = [n for n in nums if n < 30]
    return bool(nums) and min(nums) >= 3


def too_old(posted):
    # posted is "2026-11-04" or "11/4/2026"; an empty date is never too old
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", posted)
    if m:
        day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", posted)
        if not m:
            return False
        day = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return (datetime.date.today() - day).days > MAX_AGE_DAYS


def is_good(title, desc=""):
    t = " " + title.lower() + " "
    if not any(w in t for w in GOOD_WORDS):
        return False
    # "javascript" contains "java", so hide it before checking BAD_WORDS
    if any(w in t.replace("javascript", "js") for w in BAD_WORDS):
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


def wordpress_jobs(firm, id_prefix, base_url, pages, real_dates):
    # Brooksource and Beacon Hill both run the same WordPress job plugin,
    # which has a public newest-first job feed.
    jobs = []
    for page in range(1, pages + 1):
        url = base_url + "/wp-json/wp/v2/job-listings?per_page=100&page=%d" % page
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
                "id": "%s:%s" % (id_prefix, it["id"]),
                "firm": firm,
                "title": html.unescape(it["title"]["rendered"]),
                "location": location + (" (remote)" if remote else ""),
                "type": "",
                "posted": it.get("date", "")[:10] if real_dates else "",
                "contact": "",
                "link": it.get("link", ""),
                "desc": strip_tags(it.get("content", {}).get("rendered", "")),
            })
    return jobs


def brooksource_jobs():
    # Brooksource's feed date is a refresh date, not the real post date.
    return wordpress_jobs("Brooksource", "brooksource",
                          "https://jobs.brooksource.com/jobs", 3, False)


def beacon_hill_jobs():
    # Newest 300 US jobs (about a week), keep Minnesota ones.
    return wordpress_jobs("Beacon Hill", "beaconhill",
                          "https://bhsg.com/jobs", 3, True)


def randstad_jobs():
    # Their search page ignores location, so read the newest US computer/IT
    # jobs (30 per page) from the data the page carries, and keep Minnesota.
    jobs = []
    for page in (1, 2, 3, 4):
        url = "https://www.randstadusa.com/jobs/r-computer-and-mathematical-occupations/"
        if page > 1:
            url += "page-%d/" % page
        page_html = fetch(url)
        found = re.search(r"const data = (\{.*?\});\s*\n", page_html, re.S)
        if not found:
            break
        data = json.loads(found.group(1))
        for it in data.get("ecommerce", {}).get("impressions", []):
            if it.get("region") != "Minnesota":
                continue
            kinds = it.get("employment_type", "")
            jobs.append({
                "id": "randstad:%s" % it.get("reference_number"),
                "firm": "Randstad",
                "title": it.get("job_title", "").strip(" -"),
                "location": "%s, MN" % it.get("city", ""),
                "type": "Contract" if "CONTRACTOR" in kinds else "",
                "posted": it.get("launch_date", ""),
                "contact": "",
                "link": it.get("url", ""),
                "desc": "",
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


def robert_half_jobs():
    # Server-rendered search page for Minneapolis tech jobs, 25 per page.
    jobs = []
    card_re = re.compile(r'<rhcl-job-card job-id="([^"]+)".*?</rhcl-job-card>', re.S)
    for page in (1, 2, 3, 4):
        url = ("https://www.roberthalf.com/us/en/jobs/minneapolis-mn/technology"
               "?pagenumber=%d" % page)
        page_html = fetch(url)
        cards = list(card_re.finditer(page_html))
        if not cards:
            break
        for m in cards:
            card = m.group(0)

            def slot(name):
                found = re.search(r'data-subslot="%s">(.*?)</li>' % name, card, re.S)
                return found.group(1).strip() if found else ""

            link = re.search(r'href="([^"]+)"', card).group(1)
            title = re.search(r'slot="headline"[^>]*>(.*?)</a>', card, re.S).group(1)
            jobs.append({
                "id": "roberthalf:" + m.group(1),
                "firm": "Robert Half",
                "title": html.unescape(title.strip()),
                "location": html.unescape(slot("location")),
                "type": html.unescape(slot("type")),
                "posted": slot("date")[:10],
                "contact": "",
                "link": link,
                "desc": strip_tags(html.unescape(slot("copy"))),
            })
    return jobs


def teksystems_jobs():
    # Same request their search page makes, filtered to Minnesota.
    jobs = []
    for start in (0, 50, 100):
        body = {
            "lang": "en_us", "deviceType": "desktop", "country": "us",
            "pageName": "search-results", "ddoKey": "refineSearch",
            "sortBy": "Most recent", "subsearch": "", "from": start,
            "jobs": True, "counts": True, "all_fields": ["state"], "size": 50,
            "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
            "pageId": "page12", "siteType": "external", "keywords": "",
            "global": True, "selected_fields": {"state": ["Minnesota"]},
            "locationData": {},
        }
        data = json.loads(fetch("https://careers.teksystems.com/widgets", body))
        items = data["refineSearch"]["data"]["jobs"]
        if not items:
            break
        for it in items:
            slug = re.sub(r"[^A-Za-z0-9]+", "-", it.get("title", "")).strip("-")
            jobs.append({
                "id": "tek:" + it["jobId"],
                "firm": "TEKsystems",
                "title": it.get("title", ""),
                "location": "%s, MN" % it.get("city", ""),
                "type": it.get("type") or "",
                "posted": (it.get("postedDate") or "")[:10],
                "contact": "",
                "link": "https://careers.teksystems.com/us/en/job/%s/%s" % (it["jobId"], slug),
                "desc": it.get("descriptionTeaser") or "",
            })
        if len(items) < 50:
            break
    return jobs


def insight_global_jobs():
    # Their API can't filter by place, so read the newest 200 US jobs and keep MN.
    jobs = []
    for page in (1, 2, 3, 4):
        url = ("https://insightglobal.com/all/jobs?keyword=&page=%d&size=50"
               "&sort=postedDate,desc&filter.includeOnlyRemoteWork=false"
               "&filter.status=active" % page)
        data = json.loads(fetch(url))
        for it in data.get("jobs", []):
            address = it.get("workAddress") or {}
            if address.get("administrativeArea") != "MN":
                continue
            location = "%s, MN" % address.get("locality", "")
            if it.get("workRemote"):
                location += " (remote)"
            jobs.append({
                "id": "insight:" + it["requisitionId"],
                "firm": "Insight Global",
                "title": it.get("jobTitle", ""),
                "location": location,
                "type": it.get("jobType") or "",
                "posted": (it.get("postedDate") or "")[:10],
                "contact": "",
                "link": "https://insightglobal.com/jobs/" + it["requisitionId"],
                "desc": "",
            })
    return jobs


FIRMS = [
    ("Apex Systems", apex_jobs),
    ("Brooksource", brooksource_jobs),
    ("Experis", experis_jobs),
    ("Robert Half", robert_half_jobs),
    ("TEKsystems", teksystems_jobs),
    ("Insight Global", insight_global_jobs),
    ("Randstad", randstad_jobs),
    ("Beacon Hill", beacon_hill_jobs),
]


# ---------- memory (sqlite) ----------

def open_db():
    db = sqlite3.connect(DB_FILE)
    db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, title TEXT, seen_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER)")
    return db


def get_ai_fails(db):
    row = db.execute("SELECT value FROM state WHERE key = 'ai_fails'").fetchone()
    if row is None:
        return 0
    return row[0]


def set_ai_fails(db, count):
    db.execute("INSERT OR REPLACE INTO state VALUES ('ai_fails', ?)", (count,))


def is_seen(db, job_id):
    return db.execute("SELECT 1 FROM seen WHERE id = ?", (job_id,)).fetchone() is not None


def mark_seen(db, job):
    db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?, datetime('now'))",
               (job["id"], job["title"]))


# ---------- AI review (Gemini) ----------

# Tried in order: the better model first, the lighter one if the first
# hits its free daily limit or fails.
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

CANDIDATE = """
Candidate:
- Computer Science new grad, University of Minnesota, graduating December 2026.
- Strong in C++, C, Python; full-stack web (JavaScript); Linux user (not a
  sysadmin); SQL basics; Git; GitHub Actions.
- Has NO Java experience. No industry internship.
- Experience: undergrad TA for C++ (software design) and intro Python courses;
  undergrad research on a C++ peer-to-peer audio/video streaming system.
- Projects: GopherEvents (shipped full-stack web app); a job-alert tool that
  polls job APIs on GitHub Actions and uses an LLM to screen postings;
  open-source contributions to Wikimedia.
- Long-term interest: backend / systems engineering.
- Work authorization: F-1 OPT from January 2027 with STEM extension = can work
  up to 3 years with NO sponsorship needed. Cannot take roles that require US
  citizenship, a green card, or a security clearance.
- Wants software / QA / test / data / IT roles. Contract or contract-to-hire
  is fine. Does NOT want help desk or desktop support roles; mark those
  "skip" even if entry-level.
"""

PROMPT = """You screen job postings for one candidate.
%s
Be strict. Read the REQUIRED qualifications, not just the title.
For each job below decide:
- "apply": a new grad with only coursework, projects and TA work could
  realistically meet MOST of the required list. Entry-level wording
  (junior, associate, entry level, 0-2 years, new grad) is a strong signal.
- "maybe": could work but has a concern (asks 2-3 years, asks for one or two
  specific tools he hasn't used, unclear stack, or says "no sponsorship now
  or in the future").
- "skip": needs 3+ years, senior-level, wrong field, Java REQUIRED as the
  main language with no alternative (a list like "Java, .NET, or OOP" or
  "Python or Java" is fine - his C++ counts as OOP), needs
  citizenship / green card / clearance, OR the required list is mostly
  specialist hands-on experience a new grad won't have (words like "deep",
  "extensive", "expert", "proven", or several named enterprise tools/products
  like RHEL admin, Kerberos, Proxmox, Splunk, SAP, Epic, Active Directory
  administration).
Do NOT mark "apply" just because a skill word (Linux, Python, SQL) appears in
both the job and the candidate profile; judge the depth the job requires.
Software testing / QA / test analyst roles count as engineering roles he
wants, even if the title also says "analyst" or "BA".
If you are unsure between "apply" and "skip", choose "maybe". Only "skip"
when there is a clear blocker.

Return ONLY a JSON list, one object per job, in this form:
[{"id": "<job id>", "verdict": "apply|maybe|skip", "reason": "<under 15 words>"}]

Jobs:
%s
"""


def apex_description(job):
    text = strip_tags(fetch(job["link"]))
    start = text.find("Job Description")
    if start >= 0:
        text = text[start:]
    return text


def json_description(job):
    # TEKsystems and Randstad job pages carry the posting in a "description" field.
    page_html = fetch(job["link"])
    found = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){50,})"', page_html)
    if not found:
        return job["desc"]
    text = json.loads('"' + found.group(1) + '"')
    return strip_tags(html.unescape(text))


def insight_global_description(job):
    req_id = job["id"].split(":", 1)[1]
    data = json.loads(fetch("https://insightglobal.com/all/jobs/%s/details" % req_id))
    return strip_tags(data.get("description", ""))


# Firms whose job list has no (or only a short) description: open each job page.
DESCRIBERS = {
    "Apex Systems": apex_description,
    "TEKsystems": json_description,
    "Randstad": json_description,
    "Insight Global": insight_global_description,
}


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
    text = None
    # Gemini is sometimes briefly overloaded (503), so try each model up to
    # 3 times with a short wait before moving on.
    for attempt in range(3):
        for model in GEMINI_MODELS:
            url = ("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
                   % (model, key))
            try:
                data = json.loads(fetch(url, body))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                print("AI model used: " + model)
                break
            except Exception as e:
                print("AI model %s failed (%s), trying next" % (model, e))
        if text is not None:
            break
        time.sleep(20)
    if text is None:
        raise RuntimeError("all Gemini models failed")
    verdicts = {}
    for item in json.loads(text):
        verdicts[item["id"]] = (item.get("verdict", "maybe"), item.get("reason", ""))
    return verdicts


def ai_review(jobs):
    for job in jobs:
        describe = DESCRIBERS.get(job["firm"])
        if describe:
            try:
                job["desc"] = re.sub(r"\s+", " ", describe(job))[:3000]
            except Exception:
                pass
    if not os.environ.get("GEMINI_API_KEY"):
        return False
    verdicts = {}
    for i in range(0, len(jobs), 15):  # 15 jobs per request keeps it small
        try:
            verdicts.update(ask_gemini(jobs[i:i + 15]) or {})
        except Exception as e:
            # Keep the batches that worked; this batch shows up as "maybe".
            print("AI batch failed (%s)" % e)
    if not verdicts:
        return False
    # The model sometimes leaves a job out; ask once more for just those.
    missing = [j for j in jobs if j["id"] not in verdicts]
    if missing:
        try:
            verdicts.update(ask_gemini(missing) or {})
        except Exception:
            pass
    for job in jobs:
        job["verdict"], job["reason"] = verdicts.get(job["id"], ("maybe", "AI was busy, check this one yourself"))
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


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def nice_date(posted):
    # "2026-11-04" or "11/4/2026" -> "Nov 4"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", posted)
    if m:
        return "%s %d" % (MONTHS[int(m.group(2)) - 1], int(m.group(3)))
    m = re.match(r"(\d{1,2})/(\d{1,2})/\d{4}", posted)
    if m:
        return "%s %d" % (MONTHS[int(m.group(1)) - 1], int(m.group(2)))
    return posted


def job_html(job):
    e = html.escape
    details = [job["firm"], job["location"]]
    if job["type"]:
        details.append(job["type"])
    if job["posted"]:
        details.append("posted " + nice_date(job["posted"]))
    out = '<p style="margin:0 0 16px 0">'
    out += '<b><a href="%s">%s</a></b><br>' % (e(job["link"]), e(job["title"].strip()))
    out += '<span style="color:#555">%s</span>' % e(" · ".join(details))
    if job.get("reason"):
        out += "<br>%s" % e(job["reason"])
    if job["contact"]:
        out += '<br>Recruiter: <a href="mailto:%s">%s</a>' % (e(job["contact"]), e(job["contact"]))
    out += "</p>"
    return out


def build_html(new_jobs, errors, ai_used):
    parts = ['<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.4">']
    if ai_used:
        for heading, verdict in [("Apply", "apply"), ("Maybe", "maybe")]:
            group = [j for j in new_jobs if j.get("verdict") == verdict]
            if group:
                parts.append('<h3 style="margin:20px 0 10px 0">%s (%d)</h3>' % (heading, len(group)))
                for job in group:
                    parts.append(job_html(job))
        skipped = [j for j in new_jobs if j.get("verdict") == "skip"]
        if skipped:
            items = []
            for j in skipped:
                text = j["title"].strip()
                if j.get("reason"):
                    text += " (" + j["reason"] + ")"
                items.append(html.escape(text))
            parts.append('<p style="color:#888;font-size:12px">Skipped:<br>%s</p>'
                         % "<br>".join(items))
    else:
        parts.append('<p style="color:#888;font-size:12px">AI check off or failed, '
                     'showing all keyword matches.</p>')
        for job in new_jobs:
            parts.append(job_html(job))
    if errors:
        parts.append('<p style="color:#888;font-size:12px">Could not check: %s</p>'
                     % html.escape(", ".join(errors)))
    parts.append("</div>")
    return "\n".join(parts)


def send_email(subject, body):
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("EMAIL_TO", user)
    msg = MIMEText(body, "html")
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
        good = [j for j in jobs
                if is_good(j["title"], j["desc"]) and not too_old(j["posted"])]
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

    # If the AI failed, hold the jobs for the next run instead of sending them
    # unfiltered. After MAX_AI_FAILS failed runs in a row (~2 hours), send anyway.
    if not dry_run:
        if ai_used:
            set_ai_fails(db, 0)
        else:
            fails = get_ai_fails(db) + 1
            if fails < MAX_AI_FAILS:
                set_ai_fails(db, fails)
                db.commit()
                print("AI failed (%d in a row) - holding %d jobs for next run"
                      % (fails, len(new_jobs)))
                return
            print("AI failed %d runs in a row - sending unfiltered" % fails)
            set_ai_fails(db, 0)

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
        send_email(subject, build_html(new_jobs, errors, ai_used))
        print("\nEmailed.")
    else:
        print("\nAI skipped everything new - no email.")

    for job in new_jobs:
        mark_seen(db, job)
    db.commit()


if __name__ == "__main__":
    main()
