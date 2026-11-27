"""Offline Pro licensing with Ed25519 signatures (pure Python, RFC 8032 reference arithmetic).

A license key is `ASCP1.<base64url(json payload)>.<base64url(signature)>`. Verification needs only the
public key below, so ASC Pilot never phones home.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from ascpilot.config import CONFIG_DIR

# Public verification key for licenses issued by SpacePlant Corporation.
PUBLIC_KEY_HEX = "9abf33577372c44d35030844c302282fe449d575a2ec9c97aacaeb533806d9f4"

LICENSE_FILE = CONFIG_DIR / "license.key"
PREFIX = "ASCP1"

# ---------------------------------------------------------------- Ed25519 ---
_p = 2 ** 255 - 19
_d = -121665 * pow(121666, _p - 2, _p) % _p
_q = 2 ** 252 + 27742317777372353535851937790883648493
_SQRT_M1 = pow(2, (_p - 1) // 4, _p)


def _sha512_modq(s: bytes) -> int:
    return int.from_bytes(hashlib.sha512(s).digest(), "little") % _q


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C = 2 * P[3] * Q[3] * _d % _p
    D = 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _p, G * H % _p, F * G % _p, E * H % _p)


def _mul(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q) -> bool:
    return (P[0] * Q[2] - Q[0] * P[2]) % _p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _p == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _p:
        return None
    x2 = (y * y - 1) * pow(_d * y * y + 1, _p - 2, _p)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_p + 3) // 8, _p)
    if (x * x - x2) % _p != 0:
        x = x * _SQRT_M1 % _p
    if (x * x - x2) % _p != 0:
        return None
    if (x & 1) != sign:
        x = _p - x
    return x


_gy = 4 * pow(5, _p - 2, _p) % _p
_gx = _recover_x(_gy, 0)
_G = (_gx, _gy, 1, _gx * _gy % _p)


def _compress(P) -> bytes:
    zinv = pow(P[2], _p - 2, _p)
    x, y = P[0] * zinv % _p, P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _p)


def _expand(secret: bytes):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_from_secret(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = _sha512_modq(prefix + msg)
    Rs = _compress(_mul(r, _G))
    h = _sha512_modq(Rs + A + msg)
    s = (r + h * a) % _q
    return Rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _q:
        return False
    h = _sha512_modq(signature[:32] + public + msg)
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))


# ---------------------------------------------------------------- Keys ------
def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(secret: bytes, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{PREFIX}.{_b64u(body)}.{_b64u(sign(secret, body))}"


def parse(key: str, public_hex: Optional[str] = None) -> Optional[dict]:
    """Return the license payload if the key is authentic, else None."""
    try:
        prefix, body_b64, sig_b64 = key.strip().split(".")
        if prefix != PREFIX:
            return None
        body, sig = _b64u_dec(body_b64), _b64u_dec(sig_b64)
        pub = bytes.fromhex(public_hex or PUBLIC_KEY_HEX)
    except (ValueError, TypeError):
        return None
    if not verify(pub, body, sig):
        return None
    try:
        payload = json.loads(body.decode())
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def current() -> Optional[dict]:
    key = os.environ.get("ASCPILOT_LICENSE")
    if not key and LICENSE_FILE.exists():
        try:
            key = LICENSE_FILE.read_text().strip()
        except OSError:
            key = None
    return parse(key) if key else None


def is_pro() -> bool:
    lic = current()
    return bool(lic and lic.get("plan") in ("pro", "studio"))


def activate(key: str) -> Optional[dict]:
    payload = parse(key)
    if not payload:
        return None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(key.strip() + "\n")
    os.chmod(LICENSE_FILE, 0o600)
    return payload
