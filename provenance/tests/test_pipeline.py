"""
Tests proving the pipeline's core, non-negotiable property: it never
short-circuits. A failed signature must not prevent hashing, replay, or
policy evaluation from running and being recorded.
"""

from __future__ import annotations

import base64
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from provenance import (
    DefaultPolicyEvaluator,
    DeterministicReplayEngine,
    Ed25519SignatureVerifier,
    JCSCanonicalizer,
    PolicyDecision,
    ProvenancePipeline,
    ReplayStatus,
    Sha256Hasher,
    VerificationStatus,
)


def claim_transition(pre_state, transition):
    """A minimal, deterministic entity transition: apply a delta to a
    claim's reserved amount and bump its revision counter."""
    return {
        "claim_id": pre_state["claim_id"],
        "status": transition.get("new_status", pre_state["status"]),
        "reserved_amount": pre_state["reserved_amount"] + transition.get("delta", 0),
        "revision": pre_state["revision"] + 1,
    }


def raising_transition(pre_state, transition):
    raise RuntimeError("engine misconfigured")


class ProvenancePipelineTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        public_bytes = self.private_key.public_key().public_bytes_raw()
        self.key_store = {"signer-1": public_bytes}

        self.canonicalizer = JCSCanonicalizer()
        self.hasher = Sha256Hasher()
        self.signature_verifier = Ed25519SignatureVerifier(
            key_resolver=lambda kid: self.key_store.get(kid)
        )
        self.replay_engine = DeterministicReplayEngine(claim_transition)
        self.policy_evaluator = DefaultPolicyEvaluator()

        self.pipeline = ProvenancePipeline(
            canonicalizer=self.canonicalizer,
            hasher=self.hasher,
            signature_verifier=self.signature_verifier,
            replay_engine=self.replay_engine,
            policy_evaluator=self.policy_evaluator,
        )

        self.pre_state = {
            "claim_id": "CLM-1",
            "status": "OPEN",
            "reserved_amount": 1000,
            "revision": 0,
        }
        self.transition = {"new_status": "OPEN", "delta": 250}
        self.expected_post_state = claim_transition(self.pre_state, self.transition)
        self.declared_hash = self.hasher.hash(
            self.canonicalizer.canonicalize(self.expected_post_state)
        ).digest_hex
        self.event_payload = {"claim_id": "CLM-1", "transition": self.transition}

    def _sign(self, payload) -> str:
        canonical = self.canonicalizer.canonicalize(payload)
        signature = self.private_key.sign(canonical.canonical_bytes)
        return base64.b64encode(signature).decode("ascii")

    def test_valid_signature_and_matching_replay_allows(self):
        signature_b64 = self._sign(self.event_payload)

        result = self.pipeline.process(
            event_id="evt-1",
            event_payload=self.event_payload,
            pre_state=self.pre_state,
            transition=self.transition,
            declared_post_state_hash=self.declared_hash,
            signature_b64=signature_b64,
            public_key_id="signer-1",
        )

        self.assertEqual(result.signature.status, VerificationStatus.VALID)
        self.assertEqual(result.replay.status, ReplayStatus.MATCH)
        self.assertEqual(result.policy.decision, PolicyDecision.ALLOW)
        self.assertTrue(result.is_fully_verified)

    def test_missing_signature_still_hashes_and_replays_and_flags(self):
        """The load-bearing test: no signature at all must not skip any
        later stage. Hash and replay must still be fully populated, and
        the event must be flagged (not silently dropped)."""
        result = self.pipeline.process(
            event_id="evt-2",
            event_payload=self.event_payload,
            pre_state=self.pre_state,
            transition=self.transition,
            declared_post_state_hash=self.declared_hash,
            signature_b64=None,
            public_key_id=None,
        )

        self.assertEqual(result.signature.status, VerificationStatus.MISSING)
        # Hash was still computed:
        self.assertTrue(len(result.hash.digest_hex) == 64)
        # Replay still ran and matched, independent of the missing signature:
        self.assertEqual(result.replay.status, ReplayStatus.MATCH)
        self.assertEqual(result.replay.computed_hash, self.declared_hash)
        # Policy still evaluated, and recorded *why* it flagged:
        self.assertEqual(result.policy.decision, PolicyDecision.FLAG)
        self.assertIn("signature_not_valid", result.policy.triggered_rules)
        self.assertFalse(result.is_fully_verified)

    def test_invalid_signature_still_hashes_and_replays_and_flags(self):
        """A signature that fails cryptographic verification (as opposed
        to being merely absent) must produce the same non-short-circuit
        behavior as a missing one."""
        tampered_signature = self._sign({"claim_id": "CLM-1", "transition": {"delta": 999}})

        result = self.pipeline.process(
            event_id="evt-3",
            event_payload=self.event_payload,
            pre_state=self.pre_state,
            transition=self.transition,
            declared_post_state_hash=self.declared_hash,
            signature_b64=tampered_signature,
            public_key_id="signer-1",
        )

        self.assertEqual(result.signature.status, VerificationStatus.INVALID)
        self.assertEqual(result.replay.status, ReplayStatus.MATCH)
        self.assertEqual(result.policy.decision, PolicyDecision.FLAG)

    def test_unknown_key_id_errors_without_raising_and_pipeline_continues(self):
        result = self.pipeline.process(
            event_id="evt-4",
            event_payload=self.event_payload,
            pre_state=self.pre_state,
            transition=self.transition,
            declared_post_state_hash=self.declared_hash,
            signature_b64=self._sign(self.event_payload),
            public_key_id="unknown-signer",
        )

        self.assertEqual(result.signature.status, VerificationStatus.ERROR)
        self.assertIsNotNone(result.signature.reason)
        # Replay and policy still ran despite the key-resolution error:
        self.assertEqual(result.replay.status, ReplayStatus.MATCH)
        self.assertEqual(result.policy.decision, PolicyDecision.FLAG)

    def test_replay_mismatch_denies_even_with_valid_signature(self):
        """State integrity outranks authenticity: a validly-signed event
        whose transition doesn't reproduce its declared post-state hash
        must be denied, not merely flagged."""
        signature_b64 = self._sign(self.event_payload)
        wrong_declared_hash = "0" * 64

        result = self.pipeline.process(
            event_id="evt-5",
            event_payload=self.event_payload,
            pre_state=self.pre_state,
            transition=self.transition,
            declared_post_state_hash=wrong_declared_hash,
            signature_b64=signature_b64,
            public_key_id="signer-1",
        )

        self.assertEqual(result.signature.status, VerificationStatus.VALID)
        self.assertEqual(result.replay.status, ReplayStatus.MISMATCH)
        self.assertEqual(result.policy.decision, PolicyDecision.DENY)

    def test_both_signature_and_replay_fail_denies(self):
        wrong_declared_hash = "0" * 64

        result = self.pipeline.process(
            event_id="evt-6",
            event_payload=self.event_payload,
            pre_state=self.pre_state,
            transition=self.transition,
            declared_post_state_hash=wrong_declared_hash,
            signature_b64=None,
            public_key_id=None,
        )

        self.assertEqual(result.signature.status, VerificationStatus.MISSING)
        self.assertEqual(result.replay.status, ReplayStatus.MISMATCH)
        self.assertEqual(result.policy.decision, PolicyDecision.DENY)
        self.assertIn("signature_not_valid", result.policy.triggered_rules)
        self.assertIn("replay_not_matched", result.policy.triggered_rules)

    def test_transition_function_raising_surfaces_as_replay_error_not_exception(self):
        engine = DeterministicReplayEngine(raising_transition)
        pipeline = ProvenancePipeline(
            canonicalizer=self.canonicalizer,
            hasher=self.hasher,
            signature_verifier=self.signature_verifier,
            replay_engine=engine,
            policy_evaluator=self.policy_evaluator,
        )

        # Must not raise, despite the transition function throwing:
        result = pipeline.process(
            event_id="evt-7",
            event_payload=self.event_payload,
            pre_state=self.pre_state,
            transition=self.transition,
            declared_post_state_hash=self.declared_hash,
            signature_b64=self._sign(self.event_payload),
            public_key_id="signer-1",
        )

        self.assertEqual(result.replay.status, ReplayStatus.ERROR)
        self.assertIn("engine misconfigured", result.replay.reason)
        self.assertEqual(result.policy.decision, PolicyDecision.DENY)

    def test_canonicalization_is_order_independent(self):
        a = {"z": 1, "a": {"y": 2, "x": 3}}
        b = {"a": {"x": 3, "y": 2}, "z": 1}

        canon_a = self.canonicalizer.canonicalize(a)
        canon_b = self.canonicalizer.canonicalize(b)

        self.assertEqual(canon_a.canonical_bytes, canon_b.canonical_bytes)
        self.assertEqual(
            self.hasher.hash(canon_a).digest_hex,
            self.hasher.hash(canon_b).digest_hex,
        )


if __name__ == "__main__":
    unittest.main()
