"""
Canonicalizer: interface contract + reference implementation.

Every other stage in the pipeline (hashing, replay, signing) operates on
canonical bytes rather than the raw payload, so that two semantically
identical events — regardless of key order, incidental whitespace, or
which language produced them — hash and sign identically.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Protocol

from .models import CanonicalizationResult


class Canonicalizer(Protocol):
    """
    Contract: turn a JSON-compatible payload into a deterministic byte
    sequence.

    Requirements for any implementation:
    - Deterministic: the same logical payload always produces the exact
      same bytes, independent of dict insertion order.
    - Total: MUST NOT raise for any JSON-compatible payload (dict, list,
      str, int, float, bool, None). Canonicalization is the one stage
      every downstream stage depends on; if it can fail unpredictably,
      nothing built on top of it is trustworthy.
    - Self-describing: the result records which algorithm produced it,
      so verifiers on either side of a signature can confirm they agree.
    """

    def canonicalize(self, payload: Dict[str, Any]) -> CanonicalizationResult:
        ...


class JCSCanonicalizer:
    """Reference implementation: RFC 8785-style canonical JSON.

    Known simplification vs. full RFC 8785: this uses Python's own
    float/int formatting rather than the ECMAScript Number-to-String
    algorithm the RFC specifies. Two implementations in different
    languages hashing the same payload MUST confirm they agree on
    numeric formatting (or restrict payloads to integers/strings) before
    relying on cross-language hash equality.
    """

    algorithm = "jcs"

    def canonicalize(self, payload: Dict[str, Any]) -> CanonicalizationResult:
        canonical_str = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return CanonicalizationResult(
            canonical_bytes=canonical_str.encode("utf-8"),
            algorithm=self.algorithm,
        )
