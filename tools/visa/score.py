"""Rank companies that have filed for junior software workers on H-1B visas.

Inputs:  out/lca_*.csv (from extract_lca.py), USCIS Employer Information CSVs
Output:  out/visa_companies.csv
"""
import collections
import csv
import glob
import math
import re
import sys

USCIS_FILES = sys.argv[1:]

# The plan's 5 codes, plus 15-1299 ("computer occupations, all other", which IT
# services firms like Cognizant and Infosys file under) and 15-1251 (programmers).
SOFTWARE_SOC = {"15-1252", "15-1253", "15-1211", "15-1243", "15-2051", "15-1299", "15-1251"}
JUNIOR_LEVELS = {"I", "II"}
L2_PLUS = {"II", "III", "IV"}

SUFFIXES = {"llc", "inc", "ltd", "limited", "corp", "corporation", "co", "company",
            "lp", "llp", "plc", "pllc", "pc", "pa", "the", "incorporated", "usa", "us",
            "na", "of", "and"}


def clean(name):
    # "Infosys Limited" / "INFOSYS LTD." -> "infosys"
    n = (name or "").lower()
    n = re.split(r"\b(?:d/?b/?a|aka|a/k/a)\b", n)[0]
    n = n.replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    words = [w for w in n.split() if w not in SUFFIXES]
    return " ".join(words)


def number(text):
    try:
        return int(float(text or 0))
    except ValueError:
        return 0


# ---------- 1. LCA filings: certified, H-1B, software, new employment ----------
companies = {}
for path in sorted(glob.glob("out/lca_*.csv")):
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r["CASE_STATUS"] != "Certified" or r["VISA_CLASS"] != "H-1B":
            continue
        if r["SOC_CODE"][:7] not in SOFTWARE_SOC:
            continue
        new = number(r["NEW_EMPLOYMENT"])
        if new <= 0:
            continue
        key = clean(r["EMPLOYER_NAME"])
        if not key:
            continue
        c = companies.setdefault(key, {
            "names": collections.Counter(), "fein4": collections.Counter(),
            "naics": collections.Counter(), "states": collections.Counter(),
            "titles": collections.Counter(), "levels": collections.Counter(),
            "new": 0, "junior": 0, "third_party": 0, "filings": 0,
        })
        c["names"][r["EMPLOYER_NAME"].strip()] += new
        c["fein4"][re.sub(r"\D", "", r["EMPLOYER_FEIN"])[-4:]] += 1
        c["naics"][r["NAICS_CODE"][:6]] += 1
        c["states"][r["WORKSITE_STATE"]] += new
        c["titles"][r["JOB_TITLE"].strip().title()] += new
        level = r["PW_WAGE_LEVEL"].strip()
        c["levels"][level] += new
        c["new"] += new
        c["filings"] += 1
        if level in JUNIOR_LEVELS:
            c["junior"] += new
        if r["SECONDARY_ENTITY"].strip().lower() in ("y", "yes"):
            c["third_party"] += new

# ---------- 2. USCIS approvals for new employment, matched by name + tax ID ----------
uscis = collections.defaultdict(lambda: [0, 0])   # (clean name, last4) -> [approved, denied]
uscis_by_name = collections.defaultdict(lambda: [0, 0])
for path in USCIS_FILES:
    with open(path, encoding="utf-16") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            r = {k.strip(): v for k, v in r.items() if k}
            key = clean(r.get("Employer (Petitioner) Name", ""))
            if not key:
                continue
            approved = number(r.get("New Employment Approval"))
            denied = number(r.get("New Employment Denial"))
            tax = (r.get("Tax ID") or "").strip()[-4:]
            uscis[(key, tax)][0] += approved
            uscis[(key, tax)][1] += denied
            uscis_by_name[key][0] += approved
            uscis_by_name[key][1] += denied

# ---------- 3. merge entities of the same company ----------
# One brand = one careers site to watch. Well-known companies with several legal
# entities are merged by name; everything else needs its first two words to match
# (so "tek deeds" and "tek systems" stay apart).
KNOWN_BRANDS = ["amazon", "google", "meta", "apple", "microsoft", "qualcomm", "deloitte",
                "goldman sachs", "morgan stanley", "jpmorgan", "j p morgan", "samsung", "siemens",
                "fidelity", "epic systems", "pwc", "pricewaterhousecoopers", "citadel", "rivian",
                "northern trust", "hitachi", "oracle", "ibm", "international business machines",
                "accenture", "cognizant", "infosys", "tata consultancy", "wipro", "hcl", "capgemini",
                "intel", "nvidia", "cisco", "salesforce", "walmart", "wal mart", "capital one",
                "bank of america", "wells fargo", "ernst and young", "kpmg", "tiktok", "bytedance",
                "uber", "linkedin", "adobe", "tesla", "bloomberg", "two sigma", "jane street",
                "citibank", "citigroup", "citicorp", "american express", "visa", "mastercard",
                "paypal", "ebay", "intuit", "workday", "servicenow", "broadcom", "vmware", "dell",
                "micron", "applied materials", "lam research", "kla", "texas instruments",
                "advanced micro devices", "boston consulting", "mastech", "insight global",
                "lexisnexis", "blue cross", "credit karma", "delta dental", "federal home loan",
                "university of california", "toyota", "ltimindtree", "tech mahindra", "mphasis",
                "virtusa", "ust global", "genpact", "ntt data", "zensar", "hexaware", "persistent",
                "medtronic", "optum", "unitedhealth", "target", "thomson reuters", "best buy"]
KNOWN_BRANDS.sort(key=len, reverse=True)   # longest first: "bank of america" before "bank"


def brand(key):
    for b in KNOWN_BRANDS:
        if key == b or key.startswith(b + " "):
            return b
    return " ".join(key.split()[:2])


merged = {}
for key, c in companies.items():
    b = brand(key)
    m = merged.setdefault(b, {
        "keys": [], "names": collections.Counter(), "fein4": collections.Counter(),
        "naics": collections.Counter(), "states": collections.Counter(),
        "titles": collections.Counter(), "levels": collections.Counter(),
        "new": 0, "junior": 0, "third_party": 0, "filings": 0,
    })
    m["keys"].append(key)
    for field in ("names", "fein4", "naics", "states", "titles", "levels"):
        m[field].update(c[field])
    for field in ("new", "junior", "third_party", "filings"):
        m[field] += c[field]

# ---------- 4. buckets ----------
IT_SERVICES = ["infosys", "tata consultancy", "cognizant", "accenture", "capgemini", "ibm",
               "wipro", "hcl", "ltimindtree", "larsen", "tech mahindra", "deloitte", "mphasis",
               "virtusa", "hexaware", "persistent systems", "ust global", "ernst", "kpmg",
               "pricewaterhouse", "pwc", "ntt data", "dxc", "genpact", "zensar", "birlasoft",
               "cyient", "coforge", "igate", "syntel", "mindtree", "epam", "globant",
               "slalom", "cgi"]
CAP_EXEMPT_WORDS = ["university", "college", "institute of technology", "school of medicine",
                    "research foundation", "national laboratory", "medical center",
                    "children s hospital", "health science center", "regents"]
# Software, internet, telecom, data, computer/electronics makers, computer services
TECH_NAICS = ("334", "5112", "513", "516", "517", "518", "519", "5415")
# Tech companies whose official NAICS is retail, autos, etc.
TECH_NAMES = ["amazon", "google", "meta", "apple", "microsoft", "netflix", "tiktok", "bytedance",
              "uber", "lyft", "wayfair", "ebay", "paypal", "tesla", "zoox", "waymo", "airbnb",
              "doordash", "intuit", "salesforce", "linkedin", "arriver", "rivian", "nuro"]


def bucket(key, naics, third_party_share):
    if any(w in key for w in CAP_EXEMPT_WORDS) or naics.startswith("6113"):
        return "3 Cap-exempt (university / research)"
    if any(key == w or key.startswith(w + " ") for w in IT_SERVICES):
        return "1 IT services / consulting"
    if third_party_share >= 0.5:
        # most of their visa workers are placed at client sites = staffing/consulting
        return "1 IT services / consulting"
    if naics.startswith(TECH_NAICS) or key.split()[0] in TECH_NAMES:
        return "4 Tech companies"
    return "2 Large non-tech (big IT team)"


# ---------- 5. score ----------
rows = []
for key, c in merged.items():
    if c["junior"] < 3:
        continue
    name = c["names"].most_common(1)[0][0]
    naics = c["naics"].most_common(1)[0][0]
    approved = denied = 0
    for k in c["keys"]:
        found = None
        for fein4 in c["fein4"]:
            if (k, fein4) in uscis:
                found = uscis[(k, fein4)]
                break
        a, d = found or uscis_by_name.get(k) or (0, 0)
        approved += a
        denied += d
    decided = approved + denied
    approval = approved / decided if decided else None
    l2plus = sum(c["levels"][l] for l in L2_PLUS) / c["new"]
    third = c["third_party"] / c["new"]
    group = bucket(key, naics, third)
    cap_exempt = group.startswith("3")
    # More junior hires is better (log so giants don't drown everyone else);
    # higher approval and Level II+ pay (better lottery odds) raise it;
    # cap-exempt employers skip the lottery entirely.
    # Filings overstate hires (Goldman filed 9,314 junior positions, USCIS approved
    # 2,220 new petitions), so estimate real junior hires from USCIS approvals
    # times the company's junior share of filings.
    # USCIS approvals cover every occupation (a university's are mostly
    # researchers), and filings overcount positions; the smaller of the two is
    # the honest estimate of junior software hires.
    junior_share = c["junior"] / c["new"]
    est_junior = min(c["junior"], approved * junior_share) if approved else c["junior"] * 0.25
    score = math.log1p(est_junior)
    score *= approval if approval is not None else 0.8
    score *= 0.6 + 0.4 * l2plus
    if cap_exempt:
        score *= 1.5
    rows.append({
        "company": name,
        "brand": key,
        "entities": len(c["keys"]),
        "bucket": group,
        "score": round(score, 3),
        "est_junior_hires": round(est_junior),
        "junior_positions_filed": c["junior"],
        "all_new_software_hires": c["new"],
        "pct_level2_plus": round(l2plus * 100, 1),
        "level_I": c["levels"]["I"], "level_II": c["levels"]["II"],
        "level_III": c["levels"]["III"], "level_IV": c["levels"]["IV"],
        "uscis_new_approved": approved, "uscis_new_denied": denied,
        "approval_rate": "" if approval is None else round(approval * 100, 1),
        "third_party_pct": round(third * 100, 1),
        "cap_exempt": "Y" if cap_exempt else "",
        "naics": naics,
        "top_states": "; ".join(s for s, n in c["states"].most_common(4) if s),
        "top_titles": "; ".join(t for t, n in c["titles"].most_common(4)),
        "e_verify": "",
    })

rows.sort(key=lambda r: -r["score"])
for i, r in enumerate(rows, 1):
    r["rank"] = i
fields = ["rank"] + [k for k in rows[0] if k != "rank"]
with open("out/visa_companies.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print("companies scored:", len(rows))
print(collections.Counter(r["bucket"] for r in rows))
matched = sum(1 for r in rows if r["approval_rate"] != "")
print("matched to USCIS:", matched, "of", len(rows))
