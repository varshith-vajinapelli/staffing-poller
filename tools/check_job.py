"""
Live check: read one Workday job and ask Gemini about it twice -
once with the old plain 6000-character cut, once with ai_text().

Run:  python tools/check_job.py <workday job link> <firm>
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import poller  # noqa: E402
import company_poller  # noqa: E402

link, firm = sys.argv[1], sys.argv[2]
desc = company_poller.workday_page_text(link)
print("Posting length: %d characters" % len(desc))
job = {"id": "check1", "title": "check", "firm": firm, "location": "", "link": link, "desc": desc}

new_text = poller.ai_text
print("Added past the cut: %r" % new_text(desc)[poller.DESC_LIMIT:])

poller.ai_text = lambda d: " ".join(d.split())[:poller.DESC_LIMIT]
old = poller.ask_gemini([job])["check1"]
poller.ai_text = new_text
new = poller.ask_gemini([job])["check1"]

print("OLD cut verdict: %s - %s" % old)
print("NEW cut verdict: %s - %s" % new)
if new[0] != "skip":
    sys.exit("FAIL: expected skip with the sponsorship line visible")
