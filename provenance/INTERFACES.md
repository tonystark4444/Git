# Provenance Pipeline — Interface Contracts

This document specifies the five component interfaces of the provenance
verification pipeline, independent of any programming language. It is
written for implementers building a conformant component in a different
language/runtime than the Python reference implementation in this
directory (`canonicalizer.py`, `hasher.py`, `signature_verifier.py`,
`replay_engine.py`, `policy_evaluator.py`, `pipeline.py`).

A component conforms to its contract if and only if it satisfies every
**Requirement** below and passes every row of the **Conformance Matrix**
(§8).

---

## 1. Core Invariant

> The pipeline never short-circuits. Even if signature verification
> fails, it still hashes the canonical event, runs replay checks, and
> evaluates policy.

This is not a suggestion for the orchestrator alone — it constrains
every component's error-handling behavior:

- No component in this pipeline may **raise/throw** as its way of
  reporting a failure. Every failure mode (missing input, malformed
  input, cryptographic mismatch, internal fault) must be represented as
  a **value** in that component's result type, with enough detail
  (a status code + a human-readable reason) for a downstream reader to
  understand what happened without re-running anything.
- No component may consult another component's *result* to decide
  whether to run. Each stage receives the inputs it needs and always
  produces an output.
- The one exception is the *values* flowing forward: the Policy
  Evaluator's decision is explicitly a function of the Signature
  Verifier's and Replay Engine's outputs — but it still must evaluate
  and produce a decision no matter what those inputs contain, including
  when both indicate failure.

The reason this matters: this pipeline is a regulator-grade provenance
system. An event whose signature fails to verify is not noise to be
discarded — it is exactly the kind of event a reviewer most needs a
complete record of (was the state transition itself still valid? what
did policy decide, and why?). A pipeline that stops early on the first
failure destroys the evidence needed to answer that question.

---

## 2. Shared Data Model

All five stages communicate exclusively through the value types below.
Field names are given in `snake_case`; use your language's idiomatic
casing, but preserve the field semantics and the enum value strings
verbatim (they may be logged/persisted and compared across
implementations).

### 2.1 Enums

**`VerificationStatus`** — outcome of signature verification
| Value | Meaning |
|---|---|
| `valid` | Signature cryptographically verifies against the canonical bytes and the resolved key. |
| `invalid` | A signature was present and a key was resolved, but verification failed. |
| `missing` | No signature was supplied on the event. |
| `error` | Verification could not be attempted or completed for a reason other than cryptographic mismatch (unresolvable key, malformed encoding, internal fault). |

**`ReplayStatus`** — outcome of replaying a state transition
| Value | Meaning |
|---|---|
| `match` | The recomputed post-state hash equals the hash declared on the event. |
| `mismatch` | The recomputed post-state hash does not equal the declared hash. |
| `error` | Replay could not be completed (the transition function faulted, or canonicalizing/hashing the recomputed state faulted). |

**`PolicyDecision`** — outcome of policy evaluation
| Value | Meaning |
|---|---|
| `allow` | Event is fully trusted: signature valid and replay matched. |
| `flag` | Event needs review: state transition is provably correct, but authenticity is in doubt. |
| `deny` | Event is rejected: the state transition itself does not reproduce its declared result, regardless of signature status. |

### 2.2 Result Types

**`CanonicalizationResult`**
| Field | Type | Notes |
|---|---|---|
| `canonical_bytes` | bytes | The deterministic byte encoding of the input payload. |
| `algorithm` | string | Identifier for the canonicalization scheme used (e.g. `"jcs"`). |

**`HashResult`**
| Field | Type | Notes |
|---|---|---|
| `digest_hex` | string | Lowercase hex-encoded digest. |
| `algorithm` | string | Identifier for the hash function used (e.g. `"sha256"`). |

**`SignatureVerificationResult`**
| Field | Type | Notes |
|---|---|---|
| `status` | `VerificationStatus` | Required. |
| `signer_id` | string \| null | The key/signer identifier, when known. |
| `reason` | string \| null | Required (non-null) whenever `status != valid`. Human-readable. |

**`ReplayResult`**
| Field | Type | Notes |
|---|---|---|
| `status` | `ReplayStatus` | Required. |
| `expected_hash` | string \| null | The hash declared on the event. |
| `computed_hash` | string \| null | The hash the replay engine actually computed. Null when `status == error`. |
| `reason` | string \| null | Required (non-null) whenever `status != match`. |

**`PolicyEvaluation`**
| Field | Type | Notes |
|---|---|---|
| `decision` | `PolicyDecision` | Required. |
| `reasons` | list\<string\> | Human-readable justifications; empty only when `decision == allow`. |
| `triggered_rules` | list\<string\> | Machine-readable rule identifiers that fired; empty only when `decision == allow`. |

**`PipelineResult`** — the complete record for one processed event
| Field | Type | Notes |
|---|---|---|
| `event_id` | string | |
| `received_at` | timestamp (UTC) | |
| `canonicalization` | `CanonicalizationResult` | Always populated. |
| `hash` | `HashResult` | Always populated. |
| `signature` | `SignatureVerificationResult` | Always populated, regardless of status. |
| `replay` | `ReplayResult` | Always populated, regardless of signature outcome. |
| `policy` | `PolicyEvaluation` | Always populated, regardless of prior outcomes. |

A derived convenience property, `is_fully_verified`, is `true` iff
`signature.status == valid AND replay.status == match AND
policy.decision == allow`. It is *derived*, not stored input — never
compute it from anything other than the three fields above.

---

## 3. Stage 1 — Canonicalizer

**Purpose:** produce a deterministic byte sequence from a JSON-compatible
payload, so that two semantically identical payloads — regardless of key
order or incidental whitespace, and regardless of which language or
implementation produced them — canonicalize to exactly the same bytes.

**Signature:**
```
canonicalize(payload: JSON-compatible value) -> CanonicalizationResult
```

**Requirements:**
1. **Deterministic.** The same logical payload must always produce
   identical `canonical_bytes`, independent of the insertion/iteration
   order of maps/objects in the input.
2. **Total.** Must not raise/throw for any JSON-compatible payload
   (object, array, string, number, boolean, null). This is the one
   input every downstream stage depends on; it must always succeed.
3. **Self-describing.** `algorithm` must name the exact canonicalization
   scheme, so a verifier on the other side of a signature (possibly in a
   different language) can confirm it's using a compatible scheme.
4. **Cross-language safety (if applicable).** If more than one language
   implementation will canonicalize the same payloads and expect
   matching hashes/signatures, all implementations MUST agree on number
   formatting (the classic failure mode: `1.0` vs `1`, or differing
   float-to-string algorithms). The recommended baseline is RFC 8785
   (JSON Canonicalization Scheme); if you cannot guarantee RFC 8785's
   exact number formatting, restrict payload fields that cross a
   language boundary to integers and strings.

**Reference algorithm** (see `canonicalizer.py::JCSCanonicalizer`): sort
object keys recursively, use compact separators (no whitespace), encode
as UTF-8.

---

## 4. Stage 2 — Hasher

**Purpose:** anchor every event to a fixed-size digest that never fails
to exist, regardless of any other stage's outcome.

**Signature:**
```
hash(canonical: CanonicalizationResult) -> HashResult
```

**Requirements:**
1. **Pure and total.** Given the same `canonical_bytes`, always return
   the same `digest_hex`. Must not raise/throw for any byte string.
2. **Never allowed to fail.** This is the one stage in the entire
   pipeline with no failure state in its result type. If canonicalization
   succeeded (which it always does per §3.2), hashing succeeds.
3. **Self-describing.** `algorithm` must name the exact hash function
   (default: SHA-256), for the same cross-implementation reason as §3.3.

---

## 5. Stage 3 — Signature Verifier

**Purpose:** determine whether a detached signature over the canonical
event bytes is valid, without ever gating what happens next.

**Signature:**
```
verify(canonical: CanonicalizationResult,
       signature: string | null,     // base64-encoded, or absent
       public_key_id: string | null) -> SignatureVerificationResult
```

**Requirements:**
1. **Never raises/throws.** Every failure mode below must resolve to a
   `SignatureVerificationResult`, never a propagated exception:
   | Situation | `status` |
   |---|---|
   | `signature` is null/absent | `missing` |
   | `public_key_id` is null/absent | `error` |
   | `public_key_id` does not resolve to a known key | `error` |
   | `signature` is not valid base64 / malformed | `error` |
   | Signature is well-formed but does not verify against the resolved key and `canonical.canonical_bytes` | `invalid` |
   | Signature verifies | `valid` |
   | Any other internal fault (key store unavailable, unexpected exception in the crypto library) | `error` |
2. **`reason` is required whenever `status != valid`.** It must be
   specific enough for a human reviewer to understand what to fix
   (e.g. "Unknown public_key_id: 'signer-9'", not just "failed").
3. **Never gates.** This function's return value must have zero
   influence on whether the caller invokes the Replay Engine or Policy
   Evaluator — that responsibility belongs entirely to the orchestrator
   (§7), and this component must not attempt to enforce it itself (e.g.
   by refusing to be called, or requiring a prior "success" flag).
4. **Algorithm-agnostic externally.** The reference implementation uses
   Ed25519; a conformant alternative may use a different signature
   scheme as long as it satisfies requirements 1–3.

---

## 6. Stage 4 — Replay Engine

**Purpose:** independently recompute the declared post-state hash by
deterministically applying the event's transition to its declared
pre-state, and report whether it matches — **unconditionally**, whether
or not the signature verified.

**Signature:**
```
replay(pre_state: JSON-compatible value,
       transition: JSON-compatible value,
       declared_post_state_hash: string,
       hasher: Hasher,
       canonicalizer: Canonicalizer) -> ReplayResult
```

Internally, a conformant implementation is parameterized by a
**state-transition function**:
```
apply(pre_state: JSON-compatible value,
      transition: JSON-compatible value) -> post_state: JSON-compatible value
```

**Requirements:**
1. **`apply` must be pure.** No I/O, no wall-clock reads, no randomness,
   no hidden mutable state. Replay determinism depends entirely on this
   function being a total function of its two arguments. (This
   requirement is on whoever supplies the transition function to the
   engine, not on the engine itself — but the engine must document it
   loudly, since violating it silently breaks the entire pipeline's
   value proposition.)
2. **Never raises/throws.** Two failure sources must both resolve to
   `ReplayStatus.error` with a `reason`, never propagate:
   - `apply(pre_state, transition)` itself throws.
   - Canonicalizing or hashing the recomputed post-state throws.
3. **Runs unconditionally.** The engine has no parameter carrying the
   signature verification outcome, and must not accept one — this
   makes "replay never depends on signature status" a structural
   property of the interface, not a convention callers must remember.
4. **Comparison is exact string equality** between the recomputed
   `digest_hex` and `declared_post_state_hash`. Do not normalize case
   or trim whitespace — mismatches there indicate an upstream
   canonicalization bug that should surface, not be silently absorbed.

---

## 7. Stage 5 — Policy Evaluator

**Purpose:** the one stage whose explicit job is to turn raw evidence
into an actionable decision — but it, too, runs unconditionally and
must be handed the raw evidence (not a pre-collapsed boolean), so the
reasoning behind every decision is always reconstructable later.

**Signature:**
```
evaluate(signature: SignatureVerificationResult,
         replay: ReplayResult) -> PolicyEvaluation
```

**Requirements:**
1. **Never raises/throws.** An evaluator that cannot reason about an
   event must resolve to `PolicyDecision.deny` with a reason describing
   its own internal fault (e.g. `"policy_evaluator_error"` as a
   triggered rule) — a swallowed exception here would silently drop the
   one stage meant to make failures actionable.
2. **Runs unconditionally**, regardless of what `signature` and
   `replay` contain, including when both indicate failure.
3. **`reasons` and `triggered_rules` are required (non-empty) whenever
   `decision != allow`.** A `flag` or `deny` with no reasons is a
   contract violation — it defeats the purpose of the whole pipeline.
4. **Default policy** (the reference implementation's rule set; a
   deployment MAY substitute its own policy as long as requirements
   1–3 hold):
   | Signature | Replay | Decision | Rationale |
   |---|---|---|---|
   | `valid` | `match` | `allow` | Fully trusted. |
   | any non-`valid` | `match` | `flag` | State transition is provably correct; only authenticity is in doubt — merits review, not outright rejection. |
   | any | `mismatch` or `error` | `deny` | State integrity outranks authenticity: a ledger that cannot reproduce its own declared transition cannot be trusted even if validly signed. |

---

## 8. Pipeline Orchestrator Contract

The orchestrator (`ProvenancePipeline` in the reference implementation)
wires the five stages together. A conformant orchestrator:

1. Calls Canonicalizer, then Hasher, then Signature Verifier, then
   Replay Engine, then Policy Evaluator, **in that order, every time**,
   for every event, with no conditional branch that skips a later call
   based on an earlier result.
2. Passes the Signature Verifier's and Replay Engine's *result objects*
   (not booleans, not summaries) to the Policy Evaluator.
3. Returns a fully-populated `PipelineResult` (§2.2) — every field
   present, regardless of outcome — as its sole return value. It must
   not raise/throw for any well-formed input; genuinely malformed input
   (e.g. `pre_state` is not a JSON-compatible value at all) is the one
   case where raising at the orchestrator boundary is acceptable, since
   no stage can even be attempted.

### Conformance Matrix

Any conforming pipeline (this reference implementation or a reimplementation
in another language) MUST produce these outcomes for these input classes:

| # | Signature | Replay | Expected `signature.status` | Expected `replay.status` | Expected `policy.decision` |
|---|---|---|---|---|---|
| 1 | Valid, matches payload | Transition reproduces declared hash | `valid` | `match` | `allow` |
| 2 | Absent | Transition reproduces declared hash | `missing` | `match` | `flag` |
| 3 | Present but cryptographically invalid | Transition reproduces declared hash | `invalid` | `match` | `flag` |
| 4 | References an unresolvable key id | Transition reproduces declared hash | `error` | `match` | `flag` |
| 5 | Valid, matches payload | Declared hash is wrong / transition doesn't reproduce it | `valid` | `mismatch` | `deny` |
| 6 | Absent | Declared hash is wrong | `missing` | `mismatch` | `deny` |
| 7 | Valid, matches payload | Transition function itself faults | `valid` | `error` | `deny` |
| n/a | any | any | — | — | `PipelineResult` fields are ALL non-null; hash and replay are always attempted regardless of columns 2–3 |

Rows 2–4 are the ones that most directly test the core invariant (§1):
in every one of them, `replay.status == match` proves replay ran and
succeeded despite the signature failure, and `policy.decision == flag`
(not silently dropped, not `allow`) proves the failure was recorded as
an actionable finding.

The Python reference implementation's test suite
(`provenance/tests/test_pipeline.py`) implements this exact matrix, plus
a determinism check on the Canonicalizer (§3, requirement 1). Use it as
the acceptance test when validating a reimplementation: feed it the same
fixtures and confirm identical `PipelineResult` values field-for-field
(digests will of course only match if canonicalization/hash algorithms
and inputs are identical across implementations).

---

## 9. Known Caveats for Cross-Language Implementers

- **Numeric canonicalization.** The reference `JCSCanonicalizer` uses
  Python's native number formatting, which is *not* guaranteed to match
  the ECMAScript Number-to-String algorithm that RFC 8785 formally
  specifies. If you need byte-identical canonical output across
  languages, either implement the RFC 8785 number-formatting rules
  precisely in every language involved, or restrict cross-language
  payload fields to integers/strings and avoid floats entirely.
- **Signature algorithm choice.** The reference implementation uses
  Ed25519. Nothing in this spec requires Ed25519 specifically — pick
  whatever asymmetric scheme fits your key-management infrastructure, as
  long as the `SignatureVerifier` contract (§5) is met.
- **Timestamps.** `received_at` should be recorded in UTC with at least
  millisecond precision. It is metadata about when the pipeline
  processed the event, not part of any hashed or signed payload.
