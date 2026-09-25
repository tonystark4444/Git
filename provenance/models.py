"""
Shared data model for the provenance pipeline.

Every stage of the pipeline (canonicalize -> hash -> verify signature ->
replay -> evaluate policy) reports its outcome as one of the frozen
dataclasses below instead of raising. That is deliberate: the pipeline's
whole point is to produce a complete, inspectable record of what happened
to an event, including every way it failed, rather than to raise/abort
and lose that information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class VerificationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    ERROR = "error"


class ReplayStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    ERROR = "error"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    FLAG = "flag"
    DENY = "deny"


@dataclass(frozen=True)
class CanonicalizationResult:
    canonical_bytes: bytes
    algorithm: str = "jcs"


@dataclass(frozen=True)
class HashResult:
    digest_hex: str
    algorithm: str = "sha256"


@dataclass(frozen=True)
class SignatureVerificationResult:
    status: VerificationStatus
    signer_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ReplayResult:
    status: ReplayStatus
    expected_hash: Optional[str] = None
    computed_hash: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class PolicyEvaluation:
    decision: PolicyDecision
    reasons: List[str] = field(default_factory=list)
    triggered_rules: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PipelineResult:
    """The complete, ledger-ready record for one processed event.

    Every field is always populated — there is no code path that leaves
    `replay` or `policy` unset because `signature` failed. That invariant
    is what makes this record fit for regulator-grade provenance: a
    reviewer can always answer "why was this event denied/flagged" from
    this object alone, never from an absence of one.
    """

    event_id: str
    received_at: datetime
    canonicalization: CanonicalizationResult
    hash: HashResult
    signature: SignatureVerificationResult
    replay: ReplayResult
    policy: PolicyEvaluation

    @property
    def is_fully_verified(self) -> bool:
        return (
            self.signature.status == VerificationStatus.VALID
            and self.replay.status == ReplayStatus.MATCH
            and self.policy.decision == PolicyDecision.ALLOW
        )
