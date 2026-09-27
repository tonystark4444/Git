import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import data_pipeline
from data_pipeline import (
    ArkhamLabelClient,
    DiskCache,
    EdgarClient,
    HttpClient,
    OnChainClient,
    build_tracker,
    flags_to_json,
    load_address_file,
)

ESPLORA = "https://esplora.test/api"
WHALE = "bc1qwhale000000000000000000000000000000000"
CUSTODY = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
LEGACY = "1LegacyCaseSensitiveAddrXyZ123456"
NOW = datetime(2026, 9, 1)


def ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """Maps URL -> FakeResponse (or a list of them, served in order)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        route = self.routes.get(url)
        if route is None:
            return FakeResponse(404, {})
        if isinstance(route, list):
            return route.pop(0)
        return route


def tx(txid, when, *, to=None, frm=None, sats=0):
    return {
        "txid": txid,
        "status": {"confirmed": True, "block_time": ts(when)},
        "vin": [{"prevout": {"scriptpubkey_address": frm, "value": sats}}] if frm else [],
        "vout": [{"scriptpubkey_address": to, "value": sats}] if to else [],
    }


def address_stats(btc):
    return FakeResponse(payload={"chain_stats": {
        "funded_txo_sum": int(btc * 1e8), "spent_txo_sum": 0, "tx_count": 1}})


class HttpClientTest(unittest.TestCase):
    def setUp(self):
        self._sleep = data_pipeline.time.sleep
        data_pipeline.time.sleep = lambda s: None

    def tearDown(self):
        data_pipeline.time.sleep = self._sleep

    def test_retries_then_succeeds(self):
        session = FakeSession({"https://x.test/a": [
            FakeResponse(429, {}), FakeResponse(503, {}), FakeResponse(200, {"ok": 1})]})
        self.assertEqual(HttpClient(session=session).get("https://x.test/a"), {"ok": 1})
        self.assertEqual(len(session.calls), 3)

    def test_gives_up_after_max_retries(self):
        session = FakeSession({"https://x.test/a": [FakeResponse(500, {})] * 10})
        with self.assertRaises(requests.HTTPError):
            HttpClient(session=session).get("https://x.test/a")

    def test_client_error_not_retried(self):
        session = FakeSession({})
        with self.assertRaises(requests.HTTPError):
            HttpClient(session=session).get("https://x.test/missing")
        self.assertEqual(len(session.calls), 1)

    def test_disk_cache_hit_skips_network(self):
        with tempfile.TemporaryDirectory() as d:
            session = FakeSession({"https://x.test/a": [FakeResponse(200, {"v": 1})]})
            client = HttpClient(DiskCache(d), session=session)
            self.assertEqual(client.get("https://x.test/a"), {"v": 1})
            self.assertEqual(client.get("https://x.test/a"), {"v": 1})
            self.assertEqual(len(session.calls), 1)

    def test_disk_cache_expires(self):
        with tempfile.TemporaryDirectory() as d:
            cache = DiskCache(d)
            cache.set("k", {"v": 1})
            self.assertIsNone(cache.get("k", ttl=-1))
            self.assertEqual(cache.get("k", ttl=60), {"v": 1})


class OnChainClientTest(unittest.TestCase):
    def test_net_flows_and_pagination_stop_at_since(self):
        page1 = [tx(f"t{i}", NOW - timedelta(days=i), to=WHALE, sats=100_000_000) for i in range(25)]
        page2 = [
            tx("spend", NOW - timedelta(days=25), frm=WHALE, sats=50_000_000),
            tx("old", NOW - timedelta(days=90), to=WHALE, sats=1),
        ]
        session = FakeSession({
            f"{ESPLORA}/address/{WHALE}/txs/chain": FakeResponse(payload=page1),
            f"{ESPLORA}/address/{WHALE}/txs/chain/t24": FakeResponse(payload=page2),
        })
        flows = OnChainClient(HttpClient(session=session), ESPLORA).get_flows(
            WHALE, since=NOW - timedelta(days=30))
        self.assertEqual(len(flows), 26)
        self.assertEqual(flows[-1].flow_type, "outflow")
        self.assertAlmostEqual(flows[-1].amount_btc, 0.5)

    def test_self_transfer_change_is_netted(self):
        change = {
            "txid": "c", "status": {"block_time": ts(NOW)},
            "vin": [{"prevout": {"scriptpubkey_address": WHALE, "value": 300}}],
            "vout": [{"scriptpubkey_address": WHALE, "value": 200},
                     {"scriptpubkey_address": "bc1qother", "value": 90}],
        }
        self.assertEqual(OnChainClient._net_sats(change, WHALE), -100)


class ArkhamTest(unittest.TestCase):
    def test_attribution_parsing_and_auth_header(self):
        url = f"{data_pipeline.PipelineConfig.ARKHAM_BASE_URL}/intelligence/address/{CUSTODY}"
        session = FakeSession({url: FakeResponse(payload={
            "arkhamEntity": {"id": "coinbase", "name": "Coinbase", "type": "cex"},
            "arkhamLabel": {"name": "Coinbase Prime Custody"},
        })})
        result = ArkhamLabelClient(HttpClient(session=session), "KEY").get_attribution(CUSTODY)
        self.assertEqual(result["entity_id"], "coinbase")
        self.assertEqual(result["entity_type"], "cex")
        self.assertEqual(session.calls[0][2], {"API-Key": "KEY"})

    def test_failure_returns_empty_attribution(self):
        result = ArkhamLabelClient(HttpClient(session=FakeSession({})), "KEY").get_attribution(WHALE)
        self.assertIsNone(result["entity_id"])


class EdgarTest(unittest.TestCase):
    def test_requires_user_agent(self):
        with self.assertRaises(ValueError):
            EdgarClient(HttpClient(session=FakeSession({})), "")

    def test_collect_custodians_from_filings(self):
        sec = data_pipeline.PipelineConfig.SEC_BASE_URL
        data = data_pipeline.PipelineConfig.SEC_DATA_URL
        filing_url = f"{sec}/Archives/edgar/data/1234/000123426000001/ibit10k.htm"
        session = FakeSession({
            f"{sec}/files/company_tickers.json": FakeResponse(payload={
                "0": {"cik_str": 1234, "ticker": "IBIT", "title": "iShares Bitcoin Trust"}}),
            f"{data}/submissions/CIK0000001234.json": FakeResponse(payload={"filings": {"recent": {
                "form": ["4", "10-K"],
                "accessionNumber": ["0001234-26-000000", "0001234-26-000001"],
                "primaryDocument": ["form4.xml", "ibit10k.htm"],
            }}}),
            filing_url: FakeResponse(text=(
                "<html><body><p>The Trust&#8217;s bitcoin is held by Coinbase Custody Trust Company.</p>"
                f"<p>Custodian: Coinbase Prime\n{CUSTODY}</p></body></html>")),
        })
        edgar = EdgarClient(HttpClient(session=session), "Test test@example.com")
        addresses, by_ticker = edgar.collect(["ibit", "NOPE"])
        self.assertEqual(by_ticker["IBIT"], {"Coinbase Custody", "Coinbase Prime"})
        self.assertIn(CUSTODY, addresses["Coinbase Prime"])
        self.assertTrue(all(c[2] == {"User-Agent": "Test test@example.com"} for c in session.calls))


class BuildTrackerTest(unittest.TestCase):
    def make_onchain(self):
        start = NOW - timedelta(days=30)
        whale_txs = [tx(f"w{i}", NOW - timedelta(days=i), to=WHALE, sats=5 * 10**8) for i in range(20)]
        custody_txs = [tx("c0", NOW - timedelta(days=1), to=CUSTODY, sats=40 * 10**8),
                       tx("c1", NOW + timedelta(days=1), to=CUSTODY, sats=10 * 10**8)]
        routes = {
            f"{ESPLORA}/address/{WHALE}": address_stats(200),
            f"{ESPLORA}/address/{WHALE}/txs/chain": FakeResponse(payload=whale_txs),
            f"{ESPLORA}/address/{CUSTODY}": address_stats(500),
            f"{ESPLORA}/address/{CUSTODY}/txs/chain": FakeResponse(payload=custody_txs),
            f"{ESPLORA}/address/{LEGACY}": address_stats(150),
            f"{ESPLORA}/address/{LEGACY}/txs/chain": FakeResponse(payload=[
                tx("l0", NOW - timedelta(days=2), frm=LEGACY, sats=10 * 10**8)]),
        }
        return OnChainClient(HttpClient(session=FakeSession(routes)), ESPLORA), start

    def test_balances_reconstructed_to_window_end(self):
        onchain, start = self.make_onchain()
        tracker = build_tracker([WHALE, CUSTODY, LEGACY, "bc1qbroken"], onchain,
                                start=start, end=NOW, coinbase_prime=[CUSTODY])
        # whale: all flows inside window -> end balance == current balance
        self.assertAlmostEqual(tracker.wallets[WHALE].balance_btc, 200)
        # custody: a +10 flow after `end` must be excluded -> 490 at window end
        self.assertAlmostEqual(tracker.wallets[CUSTODY].balance_btc, 490)
        # legacy (case-sensitive base58) address still gets its outflow applied
        self.assertAlmostEqual(tracker.wallets[LEGACY.lower()].balance_btc, 150)
        self.assertNotIn("bc1qbroken", tracker.wallets)
        self.assertIn(CUSTODY, tracker.custodial_addresses)
        self.assertEqual(len(tracker.flow_history), 22)

    def test_arkham_custodial_types_and_single_label(self):
        onchain, start = self.make_onchain()

        class FakeArkham:
            def get_attribution(self, address):
                if address == CUSTODY:
                    return {"entity_id": "coinbase", "entity_type": "cex", "label": "Coinbase Prime"}
                return {"entity_id": None, "entity_type": None, "label": None}

        tracker = build_tracker([WHALE, CUSTODY], onchain, start=start, end=NOW, arkham=FakeArkham())
        self.assertIn(CUSTODY, tracker.custodial_addresses)
        self.assertIn("arkham:coinbase", tracker.entities)
        self.assertTrue(tracker.entities["arkham:coinbase"].is_custodial)
        self.assertEqual(tracker.wallets[WHALE].entity_id, f"wallet:{WHALE}")

    def test_end_to_end_detection_and_json(self):
        onchain, start = self.make_onchain()
        tracker = build_tracker([WHALE, CUSTODY], onchain, start=start, end=NOW,
                                coinbase_prime=[CUSTODY])
        tracker.detect_illusion_events(end_date=NOW, window_days=30)
        out = flags_to_json(tracker)
        json.dumps(out)
        self.assertEqual(out["wallet_count"], 2)
        self.assertEqual(len(out["flags"]), len(tracker.flags))


class AddressFileTest(unittest.TestCase):
    def test_comments_blank_lines_and_csv(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(f"# header\n{WHALE}\n\n{CUSTODY}, coinbase prime  # note\n")
        self.assertEqual(load_address_file(f.name), [WHALE, CUSTODY])
        self.assertEqual(load_address_file(None), [])


if __name__ == "__main__":
    unittest.main()
