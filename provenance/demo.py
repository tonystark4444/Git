"""
Runnable demonstration of the non-short-circuiting provenance pipeline.

Processes three variants of the same claim state transition:
  1. Properly signed, transition replays correctly       -> ALLOW
  2. Signature missing, transition still replays correctly -> FLAG
  3. Properly signed, but the declared post-state hash is wrong -> DENY

In every case the printed record shows a real hash and a real replay
outcome — nothing is skipped just because the signature check failed.

Run with: python3 -m provenance.demo
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from provenance import (
    DefaultPolicyEvaluator,
    DeterministicReplayEngine,
    Ed25519SignatureVerifier,
    JCSCanonicalizer,
    ProvenancePipeline,
    Sha256Hasher,
)


def claim_transition(pre_state, transition):
    return {
        "claim_id": pre_state["claim_id"],
        "status": transition.get("new_status", pre_state["status"]),
        "reserved_amount": pre_state["reserved_amount"] + transition.get("delta", 0),
        "revision": pre_state["revision"] + 1,
    }


def print_result(label: str, result) -> None:
    print(f"\n--- {label} ---")
    print(f"event_id:        {result.event_id}")
    print(f"hash:            {result.hash.digest_hex}")
    print(f"signature:       {result.signature.status.value} ({result.signature.reason or 'ok'})")
    print(f"replay:          {result.replay.status.value} ({result.replay.reason or 'matched'})")
    print(f"policy decision: {result.policy.decision.value}")
    if result.policy.reasons:
        for reason in result.policy.reasons:
            print(f"  - {reason}")
    print(f"fully verified:  {result.is_fully_verified}")


def main() -> None:
    private_key = Ed25519PrivateKey.generate()
    key_store = {"signer-1": private_key.public_key().public_bytes_raw()}

    canonicalizer = JCSCanonicalizer()
    hasher = Sha256Hasher()
    signature_verifier = Ed25519SignatureVerifier(key_resolver=lambda kid: key_store.get(kid))
    replay_engine = DeterministicReplayEngine(claim_transition)
    policy_evaluator = DefaultPolicyEvaluator()

    pipeline = ProvenancePipeline(
        canonicalizer=canonicalizer,
        hasher=hasher,
        signature_verifier=signature_verifier,
        replay_engine=replay_engine,
        policy_evaluator=policy_evaluator,
    )

    pre_state = {"claim_id": "CLM-1", "status": "OPEN", "reserved_amount": 1000, "revision": 0}
    transition = {"new_status": "OPEN", "delta": 250}
    event_payload = {"claim_id": "CLM-1", "transition": transition}
    correct_hash = hasher.hash(
        canonicalizer.canonicalize(claim_transition(pre_state, transition))
    ).digest_hex

    def sign(payload) -> str:
        canonical = canonicalizer.canonicalize(payload)
        return base64.b64encode(private_key.sign(canonical.canonical_bytes)).decode("ascii")

    allow_result = pipeline.process(
        event_id="evt-allow",
        event_payload=event_payload,
        pre_state=pre_state,
        transition=transition,
        declared_post_state_hash=correct_hash,
        signature_b64=sign(event_payload),
        public_key_id="signer-1",
    )
    print_result("Case 1: valid signature, correct replay", allow_result)

    flag_result = pipeline.process(
        event_id="evt-flag",
        event_payload=event_payload,
        pre_state=pre_state,
        transition=transition,
        declared_post_state_hash=correct_hash,
        signature_b64=None,
        public_key_id=None,
    )
    print_result("Case 2: NO signature, correct replay", flag_result)

    deny_result = pipeline.process(
        event_id="evt-deny",
        event_payload=event_payload,
        pre_state=pre_state,
        transition=transition,
        declared_post_state_hash="0" * 64,
        signature_b64=sign(event_payload),
        public_key_id="signer-1",
    )
    print_result("Case 3: valid signature, WRONG declared hash", deny_result)


if __name__ == "__main__":
    main()
