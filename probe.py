import re, json, poller as p, company_poller as c
for link in ["https://fnbcorp.wd501.myworkdayjobs.com/FNBCORP/job/Pittsburgh-PA/Data-Analytics-Engineer-1---Pittsburgh-or-Hermitage--PA_2026-02664",
             "https://career-schwab.icims.com/jobs/129593/job?mobile=true&needsRedirect=false"]:
    job = {"link": link}
    try:
        t = c.page_text(job)
        print("PAGE", link[:50], "len", len(t), "|", " ".join(t.split())[:200])
    except Exception as e:
        print("PAGE", link[:50], "ERROR", e)
m = re.match(r"https://([^/]+)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?]+)(/job/[^?]+)", "https://fnbcorp.wd501.myworkdayjobs.com/FNBCORP/job/Pittsburgh-PA/Data-Analytics-Engineer-1---Pittsburgh-or-Hermitage--PA_2026-02664")
host, site, path = m.groups()
api = "https://%s/wday/cxs/%s/%s%s" % (host, host.split(".")[0], site, path)
try:
    d = json.loads(p.fetch(api, retry=False))
    print("FNB API keys", list(d.get("jobPostingInfo", {}).keys())[:15], "desc len", len(d.get("jobPostingInfo", {}).get("jobDescription", "")))
except Exception as e:
    print("FNB API", api, "ERROR", e)
