"""
Replay engine: interface contract + reference implementation.

This is the stage that turns "the state transition math doesn't check
out" into a first-class, queryable fact. It runs unconditionally,
regardless of whether the event's signature verified — an unsigned or
badly-signed event with a state transition that replays correctly is a
very different finding from a validly-signed event whose transition
doesn't reproduce its own declared result, and the pipeline must be able
to tell those apart.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Protocol

from .canonicalizer import Canonicalizer
from .hasher import Hasher
from .models import ReplayResult, ReplayStatus

# A pure function: (pre_state, transition_payload) -> post_state.
# MUST NOT perform I/O, read the wall clock, or use randomness — replay
# determinism depends entirely on this function being a pure computation
# over its two arguments.
StateTransitionFn = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


class ReplayEngine(Protocol):
    """
    Contract: independently recompute the declared post-state hash by
    deterministically applying an event's transition to its declared
    pre-state, then compare it to the hash asserted on the event.

    Requirements for any implementation:
    - Runs unconditionally. The pipeline calls this stage regardless of
      the signature verification outcome; the engine itself has no
      dependency on that outcome either.
    - Never raises. Any internal failure (a transition function that
      throws, a canonicalization/hash error on the recomputed state)
      resolves to ReplayStatus.ERROR with a reason, not an exception.
    """

    def replay(
        self,
        pre_state: Dict[str, Any],
        transition: Dict[str, Any],
        declared_post_state_hash: str,
        hasher: Hasher,
        canonicalizer: Canonicalizer,
    ) -> ReplayResult:
        ...


class DeterministicReplayEngine:
    """Reference implementation, parameterized by a pure transition function."""

    def __init__(self, transition_fn: StateTransitionFn):
        self._transition_fn = transition_fn

    def replay(
        self,
        pre_state: Dict[str, Any],
        transition: Dict[str, Any],
        declared_post_state_hash: str,
        hasher: Hasher,
        canonicalizer: Canonicalizer,
    ) -> ReplayResult:
        try:
            recomputed_post_state = self._transition_fn(pre_state, transition)
        except Exception as exc:  # noqa: BLE001 - deliberate: never propagate
            return ReplayResult(
                status=ReplayStatus.ERROR,
                expected_hash=declared_post_state_hash,
                reason=f"Transition function raised during replay: {exc}",
            )

        try:
            canonical = canonicalizer.canonicalize(recomputed_post_state)
            computed = hasher.hash(canonical)
        except Exception as exc:  # noqa: BLE001 - deliberate: never propagate
            return ReplayResult(
                status=ReplayStatus.ERROR,
                expected_hash=declared_post_state_hash,
                reason=f"Hashing recomputed state failed: {exc}",
            )

        if computed.digest_hex == declared_post_state_hash:
            return ReplayResult(
                status=ReplayStatus.MATCH,
                expected_hash=declared_post_state_hash,
                computed_hash=computed.digest_hex,
            )

        return ReplayResult(
            status=ReplayStatus.MISMATCH,
            expected_hash=declared_post_state_hash,
            computed_hash=computed.digest_hex,
            reason="Recomputed post-state hash does not match the hash declared on the event.",
        )
