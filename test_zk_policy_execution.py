import hashlib
import unittest

from zk_policy_execution import (
    ALL_GUARANTEES,
    ALL_NON_GUARANTEES,
    Guarantee,
    NonGuarantee,
    PolicyExecutionStatement,
    ScopeError,
    VerifierExpectations,
    ZkPolicyExecutionVerifier,
    _mock_backend,
    commit,
)

S = PolicyExecutionStatement
POLICY = commit({"policy": "p", "version": "1"})
SNAPSHOT = commit({"snapshot": "2026-09-01"})
PREDICATES = commit(["not_listed"])


def make(overrides=None, drop=None):
    inputs = {
        S.POLICY_VERSION_KEY: POLICY,
        S.SANCTIONS_SNAPSHOT_KEY: SNAPSHOT,
        S.PREDICATES_KEY: PREDICATES,
        S.OUTCOME_KEY: True,
    }
    inputs.update(overrides or {})
    for k in drop or []:
        inputs.pop(k)
    statement = S(public_inputs=inputs)
    proof = hashlib.sha256(statement.digest().encode("utf-8")).digest()
    return proof, statement


EXPECTED = VerifierExpectations(POLICY, SNAPSHOT, PREDICATES)
VERIFIER = ZkPolicyExecutionVerifier(_mock_backend)


class TestGuarantees(unittest.TestCase):
    def test_all_conjuncts_hold(self):
        result = VERIFIER.verify(*make(), EXPECTED)
        self.assertTrue(result.verified)
        self.assertEqual(result.established, ALL_GUARANTEES)
        for g in Guarantee:
            self.assertTrue(result.implies(g))

    def test_invalid_proof_establishes_nothing(self):
        _, statement = make()
        result = VERIFIER.verify(b"bogus", statement, EXPECTED)
        self.assertFalse(result.verified)
        self.assertEqual(result.established, frozenset())

    def test_backend_exception_is_failure(self):
        def boom(proof, digest):
            raise RuntimeError("bad curve point")
        result = ZkPolicyExecutionVerifier(boom).verify(*make(), EXPECTED)
        self.assertNotIn(Guarantee.VALID_PROOF, result.established)
        self.assertFalse(result.verified)

    def test_proof_for_other_inputs_rejected(self):
        proof, _ = make()
        _, tampered = make({S.OUTCOME_KEY: False})
        result = VERIFIER.verify(proof, tampered, EXPECTED)
        self.assertFalse(result.verified)

    def test_missing_public_input(self):
        result = VERIFIER.verify(*make(drop=[S.SANCTIONS_SNAPSHOT_KEY]), EXPECTED)
        self.assertIn(Guarantee.VALID_PROOF, result.established)
        self.assertNotIn(Guarantee.PUBLIC_INPUTS_BOUND, result.established)
        self.assertFalse(result.verified)

    def test_pinned_digest_mismatch(self):
        proof, statement = make()
        pinned = VerifierExpectations(POLICY, SNAPSHOT, PREDICATES, public_inputs_digest="00" * 32)
        result = VERIFIER.verify(proof, statement, pinned)
        self.assertNotIn(Guarantee.PUBLIC_INPUTS_BOUND, result.established)

    def test_wrong_policy_version(self):
        result = VERIFIER.verify(*make({S.POLICY_VERSION_KEY: commit("old")}), EXPECTED)
        self.assertNotIn(Guarantee.POLICY_VERSION_BOUND, result.established)
        self.assertFalse(result.verified)
        self.assertFalse(result.implies(Guarantee.VALID_PROOF))

    def test_wrong_sanctions_snapshot(self):
        result = VERIFIER.verify(*make({S.SANCTIONS_SNAPSHOT_KEY: commit("stale")}), EXPECTED)
        self.assertNotIn(Guarantee.SANCTIONS_SNAPSHOT_BOUND, result.established)
        self.assertFalse(result.verified)

    def test_predicates_not_satisfied(self):
        result = VERIFIER.verify(*make({S.OUTCOME_KEY: False}), EXPECTED)
        self.assertNotIn(Guarantee.COMMITTED_SCREENING_PREDICATES_SATISFIED, result.established)
        self.assertFalse(result.verified)

    def test_truthy_outcome_is_not_true(self):
        result = VERIFIER.verify(*make({S.OUTCOME_KEY: 1}), EXPECTED)
        self.assertNotIn(Guarantee.COMMITTED_SCREENING_PREDICATES_SATISFIED, result.established)

    def test_different_predicates(self):
        result = VERIFIER.verify(*make({S.PREDICATES_KEY: commit(["weaker"])}), EXPECTED)
        self.assertNotIn(Guarantee.COMMITTED_SCREENING_PREDICATES_SATISFIED, result.established)


class TestNonGuarantees(unittest.TestCase):
    def test_verified_does_not_imply_non_guarantees(self):
        result = VERIFIER.verify(*make(), EXPECTED)
        self.assertTrue(result.verified)
        self.assertEqual(result.not_established, ALL_NON_GUARANTEES)
        for n in NonGuarantee:
            with self.assertRaises(ScopeError):
                result.implies(n)

    def test_non_guarantees_listed(self):
        self.assertEqual(
            {n.value for n in NonGuarantee},
            {
                "VERIFIED(LEGAL_COMPLIANCE)",
                "VERIFIED(OWNERSHIP)",
                "VERIFIED(EXTERNAL_FACT_COMPLETENESS)",
                "GOVERNMENT_APPROVAL",
            },
        )

    def test_summary_reports_both_sides(self):
        summary = VERIFIER.verify(*make(), EXPECTED).summary()
        self.assertTrue(summary["VERIFIED(ZK_POLICY_EXECUTION)"])
        self.assertEqual(len(summary["established"]), 5)
        self.assertEqual(len(summary["not_established"]), 4)


if __name__ == "__main__":
    unittest.main()
