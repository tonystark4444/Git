"""
Stage 1: Entity-Adjusted Cohort Tracking + Illusion Detection
============================================================

Purpose:
- Track entity-adjusted cohorts of Bitcoin wallets
- Label Arkham/Coinbase Prime custodial addresses
- Parse EDGAR custodian data
- Auto-flag "measurement illusion" events when raw whale accumulation
  diverges from entity-adjusted balances by >1 standard deviation over 30 days

Data Sources:
- Arkham Intelligence API (wallet labeling)
- Coinbase Prime custodial address lists
- EDGAR (SEC filings for institutional custodians)
- On-chain data (wallet balances, transaction history)
"""

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field
import requests
import re


# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    # Thresholds
    DIVERGENCE_THRESHOLD = 1.0  # Standard deviations
    WINDOW_DAYS = 30

    # API Keys (replace with your own)
    ARKHAM_API_KEY = "your_arkham_api_key"

    # Custodial address sources
    COINBASE_PRIME_ADDRESSES = [
        "bc1q...",  # Add known Coinbase Prime addresses
        # Load from external source or database
    ]

    # EDGAR parsing
    EDGAR_BASE_URL = "https://data.sec.gov"


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class Wallet:
    """Represents a Bitcoin wallet address."""
    address: str
    balance_btc: float
    labels: Set[str] = field(default_factory=set)  # e.g., {"arkham:whale", "coinbase:prime"}
    entity_id: Optional[str] = None  # Normalized entity identifier
    creation_timestamp: Optional[datetime] = None
    last_tx_timestamp: Optional[datetime] = None


@dataclass
class Entity:
    """Represents a normalized entity (e.g., institutional investor, custodian)."""
    entity_id: str
    name: str
    wallet_addresses: Set[str] = field(default_factory=set)
    is_custodial: bool = False
    custodian_name: Optional[str] = None


@dataclass
class FlowRecord:
    """Represents a flow event (deposit/withdrawal)."""
    wallet_address: str
    amount_btc: float
    timestamp: datetime
    flow_type: str  # "inflow" or "outflow"
    entity_id: Optional[str] = None


@dataclass
class IllusionFlag:
    """Represents a detected measurement illusion event."""
    flag_id: str
    timestamp: datetime
    cohort_id: str
    raw_whale_accumulation: float
    entity_adjusted_balance: float
    divergence_std: float
    severity: str  # "low", "medium", "high"
    details: Dict = field(default_factory=dict)


# =============================================================================
# DATA LOADERS
# =============================================================================

class ArkhamClient:
    """Client for Arkham Intelligence API."""

    @staticmethod
    def get_wallet_labels(address: str, api_key: str) -> Set[str]:
        """Fetch labels for a wallet from Arkham API."""
        # Mock implementation - replace with actual API call
        # Example API: https://api.arkhamintel.com/api/v1/wallet/labels
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            response = requests.get(
                f"https://api.arkhamintel.com/api/v1/wallet/{address}/labels",
                headers=headers
            )
            if response.status_code == 200:
                data = response.json()
                return set(label["label"] for label in data.get("labels", []))
        except Exception as e:
            print(f"Error fetching Arkham labels for {address}: {e}")
        return set()


class EdgarParser:
    """Parser for EDGAR custodian data."""

    @staticmethod
    def parse_13f_filing(filing_text: str) -> Dict[str, List[str]]:
        """
        Parse a 13F filing to extract custodian information.
        Returns: {custodian_name: [bitcoin_addresses]}
        """
        custodians = {}

        # Pattern to find custodian names (simplified - adjust based on actual filing format)
        custodian_pattern = re.compile(
            r'(?:Custodian|Held at):?\s*(?:for the benefit of|on behalf of)?\s*([A-Za-z\s]+?)(?:\n|\.|,)',
            re.IGNORECASE
        )

        # Pattern to find Bitcoin addresses (simplified)
        btc_pattern = re.compile(r'[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[ac-hj-np-z02-9]{11,71}')

        matches = custodian_pattern.finditer(filing_text)
        for match in matches:
            custodian_name = match.group(1).strip()
            # Find addresses near custodian mention
            addresses = btc_pattern.findall(filing_text[match.start():match.start()+500])
            if addresses:
                custodians[custodian_name] = addresses

        return custodians


# =============================================================================
# CORE LOGIC
# =============================================================================

class CohortTracker:
    """Tracks cohorts of wallets and detects measurement illusion."""

    def __init__(self):
        self.wallets: Dict[str, Wallet] = {}
        self.entities: Dict[str, Entity] = {}
        self.flow_history: List[FlowRecord] = []
        self.flags: List[IllusionFlag] = []
        self.custodial_addresses: Set[str] = set()

    def load_custodial_addresses(self, coinbase_prime: List[str], edgar_custodians: Dict[str, List[str]]):
        """Load known custodial addresses from multiple sources."""
        self.custodial_addresses.update(coinbase_prime)
        for addresses in edgar_custodians.values():
            self.custodial_addresses.update(addresses)

    def add_wallet(self, wallet: Wallet):
        """Add or update a wallet."""
        # Normalize address (case-insensitive for Bitcoin)
        normalized_addr = wallet.address.lower()

        # Check if custodial
        if normalized_addr in self.custodial_addresses:
            wallet.labels.add("custodial")

        self.wallets[normalized_addr] = wallet

    def add_flow(self, flow: FlowRecord):
        """Record a flow event."""
        self.flow_history.append(flow)

        # Update wallet balance
        if flow.wallet_address in self.wallets:
            wallet = self.wallets[flow.wallet_address]
            if flow.flow_type == "inflow":
                wallet.balance_btc += flow.amount_btc
            else:
                wallet.balance_btc -= flow.amount_btc
            wallet.last_tx_timestamp = flow.timestamp

    def build_entity_cohorts(self):
        """
        Group wallets into entity cohorts based on:
        - Arkham labels
        - Custodial status
        - Common funding patterns
        """
        # Reset entities
        self.entities = {}

        # Group by Arkham labels
        label_groups: Dict[str, Set[str]] = {}
        for addr, wallet in self.wallets.items():
            for label in wallet.labels:
                if label not in label_groups:
                    label_groups[label] = set()
                label_groups[label].add(addr)

        # Create entities from label groups
        for label, addresses in label_groups.items():
            entity_id = f"arkham:{label}"
            is_custodial = any(addr in self.custodial_addresses for addr in addresses)

            self.entities[entity_id] = Entity(
                entity_id=entity_id,
                name=label,
                wallet_addresses=addresses,
                is_custodial=is_custodial,
                custodian_name="Coinbase Prime" if is_custodial else None
            )

            # Update wallet entity_id
            for addr in addresses:
                if addr in self.wallets:
                    self.wallets[addr].entity_id = entity_id

        # Handle unlabelled wallets (create singleton entities)
        for addr, wallet in self.wallets.items():
            if wallet.entity_id is None:
                entity_id = f"wallet:{addr}"
                self.entities[entity_id] = Entity(
                    entity_id=entity_id,
                    name=f"Unlabeled Wallet {addr[:8]}...",
                    wallet_addresses={addr},
                    is_custodial=addr in self.custodial_addresses
                )
                wallet.entity_id = entity_id

    def calculate_whale_accumulation(self, start_date: datetime, end_date: datetime) -> float:
        """
        Calculate raw whale accumulation (sum of inflows to large wallets).
        Whale = wallet with balance > 100 BTC (adjustable threshold).
        """
        whale_threshold = 100.0
        total_accumulation = 0.0

        for flow in self.flow_history:
            if start_date <= flow.timestamp <= end_date:
                if flow.flow_type == "inflow":
                    wallet = self.wallets.get(flow.wallet_address.lower())
                    if wallet and wallet.balance_btc >= whale_threshold:
                        total_accumulation += flow.amount_btc

        return total_accumulation

    def calculate_entity_adjusted_balance(self, start_date: datetime, end_date: datetime) -> float:
        """
        Calculate entity-adjusted balance (excludes known custodial flows).
        """
        total_balance = 0.0

        for entity_id, entity in self.entities.items():
            if entity.is_custodial:
                continue  # Skip custodial entities

            # Sum balances for non-custodial entities
            for addr in entity.wallet_addresses:
                wallet = self.wallets.get(addr.lower())
                if wallet:
                    total_balance += wallet.balance_btc

        return total_balance

    def detect_illusion_events(self, end_date: datetime, window_days: int = 30):
        """
        Detect measurement illusion events by comparing raw whale accumulation
        to entity-adjusted balances over a rolling window.

        Flags an event if divergence > 1 standard deviation.
        """
        start_date = end_date - timedelta(days=window_days)

        # Calculate metrics for the window
        raw_accumulation = self.calculate_whale_accumulation(start_date, end_date)
        entity_balance = self.calculate_entity_adjusted_balance(start_date, end_date)

        # Calculate historical divergence for standard deviation
        divergences = []
        current_date = start_date

        # Collect divergence data points (daily)
        while current_date <= end_date:
            daily_raw = self.calculate_whale_accumulation(
                current_date, current_date + timedelta(days=1)
            )
            daily_entity = self.calculate_entity_adjusted_balance(
                current_date, current_date + timedelta(days=1)
            )
            if daily_entity > 0:  # Avoid division by zero
                divergence = (daily_raw - daily_entity) / daily_entity
                divergences.append(divergence)
            current_date += timedelta(days=1)

        # Calculate standard deviation of divergences
        if divergences:
            std_dev = np.std(divergences)
            current_divergence = (raw_accumulation - entity_balance) / entity_balance if entity_balance > 0 else 0

            # Check if divergence exceeds threshold
            if abs(current_divergence) > Config.DIVERGENCE_THRESHOLD * std_dev:
                flag = IllusionFlag(
                    flag_id=f"illusion_{end_date.strftime('%Y%m%d_%H%M%S')}",
                    timestamp=end_date,
                    cohort_id="all_whales",
                    raw_whale_accumulation=raw_accumulation,
                    entity_adjusted_balance=entity_balance,
                    divergence_std=current_divergence / std_dev if std_dev > 0 else 0,
                    severity="high" if abs(current_divergence) > 2 * std_dev else "medium",
                    details={
                        "window_start": start_date.isoformat(),
                        "window_end": end_date.isoformat(),
                        "divergence_pct": current_divergence * 100,
                        "std_dev": std_dev,
                        "wallet_count": len(self.wallets),
                        "custodial_count": len(self.custodial_addresses)
                    }
                )
                self.flags.append(flag)
                print(f"🚩 Illusion Flag: {flag.flag_id} | Divergence: {current_divergence:.2%} | Std Dev: {flag.divergence_std:.2f}")


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def load_sample_data() -> CohortTracker:
    """Load sample data for testing."""
    tracker = CohortTracker()

    # Add some custodial addresses
    coinbase_prime = [
        "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",  # Example Coinbase Prime
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
    ]
    edgar_custodians = {
        "State Street": ["bc1qar0srrr7xfkvy5l643lydnw9re59gt0jwk5d88v"],
        "BNY Mellon": ["bc1q6589rwqj3567cds3d8j7xv8k38s0f5e407v43m"]
    }
    tracker.load_custodial_addresses(coinbase_prime, edgar_custodians)

    # Add sample wallets
    wallets = [
        Wallet(address="bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", balance_btc=150.0, labels={"coinbase:prime"}),
        Wallet(address="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", balance_btc=120.0, labels={"coinbase:prime"}),
        Wallet(address="bc1qar0srrr7xfkvy5l643lydnw9re59gt0jwk5d88v", balance_btc=200.0, labels={"state_street"}),
        Wallet(address="bc1q6589rwqj3567cds3d8j7xv8k38s0f5e407v43m", balance_btc=180.0, labels={"bny_mellon"}),
        Wallet(address="bc1qa55d87x5v2695x039w35020n8x586f0c5m6t4j", balance_btc=110.0, labels={"arkham:whale"}),
        Wallet(address="bc1qz0x6x5x5x5x5x5x5x5x5x5x5x5x5x5x5x5x5", balance_btc=95.0, labels=set()),
    ]

    for wallet in wallets:
        tracker.add_wallet(wallet)

    # Add sample flows (last 30 days)
    now = datetime.now()
    for i in range(30):
        day = now - timedelta(days=i)
        flows = [
            FlowRecord(
                wallet_address="bc1qa55d87x5v2695x039w35020n8x586f0c5m6t4j",
                amount_btc=5.0,
                timestamp=day,
                flow_type="inflow"
            ),
            FlowRecord(
                wallet_address="bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                amount_btc=10.0,
                timestamp=day,
                flow_type="inflow"
            ),
        ]
        for flow in flows:
            tracker.add_flow(flow)

    # Build entity cohorts
    tracker.build_entity_cohorts()

    return tracker


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    print("🚀 Stage 1: Whale Tracking & Illusion Detection")
    print("=" * 60)

    # Load sample data
    tracker = load_sample_data()

    print(f"\n📊 Loaded {len(tracker.wallets)} wallets")
    print(f"🏦 Loaded {len(tracker.custodial_addresses)} custodial addresses")
    print(f"👥 Built {len(tracker.entities)} entity cohorts")

    # Detect illusion events
    print("\n🔍 Detecting measurement illusion events...")
    tracker.detect_illusion_events(end_date=datetime.now(), window_days=30)

    if tracker.flags:
        print(f"\n⚠️  Found {len(tracker.flags)} illusion event(s):")
        for flag in tracker.flags:
            print(f"  - {flag.flag_id}: {flag.severity.upper()} severity")
            print(f"    Raw accumulation: {flag.raw_whale_accumulation:.2f} BTC")
            print(f"    Entity-adjusted: {flag.entity_adjusted_balance:.2f} BTC")
            print(f"    Divergence: {flag.divergence_std:.2f}σ")
    else:
        print("\n✅ No illusion events detected in the last 30 days.")

    print("\n📈 Sample Entity Breakdown:")
    for entity_id, entity in list(tracker.entities.items())[:3]:
        print(f"  - {entity.name}: {len(entity.wallet_addresses)} wallet(s), Custodial: {entity.is_custodial}")
