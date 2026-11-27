"""Rendering: terminal tables and the Markdown Cash-Flow report."""

from __future__ import annotations

import os
import sys
from typing import Optional

from ascpilot import BUY_URL, PRODUCT, __version__
from ascpilot.scan import app_status, latest_by_platform, sku_counts

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def bold(t):
    return c(t, "1")


def dim(t):
    return c(t, "2")


SEV_STYLE = {"critical": ("CRIT", "1;41;97"), "high": ("HIGH", "1;31"), "medium": ("MED ", "33"), "low": ("LOW ", "2")}
STATUS_STYLE = {
    "live": ("● live", "32"), "off_sale": ("● OFF SALE", "1;31"), "approved_unreleased": ("● approved, unreleased", "1;33"),
    "in_review": ("● in review", "36"), "rejected": ("● rejected", "31"), "self_pulled": ("● pulled", "33"),
    "draft": ("○ draft", "2"), "removed": ("○ removed", "2"), "empty": ("○ empty", "2"),
}


def table(headers: list, rows: list) -> str:
    def plain(s):
        import re
        return re.sub(r"\033\[[0-9;]*m", "", str(s))
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = min(max(widths[i], len(plain(cell))), 48)

    def fmt(r):
        out = []
        for i, cell in enumerate(r):
            s = str(cell)
            p = plain(s)
            if len(p) > widths[i]:
                s = p[:widths[i] - 1] + "…"
                p = s
            out.append(s + " " * (widths[i] - len(p)))
        return "  ".join(out).rstrip()
    lines = [bold(fmt(headers)), dim("  ".join("─" * w for w in widths))]
    lines += [fmt(r) for r in rows]
    return "\n".join(lines)


def money_str(proceeds: dict) -> str:
    if not proceeds:
        return "0"
    return ", ".join(f"{v:,.2f} {k}" for k, v in sorted(proceeds.items(), key=lambda kv: -abs(kv[1])))


def render_apps(snapshot: dict) -> str:
    rows = []
    for a in snapshot["apps"]:
        st, code = STATUS_STYLE.get(app_status(a), ("?", "0"))
        latest = latest_by_platform(a)
        vers = ", ".join(f"{p.replace('MAC_OS', 'mac').replace('IOS', 'iOS').replace('VISION_OS', 'vision')} {v['version']}"
                         for p, v in latest.items())
        k = sku_counts(a)
        rows.append([a["name"], c(st, code), vers or "—", f"{k['approved']}/{k['total']}", a["id"]])
    head = f"{bold(PRODUCT)} · {len(snapshot['apps'])} apps"
    return head + "\n\n" + table(["App", "Status", "Latest", "IAP ok/all", "Apple ID"], rows)


def render_radar(snapshot: dict) -> str:
    groups = {}
    for a in snapshot["apps"]:
        for plat, v in latest_by_platform(a).items():
            groups.setdefault(v["bucket"], []).append((a, plat, v))
    order = [("approved_unreleased", "Approved — waiting for YOU to release"), ("rejected", "Rejected"),
             ("in_review", "In review"), ("self_pulled", "Pulled by developer"), ("draft", "Drafts"),
             ("live", "Live")]
    out = [f"{bold('Review Radar')} · {snapshot['scanned_at']}"]
    for key, label in order:
        items = groups.get(key) or []
        if not items:
            continue
        st, code = STATUS_STYLE.get(key, ("", "0"))
        out.append("")
        out.append(c(f"{label} ({len(items)})", code))
        for a, plat, v in sorted(items, key=lambda t: t[0]["name"].lower()):
            out.append(f"  {a['name']:<40.40} {plat:<9} {v['version'] or '':<10} {dim(v['state'])}")
    return "\n".join(out)


def render_leaks(leaks: list, summary: dict, pro: bool) -> str:
    s = summary
    sev = s["by_severity"]
    out = [
        f"{bold('Revenue Leak Scan')} · {s['apps_scanned']} apps scanned",
        "",
        f"  {c(str(sev['critical']), '1;41;97')} critical   {c(str(sev['high']), '1;31')} high   "
        f"{c(str(sev['medium']), '33')} medium   {dim(str(sev['low']) + ' low')}",
        f"  {bold(str(s['apps_with_leaks']))} apps leaking · {bold(str(s['frozen_products']))} in-app products customers cannot buy",
        "",
    ]
    if not leaks:
        out.append(c("  No leaks found. Your checkout is clean. 🎉", "32"))
        return "\n".join(out)
    shown = leaks if pro else leaks[:1]
    for l in shown:
        tag, code = SEV_STYLE[l["severity"]]
        out.append(f"{c(tag, code)}  {bold(l['app'])} — {l['title']}")
        out.append(f"      {dim(l['detail'])}")
        for it in l["items"][:8]:
            out.append(f"        · {it}")
        if len(l["items"]) > 8:
            out.append(f"        · …and {len(l['items']) - 8} more")
        if pro:
            for i, step in enumerate(l["fix"], 1):
                out.append(f"      {c(str(i) + '.', '36')} {step}")
        out.append("")
    if not pro and len(leaks) > 1:
        out.append(c(f"  🔒 {len(leaks) - 1} more leak(s) hidden.", "1;33")
                   + f" ASC Pilot Pro shows every leak, the exact fix playbook,")
        out.append(f"     your real Sales-API proceeds, the Cash-Flow report and the full MCP toolset → {BUY_URL}")
    return "\n".join(out)


def render_money(sales: dict, names: dict) -> str:
    t = sales["totals"]
    out = [
        f"{bold('Money')} · last {sales['days']} days ending {sales['window_end']} (UTC) · "
        f"{sales['days_with_reports']}/{sales['days']} daily reports available",
        "",
        f"  New installs {bold(str(t['new_installs']))}   Updates {t['updates']}   Re-downloads {t['redownloads']}   "
        f"IAP units {bold(str(t['iap_units']))}   Paying units {bold(str(t['paying_units']))}",
        f"  Developer proceeds: {bold(money_str(t['proceeds']))}  {dim('(Apple-reported, per currency, no FX)')}",
        "",
    ]
    rows = []
    for app_id, e in sorted(sales["by_app"].items(), key=lambda kv: (-sum(kv[1]["proceeds"].values()), -kv[1]["new_installs"])):
        conv = f"{(e['paying_units'] / e['new_installs'] * 100):.1f}%" if e["new_installs"] else "—"
        rows.append([names.get(app_id) or e.get("title") or app_id, e["new_installs"], e["redownloads"],
                     e["iap_units"], e["paying_units"], conv, money_str(e["proceeds"])])
    if rows:
        out.append(table(["App", "New", "Re-dl", "IAP", "Paying", "Pay/New", "Proceeds"], rows))
    if sales.get("days_without_reports"):
        out.append("")
        out.append(dim(f"  No Apple report for: {', '.join(sales['days_without_reports'][:6])}"
                       + (" …" if len(sales['days_without_reports']) > 6 else "")
                       + " (normal for days with zero activity or not yet published)"))
    return "\n".join(out)


def markdown_report(snapshot: dict, leaks: list, summary: dict, sales: Optional[dict]) -> str:
    names = {a["id"]: a["name"] for a in snapshot["apps"]}
    s = summary
    md = [
        f"# App Store Cash-Flow Report",
        "",
        f"Generated by {PRODUCT} {__version__} · scanned `{snapshot['scanned_at']}` · {s['apps_scanned']} apps · "
        f"{snapshot.get('api_calls', '?')} API calls",
        "",
        "## Headline",
        "",
        f"- **{s['total_leaks']} revenue leaks** across **{s['apps_with_leaks']} apps** "
        f"({s['by_severity']['critical']} critical · {s['by_severity']['high']} high · {s['by_severity']['medium']} medium · {s['by_severity']['low']} low)",
        f"- **{s['frozen_products']} in-app products** exist that customers cannot buy",
    ]
    if sales:
        t = sales["totals"]
        md += [
            f"- Last {sales['days']} days: **{t['new_installs']} new installs**, **{t['paying_units']} paying units**, "
            f"proceeds **{money_str(t['proceeds'])}** (Apple Sales API, {sales['days_with_reports']}/{sales['days']} days reported)",
        ]
    md += ["", "## Fix these first", ""]
    for i, l in enumerate(leaks, 1):
        md.append(f"### {i}. [{l['severity'].upper()}] {l['app']} — {l['title']}")
        md.append("")
        md.append(l["detail"])
        if l["items"]:
            md.append("")
            md += [f"- `{it}`" for it in l["items"][:20]]
        if l["fix"]:
            md.append("")
            md.append("**Fix:**")
            md += [f"{j}. {step}" for j, step in enumerate(l["fix"], 1)]
        md.append("")
    md += ["## Portfolio", "", "| App | Status | Latest versions | IAP approved/total | Territories |", "|---|---|---|---:|---:|"]
    for a in snapshot["apps"]:
        latest = latest_by_platform(a)
        vers = "<br>".join(f"{p} {v['version']} `{v['state']}`" for p, v in latest.items()) or "—"
        k = sku_counts(a)
        terr = f"{a['territories_on']}/{a['territories_total']}" if a.get("territories_total") else "—"
        md.append(f"| **{a['name']}** (`{a['id']}`) | {app_status(a)} | {vers} | {k['approved']}/{k['total']} | {terr} |")
    if sales:
        md += ["", f"## Money (last {sales['days']} days, Apple Sales API)", "",
               "| App | New | Re-dl | Updates | IAP units | Paying | Proceeds |", "|---|---:|---:|---:|---:|---:|---|"]
        for app_id, e in sorted(sales["by_app"].items(), key=lambda kv: -kv[1]["new_installs"]):
            md.append(f"| {names.get(app_id) or e.get('title') or app_id} | {e['new_installs']} | {e['redownloads']} | "
                      f"{e['updates']} | {e['iap_units']} | {e['paying_units']} | {money_str(e['proceeds'])} |")
    md += ["", "---", f"_Numbers come straight from App Store Connect. {PRODUCT} never estimates revenue or converts currencies._", ""]
    return "\n".join(md)
