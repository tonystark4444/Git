"""
Hasher: interface contract + reference implementation.

This is the one pipeline stage that is never allowed to fail. If
canonicalization succeeded, hashing succeeds — there is no such thing as
an event that couldn't be hashed, only events that failed signature
verification or replay. That asymmetry is intentional: the hash is the
ledger's anchor, and it must exist for every event regardless of any
other outcome.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from .models import CanonicalizationResult, HashResult


class Hasher(Protocol):
    """
    Contract: hash canonical bytes into a hex digest.

    Requirements for any implementation:
    - Pure and total: given the same canonical bytes, always the same
      digest; must not raise for any byte string.
    - Self-describing: records which algorithm produced the digest, so
      that a replay engine or auditor recomputing years later knows how.
    """

    def hash(self, canonical: CanonicalizationResult) -> HashResult:
        ...


class Sha256Hasher:
    algorithm = "sha256"

    def hash(self, canonical: CanonicalizationResult) -> HashResult:
        digest = hashlib.sha256(canonical.canonical_bytes).hexdigest()
        return HashResult(digest_hex=digest, algorithm=self.algorithm)
