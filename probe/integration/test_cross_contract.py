"""Phase 0: does glsim deliver a cross-contract finalized message?

This is the single question Phase 2's whole design rests on. If `emit(on=
"finalized")` does not round-trip on the simulator, the two-contract architecture
in PLAN.md has to change -- hence this is probed before any real contract code.
"""

import json

COVENANT = "No payment above 50000 without multisig approval."


def _model_reply(verdict: str) -> str:
	return f"Assessment: {json.dumps({'verdict': verdict})}"


def test_deploy_both_contracts(sim, caller_path, callee_path):
	callee = sim.deploy(callee_path, ["salt-1"])
	assert callee.startswith("0x")


def test_cross_contract_write_is_delivered(sim, caller_path, callee_path):
	"""A direct write to the callee persists state."""
	callee = sim.deploy(callee_path, ["salt-1"])

	sim.write(callee, "record", ["probe-1", "hello from integration"])
	assert sim.read(callee, "call_count") == 1


def test_cross_contract_view_is_delivered(sim, caller_path, callee_path):
	"""`caller.read_peer` performs a cross-contract view read.

	Direct mode dropped this and returned `None`. glsim must deliver it, or the
	two-contract design has no testable foundation.
	"""
	callee = sim.deploy(callee_path, ["salt-1"])
	caller = sim.deploy(caller_path, [callee, COVENANT])

	# `read_peer` null-checks and reverts if the call is undeliverable, so a
	# clean return here is itself the proof.
	result = sim.call(caller, "read_peer", ["act-1"])
	assert result == "salt-1"


def test_cross_contract_emit_is_delivered(sim, caller_path, callee_path):
	"""`caller.ping_peer` emits `callee.record` with on="finalized".

	The callee's call_count must advance. This is the load-bearing probe for
	Phase 2.
	"""
	callee = sim.deploy(callee_path, ["salt-1"])
	caller = sim.deploy(caller_path, [callee, COVENANT])

	before = sim.read(callee, "call_count")
	sim.call(caller, "ping_peer", ["act-2", "hello across the wire"])
	after = sim.read(callee, "call_count")

	assert after == before + 1


def test_consensus_seam_runs_on_glsim(sim, caller_path, callee_path):
	"""`judge_operation` equivalent: leader prompt plus validator agreement."""
	callee = sim.deploy(callee_path, ["salt-1"])
	caller = sim.deploy(caller_path, [callee, COVENANT])

	sim.install_mocks({".*": _model_reply("ALLOWED")})
	sim.call(caller, "judge", ["act-3", "op-1", COVENANT])

	assert sim.read(caller, "get_verdict", ["op-1"]) == "ALLOWED"