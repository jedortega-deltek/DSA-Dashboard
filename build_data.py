#!/usr/bin/env python3
"""
Reads the DSA Work Items Tracker from Smartsheet and writes data.json
for the live dashboard (index.html). Run by GitHub Actions on a schedule.

Env:
  SMARTSHEET_TOKEN  - Smartsheet API access token (stored as a repo secret)
  SHEET_ID          - optional override; defaults to the DSA tracker id
"""
import os, json, datetime, sys

SHEET_ID = int(os.environ.get("SHEET_ID", "564920532815748"))
TOKEN = os.environ.get("SMARTSHEET_TOKEN")
if not TOKEN:
    sys.exit("SMARTSHEET_TOKEN not set")

import smartsheet
ss = smartsheet.Smartsheet(TOKEN)
ss.errors_as_exceptions(True)
sheet = ss.Sheets.get_sheet(SHEET_ID)

# column id -> title
col = {c.id: c.title for c in sheet.columns}

def rowdict(r):
    d = {}
    for cell in r.cells:
        title = col.get(cell.column_id)
        if not title:
            continue
        val = cell.display_value if cell.display_value is not None else cell.value
        d[title] = val
    return d

rows = [rowdict(r) for r in sheet.rows]

def g(r, *names):
    for n in names:
        if r.get(n) not in (None, ""):
            return r.get(n)
    return ""

def norm(s):
    return (str(s).strip() if s is not None else "")

CLOSED = {"Complete"}
def is_open(r):
    return norm(g(r, "Status")) not in CLOSED

total = len(rows)
def cnt(fn):
    return sum(1 for r in rows if fn(r))

# ---- Status ----
STATUS_ORDER = ["Complete", "In Progress", "Re-test",
                "Working as Designed/For Review Later", "Not Started"]
STATUS_DISPLAY = {
    "Complete": "Complete", "In Progress": "In progress", "Re-test": "Re-test",
    "Working as Designed/For Review Later": "Working as designed",
    "Not Started": "Not started",
}
status_rows = []
for s in STATUS_ORDER:
    n = cnt(lambda r, s=s: norm(g(r, "Status")) == s)
    if n:
        status_rows.append({"label": STATUS_DISPLAY[s], "count": n})

# ---- Severity ----
SEV_MAP = {
    "S1-Mission Critical": "S1 \u00b7 Mission critical",
    "S2-Critical": "S2 \u00b7 Critical",
    "S3-Elevated": "S3 \u00b7 Elevated",
    "S4-General": "S4 \u00b7 General",
}
SEV_ORDER = ["S1 \u00b7 Mission critical", "S2 \u00b7 Critical",
             "S3 \u00b7 Elevated", "S4 \u00b7 General", "Unspecified"]
def sev_label(r):
    return SEV_MAP.get(norm(g(r, "Severity")), "Unspecified")
severity = []
for lab in SEV_ORDER:
    tot = cnt(lambda r, lab=lab: sev_label(r) == lab)
    op = cnt(lambda r, lab=lab: sev_label(r) == lab and is_open(r))
    if tot:
        severity.append({"label": lab, "total": tot, "open": op})

# ---- generic grouping (Category, Root Cause, Owner) ----
def group(field, blank_label):
    m = {}
    for r in rows:
        k = norm(g(r, field)) or blank_label
        d = m.setdefault(k, {"total": 0, "open": 0})
        d["total"] += 1
        if is_open(r):
            d["open"] += 1
    out = [{"label": k, "total": v["total"], "open": v["open"]} for k, v in m.items()]
    out.sort(key=lambda x: -x["total"])
    return out

category = group("Category", "Unspecified")
root_cause = group("Root Cause Layer", "Unspecified")
owner = group("Assigned To", "Unassigned")

# ---- KPIs ----
resolved = cnt(lambda r: norm(g(r, "Status")) == "Complete")
kpis = {
    "total": total,
    "resolved": resolved,
    "resolvedPct": round(resolved / total * 100) if total else 0,
    "inProgress": cnt(lambda r: norm(g(r, "Status")) == "In Progress"),
    "notStarted": cnt(lambda r: norm(g(r, "Status")) == "Not Started"),
    "openS1": cnt(lambda r: sev_label(r) == "S1 \u00b7 Mission critical" and is_open(r)),
    "openS2": cnt(lambda r: sev_label(r) == "S2 \u00b7 Critical" and is_open(r)),
    "totalOpen": cnt(is_open),
    "unassignedOpen": cnt(lambda r: not norm(g(r, "Assigned To")) and is_open(r)),
}

# ---- Recently resolved (Date Resolved within last 7 days) ----
today = datetime.date.today()
recent = []
for r in rows:
    dr = norm(g(r, "Date Resolved"))
    if not dr:
        continue
    try:
        d = datetime.date.fromisoformat(dr[:10])
    except ValueError:
        continue
    if (today - d).days <= 7 and (today - d).days >= 0:
        recent.append({"wi": norm(g(r, "Work Item")), "resolved": d.isoformat()})
recent.sort(key=lambda x: x["resolved"], reverse=True)
recent = recent[:12]

# ---- Open critical punch list (S1/S2 not Complete) ----
SEV_RANK = {"S1 \u00b7 Mission critical": 0, "S2 \u00b7 Critical": 1}
PRI_RANK = {"High": 0, "Medium": 1, "Low": 2, "": 3}
punch = []
for r in rows:
    lab = sev_label(r)
    if lab in SEV_RANK and is_open(r):
        punch.append({
            "wi": norm(g(r, "Work Item")),
            "sev": lab.split(" ")[0],
            "pri": norm(g(r, "Priority")) or "\u2014",
            "status": STATUS_DISPLAY.get(norm(g(r, "Status")), norm(g(r, "Status"))),
            "owner": norm(g(r, "Assigned To")) or "Unassigned",
        })
punch.sort(key=lambda x: (SEV_RANK.get("S1 \u00b7 Mission critical" if x["sev"] == "S1" else "S2 \u00b7 Critical", 9),
                          PRI_RANK.get(x["pri"], 3)))

data = {
    "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "kpis": kpis,
    "status": status_rows,
    "severity": severity,
    "category": category,
    "rootCause": root_cause,
    "owner": owner,
    "recent": recent,
    "punch": punch,
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Wrote data.json  total={total} resolved={resolved} openS1={kpis['openS1']} openS2={kpis['openS2']}")
