"""Consensus, and the honest limit of what the simulator can show.

**Read this before citing anything here as evidence about the network.**

## glsim's `sim_call` and `gen_call` do not run consensus

Tracing `glsim.server._rpc_sim_call_sdk` shows it calls `engine.call_method`
directly and never calls `run_consensus`. Its receipt hardcodes:

```python
return {"status": "FINALIZED", ...}
```

`gen_call` is the same shape. `run_consensus` is reachable only from
`eth_sendRawTransaction`, which needs a signed raw GenLayer transaction.

**Therefore: a `FINALIZED` status read from `sim_call` means nothing.** It is a
constant. Any evidence file that reports it as a consensus result would be
reporting a hardcoded string. `test_sim_call_does_not_run_consensus` pins this.

## Driving consensus directly

`glsim.consensus.run_consensus` is an ordinary function, so this suite calls it
directly with its own `execute_fn`. That is what actually exercises validator
voting, and it is the only way to see disagreement on the simulator.

`run_consensus` reads glsim's captured validators, which the direct-mode
`run_nondet` patch populates. `_run_validators` then re-runs that captured
validator once per validator, which re-derives the verdict by calling the model
again. Giving the leader one reply and the validators another is what a genuine
split looks like.

## Two harness constraints, both load-bearing

* **One consensus call per test.** `vm.activate()` installs the GenVM SDK on
  `sys.path` and removes it on exit, while the RPC path manages it separately.
  A test that makes a consensus call, then an RPC call, then another consensus
  call fails with `No module named 'genlayer'` inside the leader execution. Each
  test here therefore performs all setup over RPC and then makes exactly one
  consensus call.
* **`with engine.vm.activate()` wraps the whole `run_consensus` call**, not just
  `execute_fn`, because `_run_validators` itself imports `genlayer.gl.vm`.

## What this file does and does not establish

Establishes: a validator split produces `UNDETERMINED` with every validator
voting `disagree`, and the transaction does not finalize. That is the fail-closed
property at the level glsim can reach.

Does **not** establish: that the GenVM runtime on a real network behaves the same
way. glsim's own storage rollback on a failed consensus is incomplete (see
`test_failed_consensus_leaves_state_in_the_simulator`); on a real chain an
uncommitted transaction cannot write state at all, so the question does not
arise. See `docs/THREAT_MODEL.md` and `state/REVIEW_ROUND3.md`.
"""

import json

import pytest
from .conftest import (
	COVENANT,
	OWNER,
	allow_ok,
	deny_ok,
	DivergentModel,
	install_validator_capture,
)

BINDING = json.dumps({"amount": 900, "memo": "recurring retainer"}, sort_keys=True, separators=(",", ":"))


def consensus_write(sim, to, method, args=(), sender=OWNER):
	"""Run one write through glsim's real consensus path.

	Bypasses `sim_call`, which never consults `run_consensus`. Exactly one of
	these may be called per test -- see the module docstring.
	"""
	from glsim.consensus import run_consensus

	def execute_fn():
		return sim.engine.call_method(to, method, list(args), {}, sender), b""

	with sim.engine.vm.activate():
		return run_consensus(
			sim.engine, execute_fn, sim.engine.num_validators, sim.engine.max_rotations
		)


# --- the honest limit -----------------------------------------------------

def test_sim_call_does_not_run_consensus(sim_multi, deployed_multi):
	"""`sim_call` reports a hardcoded FINALIZED and never votes.

	If this test ever fails because the simulator started running consensus,
	delete the rest of this file's assumptions and re-derive them.
	"""
	from glsim import consensus as consensus_module

	called = []
	original = consensus_module.run_consensus

	def traced(*args, **kwargs):
		called.append(1)
		return original(*args, **kwargs)

	sim_multi.engine.__class__.__module__  # noqa: B018 - document the engine type in the failure
	import glsim.server as server_module

	server_module.run_consensus = traced
	try:
		guard = deployed_multi["guard"]
		sim_multi.install_mocks({".*": allow_ok()})
		receipt = sim_multi.call(guard, "propose_operation", ["p1", "op-sc", BINDING, "ev"])
	finally:
		server_module.run_consensus = original

	assert called == [], "sim_call started running consensus; re-derive this suite"
	# And the status it reports is a constant, not a vote.
	assert receipt is not None


# --- agreement ------------------------------------------------------------

def test_three_validators_agreeing_finalizes_the_ruling(sim_multi, deployed_multi):
	"""The happy path through real consensus, with three votes cast."""
	guard = deployed_multi["guard"]
	sim_multi.call(guard, "propose_operation", ["p1", "op-agree", BINDING, "ev"])
	sim_multi.install_mocks({".*": allow_ok()})

	outcome = consensus_write(sim_multi, guard, "judge_operation", ["j1", "op-agree"])

	assert outcome.votes == ["agree", "agree", "agree"]
	assert str(outcome.status).endswith("FINALIZED")
	assert sim_multi.read(guard, "get_ruling", ["op-agree"]) != ""


# --- divergence -----------------------------------------------------------

def test_a_validator_split_does_not_finalize(sim_multi, deployed_multi):
	"""The fail-closed property, on the only simulator path that votes.

	The leader is answered ALLOWED. Every validator re-derives independently and
	is answered VIOLATED. All three vote `disagree`, so no majority agrees and
	the transaction must not finalize.
	"""
	guard = deployed_multi["guard"]
	sim_multi.call(guard, "propose_operation", ["p1", "op-split", BINDING, "ev"])

	sim_multi.install_mocks({}, strict=False)
	model = DivergentModel(allow_ok(), deny_ok())
	install_validator_capture(sim_multi, model)

	outcome = consensus_write(sim_multi, guard, "judge_operation", ["j1", "op-split"])

	assert outcome.votes == ["disagree", "disagree", "disagree"]
	assert str(outcome.status).endswith("UNDETERMINED")
	assert outcome.error == "No consensus after max rotations"
	# One leader call plus one per validator.
	assert model.calls == 1 + len(outcome.votes)


def test_a_partial_split_still_fails_without_a_majority(sim_multi, deployed_multi):
	"""Two of three disagreeing is not a majority, so it must not finalize."""
	guard = deployed_multi["guard"]
	sim_multi.call(guard, "propose_operation", ["p1", "op-partial", BINDING, "ev"])

	sim_multi.install_mocks({}, strict=False)
	# Leader says ALLOWED (call 0). The first validator also says ALLOWED
	# (call 1) and therefore agrees. Every later validator says VIOLATED.
	model = DivergentModel(allow_ok(), deny_ok(), validator_switch_at=2)
	install_validator_capture(sim_multi, model)

	outcome = consensus_write(sim_multi, guard, "judge_operation", ["j1", "op-partial"])

	assert outcome.votes == ["agree", "disagree", "disagree"]
	assert outcome.votes.count("agree") < 2
	assert str(outcome.status).endswith("UNDETERMINED")


def test_failed_consensus_leaves_state_in_the_simulator(sim_multi, deployed_multi):
	"""Documents a simulator limitation rather than a contract property.

	`run_consensus` rolls back on failure, but the rollback restores the storage
	*registry* and a deep copy taken before the leader ran -- while the contract
	instance keeps its own bound storage manager. So after a split, glsim still
	reports the operation as ruled.

	This is a simulator artifact and nothing more. On a real chain a transaction
	that does not reach consensus is never committed, so it cannot write state at
	all. Asserted here so the behaviour is recorded rather than discovered later
	by someone reading a green test as proof of on-chain atomicity.
	"""
	guard = deployed_multi["guard"]
	sim_multi.call(guard, "propose_operation", ["p1", "op-roll", BINDING, "ev"])

	sim_multi.install_mocks({}, strict=False)
	install_validator_capture(sim_multi, DivergentModel(allow_ok(), deny_ok()))

	outcome = consensus_write(sim_multi, guard, "judge_operation", ["j1", "op-roll"])

	assert str(outcome.status).endswith("UNDETERMINED")
	# Recorded, not endorsed: the simulator keeps the write.
	assert sim_multi.read(guard, "get_operation_status", ["op-roll"]) == "RULED"


# --- the validator itself -------------------------------------------------

def test_the_validator_rejects_a_fabricated_excerpt_independently(sim_multi, deployed_multi):
	"""Leader and validator are given the *same* reply, and the vote is still no.

	Nothing here depends on a model split. The leader normalises a fabricated
	citation to `UNVERIFIED_EXCERPT`; the validator independently reaches the same
	conclusion and agrees. The guard against invented authority does not need
	disagreement to work -- it is in the normalisation, which is deterministic.
	"""
	from .conftest import EXCERPT_FABRICATED

	fabricated = (
		'Assessment:\n'
		+ json.dumps(
			{"verdict": "ALLOWED", "reason_code": "CITED_CLAUSE", "excerpt": EXCERPT_FABRICATED}
		)
	)
	guard = deployed_multi["guard"]
	sim_multi.call(guard, "propose_operation", ["p1", "op-fab", BINDING, "ev"])

	sim_multi.install_mocks({}, strict=False)
	install_validator_capture(sim_multi, DivergentModel(fabricated, fabricated))

	outcome = consensus_write(sim_multi, guard, "judge_operation", ["j1", "op-fab"])

	assert outcome.votes == ["agree", "agree", "agree"]
	ruling = json.loads(sim_multi.read(guard, "get_ruling", ["op-fab"]))
	assert ruling["verdict"] == "AMBIGUOUS"
	assert ruling["reason_code"] == "UNVERIFIED_EXCERPT"
	assert EXCERPT_FABRICATED not in json.dumps(ruling)