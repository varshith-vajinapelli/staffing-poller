# Job pollers

Two scripts email only new matches: `poller.py` every 30 minutes, `company_poller.py` twice a day (11:52 AM and 5:52 PM Central) as a digest.

- `poller.py`: staffing firms (Apex, Brooksource, Experis, Robert Half,
  TEKsystems, Insight Global, Randstad, Beacon Hill).
- `company_poller.py`: jobs posted by companies themselves: Simplify's public
  new-grad and internship lists (filtered to the Twin Cities, mid-size metros
  and the South; internships = Twin Cities any season, or US off-season) plus
  Minnesota employers' Workday boards (Target, U.S. Bank, Medtronic, 3M,
  General Mills, Ameriprise, Securian).

# Staffing job poller

Every 30 minutes it checks Apex Systems, Brooksource and Experis for new
Twin Cities jobs that fit an entry-level candidate, and emails you only the
ones it hasn't sent before.

## Setup (one time)

1. **Make a private GitHub repo** and push these files to it
   (`poller.py`, `README.md`, `.github/workflows/poll.yml`).

2. **Make a Gmail app password** for the account that will send the email
   (your personal Gmail works best; school accounts often block app passwords):
   Google Account → Security → 2-Step Verification (must be on) → App passwords
   → create one named "job poller". Copy the 16-character password.

3. **Add three secrets** in the repo: Settings → Secrets and variables →
   Actions → New repository secret:
   - `GMAIL_USER`: the Gmail address that sends
   - `GMAIL_APP_PASSWORD`: the app password from step 2
   - `EMAIL_TO`: where you want the alerts (can be the same address)

4. **Run it once by hand**: Actions tab → "Staffing job poller" → Run workflow.
   Open the run's log: each firm prints how many jobs it found. After that it
   runs by itself every 30 minutes.

## Test on your laptop first (optional)

```
python poller.py --dry-run
```

Prints what it would email. Sends nothing and saves nothing.

## Changing what counts as a fit

Edit the word lists at the top of `poller.py`:
- `GOOD_WORDS`: a title needs one of these
- `BAD_WORDS`: a title with any of these is skipped
- `BAD_DESC`: a description with any of these is skipped

# Google Apps Script (apps-script/)

Runs inside Google, independent of Claude and GitHub:

- `syncTracker`: 1:43 PM and 8:43 PM Central, reads job emails and updates the
  Job_Application_Tracker sheet. Silent.
- `dailyLeetCode`: 7:37 AM Central, emails the next problem from the
  "NeetCode Daily Plan" sheet.

Setup: script.google.com -> New project -> paste `Code.gs` (and `appsscript.json`
via Project Settings -> "Show appsscript.json") -> Project Settings -> Script
Properties -> add `GEMINI_API_KEY` -> run `setup()` once and approve.
