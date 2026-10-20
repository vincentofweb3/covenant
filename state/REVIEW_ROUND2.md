# Independent review — round 2 (vault and cross-contract enforcement)

Adversarial pass over `contracts/treasury_vault.py` and
`tests/integration/test_lifecycle.py`. Round 1 covered the guard's internals and
did not look at the vault at all.

Date: 2026-10-15. Scope: the cross-contract enforcement claim, the
payload-binding property, and the governance path as observed on glsim.

---

## 1. Bottom line

The enforcement claim holds on the simulator. A value-moving method consults a
policy oracle over a cross-contract call and refuses when the oracle says no —
and it does so for a fabricated clause, for ambiguity, for a pre-ruling
execution, and for an approval obtained outside the vault.

**One finding was significant enough to change the design**, and it was found
while writing the vault rather than in review of it.

| # | Severity | Finding | Verdict | Action |
|---|---|---|---|---|
| P2-1 | **P1** | Amount and recipient were out of band, so the judge never saw them | **confirmed — design changed** | canonical binding submitted to the guard |
| P2-2 | **P1** | Vault trusted `is_downstream_actionable` alone | confirmed | ruling re-read and independently verified |
| P2-3 | P2 | Cross-contract reads could return `None` and be stringified | confirmed | every read null-checked before use |
| P2-4 | P2 | No "refused" event, because refusals revert | **refuted as a defect** | documented; see §4 |
| P3-1 | P3 | Balances are internal accounting, not escrow | confirmed | stated in the module docstring and README |
| P3-2 | P3 | glsim runs one validator, so disagreement is unproven | confirmed | carried into §5, not worked around |
| P4-1 | P4 | `request_payment` is permissionless | refuted as a defect | see §4 |

## 2. The design change (P2-1)

The first version of the vault kept `amount` and `recipient` in vault storage and
submitted a human-written `summary` to the guard for judgment.

**That is exploitable, and it defeats the entire project.** The model judges the
summary and the evidence. It never sees the amount. An attacker writes
`summary="routine small vendor payment"` and requests 1,000,000 against a
covenant whose Clause 3 threshold is 50,000. The judge sees nothing wrong, returns
`ALLOWED`, and 1,000,000 moves.

This is the kind of bug that a demo would never surface, because in a demo the
proposer is honest.

**Fix.** The vault no longer submits a description standing in for the numbers.
It builds a canonical binding:

```python
{"amount": 120000, "recipient": "0x4444…", "memo": "recurring monthly retainer"}
```

serialised with sorted keys and compact separators, and submits *that* as the
operation summary. The amount and recipient are inside the judged text by
construction. On execution the vault reads the operation back and refuses unless
the summary is byte-identical to the one it submitted, and the binding digest
matches.

Regression: `test_vault_binds_amount_and_recipient_into_the_judged_text`
asserts the guard's stored summary parses to the real amount and recipient, and
equals the vault's binding.

## 3. P2-2 — the vault re-verifies rather than trusting

`execute_payment` performs three checks in order of increasing cost:

1. **The judged payload is the vault's.** The guard's copy of the operation must
   carry this vault's binding, byte for byte, with a matching binding digest.
   Otherwise authority obtained for one payload could be spent on another.
2. **The guard reports it actionable.** Only a finalized `ALLOWED` ruling makes
   `is_downstream_actionable` true.
3. **The ruling itself is re-read** and must be `ALLOWED` with
   `excerpt_verified` true.

Check 3 is deliberately redundant with the guard's own guarantee. If the guard
were ever compromised — or if a future change weakened its normalisation — a
ruling whose clause was never verified still cannot move funds. The vault does
not inherit trust in the oracle; it re-checks the one property that matters.

## 4. Refuted findings

### P2-4 — "There is no refused event"

**Refuted as a defect.** A refusal reverts the transaction, which rolls back its
logs, so a `PaymentRefused` event would never be observable anywhere. The absence
of a `PaymentExecuted` log *is* the record of a refusal. Adding one would be
worse than useless: it would suggest an audit trail that does not exist. This is
now stated in the `PaymentExecuted` docstring.

### P4-1 — "`request_payment` is permissionless"

**Refuted as a defect.** Anyone may propose a payment, and the covenant decides.
Gating the *proposal* would not add safety — it would only mean the proposer is
trusted before the judge has ruled, which is the thing the design refuses to do.
`propose_operation` on the guard is permissionless for the same reason.

## 5. What this review does not establish

1. **Real consensus is unobserved.** Every test here runs on glsim with
   `num_validators=1`, so validator agreement is trivially satisfied. That a
   genuine validator disagreement drops the transaction — the reason the seam
   fails closed on chain — is **not** proven by anything in this repository.
2. **The model is a fixed reply function.** These tests prove that the vault
   responds correctly to each verdict. They prove nothing about whether a real
   model returns those verdicts correctly, or how often.
3. **Single-validator only.** Leader rotation, multi-hop fee behaviour and
   multi-validator quorum are invisible here.
4. **glsim drains emitted messages inside the calling transaction.** On a real
   network `request_payment` returns with the guard's `propose_operation` still
   pending. The lifecycle here is therefore *synchronous* and the real one is
   not; a live run will need to await the child transaction.
5. **The binding test asserts a property of construction, not of adversarial
   input.** It proves the amount is in the judged text. It does not prove a
   model reads the number correctly when reasoning about prose.
6. **No fuzzing, no gas analysis, no formal argument.** `MAX_*` bounds exist but
   their adequacy is unmeasured.

## 6. Regression sweep

| Expected | Observed |
|---|---|
| `genvm-lint lint` on both contracts | ✓ 3 checks each |
| `genvm-lint check` on both | ✓ guard 21 methods, vault 11 methods |
| direct suite green | ✓ 78 passed |
| glsim lifecycle suite green | ✓ 18 passed |
| full suite | ✓ 116 passed |
| every cross-contract read null-checked | ✓ 3 reads in `execute_payment`, all guarded |
| the simulated clock actually advances | ✓ fixed this round; see below |

### Two harness defects found and fixed in Phase 2

Both were in the test harness rather than the contracts, and both produced tests
that would have passed for the wrong reason:

- **`sim_setTime` silently did nothing.** The OLD std lib has no
  `genlayer/message.py`, so the clock was being written to a module that does not
  exist. The amendment test failed with `timelock has not elapsed` — correctly,
  because the contract genuinely had not seen the new time. The harness now looks
  the module up in `sys.modules` and verifies it found a real dict.
- **A test asserted the wrong thing.** The original
  `test_authority_obtained_for_a_benign_payload_cannot_be_spent_on_a_large_one`
  expected a refusal in a situation where refusal was not warranted — the mock
  had approved and the payload matched. Rather than relax the test, the scenario
  was rebuilt around the attack that is actually available: propose an operation
  directly on the permissionless guard, obtain a real ALLOWED ruling, then present
  it to the vault. The vault refuses it because it holds no payment of its own
  for that id.

## 7. Recommended next work

1. Deploy both contracts to studionet and run the eight-step demo. The
   asynchronous step (awaiting the child `propose_operation`) is untested
   anywhere.
2. Run a multi-validator glsim configuration to observe genuine disagreement,
   which §5.1 identifies as the largest unproven assumption in the design.
3. Write `docs/THREAT_MODEL.md` from §5 of both rounds rather than from memory.