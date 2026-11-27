"""Revenue Leak Scanner — deterministic rules that find money an App Store account is failing to collect."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from ascpilot.scan import app_status, latest_by_platform, sku_counts

SEVERITY_WEIGHT = {"critical": 40, "high": 25, "medium": 10, "low": 3}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

PLAYBOOKS = {
    "APPROVED_NOT_RELEASED": [
        "Open App Store Connect → the app → the approved version.",
        "Click “Release This Version”. Approved builds earn nothing until released.",
        "Consider switching future versions to automatic release.",
    ],
    "APPROVED_OFF_SALE": [
        "App Store Connect → the app → Pricing and Availability.",
        "Under Availability, add all territories (or at least your top markets) and save.",
        "Apple keeps the app approved but invisible — no review needed to turn it back on.",
    ],
    "SKU_MISSING_METADATA": [
        "For each product: add a display name + description localization, a price, and the App Review screenshot.",
        "Subscriptions also need availability set and the group localization filled in.",
        "Once the state flips to Ready to Submit, attach it to your next app version (see next playbook).",
    ],
    "SKU_READY_TO_SUBMIT": [
        "Products in Ready to Submit are finished but have never been sent to Apple — customers cannot buy them.",
        "If the app has never had an approved IAP: open your next version → “In-App Purchases and Subscriptions” → add them, then submit the version.",
        "If it already has approved IAPs: submit each product directly from its page.",
    ],
    "SKU_ACTION_NEEDED": [
        "Open each product and read the reviewer note in the App Review section.",
        "Typical fixes: replace the review screenshot with the real paywall, align the name with the in-app copy, fix the price/territory mismatch.",
        "Resubmit the product (with a version if Apple asks for a binary).",
    ],
    "VERSION_REJECTED_BLOCKING": [
        "Read the Resolution Center message for the rejected version.",
        "Fix and resubmit — the in-app products waiting on this version cannot go live until it is approved.",
        "If the rejection is metadata-only, you can usually reply/fix metadata without a new build.",
    ],
    "LIVE_NO_REVENUE_PATH": [
        "This live app is free and has no approved in-app product, so it cannot earn App Store revenue.",
        "Either add a paywall (one lifetime unlock is the fastest to get approved) or set an upfront price.",
        "If it monetises through ads or the web, mark it as intentional and ignore this check.",
    ],
    "STALLED_DRAFT": [
        "A version has been sitting in Prepare for Submission. Finished work that isn't submitted earns nothing.",
        "Submit it, or delete the draft so it stops blocking product submissions.",
    ],
    "INSTALLS_NO_PAID": [
        "People are installing but nobody is paying. That's a paywall problem, not a traffic problem.",
        "Show the paywall at first value moment (not on launch), add a free trial or lifetime option, and A/B the price.",
        "Check that the product shown in-app is actually APPROVED (a frozen SKU silently fails to load).",
    ],
}


def _days_since(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - t).days
    except ValueError:
        return None


def _leak(app: dict, code: str, severity: str, title: str, detail: str, items: Optional[list] = None) -> dict:
    return {
        "app_id": app["id"],
        "app": app.get("name"),
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "items": items or [],
        "fix": PLAYBOOKS.get(code, []),
    }


ACTIVE_DAYS = 90  # an app with a version created in this window is "in play"


def _newest_version_age(app: dict) -> Optional[int]:
    ages = [a for a in (_days_since(v.get("created")) for v in app.get("versions") or []) if a is not None]
    return min(ages) if ages else None


def find_leaks(snapshot: dict, sales: Optional[dict] = None, min_installs_for_paywall_check: int = 10,
               ignore: Optional[set] = None) -> list:
    leaks = []
    sales_by_app = (sales or {}).get("by_app") or {}
    ignore = ignore or set()
    for app in snapshot.get("apps") or []:
        if app["id"] in ignore or (app.get("bundle_id") and app["bundle_id"] in ignore):
            continue
        status = app_status(app)
        age = _newest_version_age(app)
        recent = age is not None and age <= ACTIVE_DAYS
        selling = status in ("live", "in_review", "approved_unreleased")
        # Severity for checkout problems: hot if the app can sell now, warm if recently worked on, else cold.
        sku_sev = "high" if selling else ("medium" if recent else "low")
        latest = latest_by_platform(app)
        counts = sku_counts(app)
        products = app.get("products") or []
        has_live = any(v["bucket"] == "live" for v in app.get("versions") or [])

        # 1. Approved build waiting on a button press
        for plat, v in latest.items():
            if v["bucket"] == "approved_unreleased":
                leaks.append(_leak(app, "APPROVED_NOT_RELEASED", "critical",
                                   f"{plat} {v['version']} is approved but not released",
                                   "Apple approved this version and it is waiting for you to release it."))

        # 2. Approved but unavailable everywhere
        if status == "off_sale":
            if recent:
                leaks.append(_leak(app, "APPROVED_OFF_SALE", "critical", "Live build available in 0 territories",
                                   f"The app is approved but switched off in all {app.get('territories_total')} storefronts, "
                                   f"and you shipped a version {age} days ago."))
            else:
                leaks.append(_leak(app, "APPROVED_OFF_SALE", "low", "Retired? Approved build is off sale everywhere",
                                   f"No new version in {age} days. If this is intentional, run `ascpilot ignore {app['id']}`."))

        # 3. Frozen checkout — products customers cannot buy
        missing = [p for p in products if p.get("state") == "MISSING_METADATA"]
        unsub = [p for p in products if p.get("state") == "READY_TO_SUBMIT"]
        action = [p for p in products if p.get("state") in ("DEVELOPER_ACTION_NEEDED", "REJECTED")]

        def _items(ps):
            return [f"{p.get('name') or p.get('product_id')} ({p.get('product_id')}, {p.get('kind')})" for p in ps]

        relevant = has_live or status in ("in_review", "approved_unreleased", "rejected", "draft", "self_pulled")
        if missing and relevant:
            leaks.append(_leak(app, "SKU_MISSING_METADATA", sku_sev,
                               f"{len(missing)} product(s) stuck in Missing Metadata",
                               "These in-app products/subscriptions are incomplete, so the paywall cannot sell them.",
                               _items(missing)))
        if unsub and relevant:
            leaks.append(_leak(app, "SKU_READY_TO_SUBMIT", sku_sev,
                               f"{len(unsub)} finished product(s) never submitted",
                               "Complete products that were never sent to App Review — they are not purchasable.",
                               _items(unsub)))
        if action and relevant:
            leaks.append(_leak(app, "SKU_ACTION_NEEDED", sku_sev,
                               f"{len(action)} product(s) rejected / need developer action",
                               "Apple sent these products back.", _items(action)))

        # 4. A rejected version is holding products hostage
        pending_skus = len(missing) + len(unsub) + len(action) + counts["in_review"]
        for plat, v in latest.items():
            if v["bucket"] == "rejected":
                sev = ("high" if (pending_skus or not has_live) else "medium") if (selling or recent) else "low"
                leaks.append(_leak(app, "VERSION_REJECTED_BLOCKING", sev,
                                   f"{plat} {v['version']} was rejected ({v['state']})",
                                   ("Products waiting on this version cannot go live." if pending_skus
                                    else "The latest version is rejected; fixes and new paywalls are blocked.")))

        # 5. Live, free, nothing to buy
        if has_live and status != "off_sale" and counts["approved"] == 0 and app.get("paid_upfront") is False:
            sev = "medium" if counts["total"] else "low"
            leaks.append(_leak(app, "LIVE_NO_REVENUE_PATH", sev, "Live and free with no approved product",
                               "Every download of this app earns $0 on the App Store today."
                               + (f" ({counts['total']} product(s) exist but none are approved.)" if counts["total"] else "")))

        # 6. Drafts rotting
        for plat, v in latest.items():
            d_age = _days_since(v.get("created"))
            if v["bucket"] == "draft" and d_age is not None and d_age >= 21 and (has_live or counts["total"]):
                leaks.append(_leak(app, "STALLED_DRAFT", "medium" if selling else "low",
                                   f"{plat} {v['version']} has sat unsubmitted for {d_age} days",
                                   "Finished work that isn't submitted earns nothing."))

        # 7. Installs but zero payers (needs Sales data)
        s = sales_by_app.get(app["id"])
        if s and s.get("new_installs", 0) >= min_installs_for_paywall_check and s.get("paying_units", 0) == 0 \
                and (counts["total"] or app.get("paid_upfront")):
            leaks.append(_leak(app, "INSTALLS_NO_PAID", "medium",
                               f"{s['new_installs']} new installs, 0 paid units",
                               f"In the last {sales.get('days')} days nobody paid. Traffic is there; conversion is not."))

    leaks.sort(key=lambda l: (SEVERITY_ORDER[l["severity"]], (l.get("app") or "").lower()))
    return leaks


def summarize(leaks: list, snapshot: dict) -> dict:
    by_sev = {k: 0 for k in SEVERITY_WEIGHT}
    for l in leaks:
        by_sev[l["severity"]] += 1
    apps_hit = sorted({l["app_id"] for l in leaks if l["severity"] != "low"})
    frozen_products = sum(len(l["items"]) for l in leaks if l["code"].startswith("SKU_") and l["severity"] != "low")
    per_app_score = {}
    for l in leaks:
        per_app_score[l["app_id"]] = min(100, per_app_score.get(l["app_id"], 0) + SEVERITY_WEIGHT[l["severity"]])
    total_apps = len(snapshot.get("apps") or [])
    return {
        "total_leaks": len(leaks),
        "by_severity": by_sev,
        "apps_scanned": total_apps,
        "apps_with_leaks": len(apps_hit),
        "frozen_products": frozen_products,
        "worst_apps": sorted(per_app_score.items(), key=lambda kv: -kv[1])[:5],
    }
