# ASC Pilot

<!-- mcp-name: io.github.spaceplantcorporationofficial/ascpilot -->

**Find the money your App Store account is leaking.** From your terminal, or from your AI coding agent via MCP.

```
$ ascpilot leaks

Revenue Leak Scan · 131 apps scanned

  2 critical   18 high   31 medium   71 low
  38 apps leaking · 72 in-app products customers cannot buy

CRIT  SpacePlant SI — Live build available in 0 territories
      The app is approved but switched off in all 175 storefronts, and you shipped a version 31 days ago.

  🔒 121 more leak(s) hidden. ASC Pilot Pro shows every leak, the exact fix playbook, …
```
<sub>Real, unedited output from our own App Store Connect account (131 apps, 95 seconds, 493 read-only API calls).</sub>

We built ASC Pilot to run our own 130-app studio. The first time we pointed it at our account it found
**in-app purchases customers literally could not buy** — stuck in *Missing Metadata* or *Ready to Submit*,
never attached to a version — on apps that were live and getting installs. App Store Connect never warns you about this.

## What it finds

| Leak | Why it costs you money |
|---|---|
| **Approved but not released** | Apple approved the build; it's waiting on you to press a button. |
| **Approved but off sale** | Live build, available in 0 storefronts. |
| **Frozen checkout** | IAPs/subscriptions in *Missing Metadata*, *Ready to Submit* (never submitted) or *Developer Action Needed*. Your paywall can't sell them. |
| **Rejection holding products hostage** | A rejected version is blocking the products riding on it. |
| **Live, free, nothing to buy** | Every download earns $0. |
| **Stalled drafts** | Versions rotting in *Prepare for Submission* for 3+ weeks. |
| **Installs, zero payers** *(Pro, Sales API)* | Traffic is there; the paywall isn't converting. |

Every number comes straight from Apple's App Store Connect API. ASC Pilot never estimates revenue or converts currencies.

## Install

```bash
pipx install git+https://github.com/spaceplantcorporationofficial/ascpilot
# or: pip3 install --user git+https://github.com/spaceplantcorporationofficial/ascpilot
ascpilot setup      # paste your API Key ID, Issuer ID and .p8 path (stored locally, chmod 600)
ascpilot leaks
```

Zero dependencies (Python 3.9+ standard library + the `openssl` that ships with macOS). Your key never leaves your
machine; ASC Pilot talks only to `api.appstoreconnect.apple.com`. It also picks up `ASC_KEY_ID` / `ASC_ISSUER_ID` /
`ASC_KEY_FILEPATH` (fastlane-style) from your environment.

Create a key at **App Store Connect → Users and Access → Integrations → App Store Connect API**.
*App Manager* role covers everything except Sales; use *Sales* or *Admin* for `money`.

## Commands

| Command | Free | Pro |
|---|:---:|:---:|
| `ascpilot apps` — every app, status, latest versions, IAP health | ✅ | ✅ |
| `ascpilot radar` — approved/unreleased, rejected, in review, drafts | ✅ | ✅ |
| `ascpilot leaks` — Revenue Leak Scan | summary + worst leak | **every leak + fix playbook** |
| `ascpilot money` — installs, payers, proceeds from Apple's Sales API | | ✅ |
| `ascpilot report --out cashflow.md` — full Markdown Cash-Flow report | | ✅ |
| `ascpilot leaks --fail-on-leak` — CI gate (exit 2 on critical/high) | ✅ | ✅ |
| MCP server for Claude Code, Codex, Cursor, Antigravity | 3 tools | **5 tools, full detail** |

All commands accept `--json`.

## Use it from your AI agent (MCP)

```bash
uvx --from git+https://github.com/spaceplantcorporationofficial/ascpilot ascpilot mcp   # zero-install run
claude mcp add ascpilot -- ascpilot mcp          # Claude Code
codex mcp add ascpilot -- ascpilot mcp           # Codex
ascpilot mcp-config                              # JSON for Cursor / Antigravity / anything else
```

Then ask: *"Which of my apps are leaking revenue, and fix the metadata on the worst one."*

Tools: `asc_list_apps`, `asc_review_radar`, `asc_revenue_leaks`, `asc_upgrade`, `asc_sales_summary` (Pro), `asc_cashflow_report` (Pro).

- **Claude Desktop:** download `ascpilot-mcp-<version>.mcpb` from [Releases](https://github.com/spaceplantcorporationofficial/ascpilot/releases/latest) and double-click.
- **Official MCP Registry:** `io.github.spaceplantcorporationofficial/ascpilot`
- **For agents:** [llms.txt](https://spaceplantcorporationofficial.github.io/ascpilot/llms.txt) · machine-readable plans + checkout links: [pricing.json](https://spaceplantcorporationofficial.github.io/ascpilot/pricing.json)

## Pro

**$49 one-time, founding price** — every leak with step-by-step fixes, Sales-API money view, Cash-Flow report,
full MCP toolset, all 1.x updates. Licenses are verified offline (Ed25519) — ASC Pilot never phones home.

→ **[Get ASC Pilot Pro](https://spaceplantcorporationofficial.github.io/ascpilot/#pricing)**

```bash
ascpilot upgrade --open                 # opens Stripe checkout
ascpilot license activate ASCP1.xxxxx   # key is shown on the thank-you page right after payment
ascpilot license claim cs_live_...      # or fetch it later with your checkout session id
```

## License

Core: MIT (see `LICENSE`). Pro features (full leak detail, `money`, `report`, Pro MCP tools) are commercial
software under `LICENSE-PRO`; using them requires a license key.

Built by [SpacePlant Corporation](https://spaceplantcorporationofficial.github.io/).
