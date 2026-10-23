# Studionet run — 2026-10-20

> **Pre-amendment run.** This predates the evidence-provenance amendment. It
> contains no vendor/risk attestations and no signer approvals, and must not be
> cited for the provenance claim. The amended demo has not been redeployed yet.

First live deployment and demonstration of Covenant. Every transaction below
reached `FINALIZED` with consensus result `MAJORITY_AGREE`.

Raw data: [`live-run.json`](live-run.json) — 28 transactions, 22 reads.

## Network

| | |
|---|---|
| RPC | `https://studio.genlayer.com/api` |
| Chain id | `61999` |
| Runtime | `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |
| Deployer | `0x158e5ccAA39F7Db714B35Ae5fb6A5d925BE219cD` |
| Deployer balance | **0 wei** |

### The deployer had no funds, and did not need any

Studionet is gasless. Measured before deploying:

```
baseFeePerGas  0
eth_gasPrice   0
gasUsed        0   (every block)
```

A zero-balance account deployed both contracts successfully. The faucet was
never used. This is worth stating plainly because the conventional assumption —
"you need test tokens to deploy" — is wrong on this network, and the original
plan stalled on that assumption for some time.

Two deployments were discarded along the way: `0xEF30b4f4…` (timelock 3600s)
and `0x21A13c72…` (demo missing the `finalize_operation` step). Both are on-chain
and harmless. The pair below is the recorded run.

## Deployed

| Contract | Address | Methods |
|---|---|---|
| `covenant_guard` | `0xC5335e9A9F96fE77Ca8944E01e11085296E88556` | 21 (13 view, 8 write) |
| `treasury_vault` | `0xe2e7DaB935b5328E12f0D4c3F842481ed4c6497a` | 11 (7 view, 4 write) |

Constructor: covenant from `examples/covenant.md`, quorum `2`, timelock `60`s.
Sixty seconds is the contract's minimum, chosen so the timelock step is
observable inside one session.

Covenant digest at deploy:

```
afd824702fd05cd7c277adf84d8d1c9feba669eeda8ba50681b67259d363837b
```

This equals `python3 scripts/covctl.py digest examples/covenant.md` — the
off-chain compiler and the on-chain store agree.

## Results

| Step | Amount | Expected | Guard verdict | Final state | Balance moved |
|---|---|---|---|---|---|
| `op-small` | 900 | ALLOWED | `ALLOWED`, `CITED_CLAUSE`, excerpt verified | `FINALIZED` | **−900** |
| `op-big` | 120,000 | VIOLATED | `VIOLATED`, `POLICY_DENIAL`, excerpt verified | `REJECTED` | 0 |
| `op-prohibited` | 500 | VIOLATED | `VIOLATED`, `POLICY_DENIAL`, excerpt verified | `REJECTED` | 0 |
| `op-after` | 120,000 | ALLOWED (v2) | `ALLOWED`, excerpt verified | `FINALIZED` | **−120,000** |

Vault totals after the run:

```json
{"balance": 879100, "deposits_total": 1000000, "outflow_total": 120900}
```

The arithmetic closes: `1,000,000 − 900 − 120,000 = 879,100`.

### The point of the run: `op-big` and `op-after` are the same payment

Same amount, same recipient, same memo, same evidence, same vault, same guard.
The only difference is that the covenant changed in between.

`op-big` was judged against Clause 3 at a 50,000 threshold and rejected.
The covenant was then amended to raise the threshold to 250,000 by
quorum + timelock — two distinct approvers, then a 60-second wait, then
`activate_amendment`, taking the guard from version 1 to version 2:

```
v1  afd824702fd05cd7c277adf84d8d1c9feba669eeda8ba50681b67259d363837b
v2  5c011cde947dde5667c4bc74f00ae98f3d912bd661af42b92cfd9f457a5aa6d7
```

`op-after` was then resubmitted unchanged and **executed**. Enforcement tracks the
policy actually in force, not a snapshot taken at deploy time. The ruling for
`op-after` carries the v2 digest, confirming the guard judged against the amended
text.

## Consensus

| | |
|---|---|
| Transactions | 28, all `MAJORITY_AGREE` |
| Validators per round | 5 |
| Rounds needed | 1, 2 and 3 (all observed) |
| Rotations allowed | 5 |

`judge_operation` is the only nondeterministic method. It required 2 rounds for
`op-small`, 3 for `op-big`, and 1 for the others — real rotation, not a constant.
Validator votes on a representative round were `AGREE, AGREE, IDLE, AGREE, AGREE`.

Example ruling:

```json
{
  "verdict": "VIOLATED",
  "reason_code": "POLICY_DENIAL",
  "excerpt": "No single payment above 50000 GEN may be released without multisig approval from two distinct signers.",
  "excerpt_verified": true,
  "covenant_digest": "afd824702fd05cd7c277adf84d8d1c9feba669eeda8ba50681b67259d363837b",
  "model_failure": false
}
```

The model returned a verdict and a **verbatim** clause; the contract then checked
that the excerpt was a substring of its own stored covenant. It was, so
`excerpt_verified` is true. A paraphrase would have failed this check.

## Two things this run does not prove

**1. The fabricated-excerpt defence.** It requires the model to invent a clause,
which cannot be provoked from outside. It is proven with an injected
hallucinating model in `tests/direct/` and `tests/integration/`, and that
distinction should not be blurred in any write-up.

**2. That `ALLOWED` is the default for permitted operations.** It is not, and the
first attempt showed why. With the memo `"one-off large disbursement"` under the
amended covenant the model returned:

```
verdict: AMBIGUOUS   reason_code: EVIDENCE_INSUFFICIENT   excerpt: ""
```

The amended covenant prohibits certain things but never affirmatively permits a
one-off payment, so the model declined to infer permission. That is fail-closed
behaviour and arguably correct for a guard, but it means the covenant's wording —
not just its thresholds — determines the outcome. The recorded run therefore uses
a recurring vendor payment, which Clause 2 explicitly permits. Anyone assuming
"under the threshold, therefore allowed" would be wrong; this was an assumption
worth breaking early.

## Reproduce

```bash
python3 scripts/live_demo.py
```

Gasless, so no funding step, no faucet, no test tokens. Takes about four minutes,
including the timelock wait.
