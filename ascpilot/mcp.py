"""MCP server (JSON-RPC 2.0 over stdio, newline-delimited) so AI agents can run ASC Pilot as tools."""

from __future__ import annotations

import json
import sys
import traceback

from ascpilot import BUY_URL, PRODUCT, __version__, checkout_url, pricing
from ascpilot import license as lic
from ascpilot.asc import Client
from ascpilot.config import load_credentials, load_ignore
from ascpilot.leaks import find_leaks, summarize
from ascpilot.report import markdown_report
from ascpilot.sales import fetch_sales
from ascpilot.scan import app_status, latest_by_platform, load_or_scan, sku_counts

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "asc_list_apps",
        "description": "List every app in the App Store Connect account with storefront status, latest version per "
                       "platform, and in-app product health (approved/total).",
        "inputSchema": {"type": "object", "properties": {
            "refresh": {"type": "boolean", "description": "Bypass the 15-minute cache"}}},
        "pro": False,
    },
    {
        "name": "asc_review_radar",
        "description": "Review radar: versions approved but unreleased, rejected, in review, drafts and live.",
        "inputSchema": {"type": "object", "properties": {"refresh": {"type": "boolean"}}},
        "pro": False,
    },
    {
        "name": "asc_revenue_leaks",
        "description": "Revenue Leak Scan: approved-but-unreleased builds, apps off sale, in-app products customers "
                       "cannot buy (Missing Metadata / Ready to Submit / rejected), rejections blocking paywalls, "
                       "free apps with nothing to buy, stalled drafts, installs with zero payers. Free plan returns the "
                       "summary and the single worst leak; Pro returns all leaks with fix playbooks.",
        "inputSchema": {"type": "object", "properties": {
            "refresh": {"type": "boolean"},
            "days": {"type": "integer", "description": "Sales window for the paywall check (Pro)", "default": 14}}},
        "pro": False,
    },
    {
        "name": "asc_upgrade",
        "description": "How to buy ASC Pilot Pro ($49 one-time) or Studio ($149): live checkout links (Stripe), "
                       "features, and how the license is delivered/activated. Use when a Pro tool is locked or the "
                       "user asks to upgrade. Never purchase on the user's behalf without their explicit go-ahead.",
        "inputSchema": {"type": "object", "properties": {}},
        "pro": False,
    },
    {
        "name": "asc_sales_summary",
        "description": "[Pro] Apple Sales API: new installs, re-downloads, updates, IAP units, paying units and "
                       "developer proceeds per app and per currency (no FX, no estimates).",
        "inputSchema": {"type": "object", "properties": {"days": {"type": "integer", "default": 14}}},
        "pro": True,
    },
    {
        "name": "asc_cashflow_report",
        "description": "[Pro] Full Markdown Cash-Flow report: ranked leaks with fixes, portfolio table, money table.",
        "inputSchema": {"type": "object", "properties": {"days": {"type": "integer", "default": 14},
                                                           "refresh": {"type": "boolean"}}},
        "pro": True,
    },
]


def _client_factory():
    creds = load_credentials()
    if not creds:
        raise RuntimeError("No App Store Connect API key configured. Ask the user to run `ascpilot setup`.")
    return creds, (lambda: Client(creds))


def _compact_app(a):
    return {
        "id": a["id"], "name": a["name"], "bundle_id": a.get("bundle_id"), "status": app_status(a),
        "latest": {p: {"version": v["version"], "state": v["state"]} for p, v in latest_by_platform(a).items()},
        "products": sku_counts(a),
        "territories": f"{a['territories_on']}/{a['territories_total']}" if a.get("territories_total") else None,
    }


def _sales(creds, snap, days):
    if not creds.vendor_number:
        return None
    sku_to_app = {a.get("sku"): a["id"] for a in snap["apps"] if a.get("sku")}
    return fetch_sales(Client(creds), creds.vendor_number, days, sku_to_app)


def call_tool(name: str, args: dict):
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if not tool:
        raise KeyError(name)
    pro = lic.is_pro()
    if name == "asc_upgrade":
        info = pricing()
        info["how_to_activate"] = ("After checkout the key is shown on the thank-you page. Activate with "
                                   "`ascpilot license activate <KEY>` or `ascpilot license claim <cs_... session id>`.")
        info["currently_pro"] = pro
        return json.dumps(info, indent=1), False
    if tool["pro"] and not pro:
        return (f"{name} is an {PRODUCT} Pro tool ($49 one-time). Ask the user if they want to unlock it: "
                f"checkout {checkout_url('pro')} — the key appears instantly after payment; then "
                f"`ascpilot license activate <KEY>`."), True
    creds, factory = _client_factory()
    snap = load_or_scan(factory, refresh=bool(args.get("refresh")), quiet=True)
    days = int(args.get("days") or 14)
    if name == "asc_list_apps":
        return json.dumps([_compact_app(a) for a in snap["apps"]], indent=1), False
    if name == "asc_review_radar":
        out = {}
        for a in snap["apps"]:
            for p, v in latest_by_platform(a).items():
                out.setdefault(v["bucket"], []).append({"app": a["name"], "id": a["id"], "platform": p,
                                                        "version": v["version"], "state": v["state"]})
        return json.dumps(out, indent=1), False
    if name == "asc_revenue_leaks":
        sales = _sales(creds, snap, days) if pro else None
        leaks = find_leaks(snap, sales, ignore=load_ignore())
        res = {"summary": summarize(leaks, snap), "leaks": leaks if pro else leaks[:1]}
        if not pro and len(leaks) > 1:
            res["hidden_leaks"] = len(leaks) - 1
            res["upgrade"] = (f"{len(leaks) - 1} more leaks and all fix playbooks are in {PRODUCT} Pro ($49 one-time). "
                              f"Checkout: {checkout_url('pro')}")
        return json.dumps(res, indent=1), False
    if name == "asc_sales_summary":
        sales = _sales(creds, snap, days)
        if sales is None:
            return "Vendor number not configured. Ask the user to run `ascpilot setup`.", True
        return json.dumps(sales, indent=1), False
    if name == "asc_cashflow_report":
        sales = _sales(creds, snap, days)
        leaks = find_leaks(snap, sales, ignore=load_ignore())
        return markdown_report(snap, leaks, summarize(leaks, snap), sales), False
    raise KeyError(name)


def handle(msg: dict):
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if mid is None:  # notification
        return None
    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ascpilot", "title": PRODUCT, "version": __version__},
                "instructions": "Use asc_revenue_leaks first: it finds money the user's App Store account is failing "
                                "to collect. Numbers come straight from App Store Connect; never invent revenue.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{k: v for k, v in t.items() if k != "pro"} for t in TOOLS]}
        elif method == "tools/call":
            try:
                text, is_err = call_tool(params.get("name"), params.get("arguments") or {})
            except KeyError:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {params.get('name')}"}}
            except Exception as e:  # surface as a tool error the agent can read
                text, is_err = f"{type(e).__name__}: {e}", True
            result = {"content": [{"type": "text", "text": text}], "isError": is_err}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            stdout.flush()
            continue
        batch = msg if isinstance(msg, list) else [msg]
        replies = [r for r in (handle(m) for m in batch if isinstance(m, dict)) if r is not None]
        if replies:
            stdout.write(json.dumps(replies if isinstance(msg, list) else replies[0]) + "\n")
            stdout.flush()
