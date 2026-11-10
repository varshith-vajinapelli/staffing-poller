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

import csv
import datetime
import html
import json
import os
import re
import smtplib
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

DB_FILE = "jobs.db"
# The AI treats "no sponsorship in the future" postings differently for these.
STAFFING_AGENCIES = ["Apex Systems", "Brooksource", "Experis", "Robert Half", "TEKsystems",
                     "Insight Global", "Randstad", "Beacon Hill"]

DESC_LIMIT = 6000  # characters of each posting the AI reads (requirements come last)
EXTRA_LIMIT = 600  # characters of visa/sponsorship lines kept from past that cut
# Words that mark a work-visa line. OPT/CPT only in capitals ("opt out" isn't one).
VISA_WORDS = re.compile(r"sponsor|\bvisas?\b|\bh-?1b\b|authori[sz]ed to work|"
                        r"work authori[sz]ation|(?-i:\b(?:OPT|CPT)\b)", re.I)
AI_BATCH = 5       # jobs per AI request; fewer = more careful reading of each job
MAX_AI_FAILS = 4   # failed AI runs in a row before sending unfiltered (runs are 30 min apart)
ALERT_WAIT_HOURS = 3   # problems wait this long for a job email before getting their own
ZERO_RUNS = 12         # a firm with 0 jobs this many runs in a row (6 hours) gets flagged

# Only show jobs posted within this many days. Change this number to adjust.
# (Brooksource has no real post date, so its jobs are never dropped by age.)
MAX_AGE_DAYS = 3
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Title must contain one of these to count as a role worth looking at.
GOOD_WORDS = [
    "developer", "software", "engineer", "programmer", "web", "application",
    "qa", "quality assurance", "test", "analyst", "data", "it ", "systems",
    "junior", "associate", "automation", "quality engineer", "support",
    "entry", "intern", "configuration",
    "sdet", "devops", "cloud", "full stack", "full-stack", "backend", "back-end",
    "frontend", "front-end", "machine learning", "robotics", "business intelligence",
    "quantitative", "technology leadership", "rotational", "new grad", "graduate",
    "early career",
]

# Title containing any of these is skipped (too senior or wrong stack).
BAD_WORDS = [
    "senior", "sr.", "sr", "lead", "principal", "manager", "director",
    "architect", "staff ", "head of",
    "java", "cobol", "mainframe", "salesforce", "servicenow", "sap ",
    "workday", "oracle", "dynamics", "nurse", "medical rep", "accountant",
    "payroll", "recruiter",
    # non-software fields that share words like "engineer" / "technician"
    "lab technician", "laboratory", "manufacturing", "mechanical", "materials",
    "medical device", "hardware", "failure analysis", "supplier",
    "surveillance", "project engineer", "design engineer",
    "engineering technician", "repair", "accounts payable",
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

# Places outside Minnesota worth moving to: (state code, state name, metro cities).
# All of Minnesota and fully remote US jobs are always kept too.
PLACES = [
    ("WI", "wisconsin", [
        "milwaukee", "waukesha", "brookfield", "wauwatosa", "west allis",
        "menomonee falls", "new berlin", "glendale", "pewaukee", "madison",
        "middleton", "fitchburg", "sun prairie", "verona"]),
    ("MI", "michigan", [
        "detroit", "ann arbor", "troy", "southfield", "dearborn", "farmington hills",
        "novi", "auburn hills", "livonia", "warren", "sterling heights", "pontiac",
        "royal oak", "bloomfield hills", "northville", "plymouth", "grand rapids",
        "kentwood", "grandville"]),
    ("GA", "georgia", [
        "atlanta", "alpharetta", "marietta", "sandy springs", "roswell", "norcross",
        "kennesaw", "smyrna", "peachtree corners", "johns creek", "duluth", "decatur",
        "lawrenceville", "dunwoody", "buford", "cumming", "lithia springs",
        "douglasville", "mcdonough", "conyers", "stockbridge", "fayetteville",
        "peachtree city", "woodstock", "acworth", "tucker", "suwanee", "college park"]),
    ("IL", "illinois", [
        "chicago", "schaumburg", "naperville", "oak brook", "oakbrook terrace",
        "downers grove", "lisle", "deerfield", "northbrook", "lombard", "evanston",
        "rosemont", "itasca", "lincolnshire", "skokie", "elk grove village",
        "hoffman estates", "des plaines", "riverwoods", "bannockburn", "aurora",
        "wheaton", "warrenville", "oak brook", "glenview", "arlington heights",
        "rolling meadows", "vernon hills", "mettawa", "westchester", "cary"]),
    ("TX", "texas", [
        # Austin
        "austin", "round rock", "cedar park", "georgetown", "pflugerville", "leander",
        "san marcos", "kyle",
        # Dallas - Fort Worth
        "dallas", "fort worth", "plano", "irving", "frisco", "richardson", "addison",
        "arlington", "carrollton", "lewisville", "mckinney", "allen", "grapevine",
        "coppell", "the colony", "southlake", "denton", "garland", "farmers branch",
        "las colinas", "westlake", "mesquite", "grand prairie", "flower mound",
        # Houston
        "houston", "the woodlands", "sugar land", "katy", "spring", "pearland",
        "cypress", "humble", "conroe", "stafford", "bellaire", "pasadena",
        "missouri city", "tomball", "league city", "webster", "baytown",
        # San Antonio
        "san antonio", "new braunfels", "schertz", "boerne", "live oak"]),
    ("AZ", "arizona", [   # Phoenix
        "phoenix", "scottsdale", "tempe", "chandler", "mesa", "gilbert", "glendale",
        "peoria", "goodyear", "tolleson", "surprise", "avondale"]),
    ("NC", "north carolina", [
        # Charlotte
        "charlotte", "matthews", "huntersville", "concord", "mooresville", "cornelius",
        "pineville", "indian trail",
        # Raleigh - Durham
        "raleigh", "durham", "cary", "morrisville", "research triangle", "rtp",
        "chapel hill", "apex", "wake forest", "garner", "holly springs"]),
    ("OH", "ohio", [   # Columbus
        "columbus", "dublin", "westerville", "worthington", "hilliard", "grove city",
        "new albany", "gahanna", "reynoldsburg", "lewis center", "delaware", "polaris"]),
    ("MO", "missouri", [   # Kansas City, Missouri side
        "kansas city", "north kansas city", "lee's summit", "lees summit",
        "independence", "liberty", "riverside", "blue springs"]),
    ("KS", "kansas", [   # Kansas City, Kansas side
        "kansas city", "overland park", "olathe", "lenexa", "leawood", "shawnee",
        "merriam", "mission"]),
    ("TN", "tennessee", [   # Nashville
        "nashville", "franklin", "brentwood", "murfreesboro", "smyrna", "hendersonville",
        "mount juliet", "mt. juliet", "antioch", "la vergne", "gallatin"]),
]

# Center points for searches that take a location (40-mile radius around each).
METRO_CENTERS = [
    ("Minneapolis", 44.9778, -93.2650),
    ("Milwaukee", 43.0389, -87.9065),
    ("Madison", 43.0731, -89.4012),
    ("Detroit", 42.3314, -83.0458),
    ("Grand Rapids", 42.9634, -85.6681),
    ("Atlanta", 33.7490, -84.3880),
    ("Chicago", 41.8781, -87.6298),
    ("Austin", 30.2672, -97.7431),
    ("Dallas", 32.7767, -96.7970),
    ("Fort Worth", 32.7555, -97.3308),
    ("Houston", 29.7604, -95.3698),
    ("San Antonio", 29.4241, -98.4936),
    ("Phoenix", 33.4484, -112.0740),
    ("Charlotte", 35.2271, -80.8431),
    ("Raleigh", 35.7796, -78.6382),
    ("Columbus", 39.9612, -82.9988),
    ("Kansas City", 39.0997, -94.5786),
    ("Nashville", 36.1627, -86.7816),
]


# ---------- small helpers ----------

def fetch(url, data=None, timeout=30, retry=True):
    # Job sites sometimes fail for a moment (502, 503, a timeout); wait and try
    # once more before giving up. Gemini calls pass retry=False - they have their own.
    try:
        return fetch_once(url, data, timeout)
    except urllib.error.HTTPError as e:
        if not retry or e.code not in (500, 502, 503, 504):
            raise
    except (urllib.error.URLError, TimeoutError):
        if not retry:
            raise
    time.sleep(5)
    return fetch_once(url, data, timeout)


def fetch_once(url, data, timeout):
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text or ""))


def years_asked(desc):
    # Smallest "X years of experience" number in the description, or None.
    # Only counts a number when "experience" follows within ~60 characters, so
    # "18 years of age" or "in business for 20 years" are ignored.
    # Anything this misses still goes through the AI check.
    nums = []
    for m in re.finditer(r"(\d+)\s*\+?\s*(?:(?:-|–|—|to)\s*\d+\s*\+?\s*)?years?", desc):
        after = desc[m.end():m.end() + 60]
        if "experience" in after or "exp " in after or "exp." in after:
            nums.append(int(m.group(1)))
    nums = [n for n in nums if n < 30]
    if not nums:
        return None
    return min(nums)


def too_many_years(desc):
    years = years_asked(desc)
    return years is not None and years >= 3


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


def has_word(text, word):
    # Whole-word match: "lead" matches "Lead Engineer" but not "Leadership",
    # "architect" doesn't match "Architecture". A word ending in "*" matches as
    # a prefix ("actuar*" -> actuary, actuarial).
    word = word.strip().lower()
    if word.endswith("*"):
        pattern = r"(?<![a-z0-9])" + re.escape(word[:-1])
    else:
        pattern = r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def drop_reason(title, desc=""):
    # Returns why the keyword filter drops this job, or "" if it passes.
    t = " " + title.lower() + " "
    if not any(w in t for w in GOOD_WORDS):
        return "title has no tech word"
    # "javascript" contains "java", so hide it before checking BAD_WORDS
    t2 = t.replace("javascript", "js")
    for w in BAD_WORDS:
        if has_word(t2, w):
            return "title word: %s" % w.strip()
    # Level III / IV / 3 / 4 / 5 right after the role ("Software Engineer III",
    # "IT Analyst 3") is mid/senior; a number elsewhere ("4 days on-site") is not.
    level = re.search(r"(engineer|developer|analyst|programmer|specialist|tester|"
                      r"administrator|consultant)\s*(iii|iv|3|4|5)\b", t2)
    if level:
        return "title level: %s" % level.group(2)
    d = desc.lower()
    for w in BAD_DESC:
        if w in d:
            return "description: %s" % w
    years = years_asked(d)
    if years is not None and years >= 3:
        return "asks %d+ years" % years
    return ""


def is_good(title, desc=""):
    return drop_reason(title, desc) == ""


def in_twin_cities(location):
    loc = location.lower()
    return any(city in loc for city in TWIN_CITIES)


def in_minnesota(location):
    loc = location.lower()
    return in_twin_cities(location) or "minnesota" in loc or re.search(r"\bmn\b", loc) is not None


def wanted_place(location):
    # Minnesota, fully remote, or a city in one of the PLACES metros.
    loc = location.lower()
    if in_minnesota(location):
        return True
    if "remote" in loc and "hybrid" not in loc:
        return True
    for code, state, cities in PLACES:
        in_state = state in loc or re.search(r"\b%s\b" % code.lower(), loc) is not None
        if in_state and any(city in loc for city in cities):
            return True
    return False


# ---------- one function per firm; each returns a list of job dicts ----------

def apex_jobs():
    # Newest-first table of all US jobs; scan the first pages and keep wanted places.
    jobs = []
    row_re = re.compile(
        r'<tr class="(?:odd|even)">.*?href="(/job/[^"]+)".*?>([^<]+)</a>.*?'
        r'>([^<]*)</a>.*?>([^<]*)</a>.*?>([^<]*)</a>', re.S)
    for page in range(3):  # newest 75 jobs is plenty when checking every 30 min
        url = ("https://www.apexsystems.com/search-results-usa"
               "?sort=lastposteddesc&page=%d" % page)
        page_html = fetch(url)
        for link, title, city, state, posted in row_re.findall(page_html):
            location = "%s, %s" % (city.strip(), state.strip())
            if not wanted_place(location):
                continue
            title = html.unescape(title.strip())
            jobs.append({
                "id": "apex:" + link,
                "firm": "Apex Systems",
                "title": title,
                "location": location,
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
            if not (wanted_place(location) or remote):
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
    # Newest 300 US jobs (about a week), keep wanted places.
    return wordpress_jobs("Beacon Hill", "beaconhill",
                          "https://bhsg.com/jobs", 3, True)


def randstad_jobs():
    # Their search page ignores location, so read the newest US computer/IT
    # jobs (30 per page) from the data the page carries, and keep wanted places.
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
            location = "%s, %s" % (it.get("city", ""), it.get("region", ""))
            if not wanted_place(location):
                continue
            kinds = it.get("employment_type", "")
            jobs.append({
                "id": "randstad:%s" % it.get("reference_number"),
                "firm": "Randstad",
                "title": it.get("job_title", "").strip(" -"),
                "location": location,
                "type": "Contract" if "CONTRACTOR" in kinds else "",
                "posted": it.get("launch_date", ""),
                "contact": "",
                "link": it.get("url", ""),
                "desc": "",
            })
    return jobs


def experis_search(body):
    # Experis occasionally sends back a cut-off or garbled response, which shows
    # up as a JSON error. A second or third try almost always gets a clean one.
    url = "https://www.experis.com/api/services/Jobs/searchjobs"
    for attempt in range(3):
        try:
            return json.loads(fetch(url, body))
        except ValueError:
            if attempt == 2:
                raise
            time.sleep(5)


def experis_jobs():
    # Same search the Experis site runs: 40 miles around each metro center.
    jobs = []
    for name, lat, lon in METRO_CENTERS:
        body = {"filter": {
            "page": "1", "searchKeyword": "", "searchkeyword": "", "offset": 0,
            "totalCount": 0, "limit": 100, "haslocation": True, "language": "",
            "latitude": lat, "longitude": lon, "radius": 40,
        }}
        try:
            data = experis_search(body)
        except Exception as e:
            note_problem("fetch:Experis " + name, "Experis (%s): couldn't check (%s)" % (name, e))
            continue
        for it in data.get("jobsItems", []):
            jobs.append(experis_job(it))
    return jobs


def experis_job(it):
    return {
        "id": "experis:%s" % it["jobID"],
        "firm": "Experis",
        "title": it.get("jobTitle", ""),
        "location": it.get("jobLocation", ""),
        "type": it.get("employmentType") or "",
        "posted": (it.get("publishfromDate") or "")[:10],
        "contact": it.get("jobContactEmail") or "",
        "link": "https://www.experis.com" + it.get("jobURL", ""),
        "desc": strip_tags(it.get("publicDescription", "")),
    }


# Robert Half city pages to search, and how many pages (25 jobs each) to read.
ROBERT_HALF_CITIES = [
    ("minneapolis-mn", 4), ("chicago-il", 4), ("atlanta-ga", 4), ("milwaukee-wi", 2),
    ("madison-wi", 2), ("detroit-mi", 2), ("grand-rapids-mi", 2),
    ("dallas-tx", 4), ("houston-tx", 4), ("austin-tx", 4), ("fort-worth-tx", 2),
    ("san-antonio-tx", 2), ("phoenix-az", 4), ("charlotte-nc", 4), ("raleigh-nc", 2),
    ("columbus-oh", 2), ("kansas-city-mo", 2), ("nashville-tn", 2),
]


def robert_half_jobs():
    # Server-rendered search pages for tech jobs in each metro, 25 per page.
    # Four cities at a time; one by one takes about two minutes.
    jobs = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        waiting = [(city, pool.submit(robert_half_city, city, pages))
                   for city, pages in ROBERT_HALF_CITIES]
        for city, future in waiting:
            try:
                jobs.extend(future.result())
            except Exception as e:
                note_problem("fetch:Robert Half " + city, "Robert Half (%s): couldn't check (%s)" % (city, e))
    return jobs


def robert_half_city(city, pages):
    jobs = []
    card_re = re.compile(r'<rhcl-job-card job-id="([^"]+)".*?</rhcl-job-card>', re.S)
    for page in range(1, pages + 1):
        url = ("https://www.roberthalf.com/us/en/jobs/%s/technology"
               "?pagenumber=%d" % (city, page))
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
    # Same request their search page makes, one state at a time
    # (searching all states at once lets the busiest state crowd out the rest).
    jobs = []
    for state in ["Minnesota", "Wisconsin", "Michigan", "Georgia", "Illinois", "Texas",
                  "Arizona", "North Carolina", "Ohio", "Missouri", "Kansas", "Tennessee"]:
        try:
            jobs.extend(teksystems_state(state))
        except Exception as e:
            note_problem("fetch:TEKsystems " + state, "TEKsystems (%s): couldn't check (%s)" % (state, e))
    return jobs


def teksystems_state(state):
    jobs = []
    for start in (0, 50, 100):
        body = {
            "lang": "en_us", "deviceType": "desktop", "country": "us",
            "pageName": "search-results", "ddoKey": "refineSearch",
            "sortBy": "Most recent", "subsearch": "", "from": start,
            "jobs": True, "counts": True, "all_fields": ["state"], "size": 50,
            "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
            "pageId": "page12", "siteType": "external", "keywords": "",
            "global": True, "selected_fields": {"state": [state]},
            "locationData": {},
        }
        data = json.loads(fetch("https://careers.teksystems.com/widgets", body))
        items = data["refineSearch"]["data"]["jobs"]
        if not items:
            break
        for it in items:
            location = "%s, %s" % (it.get("city", ""), it.get("state", ""))
            if not wanted_place(location):
                continue
            slug = re.sub(r"[^A-Za-z0-9]+", "-", it.get("title", "")).strip("-")
            jobs.append({
                "id": "tek:" + it["jobId"],
                "firm": "TEKsystems",
                "title": it.get("title", ""),
                "location": location,
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
    # Their API can't filter by place, so read the newest 200 US jobs and keep
    # wanted places plus remote ones.
    jobs = []
    for page in (1, 2, 3, 4):
        url = ("https://insightglobal.com/all/jobs?keyword=&page=%d&size=50"
               "&sort=postedDate,desc&filter.includeOnlyRemoteWork=false"
               "&filter.status=active" % page)
        data = json.loads(fetch(url))
        for it in data.get("jobs", []):
            address = it.get("workAddress") or {}
            location = "%s, %s" % (address.get("locality", ""), address.get("administrativeArea", ""))
            if it.get("workRemote"):
                location += " (remote)"
            elif not wanted_place(location):
                continue
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


# ---------- OPT-friendly consulting firms (all US locations) ----------
# Big IT consulting firms hire many OPT new grads and fill roles fast, so they're
# checked every run and across the whole US (not just PLACES). Infosys and TCS
# block requests from GitHub's servers, so they can't be included.

ACCENTURE_SEARCHES = ["software", "developer", "data", "intern", "associate engineer", "analyst"]


def workday_days_ago(posted_on):
    # "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"
    text = (posted_on or "").lower()
    if "today" in text:
        return 0
    if "yesterday" in text:
        return 1
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else 999


def accenture_jobs():
    # Accenture's Workday board, US jobs only. A job's page (location and full
    # description) is only opened when its title passes the keyword filter.
    host = "https://accenture.wd103.myworkdayjobs.com"
    base = host + "/wday/cxs/accenture/AccentureCareers"
    us_only = {"locationCountry": ["bc33aa3152ec42d4995f4791a106ed09"]}
    seen_paths = set()
    jobs = []
    for words in ACCENTURE_SEARCHES:
        body = {"appliedFacets": us_only, "limit": 20, "offset": 0, "searchText": words}
        data = json.loads(fetch(base + "/jobs", body))
        for it in data.get("jobPostings", []):
            path = it.get("externalPath", "")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            age = workday_days_ago(it.get("postedOn"))
            if age > MAX_AGE_DAYS:
                continue
            title = it.get("title", "")
            job = {
                "id": "accenture:" + path.split("_")[-1],
                "firm": "Accenture",
                "title": title,
                "location": it.get("locationsText") or "United States",
                "type": "",
                "posted": (datetime.date.today() - datetime.timedelta(days=age)).strftime("%Y-%m-%d"),
                "contact": "",
                "link": host + "/AccentureCareers" + path,
                "desc": "",
            }
            if not drop_reason(title):
                info = json.loads(fetch(base + path)).get("jobPostingInfo", {})
                places = [info.get("location", "")] + info.get("additionalLocations", [])
                job["location"] = "; ".join(p for p in places if p)[:120] or job["location"]
                job["desc"] = strip_tags(info.get("jobDescription", ""))
            jobs.append(job)
    return jobs


def capgemini_jobs():
    # Capgemini's own jobs API (US site), 100 per page, description included.
    jobs = []
    for page in range(1, 9):
        url = ("https://cg-jobstream-api.azurewebsites.net/api/job-search"
               "?page=%d&size=100&country_code=us-en" % page)
        items = json.loads(fetch(url)).get("data", [])
        for it in items:
            if it.get("experience_level") == "Executives":
                continue
            posted = (it.get("indexed_at") or "")[:10]
            if too_old(posted):
                continue
            jobs.append({
                "id": "capgemini:" + it.get("ref", it.get("id", "")),
                "firm": "Capgemini",
                "title": it.get("title", ""),
                "location": (it.get("location") or "United States")[:120],
                "type": it.get("contract_type", ""),
                "posted": posted,
                "contact": "",
                "link": it.get("apply_job_url", ""),
                "desc": strip_tags(it.get("description", "")),
            })
        if len(items) < 100:
            break
    return jobs


def cognizant_jobs():
    # Cognizant's public listing pages are server-rendered. The global search
    # exposes US Technology jobs, including links under /global-en/jobs/.
    # Keep the original US listing as a second source for broader coverage.
    jobs = []
    seen_ids = set()
    card_re = re.compile(r'<div class="card-body">(.*?)(?=<div class="card-body">|$)', re.S)
    link_re = re.compile(
        r'href="(/(?:us-en|global-en)/jobs/(\d+)/[^"]+)"[^>]*>([^<]+)</a>'
    )
    searches = [
        ("https://careers.cognizant.com/global-en/jobs/"
         "?keyword=&industry=Technology&location=United+States&radius=100"
         "&lat=&lng=&cname=United+States&ccode=US&pagesize=10"),
        "https://careers.cognizant.com/us-en/jobs/?pagesize=10",
    ]
    for search_url in searches:
        for page in range(1, 11):
            separator = "&" if "?" in search_url else "?"
            page_html = fetch("%s%spage=%d" % (search_url, separator, page))
            for card in card_re.findall(page_html):
                link = link_re.search(card)
                if not link:
                    continue
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", card))
                place = re.search(r"([^|]*United States)", text)
                if not place or link.group(2) in seen_ids:
                    continue
                seen_ids.add(link.group(2))
                jobs.append({
                    "id": "cognizant:" + link.group(2),
                    "firm": "Cognizant",
                    "title": html.unescape(link.group(3).strip()),
                    "location": html.unescape(place.group(1).strip()),
                    "type": "",
                    "posted": "",
                    "contact": "",
                    "link": "https://careers.cognizant.com" + link.group(1),
                    "desc": "",
                })
    return jobs


def deloitte_jobs():
    # Deloitte's search page (newest first), 10 per page; description read later.
    jobs = []
    for offset in range(0, 100, 10):
        page_html = fetch("https://apply.deloitte.com/careers/SearchJobs"
                                 "?listFilterMode=1&jobRecordsPerPage=10&sort=1&jobOffset=%d" % offset)
        for card in re.findall(r'<article class="article--result.*?</article>', page_html, re.S):
            link = re.search(r'href="(https://apply\.deloitte\.com/[^"]*/JobDetail/[^"]*/(\d+))"[^>]*>\s*([^<]+?)\s*</a>', card)
            if not link:
                continue
            parts = [p.strip() for p in re.sub(r"<[^>]+>", "|", card).split("|") if p.strip()]
            location = parts[-1] if parts else "United States"
            jobs.append({
                "id": "deloitte:" + link.group(2),
                "firm": "Deloitte",
                "title": html.unescape(link.group(3)),
                "location": html.unescape(location)[:120],
                "type": "",
                "posted": "",
                "contact": "",
                "link": link.group(1),
                "desc": "",
            })
    return jobs


def wipro_fetch(body):
    # Wipro's API gives short bursts of HTTP 500. fetch() already tries twice;
    # wait longer and try up to two more times before giving up.
    url = "https://careers.wipro.com/services/recruiting/v1/jobs"
    for wait in (20, 40, None):
        try:
            return fetch(url, body)
        except urllib.error.HTTPError as e:
            if wait is None or e.code not in (500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError):
            if wait is None:
                raise
        time.sleep(wait)


def wipro_jobs():
    # Wipro's job API (SAP SuccessFactors), US only, newest first, 10 per page;
    # the description is read from each job page later.
    jobs = []
    for page in range(10):
        body = {"locale": "en_US", "pageNumber": page, "sortBy": "date", "keywords": "",
                "location": "", "facetFilters": {"jobLocationCountry": ["United States"]},
                "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""}
        try:
            data = json.loads(wipro_fetch(body))
        except Exception as e:
            if page == 0:
                raise   # nothing worked at all: report it as a problem
            # A later page failed (Wipro's API is flaky on deeper pages). Keep
            # the newest jobs we already have instead of losing the whole check.
            print("Wipro: page %d failed (%s) - using the %d jobs from earlier pages"
                  % (page, e, len(jobs)))
            break
        results = data.get("jobSearchResult", [])
        for row in results:
            it = row.get("response", {})
            month, day, year = (it.get("unifiedStandardStart") or "1/1/00").split("/")
            posted = "%d/%d/20%s" % (int(month), int(day), year)
            if too_old(posted):
                continue
            place = " ".join(it.get("jobLocationShort", [""])).replace("<br/>", "").strip()
            jobs.append({
                "id": "wipro:" + it.get("id", ""),
                "firm": "Wipro",
                "title": it.get("unifiedStandardTitle", ""),
                "location": place[:120],
                "type": "",
                "posted": posted,
                "contact": "",
                "link": "https://careers.wipro.com/job/%s/%s-en_US" % (it.get("urlTitle", ""), it.get("id", "")),
                "desc": "",
            })
        if len(results) < 10:
            break
    return jobs


def ibm_jobs():
    # IBM's career search API, newest first; keep US jobs. Its job pages are
    # built by JavaScript, so the AI gets the title, level and a short summary.
    hits = []
    for start in (0, 100):   # the API allows 100 per request
        body = {"appId": "careers", "scopes": ["careers2"], "query": {"bool": {"must": []}},
                "size": 100, "from": start, "sort": [{"dcdate": "desc"}], "lang": "zz",
                "localeSelector": {}, "sm": {"query": "", "lang": "zz"},
                "_source": ["_id", "title", "url", "description", "dcdate",
                            "field_keyword_08", "field_keyword_18", "field_keyword_19"]}
        data = json.loads(fetch("https://www-api.ibm.com/search/api/v2", body))
        hits.extend(data.get("hits", {}).get("hits", []))
    jobs = []
    for hit in hits:
        it = hit.get("_source", {})
        place = it.get("field_keyword_19", "")
        if not (place.endswith(", US") or "United States" in place):
            continue
        posted = it.get("dcdate", "")
        if too_old(posted):
            continue
        level = it.get("field_keyword_18", "")
        jobs.append({
            "id": "ibm:" + it.get("url", hit.get("_id", "")).split("jobId=")[-1],
            "firm": "IBM",
            "title": it.get("title", ""),
            "location": place,
            "type": "Internship" if level == "Internship" else "",
            "posted": posted,
            "contact": "",
            "link": it.get("url", ""),
            "desc": "Level: %s. Area: %s. %s" % (level, it.get("field_keyword_08", ""),
                                                it.get("description", "")),
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
    ("Accenture", accenture_jobs),
    ("Capgemini", capgemini_jobs),
    ("Cognizant", cognizant_jobs),
    ("Deloitte", deloitte_jobs),
    ("Wipro", wipro_jobs),
    ("IBM", ibm_jobs),
]


# ---------- memory (sqlite) ----------

def open_db():
    db = sqlite3.connect(DB_FILE)
    db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, title TEXT, seen_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS dropped (id TEXT PRIMARY KEY, firm TEXT, "
               "title TEXT, link TEXT, reason TEXT, dropped_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY, message TEXT, "
               "count INTEGER, first_at TEXT, last_at TEXT)")
    # keep the dropped log small: forget anything older than 30 days
    db.execute("DELETE FROM dropped WHERE dropped_at < datetime('now', '-30 days')")
    return db


def log_dropped(db, job, reason):
    # One row per job. If it's dropped again for a different reason (the rules
    # changed, or the AI judged it), the row shows the newest reason.
    db.execute("INSERT INTO dropped VALUES (?, ?, ?, ?, ?, datetime('now')) "
               "ON CONFLICT(id) DO UPDATE SET reason = excluded.reason, "
               "dropped_at = excluded.dropped_at WHERE dropped.reason != excluded.reason",
               (job["id"], job["firm"], job["title"], job.get("link", ""), reason))


def log_ai_skips(db, jobs):
    for job in jobs:
        if job.get("verdict") == "skip":
            log_dropped(db, job, "AI skip: %s" % job.get("reason", ""))


def show_dropped(db, days=7):
    rows = db.execute("SELECT dropped_at, reason, firm, title, link FROM dropped "
                      "WHERE dropped_at >= datetime('now', ?) ORDER BY dropped_at DESC",
                      ("-%d days" % days,)).fetchall()
    for when, reason, firm, title, link in rows:
        print("%s | %s | %s | %s | %s" % (when[:10], reason, firm, title, link))
    print("%d dropped in the last %d days" % (len(rows), days))


def get_state(db, key):
    row = db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return 0
    return row[0]


def set_state(db, key, value):
    db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, value))


def get_ai_fails(db):
    return get_state(db, "ai_fails")


def set_ai_fails(db, count):
    set_state(db, "ai_fails", count)


# ---------- problems -> "attention needed" in the next email ----------

PROBLEMS = []   # (key, message) noticed during this run


def note_problem(key, message):
    print("PROBLEM: " + message)
    PROBLEMS.append((key, message))


def ai_is_down():
    # True when Gemini refused this run for a billing / key reason.
    return any(key == "ai_down" for key, message in PROBLEMS)


def save_problems(db):
    # The same problem again just bumps its count, so repeats don't pile up.
    for key, message in PROBLEMS:
        if db.execute("SELECT 1 FROM alerts WHERE key = ?", (key,)).fetchone():
            db.execute("UPDATE alerts SET message = ?, count = count + 1, "
                       "last_at = datetime('now') WHERE key = ?", (message, key))
        else:
            db.execute("INSERT INTO alerts VALUES (?, ?, 1, datetime('now'), datetime('now'))",
                       (key, message))
    del PROBLEMS[:]


def pending_alerts(db):
    return db.execute("SELECT message, count, first_at, last_at FROM alerts "
                      "ORDER BY first_at").fetchall()


def central_time(utc_text):
    # "2026-11-05 16:00:00" (UTC, from sqlite) -> "Nov 5 10:00 AM"
    when = datetime.datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S")
    when = when.replace(tzinfo=datetime.timezone.utc).astimezone(ZoneInfo("America/Chicago"))
    return "%s %d %s" % (MONTHS[when.month - 1], when.day, when.strftime("%I:%M %p").lstrip("0"))


def alerts_html(rows):
    if not rows:
        return ""
    items = []
    for message, count, first_at, last_at in rows:
        when = central_time(first_at)
        if count > 1:
            when = "%d times, %s to %s" % (count, when, central_time(last_at))
        items.append("<li>%s <span style=\"color:#888\">(%s)</span></li>"
                     % (html.escape(message), when))
    return ('<div style="font-family:Arial,sans-serif;font-size:14px;border:2px solid #c0392b;'
            'border-radius:6px;padding:10px 14px;margin-bottom:16px">'
            '<b style="color:#c0392b">&#9888; Attention needed</b>'
            '<ul style="margin:6px 0 0 0;padding-left:20px">%s</ul></div>' % "".join(items))


def send_pending_alerts(db, now=False):
    # For problems that didn't ride along on a job email: send them on their
    # own once the oldest is ALERT_WAIT_HOURS old (or right away if now=True).
    rows = pending_alerts(db)
    if not rows:
        return
    oldest = datetime.datetime.strptime(rows[0][2], "%Y-%m-%d %H:%M:%S")
    waited = datetime.datetime.utcnow() - oldest
    if not now and waited < datetime.timedelta(hours=ALERT_WAIT_HOURS):
        return
    send_email("\u26a0 Attention needed: job poller (%d)" % len(rows), alerts_html(rows))
    db.execute("DELETE FROM alerts")
    print("Sent attention-needed email.")


def check_zero(db, name, count):
    # A firm that suddenly returns nothing usually means its page changed.
    key = "zero:" + name
    if count > 0:
        set_state(db, key, 0)
        return
    runs = get_state(db, key) + 1
    set_state(db, key, runs)
    if runs == ZERO_RUNS or (runs > ZERO_RUNS and (runs - ZERO_RUNS) % 48 == 0):
        note_problem(key, "%s returned 0 jobs for %d runs in a row - its site may have "
                          "changed and the scraper may need fixing" % (name, runs))


def is_seen(db, job_id):
    return db.execute("SELECT 1 FROM seen WHERE id = ?", (job_id,)).fetchone() is not None


def mark_seen(db, job):
    db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?, datetime('now'))",
               (job["id"], job["title"]))


# ---------- visa history (data/visa_companies.csv) ----------
# Built from public DOL visa filings + USCIS approvals by tools/visa/score.py.
# A job at one of these companies gets a short note in the email and for the AI.

VISA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "visa_companies.csv")
NAME_SUFFIXES = {"llc", "inc", "ltd", "limited", "corp", "corporation", "co", "company",
                 "lp", "llp", "plc", "pllc", "pc", "pa", "the", "incorporated", "usa", "us",
                 "na", "of", "and"}
VISA_COMPANIES = {}   # brand -> row


def clean_company(name):
    # "Infosys Limited" / "INFOSYS LTD." -> "infosys" (same cleaning as score.py)
    n = (name or "").lower()
    n = re.split(r"\b(?:d/?b/?a|aka|a/k/a)\b", n)[0]
    n = n.replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return " ".join(w for w in n.split() if w not in NAME_SUFFIXES)


def load_visa_companies():
    try:
        with open(VISA_FILE, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                VISA_COMPANIES[row["brand"]] = row
    except OSError:
        pass   # no list yet: jobs just go without a visa note


def visa_row(firm):
    # Longest brand that the cleaned firm name starts with ("goldman sachs bank" -> "goldman sachs").
    words = clean_company(firm).split()
    for n in range(len(words), 0, -1):
        row = VISA_COMPANIES.get(" ".join(words[:n]))
        if row:
            return row
    return None


def visa_note(job):
    row = visa_row(job["firm"])
    if not row:
        return ""
    note = "Visa history: ~%s junior software H-1B hires" % row["est_junior_hires"]
    if row["approval_rate"]:
        note += ", %s%% approved" % row["approval_rate"]
    if row["cap_exempt"]:
        note += ", cap-exempt (no lottery)"
    return note + " (rank %s of %d)" % (row["rank"], len(VISA_COMPANIES))


load_visa_companies()


# ---------- AI review (Gemini) ----------

# Tried in order: the better model first, the lighter one if the first
# hits its free daily limit or fails.
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

# The candidate's background comes from the CANDIDATE_PROFILE secret, so personal
# details stay out of this public repo. Without it, the AI screens for a generic new grad.
CANDIDATE = os.environ.get("CANDIDATE_PROFILE") or """
Candidate: an entry-level software engineer (new grad). No profile was given,
so judge for a typical new grad.
"""

PROMPT = """You screen job postings for one candidate.
%s
Be strict. Read the REQUIRED qualifications, not just the title.
For each job below decide:
- "apply": a new grad with only coursework, projects and TA work could
  realistically meet MOST of the required list. Entry-level wording
  (junior, associate, entry level, 0-2 years, new grad) is a strong signal.
  A job asking for up to 2 years (including ranges that start at 2, like
  "2-5 years") is still "apply" when the required skills match his stack -
  for contract and staffing roles that number is usually flexible.
- "maybe": could work but has a concern (asks for one or two specific tools
  he hasn't used, unclear stack, or a 2-year ask where the stack only partly
  matches).
"No sponsorship now or in the future" (or "must not require sponsorship at
any time"): he is on OPT and will need sponsorship later, so these usually
reject him. Mark it "skip" - EXCEPT a contract or temp role from a staffing
agency, which can fit inside his OPT years: mark that "maybe" and say to ask
the recruiter whether OPT works. A posting that only says "must be authorized
to work in the US" or "without sponsorship" (no "future") is fine - OPT
counts as authorized.
A "Visa history" line means public records show this company hiring junior
software people on H-1B visas - good news for him. It can tip a borderline job
from "maybe" to "apply", but never overrides the job's own requirements or a
"no sponsorship now or in the future" line.
- "skip": needs 3+ years, senior-level, wrong field, Java REQUIRED as the
  main language with no alternative (a list like "Java, .NET, or OOP" or
  "Python or Java" is fine - C++ counts as OOP), needs
  citizenship / green card / clearance, OR the required list is mostly
  specialist hands-on experience a new grad won't have (words like "deep",
  "extensive", "expert", "proven", or several named enterprise tools/products
  like RHEL admin, Kerberos, Proxmox, Splunk, SAP, Epic, Active Directory
  administration).
Do NOT mark "apply" just because a skill word (Linux, Python, SQL) appears in
both the job and the candidate profile; judge the depth the job requires.
Software testing / QA / test analyst roles count as engineering roles he
wants, even if the title also says "analyst" or "BA".
"Wrong field" (skip) includes jobs with no hands-on technical work that only
share a word like "analyst" or "engineer" with tech jobs: logistics /
transportation / supply-chain analyst, financial or accounting analyst,
business operations, a business analyst who only gathers requirements (no
testing, data or coding work), customer service, and hardware / data-center
technician roles.
He wants to write code. Also "skip" roles that are mostly coordination or
operations even when the title says "programmer", "developer" or "engineer":
release / environment management, control room or cutover coordination,
change management, monitoring / NOC, production-support rotations with no
development, and project or scrum coordination. If writing code is not a main
part of the day-to-day duties, it's "skip".
If the job requires experience in a specific industry domain (for example
derivatives trading, fixed income, insurance claims systems), mark it "maybe",
not "apply".
If you are unsure between "apply" and "skip", choose "maybe". Only "skip"
when there is a clear blocker - missing a good job is worse than showing a
borderline one.

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


def robert_half_description(job):
    # The search card has the "Description" part only; the job page also has
    # the "Requirements" part (years, must-have skills). Requirements go first
    # so they're never cut off.
    page_html = fetch(job["link"])
    found = re.search(r'data-testid="job-details-requirements">(.*?)</div>', page_html, re.S)
    if not found:
        return job["desc"]
    requirements = strip_tags(html.unescape(html.unescape(found.group(1))))
    return "Requirements: " + requirements + " Description: " + job["desc"]


def page_description(job):
    # The job page's visible text (Cognizant, Deloitte, Wipro).
    return strip_tags(re.sub(r"(?is)<(script|style).*?</\1>", " ", fetch(job["link"])))


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
    "Robert Half": robert_half_description,
    "Cognizant": page_description,
    "Deloitte": page_description,
    "Wipro": page_description,
}


# Price per 1M tokens (input, output incl. thinking), from Google's pricing page.
# 3.8 Flash doubles on Jan 1, 2027.
AI_PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}
if datetime.date.today() >= datetime.date(2027, 1, 1):
    AI_PRICES["gemini-3.8-flash"] = (1.50, 7.50)

AI_USAGE = {}   # model -> [input tokens, output tokens, thinking tokens] this run


def count_tokens(model, data):
    usage = data.get("usageMetadata", {})
    totals = AI_USAGE.setdefault(model, [0, 0, 0])
    totals[0] += usage.get("promptTokenCount", 0)
    totals[1] += usage.get("candidatesTokenCount", 0)
    totals[2] += usage.get("thoughtsTokenCount", 0)


def print_ai_usage():
    # One line per model in the run log, e.g.
    # "AI usage gemini-3.8-flash: 41,200 in, 2,100 out, 6,900 thinking = ~$0.06"
    for model, (tokens_in, tokens_out, thinking) in AI_USAGE.items():
        price_in, price_out = AI_PRICES.get(model, (0, 0))
        cost = (tokens_in * price_in + (tokens_out + thinking) * price_out) / 1000000
        print("AI usage %s: {:,} in, {:,} out, {:,} thinking = ~${:.3f}".format(
            tokens_in, tokens_out, thinking, cost) % model)


class AIDown(Exception):
    # Gemini refused for a reason retrying won't fix (no credit, bad key).
    pass


def gemini_error(e):
    # Short readable reason, including Gemini's own message when it sends one.
    text = str(e)
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = json.loads(e.read().decode("utf-8", errors="replace"))
            text += ": " + detail.get("error", {}).get("message", "")[:200]
        except Exception:
            pass
    return text


def ai_text(desc):
    # The first DESC_LIMIT characters, plus any visa/sponsorship sentences from
    # the part that got cut. Big companies put "no sponsorship now or in the
    # future" at the very end, after the About-us and EEO paragraphs.
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) <= DESC_LIMIT:
        return desc
    found = []
    # Split where a sentence ends and a new one starts with a capital, so
    # "the U.S. without sponsorship" stays in one piece.
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", desc[DESC_LIMIT:]):
        m = VISA_WORDS.search(sentence)
        if m:
            # Long run-on "sentences" (bullet lists) keep just the part around the word.
            found.append(sentence[max(0, m.start() - 150):m.end() + 150])
    if not found:
        return desc[:DESC_LIMIT]
    return desc[:DESC_LIMIT] + (" ... " + " ".join(found))[:EXTRA_LIMIT]


def ask_gemini(jobs):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    parts = []
    for job in jobs:
        desc = ai_text(job["desc"])
        source = "staffing agency" if job["firm"] in STAFFING_AGENCIES else "company"
        visa = visa_note(job) or "no visa filings found for this company"
        parts.append("ID: %s\nTitle: %s\nFirm: %s (%s)\nJob type: %s\nLocation: %s\n%s\nDescription: %s\n"
                     % (job["id"], job["title"], job["firm"], source, job.get("type") or "not stated",
                        job["location"], visa, desc or "(none)"))
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
                # 3.8 Flash thinks before answering; 30s cut it off, so give it 2 minutes
                data = json.loads(fetch(url, body, timeout=120, retry=False))
                count_tokens(model, data)
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                print("AI model used: " + model)
                break
            except urllib.error.HTTPError as e:
                reason = gemini_error(e)
                # 401/402/403: no credit or a bad key - stop now, retrying won't help
                if e.code in (401, 402, 403):
                    raise AIDown(reason)
                print("AI model %s failed (%s), trying next" % (model, reason))
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
                job["desc"] = re.sub(r"\s+", " ", describe(job))
            except Exception as e:
                note_problem("describe:" + job["firm"],
                             "Couldn't read %s job pages for the AI (%s); it judged them "
                             "from the title only" % (job["firm"], e))
    if not os.environ.get("GEMINI_API_KEY"):
        note_problem("ai_key", "GEMINI_API_KEY is missing, so the AI check is off")
        return False
    if not os.environ.get("CANDIDATE_PROFILE"):
        note_problem("ai_profile", "CANDIDATE_PROFILE secret is missing, so the AI is "
                                   "screening jobs for a generic new grad")
    verdicts = {}
    down = False
    for i in range(0, len(jobs), AI_BATCH):
        if i > 0:
            time.sleep(5)  # stay under Gemini's per-minute limit on big runs
        try:
            verdicts.update(ask_gemini(jobs[i:i + AI_BATCH]) or {})
        except AIDown as e:
            note_problem("ai_down", "Gemini refused the request (%s) - check Gemini "
                                    "billing / credits in Google AI Studio" % e)
            down = True
            break
        except Exception as e:
            # Keep the batches that worked; this batch's jobs are held for the next run.
            note_problem("ai_batch", "AI check failed for a batch of jobs (%s); those jobs "
                                     "were held for the next run" % e)
    if not verdicts:
        return False
    # The model sometimes leaves a job out; ask once more for just those.
    missing = [j for j in jobs if j["id"] not in verdicts]
    if missing and not down:
        try:
            verdicts.update(ask_gemini(missing) or {})
        except Exception as e:
            note_problem("ai_missing", "AI left out some jobs and the retry failed (%s); "
                                       "they were held for the next run" % e)
    # Jobs the AI never judged are marked "hold": not emailed, not marked as sent,
    # so the next run tries them again.
    for job in jobs:
        job["verdict"], job["reason"] = verdicts.get(job["id"], ("hold", ""))
    print_ai_usage()
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
    note = visa_note(job)
    if note:
        out += '<br><span style="color:#1a7f37">&#10003; %s</span>' % e(note)
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
    if "--dropped" in sys.argv:   # python poller.py --dropped  -> list what was filtered out
        show_dropped(db)
        return
    emailed = False
    crashed = False
    try:
        emailed = run(db, dry_run)
    except Exception as e:
        traceback.print_exc()
        note_problem("crash:staffing", "Staffing poller crashed: %s: %s" % (type(e).__name__, e))
        crashed = True
    if dry_run:
        return
    finish_run(db, emailed, crashed, "staffing")


def finish_run(db, emailed, crashed, who):
    # Save this run's problems and send them if no job email carried them.
    # A crash emails right away the first time; repeats follow the normal wait.
    streak_key = "crash_streak:" + who
    streak = get_state(db, streak_key) + 1 if crashed else 0
    set_state(db, streak_key, streak)
    save_problems(db)
    if not emailed:
        try:
            send_pending_alerts(db, now=(streak == 1))
        except Exception as e:
            print("Could not send attention-needed email (%s)" % e)
    db.commit()


def email_with_alerts(db, subject, body):
    # Put any saved problems at the top of a job email, then clear them.
    save_problems(db)
    rows = pending_alerts(db)
    if rows:
        subject = "\u26a0 " + subject
    send_email(subject, alerts_html(rows) + body)
    db.execute("DELETE FROM alerts")


def run(db, dry_run):
    # One poll. Returns True if a job email was sent.
    new_jobs = []

    # Ask every firm at the same time; each one mostly waits on the network.
    with ThreadPoolExecutor(max_workers=len(FIRMS)) as pool:
        waiting = [(name, pool.submit(get_jobs)) for name, get_jobs in FIRMS]

    for name, future in waiting:
        try:
            jobs = future.result()
        except Exception as e:
            note_problem("fetch:" + name, "%s: couldn't check (%s)" % (name, e))
            continue
        if not dry_run:
            # It works again, so drop any earlier "couldn't check" alert for it.
            # Per-city alerts ("fetch:Experis Detroit") count too, but keep any
            # that failed again this run so their 3-hour timer isn't reset.
            failed_now = {k for k, m in PROBLEMS}
            for (k,) in db.execute("SELECT key FROM alerts WHERE key = ? OR key LIKE ?",
                                   ("fetch:" + name, "fetch:" + name + " %")).fetchall():
                if k not in failed_now:
                    db.execute("DELETE FROM alerts WHERE key = ?", (k,))
            check_zero(db, name, len(jobs))
        good = []
        for j in jobs:
            if too_old(j["posted"]):
                continue
            reason = drop_reason(j["title"], j["desc"])
            if reason:
                if not dry_run:
                    log_dropped(db, j, reason)
            else:
                good.append(j)
        already = set(j["id"] for j in new_jobs)
        # Some firms post one job several times under different IDs; keep one
        # per firm + title + location.
        same_posting = set((j["firm"], j["title"].lower(), j["location"]) for j in new_jobs)
        fresh = []
        for j in good:
            key = (j["firm"], j["title"].lower(), j["location"])
            if not is_seen(db, j["id"]) and j["id"] not in already and key not in same_posting:
                fresh.append(j)
                already.add(j["id"])
                same_posting.add(key)
        print("%s: %d jobs in wanted places, %d fit, %d new" % (name, len(jobs), len(good), len(fresh)))
        new_jobs.extend(fresh)

    if not new_jobs:
        print("Nothing new.")
        return False

    try:
        ai_used = ai_review(new_jobs)
    except Exception as e:
        note_problem("ai_crash", "AI check crashed (%s)" % e)
        ai_used = False

    # If the AI failed, hold the jobs for the next run instead of sending them
    # unfiltered. After MAX_AI_FAILS failed runs in a row (~2 hours), send anyway.
    if not dry_run:
        if ai_used:
            set_ai_fails(db, 0)
        else:
            fails = get_ai_fails(db) + 1
            # Out of credit / bad key: keep holding (no unfiltered fallback) until it's fixed.
            if ai_is_down():
                set_ai_fails(db, fails)
                print("Gemini refused - holding %d jobs until billing is fixed" % len(new_jobs))
                return False
            if fails < MAX_AI_FAILS:
                set_ai_fails(db, fails)
                note_problem("ai_hold", "AI check failed, so new jobs were held back and "
                                        "retried on the next run (nothing lost)")
                print("AI failed (%d in a row) - holding %d jobs for next run"
                      % (fails, len(new_jobs)))
                return False
            note_problem("ai_unfiltered", "AI check failed %d runs in a row, so jobs were "
                                          "sent unfiltered (keyword matches only)" % fails)
            set_ai_fails(db, 0)

    held = [j for j in new_jobs if j.get("verdict") == "hold"]
    new_jobs = [j for j in new_jobs if j.get("verdict") != "hold"]
    if held:
        print("Holding %d jobs the AI couldn't check, for the next run" % len(held))

    good_count = len([j for j in new_jobs if j.get("verdict") != "skip"])
    print("\n" + build_email(new_jobs, [], ai_used))

    if dry_run:
        return False

    emailed = False
    if good_count > 0:
        subject = "%d new staffing-firm jobs" % good_count
        if ai_used:
            applies = len([j for j in new_jobs if j.get("verdict") == "apply"])
            subject = "%d apply / %d new staffing-firm jobs" % (applies, good_count)
        email_with_alerts(db, subject, build_html(new_jobs, [], ai_used))
        emailed = True
        print("\nEmailed.")
    else:
        print("\nAI skipped everything new - no email.")

    log_ai_skips(db, new_jobs)
    for job in new_jobs:
        mark_seen(db, job)
    return emailed


if __name__ == "__main__":
    main()
