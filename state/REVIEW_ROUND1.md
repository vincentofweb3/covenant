# Independent review — round 1 (Phase 1)

Adversarial pass over `contracts/covenant_guard.py` and the direct suite, run
before any of it was extended. Findings are recorded with a verdict and the
concrete action taken. **Refuted findings are kept, not deleted** — a reviewer who
never records a refutation has probably not looked hard enough.

Date: 2026-10-15. Scope: the judge seam, the operation lifecycle, and the
amendment flow of `covenant_guard.py`. The vault and the deployment were
out of scope and are reviewed in `state/REVIEW_ROUND2.md`.

---

## 1. Bottom line

The central claim — that a model cannot grant permission by citing a clause that
does not exist — is implemented, tested adversarially, and holds. Four defects
were found and fixed during Phase 1, two of them by tests written before the fix
existed. One design contradiction was found and resolved in favour of the design
rather than the test.

| # | Severity | Finding | Verdict | Action |
|---|---|---|---|---|
| P1-1 | **P1** | Idempotent replay returned the action envelope, not the result | **confirmed** | fixed; `_action_result` added, 5 call sites |
| P1-2 | **P1** | `get_ruling` emitted a Python `repr`, not JSON | **confirmed** | fixed; `_canonical` used |
| P1-3 | **P1** | State guard ran *before* the replay check, so a legitimate retry failed | **confirmed** | fixed; reordered in 3 methods |
| P1-4 | **P1** | View reported `EXPIRED` for an elapsed timelock while the write path allowed activation | **confirmed** | semantics corrected; staleness now the expiry condition |
| P2-1 | P2 | A pre-decoded `dict` reply was discarded as malformed | confirmed (by design) | accepted deliberately; excerpt check unchanged |
| P2-2 | P2 | Digest echo was all-or-nothing | confirmed; **changed by decision** | absent tolerated, wrong rejected |
| P3-1 | P3 | `_now()` returns 0 on an unparseable stamp | refuted as reachable | see §4 |
| P3-2 | P3 | The control-marker denylist is bypassable | **confirmed and accepted** | documented as a non-boundary |
| P4-1 | P4 | `get_amendment_status` recomputes state on read | confirmed; accepted | deliberate, documented |

---

## 2. The central claim, examined adversarially

The design says an `ALLOWED` verdict is only honoured when it cites a verbatim
substring of the covenant bytes the contract already stores. Attempts to break
that, and the result:

| Attack | Result | Test |
|---|---|---|
| Cite a confident, specific, non-existent clause | `AMBIGUOUS` / `UNVERIFIED_EXCERPT` | `test_allowed_verdict_citing_a_fabricated_clause_is_discarded` |
| Claim `ALLOWED` with no excerpt at all | `AMBIGUOUS` / `UNVERIFIED_EXCERPT` | `test_allowed_verdict_with_no_excerpt_is_discarded` |
| Cite a real clause with one extra space | `AMBIGUOUS` — verbatim means verbatim | `test_excerpt_differing_only_by_whitespace_is_not_verbatim` |
| Cite a real clause on a **VIOLATED** verdict | citation dropped, denial kept | `test_violated_verdict_drops_a_fabricated_citation_but_keeps_the_denial` |
| Pre-decode the JSON to skip our parser | still hits the excerpt check | `test_a_pre_decoded_mapping_cannot_bypass_the_excerpt_check` |
| Validator sees a different model output | `Disagree` upstream | `test_validator_disagrees_when_the_model_output_changes` |
| Put `}` inside a quoted string to truncate the object | brace scanner respects strings | `test_a_brace_inside_a_quoted_string_does_not_end_the_object_early` |

The property that matters: **no input reaches `ALLOWED` without a string that is
byte-for-byte present in stored state.** The model chooses which real clause to
invoke. It cannot invent one.

## 3. P1 findings in detail

### P1-1 — Replay returned the wrong value (confirmed, fixed)

`_action_replay` returned `True`, and the five call sites then returned
`self.actions[action_id]` — the `{"digest":…, "result":…}` envelope — instead of
the result. A caller using the same `action_id` twice would receive two different
shapes depending on which call it observed. Idempotency that changes the answer
is not idempotency.

**Fix.** Added `_action_result`, which returns the recorded payload. All five
call sites updated. Regression: `test_vault_registration_replay_is_idempotent`.

### P1-2 — `get_ruling` was not JSON (confirmed, fixed)

`str(json.loads(record).get("ruling", {}))` produced a Python dict `repr` with
single quotes. `json.loads` on the result raised `JSONDecodeError`. The view was
unreadable by any consumer.

**Fix.** Return `_canonical(ruling)`, and `""` when there is no ruling rather
than `{}`, so "no ruling" and "empty ruling" are distinguishable.

### P1-3 — State guard preceded the replay check (confirmed, fixed)

`judge_operation` checked `state == PROPOSED` *before* consulting the action
ledger. After a successful judgment the state is `RULED`, so an idempotent retry
with the same `action_id` was rejected — the second call reported "operation is
not awaiting a ruling" for an operation that *had* been ruled by that very call.

This is the classic ordering mistake: idempotency must be checked before any
state-dependent guard, because the whole point is to tolerate a repeat after
state has moved.

**Fix.** Reordered in `judge_operation`, `finalize_operation` and
`cancel_operation`. Regression:
`test_judge_replay_with_same_arguments_is_idempotent`.

### P1-4 — View and write disagreed about expiry (confirmed, design fixed)

`get_amendment_status` reported `EXPIRED` once `now > activate_after`.
`activate_amendment` required only `now >= activate_after`. So for any amendment
past its deadline, the view said expired while the write succeeded. Two APIs
giving contradictory answers about the same object is worse than either being
alone.

The fix was not to reconcile the two. It was to notice that **the concept was
wrong**: a timelock is a minimum wait, not a deadline. There is no reason to
refuse a properly-approved change merely because nobody got round to activating
it — doing so hands a veto to anyone who can simply not call the activate method.

**Fix.** `EXPIRED` now means *stale*: the amendment's `base_version` is no
longer the covenant in force. That is a real condition with a real consequence —
activating a stale amendment would silently discard whatever superseded it — and
activation now refuses it explicitly.

New regression: `test_a_stale_amendment_cannot_clobber_the_current_covenant`,
which activates one amendment and proves a second, already-approved one cannot
then overwrite it.

## 4. Refuted and non-actionable

### P3-1 — "`_now()` returns 0 on an unparseable stamp, so a stale deadline passes"

**Refuted as reachable in v1.** `datetime` is a typed field of `MessageRawType`
and is always ISO-8601 on a real deployment. Returning `0` on an unparseable
value is a safe fallback, not a hole: `0` is the earliest possible timestamp, so a
failed parse makes a timelock *longer*, never shorter. It cannot be used to
bypass a wait.

Noted for v2: if the field ever becomes attacker-influenced, the fallback should
become a revert rather than a number.

### P3-2 — The control-marker denylist is trivially bypassable

**Confirmed and accepted.** `"ignore previous instructions"` can be written a
thousand ways. The list is not a security boundary and is not claimed to be one —
the docstring and `PLAN.md` §11 say so. Its only job is to make the crudest
attempt loud rather than silent. The actual protection is structural: the excerpt
must exist in stored bytes, so an injected instruction cannot manufacture
authority however it is phrased.

### P2-1 — Pre-decoded replies discarded

Confirmed the behaviour, then decided against "fixing" it. `response_format=
'text'` returns a `str` on the VM, but a harness that pre-decodes bare JSON hands
back a `dict`. Rejecting it would be correct-by-accident and brittle. Accepting
it costs nothing: the excerpt check runs identically either way, which
`test_a_pre_decoded_mapping_cannot_bypass_the_excerpt_check` pins.

This is also the confirmed truth about the harness — a bare-JSON mock returns
`dict`, prose-plus-JSON returns `str`. Measured, not assumed.

### P2-2 — Digest echo policy

Originally the design required an exact echo, mirroring `verdictBatch`. Changed
by decision after considering live behaviour: demanding that a model reproduce a
64-hex digest reliably would fail often enough to make the contract look broken,
and the property was never load-bearing. The ruling is bound to the operation by
construction, not by the model's claim.

The shipped rule is asymmetric and documented: an **absent** digest field is
tolerated (robustness), a **wrong** one is rejected (`DIGEST_MISMATCH`, because it
means the model read different input). Both branches are tested.

## 5. What this review does not establish

Stated so the findings are not read as stronger than they are.

1. **No live consensus was observed.** Every validator assertion here is direct
   mode, which returns the leader's value and defers the validator to
   `run_validator()`. That the GenVM runtime *drops* a transaction on real
   validator disagreement is not proven by these tests. It is the reason the seam
   fails closed on chain, and it needs a live run or a glsim multi-validator run.
2. **The model is a fixed reply function.** These tests prove normalisation,
   excerpt verification and the state machine. They prove nothing about whether a
   real model produces good rulings, and nothing about whether two real models
   agree.
3. **No cross-contract behaviour is covered.** The guard has not yet been wired
   to a vault. Direct mode cannot deliver cross-contract calls at all.
4. **The amendment flow has not been exercised against a real clock.** All
   timing tests write `gl.message_raw["datetime"]` in place.
5. **The control-marker list was not reviewed adversarially.** It is not a
   boundary, so a review of it would be theatre.
6. **No gas, cost or size analysis.** `MAX_*` bounds exist but their adequacy
   under real prompt sizes is unmeasured.

## 6. Regression sweep

| Expected | Observed |
|---|---|
| `genvm-lint lint` passes | ✓ 3 checks |
| `genvm-lint check` passes, 21 methods | ✓ 13 view, 8 write |
| direct suite green | ✓ 78 passed |
| no test asserts on a second contract's storage | ✓ single-contract suite |
| every `judge_operation` path has a validator assertion | ✓ `run_validator()` in 2 tests, negative case included |

## 7. Recommended next work

1. Build `treasury_vault.py` and prove on **glsim** that the vault refuses to move
   funds without a finalized `ALLOWED` ruling. That is the central claim of the
   guard and it is currently untested.
2. Run a multi-validator glsim configuration to observe real disagreement, which
   finding §5.1 flags as unproven.
3. Write `docs/THREAT_MODEL.md` against §5 rather than from memory.