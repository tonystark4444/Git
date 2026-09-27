"""
Stage 2: Real Data Pipeline
===========================

Purpose:
- Replace Stage 1's sample data with live sources:
  * On-chain balances and transaction history (Esplora API: mempool.space /
    blockstream.info, or a self-hosted instance)
  * Arkham Intelligence entity labels
  * Coinbase Prime custodial address lists (local file; no public feed exists)
  * EDGAR filings of spot Bitcoin ETFs / trusts (custodian disclosures)
- Cache every response on disk so repeated runs don't re-hit rate-limited APIs
- Build a Stage 1 CohortTracker from that data and run illusion detection

Environment variables:
- ARKHAM_API_KEY   Arkham Intelligence API key (optional; labels skipped if unset)
- SEC_USER_AGENT   Required by the SEC for EDGAR access, e.g. "Your Name you@example.com"

Usage:
    python data_pipeline.py --addresses addresses.txt \\
        --coinbase-prime coinbase_prime.txt --edgar-tickers IBIT,GBTC,FBTC \\
        --days 30 --output flags.json
"""

import argparse
import hashlib
import html
import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests

from cohort_illusion_detection import (
    CohortTracker,
    Config,
    EdgarParser,
    FlowRecord,
    Wallet,
)


# =============================================================================
# CONFIGURATION
# =============================================================================

class PipelineConfig:
    ESPLORA_BASE_URL = "https://mempool.space/api"
    ARKHAM_BASE_URL = "https://api.arkhamintelligence.com"
    SEC_BASE_URL = "https://www.sec.gov"
    SEC_DATA_URL = "https://data.sec.gov"

    CACHE_DIR = ".cache"
    CACHE_TTL_SECONDS = 6 * 3600          # balances / tx pages
    LABEL_CACHE_TTL_SECONDS = 7 * 86400   # Arkham labels change rarely
    FILING_CACHE_TTL_SECONDS = 30 * 86400  # EDGAR documents are immutable

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 4
    # Minimum seconds between requests to the same host (SEC asks for <= 10 req/s)
    MIN_REQUEST_INTERVAL = {
        "www.sec.gov": 0.15,
        "data.sec.gov": 0.15,
        "mempool.space": 0.25,
        "blockstream.info": 0.25,
    }

    # Arkham entity types treated as custodial (their balances are client funds)
    CUSTODIAL_ENTITY_TYPES = {"cex", "custodian", "fund", "etf"}

    # EDGAR forms that disclose custodian arrangements for spot BTC products
    EDGAR_FORMS = {"10-K", "10-Q", "S-1", "S-1/A", "424B3", "424B4", "8-K"}
    EDGAR_MAX_FILINGS_PER_CIK = 5

    KNOWN_CUSTODIANS = [
        "Coinbase Custody",
        "Coinbase Prime",
        "Anchorage Digital",
        "BitGo",
        "Fidelity Digital Assets",
        "Gemini Trust",
        "BNY Mellon",
        "State Street",
        "Komainu",
    ]

    SATS_PER_BTC = 100_000_000


def utcnow() -> datetime:
    """Naive UTC 'now', matching the naive datetimes Stage 1 compares against."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =============================================================================
# HTTP + CACHE
# =============================================================================

class DiskCache:
    """Tiny JSON-on-disk cache keyed by request URL + params."""

    def __init__(self, cache_dir: str = PipelineConfig.CACHE_DIR, enabled: bool = True):
        self.dir = Path(cache_dir)
        self.enabled = enabled
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, key: str, ttl: float):
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - entry["ts"] > ttl:
            return None
        return entry["data"]

    def set(self, key: str, data):
        if not self.enabled:
            return
        self._path(key).write_text(json.dumps({"ts": time.time(), "data": data}))


class HttpClient:
    """requests wrapper with caching, per-host throttling and retry/backoff."""

    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, cache: Optional[DiskCache] = None, session: Optional[requests.Session] = None):
        self.cache = cache or DiskCache(enabled=False)
        self.session = session or requests.Session()
        self._last_request: Dict[str, float] = {}

    def _throttle(self, url: str):
        host = urlparse(url).netloc
        interval = PipelineConfig.MIN_REQUEST_INTERVAL.get(host, 0.0)
        wait = self._last_request.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def get(self, url: str, *, params: Optional[Dict] = None, headers: Optional[Dict] = None,
            ttl: float = PipelineConfig.CACHE_TTL_SECONDS, as_json: bool = True):
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        cached = self.cache.get(key, ttl)
        if cached is not None:
            return cached

        last_error: Optional[Exception] = None
        for attempt in range(PipelineConfig.MAX_RETRIES + 1):
            self._throttle(url)
            try:
                response = self.session.get(url, params=params, headers=headers,
                                            timeout=PipelineConfig.REQUEST_TIMEOUT)
            except requests.RequestException as e:
                last_error = e
            else:
                if response.status_code in self.RETRY_STATUSES:
                    last_error = requests.HTTPError(f"{response.status_code} for {url}")
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        time.sleep(int(retry_after))
                        continue
                else:
                    response.raise_for_status()
                    data = response.json() if as_json else response.text
                    self.cache.set(key, data)
                    return data
            if attempt < PipelineConfig.MAX_RETRIES:
                time.sleep(2 ** attempt)
        raise last_error


# =============================================================================
# DATA SOURCES
# =============================================================================

class OnChainClient:
    """Balances and transaction history from an Esplora-compatible API."""

    def __init__(self, http: HttpClient, base_url: str = PipelineConfig.ESPLORA_BASE_URL):
        self.http = http
        self.base_url = base_url.rstrip("/")

    def get_balance_btc(self, address: str) -> float:
        """Confirmed balance (mempool excluded)."""
        data = self.http.get(f"{self.base_url}/address/{address}")
        stats = data["chain_stats"]
        return (stats["funded_txo_sum"] - stats["spent_txo_sum"]) / PipelineConfig.SATS_PER_BTC

    def get_flows(self, address: str, since: datetime) -> List[FlowRecord]:
        """
        Net BTC flow per confirmed transaction touching `address`, newest first,
        for all transactions at or after `since`.
        """
        flows: List[FlowRecord] = []
        last_txid: Optional[str] = None
        while True:
            url = f"{self.base_url}/address/{address}/txs/chain"
            if last_txid:
                url += f"/{last_txid}"
            txs = self.http.get(url)
            if not txs:
                break
            for tx in txs:
                block_time = tx.get("status", {}).get("block_time")
                if block_time is None:
                    continue
                timestamp = datetime.fromtimestamp(block_time, tz=timezone.utc).replace(tzinfo=None)
                if timestamp < since:
                    return flows
                net_sats = self._net_sats(tx, address)
                if net_sats == 0:
                    continue
                flows.append(FlowRecord(
                    wallet_address=address,
                    amount_btc=abs(net_sats) / PipelineConfig.SATS_PER_BTC,
                    timestamp=timestamp,
                    flow_type="inflow" if net_sats > 0 else "outflow",
                ))
            # Esplora pages confirmed history 25 txs at a time
            if len(txs) < 25:
                break
            last_txid = txs[-1]["txid"]
        return flows

    @staticmethod
    def _net_sats(tx: Dict, address: str) -> int:
        received = sum(out.get("value", 0) for out in tx.get("vout", [])
                       if out.get("scriptpubkey_address") == address)
        spent = sum((vin.get("prevout") or {}).get("value", 0) for vin in tx.get("vin", [])
                    if (vin.get("prevout") or {}).get("scriptpubkey_address") == address)
        return received - spent


class ArkhamLabelClient:
    """Entity attribution from the Arkham Intelligence API."""

    def __init__(self, http: HttpClient, api_key: str, base_url: str = PipelineConfig.ARKHAM_BASE_URL):
        self.http = http
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get_attribution(self, address: str) -> Dict[str, Optional[str]]:
        """Returns {"entity_id", "entity_name", "entity_type", "label"}; values may be None."""
        try:
            data = self.http.get(
                f"{self.base_url}/intelligence/address/{address}",
                params={"chain": "bitcoin"},
                headers={"API-Key": self.api_key},
                ttl=PipelineConfig.LABEL_CACHE_TTL_SECONDS,
            )
        except requests.RequestException as e:
            print(f"Error fetching Arkham attribution for {address}: {e}")
            data = {}
        entity = data.get("arkhamEntity") or {}
        label = data.get("arkhamLabel") or {}
        return {
            "entity_id": entity.get("id"),
            "entity_name": entity.get("name"),
            "entity_type": entity.get("type"),
            "label": label.get("name"),
        }


class EdgarClient:
    """Custodian disclosures from EDGAR filings of spot Bitcoin ETFs / trusts."""

    def __init__(self, http: HttpClient, user_agent: str):
        if not user_agent:
            raise ValueError("SEC_USER_AGENT must be set (e.g. 'Your Name you@example.com') to query EDGAR")
        self.http = http
        self.headers = {"User-Agent": user_agent}

    def resolve_ciks(self, tickers: Iterable[str]) -> Dict[str, int]:
        data = self.http.get(f"{PipelineConfig.SEC_BASE_URL}/files/company_tickers.json",
                             headers=self.headers, ttl=PipelineConfig.LABEL_CACHE_TTL_SECONDS)
        by_ticker = {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}
        ciks = {}
        for ticker in tickers:
            cik = by_ticker.get(ticker.upper())
            if cik is None:
                print(f"EDGAR: no CIK found for ticker {ticker}")
            else:
                ciks[ticker.upper()] = cik
        return ciks

    def recent_filing_urls(self, cik: int) -> List[str]:
        data = self.http.get(f"{PipelineConfig.SEC_DATA_URL}/submissions/CIK{cik:010d}.json",
                             headers=self.headers)
        recent = data.get("filings", {}).get("recent", {})
        urls = []
        for form, accession, doc in zip(recent.get("form", []),
                                        recent.get("accessionNumber", []),
                                        recent.get("primaryDocument", [])):
            if form in PipelineConfig.EDGAR_FORMS and doc:
                urls.append(f"{PipelineConfig.SEC_BASE_URL}/Archives/edgar/data/"
                            f"{cik}/{accession.replace('-', '')}/{doc}")
            if len(urls) >= PipelineConfig.EDGAR_MAX_FILINGS_PER_CIK:
                break
        return urls

    def fetch_filing_text(self, url: str) -> str:
        raw = self.http.get(url, headers=self.headers, as_json=False,
                            ttl=PipelineConfig.FILING_CACHE_TTL_SECONDS)
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"[ \t]+", " ", html.unescape(text))

    @staticmethod
    def custodians_mentioned(text: str) -> Set[str]:
        lowered = text.lower()
        return {name for name in PipelineConfig.KNOWN_CUSTODIANS if name.lower() in lowered}

    def collect(self, tickers: Iterable[str]) -> Tuple[Dict[str, List[str]], Dict[str, Set[str]]]:
        """
        Returns:
          custodian_addresses: {custodian_name: [btc addresses]} (Stage 1 format)
          custodians_by_ticker: {ticker: {custodian names mentioned in filings}}
        """
        custodian_addresses: Dict[str, List[str]] = {}
        custodians_by_ticker: Dict[str, Set[str]] = {}
        for ticker, cik in self.resolve_ciks(tickers).items():
            names: Set[str] = set()
            for url in self.recent_filing_urls(cik):
                try:
                    text = self.fetch_filing_text(url)
                except requests.RequestException as e:
                    print(f"EDGAR: failed to fetch {url}: {e}")
                    continue
                names |= self.custodians_mentioned(text)
                for custodian, addresses in EdgarParser.parse_13f_filing(text).items():
                    merged = custodian_addresses.setdefault(custodian, [])
                    merged.extend(a for a in addresses if a not in merged)
            custodians_by_ticker[ticker] = names
        return custodian_addresses, custodians_by_ticker


def load_address_file(path: Optional[str]) -> List[str]:
    """One address per line (or first CSV column); blank lines and # comments ignored."""
    if not path:
        return []
    addresses = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            addresses.append(line.split(",")[0].strip())
    return addresses


# =============================================================================
# PIPELINE
# =============================================================================

def build_tracker(
    addresses: List[str],
    onchain: OnChainClient,
    *,
    start: datetime,
    end: datetime,
    arkham: Optional[ArkhamLabelClient] = None,
    coinbase_prime: Iterable[str] = (),
    edgar_custodians: Optional[Dict[str, List[str]]] = None,
) -> CohortTracker:
    """
    Build a Stage 1 CohortTracker from live data.

    Each wallet starts at its reconstructed balance as of `start`
    (current balance minus every net flow since then); flows inside
    (start, end] are then replayed so balances reflect the state at `end`.
    """
    tracker = CohortTracker()
    # Stage 1 keys wallets by lowercased address, so normalise everything the same way
    custodial = {a.lower() for a in coinbase_prime}
    attributions: Dict[str, Dict[str, Optional[str]]] = {}

    if arkham:
        for address in addresses:
            attribution = arkham.get_attribution(address)
            attributions[address] = attribution
            if (attribution.get("entity_type") or "").lower() in PipelineConfig.CUSTODIAL_ENTITY_TYPES:
                custodial.add(address.lower())

    tracker.load_custodial_addresses(
        sorted(custodial),
        {name: [a.lower() for a in addrs] for name, addrs in (edgar_custodians or {}).items()},
    )

    replay: List[FlowRecord] = []
    for address in addresses:
        try:
            balance_now = onchain.get_balance_btc(address)
            flows = onchain.get_flows(address, since=start)
        except requests.RequestException as e:
            print(f"Skipping {address}: {e}")
            continue

        net_since_start = sum(f.amount_btc if f.flow_type == "inflow" else -f.amount_btc for f in flows)
        # One label per wallet: Stage 1 builds one cohort per label, so extra labels would double count
        attribution = attributions.get(address, {})
        label = attribution.get("entity_id") or attribution.get("label")
        tracker.add_wallet(Wallet(
            address=address,
            balance_btc=balance_now - net_since_start,
            labels={label} if label else set(),
        ))
        for flow in flows:
            if flow.timestamp <= end:
                flow.wallet_address = address.lower()
                replay.append(flow)

    for flow in sorted(replay, key=lambda f: f.timestamp):
        tracker.add_flow(flow)

    tracker.build_entity_cohorts()
    return tracker


def flags_to_json(tracker: CohortTracker, extra: Optional[Dict] = None) -> Dict:
    return {
        "generated_at": utcnow().isoformat(),
        "wallet_count": len(tracker.wallets),
        "entity_count": len(tracker.entities),
        "custodial_count": len(tracker.custodial_addresses),
        "flags": [
            {**asdict(flag), "timestamp": flag.timestamp.isoformat()}
            for flag in tracker.flags
        ],
        **(extra or {}),
    }


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="Stage 2: run illusion detection on live data")
    parser.add_argument("--addresses", required=True, help="File of BTC addresses to track")
    parser.add_argument("--coinbase-prime", help="File of known Coinbase Prime addresses")
    parser.add_argument("--edgar-tickers", default="", help="Comma-separated ETF tickers, e.g. IBIT,GBTC")
    parser.add_argument("--days", type=int, default=Config.WINDOW_DAYS, help="Detection window in days")
    parser.add_argument("--end", help="Window end (ISO date, UTC); default now")
    parser.add_argument("--esplora-url", default=PipelineConfig.ESPLORA_BASE_URL)
    parser.add_argument("--cache-dir", default=PipelineConfig.CACHE_DIR)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--output", help="Write flags as JSON to this path")
    args = parser.parse_args(argv)

    end = datetime.fromisoformat(args.end) if args.end else utcnow()
    start = end - timedelta(days=args.days)

    http = HttpClient(DiskCache(args.cache_dir, enabled=not args.no_cache))
    onchain = OnChainClient(http, args.esplora_url)

    arkham_key = os.environ.get("ARKHAM_API_KEY", "")
    arkham = ArkhamLabelClient(http, arkham_key) if arkham_key else None
    if not arkham:
        print("ℹ️  ARKHAM_API_KEY not set; skipping Arkham labels")

    edgar_custodians: Dict[str, List[str]] = {}
    custodians_by_ticker: Dict[str, Set[str]] = {}
    tickers = [t.strip() for t in args.edgar_tickers.split(",") if t.strip()]
    if tickers:
        edgar = EdgarClient(http, os.environ.get("SEC_USER_AGENT", ""))
        edgar_custodians, custodians_by_ticker = edgar.collect(tickers)
        for ticker, names in custodians_by_ticker.items():
            print(f"🏛️  {ticker}: custodians disclosed = {', '.join(sorted(names)) or 'none found'}")

    print("🚀 Stage 2: Live Data Pipeline")
    print("=" * 60)
    tracker = build_tracker(
        load_address_file(args.addresses),
        onchain,
        start=start,
        end=end,
        arkham=arkham,
        coinbase_prime=load_address_file(args.coinbase_prime),
        edgar_custodians=edgar_custodians,
    )
    print(f"\n📊 Loaded {len(tracker.wallets)} wallets")
    print(f"🏦 Loaded {len(tracker.custodial_addresses)} custodial addresses")
    print(f"👥 Built {len(tracker.entities)} entity cohorts")

    print("\n🔍 Detecting measurement illusion events...")
    tracker.detect_illusion_events(end_date=end, window_days=args.days)
    if not tracker.flags:
        print(f"\n✅ No illusion events detected in the last {args.days} days.")

    if args.output:
        result = flags_to_json(tracker, {
            "edgar_custodians": {t: sorted(n) for t, n in custodians_by_ticker.items()},
        })
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(f"\n💾 Wrote {len(tracker.flags)} flag(s) to {args.output}")
    return tracker


if __name__ == "__main__":
    main()
