#!/usr/bin/env python3
"""Build the one-click MCP Bundle (.mcpb) and the MCP Registry server.json for the current version.

  python3 tools/build_mcpb.py            -> dist/ascpilot-mcp-<ver>.mcpb + server.json (sha filled)
"""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "ascpilot/__init__.py").read_text()).group(1)
NAME = "io.github.spaceplantcorporationofficial/ascpilot"
REPO = "https://github.com/spaceplantcorporationofficial/ascpilot"
DIST = ROOT / "dist"
BUNDLE = DIST / f"ascpilot-mcp-{VERSION}.mcpb"

MANIFEST = {
    "manifest_version": "0.3",
    "name": "ascpilot",
    "display_name": "ASC Pilot — App Store revenue leaks",
    "version": VERSION,
    "description": "Find the money your App Store Connect account is leaking: unreleased approvals, apps off sale, IAPs customers can't buy.",
    "long_description": "Read-only App Store Connect scanner. Lists apps, review radar, and runs a Revenue Leak Scan "
                        "(approved-but-unreleased builds, apps in 0 storefronts, frozen in-app purchases, rejections "
                        "blocking paywalls). Pro adds Sales-API money and a Cash-Flow report. Your key stays local.",
    "author": {"name": "SpacePlant Corporation", "url": "https://spaceplantcorporationofficial.github.io/"},
    "homepage": "https://spaceplantcorporationofficial.github.io/ascpilot/",
    "repository": {"type": "git", "url": REPO},
    "support": REPO + "/issues",
    "license": "MIT (core) + commercial Pro",
    "keywords": ["app-store-connect", "ios", "revenue", "in-app-purchase", "indie"],
    "server": {
        "type": "python",
        "entry_point": "server/main.py",
        "mcp_config": {
            "command": "python3",
            "args": ["${__dirname}/server/main.py", "mcp"],
            "env": {
                "PYTHONPATH": "${__dirname}/server/lib",
                "ASC_KEY_ID": "${user_config.asc_key_id}",
                "ASC_ISSUER_ID": "${user_config.asc_issuer_id}",
                "ASC_KEY_FILEPATH": "${user_config.asc_key_file}",
                "ASC_VENDOR_NUMBER": "${user_config.asc_vendor_number}",
                "ASCPILOT_LICENSE": "${user_config.license_key}",
            },
        },
    },
    "tools": [
        {"name": "asc_list_apps", "description": "Every app with status, latest versions and IAP health"},
        {"name": "asc_review_radar", "description": "Approved-unreleased, rejected, in review, drafts, live"},
        {"name": "asc_revenue_leaks", "description": "Revenue Leak Scan"},
        {"name": "asc_upgrade", "description": "Pricing and checkout links"},
        {"name": "asc_sales_summary", "description": "[Pro] Apple Sales API money view"},
        {"name": "asc_cashflow_report", "description": "[Pro] Markdown Cash-Flow report"},
    ],
    "user_config": {
        "asc_key_id": {"type": "string", "title": "App Store Connect API Key ID", "required": False,
                       "description": "Leave empty if you already ran `ascpilot setup` or have ~/.appstoreconnect/asc_env.sh"},
        "asc_issuer_id": {"type": "string", "title": "Issuer ID", "required": False},
        "asc_key_file": {"type": "file", "title": "AuthKey_XXXX.p8 file", "required": False},
        "asc_vendor_number": {"type": "string", "title": "Vendor number (for Sales data)", "required": False},
        "license_key": {"type": "string", "title": "ASC Pilot Pro license key", "required": False, "sensitive": True},
    },
    "compatibility": {"platforms": ["darwin", "linux"], "runtimes": {"python": ">=3.9.0"}},
}

MAIN = '''import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
for k in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_FILEPATH", "ASC_VENDOR_NUMBER", "ASCPILOT_LICENSE"):
    if os.environ.get(k, "").startswith("${"):  # unfilled optional user_config
        os.environ.pop(k, None)
from ascpilot.cli import main
sys.exit(main(sys.argv[1:] or ["mcp"]))
'''


def build():
    DIST.mkdir(exist_ok=True)
    fixed = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(BUNDLE, "w", zipfile.ZIP_DEFLATED) as z:
        def add(arc, data):
            zi = zipfile.ZipInfo(arc, fixed)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
        add("manifest.json", json.dumps(MANIFEST, indent=2))
        add("server/main.py", MAIN)
        for f in sorted((ROOT / "ascpilot").glob("*.py")):
            add(f"server/lib/ascpilot/{f.name}", f.read_text())
        add("LICENSE", (ROOT / "LICENSE").read_text())
        add("LICENSE-PRO", (ROOT / "LICENSE-PRO").read_text())
    sha = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    server = {
        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-10-17/server.schema.json",
        "name": NAME,
        "title": "ASC Pilot",
        "description": "Find the money your App Store Connect account is leaking (unreleased builds, frozen IAPs).",
        "version": VERSION,
        "websiteUrl": "https://spaceplantcorporationofficial.github.io/ascpilot/",
        "repository": {"url": REPO, "source": "github"},
        "packages": [{
            "registryType": "mcpb",
            "identifier": f"{REPO}/releases/download/v{VERSION}/{BUNDLE.name}",
            "version": VERSION,
            "fileSha256": sha,
            "transport": {"type": "stdio"},
        }],
    }
    assert len(server["description"]) <= 100, len(server["description"])
    (ROOT / "server.json").write_text(json.dumps(server, indent=2) + "\n")
    print(BUNDLE, sha)


if __name__ == "__main__":
    build()
