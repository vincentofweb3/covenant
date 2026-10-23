# Project state — Covenant

## Current status

**Evidence-provenance amendment implemented. 190 direct + 29 integration + 20
probe tests green. The amended contracts are not deployed yet.**

The original submission was rejected because the treasury authorization could
depend on external facts while the contract evaluated only evidence text supplied
by the payment requester. The amendment removes that path: requester supplies the
instruction and subject only; procurement/risk attestors fixed at deployment write
structured on-chain claims; signers approve the exact operation binding; the guard
reads the current claims at ruling time and binds their digest into the ruling;
the vault re-checks the evidence subject/digest before moving funds.

Both contracts pass `genvm-lint` and `preflight.py`. Direct and simulator suites
are green. The live deployment below is the **pre-amendment** run and does not
contain attestation or approval transactions.

## What exists

| File | Lines | Purpose |
|---|---|---|
| `contracts/covenant_guard.py` | 1436 | the primitive: covenant store, operation lifecycle, judge seam, evidence registry, amendment flow. 28 methods |
| `contracts/treasury_vault.py` | 416 | worked example; proves the guard is load-bearing. 11 methods |
| `tests/direct/test_covenant_guard.py` | 475 | judge seam, adversarial cases, state machine |
| `tests/direct/test_adversarial.py` | 232 | boundaries around the seam: injection, case, capture, laundering |
| `tests/direct/test_amendment_flow.py` | 295 | quorum, timelock, staleness, version history |
| `tests/direct/test_evidence_provenance.py` | 284 | role-gated claims, expiry, approvals, evidence binding |
| `tests/direct/test_covctl.py` | 240 | the linter, and that its digest equals the contract's |
| `tests/direct/conftest.py` | 143 | fixtures, attestations, `set_clock`, SDK pin |
| `tests/integration/test_lifecycle.py` | 443 | **the refusal proof** + evidence provenance |
| `tests/integration/test_consensus.py` | 236 | real validator voting, via a documented harness shim |
| `tests/integration/conftest.py` | 569 | glsim harness, typed calldata, simulated clock, validator capture |
| `scripts/covctl.py` | 420 | covenant compiler + policy linter + digest + citation checker |
| `scripts/preflight.py` | 230 | 265 architectural invariants + secret scan |
| `scripts/live_demo.py` | 560 | live deploy + attestations/approvals + demonstration, retries transient network errors, evidence to JSON |
| `docs/THREAT_MODEL.md` | — | threats → controls, explicit non-goals, what is not established |
| `docs/CONSENSUS.md` | — | the leader/validator protocol, precisely |
| `docs/ARCHITECTURE.md` | 202 | state, machines, message sequence, trust boundaries |
| `docs/BRIEF.md` | — | problem, decision, smallest live slice, go/no-go gate |
| `state/REVIEW_ROUND1.md` | 198 | guard audit — 3 refuted findings kept |
| `state/REVIEW_ROUND2.md` | 155 | vault audit — 2 refuted findings kept |
| `state/REVIEW_ROUND3.md` | 178 | consensus audit — includes a disclosure of four wrong turns |
| `state/MEMORY.md` | 314 | verified GenLayer API facts, including web/cross-contract/signature findings |

## Validation status

```console
$ python3 -m compileall -q contracts scripts tests
$ python3 scripts/preflight.py
✓ Preflight passed (329 checks, 56 files scanned)

$ GENVM_VERSION=v0.3.0-rc7 genvm-lint check contracts/covenant_guard.py
✓ Validation passed
  Methods: 28 (17 view, 11 write)

$ GENVM_VERSION=v0.3.0-rc7 genvm-lint check contracts/treasury_vault.py
✓ Validation passed
  Methods: 11 (7 view, 4 write)

$ python3 -m pytest tests/direct
190 passed

$ python3 -m pytest tests/integration
29 passed

$ python3 -m pytest probe/
20 passed
```

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

1. **The GenVM runtime's behaviour on validator disagreement is unproven.** This
   is the finding, not a caveat. glsim with a documented harness shim shows a
   split producing `UNDETERMINED`, but glsim is not the runtime.
2. **No live model was ever consulted.** Every test substitutes a fixed reply.
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

## LIVE — pre-amendment run on studionet (2026-10-20)

**The blocker was based on a wrong assumption: studionet is gasless.**
`baseFeePerGas` is 0, `eth_gasPrice` is 0, and every block reports `gasUsed` 0.
The zero-balance deployer `0x158e5ccAA39F7Db714B35Ae5fb6A5d925BE219cD` deployed
both contracts successfully. No faucet, no test tokens.

| | |
|---|---|
| `covenant_guard` | `0xC5335e9A9F96fE77Ca8944E01e11085296E88556` |
| `treasury_vault` | `0xe2e7DaB935b5328E12f0D4c3F842481ed4c6497a` |
| Transactions | 28, all `MAJORITY_AGREE`, 5 validators |
| Rounds observed | 1, 2 and 3 |

All four enforcement expectations held:

| Step | Amount | Verdict | State | Moved |
|---|---|---|---|---|
| `op-small` | 900 | ALLOWED | FINALIZED | −900 |
| `op-big` | 120,000 | VIOLATED | REJECTED | 0 |
| `op-prohibited` | 500 | VIOLATED | REJECTED | 0 |
| `op-after` | 120,000 | ALLOWED | FINALIZED | −120,000 |

`op-big` and `op-after` are the same payment. Between them the covenant was
amended by quorum + timelock, v1 → v2, and the identical payment flipped from
refused to executed. Balances reconcile exactly: 1,000,000 − 900 − 120,000 = 879,100.

Evidence: `evidence/studionet-2026-10-20.md` and `evidence/live-run.json`.
**This is the pre-amendment run.** It contains no attestations or approvals and
must not be cited for the provenance claim. The amended demo (`scripts/live_demo.py`)
now deploys with evidence roles, attests vendor/risk claims, approves the large
payment with two signers, and is ready to re-run after commit.

### Two defects the live run exposed

**10. The demo omitted `finalize_operation`.** `judge_operation` leaves an
operation `RULED`; `is_downstream_actionable` correctly returns false until
`finalize_operation` settles it. The first live attempt skipped that step and
every execution was refused — the guard was right and the demo was wrong. Worth
recording because the failure looked like a contract bug and was not.

**11. Both test trees imported `conftest` as a top-level module.** `pytest
tests/direct` and `pytest tests/integration` each passed alone, but
`pytest tests` failed: `from conftest import set_clock` resolved to whichever
`conftest.py` pytest had imported first. Fixed by making both trees packages and
using relative imports, plus a uniquely named `direct_support.py`. 164 tests now
pass in one run, where before the combined run failed.

### Findings from the live run

1. **Studionet is gasless.** The entire "fund the key" blocker was wrong.
2. **`finalize_operation` is a required step.** `judge_operation` leaves the
   operation `RULED`; `is_downstream_actionable` correctly returns false until
   `finalize_operation` settles it. The first live attempt omitted it and every
   execution was refused — the guard was right, the demo was incomplete.
3. **`ALLOWED` is not the default under a threshold.** A one-off payment below
   the amended threshold came back `AMBIGUOUS`/`EVIDENCE_INSUFFICIENT`, because
   the covenant prohibits but never affirmatively permits one-off payments. Fail
   closed, and arguably correct — but it means covenant wording drives outcomes,
   not just numbers.
4. **The fabricated-excerpt defence is still unproven on-chain.** It needs a
   hallucinating model, which cannot be provoked from outside. Proven only with
   an injected model in the test suites. Do not blur this in any write-up.

## Next checkpoint

The evidence-provenance amendment is implemented and locally green. Remaining:

1. commit the amendment (user approval required);
2. regenerate the covenant fixture and covctl vendor/fixture from that commit;
3. run the full suite, preflight, and lint again;
4. redeploy to studionet with the amended demo (attestations + approvals);
5. record new evidence and replace the pre-amendment live table;
6. resubmit, citing the rejection and the specific control that answers it.

The pre-amendment live run resolved the original deployment question but is **not**
evidence for the provenance claim. The fabricated-excerpt defence remains proven
only with an injected model; the attestor-honesty assumption is disclosed in
`docs/THREAT_MODEL.md` §2.6.