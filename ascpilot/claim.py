"""Server-less license delivery.

After a Stripe checkout, the fulfillment worker issues a license key, encrypts it with a key derived from the
buyer's private Checkout Session ID, and publishes the ciphertext at a path derived from a one-way hash of that
same ID. Only the buyer (who was redirected with the session ID) can locate and decrypt their key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Optional

from ascpilot import SITE_URL, __version__

CLAIMS_URL = SITE_URL + "claims/"


def claim_id(session_id: str) -> str:
    return hashlib.sha256(b"ascpilot-claim-id:" + session_id.encode()).hexdigest()[:40]


def _keystream(session_id: str, n: int) -> bytes:
    out, counter = b"", 0
    while len(out) < n:
        out += hashlib.sha256(b"ascpilot-claim-key:" + session_id.encode() + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:n]


def _mac_key(session_id: str) -> bytes:
    return hashlib.sha256(b"ascpilot-claim-mac:" + session_id.encode()).digest()


def seal(session_id: str, license_key: str, plan: str, created: str) -> dict:
    pt = license_key.encode()
    ct = bytes(a ^ b for a, b in zip(pt, _keystream(session_id, len(pt))))
    return {"v": 1, "plan": plan, "created": created, "ct": base64.b64encode(ct).decode(),
            "mac": hmac.new(_mac_key(session_id), ct, hashlib.sha256).hexdigest()}


def open_sealed(session_id: str, blob: dict) -> Optional[str]:
    try:
        ct = base64.b64decode(blob["ct"])
    except (KeyError, ValueError):
        return None
    if not hmac.compare_digest(hmac.new(_mac_key(session_id), ct, hashlib.sha256).hexdigest(), blob.get("mac", "")):
        return None
    return bytes(a ^ b for a, b in zip(ct, _keystream(session_id, len(ct)))).decode()


def fetch(session_id: str) -> Optional[str]:
    """Download and decrypt the license for a Checkout Session ID. None if not published yet."""
    url = f"{CLAIMS_URL}{claim_id(session_id.strip())}.json"
    req = urllib.request.Request(url, headers={"User-Agent": f"ascpilot/{__version__}", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            blob = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return open_sealed(session_id.strip(), blob)
