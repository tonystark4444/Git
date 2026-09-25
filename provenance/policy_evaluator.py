"""
Policy evaluator: interface contract + reference implementation.

This is the one stage whose job is explicitly to turn raw evidence
(signature status, replay status) into an actionable decision. It still
runs unconditionally like every other stage, and — critically — it is
handed the raw evidence rather than a pre-collapsed boolean, so the
reasons behind ALLOW/FLAG/DENY are always reconstructable from the
PipelineResult alone.
"""

from __future__ import annotations

from typing import List, Protocol

from .models import (
    PolicyDecision,
    PolicyEvaluation,
    ReplayResult,
    ReplayStatus,
    SignatureVerificationResult,
    VerificationStatus,
)


class PolicyEvaluator(Protocol):
    """
    Contract: given the signature and replay evidence for an event
    (whatever it is, including failures), produce a PolicyDecision plus
    the reasons and rule names that led to it.

    Requirements for any implementation:
    - Runs unconditionally, on whatever evidence it is given — it does
      not get to decline to evaluate because upstream evidence looks bad.
    - Never raises. An evaluator that cannot reason about an event is
      itself a finding (DENY with a reason), not a null result — a
      swallowed exception here would silently drop the one stage meant
      to make failures actionable.
    """

    def evaluate(
        self,
        signature: SignatureVerificationResult,
        replay: ReplayResult,
    ) -> PolicyEvaluation:
        ...


class DefaultPolicyEvaluator:
    """Reference implementation encoding a conservative default policy:

    - Valid signature + matching replay          -> ALLOW
    - Replay mismatch/error (any signature state) -> DENY
      A ledger that cannot reproduce its own declared state transition
      cannot be trusted, even if the event was validly signed — signing
      an internally-inconsistent transition doesn't make it consistent.
    - Replay matches but signature missing/invalid/error -> FLAG
      The state transition is provably correct; only attribution or
      authenticity is in question, which merits review rather than an
      outright deny.
    """

    def evaluate(
        self,
        signature: SignatureVerificationResult,
        replay: ReplayResult,
    ) -> PolicyEvaluation:
        try:
            reasons: List[str] = []
            triggered: List[str] = []

            signature_ok = signature.status == VerificationStatus.VALID
            replay_ok = replay.status == ReplayStatus.MATCH

            if not signature_ok:
                triggered.append("signature_not_valid")
                detail = f": {signature.reason}" if signature.reason else ""
                reasons.append(f"Signature status={signature.status.value}{detail}")

            if not replay_ok:
                triggered.append("replay_not_matched")
                detail = f": {replay.reason}" if replay.reason else ""
                reasons.append(f"Replay status={replay.status.value}{detail}")

            if signature_ok and replay_ok:
                return PolicyEvaluation(decision=PolicyDecision.ALLOW)

            if not replay_ok:
                return PolicyEvaluation(
                    decision=PolicyDecision.DENY,
                    reasons=reasons,
                    triggered_rules=triggered,
                )

            return PolicyEvaluation(
                decision=PolicyDecision.FLAG,
                reasons=reasons,
                triggered_rules=triggered,
            )

        except Exception as exc:  # noqa: BLE001 - deliberate: never propagate
            return PolicyEvaluation(
                decision=PolicyDecision.DENY,
                reasons=[f"Policy evaluator raised internally: {exc}"],
                triggered_rules=["policy_evaluator_error"],
            )
