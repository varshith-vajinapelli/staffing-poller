"""Pull only the needed columns from a DOL LCA disclosure Excel file into a small CSV.

Keeps computer/math occupations (SOC codes starting with 15-) only.
Usage: python extract_lca.py <input.xlsx> <output.csv>
"""
import csv
import sys
import time

import openpyxl

KEEP = [
    "CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "DECISION_DATE", "VISA_CLASS",
    "JOB_TITLE", "SOC_CODE", "SOC_TITLE", "BEGIN_DATE", "TOTAL_WORKER_POSITIONS",
    "NEW_EMPLOYMENT", "CONTINUED_EMPLOYMENT", "CHANGE_EMPLOYER",
    "EMPLOYER_NAME", "TRADE_NAME_DBA", "EMPLOYER_FEIN", "EMPLOYER_CITY",
    "EMPLOYER_STATE", "NAICS_CODE", "SECONDARY_ENTITY", "SECONDARY_ENTITY_BUSINESS_NAME",
    "WORKSITE_CITY", "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY",
    "PW_WAGE_LEVEL", "H_1B_DEPENDENT",
]

src, out = sys.argv[1], sys.argv[2]
start = time.time()
book = openpyxl.load_workbook(src, read_only=True)
sheet = book.worksheets[0]
rows = sheet.iter_rows(values_only=True)
header = [str(h).strip() if h is not None else "" for h in next(rows)]
missing = [k for k in KEEP if k not in header]
cols = [k for k in KEEP if k in header]
idx = [header.index(k) for k in cols]
soc_i = header.index("SOC_CODE")
print("missing columns:", missing, flush=True)

total = kept = 0
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for row in rows:
        total += 1
        soc = str(row[soc_i] or "")
        if soc.startswith("15-"):
            w.writerow(["" if row[i] is None else row[i] for i in idx])
            kept += 1
        if total % 100000 == 0:
            print("%d rows read, %d kept, %.0fs" % (total, kept, time.time() - start), flush=True)
print("DONE %s: %d rows, %d kept, %.0fs" % (src, total, kept, time.time() - start), flush=True)
