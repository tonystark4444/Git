"""
Signature verifier: interface contract + reference implementation.

Critical pipeline invariant: this stage MUST NEVER raise, and its result
MUST NEVER be used upstream to skip hashing, replay, or policy
evaluation. A failed signature is data to be recorded and reasoned
about later (by the policy evaluator), not an event to short-circuit on.
"""

from __future__ import annotations

import base64
from typing import Callable, Optional, Protocol

from .models import CanonicalizationResult, SignatureVerificationResult, VerificationStatus

KeyResolver = Callable[[str], Optional[bytes]]


class SignatureVerifier(Protocol):
    """
    Contract: verify a detached signature over canonical event bytes.

    Requirements for any implementation:
    - Never raises. Every failure mode — missing signature, unknown key,
      malformed base64, cryptographic mismatch, unsupported algorithm —
      resolves to a SignatureVerificationResult with a status and a
      human-readable `reason`, never to a propagated exception.
    - Never gates. The caller (the pipeline) is responsible for running
      later stages unconditionally; this method's job is only to report,
      not to decide what happens next.
    """

    def verify(
        self,
        canonical: CanonicalizationResult,
        signature_b64: Optional[str],
        public_key_id: Optional[str],
    ) -> SignatureVerificationResult:
        ...


class Ed25519SignatureVerifier:
    """Reference implementation using Ed25519 and a pluggable key resolver.

    `key_resolver(public_key_id)` returns the raw 32-byte Ed25519 public
    key for that id, or None if the id is unknown. Swap in a resolver
    backed by a KMS, a trust store, or a static test fixture as needed.
    """

    def __init__(self, key_resolver: KeyResolver):
        self._key_resolver = key_resolver

    def verify(
        self,
        canonical: CanonicalizationResult,
        signature_b64: Optional[str],
        public_key_id: Optional[str],
    ) -> SignatureVerificationResult:
        if not signature_b64:
            return SignatureVerificationResult(
                status=VerificationStatus.MISSING,
                signer_id=public_key_id,
                reason="No signature present on event.",
            )

        if not public_key_id:
            return SignatureVerificationResult(
                status=VerificationStatus.ERROR,
                reason="No public_key_id supplied; cannot resolve verification key.",
            )

        try:
            key_bytes = self._key_resolver(public_key_id)
        except Exception as exc:  # noqa: BLE001 - deliberate: never propagate
            return SignatureVerificationResult(
                status=VerificationStatus.ERROR,
                signer_id=public_key_id,
                reason=f"Key resolution raised: {exc}",
            )

        if key_bytes is None:
            return SignatureVerificationResult(
                status=VerificationStatus.ERROR,
                signer_id=public_key_id,
                reason=f"Unknown public_key_id: {public_key_id!r}",
            )

        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            signature = base64.b64decode(signature_b64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(key_bytes)

            try:
                public_key.verify(signature, canonical.canonical_bytes)
            except InvalidSignature:
                return SignatureVerificationResult(
                    status=VerificationStatus.INVALID,
                    signer_id=public_key_id,
                    reason="Signature does not match canonical event bytes.",
                )

            return SignatureVerificationResult(
                status=VerificationStatus.VALID,
                signer_id=public_key_id,
            )

        except Exception as exc:  # noqa: BLE001 - deliberate: never propagate
            return SignatureVerificationResult(
                status=VerificationStatus.ERROR,
                signer_id=public_key_id,
                reason=f"Verification error: {exc}",
            )
