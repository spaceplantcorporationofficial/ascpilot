"""Portfolio scanner: one structured snapshot of every app, version, in-app product and storefront state."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import sys
import time
from typing import Callable, Optional

from ascpilot import __version__
from ascpilot.asc import ASCError, Client
from ascpilot.config import CACHE_DIR

LIVE_STATES = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "PREORDER_READY_FOR_SALE"}
REVIEW_STATES = {"WAITING_FOR_REVIEW", "IN_REVIEW", "PROCESSING_FOR_APP_STORE", "WAITING_FOR_EXPORT_COMPLIANCE",
                 "PENDING_APPLE_RELEASE", "PROCESSING_FOR_DISTRIBUTION", "PENDING_CONTRACT"}
APPROVED_UNRELEASED_STATES = {"PENDING_DEVELOPER_RELEASE"}
REJECTED_STATES = {"REJECTED", "METADATA_REJECTED", "INVALID_BINARY"}
SELF_PULLED_STATES = {"DEVELOPER_REJECTED"}
DRAFT_STATES = {"PREPARE_FOR_SUBMISSION"}
REMOVED_STATES = {"REMOVED_FROM_SALE", "DEVELOPER_REMOVED_FROM_SALE", "REPLACED_WITH_NEW_VERSION"}

SKU_APPROVED = {"APPROVED"}
SKU_FROZEN_METADATA = {"MISSING_METADATA"}
SKU_FROZEN_UNSUBMITTED = {"READY_TO_SUBMIT"}
SKU_ACTION_NEEDED = {"DEVELOPER_ACTION_NEEDED", "REJECTED", "PENDING_BINARY_APPROVAL"}
SKU_IN_REVIEW = {"WAITING_FOR_REVIEW", "IN_REVIEW", "PROCESSING_CONTENT"}
SKU_REMOVED = {"REMOVED_FROM_SALE", "DEVELOPER_REMOVED_FROM_SALE"}

CACHE_FILE = CACHE_DIR / "snapshot.json"
CACHE_TTL_SECONDS = 15 * 60


def _version_state(attrs: dict) -> str:
    return attrs.get("appStoreState") or attrs.get("appVersionState") or "UNKNOWN"


def classify_version_state(state: str) -> str:
    if state in LIVE_STATES:
        return "live"
    if state in APPROVED_UNRELEASED_STATES:
        return "approved_unreleased"
    if state in REVIEW_STATES:
        return "in_review"
    if state in REJECTED_STATES:
        return "rejected"
    if state in SELF_PULLED_STATES:
        return "self_pulled"
    if state in DRAFT_STATES:
        return "draft"
    if state in REMOVED_STATES:
        return "removed"
    return "other"


def _scan_app(client: Client, app: dict) -> dict:
    aid = app["id"]
    attrs = app.get("attributes") or {}
    rec = {
        "id": aid,
        "name": attrs.get("name") or aid,
        "bundle_id": attrs.get("bundleId"),
        "sku": attrs.get("sku"),
        "versions": [],
        "products": [],
        "territories_on": None,
        "territories_total": None,
        "paid_upfront": None,
        "warnings": [],
    }

    # Versions (newest first)
    try:
        resp = client.get(f"/v1/apps/{aid}/appStoreVersions", {
            "limit": 12,
            "fields[appStoreVersions]": "platform,versionString,appStoreState,appVersionState,createdDate",
        })
        for v in resp.get("data") or []:
            va = v.get("attributes") or {}
            state = _version_state(va)
            rec["versions"].append({
                "id": v.get("id"),
                "platform": va.get("platform") or "IOS",
                "version": va.get("versionString"),
                "state": state,
                "bucket": classify_version_state(state),
                "created": va.get("createdDate"),
            })
    except ASCError as e:
        rec["warnings"].append(f"versions: {e}")

    # In-app purchases (consumable / non-consumable / non-renewing)
    try:
        data, _ = client.paginate(f"/v1/apps/{aid}/inAppPurchasesV2", {
            "limit": 200,
            "fields[inAppPurchases]": "name,productId,inAppPurchaseType,state",
        })
        for p in data:
            pa = p.get("attributes") or {}
            rec["products"].append({
                "id": p.get("id"), "kind": "iap", "type": pa.get("inAppPurchaseType"),
                "name": pa.get("name"), "product_id": pa.get("productId"), "state": pa.get("state") or "UNKNOWN",
            })
    except ASCError as e:
        if e.status not in (404,):
            rec["warnings"].append(f"in-app purchases: {e}")

    # Auto-renewable subscriptions
    try:
        _, included = client.paginate(f"/v1/apps/{aid}/subscriptionGroups", {
            "limit": 50,
            "include": "subscriptions",
            "limit[subscriptions]": 50,
            "fields[subscriptions]": "name,productId,state,subscriptionPeriod",
        })
        for s in included:
            if s.get("type") != "subscriptions":
                continue
            sa = s.get("attributes") or {}
            rec["products"].append({
                "id": s.get("id"), "kind": "subscription", "type": sa.get("subscriptionPeriod"),
                "name": sa.get("name"), "product_id": sa.get("productId"), "state": sa.get("state") or "UNKNOWN",
            })
    except ASCError as e:
        if e.status not in (404,):
            rec["warnings"].append(f"subscriptions: {e}")

    has_live = any(v["bucket"] == "live" for v in rec["versions"])
    if has_live:
        # Storefront availability
        try:
            data, _ = client.paginate(f"/v2/appAvailabilities/{aid}/territoryAvailabilities", {
                "limit": 200, "fields[territoryAvailabilities]": "available",
            }, max_pages=3)
            rec["territories_total"] = len(data)
            rec["territories_on"] = sum(1 for t in data if (t.get("attributes") or {}).get("available"))
        except ASCError as e:
            if e.status == 404:
                rec["territories_total"] = 0
                rec["territories_on"] = 0
            else:
                rec["warnings"].append(f"availability: {e}")
        # Upfront price (any non-zero manual price point => paid app)
        try:
            resp = client.get(f"/v1/appPriceSchedules/{aid}/manualPrices", {
                "include": "appPricePoint", "limit": 10, "fields[appPricePoints]": "customerPrice",
            })
            prices = []
            for inc in resp.get("included") or []:
                if inc.get("type") == "appPricePoints":
                    try:
                        prices.append(float((inc.get("attributes") or {}).get("customerPrice") or 0))
                    except ValueError:
                        pass
            rec["paid_upfront"] = any(p > 0 for p in prices) if prices else False
        except ASCError as e:
            if e.status != 404:
                rec["warnings"].append(f"price: {e}")
    return rec


def scan(client: Client, app_ids: Optional[list] = None, workers: int = 6,
         progress: Optional[Callable[[int, int], None]] = None) -> dict:
    started = time.time()
    apps, _ = client.paginate("/v1/apps", {"limit": 200, "fields[apps]": "name,bundleId,sku"})
    if app_ids:
        wanted = set(app_ids)
        apps = [a for a in apps if a["id"] in wanted or (a.get("attributes") or {}).get("bundleId") in wanted]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(_scan_app, client, a): a for a in apps}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            try:
                results.append(fut.result())
            except Exception as e:  # never let one app kill the scan
                a = futs[fut]
                results.append({"id": a["id"], "name": (a.get("attributes") or {}).get("name", a["id"]),
                                "versions": [], "products": [], "warnings": [f"scan failed: {e}"]})
            if progress:
                progress(done, len(apps))
    results.sort(key=lambda r: (r.get("name") or "").lower())
    return {
        "tool": "ascpilot",
        "tool_version": __version__,
        "scanned_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": round(time.time() - started, 1),
        "api_calls": client.calls,
        "filtered": bool(app_ids),
        "apps": results,
    }


def load_or_scan(client_factory: Callable[[], Client], refresh: bool = False, app_ids: Optional[list] = None,
                 quiet: bool = False) -> dict:
    if not refresh and not app_ids and CACHE_FILE.exists():
        try:
            snap = json.loads(CACHE_FILE.read_text())
            if time.time() - CACHE_FILE.stat().st_mtime < CACHE_TTL_SECONDS and snap.get("tool_version") == __version__:
                snap["from_cache"] = True
                return snap
        except (OSError, ValueError):
            pass

    def progress(done: int, total: int) -> None:
        if not quiet and sys.stderr.isatty():
            sys.stderr.write(f"\r  scanning App Store Connect… {done}/{total} apps")
            sys.stderr.flush()
            if done == total:
                sys.stderr.write("\r" + " " * 60 + "\r")

    snap = scan(client_factory(), app_ids=app_ids, progress=progress)
    if not app_ids:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(snap))
        except OSError:
            pass
    snap["from_cache"] = False
    return snap


def latest_by_platform(app: dict) -> dict:
    """Newest version per platform (the API returns newest first)."""
    out = {}
    for v in app.get("versions") or []:
        out.setdefault(v["platform"], v)
    return out


def sku_counts(app: dict) -> dict:
    c = {"approved": 0, "missing_metadata": 0, "ready_to_submit": 0, "action_needed": 0, "in_review": 0,
         "removed": 0, "other": 0, "total": 0}
    for p in app.get("products") or []:
        s = p.get("state")
        c["total"] += 1
        if s in SKU_APPROVED:
            c["approved"] += 1
        elif s in SKU_FROZEN_METADATA:
            c["missing_metadata"] += 1
        elif s in SKU_FROZEN_UNSUBMITTED:
            c["ready_to_submit"] += 1
        elif s in SKU_ACTION_NEEDED:
            c["action_needed"] += 1
        elif s in SKU_IN_REVIEW:
            c["in_review"] += 1
        elif s in SKU_REMOVED:
            c["removed"] += 1
        else:
            c["other"] += 1
    return c


def app_status(app: dict) -> str:
    """One-word storefront status for the app as a whole."""
    buckets = {v["bucket"] for v in (app.get("versions") or [])}
    if "live" in buckets:
        if app.get("territories_total") and app.get("territories_on") == 0:
            return "off_sale"
        return "live"
    for b in ("approved_unreleased", "in_review", "rejected", "self_pulled", "draft", "removed"):
        if b in buckets:
            return b
    return "empty"
