"""Credential discovery. Nothing is ever uploaded anywhere except Apple's own API."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(os.environ.get("ASCPILOT_HOME", Path.home() / ".config" / "ascpilot"))
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = Path(os.environ.get("ASCPILOT_CACHE", Path.home() / ".cache" / "ascpilot"))

# Env var aliases used by fastlane, Xcode Cloud scripts and common CI setups.
KEY_ID_VARS = ("ASC_KEY_ID", "APP_STORE_CONNECT_API_KEY_ID", "APP_STORE_CONNECT_KEY_ID")
ISSUER_VARS = ("ASC_ISSUER_ID", "APP_STORE_CONNECT_API_ISSUER_ID", "APP_STORE_CONNECT_ISSUER_ID")
KEY_PATH_VARS = ("ASC_KEY_FILEPATH", "ASC_PRIVATE_KEY_PATH", "APP_STORE_CONNECT_API_KEY_PATH", "APP_STORE_CONNECT_KEY_PATH")
VENDOR_VARS = ("ASC_VENDOR_NUMBER", "APP_STORE_CONNECT_VENDOR_NUMBER")


@dataclass
class Credentials:
    key_id: str
    issuer_id: str
    key_path: str
    vendor_number: Optional[str] = None
    source: str = ""

    def redacted(self) -> dict:
        return {
            "key_id": self.key_id[:3] + "…" if self.key_id else None,
            "issuer_id": self.issuer_id[:4] + "…" if self.issuer_id else None,
            "key_path": self.key_path,
            "vendor_number": "set" if self.vendor_number else None,
            "source": self.source,
        }


def _first_env(names) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return None


def _parse_env_file(path: Path) -> dict:
    """Parse simple `export KEY=value` shell files without executing them."""
    out = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if not m:
            continue
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        # Expand $VAR / ${VAR} using variables defined earlier in the same file, then the environment.
        val = re.sub(r"\$\{([A-Za-z0-9_]+)\}|\$([A-Za-z0-9_]+)",
                     lambda mm: out.get(mm.group(1) or mm.group(2), os.environ.get(mm.group(1) or mm.group(2), mm.group(0))),
                     val)
        out[m.group(1)] = os.path.expanduser(val)
    return out


def load_credentials() -> Optional[Credentials]:
    # 1. Environment
    kid, iss, kp = _first_env(KEY_ID_VARS), _first_env(ISSUER_VARS), _first_env(KEY_PATH_VARS)
    vendor = _first_env(VENDOR_VARS)
    if kid and iss and kp:
        return Credentials(kid, iss, os.path.expanduser(kp), vendor, "environment")

    # 2. ascpilot config file
    if CONFIG_FILE.exists():
        try:
            c = json.loads(CONFIG_FILE.read_text())
            if c.get("key_id") and c.get("issuer_id") and c.get("key_path"):
                return Credentials(c["key_id"], c["issuer_id"], os.path.expanduser(c["key_path"]),
                                   c.get("vendor_number") or vendor, str(CONFIG_FILE))
        except (OSError, ValueError):
            pass

    # 3. Conventional env files
    for candidate in (Path.home() / ".appstoreconnect" / "asc_env.sh", Path.home() / ".asc_env"):
        if candidate.exists():
            env = _parse_env_file(candidate)
            kid = next((env[n] for n in KEY_ID_VARS if env.get(n)), None)
            iss = next((env[n] for n in ISSUER_VARS if env.get(n)), None)
            kp = next((env[n] for n in KEY_PATH_VARS if env.get(n)), None)
            vn = next((env[n] for n in VENDOR_VARS if env.get(n)), None)
            if kid and iss and kp:
                return Credentials(kid, iss, os.path.expanduser(kp), vn, str(candidate))
    return None


def save_config(key_id: str, issuer_id: str, key_path: str, vendor_number: Optional[str]) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {"key_id": key_id, "issuer_id": issuer_id, "key_path": key_path}
    if vendor_number:
        data["vendor_number"] = vendor_number
    CONFIG_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CONFIG_FILE, 0o600)
    return CONFIG_FILE


IGNORE_FILE = CONFIG_DIR / "ignore.json"


def load_ignore() -> set:
    try:
        return set(json.loads(IGNORE_FILE.read_text()))
    except (OSError, ValueError):
        return set()


def save_ignore(ids: set) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    IGNORE_FILE.write_text(json.dumps(sorted(ids), indent=1))
