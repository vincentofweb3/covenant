# Independent review — round 3 (the consensus claims)

Adversarial pass over the claims this project makes about consensus, triggered by
rounds 1 and 2 both flagging "real consensus is unobserved" as the largest
unproven assumption.

Date: 2026-10-15. Scope: `tests/integration/test_consensus.py`, the glsim harness
in `tests/integration/conftest.py`, and every claim about fail-closed behaviour
in `README.md` and `docs/`.

---

## 1. Disclosure first

The review that produced this file began by attempting to demonstrate validator
disagreement on the simulator, and **failed repeatedly before succeeding**. Four
wrong conclusions were reached on the way, and two of them are the kind of error
that produces a confident, wrong evidence file. Recording them, because a reader
who skips to §3 would otherwise assume the working path was found first time.

| Attempt | Conclusion drawn | Why it was wrong |
|---|---|---|
| Set `num_validators=3`, install a stateful mock | "static mocks cannot diverge validators" | Correct conclusion, wrong evidence — `_run_validators` re-runs one closure, but a *live handler* can differ per call |
| Install `_live_llm_handler` on `vm` | "the handler is never called" | True, and the reason was not guessed: glsim re-installs its own handler on every activation |
| Monkeypatch `wasi_mock._handle_run_nondet` | "the shim is dead code" | The dispatcher does a module-global lookup, so the shim *was* live — but glsim never routes `run_nondet` through it |
| Conclude from `_captured_validators` being empty that consensus is vacuous | **recorded as fact** | **Right conclusion, and the reasoning did not follow.** `_captured_validators` is populated by the direct-mode `run_nondet` patch, not by the wasi path. The empty list came from `sim_call` never invoking `run_consensus` at all |

The last row is the important one. The correct statement is narrower and
stronger than the one I first wrote, and it is now what the docs say.

## 2. Bottom line

Two facts about `glsim`, established by tracing source rather than reading docs:

1. **`sim_call` and `gen_call` never run consensus.** `glsim.server.
   _rpc_sim_call_sdk` calls `engine.call_method` directly and returns
   `"status": "FINALIZED"` as a literal. `run_consensus` is referenced only from
   `_rpc_eth_send_raw_transaction`. Therefore **a `FINALIZED` status read from
   `sim_call` is a constant and is not evidence of anything.**
2. **The validator half of `run_nondet` is discarded by the default harness.**
   The std lib sends both `data_leader` and `data_validator`; the harness reads
   only the leader. So on the stock path consensus is decided without consulting
   a single validator.

Fixing (2) with a capture shim and calling `run_consensus` directly makes real
votes possible. What that demonstrates, and what it still does not, is §5.

| # | Severity | Finding | Verdict | Action |
|---|---|---|---|---|
| P3-1 | **P1** | `sim_call` reports a hardcoded `FINALIZED` | **confirmed** | documented in README §1, THREAT_MODEL §4, CONSENSUS; pinned by `test_sim_call_does_not_run_consensus` |
| P3-2 | **P1** | Stock glsim never runs the validator | **confirmed** | capture shim added; limitation stated wherever consensus is claimed |
| P3-3 | P1 | A split can be demonstrated | **confirmed** | `test_a_validator_split_does_not_finalize` |
| P3-4 | P2 | A 1-of-3 minority can finalize | **refuted** | majority is `n//2+1`; tested in `test_a_partial_split_still_fails_without_a_majority` |
| P3-5 | P2 | glsim rolls back storage after a failed consensus | **refuted** | incomplete; recorded in `test_failed_consensus_leaves_state_in_the_simulator` |
| P3-6 | P2 | Mixed RPC/consensus call sequences are safe | **refuted** | `No module named 'genlayer'`; one consensus call per test |

## 3. What was actually demonstrated

With the capture shim installed and `run_consensus` called directly:

| Scenario | Votes | Status |
|---|---|---|
| Leader and all three validators agree | `['agree','agree','agree']` | `FINALIZED` |
| Leader says `ALLOWED`, all validators say `VIOLATED` | `['disagree','disagree','disagree']` | `UNDETERMINED`, `No consensus after max rotations` |
| Leader `ALLOWED`, validator 1 `ALLOWED`, validators 2-3 `VIOLATED` | `['agree','disagree','disagree']` | `UNDETERMINED` |
| Leader and all validators cite the *same* fabricated clause | `['agree','agree','agree']` | `FINALIZED`, ruling `AMBIGUOUS` / `UNVERIFIED_EXCERPT` |

The fourth row is the most interesting and was not anticipated when the seam was
written. **The guard against invented authority does not depend on
disagreement.** Both sides normalise the fabricated citation to `UNVERIFIED_EXCERPT`
deterministically, so they agree — and the result is still a refusal. The excerpt
check lives in normalisation, not in the vote.

That is a stronger position than the design claimed, and it is now stated as such
in `docs/THREAT_MODEL.md` §2.1.

## 4. Refuted findings

### P3-4 — "a 1-of-3 minority could finalize"

Refuted. `run_consensus` computes `majority = num_validators // 2 + 1`, so with
three validators a majority is two, and one agreeing vote is insufficient. Pinned
by `test_a_partial_split_still_fails_without_a_majority`, which asserts the exact
vote vector rather than just the status.

### P3-5 — "glsim rolls back storage after a failed consensus"

**Refuted as a working guarantee.** `run_consensus` calls `_restore_storages` on
failure, but `engine.restore_snapshot` restores only registry *membership* —
which keys exist — while contents are restored from a `copy.deepcopy` taken before
the leader ran. The contract instance keeps its own bound storage manager, so
after a split the simulator still reports the operation as `RULED`.

Recorded in `test_failed_consensus_leaves_state_in_the_simulator`, asserted as
observed behaviour and explicitly **not** endorsed. This matters because a green
test read as proof of on-chain atomicity would be a serious overclaim. On a real
chain the question does not arise: a transaction that does not reach consensus is
never committed, so it cannot write state at all.

### P3-6 — "RPC and consensus calls can be mixed freely"

Refuted. `vm.activate()` installs the GenVM SDK on `sys.path` and removes it on
exit; the RPC path manages it separately. A test that makes a consensus call, then
an RPC call, then another consensus call fails with `No module named 'genlayer'`
inside the leader execution — a failure that looks like a contract bug and is
not one. Every test in `test_consensus.py` now performs all setup over RPC and
makes exactly one consensus call, and the constraint is documented in the module
docstring.

## 5. What this review does not establish

1. **The GenVM runtime's actual behaviour on disagreement is still unproven.**
   This is the finding, not a caveat to it. Everything about fail-closed behaviour
   on a real network rests on it, and only a live run or a real multi-validator
   environment settles it.
2. **glsim is not the runtime.** A shimmed simulator agreeing with a shimmed
   harness proves the seam is wired correctly. It is not evidence about production.
3. **`max_rotations=1` everywhere.** Leader rotation under real failure is
   untested, and is the mechanism that would actually resolve (1).
4. **No real model was ever consulted.** Divergence was manufactured by a
   call-counting stand-in. Nothing here measures how often real validators
   genuinely disagree.
5. **Single-process, single-block.** No block production, no reorganisation, no
   fee behaviour across multi-hop finalized messages.
6. **The harness shims are not the contracts under test.** `contract(s) are
   unmodified`; the shims sit entirely in the harness, and each is documented at
   its definition. A shim that changed contract semantics would invalidate these
   results — none does, but that is a claim about the shims, not something the
   tests can assert about themselves.

## 6. Regression sweep

| Expected | Observed |
|---|---|
| `genvm-lint lint` both contracts | ✓ 3 checks each |
| `genvm-lint check` both contracts | ✓ 21 and 11 methods |
| `scripts/preflight.py` | ✓ 265 checks, 36 files |
| direct suite | ✓ 120 passed |
| integration suite | ✓ 24 passed |

## 7. Recommended next work

1. **The live run is the only thing that resolves §5.1.** Everything else on this
   list is lower value than eight real transactions on studionet.
2. Write `evidence/studionet-<date>.md` with protocol status, consensus result and
   leader execution as three separate columns, and **publish the failures**.
3. If the live network cannot produce reliable three-way agreement, say so in the
   submission rather than shipping a demo that implies it did.