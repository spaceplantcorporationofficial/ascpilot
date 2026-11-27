"""Minimal, dependency-free App Store Connect API client (ES256 JWT via the system `openssl`)."""

from __future__ import annotations

import base64
import gzip
import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from ascpilot import __version__
from ascpilot.config import Credentials

API = "https://api.appstoreconnect.apple.com"
USER_AGENT = f"ascpilot/{__version__}"


class ASCError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def der_to_raw_signature(der: bytes, size: int = 32) -> bytes:
    """Convert an ASN.1 DER ECDSA signature (SEQUENCE{INTEGER r, INTEGER s}) to JOSE raw r||s."""
    if len(der) < 8 or der[0] != 0x30:
        raise ASCError("openssl returned a malformed ECDSA signature")
    idx = 2
    if der[1] & 0x80:
        idx = 2 + (der[1] & 0x7F)

    def read_int(i: int) -> Tuple[bytes, int]:
        if der[i] != 0x02:
            raise ASCError("malformed DER integer in ECDSA signature")
        ln = der[i + 1]
        val = der[i + 2:i + 2 + ln].lstrip(b"\x00")
        if len(val) > size:
            raise ASCError("ECDSA integer too large")
        return val.rjust(size, b"\x00"), i + 2 + ln

    r, idx = read_int(idx)
    s, _ = read_int(idx)
    return r + s


class Client:
    def __init__(self, creds: Credentials, timeout: float = 30.0):
        self.creds = creds
        self.timeout = timeout
        self._token: Optional[str] = None
        self._exp = 0.0
        self._lock = threading.Lock()
        self.calls = 0

    # -- auth -------------------------------------------------------------
    def _sign(self, message: bytes) -> bytes:
        try:
            proc = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", self.creds.key_path],
                input=message, capture_output=True, check=False,
            )
        except FileNotFoundError:
            raise ASCError("`openssl` was not found on PATH (it ships with macOS).")
        if proc.returncode != 0:
            raise ASCError("Could not sign the API token with your .p8 key: "
                           + proc.stderr.decode(errors="replace").strip()[:300])
        return der_to_raw_signature(proc.stdout)

    def token(self, force: bool = False) -> str:
        with self._lock:
            now = time.time()
            if force or not self._token or now > self._exp - 90:
                header = {"alg": "ES256", "kid": self.creds.key_id, "typ": "JWT"}
                exp = int(now) + 1140  # Apple allows up to 20 minutes
                payload = {"iss": self.creds.issuer_id, "iat": int(now) - 20, "exp": exp, "aud": "appstoreconnect-v1"}
                h = b64u(json.dumps(header, separators=(",", ":")).encode())
                p = b64u(json.dumps(payload, separators=(",", ":")).encode())
                sig = self._sign(f"{h}.{p}".encode())
                self._token = f"{h}.{p}.{b64u(sig)}"
                self._exp = exp
            return self._token

    # -- transport --------------------------------------------------------
    def request(self, path: str, params: Optional[dict] = None, accept: str = "application/json",
                retries: int = 4) -> Tuple[int, bytes]:
        url = path if path.startswith("http") else API + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        refreshed = False
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token()}",
                "Accept": accept,
                "User-Agent": USER_AGENT,
            })
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as e:
                body = e.read() if hasattr(e, "read") else b""
                if e.code == 401 and not refreshed:
                    refreshed = True
                    self.token(force=True)
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.5 * (2 ** attempt)
                    time.sleep(min(delay, 30))
                    continue
                return e.code, body
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise ASCError(f"Network error talking to App Store Connect: {e}")
        raise ASCError("App Store Connect request failed after retries")

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        status, body = self.request(path, params)
        if status >= 400:
            detail = ""
            try:
                errs = json.loads(body.decode()).get("errors") or []
                if errs:
                    detail = errs[0].get("detail") or errs[0].get("title") or ""
            except ValueError:
                pass
            raise ASCError(f"HTTP {status} for {path}: {detail}".strip(), status)
        return json.loads(body.decode() or "{}")

    def paginate(self, path: str, params: Optional[dict] = None, max_pages: int = 50) -> Tuple[list, list]:
        data, included = [], []
        resp = self.get(path, params)
        pages = 1
        while True:
            data.extend(resp.get("data") or [])
            included.extend(resp.get("included") or [])
            nxt = (resp.get("links") or {}).get("next")
            if not nxt or pages >= max_pages:
                break
            resp = self.get(nxt)
            pages += 1
        return data, included

    def sales_report(self, vendor: str, report_date: str, frequency: str = "DAILY") -> Optional[str]:
        """Return the TSV text for a Sales SUMMARY report, or None if Apple has no report for that date."""
        params = {
            "filter[frequency]": frequency,
            "filter[reportType]": "SALES",
            "filter[reportSubType]": "SUMMARY",
            "filter[vendorNumber]": vendor,
            "filter[reportDate]": report_date,
            "filter[version]": "1_1" if frequency == "DAILY" else "1_0",
        }
        status, body = self.request("/v1/salesReports", params, accept="application/a-gzip")
        if status == 404:
            return None
        if status >= 400:
            raise ASCError(f"Sales report HTTP {status} for {report_date}", status)
        try:
            return gzip.decompress(body).decode("utf-8", errors="replace")
        except OSError:
            return body.decode("utf-8", errors="replace")
