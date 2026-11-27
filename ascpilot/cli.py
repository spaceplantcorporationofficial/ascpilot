"""`ascpilot` command line."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from ascpilot import BUY_URL, PRODUCT, REPO_URL, __version__, checkout_url, pricing
from ascpilot import license as lic
from ascpilot.asc import ASCError, Client
from ascpilot.config import load_ignore, save_ignore, CONFIG_FILE, load_credentials, save_config
from ascpilot.leaks import find_leaks, summarize
from ascpilot.report import bold, c, dim, markdown_report, render_apps, render_leaks, render_money, render_radar
from ascpilot.sales import fetch_sales
from ascpilot.scan import load_or_scan


class UserError(Exception):
    pass


def _creds():
    creds = load_credentials()
    if not creds:
        raise UserError(
            "No App Store Connect API key found.\n"
            "  Run `ascpilot setup`, or export ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_FILEPATH.\n"
            "  Create a key at https://appstoreconnect.apple.com/access/integrations/api "
            "(role: Sales or Admin for revenue data; App Manager works for everything else).")
    if not Path(creds.key_path).exists():
        raise UserError(f"API key file not found: {creds.key_path}")
    return creds


def _snapshot(args):
    creds = _creds()
    return load_or_scan(lambda: Client(creds), refresh=getattr(args, "refresh", False),
                        app_ids=getattr(args, "app", None), quiet=getattr(args, "json", False))


def _require_pro(feature: str):
    if not lic.is_pro():
        raise UserError(f"{feature} is part of {PRODUCT} Pro ($49 one-time).\n"
                        f"  Buy (instant key): {checkout_url('pro')}\n"
                        f"  Or run: ascpilot upgrade --open")


def _sales_for(snapshot, days):
    creds = _creds()
    if not creds.vendor_number:
        raise UserError("Sales data needs your vendor number (App Store Connect → Payments and Financial Reports, top-left).\n"
                        "  Run `ascpilot setup` again or export ASC_VENDOR_NUMBER.")
    sku_to_app = {a.get("sku"): a["id"] for a in snapshot["apps"] if a.get("sku")}
    return fetch_sales(Client(creds), creds.vendor_number, days, sku_to_app)


def _out(args, obj, text):
    if getattr(args, "json", False):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(text)


# ------------------------------------------------------------------ commands
def cmd_setup(args):
    print(bold(f"{PRODUCT} setup") + dim("  (stored locally at " + str(CONFIG_FILE) + ", chmod 600)"))
    existing = load_credentials()
    if existing:
        print(dim(f"  Found existing credentials via {existing.source}."))
    key_id = input(f"  API Key ID{f' [{existing.key_id}]' if existing else ''}: ").strip() or (existing.key_id if existing else "")
    issuer = input(f"  Issuer ID{f' [{existing.issuer_id}]' if existing else ''}: ").strip() or (existing.issuer_id if existing else "")
    key_path = input(f"  Path to AuthKey_XXXX.p8{f' [{existing.key_path}]' if existing else ''}: ").strip() or (existing.key_path if existing else "")
    vendor = input(f"  Vendor number (optional, for Sales){f' [{existing.vendor_number}]' if existing and existing.vendor_number else ''}: ").strip() \
        or (existing.vendor_number if existing else None)
    key_path = os.path.expanduser(key_path)
    if not (key_id and issuer and key_path):
        raise UserError("Key ID, Issuer ID and key path are all required.")
    path = save_config(key_id, issuer, key_path, vendor)
    print(c(f"  Saved {path}", "32"))
    return cmd_doctor(args)


def cmd_doctor(args):
    creds = _creds()
    print(bold("Doctor"))
    print(f"  credentials   {creds.source}")
    print(f"  key file      {'ok' if Path(creds.key_path).exists() else 'MISSING'}  {dim(creds.key_path)}")
    client = Client(creds)
    try:
        client.token()
        print("  jwt signing   ok")
        resp = client.get("/v1/apps", {"limit": 1, "fields[apps]": "name"})
        total = (resp.get("meta") or {}).get("paging", {}).get("total")
        print(c(f"  api access    ok  ({total if total is not None else '?'} apps visible)", "32"))
    except ASCError as e:
        print(c(f"  api access    FAILED  {e}", "31"))
        return 1
    print(f"  vendor number {'set' if creds.vendor_number else dim('not set (Sales/Money disabled)')}")
    payload = lic.current()
    print(f"  license       {c('Pro · ' + payload.get('email', ''), '32') if payload else 'Free  ' + dim(BUY_URL)}")
    return 0


def cmd_apps(args):
    snap = _snapshot(args)
    _out(args, snap["apps"], render_apps(snap))


def cmd_radar(args):
    snap = _snapshot(args)
    _out(args, snap["apps"], render_radar(snap))


def cmd_leaks(args):
    snap = _snapshot(args)
    pro = lic.is_pro()
    sales = None
    if pro and args.with_sales and _creds().vendor_number:
        try:
            sales = _sales_for(snap, args.days)
        except (ASCError, UserError) as e:
            print(dim(f"  (sales skipped: {e})"), file=sys.stderr)
    leaks = find_leaks(snap, sales, ignore=load_ignore())
    summary = summarize(leaks, snap)
    if args.json:
        print(json.dumps({"summary": summary, "leaks": leaks if pro else leaks[:1], "pro": pro,
                          "hidden": 0 if pro else max(0, len(leaks) - 1)}, indent=2))
    else:
        print(render_leaks(leaks, summary, pro))
    return 2 if args.fail_on_leak and any(l["severity"] in ("critical", "high") for l in leaks) else 0


def cmd_money(args):
    _require_pro("`ascpilot money` (Apple Sales API proceeds)")
    snap = _snapshot(args)
    sales = _sales_for(snap, args.days)
    names = {a["id"]: a["name"] for a in snap["apps"]}
    _out(args, sales, render_money(sales, names))


def cmd_report(args):
    _require_pro("`ascpilot report` (Cash-Flow report)")
    snap = _snapshot(args)
    sales = None
    if _creds().vendor_number:
        try:
            sales = _sales_for(snap, args.days)
        except ASCError as e:
            print(dim(f"  (sales skipped: {e})"), file=sys.stderr)
    leaks = find_leaks(snap, sales, ignore=load_ignore())
    md = markdown_report(snap, leaks, summarize(leaks, snap), sales)
    if args.out:
        Path(args.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).expanduser().write_text(md)
        print(c(f"Wrote {args.out}", "32"))
    else:
        print(md)


def cmd_license(args):
    if args.action == "claim":
        from ascpilot import claim as claimlib
        import time as _t
        sid = (args.key or input("Checkout session id (cs_...): ")).strip()
        if not sid.startswith("cs_"):
            raise UserError("That doesn't look like a Stripe Checkout session id (it starts with cs_). It's in the thank-you page URL.")
        deadline = _t.time() + (args.wait if args.wait else 0)
        while True:
            key = claimlib.fetch(sid)
            if key or _t.time() >= deadline:
                break
            print(dim("  license not published yet, retrying in 10s…"), file=sys.stderr)
            _t.sleep(10)
        if not key:
            raise UserError("No license published for that session yet. Licenses appear 1–3 minutes after payment;\n"
                            "  retry with `ascpilot license claim <id> --wait 600`.")
        payload = lic.activate(key)
        if not payload:
            raise UserError("Downloaded key failed verification. Open a ticket: " + REPO_URL + "/issues")
        print(c(f"✓ {PRODUCT} {payload.get('plan', 'pro').title()} activated for {payload.get('email') or 'you'}", "32"))
        return
    if args.action == "activate":
        key = args.key or getpass.getpass("License key: ")
        payload = lic.activate(key)
        if not payload:
            raise UserError("That license key is not valid. Copy it exactly from your receipt email.")
        print(c(f"✓ {PRODUCT} {payload.get('plan', 'pro').title()} activated for {payload.get('email')}", "32"))
    else:
        payload = lic.current()
        if payload:
            print(f"{payload.get('plan', 'pro').title()} · {payload.get('email')} · issued {payload.get('issued')}")
        else:
            print(f"Free plan. Upgrade → {BUY_URL}")


def cmd_ignore(args):
    ids = load_ignore()
    if args.ids:
        ids = (ids - set(args.ids)) if args.remove else (ids | set(args.ids))
        save_ignore(ids)
    print("Ignored apps: " + (", ".join(sorted(ids)) if ids else "none"))


def cmd_upgrade(args):
    info = pricing()
    url = checkout_url(args.plan)
    plans = {p.get("id"): p for p in info.get("plans") or []}
    p = plans.get(args.plan) or {}
    if args.json:
        print(json.dumps({"plan": args.plan, "price_usd": p.get("price_usd"), "checkout_url": url,
                          "after_payment": "Key appears on the thank-you page; or run `ascpilot license claim <cs_...>`"}, indent=2))
        return
    print(bold(f"{PRODUCT} {args.plan.title()}") + f" — ${p.get('price_usd', 49 if args.plan == 'pro' else 149)} one-time")
    for f in p.get("features") or []:
        print(f"  ✓ {f}")
    print(f"\n  Checkout: {c(url, '36')}")
    print(dim("  Your license key appears on the thank-you page right after payment (or: ascpilot license claim <cs_...>)."))
    if args.open:
        import subprocess as _sp
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        _sp.run([opener, url], check=False)


def cmd_mcp(args):
    from ascpilot.mcp import serve
    serve()


def cmd_mcp_config(args):
    exe = sys.argv[0] if os.path.isabs(sys.argv[0]) else "ascpilot"
    cfg = {"mcpServers": {"ascpilot": {"command": exe if exe.endswith("ascpilot") else "ascpilot", "args": ["mcp"]}}}
    print(json.dumps(cfg, indent=2))
    print(dim("\nClaude Code:  claude mcp add ascpilot -- ascpilot mcp\n"
              "Codex:        codex mcp add ascpilot -- ascpilot mcp\n"
              "Cursor / Antigravity / others: paste the JSON above into your MCP config."), file=sys.stderr)


def build_parser():
    p = argparse.ArgumentParser(prog="ascpilot", description=f"{PRODUCT} — find the money your App Store account is leaking.")
    p.add_argument("--version", action="version", version=f"ascpilot {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def common(sp, scan=True):
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        if scan:
            sp.add_argument("--refresh", action="store_true", help="ignore the 15-minute scan cache")
            sp.add_argument("--app", action="append", help="limit to an Apple ID or bundle ID (repeatable)")

    sub.add_parser("setup", help="save your App Store Connect API key").set_defaults(func=cmd_setup)
    sub.add_parser("doctor", help="check credentials, API access and license").set_defaults(func=cmd_doctor)
    sp = sub.add_parser("apps", help="every app, status, latest versions, IAP health")
    common(sp)
    sp.set_defaults(func=cmd_apps)
    sp = sub.add_parser("radar", help="review radar: approved/unreleased, rejected, in review, drafts")
    common(sp)
    sp.set_defaults(func=cmd_radar)
    sp = sub.add_parser("leaks", help="Revenue Leak Scan (free: summary + top leak; Pro: everything + fixes)")
    common(sp)
    sp.add_argument("--days", type=int, default=14, help="sales window for the paywall check (Pro)")
    sp.add_argument("--no-sales", dest="with_sales", action="store_false", help="skip the Sales-API paywall check")
    sp.add_argument("--fail-on-leak", action="store_true", help="exit 2 when critical/high leaks exist (CI)")
    sp.set_defaults(func=cmd_leaks)
    sp = sub.add_parser("money", help="[Pro] installs, payers and proceeds from Apple's Sales API")
    common(sp)
    sp.add_argument("--days", type=int, default=14)
    sp.set_defaults(func=cmd_money)
    sp = sub.add_parser("report", help="[Pro] full Markdown Cash-Flow report")
    common(sp)
    sp.add_argument("--days", type=int, default=14)
    sp.add_argument("--out", help="write to a file instead of stdout")
    sp.set_defaults(func=cmd_report)
    sp = sub.add_parser("license", help="show or activate your license")
    sp.add_argument("action", nargs="?", choices=["show", "activate", "claim"], default="show")
    sp.add_argument("key", nargs="?", help="license key (activate) or Stripe checkout session id cs_... (claim)")
    sp.add_argument("--wait", type=int, default=0, help="claim: keep retrying for N seconds")
    sp.set_defaults(func=cmd_license)
    sp = sub.add_parser("upgrade", help="get Pro/Studio: prints (or opens) the checkout link")
    sp.add_argument("--plan", choices=["pro", "studio"], default="pro")
    sp.add_argument("--open", action="store_true", help="open checkout in your browser")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_upgrade)
    sp = sub.add_parser("ignore", help="hide retired apps from the leak scan (Apple ID or bundle ID)")
    sp.add_argument("ids", nargs="*")
    sp.add_argument("--remove", action="store_true")
    sp.set_defaults(func=cmd_ignore)
    sub.add_parser("mcp", help="run the MCP server (stdio) for AI agents").set_defaults(func=cmd_mcp)
    sub.add_parser("mcp-config", help="print MCP config for Claude Code, Codex, Cursor, Antigravity").set_defaults(func=cmd_mcp_config)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        print(dim(f"\nStart with:  ascpilot setup   then   ascpilot leaks\n{REPO_URL}"))
        return 0
    try:
        rc = args.func(args)
        return int(rc or 0)
    except UserError as e:
        print(c("✗ ", "31") + str(e), file=sys.stderr)
        return 1
    except ASCError as e:
        print(c("✗ App Store Connect: ", "31") + str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
