"""
Live check: read job pages the way the poller does and show what the AI gets.

Run:  python tools/check_pages.py <link> [<link> ...]
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import company_poller  # noqa: E402

bad = 0
for link in sys.argv[1:]:
    try:
        text = re.sub(r"\s+", " ", company_poller.page_text({"link": link}))
    except Exception as e:
        text = ""
        print("ERROR %s: %s" % (link, e))
    print("%5d chars  %s\n       %s\n" % (len(text), link, text[:200]))
    if len(text) < 300:
        bad += 1
if bad:
    sys.exit("FAIL: %d page(s) still unreadable" % bad)
