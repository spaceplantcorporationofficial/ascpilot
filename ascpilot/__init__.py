"""ASC Pilot — find the money your App Store Connect account is leaking.

From your terminal, or from your AI coding agent via MCP.
"""

__version__ = "1.2.0"

PRODUCT = "ASC Pilot"
SITE_URL = "https://spaceplantcorporationofficial.github.io/ascpilot/"
BUY_URL = SITE_URL + "#pricing"
REPO_URL = "https://github.com/spaceplantcorporationofficial/ascpilot"
PRICING_URL = SITE_URL + "pricing.json"
MCP_NAME = "io.github.spaceplantcorporationofficial/ascpilot"


def pricing(timeout: float = 6.0) -> dict:
    """Live plans + checkout links (single source of truth on the website). Falls back to static info offline."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(PRICING_URL, headers={"User-Agent": f"ascpilot/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {"plans": [{"id": "pro", "price_usd": 49, "checkout_url": None},
                          {"id": "studio", "price_usd": 149, "checkout_url": None}], "buy_page": BUY_URL}


def checkout_url(plan: str = "pro") -> str:
    info = pricing()
    for p in info.get("plans") or []:
        if p.get("id") == plan and p.get("checkout_url"):
            return p["checkout_url"]
    return info.get("buy_page") or BUY_URL
