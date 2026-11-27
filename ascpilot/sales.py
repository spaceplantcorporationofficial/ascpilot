"""Sales Reports (Apple's official daily SUMMARY TSV). Honest numbers only: no FX conversion, no estimates."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
from typing import Optional

from ascpilot.asc import ASCError, Client

# Apple "Product Type Identifier" families
NEW_INSTALL_TYPES = {"1", "1F", "1T", "1E", "1EP", "1EU", "F1"}
UPDATE_TYPES = {"7", "7F", "7T", "F7"}
REDOWNLOAD_TYPES = {"3", "3F", "3T", "F3"}
IAP_TYPES = {"IA1", "IA9", "IAY", "IAC", "FI1", "IA1-M", "IA9-M", "IAY-M", "IAC-M"}
SUBSCRIPTION_TYPES = {"IAY", "IAY-M"}


def parse_tsv(text: str) -> list:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    header = [h.strip() for h in lines[0].split("\t")]
    idx = {h: i for i, h in enumerate(header)}

    def col(cols, name):
        i = idx.get(name, -1)
        return cols[i].strip() if 0 <= i < len(cols) else ""

    def num(s, cast=float):
        try:
            return cast(float(s)) if s else cast(0)
        except ValueError:
            return cast(0)

    rows = []
    for ln in lines[1:]:
        c = ln.split("\t")
        units = num(col(c, "Units"), int)
        unit_proceeds = num(col(c, "Developer Proceeds"))
        rows.append({
            "apple_id": col(c, "Apple Identifier"),
            "sku": col(c, "SKU"),
            "parent": col(c, "Parent Identifier"),
            "title": col(c, "Title"),
            "type": col(c, "Product Type Identifier"),
            "units": units,
            "proceeds": round(unit_proceeds * units, 4),
            "currency": col(c, "Currency of Proceeds") or "UNKNOWN",
            "customer_price": num(col(c, "Customer Price")),
            "country": col(c, "Country Code"),
            "date": col(c, "Begin Date"),
        })
    return rows


def aggregate(rows: list, sku_to_app: dict, days: int) -> dict:
    by_app = {}
    totals = {"new_installs": 0, "updates": 0, "redownloads": 0, "iap_units": 0, "paid_app_units": 0,
              "paying_units": 0, "subscription_units": 0, "proceeds": {}}
    for r in rows:
        if r["type"] in IAP_TYPES:  # IAP rows point at the parent app through its SKU
            app_id = sku_to_app.get(r["parent"]) or r["parent"] or "unknown"
        else:
            app_id = r["apple_id"] or sku_to_app.get(r["sku"]) or "unknown"
        e = by_app.setdefault(app_id, {
            "title": None, "new_installs": 0, "updates": 0, "redownloads": 0, "iap_units": 0,
            "paid_app_units": 0, "paying_units": 0, "subscription_units": 0, "proceeds": {}})
        if r["type"] not in IAP_TYPES and r["title"]:
            e["title"] = r["title"]
        u = r["units"]
        t = r["type"]
        if t in NEW_INSTALL_TYPES:
            e["new_installs"] += u
            totals["new_installs"] += u
            if r["proceeds"] > 0:
                e["paid_app_units"] += u
                totals["paid_app_units"] += u
        elif t in UPDATE_TYPES:
            e["updates"] += u
            totals["updates"] += u
        elif t in REDOWNLOAD_TYPES:
            e["redownloads"] += u
            totals["redownloads"] += u
        elif t in IAP_TYPES:
            e["iap_units"] += u
            totals["iap_units"] += u
            if t in SUBSCRIPTION_TYPES:
                e["subscription_units"] += u
                totals["subscription_units"] += u
        if r["proceeds"] != 0:
            if r["proceeds"] > 0:
                e["paying_units"] += u
                totals["paying_units"] += u
            e["proceeds"][r["currency"]] = round(e["proceeds"].get(r["currency"], 0.0) + r["proceeds"], 2)
            totals["proceeds"][r["currency"]] = round(totals["proceeds"].get(r["currency"], 0.0) + r["proceeds"], 2)
    return {"days": days, "by_app": by_app, "totals": totals}


def fetch_sales(client: Client, vendor: str, days: int, sku_to_app: dict, end: Optional[dt.date] = None) -> dict:
    """Pull `days` daily SUMMARY reports ending yesterday (UTC). Missing days are reported, never invented."""
    end = end or (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1))
    dates = [(end - dt.timedelta(days=i)).isoformat() for i in range(days)]
    rows, available, missing, errors = [], [], [], []

    def one(d):
        return d, client.sales_report(vendor, d)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for fut in concurrent.futures.as_completed([pool.submit(one, d) for d in dates]):
            try:
                d, text = fut.result()
            except ASCError as e:
                errors.append(str(e))
                continue
            if text is None:
                missing.append(d)
            else:
                available.append(d)
                rows.extend(parse_tsv(text))
    agg = aggregate(rows, sku_to_app, days)
    agg.update({
        "window_end": end.isoformat(),
        "days_with_reports": len(available),
        "days_without_reports": sorted(missing),
        "errors": errors[:5],
        "row_count": len(rows),
    })
    return agg
