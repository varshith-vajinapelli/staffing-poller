"""
Company job poller.

Same idea as poller.py, but for jobs posted directly by companies:
  - Simplify's public new-grad and internship lists (hundreds of companies)
  - Minnesota employers' own Workday job boards

Keeps full-time roles in the Twin Cities, mid-size metros and the South;
keeps internships in the Twin Cities (any season) or anywhere in the US if
they are off-season (Winter / Spring 2027). New matches get the same keyword
filter, 7-day age limit and Gemini review, then one email.

Run:  python company_poller.py            (check + email)
      python company_poller.py --dry-run  (check + print, no email, nothing saved)
"""

import datetime
import html
import json
import re
import sys

import poller  # reuse fetch, filters, memory, AI review and email

SIMPLIFY_NEW_GRAD = ("https://raw.githubusercontent.com/SimplifyJobs/"
                     "New-Grad-Positions/dev/.github/scripts/listings.json")
SIMPLIFY_INTERNSHIPS = ("https://raw.githubusercontent.com/SimplifyJobs/"
                        "Summer2027-Internships/dev/.github/scripts/listings.json")

# Simplify categories worth looking at (skips Hardware, Product, Quant).
GOOD_CATEGORIES = ["Software", "Software Engineering", "AI/ML/Data",
                   "Data Science, AI & Machine Learning"]

# Internship seasons that start before Summer 2027 (off-season).
OFF_SEASON_TERMS = ["Winter 2027", "Spring 2027"]

# Minnesota employers on Workday: (company, host, tenant, site)
WORKDAY_COMPANIES = [
    ("Target", "target.wd5.myworkdayjobs.com", "target", "targetcareers"),
    ("U.S. Bank", "usbank.wd1.myworkdayjobs.com", "usbank", "US_Bank_Careers"),
    ("Medtronic", "medtronic.wd1.myworkdayjobs.com", "medtronic", "MedtronicCareers"),
    ("3M", "3m.wd1.myworkdayjobs.com", "3m", "Search"),
    ("General Mills", "genmills.wd1.myworkdayjobs.com", "genmills", "GMI_External_Careers"),
    ("Ameriprise", "ameriprise.wd5.myworkdayjobs.com", "ameriprise", "Ameriprise"),
    ("Securian", "hq.wd12.myworkdayjobs.com", "hq", "Securian_External"),
]
WORKDAY_SEARCHES = ["software", "developer", "data", "intern", "associate engineer"]

# Company titles must contain one of these (stricter than the staffing feed,
# because companies post every kind of job, not just tech).
TECH_WORDS = [
    "software", "developer", "programmer", "engineer", "data", "machine learning",
    " ml ", " ai ", "ai/", "qa", "quality assurance", "test", "automation",
    "full-stack", "full stack", "frontend", "front-end", "backend", "back-end",
    "web", "cloud", "devops", "platform", "application", "computer",
]

# Company titles with any of these are skipped.
COMPANY_BAD_WORDS = [
    "warehouse", "advisor", "patient", "customer service", "sales", "marketing",
    "designer", "clinical", "toll", "sports", "gis", "geographic", "credit risk",
    "financial", "editorial", "psqr", "construction", "cost engineer",
    "mechanical", "electrical", "process engineer", "manufacturing", "chemical",
    "supply chain", "operations analyst", "business operations analyst",
    "hubspot", "recruit", "accountant", "actuar", "bim",
]


def company_title_ok(title):
    t = " " + title.lower() + " "
    if not any(w in t for w in TECH_WORDS):
        return False
    return not any(w in t for w in COMPANY_BAD_WORDS)


# ---------- where Sai wants to work ----------

SOUTH_STATES = {
    "TX": "texas", "GA": "georgia", "TN": "tennessee", "NC": "north carolina",
    "SC": "south carolina", "FL": "florida", "AL": "alabama", "OK": "oklahoma",
    "LA": "louisiana", "AR": "arkansas", "MS": "mississippi", "AZ": "arizona",
    "NM": "new mexico",
}

MIDSIZE_CITIES = [
    "madison", "milwaukee", "green bay", "des moines", "cedar rapids",
    "iowa city", "omaha", "lincoln", "sioux falls", "fargo", "rochester, mn",
    "kansas city", "st. louis", "saint louis", "columbus", "cincinnati",
    "indianapolis", "louisville", "lexington", "pittsburgh", "cleveland",
    "grand rapids", "ann arbor", "boise", "salt lake city", "richmond",
    "spokane", "wichita", "springfield",
]


def in_minnesota(location):
    loc = location.lower()
    return (poller.in_twin_cities(location) or "minnesota" in loc
            or re.search(r"\bmn\b", loc) is not None)


def in_south(location):
    loc = location.lower()
    for code, name in SOUTH_STATES.items():
        if name in loc or re.search(r",\s*%s\b" % code.lower(), loc):
            return True
    return False


def in_midsize_metro(location):
    loc = location.lower()
    return any(city in loc for city in MIDSIZE_CITIES)


NOT_US = re.compile(r"\b(canada|ontario|quebec|uk|united kingdom|england|london|"
                    r"india|germany|ireland|singapore|poland|china|japan)\b"
                    r"|,\s*(bc|on)\b")


def in_us(location):
    loc = location.lower().replace("new mexico", "")
    if "mexico" in loc:
        return False
    return NOT_US.search(loc) is None


def wanted_place(location):
    return in_minnesota(location) or in_south(location) or in_midsize_metro(location)


# ---------- Simplify lists ----------

def simplify_items(url):
    items = json.loads(poller.fetch(url))
    cutoff = datetime.datetime.now().timestamp() - poller.MAX_AGE_DAYS * 86400
    good = []
    for it in items:
        if not it.get("active") or not it.get("is_visible", True):
            continue
        if it.get("date_posted", 0) < cutoff:
            continue
        if it.get("category") not in GOOD_CATEGORIES:
            continue
        if "citizenship" in (it.get("sponsorship") or "").lower():
            continue
        good.append(it)
    return good


def simplify_job(it, kind):
    posted = datetime.datetime.fromtimestamp(it.get("date_posted", 0)).strftime("%Y-%m-%d")
    return {
        "id": "simplify:" + it["id"],
        "firm": it.get("company_name", ""),
        "title": it.get("title", ""),
        "location": "; ".join(it.get("locations", []))[:120],
        "type": kind,
        "posted": posted,
        "contact": "",
        "link": it.get("url", ""),
        "desc": "",
    }


def simplify_new_grad_jobs():
    jobs = []
    for it in simplify_items(SIMPLIFY_NEW_GRAD):
        if any(wanted_place(loc) for loc in it.get("locations", [])):
            jobs.append(simplify_job(it, "Full-time"))
    return jobs


def simplify_internship_jobs():
    jobs = []
    for it in simplify_items(SIMPLIFY_INTERNSHIPS):
        terms = it.get("terms", [])
        locations = it.get("locations", [])
        local = any(in_minnesota(loc) for loc in locations)
        off_season = [t for t in terms if t in OFF_SEASON_TERMS]
        if local:
            kind = "Internship (%s)" % ", ".join(terms or ["season n/a"])
        elif off_season and any(in_us(loc) for loc in locations):
            kind = "Internship (%s)" % ", ".join(off_season)
        else:
            continue
        jobs.append(simplify_job(it, kind))
    return jobs


# ---------- Workday boards ----------

def days_ago(posted_on):
    # "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"
    text = (posted_on or "").lower()
    if "today" in text:
        return 0
    if "yesterday" in text:
        return 1
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else 999


# Workday jobs dropped by title before their details are fetched.
early_drops = []


def workday_jobs_for(company, host, tenant, site):
    base = "https://%s/wday/cxs/%s/%s" % (host, tenant, site)
    seen_paths = set()
    jobs = []
    for words in WORKDAY_SEARCHES:
        body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": words}
        data = json.loads(poller.fetch(base + "/jobs", body))
        for it in data.get("jobPostings", []):
            path = it.get("externalPath", "")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            age = days_ago(it.get("postedOn"))
            if age > poller.MAX_AGE_DAYS:
                continue
            title = it.get("title", "")
            reason = poller.drop_reason(title)
            if reason:
                # remembered here, written to the dropped log in main()
                early_drops.append({
                    "id": "workday:%s:%s" % (tenant, path),
                    "firm": company,
                    "title": title,
                    "link": "https://%s/%s%s" % (host, site, path),
                    "reason": reason,
                })
                continue
            # The list only says "3 Locations" sometimes, so read the job itself.
            info = json.loads(poller.fetch(base + path)).get("jobPostingInfo", {})
            places = [info.get("location", "")] + info.get("additionalLocations", [])
            places_text = "; ".join(p for p in places if p)
            if not (any(in_minnesota(p) or "home office" in p.lower() for p in places)
                    or any(wanted_place(p) for p in places)):
                continue
            kind = "Internship" if re.search(r"\bintern", title.lower()) else "Full-time"
            posted = (datetime.date.today() - datetime.timedelta(days=age)).strftime("%Y-%m-%d")
            jobs.append({
                "id": "workday:%s:%s" % (tenant, info.get("jobReqId") or path),
                "firm": company,
                "title": title,
                "location": places_text[:120],
                "type": kind,
                "posted": posted,
                "contact": "",
                "link": info.get("externalUrl") or "https://%s/%s%s" % (host, site, path),
                "desc": poller.strip_tags(info.get("jobDescription", "")),
            })
    return jobs


def workday_jobs():
    jobs = []
    errors = []
    for company, host, tenant, site in WORKDAY_COMPANIES:
        try:
            jobs.extend(workday_jobs_for(company, host, tenant, site))
        except Exception as e:
            print("Workday %s: FAILED (%s)" % (company, e))
            errors.append(company)
    return jobs, errors


# ---------- AI: same reviewer, plus internship rules ----------

def page_text(job):
    # Simplify links go to many different career sites; take the page's text.
    text = poller.strip_tags(re.sub(r"(?is)<(script|style).*?</\1>", " ", poller.fetch(job["link"])))
    return text


INTERNSHIP_RULES = """Some jobs are internships (their Location/type line says so). For those:
- Twin Cities internships are fine in any season.
- Internships elsewhere are only fine if they start before Summer 2027.
- He graduates December 2026, so internships that require being a
  current student AFTER that date are a concern ("maybe"), not a blocker,
  unless the posting says graduates are not eligible ("skip").
"""


# ---------- main ----------

def main():
    dry_run = "--dry-run" in sys.argv
    db = poller.open_db()
    new_jobs = []
    errors = []
    sources = [
        ("Simplify new grad", simplify_new_grad_jobs),
        ("Simplify internships", simplify_internship_jobs),
    ]
    for name, get_jobs in sources:
        try:
            jobs = get_jobs()
        except Exception as e:
            print("%s: FAILED (%s)" % (name, e))
            errors.append(name)
            continue
        print("%s: %d in your regions" % (name, len(jobs)))
        new_jobs.extend(jobs)

    wd_jobs, wd_errors = workday_jobs()
    print("Minnesota Workday boards: %d" % len(wd_jobs))
    new_jobs.extend(wd_jobs)
    errors.extend(wd_errors)

    # keyword filter + memory (same rules as the staffing poller)
    fresh = []
    already = set()
    same_posting = set()
    for j in early_drops:
        if not dry_run:
            poller.log_dropped(db, j, j["reason"])
    for j in new_jobs:
        reason = poller.drop_reason(j["title"], j["desc"])
        if not reason and not company_title_ok(j["title"]):
            reason = "company title filter"
        if reason:
            if not dry_run:
                poller.log_dropped(db, j, reason)
            continue
        if poller.is_seen(db, j["id"]) or j["id"] in already:
            continue
        # The lists sometimes repeat one job (same company + title) several times.
        key = (j["firm"].lower(), j["title"].lower())
        if key in same_posting:
            continue
        same_posting.add(key)
        fresh.append(j)
        already.add(j["id"])
    print("%d new after filters" % len(fresh))

    if not fresh:
        db.commit()  # save the dropped log
        print("Nothing new.")
        return

    # Simplify jobs have no description yet: read their pages for the AI.
    for job in fresh:
        if job["id"].startswith("simplify:") and not job["desc"]:
            try:
                job["desc"] = re.sub(r"\s+", " ", page_text(job))[:3000]
            except Exception:
                pass

    for job in fresh:
        job["location"] = "%s · %s" % (job["location"], job["type"])

    poller.PROMPT = poller.PROMPT.replace("Return ONLY", INTERNSHIP_RULES + "\nReturn ONLY")
    try:
        ai_used = poller.ai_review(fresh)
    except Exception as e:
        print("AI check FAILED (%s)" % e)
        ai_used = False

    for job in fresh:
        job["type"] = ""  # already shown next to the location

    good_count = len([j for j in fresh if j.get("verdict") != "skip"])
    body = poller.build_html(fresh, errors, ai_used)
    print("\n" + poller.build_email(fresh, errors, ai_used))

    if dry_run:
        return

    if good_count > 0:
        subject = "%d new company jobs" % good_count
        if ai_used:
            applies = len([j for j in fresh if j.get("verdict") == "apply"])
            subject = "%d apply / %d new company jobs" % (applies, good_count)
        poller.send_email(subject, body)
        print("\nEmailed.")
    else:
        print("\nAI skipped everything new - no email.")

    poller.log_ai_skips(db, fresh)
    for job in fresh:
        poller.mark_seen(db, job)
    db.commit()


if __name__ == "__main__":
    main()
