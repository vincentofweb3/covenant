# Project state — Covenant

## Current status

**Evidence-provenance amendment implemented, deployed, and demonstrated live.
193 direct + 30 integration + 20 probe tests green. 42/42 live transactions
finalized with `MAJORITY_AGREE`; all five enforcement outcomes matched.**

The original submission was rejected because the treasury authorization could
depend on external facts while the contract evaluated only evidence text supplied
by the payment requester. The amendment removes that path: requester supplies the
instruction and subject only; procurement/risk attestors fixed at deployment write
structured on-chain claims; signers approve the exact operation binding; the guard
reads the current claims at ruling time and binds their digest into the ruling;
the vault re-checks the evidence subject/digest before moving funds.

Both contracts pass `genvm-lint` and `preflight.py`. Direct and simulator suites
are green. The live deployment below is the amended run: it contains attestations,
signer approvals, evidence-bound rulings, and the v1 → v2 amendment.

## What exists

| File | Lines | Purpose |
|---|---|---|
| `contracts/covenant_guard.py` | 1463 | the primitive: covenant store, operation lifecycle, judge seam, evidence registry, amendment flow. 28 methods |
| `contracts/treasury_vault.py` | 426 | worked example; proves the guard is load-bearing. 11 methods |
| `tests/direct/test_covenant_guard.py` | 506 | judge seam, adversarial cases, state machine |
| `tests/direct/test_adversarial.py` | 232 | boundaries around the seam: injection, case, capture, laundering |
| `tests/direct/test_amendment_flow.py` | 316 | quorum, timelock, staleness, version history |
| `tests/direct/test_evidence_provenance.py` | 284 | role-gated claims, expiry, approvals, evidence binding |
| `tests/direct/test_covctl.py` | 459 | the linter, and that its digest equals the contract's |
| `tests/direct/conftest.py` | 143 | fixtures, attestations, `set_clock`, SDK pin |
| `tests/integration/test_lifecycle.py` | 466 | **the refusal proof** + evidence provenance |
| `tests/integration/test_consensus.py` | 236 | real validator voting, via a documented harness shim |
| `tests/integration/conftest.py` | 569 | glsim harness, typed calldata, simulated clock, validator capture |
| `scripts/covctl.py` | 420 | covenant compiler + policy linter + digest + citation checker |
| `scripts/preflight.py` | 272 | 337 architectural invariants + secret scan |
| `scripts/live_demo.py` | 595 | live deploy + attestations/approvals + demonstration, retries transient network errors, evidence to JSON |
| `docs/THREAT_MODEL.md` | — | threats → controls, explicit non-goals, what is not established |
| `docs/CONSENSUS.md` | — | the leader/validator protocol, precisely |
| `docs/ARCHITECTURE.md` | 254 | state, machines, message sequence, trust boundaries |
| `docs/BRIEF.md` | — | problem, decision, smallest live slice, go/no-go gate |
| `state/REVIEW_ROUND1.md` | 198 | guard audit — 3 refuted findings kept |
| `state/REVIEW_ROUND2.md` | 155 | vault audit — 2 refuted findings kept |
| `state/REVIEW_ROUND3.md` | 178 | consensus audit — includes a disclosure of four wrong turns |
| `state/MEMORY.md` | 314 | verified GenLayer API facts, including web/cross-contract/signature findings |

## Validation status

```console
$ python3 -m compileall -q contracts scripts tests
$ python3 scripts/preflight.py
✓ Preflight passed (337 checks, 58 files scanned)

$ GENVM_VERSION=v0.3.0-rc7 genvm-lint check contracts/covenant_guard.py
✓ Validation passed
  Methods: 28 (17 view, 11 write)

$ GENVM_VERSION=v0.3.0-rc7 genvm-lint check contracts/treasury_vault.py
✓ Validation passed
  Methods: 11 (7 view, 4 write)

$ python3 -m pytest tests/direct
193 passed

$ python3 -m pytest tests/integration
30 passed

$ python3 -m pytest probe/
20 passed
```

## Steward findings and fixes

The review after the first resubmission found three concrete issues, all fixed
and covered by regression tests:

1. **Arbitrary amendment approvers.** `approve_amendment` now accepts only the
   configured governance set (`owner`, `signer_a`, `signer_b`), and construction
   rejects a quorum larger than that set.
2. **Unauthorized cancellation.** `cancel_operation` accepts only the operation
   proposer or the covenant owner; `cancel_payment` accepts only the payment
   proposer or the vault owner.
3. **Missing serialized cancellation result.** `cancel_operation` now returns the
   canonical cancelled record explicitly; a live outsider attempt left the
   operation `PROPOSED`, and the owner's call returned `CANCELLED`.

The live run exercises the guard-side cancellation path; the vault-side path is
covered by `tests/integration/test_lifecycle.py`.

## Defects found and fixed

Seven, plus four harness defects. Five were caught by tests written before the
fix existed; two were found by reviewing our own work and are the more valuable
ones.

**Phase 1 — guard**

1. Idempotent replay returned the action envelope instead of the result, so the
   same `action_id` yielded two different shapes.
2. `get_ruling` returned a Python `repr`, not JSON — unreadable by any consumer.
3. The state guard ran *before* the replay check, so a legitimate retry failed.
4. `EXPIRED` meant two contradictory things. The concept was wrong, not the code:
   a timelock is a minimum wait, not a deadline. `EXPIRED` now means *stale*.

**Phase 2 — vault**

5. **Amount and recipient were out of band.** The model judged a summary and
   never saw the number, so `summary="routine small payment"` moved 1,000,000 past
   a 50,000 threshold. This defeated the entire project, and no demo would have
   surfaced it. Fixed by submitting a canonical binding as the judged text.
6. The vault trusted `is_downstream_actionable` alone. It now re-reads the ruling
   and independently requires `excerpt_verified`.

**Phase 3 — harness**

7. `sim_setTime` wrote the clock to `genlayer.message`, which the OLD std lib does
   not have, so the amendment tests were timing against a frozen clock.
8. A test of mine asserted a refusal where refusal was not warranted. Rebuilt
   around the attack that is actually available.
9. Two `covctl` linter bugs found by its own tests: the defined-terms regex
   reported the clause that *defines* a term as failing to define it.

## Decisions

- **Concept:** a semantic treasury guard. A written covenant is stored on-chain
  and GenLayer consensus rules whether each proposed value movement complies.
- **Network:** studionet, RPC `https://studio.genlayer.com/api`, chain id `61999`.
- **Std lib:** OLD generation, `py-genlayer:1jb45aa8…`, runner pinned to
  `v0.3.0-rc7` in two independent places.
- **The judged payload is the bound payload.** The one decision that makes the
  guard work at all, and the last to be made.
- **Exactly one consensus seam.** The guard rules; the vault asks. Enforced by
  `preflight.py`, which fails if a second contract grows one.
- **The guard holds no value.** Enforced by `preflight.py` too.
- **No frontend.** Contract primitive only.
- **Submissions:** four — Contract Deployed, GitHub Repository,
  Tutorial/Documentation, Developer Tool (`covctl`).

## What is deliberately not established

Carried forward from `state/REVIEW_ROUND3.md` §5 and `docs/THREAT_MODEL.md` §6:

1. **Real consensus is observed on one live run, not established as reliable.**
   42/42 transactions finalized `MAJORITY_AGREE`; four judges needed two rounds.
   glsim still drives a split to `UNDETERMINED`; one run is not a reliability claim.
2. **A live model was consulted five times, not measured.** All five rulings
   matched expectations and cited a real clause; an earlier overlapping covenant
   split validators. Generalization is unmeasured.
3. **`max_rotations=1` everywhere.** Leader rotation under real failure is
   untested, and is the mechanism that would resolve (1).
4. **The excerpt check proves a clause is real, not that it applies.** Irreducible
   here, and stated as such.
5. **No gas, cost, size or fuzzing analysis.**
6. **Vault balances are internal accounting.** Not escrow, not settlement.

## Evidence-provenance amendment (2026-10-23)

**Rejection.** "The treasury authorization can depend on external facts, but the
contract evaluates only evidence text supplied by the payment requester." The
excerpt check proved a policy clause existed; it did not verify vendor approval
or risk status.

**Fix.** A guard-local attestation registry:

- constructor fixes `procurement_attestor`, `risk_attestor`, `signer_a`,
  `signer_b` (four distinct addresses);
- `attest_vendor` / `attest_risk` are role-gated by
  `gl.message.sender_address`; claims are subject-keyed, status-typed, have a
  bounded validity window, `https://` source URI, 64-hex source digest, and a
  recomputable claim digest;
- `propose_operation(action_id, operation_id, summary, subject)` has no evidence
  argument;
- `approve_payment(action_id, operation_id)` records a configured signer's
  approval of the exact `binding_digest`;
- `judge_operation` acquires the current vendor/risk claims and distinct matching
  approvals, computes `evidence_digest`, and records it on the ruling;
- the vault re-checks `evidence_verified`, `evidence_subject`, `evidence_digest`
  and the stored `input_digest` before execution.

**Honest limit.** The contract proves which role wrote a claim and when, not that
the claim is true. Attestor keys are immutable, with no rotation/revocation.
Evidence is a ruling-time snapshot; expiry is not re-checked at execution. See
`docs/THREAT_MODEL.md` §2.6–2.8 and §6.

## LIVE — authority-fix run on studionet (2026-10-25)

The amended contracts deployed successfully with the zero-balance key, then ran
the full attestation → request → approvals → ruling → execution path.

| | |
|---|---|
| `covenant_guard` | `0x20E0090e6bC50bd237FAF0df57159c565c554df5` |
| `treasury_vault` | `0xCf542C78302Dd3B39Cef0aE6C0B5e07FE790F536` |
| Transactions | 42, all `FINALIZED` / `MAJORITY_AGREE`, 5 validators |
| Rounds observed | 38 at 1 round; four judges at 2 |
| Covenant v1 | `fea742a0c51497a7288181070698f403f162a89fb8c16d53688bb4a0c2bf9b5e` |
| Covenant v2 | `9aec91476aa33aa414cc355313addd23b17a871ba24e8ab42cec570e8d433275` |

All five enforcement expectations held:

| Step | Amount | Verdict | State | Moved |
|---|---|---|---|---|
| `op-small` | 900 | ALLOWED | FINALIZED | −900 |
| `op-signed` | 900 | ALLOWED | FINALIZED | −900 |
| `op-big` | 120,000 | VIOLATED | REJECTED | 0 |
| `op-prohibited` | 500 | VIOLATED | REJECTED | 0 |
| `op-after` | 120,000 | ALLOWED | FINALIZED | −120,000 |

`op-signed` has two distinct signer approvals bound to the operation; it is
permitted independently because it is below the threshold. An outsider could not
cancel a pending operation, while the owner could; `cancel_operation` returned
the serialized `CANCELLED` record. `op-big` and
`op-after` are the same payment, split by the quorum + timelock amendment; it
flipped from refused to executed. Balances reconcile exactly:
`1,000,000 − 900 − 900 − 120,000 = 878,200` (`get_totals` returned the same
values).

Evidence: `evidence/studionet-2026-10-25.md` and `evidence/live-run.json`.
Historical pre-amendment run: `evidence/studionet-2026-10-20.md` and
`evidence/live-run-2026-10-20.json`.

### Two earlier live defects

**10. The demo omitted `finalize_operation`.** `judge_operation` leaves an
operation `RULED`; `is_downstream_actionable` correctly returns false until
`finalize_operation` settles it. The first live attempt skipped that step and
every execution was refused — the guard was right and the demo was wrong. Worth
recording because the failure looked like a contract bug and was not.

**11. Both test trees imported `conftest` as a top-level module.** `pytest
tests/direct` and `pytest tests/integration` each passed alone, but `pytest
tests` failed: `from conftest import set_clock` resolved to whichever
`conftest.py` pytest had imported first. Fixed by making both trees packages and
using relative imports, plus a uniquely named `direct_support.py`. 164 tests now
pass in one run, where before the combined run failed.

### Findings from the live runs

1. **Studionet is gasless.** The entire "fund the key" blocker was wrong.
2. **`finalize_operation` is a required step.** `judge_operation` leaves the
   operation `RULED`; `is_downstream_actionable` correctly returns false until
   `finalize_operation` settles it.
3. **Policy wording must be disjoint.** Earlier drafts had a recurring-payment
   permission and a threshold-approval clause. A 120,000 payment with two
   approvals split validators across those overlapping permissions and produced
   `MAJORITY_DISAGREE` with no ruling. The covenant now states the policy purely
   by amount: at or below the threshold is permitted to an approved vendor; above
   it requires two signers. The same denial then produced a clean `VIOLATED`.
4. **`ALLOWED` is not the default under a threshold.** A one-off payment below
   the amended threshold once came back `AMBIGUOUS`/`EVIDENCE_INSUFFICIENT`,
   because the old covenant prohibited but never affirmatively permitted it.
   Fail closed, but covenant wording drives outcomes, not just numbers.
5. **The fabricated-excerpt defence is still unproven on-chain.** It needs a
   hallucinating model, which cannot be provoked from outside. Proven only with
   an injected model in the test suites. Do not blur this in any write-up.

## Next checkpoint

The amendment is deployed, evidenced, and locally green. Remaining:

1. commit the final evidence and documentation;
2. push Covenant and covctl;
3. resubmit, citing the rejection and the specific control that answers it:
   requester cannot supply facts; role-gated claims are read at ruling time;
   evidence digest/subject are bound into the ruling; the vault re-checks both
   before moving funds.

The live run establishes that this demonstration worked once, not consensus
reliability. The fabricated-excerpt defence remains proven only with an injected
model; the attestor-honesty assumption is disclosed in
`docs/THREAT_MODEL.md` §2.6.