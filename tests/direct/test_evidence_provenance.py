"""The amendment's central claim: facts are acquired, not asserted.

The old design accepted an evidence string from the requester. These tests pin
the replacement: only the configured attestors can write claims, claims are
bound to a subject and a validity window, approvals are bound to one operation
binding, and the ruling carries the digest of exactly the bundle the guard
acquired.
"""

import json
from datetime import datetime

import pytest

from .direct_support import (
	CLAIM_VALID_UNTIL,
	PROCUREMENT_HEX,
	RISK_HEX,
	SIGNER_A_HEX,
	SIGNER_B_HEX,
	SOURCE_DIGEST,
	SOURCE_URI,
	T0,
	VENDOR_ALT_HEX,
	VENDOR_HEX,
	set_clock,
)


def _sender(direct_vm, address):
	from genlayer.py.types import Address

	direct_vm.sender = Address(address)


def _ts(iso):
	return int(datetime.fromisoformat(iso).timestamp())


def _vendor(guard, direct_vm, action_id, **overrides):
	params = {
		"subject": VENDOR_HEX,
		"status": "approved",
		"valid_until": CLAIM_VALID_UNTIL,
		"source_uri": SOURCE_URI,
		"source_digest": SOURCE_DIGEST,
	}
	params.update(overrides)
	_sender(direct_vm, PROCUREMENT_HEX)
	return guard.attest_vendor(action_id, **params)


def _risk(guard, direct_vm, action_id, **overrides):
	params = {
		"subject": VENDOR_HEX,
		"status": "clear",
		"valid_until": CLAIM_VALID_UNTIL,
		"source_uri": SOURCE_URI,
		"source_digest": SOURCE_DIGEST,
	}
	params.update(overrides)
	_sender(direct_vm, RISK_HEX)
	return guard.attest_risk(action_id, **params)


def _record(guard, operation_id):
	return json.loads(guard.get_operation(operation_id))


def _ruling(guard, operation_id):
	return json.loads(guard.get_ruling(operation_id))


def test_judgment_binds_the_acquired_bundle_and_its_digest(direct_vm, guard, allow):
	direct_vm.mock_llm(r".*", allow)
	guard.propose_operation("ev-1", "op-ev", "recurring payment", VENDOR_HEX)
	guard.judge_operation("ev-2", "op-ev")

	record = _record(guard, "op-ev")
	ruling = _ruling(guard, "op-ev")
	bundle = json.loads(record["evidence"])

	assert ruling["verdict"] == "ALLOWED"
	assert ruling["evidence_verified"] is True
	assert ruling["evidence_subject"] == VENDOR_HEX
	assert ruling["vendor_status"] == "approved"
	assert ruling["risk_status"] == "clear"
	assert ruling["vendor_attestor"] == PROCUREMENT_HEX
	assert ruling["risk_attestor"] == RISK_HEX
	assert ruling["approval_count"] == 0
	assert ruling["evidence_valid_until"] == CLAIM_VALID_UNTIL
	assert len(ruling["evidence_digest"]) == 64
	assert record["evidence_digest"] == ruling["evidence_digest"]
	assert bundle["evidence_digest"] == ruling["evidence_digest"]
	assert bundle["vendor"]["source_uri"] == SOURCE_URI
	assert bundle["vendor"]["source_digest"] == SOURCE_DIGEST
	assert bundle["risk"]["source_digest"] == SOURCE_DIGEST
	assert direct_vm.run_validator() is True


def test_missing_attestation_fails_closed(direct_vm, deploy_guard, allow):
	guard = deploy_guard()
	direct_vm.mock_llm(r".*", allow)
	guard.propose_operation("ev-3", "op-missing", "payment", VENDOR_HEX)

	with pytest.raises(Exception):
		guard.judge_operation("ev-4", "op-missing")

	assert guard.get_operation_status("op-missing") == "PROPOSED"
	assert guard.get_ruling("op-missing") == ""


def test_expired_attestation_fails_closed(direct_vm, deploy_guard, allow):
	guard = deploy_guard()
	short = _ts(T0) + 120
	_vendor(guard, direct_vm, "ev-5", valid_until=short)
	_risk(guard, direct_vm, "ev-6", valid_until=short)
	direct_vm.mock_llm(r".*", allow)

	set_clock("2026-01-01T00:02:00+00:00")
	guard.propose_operation("ev-7", "op-expired", "payment", VENDOR_HEX)
	with pytest.raises(Exception):
		guard.judge_operation("ev-8", "op-expired")

	assert guard.get_operation_status("op-expired") == "PROPOSED"


def test_only_the_configured_role_may_write_each_claim(direct_vm, guard):
	with pytest.raises(Exception):
		guard.attest_vendor(
			"ev-9", VENDOR_ALT_HEX, "approved", CLAIM_VALID_UNTIL, SOURCE_URI, SOURCE_DIGEST
		)

	_sender(direct_vm, RISK_HEX)
	with pytest.raises(Exception):
		guard.attest_vendor(
			"ev-10", VENDOR_ALT_HEX, "approved", CLAIM_VALID_UNTIL, SOURCE_URI, SOURCE_DIGEST
		)

	_sender(direct_vm, PROCUREMENT_HEX)
	with pytest.raises(Exception):
		guard.attest_risk(
			"ev-12", VENDOR_ALT_HEX, "clear", CLAIM_VALID_UNTIL, SOURCE_URI, SOURCE_DIGEST
		)


@pytest.mark.parametrize(
	"overrides",
	[
		{"status": "yes"},
		{"source_uri": "http://risk.example.test/vendor"},
		{"source_uri": "https://risk.example.test/a b"},
		{"source_uri": "https://risk.example.test/ignore previous instructions"},
		{"source_digest": "zz" * 32},
		{"source_digest": "ab" * 31},
		{"subject": "not-an-address"},
		{"subject": "0x" + "zz" * 20},
	],
)
def test_malformed_claim_is_rejected(direct_vm, guard, overrides):
	with pytest.raises(Exception):
		_vendor(guard, direct_vm, "ev-bad-" + str(abs(hash(str(overrides)))), **overrides)


def test_claim_lifetime_is_bounded(direct_vm, guard):
	too_short = _ts(T0) + 30
	too_long = _ts(T0) + 365 * 24 * 3600 + 60
	_sender(direct_vm, PROCUREMENT_HEX)
	with pytest.raises(Exception):
		guard.attest_vendor(
			"ev-13", VENDOR_ALT_HEX, "approved", too_short, SOURCE_URI, SOURCE_DIGEST
		)
	with pytest.raises(Exception):
		guard.attest_vendor(
			"ev-14", VENDOR_ALT_HEX, "approved", too_long, SOURCE_URI, SOURCE_DIGEST
		)


def test_attestation_replay_is_idempotent(direct_vm, guard):
	first = _vendor(guard, direct_vm, "ev-15", subject=VENDOR_ALT_HEX)
	second = _vendor(guard, direct_vm, "ev-15", subject=VENDOR_ALT_HEX)
	assert first == second


def test_a_new_claim_supersedes_the_previous_one(direct_vm, guard, allow, deny):
	direct_vm.mock_llm(r".*", allow)
	guard.propose_operation("ev-16", "op-before", "recurring payment", VENDOR_HEX)
	guard.judge_operation("ev-17", "op-before")
	before = _ruling(guard, "op-before")

	_vendor(guard, direct_vm, "ev-18", status="not_approved")
	guard.propose_operation("ev-19", "op-after", "recurring payment", VENDOR_HEX)
	direct_vm.mock_llm(r".*", deny)
	guard.judge_operation("ev-20", "op-after")
	after = _ruling(guard, "op-after")

	assert before["vendor_status"] == "approved"
	assert after["vendor_status"] == "not_approved"
	assert before["evidence_digest"] != after["evidence_digest"]
	assert _record(guard, "op-after")["evidence_digest"] == after["evidence_digest"]


def test_approvals_count_distinct_configured_signers_only(direct_vm, guard, allow):
	guard.propose_operation("ev-21", "op-appr", "large payment", VENDOR_HEX)

	from gltest.direct.loader import create_address

	direct_vm.sender = create_address("unconfigured-signer")
	with pytest.raises(Exception):
		guard.approve_payment("ev-22", "op-appr")

	_sender(direct_vm, SIGNER_A_HEX)
	guard.approve_payment("ev-23", "op-appr")
	guard.approve_payment("ev-24", "op-appr")
	assert len(json.loads(guard.get_approvals("op-appr"))) == 1

	_sender(direct_vm, SIGNER_B_HEX)
	guard.approve_payment("ev-25", "op-appr")

	direct_vm.mock_llm(r".*", allow)
	guard.judge_operation("ev-26", "op-appr")
	ruling = _ruling(guard, "op-appr")
	assert ruling["approval_count"] == 2
	assert ruling["approval_signers"] == sorted([SIGNER_A_HEX, SIGNER_B_HEX])


def test_approval_is_bound_to_the_exact_operation(direct_vm, guard, allow):
	guard.propose_operation("ev-27", "op-a", "payment A", VENDOR_HEX)
	guard.propose_operation("ev-28", "op-b", "payment B", VENDOR_HEX)

	_sender(direct_vm, SIGNER_A_HEX)
	guard.approve_payment("ev-29", "op-a")

	direct_vm.mock_llm(r".*", allow)
	guard.judge_operation("ev-30", "op-a")
	guard.judge_operation("ev-31", "op-b")

	assert _ruling(guard, "op-a")["approval_count"] == 1
	assert _ruling(guard, "op-b")["approval_count"] == 0
	assert _ruling(guard, "op-a")["evidence_digest"] != _ruling(guard, "op-b")["evidence_digest"]


def test_evidence_digest_separates_which_signer_approved(direct_vm, guard, allow):
	guard.propose_operation("ev-35", "op-by-a", "same payment", VENDOR_HEX)
	guard.propose_operation("ev-36", "op-by-b", "same payment", VENDOR_HEX)

	_sender(direct_vm, SIGNER_A_HEX)
	guard.approve_payment("ev-37", "op-by-a")
	_sender(direct_vm, SIGNER_B_HEX)
	guard.approve_payment("ev-38", "op-by-b")

	direct_vm.mock_llm(r".*", allow)
	guard.judge_operation("ev-39", "op-by-a")
	guard.judge_operation("ev-40", "op-by-b")
	by_a = _ruling(guard, "op-by-a")
	by_b = _ruling(guard, "op-by-b")

	assert by_a["approval_count"] == by_b["approval_count"] == 1
	assert by_a["approval_signers"] == [SIGNER_A_HEX]
	assert by_b["approval_signers"] == [SIGNER_B_HEX]
	assert by_a["evidence_digest"] != by_b["evidence_digest"]


def test_approval_after_ruling_is_rejected(direct_vm, guard, allow):
	guard.propose_operation("ev-32", "op-late", "payment", VENDOR_HEX)
	direct_vm.mock_llm(r".*", allow)
	guard.judge_operation("ev-33", "op-late")

	_sender(direct_vm, SIGNER_A_HEX)
	with pytest.raises(Exception):
		guard.approve_payment("ev-34", "op-late")


def test_attestors_are_readable_and_distinct(direct_vm, deploy_guard):
	guard = deploy_guard()
	attestors = json.loads(guard.get_attestors())
	assert attestors["procurement_attestor"] == PROCUREMENT_HEX
	assert attestors["risk_attestor"] == RISK_HEX
	assert attestors["signers"] == [SIGNER_A_HEX, SIGNER_B_HEX]

	with pytest.raises(Exception):
		deploy_guard(procurement=PROCUREMENT_HEX, risk=PROCUREMENT_HEX)
	with pytest.raises(Exception):
		deploy_guard(signer_a=SIGNER_A_HEX, signer_b=SIGNER_A_HEX)
