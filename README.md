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

The policy facts the model reasons about are not supplied by the requester. A
procurement attestor and a risk attestor, fixed at deployment, write structured
claims on-chain; the guard reads them at ruling time, binds their digest into the
ruling, and the vault re-checks that binding before it moves anything. Provenance
is the attestor's authenticated transaction, not a paragraph attached to the
payment request.

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
- **The requester cannot supply the facts.** `propose_operation` takes the
  instruction and the subject, never evidence text. Vendor and risk claims are
  role-gated, subject-keyed, expiring records; approvals are bound to one exact
  operation binding. A missing or expired claim fails closed.

The evidence amendment was forced by a review finding, not added for polish: the
first version accepted a free-text evidence string, so a requester could assert
"vendor approved" and have the model reason about the assertion. The excerpt
check proved the policy clause existed; it did not verify the fact. See
`docs/THREAT_MODEL.md` §2.6.

---

## What is in here

```
contracts/covenant_guard.py    the primitive. 28 methods.
contracts/treasury_vault.py    a worked example that proves the guard is load-bearing.

tests/direct/                  190 tests. State machine, judge seam, evidence provenance.
tests/integration/             29 tests. Cross-contract lifecycle and consensus, on glsim.
probe/                         20 tests. Behavioural probes that reject invented SDK APIs.

scripts/preflight.py           architectural invariants + secret scan. Runs in CI.
scripts/covctl.py              covenant compiler and policy linter.
                               Also published standalone: github.com/vincentofweb3/covctl
fixtures/covenant_guard_constants.json
                               contract-derived vectors used by covctl selftest

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
  Methods: 28 (17 view, 11 write)

$ GENVM_VERSION=v0.3.0-rc7 genvm-lint check contracts/treasury_vault.py
✓ Validation passed
  Methods: 11 (7 view, 4 write)

$ python3 -m pytest tests/direct -q
190 passed

$ python3 -m pytest tests/integration -q
29 passed
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

**Pre-amendment run.** The addresses and evidence below predate the evidence-
provenance amendment and do not contain attestation or approval transactions. The
amended contracts are not deployed yet. Do not cite this run for the provenance
claim; re-run `scripts/live_demo.py` after the amendment is committed.

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

## The vault, and two decisions it forced

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

The second decision is the amendment. The vault does not forward an evidence
paragraph either. It supplies the recipient; the guard acquires the vendor and
risk claims for that recipient and the signers' approvals of that exact binding.
The vault refuses execution unless the ruling carries the same evidence subject
and digest that the guard stored on the operation.

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
   The evidence is contract-acquired, so the requester cannot inject facts.
5. **The excerpt check proves a clause is real, not that it applies.** A model may
   cite a genuine clause that does not support the operation. That residual
   semantic risk is irreducible here and is stated in `docs/THREAT_MODEL.md` §2.1.
6. **Consensus reliability is not demonstrated by green tests.** One successful
   fixture is not a reliability claim.
7. **No gas, cost, size or fuzzing analysis.**
8. **Quorum is a policy choice, not a proof.** `MIN_QUORUM = 2` means two keys
   can legitimately rewrite the covenant through the proper process.
9. **The evidence roles are trusted and immutable.** The contract proves which
   attestor wrote a claim and when; it does not prove the claim is true. There
   is no rotation or revocation, and a compromised attestor key can lie until
   redeployment.
10. **Evidence is a ruling-time snapshot.** A claim valid when the guard ruled
    may expire before the vault executes. The vault does not re-check expiry.

## Licence

See the repository. The contracts are provided as-is, with no warranty and with
no claim of production readiness.