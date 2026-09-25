"""
provenance: a regulator-grade, non-short-circuiting event verification
pipeline.

Five stages, each with an interface contract (a `Protocol`) and a
reference implementation:

    canonicalizer.py       Canonicalizer / JCSCanonicalizer
    hasher.py               Hasher / Sha256Hasher
    signature_verifier.py   SignatureVerifier / Ed25519SignatureVerifier
    replay_engine.py        ReplayEngine / DeterministicReplayEngine
    policy_evaluator.py     PolicyEvaluator / DefaultPolicyEvaluator

wired together by pipeline.ProvenancePipeline, which enforces the core
invariant: no stage after canonicalization is ever skipped because an
earlier stage failed.
"""

from .canonicalizer import Canonicalizer, JCSCanonicalizer
from .hasher import Hasher, Sha256Hasher
from .models import (
    CanonicalizationResult,
    HashResult,
    PipelineResult,
    PolicyDecision,
    PolicyEvaluation,
    ReplayResult,
    ReplayStatus,
    SignatureVerificationResult,
    VerificationStatus,
)
from .pipeline import ProvenancePipeline
from .policy_evaluator import DefaultPolicyEvaluator, PolicyEvaluator
from .replay_engine import DeterministicReplayEngine, ReplayEngine, StateTransitionFn
from .signature_verifier import Ed25519SignatureVerifier, SignatureVerifier

__all__ = [
    "Canonicalizer",
    "JCSCanonicalizer",
    "Hasher",
    "Sha256Hasher",
    "SignatureVerifier",
    "Ed25519SignatureVerifier",
    "ReplayEngine",
    "DeterministicReplayEngine",
    "StateTransitionFn",
    "PolicyEvaluator",
    "DefaultPolicyEvaluator",
    "ProvenancePipeline",
    "CanonicalizationResult",
    "HashResult",
    "SignatureVerificationResult",
    "VerificationStatus",
    "ReplayResult",
    "ReplayStatus",
    "PolicyEvaluation",
    "PolicyDecision",
    "PipelineResult",
]
