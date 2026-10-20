"""Phase 0 probe tests — see conftest.py. NOT the project's real suite."""

COVENANT = "No payment above 50000 without multisig approval."


def _model_reply(verdict: str) -> str:
	"""Prose plus JSON.

	Deliberately not a bare JSON string: this exercises the contract's own
	brace-scan parser rather than the SDK's JSON path, which is the design we
intend to ship.
	"""
	return f'Looking at the covenant. My assessment: {{"verdict": "{verdict}"}}'


# --- surface smoke -------------------------------------------------------

def test_deploy_and_views_work(caller, callee, direct_vm):
	# NOTE: `callee` is requested last by fixture ordering (caller -> peer ->
	# callee), so allocating `caller` has already disturbed `callee`'s slots.
	# See test_probe_behaviour.py::test_deploying_a_second_contract_disturbs_
	# the_first_stored_deploy. Only the caller is asserted on here.
	assert caller.get_last_receipt() != ""
	assert caller.get_verdict("nothing") == ""


def test_callee_owns_its_own_deploy(callee):
	assert callee.get_salt() == "salt-1"
	assert callee.call_count() == 0


def test_storage_roundtrips_digest(caller, callee, direct_vm):
	import hashlib

	expected = hashlib.sha256(COVENANT.encode("utf-8")).hexdigest()
	assert caller.get_last_receipt() == expected


# --- Q: does gl.message_raw carry a usable clock? ------------------------

def test_message_raw_datetime_is_present_and_nonempty(caller, direct_vm):
	stamp = caller.now("probe-clock-1")
	assert stamp != ""


def test_warp_alone_does_not_move_the_raw_datetime(caller, direct_vm):
	"""Records a trap: VMContext.warp() refreshes sender/origin only.

	`_refresh_gl_message` updates `sender_address` and `origin_address` on
	`gl.message_raw` but never `datetime`. A test that calls `warp()` and then
	reads `gl.message_raw['datetime']` is reading the original stamp.

	Consequence for the real design: any timelock in CovenantGuard must either
	read a value the harness refreshes, or tests must set the raw key in place.
	"""
	before = caller.now("probe-clock-2")
	direct_vm.warp("2030-01-01T00:00:00Z")
	after = caller.now("probe-clock-3")
	assert after == before


# --- the consensus seam --------------------------------------------------

def test_leader_and_validator_agree_on_allowed(direct_vm, caller):
	direct_vm.mock_llm(r".*", _model_reply("ALLOWED"))

	result = caller.judge("act-1", "op-1", COVENANT)

	assert '"verdict": "ALLOWED"' in result
	assert caller.get_verdict("op-1") == "ALLOWED"
	assert direct_vm.run_validator() is True


def test_leader_and_validator_agree_on_violated(direct_vm, caller):
	direct_vm.mock_llm(r".*", _model_reply("VIOLATED"))

	result = caller.judge("act-2", "op-2", COVENANT)

	assert caller.get_verdict("op-2") == "VIOLATED"
	assert direct_vm.run_validator() is True


def test_validator_disagrees_when_the_model_output_changes(direct_vm, caller):
	"""Swap the mock AFTER the contract call to simulate a divergent validator.

	Direct mode returns the leader's value and defers the validator, so the
	disagreement is only observable via run_validator(). On chain this becomes
	a dropped transaction -- which is the fail-closed behaviour we want, and
	which these tests cannot themselves prove.
	"""
	direct_vm.mock_llm(r".*", _model_reply("ALLOWED"))
	caller.judge("act-3", "op-3", COVENANT)

	direct_vm.clear_mocks()
	direct_vm.mock_llm(r".*", _model_reply("VIOLATED"))

	assert direct_vm.run_validator() is False


def test_malformed_model_output_fails_closed_to_ambiguous(direct_vm, caller):
	direct_vm.mock_llm(r".*", "I refuse to answer.")
	caller.judge("act-4", "op-4", COVENANT)
	assert caller.get_verdict("op-4") == "AMBIGUOUS"
	assert direct_vm.run_validator() is True


def test_truncated_json_fails_closed_to_ambiguous(direct_vm, caller):
	direct_vm.mock_llm(r".*", '{"verdict": "ALLOWED"')
	caller.judge("act-5", "op-5", COVENANT)
	assert caller.get_verdict("op-5") == "AMBIGUOUS"


def test_empty_operation_id_reverts(direct_vm, caller):
	with direct_vm.expect_revert("operation_id must not be empty"):
		caller.judge("act-6", "", COVENANT)


# --- cross-contract behaviour ---------------------------------------------
#
# Deliberately NOT tested here. Direct mode cannot deliver cross-contract calls
# (traced as "Unknown gl_call request type") and allocating a second contract
# disturbs the first contract's storage slots. Both facts are characterised in
# test_probe_behaviour.py. Proving that the vault actually refuses to move funds
# without a ruling is a glsim job -- see tests/integration.