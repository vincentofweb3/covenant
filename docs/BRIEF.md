# Brief

## Decision

**Go — with the payload binding and the evidence-provenance amendment, neither
of which was in the original design.**

The initial plan was a guard that asks a model whether an operation complies
with a written covenant. Phase 2 found that the obvious version of the vault is
exploitable, because the amount never reaches the model. The binding decision
makes the design work. A review then found the second gap: the requester supplied
the external facts as prose, and a real citation proved only that the policy
clause existed. The amendment replaces requester evidence with role-gated
on-chain attestations and binds their digest into the ruling.

## User and problem

An organisation holds a treasury. It operates under a written policy, and the
clauses that actually constrain behaviour are prose:

> the counterparty must be on the approved vendor list
> no swap with assets the risk desk has flagged
> recurring payments to a provider are permitted, one-off transfers to the same
> provider are not

Today someone reads that policy and clicks approve. That is a control that
works at one desk and fails the moment there are two, or under time pressure, or
when the policy is longer than anyone's attention span.

## The external action

`TreasuryVault.execute_payment` moves a payment **only** after
`CovenantGuard` has recorded a finalized `ALLOWED` ruling whose cited clause was
verified against stored covenant bytes. Without one the call reverts and the
balance does not move.

## Authorization model

| Actor | Can | Cannot |
|---|---|---|
| Covenant owner | propose an amendment | activate one alone |
| Amendment approver | approve once, if distinct | approve twice, or force activation |
| Payment proposer | request anything | supply evidence, or cause an `ALLOWED` verdict |
| Procurement attestor | write vendor claims for its role | write risk claims or approvals |
| Risk attestor | write risk claims for its role | write vendor claims or approvals |
| Payment signer | approve one exact operation binding | approve twice, or spend approval elsewhere |
| Vault | execute against a verified ruling | rule for itself — enforced by preflight |
| Model | cite a clause | invent one, or ignore the contract-acquired facts without consequence in the audit trail |

## Success observation

`is_downstream_actionable(operation_id)` is the machine-readable seam. It is true
only for a finalized `ALLOWED` ruling, and every other disposition — including a
transaction the protocol reports `FINALIZED` — returns false.

## The decision the whole design rests on

**The amount must be inside the judged text.**

The obvious design keeps `amount` in vault storage and submits a human-written
summary. The model judges the summary and never sees the number, so
`summary="routine small payment"` moves 1,000,000 past a 50,000 threshold and the
judge sees nothing wrong. No demo would surface this, because in a demo the
proposer is honest.

So the vault submits a canonical binding as the summary, and refuses execution
unless the guard holds that exact payload.

## The second decision: facts are acquired, not asserted

`propose_operation` takes the instruction and the subject, never evidence text.
Procurement and risk attestors fixed at deployment write structured claims for the
subject; two signers approve the exact operation binding. The guard reads the
current claims at ruling time, checks role, status, expiry and claim digest,
counts distinct matching approvals, and computes `evidence_digest`. The model sees
a `VERIFIED_EVIDENCE` block the caller could not write. The vault re-checks that
the ruling's evidence subject and digest match the operation before it moves
funds. A missing or expired claim fails closed at judgment time.

The contract proves provenance, not truth: the attestor keys are trusted and
immutable. That limit is stated in `docs/THREAT_MODEL.md` §2.6.

## Smallest live end-to-end slice

1. Deploy the guard with the six-clause demo covenant, quorum 2, timelock 1 hour,
   and four distinct evidence roles.
2. Attest the recipient as `approved`/`clear`; attest a removed vendor as
   `not_approved`; deploy the vault, register it, deposit.
3. Request a 120,000 payment with no approvals → `VIOLATED` → refused.
4. Request a 900 payment with two recorded signer approvals →
   `ALLOWED` → executes. The approvals are acquired and bound in the ruling;
   the approval-required path is covered deterministically in `tests/`.
5. Request a 900 payment to the approved vendor without approvals → `ALLOWED`
   → executes.
6. Request a payment to the `not_approved` vendor → `VIOLATED` → refused.
7. Amend the covenant to raise the threshold to 250,000 with two approvers and
   an elapsed timelock. Re-run the 120,000 payment without approvals → now
   `ALLOWED`.

Step 6 is the one that matters. It proves enforcement tracks the policy actually
in force, and that the guard cannot be edited by whoever it is blocking.

## Main risks

1. **Real consensus is observed only on one live run.** 39/39 transactions
   finalized with `MAJORITY_AGREE`; one judge needed three rounds. That is not a
   reliability claim. glsim still drives a split to `UNDETERMINED`.
2. **The excerpt check proves a clause is real, not that it applies.** A model can
   cite a genuine clause that does not support the operation. Irreducible here.
3. **A real model may be poor at citing.** The design is strict on purpose, and
   strictness that a live model cannot satisfy would make the guard unusable. Only
   a live run will show.
4. **Live models can be non-deterministic across leader and validators.** An
   earlier covenant with overlapping permissions produced `MAJORITY_DISAGREE`
   and failed closed. Disjoint amount thresholds fixed that run; high agreement
   requirements can still make a ruling fail closed, which is safe and useless.
5. **No appeal path.** A wrongly-blocked payment has no recourse. Currently a
   documented open decision, not an oversight.

## Go/no-go gate

Proceed while: the excerpt invariant holds under test; the refusal is
demonstrable on the simulator; and a single live ruling can be obtained. Stop and
re-scope if the live network cannot produce a reliable three-way agreement — at
that point the honest deliverable is the primitive plus its tests, not a demo.

**Gate status:** construction, the amended deployment, and the full live
demonstration passed. 39/39 transactions finalized with `MAJORITY_AGREE`; all
five enforcement outcomes matched. The live run is evidence for this
demonstration, not for consensus reliability.
