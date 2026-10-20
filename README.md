# Covenant

**A semantic treasury guard for GenLayer Intelligent Contracts.**

An organisation's treasury operates under a written policy. An ordinary contract
can enforce the machine-checkable part of that policy. It cannot enforce the part
that actually matters —

> the counterparty must be on the approved vendor list
> no swap with assets the risk desk has flagged
> recurring payments to a provider are permitted, one-off transfers to the same
> provider are not

— because enforcing prose requires reading it and judging whether a specific
action complies. Covenant puts that prose on-chain and asks GenLayer's
consensus whether each proposed movement complies, **before the value moves**.

It is not doable on an EVM. That is the whole argument.

---

## The idea

The obvious design — ask a model, take its answer — is not a security property.
The interesting problem is **constraining what the model is allowed to do so that
the deterministic layer can audit every influence it had.**

The model gets exactly two degrees of freedom:

1. a verdict from a three-value enum — `ALLOWED`, `VIOLATED`, `AMBIGUOUS`; and
2. a **verbatim excerpt** from the covenant text that the verdict relies on.

The second is load-bearing. The contract stores the covenant's own bytes and
checks, deterministically and on-chain, that an `ALLOWED` excerpt is a substring
of them. A verdict citing a clause that does not exist is discarded, not weighted,
not averaged. The validator repeats the check independently.

**The model can choose which real clause to invoke. It cannot invent one.**

Three rules follow, and they are the real content of the contract:

- **Ambiguity never defaults to permission.** `AMBIGUOUS` blocks.
- **A model failure is not a ruling.** Malformed output, provider failure and
  validator disagreement produce *no permission* either — and are recorded
  distinctly from a ruling of `VIOLATED`.
- **The guard cannot be rewritten by whoever it is guarding.** Covenant
  amendments need a quorum of distinct approvers plus a timelock.

---

## What is in here

```
contracts/covenant_guard.py    the primitive. 21 methods.
contracts/treasury_vault.py    a worked example that proves the guard is load-bearing.

tests/direct/                  90 tests. Single-contract state machine and judge seam.
tests/integration/             24 tests. Cross-contract lifecycle and consensus, on glsim.

scripts/preflight.py           architectural invariants + secret scan. Runs in CI.
scripts/covctl.py              covenant compiler and policy linter.

docs/ARCHITECTURE.md           durable state, state machines, trust boundaries
docs/CONSENSUS.md              the leader/validator protocol, precisely
docs/THREAT_MODEL.md           threats → controls, and what is explicitly not covered
state/MEMORY.md                verified GenLayer API facts, each one read off an artifact
state/REVIEW_ROUND1.md         independent audit of the guard, with refuted findings
state/REVIEW_ROUND2.md         independent audit of the vault
state/REVIEW_ROUND3.md         independent audit of the consensus claims
```

---

## Verification

```console
$ python3 scripts/preflight.py
✓ Preflight passed (...)

$ GENVM_VERSION=v0.3.0-rc7 genvm-lint check contracts/covenant_guard.py
✓ Validation passed
  Methods: 21 (13 view, 8 write)

$ GENVM_VERSION=v0.3.0-rc7 genvm-lint check contracts/treasury_vault.py
✓ Validation passed
  Methods: 11 (7 view, 4 write)

$ python3 -m pytest tests/direct -q
90 passed

$ python3 -m pytest tests/integration -q
24 passed
```

`GENVM_VERSION=v0.3.0-rc7` is **required** for the `genvm-lint check` commands.
Without it the linter resolves the newest cached GenVM bundle, which on many
machines is the newer generation and does not ship this contract's runner. See
`state/MEMORY.md` §11 for the full explanation.

## Deployment target

| | |
|---|---|
| Network | **studionet** (stable hosted) |
| RPC | `https://studio.genlayer.com/api` |
| Chain id | `61999` |
| Contract runtime | `py-genlayer:1jb45aa8…` (GenVM `v0.3.0-rc7`) |

This exact runtime has already been deployed to this exact chain twice by this
author (`docket`, `legxusai`). It was chosen for that reason, not because it is
current. `studio-dev` (chain 61997) is a separate preview network and is not a
target.

Studionet charges **no gas** — `baseFeePerGas` and `gasPrice` are 0, and every
block reports `gasUsed` 0 — so no funded account or faucet is needed.

### Live deployment

| Contract | Address |
|---|---|
| `covenant_guard` | `0xC5335e9A9F96fE77Ca8944E01e11085296E88556` |
| `treasury_vault` | `0xe2e7DaB935b5328E12f0D4c3F842481ed4c6497a` |

28 transactions, all `FINALIZED` with `MAJORITY_AGREE`, 5 validators, 1–3 rounds.
A 120,000 payment was refused under covenant v1, the covenant was amended by
quorum and timelock, and the same payment executed under v2. Balances reconcile
exactly: `1,000,000 − 900 − 120,000 = 879,100`.

Evidence: [`evidence/studionet-2026-10-20.md`](evidence/studionet-2026-10-20.md).
Reproduce: `python3 scripts/live_demo.py`.

---

## The vault, and one decision it forced

A guard nobody must consult is a guard nobody will consult, so `treasury_vault.py`
exists to show the enforcement happening: it asks the guard to rule, then
re-reads the guard over a cross-contract call and refuses to move funds without a
verified `ALLOWED` ruling.

**The decision the whole project rests on: the amount must be inside the judged
text.**

The first version kept `amount` and `recipient` in vault storage and submitted a
human-written summary for judgment. That is exploitable, and it defeats the
entire project — the model judges the prose and never sees the number, so a
proposer writes `summary="routine small payment"` and moves 1,000,000 past a
50,000 threshold. The judge sees nothing wrong.

So the vault submits a canonical binding as the operation summary:

```json
{"amount":120000,"memo":"recurring monthly retainer","recipient":"0x4444…"}
```

and refuses execution unless the guard holds that exact payload. Authority
obtained for one payload cannot be spent on another.

No demo would ever have surfaced that bug, because in a demo the proposer is
honest.

---

## Honest limitations

These are not hedges. They are the claims this project does **not** make.

1. **Real consensus is unobserved.** The design assumes the GenVM runtime drops a
   transaction whose validators disagree. `tests/integration/test_consensus.py`
   drives glsim's `run_consensus` directly — because `sim_call` and `gen_call`
   never run consensus at all, and `sim_call` hardcodes `"status": "FINALIZED"`
   — and shows a split producing `UNDETERMINED`. But glsim is not the runtime.
2. **No live model was ever consulted.** Every test substitutes a fixed reply
   function. Whether a real model cites real clauses, and agrees with other real
   validators, is unmeasured.
3. **Not escrow, not settlement.** Vault balances are internal accounting
   entries. No native token is transferred and nothing here is custody of funds.
4. **The prompt-injection denylist is not a security boundary** and is not
   claimed to be. What holds is structural: authority requires a substring of
   stored covenant bytes, so an injected instruction cannot manufacture it.
5. **The excerpt check proves a clause is real, not that it applies.** A model may
   cite a genuine clause that does not support the operation. That residual
   semantic risk is irreducible here and is stated in `docs/THREAT_MODEL.md` §2.1.
6. **Consensus reliability is not demonstrated by green tests.** One successful
   fixture is not a reliability claim.
7. **No gas, cost, size or fuzzing analysis.**
8. **Quorum is a policy choice, not a proof.** `MIN_QUORUM = 2` means two keys
   can legitimately rewrite the covenant through the proper process.

## Licence

See the repository. The contracts are provided as-is, with no warranty and with
no claim of production readiness.