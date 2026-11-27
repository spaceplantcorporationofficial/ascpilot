import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ASCPILOT_HOME"] = tempfile.mkdtemp()
os.environ["ASCPILOT_CACHE"] = tempfile.mkdtemp()

from ascpilot import license as lic  # noqa: E402
from ascpilot.asc import der_to_raw_signature  # noqa: E402
from ascpilot.leaks import find_leaks, summarize  # noqa: E402
from ascpilot.mcp import handle  # noqa: E402
from ascpilot.report import markdown_report, render_leaks  # noqa: E402
from ascpilot.sales import aggregate, parse_tsv  # noqa: E402
from ascpilot.scan import app_status, classify_version_state  # noqa: E402


class Ed25519Tests(unittest.TestCase):
    # RFC 8032 §7.1 TEST 1 and TEST 2
    def test_rfc8032_vectors(self):
        sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        pk = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertEqual(lic.public_from_secret(sk), pk)
        self.assertEqual(lic.sign(sk, b""), sig)
        self.assertTrue(lic.verify(pk, b"", sig))
        sk2 = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
        pk2 = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
        sig2 = bytes.fromhex("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
        self.assertTrue(lic.verify(pk2, bytes([0x72]), sig2))
        self.assertFalse(lic.verify(pk2, bytes([0x73]), sig2))

    def test_license_roundtrip_and_tamper(self):
        sk = os.urandom(32)
        pub = lic.public_from_secret(sk).hex()
        key = lic.issue(sk, {"email": "dev@example.com", "plan": "pro", "issued": "2026-11-27"})
        self.assertEqual(lic.parse(key, pub)["email"], "dev@example.com")
        prefix, body, sig = key.split(".")
        forged = lic._b64u(json.dumps({"email": "x@y.z", "plan": "studio"}).encode())
        self.assertIsNone(lic.parse(f"{prefix}.{forged}.{sig}", pub))
        self.assertIsNone(lic.parse("garbage", pub))


class JWTTests(unittest.TestCase):
    def test_der_conversion_strips_and_pads(self):
        r = b"\x00" + b"\x81" * 32
        s = b"\x01" * 31
        der = bytes([0x30, 2 + len(r) + 2 + len(s), 0x02, len(r)]) + r + bytes([0x02, len(s)]) + s
        raw = der_to_raw_signature(der)
        self.assertEqual(len(raw), 64)
        self.assertEqual(raw[:32], b"\x81" * 32)
        self.assertEqual(raw[32:], b"\x00" + b"\x01" * 31)


def _app(aid, name, versions, products=(), terr=(175, 175), paid=False, sku=None):
    return {"id": aid, "name": name, "sku": sku or f"SKU{aid}", "versions": [
        {"platform": p, "version": ver, "state": st, "bucket": classify_version_state(st), "created": created}
        for p, ver, st, created in versions],
        "products": [{"name": n, "product_id": pid, "kind": k, "state": st} for n, pid, k, st in products],
        "territories_on": terr[0], "territories_total": terr[1], "paid_upfront": paid, "warnings": []}


SNAP = {"scanned_at": "2026-11-27T00:00:00Z", "api_calls": 10, "apps": [
    _app("1", "Clean Paid", [("IOS", "2.0", "READY_FOR_SALE", "2026-01-01T00:00:00Z")],
         [("Pro", "pro", "subscription", "APPROVED")]),
    _app("2", "Frozen Checkout", [("IOS", "1.1", "WAITING_FOR_REVIEW", "2026-11-20T00:00:00Z"),
                                  ("IOS", "1.0", "READY_FOR_SALE", "2026-10-01T00:00:00Z")],
         [("Lifetime", "life", "iap", "MISSING_METADATA"), ("Monthly", "mo", "subscription", "READY_TO_SUBMIT")]),
    _app("3", "Forgot To Release", [("IOS", "3.0", "PENDING_DEVELOPER_RELEASE", "2026-11-20T00:00:00Z")]),
    _app("4", "Invisible", [("IOS", "1.0", "READY_FOR_SALE", "2026-01-01T00:00:00Z")],
         [("Tip", "tip", "iap", "APPROVED")], terr=(0, 175)),
    _app("5", "Free Nothing", [("IOS", "1.0", "READY_FOR_SALE", "2026-01-01T00:00:00Z")]),
    _app("6", "Rejected Hostage", [("IOS", "2.0", "REJECTED", "2026-11-01T00:00:00Z"),
                                   ("IOS", "1.0", "READY_FOR_SALE", "2026-01-01T00:00:00Z")],
         [("Pro", "pro6", "iap", "WAITING_FOR_REVIEW")]),
    _app("7", "Archived Draft", [("IOS", "1.0", "PREPARE_FOR_SUBMISSION", "2025-01-01T00:00:00Z")]),
]}


class LeakTests(unittest.TestCase):
    def test_rules(self):
        leaks = find_leaks(SNAP)
        codes = {(l["app"], l["code"]) for l in leaks}
        self.assertIn(("Forgot To Release", "APPROVED_NOT_RELEASED"), codes)
        self.assertIn(("Invisible", "APPROVED_OFF_SALE"), codes)
        self.assertIn(("Frozen Checkout", "SKU_MISSING_METADATA"), codes)
        self.assertIn(("Frozen Checkout", "SKU_READY_TO_SUBMIT"), codes)
        self.assertIn(("Free Nothing", "LIVE_NO_REVENUE_PATH"), codes)
        self.assertIn(("Rejected Hostage", "VERSION_REJECTED_BLOCKING"), codes)
        self.assertFalse(any(a == "Clean Paid" for a, _ in codes))
        self.assertFalse(any(a == "Archived Draft" for a, _ in codes))  # no live build, no products: not a leak
        self.assertEqual(leaks[0]["severity"], "critical")
        s = summarize(leaks, SNAP)
        self.assertEqual(s["frozen_products"], 2)
        self.assertEqual(app_status(SNAP["apps"][3]), "off_sale")

    def test_free_render_hides_details(self):
        leaks = find_leaks(SNAP)
        text = render_leaks(leaks, summarize(leaks, SNAP), pro=False)
        self.assertIn("more leak(s) hidden", text)
        self.assertNotIn("Rejected Hostage", text)
        full = render_leaks(leaks, summarize(leaks, SNAP), pro=True)
        self.assertIn("Rejected Hostage", full)
        md = markdown_report(SNAP, leaks, summarize(leaks, SNAP), None)
        self.assertIn("# App Store Cash-Flow Report", md)


TSV = "\t".join(["Provider", "Provider Country", "SKU", "Developer", "Title", "Version", "Product Type Identifier",
                 "Units", "Developer Proceeds", "Begin Date", "End Date", "Customer Currency", "Country Code",
                 "Currency of Proceeds", "Apple Identifier", "Customer Price", "Promo Code", "Parent Identifier"]) + "\n" + \
      "\n".join("\t".join(r) for r in [
          ["APPLE", "US", "SKU2", "Dev", "Frozen Checkout", "1.0", "1F", "12", "0", "11/26/2026", "11/26/2026", "USD", "US", "USD", "2", "0", "", ""],
          ["APPLE", "US", "SKU2", "Dev", "Frozen Checkout", "1.0", "7F", "3", "0", "11/26/2026", "11/26/2026", "USD", "US", "USD", "2", "0", "", ""],
          ["APPLE", "US", "pro", "Dev", "Pro", "", "IAY", "2", "6.99", "11/26/2026", "11/26/2026", "USD", "US", "USD", "999", "9.99", "", "SKU1"],
          ["APPLE", "DE", "pro", "Dev", "Pro", "", "IAY", "1", "6.50", "11/26/2026", "11/26/2026", "EUR", "DE", "EUR", "999", "9.99", "", "SKU1"],
      ])


class SalesTests(unittest.TestCase):
    def test_parse_and_aggregate(self):
        rows = parse_tsv(TSV)
        agg = aggregate(rows, {"SKU1": "1", "SKU2": "2"}, 1)
        self.assertEqual(agg["totals"]["new_installs"], 12)
        self.assertEqual(agg["totals"]["iap_units"], 3)
        self.assertEqual(agg["totals"]["paying_units"], 3)
        self.assertEqual(agg["totals"]["proceeds"], {"USD": 13.98, "EUR": 6.5})
        self.assertEqual(agg["by_app"]["1"]["subscription_units"], 3)
        leaks = find_leaks(SNAP, {"by_app": agg["by_app"], "days": 1})
        self.assertIn(("Frozen Checkout", "INSTALLS_NO_PAID"), {(l["app"], l["code"]) for l in leaks})


class MCPTests(unittest.TestCase):
    def test_handshake_and_tools(self):
        init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertIn("asc_revenue_leaks", [t["name"] for t in tools])
        self.assertTrue(all("pro" not in t for t in tools))
        locked = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "asc_sales_summary", "arguments": {}}})
        self.assertTrue(locked["result"]["isError"])
        self.assertIn("Pro", locked["result"]["content"][0]["text"])
        missing = handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})
        self.assertEqual(missing["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
