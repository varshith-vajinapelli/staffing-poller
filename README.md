# 🤖 Fully managed by a Claude agent

> **This repo is automated.** The code is written and maintained by Claude (Anthropic's AI agent), and the pollers run on their own via GitHub Actions. Commits like "update seen jobs" are the bot saving its progress.

# Job pollers

Two scripts find new entry-level tech jobs, have an AI screen each one against
a candidate profile, and email only new matches:

- `poller.py` (staffing and consulting firms): every 30 minutes. Staffing
  firms Apex Systems, Brooksource, Experis, Robert Half, TEKsystems, Insight
  Global, Randstad, Beacon Hill; and OPT-friendly IT consulting firms
  Accenture, Capgemini, Cognizant, Deloitte, Wipro, IBM, searched across the
  whole US. (Infosys and TCS block requests from GitHub's servers, so they
  can't be included. IBM's job pages are built by JavaScript, so the AI
  judges IBM jobs from a short summary.)
- `company_poller.py` (companies hiring directly): a digest twice a day
  (11:52 AM and 5:52 PM Central). Simplify's public new-grad and internship
  lists plus Minnesota employers' Workday boards (Target, U.S. Bank, Medtronic,
  3M, General Mills, Ameriprise, Securian).

Both run on GitHub Actions, triggered by cron-job.org. They share `jobs.db`
(what was already sent, what was dropped and why, pending alerts), and only one
runs at a time - if one starts while the other is going, it waits its turn.

## Where it looks (staffing poller)

For the staffing firms: all of Minnesota, fully remote US jobs, and these
metros with their suburbs (the consulting firms are searched across the whole US):
Milwaukee, Madison, Detroit / Ann Arbor, Grand Rapids, Chicago, Atlanta,
Austin, Dallas - Fort Worth, Houston, San Antonio, Phoenix, Charlotte,
Raleigh - Durham, Columbus, Kansas City (MO and KS sides), Nashville.

To add a place: add its cities to `PLACES`, its center to `METRO_CENTERS`,
its Robert Half city page to `ROBERT_HALF_CITIES`, and its state to the
TEKsystems list in `teksystems_jobs()`.

## What happens each run

1. Check every firm (all at once) in every place. A firm or city that fails
   gets one retry after 5 seconds.
2. Drop jobs older than 3 days (`MAX_AGE_DAYS`) and jobs the keyword filter
   rejects. Every drop is saved with its reason (see "Dropped log").
3. Skip jobs already emailed.
4. Send the new ones to Gemini, 5 per request (`AI_BATCH`), with each job's
   full description. It uses `gemini-3.8-flash`, falling back to
   `gemini-3.5-flash-lite` if 3.8 Flash is overloaded. Each job gets
   apply / maybe / skip and a one-line reason. When unsure it picks "maybe" -
   missing a good job is worse than showing a borderline one.
5. Email the apply and maybe jobs (skipped ones are listed at the bottom), then
   mark them as sent. A run with nothing new sends nothing.

## When something goes wrong

- **Gemini out of credit / bad key (401/402/403):** stops after one call and
  holds every new job (nothing emailed, nothing lost) until it's fixed. Top up
  in Google AI Studio -> Billing.
- **Gemini down for a while (503s, timeouts):** jobs are held and retried on
  the next run. After 4 failed staffing runs in a row (2 failed company
  digests) they're sent unfiltered so nothing gets stuck.
- **One AI batch fails:** only those jobs wait for the next run.
- **Alerts:** every problem (a firm or city failing, a firm returning 0 jobs
  for 6 hours, AI failures, a missing secret, a crash) is shown in a red
  "Attention needed" box at the top of the next job email. If no job email
  goes out within 3 hours it gets its own email; a crash emails right away.

## Keyword filter

Edit the lists at the top of `poller.py` (and `TECH_WORDS` /
`COMPANY_BAD_WORDS` in `company_poller.py`):

- `GOOD_WORDS`: a title needs one of these.
- `BAD_WORDS`: a title with any of these is dropped. Whole words only
  ("lead" blocks "Lead Engineer", not "Leadership"); a word ending in `*` is
  a prefix ("recruit*"). Level III/IV/3/4/5 right after the role
  ("Engineer III") is dropped too.
- `BAD_DESC`: a description with any of these phrases is dropped (clear
  citizenship / clearance requirements).
- Descriptions asking for 3+ years of experience are dropped.

## Dropped log

Every job the filters or the AI drop is kept for 30 days with its link and
the reason. To see the last 7 days:

```
python poller.py --dropped
```

## Setup (one time)

1. **Gmail app password** for the sending account: Google Account -> Security
   -> 2-Step Verification (must be on) -> App passwords -> create one.

2. **Repo secrets** (Settings -> Secrets and variables -> Actions):
   - `GMAIL_USER`: the Gmail address that sends
   - `GMAIL_APP_PASSWORD`: the app password from step 1
   - `EMAIL_TO`: where alerts go (can be the same address)
   - `GEMINI_API_KEY`: from Google AI Studio (prepaid billing; set a monthly
     spend cap under Spend)
   - `CANDIDATE_PROFILE`: plain-text profile the AI screens against - skills,
     experience, what roles are wanted and not wanted, work authorization.
     Kept as a secret so personal details stay out of this public repo.

3. **Schedule**: cron-job.org calls each workflow's dispatch endpoint (staffing
   every 30 minutes, company digest at 11:52 AM and 5:52 PM Central). Runs can
   also be started by hand: Actions tab -> pick the workflow -> Run workflow.

## Test on your laptop (optional)

```
python poller.py --dry-run
python company_poller.py --dry-run
```

Prints what would be emailed. Sends nothing and saves nothing.

# Google Apps Script (apps-script/)

Runs inside Google, independent of Claude and GitHub:

- `syncTracker`: 1:43 PM and 8:43 PM Central, reads job emails and updates the
  Job_Application_Tracker sheet. Silent.
- `dailyLeetCode`: 7:37 AM Central, emails the next problem from the
  "NeetCode Daily Plan" sheet.

Setup: script.google.com -> New project -> paste `Code.gs` (and `appsscript.json`
via Project Settings -> "Show appsscript.json") -> Project Settings -> Script
Properties -> add `GEMINI_API_KEY` -> run `setup()` once and approve.
