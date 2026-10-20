# Evidence records

One file per live run, named `studionet-<YYYY-MM-DD>.md`.

No run has been performed yet. `evidence/studionet-PENDING.md` below is the
template, and the sections marked **must not be skipped** are the ones that keep
this honest.

## The rule this folder exists to enforce

For every transaction, three facts are recorded **separately**:

| Fact | Source | What it proves |
|---|---|---|
| protocol status | receipt status | the transaction was accepted by the network |
| consensus result | receipt consensus fields | validators reached an outcome |
| leader execution | receipt execution fields | *the method actually ran successfully* |

A protocol `FINALIZED` is **not** evidence that the contract method succeeded.
These diverge in practice, and a prior project of this author recorded a
transaction that reached `FINALIZED` / `MAJORITY_AGREE` while its leader
execution was `ERROR` and no application state was written.

## Failed transactions are published too

A table containing only successes is evidence of nothing. If a step failed, the
row stays, with the failure recorded.

## Two hard constraints on simulator-sourced claims

Do not import either of these into a live evidence file:

1. `sim_call` reports a hardcoded `"status": "FINALIZED"` and never runs
   consensus. A `FINALIZED` from the simulator is a constant.
2. glsim's storage rollback after a failed consensus is incomplete. A simulator
   "successful" write is not evidence of atomicity.

See `docs/THREAT_MODEL.md` §4 and `state/REVIEW_ROUND3.md`.
