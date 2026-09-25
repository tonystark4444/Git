"""
ProvenancePipeline: orchestrates the five stages end-to-end.

Non-negotiable invariant, per spec: the pipeline never short-circuits.
Even if signature verification fails, it still hashes the canonical
event, runs replay checks, and evaluates policy. This keeps the ledger
complete and lets downstream systems reason about *why* an event failed
rather than losing the event entirely — the property that makes this
suitable for a regulator-grade provenance system.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .canonicalizer import Canonicalizer
from .hasher import Hasher
from .models import PipelineResult
from .policy_evaluator import PolicyEvaluator
from .replay_engine import ReplayEngine
from .signature_verifier import SignatureVerifier


class ProvenancePipeline:
    def __init__(
        self,
        canonicalizer: Canonicalizer,
        hasher: Hasher,
        signature_verifier: SignatureVerifier,
        replay_engine: ReplayEngine,
        policy_evaluator: PolicyEvaluator,
    ):
        self._canonicalizer = canonicalizer
        self._hasher = hasher
        self._signature_verifier = signature_verifier
        self._replay_engine = replay_engine
        self._policy_evaluator = policy_evaluator

    def process(
        self,
        event_id: str,
        event_payload: Dict[str, Any],
        pre_state: Dict[str, Any],
        transition: Dict[str, Any],
        declared_post_state_hash: str,
        signature_b64: Optional[str],
        public_key_id: Optional[str],
    ) -> PipelineResult:
        # Stage 1: canonicalize. Every later stage depends on this, and
        # per contract it must succeed for any JSON-compatible payload.
        canonical = self._canonicalizer.canonicalize(event_payload)

        # Stage 2: hash. Always runs; never conditioned on anything else.
        digest = self._hasher.hash(canonical)

        # Stage 3: verify signature. A failure here is captured as data
        # (see SignatureVerificationResult) and does NOT gate stages 4-5.
        signature_result = self._signature_verifier.verify(
            canonical, signature_b64, public_key_id
        )

        # Stage 4: replay. Runs regardless of signature_result — this is
        # the "even if signature verification fails, you still ... run
        # replay checks" requirement, enforced structurally: there is no
        # branch here that skips this call.
        replay_result = self._replay_engine.replay(
            pre_state,
            transition,
            declared_post_state_hash,
            self._hasher,
            self._canonicalizer,
        )

        # Stage 5: policy. Runs regardless of both prior outcomes, and is
        # handed the raw evidence rather than a pre-collapsed boolean.
        policy_result = self._policy_evaluator.evaluate(signature_result, replay_result)

        return PipelineResult(
            event_id=event_id,
            received_at=datetime.now(timezone.utc),
            canonicalization=canonical,
            hash=digest,
            signature=signature_result,
            replay=replay_result,
            policy=policy_result,
        )
