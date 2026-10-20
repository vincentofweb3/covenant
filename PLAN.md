# Covenant — a semantic treasury guard for GenLayer

**Status:** planned. No contract code written yet.
**Target:** studionet, RPC `https://studio.genlayer.com/api`, chain id `61999`.
**Std lib:** OLD generation, header `py-genlayer:1jb45aa8…` (see `state/MEMORY.md` §1, §9).

---

## 1. The problem

An organisation treasury operates under a *written* policy. Some of it is machine-checkable
("never spend more than 50,000"), and an EVM contract enforces that fine. But most of the
policy that actually matters is **prose**: "the counterparty must be on the approved vendor
list", "no swap with assets our risk desk has flagged as high-risk", "recurring payments to
a provider are permitted, one-off transfers to the same provider are not".

An EVM contract cannot read that clause and decide whether a specific proposed transfer
complies with it. That is the gap. Today those clauses are enforced by a human reading a
dashboard, which is exactly the control that fails at scale and under pressure.

**Covenant** puts the written policy on-chain and asks GenLayer's consensus — the
Equivalence Principle — whether each proposed movement complies, before the value moves.

It is not doable on an EVM. That is the whole argument, and it has to survive a hostile
review, because "we called an LLM" is not a security property.

## 2. The thesis: verdict-conditioned determinism

The interesting engineering problem is not *asking a model*. It is **constraining what the
model is allowed to do so that the deterministic layer can audit every influence it had.**

The model is permitted exactly two degrees of freedom:

1. A verdict from a **three-value enum** — `ALLOWED`, `VIOLATED`, `AMBIGUOUS`.
2. A **verbatim excerpt** from the covenant text that the verdict relies on.

Freedom #2 is the load-bearing one. The contract stores the covenant's own bytes and
checks, deterministically and on-chain, that the returned excerpt is a substring of them.
An `ALLOWED` verdict with no excerpt, or with an excerpt that does not occur in the
covenant, is rejected outright. A validator disagreement over that check is a `Disagree`,
which fails closed.

So the model can influence state **only through a channel the contract can re-verify
without the model**. Fabricated authority becomes structurally detectable rather than a
matter of trust. That is the sentence the submission leads with.

Three rules follow, and they are the contract's real content:

- **Ambiguity never defaults to permission.** `AMBIGUOUS` blocks. It never becomes a
  silent `ALLOWED`.
- **A model failure is not a failure of the covenant.** Provider error, malformed output
  and validator disagreement produce *no ruling at all* — distinct from a ruling of
  `VIOLATED`. Liveness and safety are separate concerns and must stay separate.
- **The guard cannot be rewritten by whoever it is guarding.** Covenant amendments need a
  quorum of distinct approvers plus a timelock. A single compromised key cannot loosen
  the policy to unblock its own pending transfer.

## 3. Why this and not another adjudicator

The space is saturated. Our friend shipped evidence-gated batch adjudication (`verdictBatch`)
and an evidence-gated saga engine (`knot`). This author has shipped dispute resolution
(`legxusai`), a containment guard for autonomous agents, and a case-dispatch registry (`docket`).
A fifth adjudication-shaped contract would be read as a near-duplicate and would be scored
as such.

Covenant is a **gate on value movement**, not a referee between disputants. There is no
claim, no counterparty, no appeal-from-loser. The semantic question is "does this concrete
action comply with this standing written rule", which is a different and more reusable
primitive.

## 4. Architecture

```
                    ┌──────────────────────────┐
                    │       TreasuryVault      │   holds value
                    │  (reference participant) │
                    └────────────┬─────────────┘
                                 │ 1. request_move(op)
                                 ▼
                    ┌──────────────────────────┐
                    │       CovenantGuard      │   holds covenant bytes,
                    │  (the deployable IC)      │   versions, quorum cfg
                    └────────────┬─────────────┘
                                 │ 2. judge → run_nondet
                                 │    leader: exec_prompt → ALLOWED/VIOLATED/AMBIGUOUS + excerpt
                                 │    validator: re-derives independently, asserts
                                 │      (a) same verdict, (b) same digests,
                                 │      (c) excerpt is a substring of stored covenant
                                 ▼
                    ┌──────────────────────────┐
                    │  ruling + receipt stored  │  digest-bound, verifiable offline
                    └──────────────────────────┘
```

Two contracts, deliberately. `CovenantGuard` is the primitive; `TreasuryVault` exists only
to prove the guard is **load-bearing** — it must refuse to move funds without a verified
`ALLOWED` ruling. A guard nobody must consult is a guard nobody will consult, and the demo
has to show the refusal actually happening on-chain.

The guard is reusable: any value-holding GenLayer contract can register. The vault is the
worked example, not part of the primitive.

### The decision the whole design rests on

**The amount must be inside the judged text.**

The obvious design keeps `amount` and `recipient` in vault storage and sends the guard a
human-written `summary`. That is exploitable, and it defeats the entire project: the model
judges the summary, never the number. A proposer writes
`summary="routine small vendor payment"` and moves 1,000,000 past a covenant threshold of
50,000. The judge sees nothing wrong.

So `TreasuryVault` submits a canonical binding of the payload as the operation summary:

```json
{"amount":120000,"memo":"recurring monthly retainer","recipient":"0x4444…"}
```

serialised with sorted keys and compact separators. On execution the vault reads the
operation back from the guard and refuses unless the summary is byte-identical to the one
it submitted and the binding digest matches. Authority obtained for one payload cannot be
spent on another.

The vault also does not merely trust `is_downstream_actionable`. It re-reads the ruling and
independently requires `excerpt_verified`. If the guard is ever compromised, a ruling whose
clause was never verified still cannot move funds.

No demo would ever have surfaced this bug, because in a demo the proposer is honest.

### Contract surface (target ~20 methods on the guard, ~7 on the vault)

Views: `get_covenant`, `get_covenant_hash`, `get_version`, `get_ruling`, `get_operation`,
`is_downstream_actionable`, `get_amendment`, `compute_excerpt_proof`.

Writes: `register_vault`, `propose_operation`, `judge_operation` (the consensus seam),
`finalize_operation`, `propose_amendment`, `approve_amendment`, `activate_amendment`,
`deposit` (payable), `cancel_operation`.

Every write takes `action_id` first and is idempotent: replay with identical args returns
the recorded result, replay with different args reverts. Same convention as the friend's
`verdictBatch`, and it is the right one.

## 5. Amendment flow (the capture-resistance half)

Amendments are a quorum + timelock so the guard cannot be edited by whoever it blocks:

```
DRAFT --approve x quorum--> APPROVED --timelock elapsed--> ACTIVE
   |                             |
   |                             +-- base_version no longer current--> EXPIRED
   +-- below quorum: not activatable

Every superseded version is retained in `covenant_history`, so a ruling can always
be re-read against the exact policy text that was in force when it was made.
Without that, an audit trail is worthless the first time the policy changes —
which is the interesting case.

**`EXPIRED` means stale, not "too late".** The timelock is a *minimum wait*, not a
deadline: once quorum is met and the wait has passed the amendment is ready, and
failing it forever would let one unactivated amendment block a policy change
indefinitely. The condition that genuinely expires an amendment is staleness — it
was written against a covenant version that is no longer in force. Activating it
would silently discard whatever superseded it.

This was not the original design. The first implementation treated an elapsed
timelock as expiry, which contradicted its own activation path: the view reported
`EXPIRED` while `activate_amendment` happily proceeded. A test caught the
contradiction and the semantics were corrected, not the test.

## 6. Live demo path (Studionet)

Eight steps, each producing a transaction hash. This is the evidence record's spine.

1. Deploy `CovenantGuard` with covenant v1 (vendor list, threshold, recursion clause).
2. Deploy `TreasuryVault`, register it against the guard.
3. `deposit` — fund the vault. Read back balance.
4. Propose a **compliant** move → `ALLOWED` → `finalize_operation` → value moves.
   Read back the ruling, its excerpt, and the receipt digest.
5. Propose a **violating** move (over threshold, no multisig) → `VIOLATED` → blocked.
   Read back that funds did not move.
6. Propose an **ambiguous** move (evidence that could be read either way) → `AMBIGUOUS`
   → blocked, and explicitly distinguishable from step 5 in state.
7. Amend the covenant to raise the threshold → quorum approvals → timelock → activate.
   **Re-run step 5's exact operation.** It is now `ALLOWED`.
8. Recompute every receipt digest offline and confirm they match what the chain returned.

Step 7 is the one that makes the submission defensible. It proves the amendment path is
real and that the guard's behaviour tracks the policy actually in force.

## 7. Repo layout

```
contracts/covenant_guard.py      the primitive
contracts/treasury_vault.py      reference participant, proves the guard is load-bearing
tests/direct/                    ~45-55 tests, no network
tests/integration/               ~4-5 glsim cross-contract lifecycle tests
scripts/covctl.py                covenant compiler + policy linter + Studio deploy/evidence helper
scripts/preflight.py             dependency-free invariant + secret scanner, runs in CI
docs/BRIEF.md                    problem, decision, go/no-go gate
docs/ARCHITECTURE.md             durable state, state machines, trust boundaries
docs/CONSENSUS.md                leader/validator protocol, fail-closed policy
docs/THREAT_MODEL.md             threats -> controls, with explicit non-goals
state/MEMORY.md                  verified GenLayer API facts (already written)
state/PROJECT-STATE.md           checkpoint
state/REVIEW_ROUND1.md           independent audit findings + adjudication table
evidence/studionet-<date>.md     tx table, verbatim state reads, honest limitations
```

## 8. Submission mapping

| Contribution type | Range | What earns it | Target |
|---|---|---|---|
| Contract Deployed | 25-500 | 2 contracts, cross-contract messaging, consensus seam, external evidence | 200-300 |
| GitHub Repository | 50-300 | full repo, CI green, 50+ tests | 100-200 |
| Tutorial / Documentation | 50-200 | `docs/` + reproducible demo walkthrough | 75-150 |
| Developer Tool | 100-500 | `covctl`: covenant compiler, policy linter, deploy/evidence helper | 100-250 |

Total target 475-900 BP. The first two alone clear 150 if the rest goes badly, which is
the point of building the repo so that any two rows stand on their own.

## 9. Phases

**Phase 0 — prove the toolchain before writing a line of contract code.**
The SDK here is five months stale and two divergent envs exist. Write a throwaway
contract, run `genvm-lint check` on it, run one `gltest` direct test, run one `glsim`
test. Settle the five open questions in `state/MEMORY.md` §10. **If any of them fails, the
architecture in §4 changes and that is cheaper to learn now than after 1000 lines.**
Exit criteria: a `main`-like branch with three green commands recorded verbatim.

**Phase 1 — `covenant_guard.py`.** Covenant storage + versioning, operation lifecycle,
and the judge seam. The judge seam lands first and in isolation, with its own tests,
because it is the part that is hard to get right.

**Phase 2 — `treasury_vault.py`. DONE.** Cross-contract messaging with `on="finalized"`
(confirmed available and the default), idempotent operation IDs, the payload binding, and
the refusal path. 18 glsim tests including the full demo's step 7 — the same 120,000
payment refused under covenant v1, permitted after a quorum-approved amendment raises the
threshold.

**Phase 3 — tests.** Direct mode is for the single-contract state machine and
the judge seam. It **cannot** test two contracts: storage slots are shared across
instances, and cross-contract calls are dropped (proved in Phase 0). So the split
is fixed, not stylistic:

- `tests/direct/` — covenant storage, versioning, quorum/timelock, and the judge
  seam in depth, including a dedicated adversarial suite: fenced and
  prose-wrapped JSON, truncated and undecodable output, **a forged excerpt that
  does not occur in the covenant**, forged digests, prompt injection carried in
  the operation description, leader/validator disagreement, provider failure,
  oversized fields.
- `tests/integration/` (glsim) — the cross-contract lifecycle, and specifically
  the central claim: **the vault refuses to move funds without an `ALLOWED`
  ruling.** That assertion cannot live in direct mode. It is the whole point of
  the guard, so it must be a real glsim test and not an inference.

Direct mode also does not run the validator for you. Every judge test must call
`direct_vm.run_validator()` and assert its boolean, swapping the mock in between
to simulate divergence.

**Phase 4 — `covctl`.** Covenant compiler (prose → structured clauses → digest), policy
linter (contradictory rules, unbounded thresholds, missing fallbacks), and a Studio
deploy/evidence helper. This is the separate Developer Tool submission — it must be
genuinely reusable by a contract the author did not write, or it does not qualify.

**Phase 5 — docs + state.** Written against the code, not ahead of it.

**Phase 6 — CI.** `preflight` → `compileall` → `genvm-lint check` on both contracts →
`pytest tests/direct`; second job runs `pytest tests/integration`.

**Phase 7 — deploy + evidence record.** studionet, 8 demo steps, every tx hash, protocol
status, consensus result, and **leader execution result recorded as three separate
columns**. Publish the failures too. A table with a hidden failed transaction is worth
less than no table.

**Phase 8 — independent review + submission packaging.**

## 10. The honest-limitations rule

Every doc, and the final submission, states plainly what this is **not**:

- Bonds/ledger entries are internal accounting, not escrow. (n/a here — but the same
  discipline applies to anything we add.)
- Consensus reliability is **not** demonstrated by one successful fixture.
- A denylist on prompt content is not a security boundary.
- SSRF filtering of `gl.nondet.web` results does not follow redirects or resolve DNS.
- A tx reported `FINALIZED` by the protocol is not proof the contract method succeeded.

This is copied deliberately from how both friends' repos are written. It is the clearest
signal available that the work was actually reviewed, and it is what separates a 150-point
submission from a 300-point one.

## 11. Kill criteria

Stated up front so the decision is not made emotionally later:

- If Phase 0 shows the OLD std lib cannot do cross-contract messaging cleanly, Phase 2
  becomes materially harder → re-scope to a single self-contained contract and say so in
  the README rather than shipping a weaker two-contract design.
- If live judging on Studio proves too flaky to demo three distinct verdicts, we ship the
  `ALLOWED`/`VIOLATED` path only and disclose the `AMBIGUOUS` path as untested live.

**Phase 0-2 result: neither criterion fired.** Both contracts validate, 116 tests green, and
the refusal proof runs on glsim. Phase 2 did force one design change — binding the payload
into the judged text — recorded in §4 and in `state/REVIEW_ROUND2.md`.

**Phase 0 result (2026-10-15): neither kill criterion fired.** All four gates green
— see `state/PROJECT-STATE.md`. Cross-contract `emit(on="finalized")` round-trips on
glsim, and `on` defaults to `'finalized'`. The two-contract design stands.

Three facts from Phase 0 now constrain the rest of the build. All are recorded with
evidence in `state/MEMORY.md` §10-13:

1. **One contract per file**, enforced by the linter *and* by the std lib at import.
2. **`response_format='text'` plus our own brace parser**, not `'json'`. This is what
   makes prose-wrapped and fenced model output work, and it keeps the mock layer out of
   the picture.
3. **Any cross-contract read must null-check before coercing to `str`.** A failed read
   otherwise becomes the plausible-looking string `"None"`. Found in our own probe
   before it reached production.