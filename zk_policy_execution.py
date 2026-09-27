"""
ZK Policy Execution: Verification Scope
=======================================

Purpose:
- Verify a zero-knowledge proof that a screening policy was executed
- Make the scope of that verification explicit and machine-checkable

Scope (what a successful verification establishes):

    VERIFIED(ZK_POLICY_EXECUTION)
    ⇒   VALID_PROOF
    ∧   PUBLIC_INPUTS_BOUND
    ∧   POLICY_VERSION_BOUND
    ∧   SANCTIONS_SNAPSHOT_BOUND
    ∧   COMMITTED_SCREENING_PREDICATES_SATISFIED

Non-scope (what it does NOT establish):

    VERIFIED(ZK_POLICY_EXECUTION)
    ⇏ VERIFIED(LEGAL_COMPLIANCE)
    ⇏ VERIFIED(OWNERSHIP)
    ⇏ VERIFIED(EXTERNAL_FACT_COMPLETENESS)
    ⇏ GOVERNMENT_APPROVAL

A proof shows that committed predicates hold over committed inputs. It says
nothing about whether those predicates capture the law, whether the prover
owns what it screened, whether the sanctions snapshot was complete or current
relative to the world, or whether any authority has approved anything.

The proof system itself is pluggable (`ProofBackend`); this module owns the
binding checks and the scope contract, not the cryptography.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional


# =============================================================================
# SCOPE
# =============================================================================

class Guarantee(Enum):
    """Properties established by VERIFIED(ZK_POLICY_EXECUTION)."""
    VALID_PROOF = "VALID_PROOF"
    PUBLIC_INPUTS_BOUND = "PUBLIC_INPUTS_BOUND"
    POLICY_VERSION_BOUND = "POLICY_VERSION_BOUND"
    SANCTIONS_SNAPSHOT_BOUND = "SANCTIONS_SNAPSHOT_BOUND"
    COMMITTED_SCREENING_PREDICATES_SATISFIED = "COMMITTED_SCREENING_PREDICATES_SATISFIED"


class NonGuarantee(Enum):
    """Properties NOT implied by VERIFIED(ZK_POLICY_EXECUTION)."""
    LEGAL_COMPLIANCE = "VERIFIED(LEGAL_COMPLIANCE)"
    OWNERSHIP = "VERIFIED(OWNERSHIP)"
    EXTERNAL_FACT_COMPLETENESS = "VERIFIED(EXTERNAL_FACT_COMPLETENESS)"
    GOVERNMENT_APPROVAL = "GOVERNMENT_APPROVAL"


ALL_GUARANTEES: FrozenSet[Guarantee] = frozenset(Guarantee)
ALL_NON_GUARANTEES: FrozenSet[NonGuarantee] = frozenset(NonGuarantee)


class ScopeError(Exception):
    """Raised when a caller tries to derive a non-guarantee from a ZK verification."""


# =============================================================================
# COMMITMENTS
# =============================================================================

def commit(value: Any) -> str:
    """Deterministic SHA-256 commitment over a JSON-serializable value."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass(frozen=True)
class PolicyExecutionStatement:
    """
    Public statement the proof is about.

    `public_inputs` is exactly what the circuit exposes. It must carry the
    policy version commitment, the sanctions snapshot commitment, the
    commitment to the screening predicates, and the predicate outcome.
    """
    public_inputs: Mapping[str, Any]

    POLICY_VERSION_KEY = "policy_version_commitment"
    SANCTIONS_SNAPSHOT_KEY = "sanctions_snapshot_commitment"
    PREDICATES_KEY = "screening_predicates_commitment"
    OUTCOME_KEY = "predicates_satisfied"

    def digest(self) -> str:
        return commit(dict(self.public_inputs))


@dataclass(frozen=True)
class VerifierExpectations:
    """What the verifier independently expects the proof to be bound to."""
    policy_version_commitment: str
    sanctions_snapshot_commitment: str
    screening_predicates_commitment: str
    public_inputs_digest: Optional[str] = None  # If set, full public inputs must match


@dataclass
class VerificationResult:
    """
    Outcome of verifying a ZK policy execution proof.

    `established` lists which guarantees held. `verified` is True only when
    all of them hold. Non-guarantees are never established by this result,
    regardless of `verified`.
    """
    established: FrozenSet[Guarantee]
    failures: List[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.established == ALL_GUARANTEES

    @property
    def not_established(self) -> FrozenSet[NonGuarantee]:
        return ALL_NON_GUARANTEES

    def implies(self, claim: Any) -> bool:
        """
        Whether this result supports `claim`.

        Guarantees are implied only when fully verified. Non-guarantees are
        never implied; asking for one raises ScopeError so the mistake is loud.
        """
        if isinstance(claim, NonGuarantee):
            raise ScopeError(
                f"VERIFIED(ZK_POLICY_EXECUTION) ⇏ {claim.value}; "
                f"establish it through a separate process"
            )
        if isinstance(claim, Guarantee):
            return self.verified and claim in self.established
        raise TypeError(f"Unknown claim type: {claim!r}")

    def summary(self) -> Dict[str, Any]:
        return {
            "VERIFIED(ZK_POLICY_EXECUTION)": self.verified,
            "established": sorted(g.value for g in self.established),
            "not_established": sorted(n.value for n in self.not_established),
            "failures": list(self.failures),
        }


# =============================================================================
# VERIFIER
# =============================================================================

# (proof, statement_digest) -> bool. Must bind the proof to the exact public inputs.
ProofBackend = Callable[[bytes, str], bool]


class ZkPolicyExecutionVerifier:
    """Checks each conjunct of VERIFIED(ZK_POLICY_EXECUTION) independently."""

    def __init__(self, backend: ProofBackend):
        self.backend = backend

    def verify(
        self,
        proof: bytes,
        statement: PolicyExecutionStatement,
        expected: VerifierExpectations,
    ) -> VerificationResult:
        established = set()
        failures: List[str] = []
        inputs = statement.public_inputs

        # VALID_PROOF: backend accepts the proof against the statement digest
        try:
            proof_ok = bool(self.backend(proof, statement.digest()))
        except Exception as exc:  # A crashing backend is a failed proof, not a pass
            proof_ok = False
            failures.append(f"VALID_PROOF: backend error: {exc}")
        if proof_ok:
            established.add(Guarantee.VALID_PROOF)
        elif not failures:
            failures.append("VALID_PROOF: backend rejected proof")

        # PUBLIC_INPUTS_BOUND: all required inputs present, and match full digest if pinned
        required = (
            PolicyExecutionStatement.POLICY_VERSION_KEY,
            PolicyExecutionStatement.SANCTIONS_SNAPSHOT_KEY,
            PolicyExecutionStatement.PREDICATES_KEY,
            PolicyExecutionStatement.OUTCOME_KEY,
        )
        missing = [k for k in required if k not in inputs]
        if missing:
            failures.append(f"PUBLIC_INPUTS_BOUND: missing {missing}")
        elif expected.public_inputs_digest and expected.public_inputs_digest != statement.digest():
            failures.append("PUBLIC_INPUTS_BOUND: public inputs digest mismatch")
        elif proof_ok:
            # Inputs are only bound if the proof was checked against them
            established.add(Guarantee.PUBLIC_INPUTS_BOUND)
        else:
            failures.append("PUBLIC_INPUTS_BOUND: no valid proof binds these inputs")

        bound = Guarantee.PUBLIC_INPUTS_BOUND in established

        # POLICY_VERSION_BOUND
        if bound and inputs.get(PolicyExecutionStatement.POLICY_VERSION_KEY) == expected.policy_version_commitment:
            established.add(Guarantee.POLICY_VERSION_BOUND)
        else:
            failures.append("POLICY_VERSION_BOUND: commitment mismatch or inputs unbound")

        # SANCTIONS_SNAPSHOT_BOUND
        if bound and inputs.get(PolicyExecutionStatement.SANCTIONS_SNAPSHOT_KEY) == expected.sanctions_snapshot_commitment:
            established.add(Guarantee.SANCTIONS_SNAPSHOT_BOUND)
        else:
            failures.append("SANCTIONS_SNAPSHOT_BOUND: commitment mismatch or inputs unbound")

        # COMMITTED_SCREENING_PREDICATES_SATISFIED: right predicates, and they held
        predicates_match = inputs.get(PolicyExecutionStatement.PREDICATES_KEY) == expected.screening_predicates_commitment
        satisfied = inputs.get(PolicyExecutionStatement.OUTCOME_KEY) is True
        if bound and predicates_match and satisfied:
            established.add(Guarantee.COMMITTED_SCREENING_PREDICATES_SATISFIED)
        else:
            failures.append(
                "COMMITTED_SCREENING_PREDICATES_SATISFIED: "
                f"bound={bound} predicates_match={predicates_match} satisfied={satisfied}"
            )

        return VerificationResult(established=frozenset(established), failures=failures)


# =============================================================================
# DEMO
# =============================================================================

def _mock_backend(proof: bytes, statement_digest: str) -> bool:
    """Stand-in for a real proof system: proof must be H(digest). NOT cryptographically meaningful."""
    return proof == hashlib.sha256(statement_digest.encode("utf-8")).digest()


if __name__ == "__main__":
    policy_c = commit({"policy": "screening-v3", "version": "3.1.0"})
    snapshot_c = commit({"list": "sanctions", "as_of": "2026-09-01"})
    predicates_c = commit(["counterparty_not_listed", "jurisdiction_not_embargoed"])

    statement = PolicyExecutionStatement(public_inputs={
        PolicyExecutionStatement.POLICY_VERSION_KEY: policy_c,
        PolicyExecutionStatement.SANCTIONS_SNAPSHOT_KEY: snapshot_c,
        PolicyExecutionStatement.PREDICATES_KEY: predicates_c,
        PolicyExecutionStatement.OUTCOME_KEY: True,
    })
    proof = hashlib.sha256(statement.digest().encode("utf-8")).digest()
    expected = VerifierExpectations(policy_c, snapshot_c, predicates_c)

    result = ZkPolicyExecutionVerifier(_mock_backend).verify(proof, statement, expected)
    print(json.dumps(result.summary(), indent=2, ensure_ascii=False))

    for non in NonGuarantee:
        try:
            result.implies(non)
        except ScopeError as exc:
            print(f"ScopeError: {exc}")
