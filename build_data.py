#!/usr/bin/env python3
"""
Reads the DSA Work Items Tracker from Smartsheet and writes data.json
for the DSA Dashboard (index.html). Run by GitHub Actions on a schedule.

Counting rules:
  * FUTURE      - Item Type == "Enhancement/Wishlist"  -> excluded from launch scope, listed separately.
  * SET ASIDE   - Status in {Duplicate, Working as Designed/For Review Later} (non-future)
                  -> excluded from the actionable count, surfaced as a separate number.
  * ACTIONABLE  - everything else. DONE = Complete. OPEN = Not Started/In Progress/Re-test/Re-open.
  * Re-open is a post-Complete status, so it counts as OPEN (a regression).

Env:
  SMARTSHEET_TOKEN  - Smartsheet API token (repo secret)
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
col = {c.id: c.title for c in sheet.columns}

def rowdict(r):
    d = {}
    for cell in r.cells:
        t = col.get(cell.column_id)
        if not t:
            continue
        v = cell.display_value if cell.display_value is not None else cell.value
        d[t] = v
    return d

rows = [rowdict(r) for r in sheet.rows]
rows = [r for r in rows if str(r.get("Work Item") or "").strip()]  # drop blank/unused rows

def g(r, *names):
    for n in names:
        if r.get(n) not in (None, ""):
            return r.get(n)
    return ""

def norm(s):
    return str(s).strip() if s is not None else ""

def owner_of(r):
    o = norm(g(r, "Assigned To"))
    return o or "Unassigned"

FUTURE_ITEMTYPE = "Enhancement/Wishlist"
SET_ASIDE = {"Duplicate", "Working as Designed/For Review Later"}
OPEN_STATUSES = {"Not Started", "In Progress", "Re-test", "Re-open"}

def item_type(r):
    return norm(g(r, "Item Type"))

def status_of(r):
    return norm(g(r, "Status"))

future_rows    = [r for r in rows if item_type(r) == FUTURE_ITEMTYPE]
nonfuture      = [r for r in rows if item_type(r) != FUTURE_ITEMTYPE]
set_aside_rows = [r for r in nonfuture if status_of(r) in SET_ASIDE]
actionable     = [r for r in nonfuture if status_of(r) not in SET_ASIDE]

def is_open(r):
    return status_of(r) != "Complete"

def cnt(seq, fn):
    return sum(1 for r in seq if fn(r))

# ---- Severity ----
SEV_MAP = {"S1-Mission Critical":"S1 \u00b7 Mission critical","S2-Critical":"S2 \u00b7 Critical",
           "S3-Elevated":"S3 \u00b7 Elevated","S4-General":"S4 \u00b7 General"}
SEV_ORDER = ["S1 \u00b7 Mission critical","S2 \u00b7 Critical","S3 \u00b7 Elevated","S4 \u00b7 General","Unspecified"]
def sev_of(r):
    return SEV_MAP.get(norm(g(r, "Severity")), "Unspecified")

# ---- Status (actionable) ----
STATUS_ORDER   = ["Complete","Re-open","In Progress","Re-test","Not Started"]
STATUS_DISPLAY = {"Complete":"Complete","Re-open":"Re-open","In Progress":"In progress",
                  "Re-test":"Re-test","Not Started":"Not started",
                  "Working as Designed/For Review Later":"Working as designed","Duplicate":"Duplicate"}
status_rows = []
for s in STATUS_ORDER:
    n = cnt(actionable, lambda r, s=s: status_of(r) == s)
    if n:
        status_rows.append({"label": STATUS_DISPLAY[s], "count": n})
_other = len(actionable) - sum(s["count"] for s in status_rows)
if _other:  # safety net: any status value not in STATUS_ORDER still gets counted, not dropped
    status_rows.append({"label": "Other/unspecified", "count": _other})

severity = []
for lab in SEV_ORDER:
    tot = cnt(actionable, lambda r, lab=lab: sev_of(r) == lab)
    if tot:
        severity.append({"label": lab, "total": tot,
                         "open": cnt(actionable, lambda r, lab=lab: sev_of(r) == lab and is_open(r))})

def group(field, blank_label):
    m = {}
    for r in actionable:
        k = norm(g(r, field)) or blank_label
        d = m.setdefault(k, {"total": 0, "open": 0})
        d["total"] += 1
        if is_open(r):
            d["open"] += 1
    out = [{"label": k, **v} for k, v in m.items()]
    out.sort(key=lambda x: -x["total"])
    return out

category   = group("Category", "Unspecified")
root_cause = group("Root Cause Layer", "Unspecified")

owner_map = {}
for r in actionable:
    k = owner_of(r)
    d = owner_map.setdefault(k, {"total": 0, "open": 0})
    d["total"] += 1
    if is_open(r):
        d["open"] += 1
owner = [{"label": k, **v} for k, v in owner_map.items()]
owner.sort(key=lambda x: -x["total"])

# ---- Item types (all rows) ----
it_map = {}
for r in rows:
    k = item_type(r) or "Untagged"
    it_map[k] = it_map.get(k, 0) + 1
item_types = [{"label": k, "count": v} for k, v in it_map.items()]
item_types.sort(key=lambda x: -x["count"])

# ---- Future / backlog list ----
future = [{"wi": norm(g(r, "Work Item")),
           "status": STATUS_DISPLAY.get(status_of(r), status_of(r) or "\u2014"),
           "owner": owner_of(r)} for r in future_rows]

resolved = cnt(actionable, lambda r: status_of(r) == "Complete")
total = len(actionable)
kpis = {
    "total": total,
    "resolved": resolved,
    "resolvedPct": round(resolved / total * 100) if total else 0,
    "inProgress": cnt(actionable, lambda r: status_of(r) == "In Progress"),
    "notStarted": cnt(actionable, lambda r: status_of(r) == "Not Started"),
    "reTest": cnt(actionable, lambda r: status_of(r) == "Re-test"),
    "reopened": cnt(actionable, lambda r: status_of(r) == "Re-open"),
    "openS1": cnt(actionable, lambda r: sev_of(r) == "S1 \u00b7 Mission critical" and is_open(r)),
    "openS2": cnt(actionable, lambda r: sev_of(r) == "S2 \u00b7 Critical" and is_open(r)),
    "totalOpen": cnt(actionable, is_open),
    "unassignedOpen": cnt(actionable, lambda r: owner_of(r) == "Unassigned" and is_open(r)),
    "future": len(future_rows),
    "setAside": len(set_aside_rows),
}

# ---- Recently resolved (Complete, Date Resolved within 7 days) ----
today = datetime.date.today()
recent = []
for r in actionable:
    if status_of(r) != "Complete":
        continue
    dr = norm(g(r, "Date Resolved"))
    try:
        d = datetime.date.fromisoformat(dr[:10])
    except ValueError:
        continue
    if 0 <= (today - d).days <= 7:
        recent.append({"wi": norm(g(r, "Work Item")), "resolved": d.isoformat()})
recent.sort(key=lambda x: x["resolved"], reverse=True)
recent = recent[:12]

# ---- Open items (actionable & open, all severities) ----
SEV_SHORT = {"S1-Mission Critical": "S1", "S2-Critical": "S2", "S3-Elevated": "S3", "S4-General": "S4"}
SEV_RANK = {"S1": 0, "S2": 1, "S3": 2, "S4": 3, "\u2014": 4}
STATUS_RANK = {"Re-open": 0, "In progress": 1, "Re-test": 2, "Not started": 3}
open_items = []
for r in actionable:
    if not is_open(r):
        continue
    sv = SEV_SHORT.get(norm(g(r, "Severity")), "\u2014")
    open_items.append({
        "wi": norm(g(r, "Work Item")),
        "sev": sv,
        "pri": norm(g(r, "Priority")) or "\u2014",
        "status": STATUS_DISPLAY.get(status_of(r), status_of(r)),
        "owner": owner_of(r),
        "type": item_type(r) or "Untagged",
    })
open_items.sort(key=lambda x: (SEV_RANK.get(x["sev"], 9), STATUS_RANK.get(x["status"], 9), x["wi"]))

# ---- Aging: how long each OPEN item has been open (from Date Found) ----
aging = []
for r in actionable:
    if not is_open(r):
        continue
    df = norm(g(r, "Date Found"))
    try:
        d0 = datetime.date.fromisoformat(df[:10]); days = (today - d0).days
    except ValueError:
        d0 = None; days = None
    aging.append({
        "wi": norm(g(r, "Work Item")),
        "sev": SEV_SHORT.get(norm(g(r, "Severity")), "\u2014"),
        "owner": owner_of(r),
        "status": STATUS_DISPLAY.get(status_of(r), status_of(r)),
        "opened": d0.isoformat() if d0 else "",
        "days": days,
    })
aging.sort(key=lambda x: (x["days"] is None, -(x["days"] or 0)))

# ---- Solved per day, by resource (Complete items that have a resolved date) ----
solved = []
for r in actionable:
    if status_of(r) != "Complete":
        continue
    dr = norm(g(r, "Date Resolved"))
    try:
        d = datetime.date.fromisoformat(dr[:10])
    except ValueError:
        continue
    df = norm(g(r, "Date Found"))
    try:
        f = datetime.date.fromisoformat(df[:10]); cyc = (d - f).days
    except ValueError:
        cyc = None
    solved.append({"owner": owner_of(r), "resolved": d.isoformat(), "wi": norm(g(r, "Work Item")), "days": cyc})
solved.sort(key=lambda x: x["resolved"])

data = {
    "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "kpis": kpis, "status": status_rows, "severity": severity,
    "category": category, "rootCause": root_cause, "owner": owner,
    "itemTypes": item_types, "future": future, "recent": recent, "openItems": open_items,
    "aging": aging, "solved": solved,
}
with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"Wrote data.json  actionable={total} done={resolved} open={kpis['totalOpen']} "
      f"reopened={kpis['reopened']} future={kpis['future']} setAside={kpis['setAside']}")
