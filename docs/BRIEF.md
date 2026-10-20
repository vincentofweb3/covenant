# Brief

## Decision

**Go — with the payload binding, which was not in the original design.**

The initial plan was a guard that asks a model whether an operation complies
with a written covenant. Phase 2 found that the obvious version of the vault is
exploitable, because the amount never reaches the model. The binding decision
(§ "The decision the whole design rests on", `PLAN.md` §4) makes the design
work and was worth the rework.

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
| Payment proposer | request anything | cause an `ALLOWED` verdict |
| Vault | execute against a verified ruling | rule for itself — enforced by preflight |
| Model | cite a clause | invent one |

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

## Smallest live end-to-end slice

1. Deploy the guard with a four-clause covenant, quorum 2, timelock 1 hour.
2. Deploy the vault, register it, deposit.
3. Request a 120,000 payment → `VIOLATED` (over Clause 3) → `execute_payment`
   reverts, balance unchanged.
4. Request a 900 payment → `ALLOWED` → executes, balance drops by 900.
5. Ask the model to allow the 120,000 by citing a clause that does not exist →
   `AMBIGUOUS` / `UNVERIFIED_EXCERPT` → still refused.
6. Amend the covenant to raise the threshold to 250,000 with two approvers and
   an elapsed timelock. Re-run step 3's identical payment → now `ALLOWED`.

Step 6 is the one that matters. It proves enforcement tracks the policy actually
in force, and that the guard cannot be edited by whoever it is blocking.

## Main risks

1. **Real consensus is unobserved.** The design assumes the GenVM runtime drops a
   transaction whose validators disagree. Demonstrated on glsim; not on the
   runtime.
2. **The excerpt check proves a clause is real, not that it applies.** A model can
   cite a genuine clause that does not support the operation. Irreducible here.
3. **A real model may be poor at citing.** The design is strict on purpose, and
   strictness that a live model cannot satisfy would make the guard unusable. Only
   a live run will show.
4. **Live models may be non-deterministic across leader and validators.** High
   agreement requirements could make every ruling fail closed, which is safe and
   useless.
5. **No appeal path.** A wrongly-blocked payment has no recourse. Currently a
   documented open decision, not an oversight.

## Go/no-go gate

Proceed while: the excerpt invariant holds under test; the refusal is
demonstrable on the simulator; and a single live ruling can be obtained. Stop and
re-scope if the live network cannot produce a reliable three-way agreement — at
that point the honest deliverable is the primitive plus its tests, not a demo.

**Gate status: passed for construction, unpassed for live deployment.** Phases 0-3
are complete; the live run has not been attempted.
